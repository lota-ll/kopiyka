"""Звірка виписок без БД: цілісність балансів, дублікати, внутрішні перекази.

Відповідає на три питання, які треба закрити до першого імпорту в БД:

1. **Чи правильно розібрані суми?** Перевіряється ланцюг залишків: якщо
   банк дає «Залишок після операції», то ``залишок[i] - сума[i]`` має
   дорівнювати ``залишок[i-1]``. Розрив означає помилку парсера — це
   самоперевірка, яка не потребує ручного звіряння з банком.

2. **Чи немає колізій дедуплікації?** Дві різні транзакції з однаковим
   ``dedup_hash`` злилися б в одну при імпорті.

3. **Скільки з «витрат» насправді є переказами між власними картками?**
   Без цієї відповіді аналітика витрат буде роздутою.

Запуск::

    python -m scripts.reconcile ~/statements/*.csv ~/statements/*.xlsx
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

from kopiyka.domain.dedup import dedup_hash
from kopiyka.domain.money import format_amount
from kopiyka.domain.transfers import TransferRow, match_internal_transfers
from kopiyka.loaders import csv_loader, xlsx_loader  # noqa: F401 — реєстрація
from kopiyka.loaders.base import LoaderError, load_any
from kopiyka.parsers import mono, privat  # noqa: F401 — реєстрація
from kopiyka.parsers.base import ParsedTransaction, ParserError, detect_parser


class Entry:
    """Транзакція разом з визначеним рахунком."""

    __slots__ = ("account", "bank", "source", "tx")

    def __init__(self, tx: ParsedTransaction, account: str, bank: str, source: str) -> None:
        self.tx = tx
        self.account = account
        self.bank = bank
        self.source = source


def collect(paths: list[Path]) -> list[Entry]:
    entries: list[Entry] = []
    for path in paths:
        loaded = load_any(path.read_bytes(), path.name)
        result = detect_parser(loaded)().parse(loaded)
        fallback = path.stem
        for tx in result.transactions:
            account = tx.account_hint or fallback
            entries.append(Entry(tx, account, result.bank, path.name))
        print(
            f"{path.name:24} {result.bank:7} {len(result.transactions):>4} транзакцій, "
            f"пропущено {result.rows_skipped}"
        )
    return entries


def check_balance_chain(entries: list[Entry]) -> None:
    """Перевіряє неперервність ланцюга залишків для кожного рахунку."""
    print("\n--- Цілісність ланцюга залишків ---")
    by_account: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        if entry.tx.balance_after_minor is not None:
            by_account[entry.account].append(entry)

    if not by_account:
        print("  колонка залишку відсутня — перевірку пропущено")
        return

    for account, rows in sorted(by_account.items()):
        # Файли віддані від новіших до старіших, тому при однаковому часі
        # більший source_row означає ранішу операцію.
        ordered = sorted(rows, key=lambda e: (e.tx.booked_at, -e.tx.source_row))
        breaks = 0
        first_break: str | None = None

        for prev, curr in pairwise(ordered):
            expected = prev.tx.balance_after_minor
            actual = curr.tx.balance_after_minor
            assert expected is not None and actual is not None
            if actual - curr.tx.amount_account_minor != expected:
                breaks += 1
                if first_break is None:
                    delta = actual - curr.tx.amount_account_minor - expected
                    first_break = (
                        f"рядок {curr.tx.source_row} ({curr.tx.booked_at:%d.%m.%Y %H:%M}) "
                        f"розбіжність {format_amount(delta)}"
                    )

        total = max(len(ordered) - 1, 1)
        status = "OK" if breaks == 0 else f"РОЗРИВІВ {breaks}/{total}"
        print(f"  {account:16} {len(ordered):>4} операцій   {status}")
        if first_break:
            print(f"      перший: {first_break}")


def check_dedup_collisions(entries: list[Entry]) -> None:
    print("\n--- Колізії дедуплікації ---")
    seen: dict[tuple[str, bytes], Entry] = {}
    collisions = 0
    for entry in entries:
        key = (
            entry.account,
            dedup_hash(
                account_ref=entry.account,
                booked_at=entry.tx.booked_at,
                amount_minor=entry.tx.amount_minor,
                currency=entry.tx.currency,
                description=entry.tx.description_raw,
            ),
        )
        if key in seen:
            collisions += 1
            if collisions <= 5:
                other = seen[key]
                print(
                    f"  {entry.tx.booked_at:%d.%m %H:%M} "
                    f"{format_amount(entry.tx.amount_account_minor):>10}  "
                    f"«{entry.tx.description_raw[:32]}» "
                    f"(рядки {other.tx.source_row} і {entry.tx.source_row})"
                )
        else:
            seen[key] = entry

    if collisions == 0:
        print("  колізій немає")
    else:
        print(
            f"  усього {collisions}. Це не обов'язково помилка: дві однакові покупки\n"
            f"  в одну хвилину злиються в одну (див. ADR-0003). Перевір їх очима."
        )


def analyse_transfers(entries: list[Entry], window_hours: int) -> None:
    print(f"\n--- Внутрішні перекази (вікно {window_hours} год) ---")
    rows = [
        TransferRow(
            id=i,
            account_id=e.account,
            booked_at=e.tx.booked_at,
            amount_account_minor=e.tx.amount_account_minor,
        )
        for i, e in enumerate(entries)
    ]
    pairs = match_internal_transfers(rows, window=timedelta(hours=window_hours))
    matched: set[object] = set()
    for pair in pairs:
        matched.add(pair.outgoing_id)
        matched.add(pair.incoming_id)

    transferred = sum(p.amount_minor for p in pairs)
    print(f"  знайдено пар:        {len(pairs)}")
    print(f"  обсяг переказів:     {format_amount(transferred)}")

    routes = Counter(
        (entries[int(str(p.outgoing_id))].account, entries[int(str(p.incoming_id))].account)
        for p in pairs
    )
    for (src, dst), count in routes.most_common(10):
        print(f"    {src:14} → {dst:14} {count:>3}")

    naive_spend = sum(e.tx.amount_account_minor for e in entries if e.tx.amount_account_minor < 0)
    real_spend = sum(
        e.tx.amount_account_minor
        for i, e in enumerate(entries)
        if e.tx.amount_account_minor < 0 and i not in matched
    )
    real_income = sum(
        e.tx.amount_account_minor
        for i, e in enumerate(entries)
        if e.tx.amount_account_minor > 0 and i not in matched
    )

    print("\n--- Підсумок за весь період ---")
    print(f"  «витрати» без фільтра:      {format_amount(naive_spend)}")
    print(f"  реальні витрати:            {format_amount(real_spend)}")
    print(f"  реальні надходження:        {format_amount(real_income)}")
    inflation = naive_spend - real_spend
    if naive_spend:
        share = abs(inflation) / abs(naive_spend) * 100
        print(f"  роздуття через перекази:    {format_amount(inflation)} ({share:.1f}%)")

    _monthly(entries, matched)


def _monthly(entries: list[Entry], matched: set[object]) -> None:
    print("\n--- Реальні витрати по місяцях ---")
    by_month: dict[str, int] = defaultdict(int)
    for i, entry in enumerate(entries):
        if entry.tx.amount_account_minor < 0 and i not in matched:
            by_month[f"{entry.tx.booked_at:%Y-%m}"] += entry.tx.amount_account_minor
    for month, total in sorted(by_month.items()):
        print(f"  {month}   {format_amount(total):>12}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reconcile", description="Звірка виписок")
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--window", type=int, default=24, help="вікно матчингу переказів, год")
    args = ap.parse_args(argv)

    try:
        entries = collect(args.paths)
    except (ParserError, LoaderError, OSError) as exc:
        print(f"помилка: {exc}", file=sys.stderr)
        return 1

    if not entries:
        print("немає транзакцій", file=sys.stderr)
        return 1

    accounts = Counter(e.account for e in entries)
    print(f"\nрахунків: {len(accounts)} — " + ", ".join(f"{a} ({n})" for a, n in accounts.items()))

    check_balance_chain(entries)
    check_dedup_collisions(entries)
    analyse_transfers(entries, args.window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
