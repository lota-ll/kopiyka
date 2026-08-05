"""Тести атрибуції безкарткових операцій.

Сценарій узятий з реальної виписки Приват24: «Списання відсотків за
використання кредиту» не має номера картки, бо це рух за кредитним
договором, а не картками операція.
"""

from __future__ import annotations

from datetime import datetime

from kopiyka.domain.attribution import attribute_orphans
from kopiyka.parsers.base import ParsedTransaction


def _tx(
    row: int,
    day: int,
    amount: int,
    balance: int | None,
    card: str | None = None,
    hour: int = 12,
) -> ParsedTransaction:
    return ParsedTransaction(
        booked_at=datetime(2026, 7, day, hour, 0),
        amount_minor=amount,
        currency="UAH",
        amount_account_minor=amount,
        account_currency="UAH",
        description_raw="test",
        balance_after_minor=balance,
        source_row=row,
        account_hint=card,
    )


def test_resolves_between_two_known_rows() -> None:
    rows = [
        _tx(10, 1, -100_00, 500_00, "4441"),
        _tx(11, 2, -50_00, 450_00, None),  # орфан
        _tx(12, 3, -30_00, 420_00, "4441"),
        _tx(13, 2, -70_00, 930_00, "9012"),
    ]
    report = attribute_orphans(rows)
    assert rows[1].account_hint == "4441"
    assert report.resolved == {11: "4441"}


def test_resolves_newest_row_with_only_predecessor() -> None:
    """Реальний випадок: списання відсотків — найновіша операція рахунку."""
    rows = [
        _tx(10, 1, -100_00, 500_00, "4441"),
        _tx(11, 5, -3924_66, 500_00 - 3924_66, None),
        _tx(12, 1, -70_00, 930_00, "9012"),
    ]
    attribute_orphans(rows)
    assert rows[1].account_hint == "4441"


def test_works_with_negative_balances() -> None:
    """Кредитна картка має від'ємний залишок — арифметика та сама."""
    rows = [
        _tx(10, 1, -100_00, -68_403_24, "1884"),
        _tx(11, 2, -3924_66, -68_403_24 - 3924_66, None),
        _tx(12, 1, -70_00, 28_230_39, "1392"),
    ]
    attribute_orphans(rows)
    assert rows[1].account_hint == "1884"


def test_leaves_unresolved_when_chain_does_not_fit() -> None:
    rows = [
        _tx(10, 1, -100_00, 500_00, "4441"),
        _tx(11, 2, -50_00, 999_99, None),  # не сходиться ні з чим
    ]
    report = attribute_orphans(rows)
    assert rows[1].account_hint is None
    assert report.unresolved == [11]


def test_reports_ambiguity_instead_of_guessing() -> None:
    """Два рахунки з однаковим залишком — вгадувати не можна."""
    rows = [
        _tx(10, 1, -100_00, 500_00, "4441"),
        _tx(11, 1, -100_00, 500_00, "9012"),
        _tx(12, 2, -50_00, 450_00, None),
    ]
    report = attribute_orphans(rows)
    assert rows[2].account_hint is None
    assert report.ambiguous[12] == ["4441", "9012"]


def test_row_without_balance_cannot_be_attributed() -> None:
    rows = [
        _tx(10, 1, -100_00, 500_00, "4441"),
        _tx(11, 2, -50_00, None, None),
    ]
    report = attribute_orphans(rows)
    assert report.unresolved == [11]


def test_resolved_orphan_helps_next_one() -> None:
    """Віднесена операція стає частиною ланцюга для наступних."""
    rows = [
        _tx(10, 1, -100_00, 500_00, "4441"),
        _tx(11, 2, -50_00, 450_00, None),
        _tx(12, 3, -20_00, 430_00, None),  # спирається на попередній орфан
    ]
    report = attribute_orphans(rows)
    assert rows[1].account_hint == "4441"
    assert rows[2].account_hint == "4441"
    assert len(report.resolved) == 2
