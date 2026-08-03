"""CRUD рахунків. Канонічний шаблон tenant-скопованого ендпоінта."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from kopiyka.api.deps import CurrentPrincipal, WritePrincipal
from kopiyka.api.schemas import AccountIn, AccountOut
from kopiyka.db.models import Account
from kopiyka.db.session import tenant_session

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])


@router.get("", response_model=list[AccountOut])
async def list_accounts(principal: CurrentPrincipal) -> list[Account]:
    async with tenant_session(principal.household_id) as session:
        # Свідомо без WHERE household_id: це і є перевірка, що RLS працює.
        rows = await session.scalars(select(Account).order_by(Account.name))
        return list(rows)


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(payload: AccountIn, principal: WritePrincipal) -> Account:
    async with tenant_session(principal.household_id) as session:
        account = Account(household_id=principal.household_id, **payload.model_dump())
        session.add(account)
        await session.flush()
        await session.refresh(account)
        return account


@router.get("/{account_id}", response_model=AccountOut)
async def get_account(account_id: uuid.UUID, principal: CurrentPrincipal) -> Account:
    async with tenant_session(principal.household_id) as session:
        account = await session.get(Account, account_id)
        if account is None:
            # 404, а не 403 — щоб не підтверджувати існування чужого ресурсу.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "не знайдено")
        return account
