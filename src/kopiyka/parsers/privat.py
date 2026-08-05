"""Адаптер виписки Приват24 (XLSX).

Мапа колонок звірена з реальним експортом (серпень 2026), аркуш «Виписки»:

    Дата | Категорія | Картка | Опис операції | Сума в валюті картки |
    Валюта картки | Сума в валюті транзакції | Валюта транзакції |
    Залишок на кінець періоду | Валюта залишку

Особливості формату:
  * перший рядок — шапка «Історія операцій за період …», не заголовок;
  * **один файл містить операції кількох карток** → заповнюється
    ``account_hint``, і рядки розкладаються по рахунках при імпорті;
  * колонка «Картка» подекуди порожня (комісії, службові операції) —
    такі рядки отримують ``account_hint=None`` і йдуть у рахунок за
    замовчуванням, зазначений у запиті;
  * MCC відсутній → категоризація спирається на «Категорію» банку та
    regex по опису;
  * суми у XLSX приходять як ``float`` і конвертуються лоадером.
"""

from __future__ import annotations

from typing import ClassVar

from kopiyka.domain.attribution import attribute_orphans
from kopiyka.domain.money import MoneyParseError, parse_amount
from kopiyka.loaders.base import LoadedFile
from kopiyka.parsers.base import (
    ParsedTransaction,
    ParserError,
    ParseResult,
    StatementParser,
    normalize_card_ref,
    parse_datetime,
    register,
)


@register
class PrivatParser(StatementParser):
    bank = "privat"
    version = "privat-2"

    COLUMN_HINTS: ClassVar[dict[str, tuple[str, ...]]] = {
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
    REQUIRED: ClassVar[tuple[str, ...]] = ("date", "description", "amount_account")
    HEADER_MARKERS: ClassVar[tuple[str, ...]] = ("date", "amount_account")

    @classmethod
    def sniff(cls, loaded: LoadedFile) -> bool:
        head = loaded.head_text()
        has_card_currency = any(h in head for h in cls.COLUMN_HINTS["currency_account"])
        return has_card_currency and "mcc" not in head

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
                tx = self._parse_row(row, idx, line)
            except (ParserError, MoneyParseError, ValueError) as exc:
                result.rows_skipped += 1
                result.warnings.append(f"рядок {line}: {exc}")
                continue
            result.transactions.append(tx)

        if result.transactions:
            result.account_currency = result.transactions[0].account_currency

        # Операції без номера картки (списання відсотків за кредитом тощо)
        # відносяться до рахунку за неперервністю ланцюга залишків.
        report = attribute_orphans(result.transactions)
        result.warnings.extend(report.warnings)

        if len(result.account_hints) > 1:
            result.warnings.append(
                "у файлі кілька карток: " + ", ".join(sorted(result.account_hints))
            )

        return result

    def _parse_row(self, row: list[str], idx: dict[str, int], line: int) -> ParsedTransaction:
        stamp = self.cell(row, idx, "date")
        if self.cell(row, idx, "time"):
            stamp = f"{stamp} {self.cell(row, idx, 'time')}"
        booked_at = parse_datetime(stamp)

        account_currency = (self.cell(row, idx, "currency_account") or "UAH").upper()[:3]
        amount_account = parse_amount(self.cell(row, idx, "amount_account"), account_currency)

        op_currency = (self.cell(row, idx, "currency_operation") or account_currency).upper()[:3]
        raw_operation = self.cell(row, idx, "amount_operation")
        amount_operation = (
            parse_amount(raw_operation, op_currency) if raw_operation else amount_account
        )

        balance_raw = self.cell(row, idx, "balance")

        return ParsedTransaction(
            booked_at=booked_at,
            amount_minor=amount_operation,
            currency=op_currency,
            amount_account_minor=amount_account,
            account_currency=account_currency,
            description_raw=self.cell(row, idx, "description"),
            mcc=None,  # Приват не віддає MCC
            counterparty=self.cell(row, idx, "category") or None,
            balance_after_minor=parse_amount(balance_raw, account_currency)
            if balance_raw
            else None,
            source_row=line,
            account_hint=normalize_card_ref(self.cell(row, idx, "card")),
        )
