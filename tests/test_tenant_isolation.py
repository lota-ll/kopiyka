"""Tenant isolation test suite.

Найцінніший тест проєкту. Ідея: не покладатися на дисципліну розробника,
а зробити так, щоб **новий ендпоінт без ізоляції автоматично валив CI**.

Три рівні перевірки:

1. ``test_every_route_is_covered`` — інвентаризація маршрутів. Якщо
   з'явився новий tenant-маршрут, не описаний у ``ROUTE_MATRIX``, тест
   падає. Забути неможливо.
2. ``test_rls_blocks_cross_tenant_read`` — рівень БД. Перевіряє, що навіть
   ``SELECT`` без ``WHERE`` не бачить чужих рядків.
3. ``test_cross_tenant_access_returns_404`` — рівень HTTP. Користувач B
   отримує 404 (не 403!) на ресурси A. 403 підтвердив би існування
   ресурсу — це витік метаданих.

Тести з БД пропускаються без ``KOPIYKA_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

import pytest

DB_URL = os.environ.get("KOPIYKA_TEST_DATABASE_URL")
requires_db = pytest.mark.skipif(not DB_URL, reason="потрібен KOPIYKA_TEST_DATABASE_URL")


@dataclass(frozen=True)
class RouteSpec:
    """Опис очікуваної поведінки маршруту щодо ізоляції."""

    method: str
    path: str
    tenant_scoped: bool
    note: str = ""


# Джерело істини. Кожен новий маршрут має бути доданий сюди свідомо.
ROUTE_MATRIX: tuple[RouteSpec, ...] = (
    RouteSpec("GET", "/healthz", tenant_scoped=False, note="liveness, без даних"),
    RouteSpec("GET", "/readyz", tenant_scoped=False, note="readiness, без даних"),
    RouteSpec("GET", "/api/v1/me", tenant_scoped=False, note="дані самого користувача"),
    RouteSpec("GET", "/api/v1/accounts", tenant_scoped=True),
    RouteSpec("POST", "/api/v1/accounts", tenant_scoped=True),
    RouteSpec("GET", "/api/v1/accounts/{account_id}", tenant_scoped=True),
    RouteSpec("POST", "/api/v1/imports", tenant_scoped=True),
    RouteSpec("GET", "/api/v1/imports/{batch_id}/summary", tenant_scoped=True),
)

IGNORED_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def _collect(routes, found: set[tuple[str, str]]) -> None:
    """Рекурсивно обходить дерево маршрутів.

    Дві пастки, на яких цей тест може стати «зеленим, але марним»:

    1. Сучасний FastAPI не «розплющує» ``include_router`` у плоский
       список, а зберігає службову обгортку; справжній роутер лежить в
       ``original_router``. Наївний прохід по ``app.routes`` побачить лише
       маршрути, оголошені прямо на ``app``.
    2. ``APIRoute.path`` уже містить префікс роутера — додавати його
       вдруге не можна.
    """
    for route in routes:
        inner = getattr(route, "original_router", None) or route
        nested = getattr(inner, "routes", None)
        if nested:
            _collect(nested, found)
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or path in IGNORED_PATHS:
            continue
        for method in methods:
            if method not in ("HEAD", "OPTIONS"):
                found.add((method, path))


def _app_routes() -> set[tuple[str, str]]:
    from kopiyka.api.main import app

    found: set[tuple[str, str]] = set()
    _collect(app.routes, found)
    return found


def test_every_route_is_covered() -> None:
    """Новий маршрут без запису в ROUTE_MATRIX = червоний CI.

    Це і є механізм, що не дає забути про ізоляцію при додаванні фічі.
    """
    declared = {(r.method, r.path) for r in ROUTE_MATRIX}
    actual = _app_routes()

    undeclared = actual - declared
    assert not undeclared, (
        "Знайдено маршрути без опису ізоляції. Додай їх у ROUTE_MATRIX "
        f"і переконайся, що вони скоповані по household: {sorted(undeclared)}"
    )

    stale = declared - actual
    assert not stale, f"ROUTE_MATRIX описує неіснуючі маршрути: {sorted(stale)}"


def test_tenant_routes_use_tenant_session() -> None:
    """Статична перевірка: жоден роутер із даними не тягне admin_session.

    admin_session обходить RLS. Легальні місця його використання —
    автентифікація (deps.py) і health-check. У роутерах даних його бути не може.
    """
    import pathlib

    routers_dir = pathlib.Path(__file__).parent.parent / "src" / "kopiyka" / "api" / "routers"
    offenders = []
    for path in routers_dir.glob("*.py"):
        if path.name in ("__init__.py", "health.py"):
            continue
        source = path.read_text("utf-8")
        if "admin_session" in source:
            offenders.append(path.name)
    assert not offenders, f"admin_session у роутерах даних обходить RLS: {offenders}"


# --- Перевірки, що потребують живої БД ------------------------------------


@pytest.fixture
async def two_households():
    """Створює два household з рахунком у кожному."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(DB_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    hh_a, hh_b = uuid.uuid4(), uuid.uuid4()
    acc_a, acc_b = uuid.uuid4(), uuid.uuid4()

    async with factory() as session, session.begin():
        for hh, acc, name in ((hh_a, acc_a, "A"), (hh_b, acc_b, "B")):
            await session.execute(
                text("INSERT INTO households (id, name) VALUES (:id, :name)"),
                {"id": hh, "name": f"Household {name}"},
            )
            await session.execute(
                text(
                    "INSERT INTO accounts (id, household_id, bank, account_ref, name, currency) "
                    "VALUES (:id, :hh, 'mono', :ref, :name, 'UAH')"
                ),
                {"id": acc, "hh": hh, "ref": f"ref-{name}", "name": f"Картка {name}"},
            )

    yield {"hh_a": hh_a, "hh_b": hh_b, "acc_a": acc_a, "acc_b": acc_b}

    async with factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM households WHERE id = ANY(:ids)"), {"ids": [hh_a, hh_b]}
        )
    await engine.dispose()


@requires_db
async def test_rls_blocks_cross_tenant_read(two_households) -> None:
    """SELECT без WHERE у контексті A не бачить рядків B."""
    from sqlalchemy import text

    from kopiyka.db.session import tenant_session

    async with tenant_session(two_households["hh_a"]) as session:
        rows = (await session.execute(text("SELECT id, household_id FROM accounts"))).fetchall()

    household_ids = {row.household_id for row in rows}
    assert household_ids == {two_households["hh_a"]}, "RLS пропустив чужі рядки"
    assert two_households["acc_b"] not in {row.id for row in rows}


@requires_db
async def test_rls_blocks_cross_tenant_write(two_households) -> None:
    """WITH CHECK не дає вставити рядок у чужий household."""
    import asyncpg
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from kopiyka.db.session import tenant_session

    with pytest.raises((DBAPIError, asyncpg.PostgresError)):
        async with tenant_session(two_households["hh_a"]) as session:
            await session.execute(
                text(
                    "INSERT INTO accounts (household_id, bank, account_ref, name, currency) "
                    "VALUES (:hh, 'mono', 'evil', 'Чужий', 'UAH')"
                ),
                {"hh": two_households["hh_b"]},
            )


@requires_db
async def test_app_role_cannot_bypass_rls() -> None:
    """Роль застосунку не має BYPASSRLS і не є власником таблиць.

    Найпоширеніша причина «RLS не працює»: застосунок ходить під
    суперкористувачем або під власником без FORCE ROW LEVEL SECURITY.
    """
    from sqlalchemy import text

    from kopiyka.db.session import admin_session

    async with admin_session() as session:
        row = (
            await session.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert not row.rolsuper, "застосунок ходить під суперкористувачем — RLS не діє"
    assert not row.rolbypassrls, "роль має BYPASSRLS — RLS не діє"


@requires_db
async def test_all_tenant_tables_have_rls_enabled() -> None:
    """Жодна tenant-таблиця не забута при додаванні нової міграції."""
    from sqlalchemy import text

    from kopiyka.db.models import RLS_TABLES
    from kopiyka.db.session import admin_session

    async with admin_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = ANY(:names)"
                ),
                {"names": list(RLS_TABLES)},
            )
        ).fetchall()

    state = {r.relname: (r.relrowsecurity, r.relforcerowsecurity) for r in rows}
    missing = [t for t in RLS_TABLES if state.get(t) != (True, True)]
    assert not missing, f"RLS не увімкнено або не FORCE для: {missing}"


@requires_db
async def test_cross_tenant_access_returns_404(two_households) -> None:
    """HTTP-рівень: B отримує 404 на ресурс A, а не 403."""
    from httpx import ASGITransport, AsyncClient

    from kopiyka.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/accounts/{two_households['acc_a']}",
            headers={
                "X-Dev-User": "b@example.com",
                "X-Household-Id": str(two_households["hh_b"]),
            },
        )
    assert (
        response.status_code == 404
    ), f"очікувався 404, отримано {response.status_code}: 403 підтверджує існування чужого ресурсу"
