"""Контракт парсера виписки та реєстр адаптерів.

Архітектурне правило: **банк-специфічний код живе тільки тут**. Усе, що
нижче по стеку (імпорт, категоризація, аналітика), працює виключно з
``ParsedTransaction`` і не знає, з якого банку рядок прийшов.

Додати новий банк = додати один файл і зареєструвати клас. Нічого більше
в проєкті змінювати не потрібно.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar


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


@dataclass(slots=True)
class ParseResult:
    """Результат розбору файлу разом з діагностикою."""

    bank: str
    parser_version: str
    encoding: str
    account_currency: str
    transactions: list[ParsedTransaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows_skipped: int = 0

    @property
    def rows_total(self) -> int:
        return len(self.transactions) + self.rows_skipped


class StatementParser(abc.ABC):
    """Базовий клас адаптера банку."""

    bank: ClassVar[str]
    version: ClassVar[str]

    @classmethod
    @abc.abstractmethod
    def sniff(cls, text: str) -> bool:
        """Чи схожий цей текст на виписку саме цього банку.

        Дивиться тільки на заголовок — рішення має бути дешевим.
        """

    @abc.abstractmethod
    def parse(self, text: str, *, encoding: str) -> ParseResult:
        """Розбирає повний текст файлу."""


_REGISTRY: list[type[StatementParser]] = []


def register(cls: type[StatementParser]) -> type[StatementParser]:
    """Декоратор реєстрації адаптера."""
    _REGISTRY.append(cls)
    return cls


def registered_parsers() -> list[type[StatementParser]]:
    return list(_REGISTRY)


def detect_parser(text: str) -> type[StatementParser]:
    """Обирає парсер за сигнатурою заголовка."""
    head = "\n".join(text.splitlines()[:5])
    for cls in _REGISTRY:
        if cls.sniff(head):
            return cls
    raise UnknownFormatError(
        "формат не розпізнано; перевірені: " + ", ".join(c.bank for c in _REGISTRY)
    )
