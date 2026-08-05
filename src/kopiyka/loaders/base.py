"""Шар завантаження файлів.

Розділення відповідальності, яке з'явилося у v0.2.0:

* ``loaders/``  — **як прочитати файл** (CSV, XLSX, у майбутньому PDF/HTML);
* ``parsers/``  — **як зрозуміти колонки** конкретного банку.

Це дві незалежні осі. Моно віддає CSV, приват — XLSX, але обидва мають
власні мапи колонок. Якщо завтра моно перейде на XLSX, зміниться лише
рядок детекції формату, а парсер лишиться недоторканим.

Контракт між шарами — ``LoadedFile.rows``: таблиця рядків **уже у вигляді
рядків тексту**. Жодних ``float``, жодних ``datetime`` — конвертація типів
Excel відбувається в лоадері, бо саме там знаходиться знання про формат.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import ClassVar

# Символи, якими банки позначають «значення відсутнє».
# Довге тире тут критичне: monobank ставить «—» у колонках курсу,
# комісії та кешбеку. Без цього списку нормалізатор грошей прийняв би
# його за знак мінус (U+2014 → '-') і впав на порожньому числі.
EMPTY_MARKERS = frozenset({"", "-", "\u2013", "\u2014", "\u2212", "n/a", "N/A"})


class LoaderError(Exception):
    """Файл не вдалося прочитати."""


class UnsupportedFileError(LoaderError):
    """Формат файлу не підтримується."""


@dataclass(slots=True)
class LoadedFile:
    """Табличні дані, витягнуті з файлу, разом з діагностикою."""

    rows: list[list[str]]
    source_format: str  # csv | xlsx
    encoding: str  # для xlsx завжди 'utf-8' (XML всередині ZIP)
    sheet: str | None = None
    warnings: list[str] = field(default_factory=list)

    def head_text(self, limit: int = 15) -> str:
        """Перші рядки одним текстом — для детекції банку."""
        return "\n".join(" ".join(cell for cell in row) for row in self.rows[:limit]).lower()


def clean(value: str) -> str:
    """Нормалізує комірку: тире-заповнювачі стають порожнім рядком."""
    text = value.strip()
    return "" if text in EMPTY_MARKERS else text


class FileLoader(abc.ABC):
    """Базовий клас лоадера."""

    fmt: ClassVar[str]

    @classmethod
    @abc.abstractmethod
    def sniff(cls, data: bytes, filename: str | None) -> bool:
        """Чи цей лоадер уміє читати такий файл."""

    @abc.abstractmethod
    def load(self, data: bytes) -> LoadedFile:
        """Читає файл у табличний вигляд."""


_LOADERS: list[type[FileLoader]] = []


def register_loader(cls: type[FileLoader]) -> type[FileLoader]:
    _LOADERS.append(cls)
    return cls


def load_any(data: bytes, filename: str | None = None) -> LoadedFile:
    """Обирає лоадер за вмістом файлу (не лише за розширенням)."""
    if not data:
        raise LoaderError("порожній файл")
    for cls in _LOADERS:
        if cls.sniff(data, filename):
            return cls().load(data)
    raise UnsupportedFileError(
        "непідтримуваний формат; доступні: " + ", ".join(c.fmt for c in _LOADERS)
    )
