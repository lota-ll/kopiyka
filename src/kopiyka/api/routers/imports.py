"""Імпорт банківської виписки.

Ключова властивість — **ідемпотентність**: залив того самого файлу вдруге
дає ``rows_inserted == 0``. Забезпечується унікальним індексом
``(account_id, dedup_hash)`` та ``ON CONFLICT DO NOTHING``, а не перевіркою
у Python. Перевірка в коді програє гонці між паралельними заливами;
обмеження в БД — ні.
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from kopiyka.api.deps import WritePrincipal
from kopiyka.api.schemas import ImportResultOut
from kopiyka.config import get_settings
from kopiyka.db.models import Account, ImportBatch, Transaction
from kopiyka.db.session import tenant_session
from kopiyka.domain.dedup import dedup_hash, normalize_description
from kopiyka.parsers import mono, privat  # noqa: F401 — реєстрація адаптерів
from kopiyka.parsers.base import ParserError, detect_parser
from kopiyka.parsers.encoding import decode_statement

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


@router.post("", response_model=ImportResultOut, status_code=status.HTTP_201_CREATED)
async def import_statement(
    principal: WritePrincipal,
    account_id: uuid.UUID,
    file: UploadFile = File(...),  # noqa: B008 — так вимагає FastAPI
) -> ImportResultOut:
    settings = get_settings()
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "файл завеликий")

    try:
        text_content, encoding = decode_statement(data)
        parser_cls = detect_parser(text_content)
        result = parser_cls().parse(text_content, encoding=encoding)
    except (ParserError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    async with tenant_session(principal.household_id) as session:
        account = await session.get(Account, account_id)
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "не знайдено")

        batch = ImportBatch(
            household_id=principal.household_id,
            account_id=account.id,
            uploaded_by=principal.user_id,
            file_sha256=hashlib.sha256(data).digest(),
            parser_version=result.parser_version,
            encoding=result.encoding,
            status="parsed",
            rows_total=result.rows_total,
        )
        session.add(batch)
        await session.flush()

        rows = [
            {
                "household_id": principal.household_id,
                "account_id": account.id,
                "import_batch_id": batch.id,
                "booked_at": tx.booked_at,
                "amount_minor": tx.amount_minor,
                "currency": tx.currency,
                "amount_account_minor": tx.amount_account_minor,
                "mcc": tx.mcc,
                "description_raw": tx.description_raw,
                "description_norm": normalize_description(tx.description_raw),
                "counterparty": tx.counterparty,
                "balance_after_minor": tx.balance_after_minor,
                "dedup_hash": dedup_hash(
                    account_ref=account.account_ref,
                    booked_at=tx.booked_at,
                    amount_minor=tx.amount_minor,
                    currency=tx.currency,
                    description=tx.description_raw,
                ),
            }
            for tx in result.transactions
        ]

        inserted = 0
        if rows:
            stmt = (
                insert(Transaction)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_tx_dedup")
                .returning(Transaction.id)
            )
            inserted = len((await session.execute(stmt)).fetchall())

        batch.rows_inserted = inserted
        batch.rows_duplicate = len(rows) - inserted

        return ImportResultOut(
            batch_id=batch.id,
            bank=result.bank,
            encoding=result.encoding,
            parser_version=result.parser_version,
            rows_total=result.rows_total,
            rows_inserted=inserted,
            rows_duplicate=len(rows) - inserted,
            rows_skipped=result.rows_skipped,
            warnings=result.warnings[:50],
        )


@router.get("/{batch_id}/summary")
async def batch_summary(batch_id: uuid.UUID, principal: WritePrincipal) -> dict[str, object]:
    async with tenant_session(principal.household_id) as session:
        batch = await session.get(ImportBatch, batch_id)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "не знайдено")
        total = await session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_account_minor), 0)).where(
                Transaction.import_batch_id == batch_id
            )
        )
        return {
            "batch_id": str(batch.id),
            "status": batch.status,
            "rows_inserted": batch.rows_inserted,
            "rows_duplicate": batch.rows_duplicate,
            "net_minor": int(total or 0),
        }
