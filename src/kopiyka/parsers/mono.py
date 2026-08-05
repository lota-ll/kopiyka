"""Адаптер виписки monobank.

Мапа колонок звірена з реальним CSV-експортом (серпень 2026):

    "Дата i час операції","Деталі операції",MCC,"Сума в валюті картки (UAH)",
    "Сума в валюті операції",Валюта,Курс,"Сума комісій (UAH)",
    "Сума кешбеку (UAH)","Залишок після операції"

Особливості формату:
  * десятковий роздільник — **крапка** (``-113.98``);
  * порожні значення позначаються довгим тире ``—`` (обробляється лоадером);
  * колонки з номером картки **немає** — monobank віддає окремий файл на
    кожну картку, тому ``account_hint`` завжди ``None``;
  * у заголовку «Дата i час» літера ``i`` — латинська; тому в підказках
    присутні обидва варіанти написання.
"""

from __future__ import annotations

from typing import ClassVar

from kopiyka.domain.money import MoneyParseError, parse_amount
from kopiyka.loaders.base import LoadedFile
from kopiyka.parsers.base import (
    ParsedTransaction,
    ParserError,
    ParseResult,
    StatementParser,
    parse_datetime,
    register,
)


@register
class MonoParser(StatementParser):
    bank = "mono"
    version = "mono-2"

    COLUMN_HINTS: ClassVar[dict[str, tuple[str, ...]]] = {
        "datetime": ("дата i час", "дата і час", "date and time"),
        "description": ("деталі операції", "опис", "description"),
        "mcc": ("mcc",),
        "amount_account": ("сума в валюті картки", "сума у валюті картки"),
        "amount_operation": ("сума в валюті операції", "сума у валюті операції"),
        "currency": ("валюта",),
        "balance": ("залишок після операції", "залишок"),
    }
    REQUIRED: ClassVar[tuple[str, ...]] = ("datetime", "description", "amount_account")
    HEADER_MARKERS: ClassVar[tuple[str, ...]] = ("datetime", "amount_account")

    @classmethod
    def sniff(cls, loaded: LoadedFile) -> bool:
        head = loaded.head_text()
        has_card_amount = any(h in head for h in cls.COLUMN_HINTS["amount_account"])
        return has_card_amount and "mcc" in head

    def parse(self, loaded: LoadedFile) -> ParseResult:
        rows = loaded.rows
        header_idx = self.find_header(rows)
        idx = self.map_columns(rows[header_idx])

        result = ParseResult(
            bank=self.bank,
            parser_version=self.version,
            encoding=loaded.encoding,
            source_format=loaded.source_format,
            account_currency="UAH",
            warnings=list(loaded.warnings),
        )

        for line, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
            try:
                result.transactions.append(self._parse_row(row, idx, line))
            except (ParserError, MoneyParseError, ValueError) as exc:
                result.rows_skipped += 1
                result.warnings.append(f"рядок {line}: {exc}")

        return result

    def _parse_row(self, row: list[str], idx: dict[str, int], line: int) -> ParsedTransaction:
        booked_at = parse_datetime(self.cell(row, idx, "datetime"))
        currency = (self.cell(row, idx, "currency") or "UAH").upper()[:3]
        amount_account = parse_amount(self.cell(row, idx, "amount_account"), "UAH")

        raw_operation = self.cell(row, idx, "amount_operation")
        amount_operation = (
            parse_amount(raw_operation, currency) if raw_operation else amount_account
        )

        mcc_raw = self.cell(row, idx, "mcc")
        balance_raw = self.cell(row, idx, "balance")

        return ParsedTransaction(
            booked_at=booked_at,
            amount_minor=amount_operation,
            currency=currency,
            amount_account_minor=amount_account,
            account_currency="UAH",
            description_raw=self.cell(row, idx, "description"),
            mcc=int(mcc_raw) if mcc_raw.isdigit() else None,
            balance_after_minor=parse_amount(balance_raw, "UAH") if balance_raw else None,
            source_row=line,
            account_hint=None,  # один файл = одна картка
        )
