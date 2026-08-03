"""Тести парсерів на golden-фікстурах.

Ці тести — головний захист від «банк тихо змінив формат». Фікстури
навмисно містять брудні реальні випадки: NBSP у сумах, cp1251, рядок
без часу, порожній рядок, зіпсований рядок.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from kopiyka.domain.dedup import dedup_hash, normalize_description
from kopiyka.domain.money import MoneyParseError, format_amount, parse_amount
from kopiyka.parsers import mono, privat  # noqa: F401 — реєстрація адаптерів
from kopiyka.parsers.base import UnknownFormatError, detect_parser
from kopiyka.parsers.encoding import decode_statement

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    data = (FIXTURES / name).read_bytes()
    text, encoding = decode_statement(data)
    parser_cls = detect_parser(text)
    return parser_cls().parse(text, encoding=encoding)


# --- money ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-1 234,56", -123456),
        ("1\u00a0234,56", 123456),  # NBSP як роздільник тисяч
        ("1'234.50", 123450),
        ("−100,00", -10000),  # U+2212
        ("(50,00)", -5000),  # бухгалтерські дужки
        ("0,01", 1),
        ("1,234.56", 123456),  # англійський формат
        ("100 UAH", 10000),
    ],
)
def test_parse_amount_variants(raw: str, expected: int) -> None:
    assert parse_amount(raw) == expected


def test_parse_amount_zero_decimal_currency() -> None:
    assert parse_amount("1500", "JPY") == 1500


def test_parse_amount_rejects_garbage() -> None:
    with pytest.raises(MoneyParseError):
        parse_amount("не сума")


def test_parse_amount_rejects_excess_precision() -> None:
    # Тихе округлення фінансових даних неприпустиме.
    with pytest.raises(MoneyParseError):
        parse_amount("10,005")


def test_format_roundtrip() -> None:
    assert format_amount(parse_amount("-1 234,56")) == "-1234.56"


# --- dedup ----------------------------------------------------------------


def test_normalize_description_collapses_noise() -> None:
    assert normalize_description("  SILPO  ,  KYIV\u00a0 ") == "silpo kyiv"


def test_dedup_hash_is_stable() -> None:
    args = {
        "account_ref": "4441",
        "booked_at": datetime(2026, 1, 15, 12, 30),
        "amount_minor": -12345,
        "currency": "UAH",
        "description": "SILPO KYIV",
    }
    assert dedup_hash(**args) == dedup_hash(**args)


def test_dedup_hash_ignores_seconds() -> None:
    base = {
        "account_ref": "4441",
        "amount_minor": -12345,
        "currency": "UAH",
        "description": "SILPO",
    }
    a = dedup_hash(booked_at=datetime(2026, 1, 15, 12, 30, 0), **base)
    b = dedup_hash(booked_at=datetime(2026, 1, 15, 12, 30, 59), **base)
    assert a == b


def test_dedup_hash_differs_on_amount() -> None:
    base = {
        "account_ref": "4441",
        "booked_at": datetime(2026, 1, 15, 12, 30),
        "currency": "UAH",
        "description": "SILPO",
    }
    assert dedup_hash(amount_minor=-100, **base) != dedup_hash(amount_minor=-200, **base)


# --- encoding -------------------------------------------------------------


def test_detects_cp1251() -> None:
    data = "Дата;Опис операції;Сума".encode("cp1251")
    text, encoding = decode_statement(data)
    assert encoding == "cp1251"
    assert "Опис" in text


def test_detects_utf8_bom() -> None:
    data = "Дата;Сума".encode("utf-8-sig")
    text, encoding = decode_statement(data)
    assert encoding == "utf-8"
    assert text.startswith("Дата")


# --- mono -----------------------------------------------------------------


def test_mono_parses_fixture() -> None:
    result = _load("mono_sample.csv")
    assert result.bank == "mono"
    assert len(result.transactions) == 4

    first = result.transactions[0]
    assert first.booked_at == datetime(2026, 1, 15, 12, 30, 45)
    assert first.amount_account_minor == -24750
    assert first.mcc == 5411
    assert first.currency == "UAH"


def test_mono_handles_foreign_currency() -> None:
    result = _load("mono_sample.csv")
    foreign = [t for t in result.transactions if t.currency != "UAH"]
    assert len(foreign) == 1
    assert foreign[0].currency == "PLN"
    # Сума операції та сума у валюті рахунку зберігаються окремо.
    assert foreign[0].amount_minor != foreign[0].amount_account_minor


def test_mono_skips_broken_row_without_failing() -> None:
    result = _load("mono_sample.csv")
    assert result.rows_skipped == 1
    assert result.warnings


# --- privat ---------------------------------------------------------------


def test_privat_parses_cp1251_fixture() -> None:
    result = _load("privat_sample.csv")
    assert result.bank == "privat"
    assert result.encoding == "cp1251"
    assert len(result.transactions) == 3
    assert all(t.mcc is None for t in result.transactions)


def test_privat_row_without_time_defaults_to_midnight() -> None:
    result = _load("privat_sample.csv")
    midnight = [t for t in result.transactions if t.booked_at.hour == 0]
    assert midnight


# --- detection ------------------------------------------------------------


def test_unknown_format_raises() -> None:
    with pytest.raises(UnknownFormatError):
        detect_parser("foo,bar,baz\n1,2,3")


def test_parsers_do_not_claim_each_others_files() -> None:
    """Регресія: sniff() двох банків не повинні перетинатися."""
    mono_text = (FIXTURES / "mono_sample.csv").read_text("utf-8")
    privat_text = decode_statement((FIXTURES / "privat_sample.csv").read_bytes())[0]
    assert detect_parser(mono_text).bank == "mono"
    assert detect_parser(privat_text).bank == "privat"
