"""ORM-моделі.

Ключове правило схеми: ``household_id`` присутній на **кожній** tenant-таблиці,
навіть там, де його можна вивести через JOIN. Це не помилка нормалізації —
це вимога, щоб RLS-політика була однорядковою і не робила підзапитів.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identities: Mapped[list[Identity]] = relationship(back_populates="user")


class Identity(Base):
    """Розв'язує внутрішній user.id та ідентифікатор від IdP.

    Завдяки цій таблиці зміна провайдера автентифікації — це міграція
    одного рядка, а не всієї бази.
    """

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_identity_provider_subject"),)

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # cf_access | google | email
    subject: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="identities")


class Household(Base):
    """Тенант. Навмисно не дорівнює користувачу — спільний бюджет це норма."""

    __tablename__ = "households"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), default="UAH", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        CheckConstraint("role IN ('owner','member','viewer')", name="ck_membership_role"),
    )

    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("bank IN ('mono','privat','manual')", name="ck_account_bank"),
        UniqueConstraint("household_id", "account_ref", name="uq_account_ref_per_household"),
    )

    id: Mapped[uuid.UUID] = _pk()
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    bank: Mapped[str] = mapped_column(String(16), nullable=False)
    account_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="UAH", nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class ImportBatch(Base):
    """Аудит кожного заливу файлу — скільки рядків, скільки дублікатів."""

    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint("status IN ('pending','parsed','failed')", name="ck_import_batch_status"),
    )

    id: Mapped[uuid.UUID] = _pk()
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    source_type: Mapped[str] = mapped_column(String(32), default="csv")
    object_key: Mapped[str | None] = mapped_column(Text)
    file_sha256: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    encoding: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_inserted: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    error_detail: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Category(Base):
    """``household_id IS NULL`` = глобальний seed-довідник."""

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = _pk()
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="expense")
    icon: Mapped[str | None] = mapped_column(String(32))


class CategoryRule(Base):
    __tablename__ = "category_rules"

    id: Mapped[uuid.UUID] = _pk()
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=100)
    match_mcc: Mapped[int | None] = mapped_column(SmallInteger)
    match_regex: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("account_id", "dedup_hash", name="uq_tx_dedup"),
        CheckConstraint(
            "category_source IN ('mcc','rule','manual','none')", name="ck_tx_category_source"
        ),
        Index("ix_tx_household_booked", "household_id", "booked_at"),
        Index("ix_tx_household_category", "household_id", "category_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL")
    )
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_account_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mcc: Mapped[int | None] = mapped_column(SmallInteger)
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    description_norm: Mapped[str] = mapped_column(Text, nullable=False)
    counterparty: Mapped[str | None] = mapped_column(Text)
    balance_after_minor: Mapped[int | None] = mapped_column(BigInteger)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    category_source: Mapped[str] = mapped_column(String(8), default="none")
    is_internal_transfer: Mapped[bool] = mapped_column(Boolean, default=False)
    dedup_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = _pk()
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="UAH")
    target_date: Mapped[date | None] = mapped_column(Date)
    saved_minor: Mapped[int] = mapped_column(BigInteger, default=0)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    household_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Таблиці, на які накладається RLS. Список використовується міграцією
# і тестом, який перевіряє, що жодна tenant-таблиця не забута.
RLS_TABLES: tuple[str, ...] = (
    "households",
    "memberships",
    "accounts",
    "import_batches",
    "transactions",
    "category_rules",
    "goals",
)
