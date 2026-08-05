"""Тести лоадерів і парсерів на golden-фікстурах.

Фікстури відтворюють **реальну** структуру виписок (серпень 2026):
monobank CSV з крапкою як десятковим роздільником і довгим тире замість
порожніх значень; Приват24 XLSX із шапкою, двома картками та рядком без
номера картки.
"""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from pathlib import Path

import pytest

from kopiyka.domain.dedup import dedup_hash, normalize_description
from kopiyka.domain.money import MoneyParseError, format_amount, parse_amount
from kopiyka.loaders import csv_loader, xlsx_loader  # noqa: F401 — реєстрація
from kopiyka.loaders.base import LoaderError, load_any
from kopiyka.loaders.encoding import decode_statement
from kopiyka.parsers import mono, privat  # noqa: F401 — реєстрація
from kopiyka.parsers.base import UnknownFormatError, detect_parser, normalize_card_ref

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    loaded = load_any((FIXTURES / name).read_bytes(), name)
    return detect_parser(loaded)().parse(loaded)


# --- money ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("-113.98", -11398),  # реальний формат monobank
        ("-1 234,56", -123456),
        ("1\u00a0234,56", 123456),
        ("1'234.50", 123450),
        ("(50,00)", -5000),
        ("0,01", 1),
        ("-74.9", -7490),
    ],
)
def test_parse_amount_variants(raw: str, expected: int) -> None:
    assert parse_amount(raw) == expected


def test_parse_amount_rejects_excess_precision() -> None:
    with pytest.raises(MoneyParseError):
        parse_amount("10,005")


def test_parse_amount_rejects_bool() -> None:
    with pytest.raises(MoneyParseError):
        parse_amount(True)  # type: ignore[arg-type]


def test_format_roundtrip() -> None:
    assert format_amount(parse_amount("-1 234,56")) == "-1234.56"


# --- dedup ----------------------------------------------------------------


def test_normalize_description_collapses_noise() -> None:
    assert normalize_description("  SILPO  ,  KYIV\u00a0 ") == "silpo kyiv"


def test_dedup_hash_ignores_seconds() -> None:
    base = {"account_ref": "4441", "amount_minor": -12345, "currency": "UAH", "description": "X"}
    a = dedup_hash(booked_at=datetime(2026, 1, 15, 12, 30, 0), **base)
    b = dedup_hash(booked_at=datetime(2026, 1, 15, 12, 30, 59), **base)
    assert a == b


def test_dedup_hash_differs_per_account() -> None:
    base = {
        "booked_at": datetime(2026, 1, 15, 12, 30),
        "amount_minor": -100,
        "currency": "UAH",
        "description": "X",
    }
    assert dedup_hash(account_ref="4441", **base) != dedup_hash(account_ref="9012", **base)


# --- loaders --------------------------------------------------------------


def test_detects_cp1251() -> None:
    text, encoding = decode_statement("Дата;Опис операції;Сума".encode("cp1251"))
    assert encoding == "cp1251"
    assert "Опис" in text


def test_csv_loader_strips_dash_placeholders() -> None:
    """Довге тире «—» у monobank означає «порожньо», а не мінус."""
    loaded = load_any((FIXTURES / "mono_sample.csv").read_bytes(), "mono_sample.csv")
    header = [c.lower() for c in loaded.rows[0]]
    rate_idx = next(i for i, c in enumerate(header) if "курс" in c)
    assert loaded.rows[1][rate_idx] == ""


def test_csv_loader_delimiter_ignores_quoted_commas() -> None:
    """«Сума в валюті картки (UAH)» містить кому всередині лапок."""
    loaded = load_any((FIXTURES / "mono_sample.csv").read_bytes(), "mono_sample.csv")
    assert len(loaded.rows[0]) == 10


def test_xlsx_loader_converts_float_without_binary_error() -> None:
    """openpyxl віддає суми як float — конвертація має бути точною."""
    loaded = load_any((FIXTURES / "privat_sample.xlsx").read_bytes(), "privat_sample.xlsx")
    assert loaded.source_format == "xlsx"
    flat = [cell for row in loaded.rows for cell in row]
    assert "-312.4" in flat or "-312.40" in flat
    assert not any("0000000" in cell for cell in flat)


def test_xlsx_loader_picks_statement_sheet() -> None:
    loaded = load_any((FIXTURES / "privat_sample.xlsx").read_bytes(), "privat_sample.xlsx")
    assert loaded.sheet == "Виписки"


def test_corrupt_xlsx_gives_clean_error() -> None:
    """ZIP-сигнатура є, вміст — ні. Користувач має отримати LoaderError,
    а не голий zipfile.BadZipFile із нутрощів openpyxl."""
    with pytest.raises(LoaderError):
        load_any(b"PK\x03\x04not-really-xlsx-but-zip", "archive.zip")


# --- mono -----------------------------------------------------------------


def test_mono_parses_real_structure() -> None:
    result = _load("mono_sample.csv")
    assert result.bank == "mono"
    assert result.source_format == "csv"
    assert len(result.transactions) == 5

    first = result.transactions[0]
    assert first.booked_at == datetime(2026, 8, 1, 10, 37, 16)
    assert first.amount_account_minor == -11398
    assert first.mcc == 5411
    assert first.balance_after_minor == 5408843


def test_mono_has_no_card_hint() -> None:
    """monobank віддає окремий файл на картку — hint завжди None."""
    result = _load("mono_sample.csv")
    assert result.account_hints == set()


def test_mono_handles_foreign_currency() -> None:
    result = _load("mono_sample.csv")
    foreign = [t for t in result.transactions if t.currency != "UAH"]
    assert len(foreign) == 1
    assert foreign[0].currency == "PLN"
    assert foreign[0].amount_minor != foreign[0].amount_account_minor


def test_mono_skips_broken_row() -> None:
    result = _load("mono_sample.csv")
    assert result.rows_skipped == 1
    assert result.warnings


# --- privat ---------------------------------------------------------------


def test_privat_parses_xlsx() -> None:
    result = _load("privat_sample.xlsx")
    assert result.bank == "privat"
    assert result.source_format == "xlsx"
    assert len(result.transactions) == 4
    assert all(t.mcc is None for t in result.transactions)


def test_privat_splits_by_card() -> None:
    """Один файл містить дві картки — вони мають бути розрізнені."""
    result = _load("privat_sample.xlsx")
    assert result.account_hints == {"4441", "9012"}


def test_privat_orphan_row_attributed_by_balance_chain() -> None:
    """Списання відсотків без номера картки відноситься за ланцюгом залишків."""
    result = _load("privat_sample.xlsx")
    assert all(t.account_hint is not None for t in result.transactions)

    orphan = next(t for t in result.transactions if "відсотк" in t.description_raw)
    assert orphan.account_hint == "4441"
    assert any("за ланцюгом залишків" in w for w in result.warnings)


def test_privat_row_without_time_defaults_to_midnight() -> None:
    result = _load("privat_sample.xlsx")
    assert any(t.booked_at.hour == 0 for t in result.transactions)


# --- card ref -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("51****4441", "4441"), ("5168 **** **** 9012", "9012"), ("", None), ("невідомо", None)],
)
def test_normalize_card_ref(raw: str, expected: str | None) -> None:
    assert normalize_card_ref(raw) == expected


# --- detection ------------------------------------------------------------


def test_parsers_do_not_claim_each_others_files() -> None:
    mono_file = load_any((FIXTURES / "mono_sample.csv").read_bytes(), "mono_sample.csv")
    privat_file = load_any((FIXTURES / "privat_sample.xlsx").read_bytes(), "privat_sample.xlsx")
    assert detect_parser(mono_file).bank == "mono"
    assert detect_parser(privat_file).bank == "privat"


def test_unknown_format_raises() -> None:
    loaded = load_any(b"foo,bar,baz\n1,2,3", "x.csv")
    with pytest.raises(UnknownFormatError):
        detect_parser(loaded)


# --- цілісність ланцюга залишків ------------------------------------------


def test_balance_chain_is_consistent() -> None:
    """Самоперевірка парсера: залишок[i] - сума[i] == залишок[i-1].

    Розрив у ланцюгу означає, що сума розібрана неправильно. Це та сама
    перевірка, яку робить ``scripts/reconcile.py`` на реальних виписках,
    тому вона має бути покрита тестом.
    """
    result = _load("mono_sample.csv")
    ordered = sorted(result.transactions, key=lambda t: (t.booked_at, -t.source_row))
    for prev, curr in pairwise(ordered):
        assert curr.balance_after_minor is not None
        assert prev.balance_after_minor is not None
        assert curr.balance_after_minor - curr.amount_account_minor == prev.balance_after_minor
