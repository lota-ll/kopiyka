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
async def identity_session(user_id: uuid.UUID) -> AsyncIterator[AsyncSession]:
    """Сесія, у якій відома особа користувача, але household ще не обраний.

    Потрібна для розв'язання "курки і яйця" автентифікації: щоб визначити,
    до яких households належить користувач, треба прочитати ``memberships``
    ще до того, як household узагалі відомий — а RLS-політика цієї таблиці
    (міграція 0002) дозволяє читати або в межах активного household, або
    власні рядки за ``user_id``. Ця сесія виставляє саме другий контекст.

    Використовується виключно в auth-шарі (``deps.py``), у вузькому вікні
    між моментом, коли особу встановлено, і моментом, коли household
    обрано.
    """
    async with SessionFactory() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"),
            {"uid": str(user_id)},
        )
        yield session


@asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncSession]:
    """Сесія без будь-якого tenant-контексту.

    Дозволена **тільки** для операцій рівня платформи, які не стосуються
    RLS-таблиць: пошук користувача за email в ``users``/``identities``
    (ці таблиці RLS не мають — вони не належать жодному household), а
    також службові задачі. Ніколи не використовується для читання чи
    запису в households/memberships/accounts/transactions і подібні —
    для цього є ``tenant_session`` та ``identity_session``.
    """
    async with SessionFactory() as session:
        yield session
