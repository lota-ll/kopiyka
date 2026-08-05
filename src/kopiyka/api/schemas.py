"""Pydantic-схеми API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HealthOut(BaseModel):
    status: str
    version: str
    db: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    household_id: uuid.UUID
    role: str


class AccountIn(BaseModel):
    bank: str = Field(pattern="^(mono|privat|manual)$")
    account_ref: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(default="UAH", min_length=3, max_length=3)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bank: str
    account_ref: str
    name: str
    currency: str
    archived: bool


class ImportResultOut(BaseModel):
    batch_id: uuid.UUID
    bank: str
    source_format: str
    encoding: str
    parser_version: str
    rows_total: int
    rows_inserted: int
    rows_duplicate: int
    rows_skipped: int
    warnings: list[str]


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booked_at: datetime
    amount_minor: int
    currency: str
    amount_account_minor: int
    description_raw: str
    mcc: int | None
    category_id: uuid.UUID | None
    category_source: str
    is_internal_transfer: bool
