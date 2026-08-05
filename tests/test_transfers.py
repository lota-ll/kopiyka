"""Тести матчингу внутрішніх переказів.

За чотирьох карток кожен переказ між власними рахунками потрапляє у
виписки двічі. Без цих тестів «загальна картина витрат» тихо роздувалася б.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from kopiyka.domain.transfers import TransferRow, match_internal_transfers


def _row(rid: str, account: str, hours: int, amount: int) -> TransferRow:
    return TransferRow(
        id=rid,
        account_id=account,
        booked_at=datetime(2026, 8, 1, 12, 0) + timedelta(hours=hours),
        amount_account_minor=amount,
    )


def test_matches_simple_transfer() -> None:
    rows = [_row("a", "mono", 0, -200000), _row("b", "privat", 1, 200000)]
    pairs = match_internal_transfers(rows)
    assert len(pairs) == 1
    assert {pairs[0].outgoing_id, pairs[0].incoming_id} == {"a", "b"}


def test_ignores_same_account() -> None:
    """Витрата й надходження на одному рахунку — не переказ."""
    rows = [_row("a", "mono", 0, -200000), _row("b", "mono", 1, 200000)]
    assert match_internal_transfers(rows) == []


def test_ignores_outside_window() -> None:
    rows = [_row("a", "mono", 0, -200000), _row("b", "privat", 48, 200000)]
    assert match_internal_transfers(rows) == []


def test_ignores_different_amounts() -> None:
    rows = [_row("a", "mono", 0, -200000), _row("b", "privat", 1, 199000)]
    assert match_internal_transfers(rows) == []


def test_each_transaction_matched_once() -> None:
    """Одне надходження не може «погасити» дві витрати."""
    rows = [
        _row("out1", "mono", 0, -200000),
        _row("out2", "mono", 1, -200000),
        _row("in1", "privat", 2, 200000),
    ]
    pairs = match_internal_transfers(rows)
    assert len(pairs) == 1
    used = {pairs[0].outgoing_id, pairs[0].incoming_id}
    assert "in1" in used


def test_picks_closest_in_time() -> None:
    rows = [
        _row("out", "mono", 0, -200000),
        _row("far", "privat", 10, 200000),
        _row("near", "privat", 1, 200000),
    ]
    pairs = match_internal_transfers(rows)
    assert pairs[0].incoming_id == "near"


def test_four_cards_realistic_scenario() -> None:
    """Дві картки моно + дві привату, два перекази і звичайні витрати."""
    rows = [
        _row("mono_white_out", "mono_white", 0, -500000),
        _row("privat_4441_in", "privat_4441", 1, 500000),
        _row("privat_9012_out", "privat_9012", 5, -100000),
        _row("mono_black_in", "mono_black", 6, 100000),
        _row("grocery", "mono_white", 3, -31240),
        _row("salary", "mono_black", 8, 4500000),
    ]
    pairs = match_internal_transfers(rows)
    assert len(pairs) == 2

    matched = {p.outgoing_id for p in pairs} | {p.incoming_id for p in pairs}
    assert "grocery" not in matched
    assert "salary" not in matched

    # Реальні витрати після відкидання переказів
    real_spend = sum(
        r.amount_account_minor for r in rows if r.amount_account_minor < 0 and r.id not in matched
    )
    assert real_spend == -31240
