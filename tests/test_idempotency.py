"""Ідемпотентність імпорту — властивість, заради якої існує dedup_hash.

Тест рівня домену працює без БД: перевіряє, що повторний розбір того
самого файлу дає той самий набір хешів. Перевірка на рівні БД
(``ON CONFLICT DO NOTHING``) живе в test_tenant_isolation з міткою db.
"""

from __future__ import annotations

from pathlib import Path

from kopiyka.domain.dedup import dedup_hash
from kopiyka.parsers import mono, privat  # noqa: F401
from kopiyka.parsers.base import detect_parser
from kopiyka.parsers.encoding import decode_statement

FIXTURES = Path(__file__).parent / "fixtures"


def _hashes(name: str, account_ref: str = "4441") -> set[bytes]:
    text, encoding = decode_statement((FIXTURES / name).read_bytes())
    result = detect_parser(text)().parse(text, encoding=encoding)
    return {
        dedup_hash(
            account_ref=account_ref,
            booked_at=tx.booked_at,
            amount_minor=tx.amount_minor,
            currency=tx.currency,
            description=tx.description_raw,
        )
        for tx in result.transactions
    }


def test_reimport_produces_identical_hashes() -> None:
    assert _hashes("mono_sample.csv") == _hashes("mono_sample.csv")


def test_no_hash_collisions_within_file() -> None:
    text, encoding = decode_statement((FIXTURES / "mono_sample.csv").read_bytes())
    result = detect_parser(text)().parse(text, encoding=encoding)
    assert len(_hashes("mono_sample.csv")) == len(result.transactions)


def test_same_transaction_on_different_accounts_differs() -> None:
    assert _hashes("mono_sample.csv", "4441") != _hashes("mono_sample.csv", "9012")
