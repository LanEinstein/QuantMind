"""Z-line ledger — the institutional-rent P&L line (MZ-1, protocol §5).

Append-only JSONL at ``data/institutional_rent/z_ledger.jsonl``. Its one
job is making the rent contribution (and its decay) visible as a separate
line in the mock book. Until the MI-1 reconciliation loop lands, records
are appended by hand via the CLI when the owner reports a win/sell in
Feishu::

    python -m scripts.institutional_rent.z_ledger add \
        --type ipo_sell --code 301689.SZ --name 电科思仪 --amount 21850 \
        --note "首日收盘卖出"
    python -m scripts.institutional_rent.z_ledger summary

``amount`` semantics per type: ``ipo_win``/``cb_win`` = allotment cost
paid (informational, NOT P&L); ``ipo_sell``/``cb_sell``/``cash_yield`` =
realized net P&L in CNY. ``summary`` sums only the realized types.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_LEDGER = Path("data/institutional_rent/z_ledger.jsonl")
LEDGER_TYPES = frozenset({"ipo_win", "ipo_sell", "cb_win", "cb_sell", "cash_yield"})
REALIZED_TYPES = frozenset({"ipo_sell", "cb_sell", "cash_yield"})


@dataclass(frozen=True)
class LedgerRecord:
    recorded_at: str
    type: str
    code: str
    name: str
    amount: float
    note: str


def make_record(
    *, type: str, code: str, name: str, amount: float, note: str = ""
) -> LedgerRecord:
    if type not in LEDGER_TYPES:
        raise ValueError(
            f"unknown ledger type {type!r} — one of {sorted(LEDGER_TYPES)}"
        )
    if not code and type != "cash_yield":
        raise ValueError("code is required for win/sell records")
    return LedgerRecord(
        recorded_at=datetime.now(UTC).isoformat(),
        type=type,
        code=code,
        name=name,
        amount=float(amount),
        note=note,
    )


def append_record(path: Path, record: LedgerRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


def load_records(path: Path) -> tuple[LedgerRecord, ...]:
    if not path.exists():
        return ()
    records: list[LedgerRecord] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        raw = json.loads(line)
        if raw.get("type") not in LEDGER_TYPES:
            raise ValueError(
                f"{path}:{line_no}: unknown ledger type {raw.get('type')!r}"
            )
        records.append(
            LedgerRecord(
                recorded_at=str(raw.get("recorded_at", "")),
                type=str(raw["type"]),
                code=str(raw.get("code", "")),
                name=str(raw.get("name", "")),
                amount=float(raw.get("amount", 0.0)),
                note=str(raw.get("note", "")),
            )
        )
    return tuple(records)


def summarize(records: tuple[LedgerRecord, ...]) -> dict[str, float | int]:
    by_type = {t: 0.0 for t in sorted(LEDGER_TYPES)}
    for r in records:
        by_type[r.type] += r.amount
    realized = sum(by_type[t] for t in REALIZED_TYPES)
    return {**by_type, "records": len(records), "realized_pnl": realized}


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
