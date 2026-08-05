"""Лоадер CSV з детекцією кодування та роздільника."""

from __future__ import annotations

import csv
import io

from kopiyka.loaders.base import FileLoader, LoadedFile, LoaderError, clean, register_loader
from kopiyka.loaders.encoding import decode_statement

# Порядок перевірки роздільників має значення: у виписках із комою як
# десятковим роздільником крапка з комою зустрічається частіше.
_DELIMITERS = (",", ";", "\t")


@register_loader
class CsvLoader(FileLoader):
    fmt = "csv"

    @classmethod
    def sniff(cls, data: bytes, filename: str | None) -> bool:
        # XLSX — це ZIP, він починається з 'PK'. Усе інше вважаємо текстом.
        if data[:2] == b"PK":
            return False
        return not (filename and filename.lower().endswith((".xlsx", ".xls")))

    def load(self, data: bytes) -> LoadedFile:
        text, encoding = decode_statement(data)
        delimiter = self._detect_delimiter(text)
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = [[clean(cell) for cell in row] for row in reader if any(c.strip() for c in row)]
        if not rows:
            raise LoaderError("файл не містить рядків")
        return LoadedFile(rows=rows, source_format=self.fmt, encoding=encoding)

    @staticmethod
    def _detect_delimiter(text: str) -> str:
        lines = text.splitlines()
        head = lines[0] if lines else ""
        # Рахуємо лише поза лапками: "Сума в валюті картки (UAH)" містить
        # коми всередині, і наївний count() дав би хибний результат.
        outside = []
        in_quotes = False
        for char in head:
            if char == '"':
                in_quotes = not in_quotes
            elif not in_quotes:
                outside.append(char)
        cleaned = "".join(outside)
        return max(_DELIMITERS, key=cleaned.count)
