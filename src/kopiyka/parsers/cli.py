"""CLI: ``kopiyka-parse statement.csv``.

Навмисно окремий від API вхід: парсер має працювати без БД, без Docker і
без мережі. Якщо фундамент кривий — усе решта не має сенсу.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from kopiyka.domain.dedup import dedup_hash
from kopiyka.domain.money import format_amount
from kopiyka.loaders import csv_loader, xlsx_loader  # noqa: F401 — реєстрація лоадерів
from kopiyka.loaders.base import LoaderError, load_any
from kopiyka.parsers import mono, privat  # noqa: F401 — реєстрація адаптерів
from kopiyka.parsers.base import ParserError, ParseResult, detect_parser


def parse_file(path: Path) -> ParseResult:
    loaded = load_any(path.read_bytes(), path.name)
    return detect_parser(loaded)().parse(loaded)


def to_json(result: ParseResult, *, account_ref: str) -> str:
    payload = {
        "bank": result.bank,
        "parser_version": result.parser_version,
        "source_format": result.source_format,
        "encoding": result.encoding,
        "account_currency": result.account_currency,
        "account_hints": sorted(result.account_hints),
        "rows_total": result.rows_total,
        "rows_skipped": result.rows_skipped,
        "warnings": result.warnings,
        "transactions": [
            {
                **asdict(tx),
                "booked_at": tx.booked_at.isoformat(),
                "dedup_hash": dedup_hash(
                    account_ref=tx.account_hint or account_ref,
                    booked_at=tx.booked_at,
                    amount_minor=tx.amount_minor,
                    currency=tx.currency,
                    description=tx.description_raw,
                ).hex(),
            }
            for tx in result.transactions
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def print_stats(result: ParseResult) -> None:
    spent = sum(t.amount_account_minor for t in result.transactions if t.amount_account_minor < 0)
    received = sum(
        t.amount_account_minor for t in result.transactions if t.amount_account_minor > 0
    )
    print(f"банк:          {result.bank} ({result.parser_version})")
    print(f"формат:        {result.source_format} / {result.encoding}")
    print(f"транзакцій:    {len(result.transactions)}")
    print(f"пропущено:     {result.rows_skipped}")
    print(f"витрати:       {format_amount(spent)}")
    print(f"надходження:   {format_amount(received)}")
    print(f"сальдо:        {format_amount(spent + received)}")

    if result.transactions:
        dates = sorted(t.booked_at for t in result.transactions)
        print(f"період:        {dates[0].date()} — {dates[-1].date()}")

    if result.account_hints:
        counts = Counter(t.account_hint for t in result.transactions if t.account_hint)
        cards = ", ".join(f"{card} ({n})" for card, n in sorted(counts.items()))
        print(f"картки:        {cards}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kopiyka-parse", description="Розбір банківської виписки")
    ap.add_argument("path", type=Path, help="шлях до виписки (.csv або .xlsx)")
    ap.add_argument("--account-ref", default="cli", help="рахунок за замовчуванням для dedup_hash")
    ap.add_argument("--json", action="store_true", help="вивести повний JSON замість зведення")
    args = ap.parse_args(argv)

    try:
        result = parse_file(args.path)
    except (ParserError, LoaderError, ValueError, OSError) as exc:
        print(f"помилка: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(to_json(result, account_ref=args.account_ref))
    else:
        print_stats(result)

    for warning in result.warnings[:20]:
        print(f"  ⚠ {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
