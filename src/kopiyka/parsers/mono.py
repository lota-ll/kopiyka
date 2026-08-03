"""Адаптер CSV-експорту monobank.

⚠️ Мапа колонок нижче складена за типовим виглядом експорту і **має бути
звірена з твоїм реальним файлом** у Задачі 1.2 тижня 1. Банк міняє
формулювання заголовків без попередження — саме тому вони винесені в
константу, а не зашиті в код розбору.
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

# Ключ — канонічна назва поля, значення — фрагменти реальних заголовків
# у нижньому регістрі. Пошук іде за входженням підрядка.
COLUMN_HINTS: dict[str, tuple[str, ...]] = {
    "datetime": ("дата i час", "дата і час", "date and time"),
    "description": ("деталі операції", "опис", "description"),
    "mcc": ("mcc",),
    "amount_account": ("сума в валюті картки", "сума у валюті картки", "card currency amount"),
    "amount_operation": ("сума в валюті операції", "сума у валюті операції", "operation amount"),
    "currency": ("валюта",),
    "balance": ("залишок", "balance"),
}

DATE_FORMATS = ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S")


@register
class MonoParser(StatementParser):
    bank = "mono"
    version = "mono-csv-1"

    @classmethod
    def sniff(cls, text: str) -> bool:
        head = text.lower()
        has_mono_columns = any(h in head for h in COLUMN_HINTS["amount_account"])
        return has_mono_columns and "mcc" in head

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
        """Експорт може містити рядки-шапку перед таблицею."""
        for i, row in enumerate(rows[:15]):
            joined = " ".join(row).lower()
            if any(h in joined for h in COLUMN_HINTS["datetime"]):
                return i
        raise ParserError("не знайдено рядок заголовків")

    @staticmethod
    def _map_columns(header: list[str]) -> dict[str, int]:
        idx: dict[str, int] = {}
        for field_name, hints in COLUMN_HINTS.items():
            for i, col in enumerate(header):
                if any(h in col for h in hints) and field_name not in idx:
                    idx[field_name] = i
        required = ("datetime", "description", "amount_account")
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

        booked_at = _parse_datetime(cell("datetime"))
        currency = (cell("currency") or "UAH").upper()[:3]
        amount_account = parse_amount(cell("amount_account"), "UAH")

        raw_operation = cell("amount_operation")
        amount_operation = (
            parse_amount(raw_operation, currency) if raw_operation else amount_account
        )

        mcc_raw = cell("mcc")
        mcc = int(mcc_raw) if mcc_raw.isdigit() else None

        balance_raw = cell("balance")
        balance = parse_amount(balance_raw, "UAH") if balance_raw else None

        return ParsedTransaction(
            booked_at=booked_at,
            amount_minor=amount_operation,
            currency=currency,
            amount_account_minor=amount_account,
            account_currency="UAH",
            description_raw=cell("description"),
            mcc=mcc,
            balance_after_minor=balance,
            source_row=line,
        )


def _parse_datetime(raw: str) -> datetime:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise ParserError(f"невідомий формат дати: {raw!r}")
