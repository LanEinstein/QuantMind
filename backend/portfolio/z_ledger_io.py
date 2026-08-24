"""Z-line ledger IO — pure record/read/summarize (MZ-1 protocol §5).

Moved from ``scripts/institutional_rent/z_ledger.py`` in MI-1 so the
read-side account aggregation (:mod:`backend.portfolio.lines`) can consume
the Z line without a backend→scripts import; the CLI re-exports from here.

``amount`` semantics per type: ``ipo_win``/``cb_win`` = allotment cost paid
(informational, NOT P&L); ``ipo_sell``/``cb_sell``/``cash_yield`` = realized
net P&L in CNY. ``summarize`` sums only the realized types.
"""

from __future__ import annotations

import json
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
