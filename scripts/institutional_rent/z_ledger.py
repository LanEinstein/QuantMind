"""Z-line ledger CLI — the institutional-rent P&L line (MZ-1, protocol §5).

Append-only JSONL at ``data/institutional_rent/z_ledger.jsonl``. Its one
job is making the rent contribution (and its decay) visible as a separate
line in the mock book. Records are appended via this CLI (or the MI-1
reconciliation loop) when the owner reports a win/sell in Feishu::

    python -m scripts.institutional_rent.z_ledger add \
        --type ipo_sell --code 301689.SZ --name 电科思仪 --amount 21850 \
        --note "首日收盘卖出"
    python -m scripts.institutional_rent.z_ledger summary

The pure IO lives in :mod:`backend.portfolio.z_ledger_io` (moved there in
MI-1 for the read-side line aggregation); this module re-exports it so
existing imports keep working.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.portfolio.z_ledger_io import (
    DEFAULT_LEDGER,
    LEDGER_TYPES,
    REALIZED_TYPES,
    LedgerRecord,
    append_record,
    load_records,
    make_record,
    summarize,
)

__all__ = [
    "DEFAULT_LEDGER",
    "LEDGER_TYPES",
    "REALIZED_TYPES",
    "LedgerRecord",
    "append_record",
    "load_records",
    "make_record",
    "summarize",
    "main",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="append one record")
    add.add_argument("--type", required=True, choices=sorted(LEDGER_TYPES))
    add.add_argument("--code", default="")
    add.add_argument("--name", default="")
    add.add_argument("--amount", type=float, required=True)
    add.add_argument("--note", default="")
    sub.add_parser("summary", help="print per-type totals and realized P&L")
    args = parser.parse_args(argv)

    path = Path(args.ledger)
    if args.command == "add":
        record = make_record(
            type=args.type,
            code=args.code,
            name=args.name,
            amount=args.amount,
            note=args.note,
        )
        append_record(path, record)
        print(f"appended {record.type} {record.code} amount={record.amount:.2f}")
        return 0
    summary = summarize(load_records(path))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
