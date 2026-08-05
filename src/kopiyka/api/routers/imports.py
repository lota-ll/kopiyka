"""Імпорт банківської виписки.

Дві ключові властивості.

**Ідемпотентність.** Залив того самого файлу вдруге дає
``rows_inserted == 0``. Забезпечується унікальним індексом
``(account_id, dedup_hash)`` та ``ON CONFLICT DO NOTHING``, а не перевіркою
в Python: перевірка в коді програє гонці між паралельними заливами,
обмеження в БД — ні.

**Розкладання по рахунках.** monobank віддає окремий файл на картку, тому
рахунок береться з параметра запиту. Приват24 віддає один файл на всі
картки, тому рахунок визначається для кожного рядка окремо за
``account_hint`` (останні чотири цифри картки, зіставлені з
``accounts.account_ref``). Рядки без картки (комісії, службові операції)
йдуть у рахунок за замовчуванням із параметра запиту.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from kopiyka.api.deps import WritePrincipal
from kopiyka.api.schemas import ImportResultOut
from kopiyka.config import get_settings
from kopiyka.db.models import Account, ImportBatch, Transaction
from kopiyka.db.session import tenant_session
from kopiyka.domain.dedup import dedup_hash, normalize_description
from kopiyka.loaders import csv_loader, xlsx_loader  # noqa: F401 — реєстрація лоадерів
from kopiyka.loaders.base import LoaderError, load_any
from kopiyka.parsers import mono, privat  # noqa: F401 — реєстрація адаптерів
from kopiyka.parsers.base import ParsedTransaction, ParserError, detect_parser

router = APIRouter(prefix="/api/v1/imports", tags=["imports"])


@router.post("", response_model=ImportResultOut, status_code=status.HTTP_201_CREATED)
async def import_statement(
    principal: WritePrincipal,
    default_account_id: uuid.UUID,
    file: UploadFile = File(...),  # noqa: B008 — так вимагає FastAPI
) -> ImportResultOut:
    settings = get_settings()
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "файл завеликий")

    try:
        loaded = load_any(data, file.filename)
        result = detect_parser(loaded)().parse(loaded)
    except (ParserError, LoaderError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    async with tenant_session(principal.household_id) as session:
        default_account = await session.get(Account, default_account_id)
        if default_account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "не знайдено")

        # Карта account_ref → Account у межах household. RLS гарантує, що
        # сюди не потраплять рахунки чужого тенанта.
        accounts = list(await session.scalars(select(Account)))
        by_ref = {a.account_ref: a for a in accounts}

        unknown = {h for h in result.account_hints if h not in by_ref}
        if unknown:
            # Свідомо не створюємо рахунки автоматично: тиха поява нового
            # рахунку замаскувала б помилку «залив не той файл».
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"невідомі картки у виписці: {sorted(unknown)}. "
                "Створіть відповідні рахунки перед імпортом.",
            )

        batch = ImportBatch(
            household_id=principal.household_id,
            account_id=default_account.id,
            uploaded_by=principal.user_id,
            source_type=result.source_format,
            file_sha256=hashlib.sha256(data).digest(),
            parser_version=result.parser_version,
            encoding=result.encoding,
            status="parsed",
            rows_total=result.rows_total,
        )
        session.add(batch)
        await session.flush()

        rows = [
            _to_row(tx, principal.household_id, batch.id, by_ref, default_account)
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

        warnings = list(result.warnings)
        if result.account_hints:
            counts = Counter(t.account_hint for t in result.transactions if t.account_hint)
            warnings.append(
                "розподіл по картках: "
                + ", ".join(f"{card}={n}" for card, n in sorted(counts.items()))
            )

        return ImportResultOut(
            batch_id=batch.id,
            bank=result.bank,
            source_format=result.source_format,
            encoding=result.encoding,
            parser_version=result.parser_version,
            rows_total=result.rows_total,
            rows_inserted=inserted,
            rows_duplicate=len(rows) - inserted,
            rows_skipped=result.rows_skipped,
            warnings=warnings[:50],
        )


def _to_row(
    tx: ParsedTransaction,
    household_id: uuid.UUID,
    batch_id: uuid.UUID,
    by_ref: dict[str, Account],
    default_account: Account,
) -> dict[str, object]:
    """Готує рядок для масової вставки, обираючи рахунок для транзакції."""
    account = by_ref.get(tx.account_hint or "", default_account)
    return {
        "household_id": household_id,
        "account_id": account.id,
        "import_batch_id": batch_id,
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
