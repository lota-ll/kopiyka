"""Детекція кодування виписки.

Чому це окремий модуль: monobank віддає UTF-8 (часто з BOM), а експорт
Приват24 історично зустрічається у windows-1251. Якщо вгадати неправильно,
кирилиця перетвориться на кашу, опис операції зіпсується — і зіпсується
``dedup_hash``, тобто повторний імпорт створить дублікати. Кодування тут
не косметика, а частина коректності.
"""

from __future__ import annotations

from charset_normalizer import from_bytes

# Порядок має значення: перевіряємо найімовірніші кодування явно, і лише
# потім віддаємо справу статистичному детектору.
_CANDIDATES = ("utf-8-sig", "utf-8", "cp1251", "cp1252")

# Символи, поява яких означає, що кодування вгадано неправильно
# (типова «кракозябра» при читанні cp1251 як utf-8 і навпаки).
_MOJIBAKE = ("Ð", "Ñ", "�", "Ã¯", "Â ")


def decode_statement(data: bytes) -> tuple[str, str]:
    """Повертає ``(text, encoding_name)``.

    >>> decode_statement("Дата,Сума".encode("cp1251"))[1]
    'cp1251'
    """
    if not data:
        raise ValueError("порожній файл")

    for enc in _CANDIDATES:
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if _looks_sane(text):
            return text, "utf-8" if enc == "utf-8-sig" else enc

    best = from_bytes(data).best()
    if best is None:
        raise ValueError("не вдалося визначити кодування файлу")
    return str(best), best.encoding


def _looks_sane(text: str) -> bool:
    """Груба евристика: у виписці не має бути mojibake-послідовностей."""
    sample = text[:4000]
    return not any(marker in sample for marker in _MOJIBAKE)
