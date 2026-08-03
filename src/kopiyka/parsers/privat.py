"""Адаптер CSV-експорту Приват24.

Відмінності від mono, які визначили дизайн:
  * MCC у виписці зазвичай **відсутній** → категоризація спирається на
    regex по опису (див. ``kopiyka.categorize.rules``);
  * трапляється кодування cp1251;
  * дата подекуди без часу → підставляється 00:00, і саме тому
    ``dedup_hash`` округлює час до хвилини.

⚠️ Мапу колонок звір із реальним експортом (Задача 1.2).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from kopiyka.domain.money import MoneyParseError, parse_amount
from kopiyka.parsers.base import (
    ParsedTransaction,
    ParserError,
    ParseResult,
    StatementParser,
    register,
)

COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "date": ("дата",),
    "time": ("час",),
    "category": ("категорія", "категория"),
    "card": ("картка", "карта"),
    "description": ("опис операції", "описание операции", "опис"),
    "amount_account": ("сума в валюті картки", "сумма в валюте карты"),
    "currency_account": ("валюта картки", "валюта карты"),
    "amount_operation": ("сума в валюті транзакції", "сумма в валюте транзакции"),
    "currency_operation": ("валюта транзакції", "валюта транзакции"),
    "balance": ("залишок на кінець періоду", "остаток на конец периода"),
}

DATE_FORMATS = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d")


@register
class PrivatParser(StatementParser):
    bank = "privat"
    version = "privat-csv-1"

    @classmethod
    def sniff(cls, text: str) -> bool:
        head = text.lower()
        has_card_currency = any(h in head for h in COLUMN_HINTS["currency_account"])
        return has_card_currency and "mcc" not in head

    def parse(self, text: str, *, encoding: str) -> ParseResult:
        reader = csv.reader(io.StringIO(text), delimiter=self._delimiter(text))
        rows = [r for r in reader if any(cell.strip() for cell in r)]
        if not rows:
            raise ParserError("файл не містить рядків")

        header_idx = self._find_header(rows)
        header = [c.strip().lower() for c in rows[header_idx]]
        idx = self._map_columns(header)

        result = ParseResult(
            bank=self.bank,
            parser_version=self.version,
            encoding=encoding,
            account_currency="UAH",
        )

        for offset, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
            try:
                tx = self._parse_row(row, idx, offset)
            except (ParserError, MoneyParseError, ValueError) as exc:
                result.rows_skipped += 1
                result.warnings.append(f"рядок {offset}: {exc}")
                continue
            result.transactions.append(tx)

        if result.transactions:
            result.account_currency = result.transactions[0].account_currency
        return result

    @staticmethod
    def _delimiter(text: str) -> str:
        head = text.splitlines()[0] if text.splitlines() else ""
        return ";" if head.count(";") > head.count(",") else ","

    @staticmethod
    def _find_header(rows: list[list[str]]) -> int:
        for i, row in enumerate(rows[:15]):
            joined = " ".join(row).lower()
            if any(h in joined for h in COLUMN_HINTS["date"]) and any(
                h in joined for h in COLUMN_HINTS["amount_account"]
            ):
                return i
        raise ParserError("не знайдено рядок заголовків")

    @staticmethod
    def _map_columns(header: list[str]) -> dict[str, int]:
        idx: dict[str, int] = {}
        for field_name, hints in COLUMN_HINTS.items():
            for i, col in enumerate(header):
                if any(h in col for h in hints) and field_name not in idx:
                    idx[field_name] = i
        required = ("date", "description", "amount_account")
        missing = [f for f in required if f not in idx]
        if missing:
            raise ParserError(f"відсутні обов'язкові колонки: {missing}; заголовок={header}")
        return idx

    def _parse_row(self, row: list[str], idx: dict[str, int], line: int) -> ParsedTransaction:
        def cell(name: str) -> str:
            i = idx.get(name)
            if i is None or i >= len(row):
                return ""
            return row[i].strip()

        stamp = cell("date")
        if cell("time"):
            stamp = f"{stamp} {cell('time')}"
        booked_at = _parse_datetime(stamp)

        account_currency = (cell("currency_account") or "UAH").upper()[:3]
        amount_account = parse_amount(cell("amount_account"), account_currency)

        op_currency = (cell("currency_operation") or account_currency).upper()[:3]
        raw_operation = cell("amount_operation")
        amount_operation = (
            parse_amount(raw_operation, op_currency) if raw_operation else amount_account
        )

        balance_raw = cell("balance")
        balance = parse_amount(balance_raw, account_currency) if balance_raw else None

        return ParsedTransaction(
            booked_at=booked_at,
            amount_minor=amount_operation,
            currency=op_currency,
            amount_account_minor=amount_account,
            account_currency=account_currency,
            description_raw=cell("description"),
            mcc=None,  # Приват не віддає MCC у CSV
            counterparty=cell("category") or None,
            balance_after_minor=balance,
            source_row=line,
        )


def _parse_datetime(raw: str) -> datetime:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    raise ParserError(f"невідомий формат дати: {raw!r}")
