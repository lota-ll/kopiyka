"""Робота з грошима.

Єдине правило цього модуля: **гроші ніколи не є float**.
Усередині системи сума — це ``int`` у мінорних одиницях (копійках).
Конвертація у/з людського представлення відбувається тільки тут.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Кількість знаків після коми для валют, де вона не дорівнює 2.
# ISO 4217 exponent; більшість валют = 2, тому тут лише винятки.
_MINOR_UNITS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "ISK": 0,
    "CLP": 0,
    "VND": 0,
    "BHD": 3,
    "KWD": 3,
    "TND": 3,
    "JOD": 3,
}

# Виписки містять пробіли-роздільники тисяч у різних варіантах:
# звичайний пробіл, NBSP (\xa0), narrow NBSP (\u202f), апостроф.
_THOUSAND_SEP = re.compile(r"[\s\u00a0\u202f']")
_ALLOWED = re.compile(r"^-?\d+(\.\d+)?$")


class MoneyParseError(ValueError):
    """Рядок не вдалося інтерпретувати як грошову суму."""


def minor_units(currency: str) -> int:
    """Скільки знаків після коми має валюта."""
    return _MINOR_UNITS.get(currency.upper(), 2)


def parse_amount(raw: str | int | float | Decimal, currency: str = "UAH") -> int:
    """Перетворює суму з виписки на ціле число мінорних одиниць.

    Обробляє реальні варіації банківських експортів:

    >>> parse_amount("-1 234,56")
    -123456
    >>> parse_amount("1'234.50")
    123450
    >>> parse_amount("−100,00")   # U+2212 MINUS SIGN, трапляється у PDF/HTML
    -10000
    >>> parse_amount("(50,00)")   # бухгалтерські дужки = від'ємне
    -5000
    >>> parse_amount("100", "JPY")
    100

    ``float`` приймається лише заради зручності тестів і одразу
    переганяється через ``Decimal(str(...))``, щоб не тягнути двійкову похибку.
    """
    if isinstance(raw, bool):
        # bool — підклас int; тиха інтерпретація True як 1 копійки була б багом.
        raise MoneyParseError("bool не є грошовою сумою")
    if isinstance(raw, int):
        # Уже мінорні одиниці (наприклад, monobank API віддає копійки цілим).
        return raw
    if isinstance(raw, Decimal):
        return _quantize(raw, currency)
    if isinstance(raw, float):
        return _quantize(Decimal(str(raw)), currency)

    text = raw.strip()
    if not text:
        raise MoneyParseError("порожній рядок суми")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()

    # Нормалізація unicode-мінусів до ASCII.
    text = text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")

    # Прибираємо символи валюти та роздільники тисяч.
    text = re.sub(r"[^\d,.\-+]", "", _THOUSAND_SEP.sub("", text))
    text = text.lstrip("+")

    if text.count(",") and text.count("."):
        # Останній роздільник — десятковий, попередній був тисячним.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")

    if not _ALLOWED.match(text):
        raise MoneyParseError(f"не схоже на суму: {raw!r}")

    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover — захищено регуляркою вище
        raise MoneyParseError(f"не схоже на суму: {raw!r}") from exc

    if negative:
        value = -value
    return _quantize(value, currency)


def _quantize(value: Decimal, currency: str) -> int:
    exp = minor_units(currency)
    scaled = value.scaleb(exp)
    rounded = scaled.to_integral_value(rounding="ROUND_HALF_UP")
    if scaled != rounded:
        # Виписка містить більше знаків, ніж має валюта — округлення тут
        # означає втрату даних, тому це помилка, а не тихе приведення.
        raise MoneyParseError(f"сума {value} має більше знаків, ніж дозволяє {currency}")
    return int(rounded)


def format_amount(minor: int, currency: str = "UAH") -> str:
    """Зворотне перетворення — для UI та логів.

    >>> format_amount(-123456)
    '-1234.56'
    """
    exp = minor_units(currency)
    if exp == 0:
        return str(minor)
    return f"{Decimal(minor).scaleb(-exp):.{exp}f}"
