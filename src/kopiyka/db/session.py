"""Сесії БД та tenant-контекст.

Найважливіший файл проєкту з точки зору безпеки.

``SET LOCAL`` діє **тільки в межах транзакції**. Це не деталь стилю:
якщо виставити параметр поза транзакцією, то при роботі через PgBouncer у
режимі transaction pooling наступний запит може піти в інше з'єднання —
і застосунок або впаде, або (значно гірше) побачить чужий household.
Тому ``tenant_session`` завжди відкриває транзакцію першою дією.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kopiyka.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    echo=_settings.db_echo,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def tenant_session(household_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Сесія, обмежена одним household через RLS.

    Використання::

        async with tenant_session(hh_id) as session:
            rows = await session.scalars(select(Transaction))
            # RLS гарантує, що чужих рядків тут не буде навіть без WHERE
    """
    async with SessionFactory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.household_id', :hh, true)"),
            {"hh": str(household_id)},
        )
        yield session


@asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncSession]:
    """Сесія без tenant-контексту.

    Дозволена **тільки** для операцій рівня платформи: автентифікація
    (пошук користувача за email до того, як відомий household), міграції,
    службові задачі. Ніколи не використовується в обробниках даних.
    """
    async with SessionFactory() as session:
        yield session
