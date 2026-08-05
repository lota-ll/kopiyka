"""Контракт парсера виписки та реєстр адаптерів.

Архітектурне правило: **банк-специфічний код живе тільки тут**. Усе, що
нижче по стеку (імпорт, категоризація, аналітика), працює виключно з
``ParsedTransaction`` і не знає, з якого банку та з якого формату файлу
рядок прийшов.

Додати новий банк = додати один файл і зареєструвати клас.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from kopiyka.loaders.base import LoadedFile


class ParserError(Exception):
    """Файл не вдалося розібрати."""


class UnknownFormatError(ParserError):
    """Жоден зареєстрований парсер не впізнав формат файлу."""


@dataclass(slots=True)
class ParsedTransaction:
    """Одна транзакція у канонічному вигляді, незалежному від банку."""

    booked_at: datetime
    amount_minor: int  # у валюті операції; від'ємне = витрата
    currency: str
    amount_account_minor: int  # у валюті рахунку
    account_currency: str
    description_raw: str
    mcc: int | None = None
    counterparty: str | None = None
    balance_after_minor: int | None = None
    source_row: int = 0
    # Підказка про рахунок, витягнута з самого рядка (останні цифри картки).
    # Заповнюється, коли один файл містить операції кількох карток
    # (виписка Приват24). Для monobank завжди None: там файл = одна картка.
    account_hint: str | None = None


@dataclass(slots=True)
class ParseResult:
    """Результат розбору файлу разом з діагностикою."""

    bank: str
    parser_version: str
    encoding: str
    source_format: str
    account_currency: str
    transactions: list[ParsedTransaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows_skipped: int = 0

    @property
    def rows_total(self) -> int:
        return len(self.transactions) + self.rows_skipped

    @property
    def account_hints(self) -> set[str]:
        """Усі картки, що зустрілися у файлі."""
        return {t.account_hint for t in self.transactions if t.account_hint}


_CARD_DIGITS = re.compile(r"(\d{4})\s*$")


def normalize_card_ref(raw: str) -> str | None:
    """Зводить номер картки до останніх чотирьох цифр.

    Банки маскують номер по-різному (``51****4441``, ``5168 **** **** 4441``),
    але останні чотири цифри стабільні й достатні для розрізнення карток
    у межах одного household.

    >>> normalize_card_ref("51****4441")
    '4441'
    >>> normalize_card_ref("")
    """
    text = raw.strip()
    if not text:
        return None
    match = _CARD_DIGITS.search(text)
    return match.group(1) if match else None


class StatementParser(abc.ABC):
    """Базовий клас адаптера банку."""

    bank: ClassVar[str]
    version: ClassVar[str]
    COLUMN_HINTS: ClassVar[dict[str, tuple[str, ...]]]
    REQUIRED: ClassVar[tuple[str, ...]]
    HEADER_MARKERS: ClassVar[tuple[str, ...]]

    @classmethod
    @abc.abstractmethod
    def sniff(cls, loaded: LoadedFile) -> bool:
        """Чи схожий цей файл на виписку саме цього банку."""

    @abc.abstractmethod
    def parse(self, loaded: LoadedFile) -> ParseResult:
        """Розбирає завантажену таблицю."""

    # --- спільні хелпери ---------------------------------------------------

    @classmethod
    def find_header(cls, rows: list[list[str]]) -> int:
        """Знаходить рядок заголовків.

        Виписки часто починаються з шапки: у Приват24 перший рядок —
        «Історія операцій за період ...». Тому заголовок шукається за
        маркерами, а не береться як ``rows[0]``.
        """
        for i, row in enumerate(rows[:20]):
            joined = " ".join(row).lower()
            if all(any(h in joined for h in cls.COLUMN_HINTS[m]) for m in cls.HEADER_MARKERS):
                return i
        raise ParserError("не знайдено рядок заголовків")

    @classmethod
    def map_columns(cls, header: list[str]) -> dict[str, int]:
        idx: dict[str, int] = {}
        lowered = [c.strip().lower() for c in header]
        for field_name, hints in cls.COLUMN_HINTS.items():
            for i, col in enumerate(lowered):
                if any(h in col for h in hints) and field_name not in idx:
                    idx[field_name] = i
        missing = [f for f in cls.REQUIRED if f not in idx]
        if missing:
            raise ParserError(f"відсутні обов'язкові колонки: {missing}; заголовок={header}")
        return idx

    @staticmethod
    def cell(row: list[str], idx: dict[str, int], name: str) -> str:
        i = idx.get(name)
        if i is None or i >= len(row):
            return ""
        return row[i].strip()


_REGISTRY: list[type[StatementParser]] = []


def register(cls: type[StatementParser]) -> type[StatementParser]:
    _REGISTRY.append(cls)
    return cls


def registered_parsers() -> list[type[StatementParser]]:
    return list(_REGISTRY)


def detect_parser(loaded: LoadedFile) -> type[StatementParser]:
    """Обирає парсер за сигнатурою заголовка."""
    for cls in _REGISTRY:
        if cls.sniff(loaded):
            return cls
    raise UnknownFormatError(
        "формат не розпізнано; перевірені: " + ", ".join(c.bank for c in _REGISTRY)
    )


DATE_FORMATS = (
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def parse_datetime(raw: str) -> datetime:
    text = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ParserError(f"невідомий формат дати: {raw!r}")
