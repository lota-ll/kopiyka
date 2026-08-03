"""CLI: ``kopiyka-parse statement.csv > normalized.json``.

Це навмисно окремий від API вхід. Тиждень 1 закривається саме тут:
парсер має працювати без БД, без Docker і без мережі. Якщо фундамент
кривий — усе решта не має сенсу.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from kopiyka.domain.dedup import dedup_hash
from kopiyka.parsers import mono, privat  # noqa: F401 — реєстрація адаптерів
from kopiyka.parsers.base import ParserError, ParseResult, detect_parser
from kopiyka.parsers.encoding import decode_statement


def parse_file(path: Path, *, account_ref: str = "cli") -> ParseResult:
    data = path.read_bytes()
    text, encoding = decode_statement(data)
    parser_cls = detect_parser(text)
    return parser_cls().parse(text, encoding=encoding)


def to_json(result: ParseResult, *, account_ref: str) -> str:
    payload = {
        "bank": result.bank,
        "parser_version": result.parser_version,
        "encoding": result.encoding,
        "account_currency": result.account_currency,
        "rows_total": result.rows_total,
        "rows_skipped": result.rows_skipped,
        "warnings": result.warnings,
        "transactions": [
            {
                **asdict(tx),
                "booked_at": tx.booked_at.isoformat(),
                "dedup_hash": dedup_hash(
                    account_ref=account_ref,
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kopiyka-parse", description="Розбір банківської виписки")
    ap.add_argument("path", type=Path, help="шлях до CSV-виписки")
    ap.add_argument("--account-ref", default="cli", help="ідентифікатор рахунку для dedup_hash")
    ap.add_argument("--stats", action="store_true", help="показати лише зведення")
    args = ap.parse_args(argv)

    try:
        result = parse_file(args.path, account_ref=args.account_ref)
    except (ParserError, ValueError, OSError) as exc:
        print(f"помилка: {exc}", file=sys.stderr)
        return 1

    if args.stats:
        total = sum(t.amount_account_minor for t in result.transactions)
        print(f"банк:         {result.bank} ({result.parser_version})")
        print(f"кодування:    {result.encoding}")
        print(f"транзакцій:   {len(result.transactions)}")
        print(f"пропущено:    {result.rows_skipped}")
        print(f"сальдо, коп.: {total}")
    else:
        print(to_json(result, account_ref=args.account_ref))

    for warning in result.warnings[:20]:
        print(f"  ⚠ {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
