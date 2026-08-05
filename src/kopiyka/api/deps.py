"""Залежності FastAPI: автентифікація та tenant-контекст.

Фаза 1 (тижні 1–6): ``auth_mode=cf_access``. Cloudflare Access стоїть перед
застосунком, робить SSO і додає заголовок ``Cf-Access-Jwt-Assertion``.
Ми його верифікуємо і робимо upsert користувача. Нуль власного auth-коду.

Фаза 2 (тиждень 7): ``auth_mode=oidc`` — Google + magic link.
Паролів у проєкті немає навмисно (див. ADR-0004).

``auth_mode=dev`` дозволений **тільки** при ``env=local`` і читає
заголовок ``X-Dev-User``. Спроба увімкнути його поза local — падіння на старті.
"""

from __future__ import annotations

import uuid
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from jwt import PyJWKClient
from sqlalchemy import select, text

from kopiyka.config import Settings, get_settings
from kopiyka.db.models import Household, Identity, Membership, User
from kopiyka.db.session import admin_session, identity_session

_jwk_client: PyJWKClient | None = None


class Principal:
    """Автентифікований користувач разом з активним household."""

    def __init__(self, user_id: uuid.UUID, email: str, household_id: uuid.UUID, role: str) -> None:
        self.user_id = user_id
        self.email = email
        self.household_id = household_id
        self.role = role

    @property
    def can_write(self) -> bool:
        return self.role in ("owner", "member")


async def _verify_cf_access(token: str, settings: Settings) -> str:
    """Повертає email із JWT, виданого Cloudflare Access."""
    global _jwk_client
    if not settings.cf_access_team_domain or not settings.cf_access_aud:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "CF Access не налаштовано")
    if _jwk_client is None:
        _jwk_client = PyJWKClient(settings.cf_certs_url)
    try:
        key = _jwk_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.cf_access_aud,
            issuer=f"https://{settings.cf_access_team_domain}",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "невалідний токен") from exc

    email = claims.get("email")
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "у токені немає email")
    return str(email)


async def _resolve_user(email: str, provider: str, subject: str) -> User:
    """Знаходить або створює користувача.

    Виконується в admin-сесії: ``users``/``identities`` RLS не мають.
    Виняток — гілка self-registration нижче: щоб вставити перший household
    нового користувача, потрібен tenant-контекст ще ДО того, як household
    існує в базі. Розв'язання — згенерувати ``id`` на клієнті заздалегідь
    (не покладатись на server-side default) і виставити
    ``app.household_id`` на це значення перед INSERT: рядок стає першим і
    єдиним членом свого ж тенанта, і RLS WITH CHECK це дозволяє.
    """
    settings = get_settings()
    async with admin_session() as session, session.begin():
        identity = await session.scalar(
            select(Identity).where(Identity.provider == provider, Identity.subject == subject)
        )
        if identity is not None:
            user = await session.get(User, identity.user_id)
            if user is None or user.deleted_at is not None:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "обліковий запис вимкнено")
            return user

        user = await session.scalar(select(User).where(User.email == email))
        if user is None:
            if settings.invite_only:
                # Реєстрація тільки за запрошенням: користувач має бути
                # створений заздалегідь (scripts/invite.py).
                raise HTTPException(status.HTTP_403_FORBIDDEN, "потрібне запрошення")
            user = User(email=email)
            session.add(user)
            await session.flush()

            household = Household(id=uuid.uuid4(), name=f"Бюджет {email.split('@')[0]}")
            await session.execute(
                text("SELECT set_config('app.household_id', :hh, true)"),
                {"hh": str(household.id)},
            )
            session.add(household)
            await session.flush()
            session.add(Membership(household_id=household.id, user_id=user.id, role="owner"))

        session.add(Identity(user_id=user.id, provider=provider, subject=subject))
        await session.flush()
        return user


async def get_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    cf_jwt: Annotated[str | None, Header(alias="Cf-Access-Jwt-Assertion")] = None,
    dev_user: Annotated[str | None, Header(alias="X-Dev-User")] = None,
    household: Annotated[str | None, Header(alias="X-Household-Id")] = None,
) -> Principal:
    if settings.auth_mode == "cf_access":
        if not cf_jwt:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "немає заголовка Cloudflare Access")
        email = await _verify_cf_access(cf_jwt, settings)
        provider, subject = "cf_access", email
    elif settings.auth_mode == "dev":
        if settings.env != "local":
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "dev-auth поза local")
        if not dev_user:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "немає заголовка X-Dev-User")
        email, provider, subject = dev_user, "dev", dev_user
    else:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "OIDC додається у тижні 7")

    user = await _resolve_user(email, provider, subject)

    async with identity_session(user.id) as session:
        memberships = list(
            await session.scalars(select(Membership).where(Membership.user_id == user.id))
        )
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "користувач не належить до household")

    if household:
        try:
            requested = uuid.UUID(household)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "невалідний household id") from exc
        chosen = next((m for m in memberships if m.household_id == requested), None)
        if chosen is None:
            # 404, а не 403: 403 підтвердив би існування чужого household.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "не знайдено")
    else:
        chosen = memberships[0]

    request.state.household_id = chosen.household_id
    return Principal(user.id, user.email, chosen.household_id, chosen.role)


def require_write(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if not principal.can_write:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "роль viewer не має права запису")
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
WritePrincipal = Annotated[Principal, Depends(require_write)]
