"""Ідемпотентність імпорту — властивість, заради якої існує dedup_hash."""

from __future__ import annotations

from pathlib import Path

from kopiyka.domain.dedup import dedup_hash
from kopiyka.loaders import csv_loader, xlsx_loader  # noqa: F401
from kopiyka.loaders.base import load_any
from kopiyka.parsers import mono, privat  # noqa: F401
from kopiyka.parsers.base import detect_parser

FIXTURES = Path(__file__).parent / "fixtures"


def _hashes(name: str, default_ref: str = "default") -> set[bytes]:
    loaded = load_any((FIXTURES / name).read_bytes(), name)
    result = detect_parser(loaded)().parse(loaded)
    return {
        dedup_hash(
            account_ref=tx.account_hint or default_ref,
            booked_at=tx.booked_at,
            amount_minor=tx.amount_minor,
            currency=tx.currency,
            description=tx.description_raw,
        )
        for tx in result.transactions
    }


def test_reimport_produces_identical_hashes_csv() -> None:
    assert _hashes("mono_sample.csv") == _hashes("mono_sample.csv")


def test_reimport_produces_identical_hashes_xlsx() -> None:
    assert _hashes("privat_sample.xlsx") == _hashes("privat_sample.xlsx")


def test_no_hash_collisions_within_file() -> None:
    loaded = load_any((FIXTURES / "mono_sample.csv").read_bytes(), "mono_sample.csv")
    result = detect_parser(loaded)().parse(loaded)
    assert len(_hashes("mono_sample.csv")) == len(result.transactions)


def test_privat_cards_get_distinct_hashes() -> None:
    """Дві картки в одному файлі не змішуються в дедуплікації."""
    loaded = load_any((FIXTURES / "privat_sample.xlsx").read_bytes(), "privat_sample.xlsx")
    result = detect_parser(loaded)().parse(loaded)
    per_card = {t.account_hint for t in result.transactions if t.account_hint}
    assert len(per_card) == 2
