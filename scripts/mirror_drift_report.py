#!/usr/bin/env python
"""MI-1 monthly execution-drift disclosure — mirror vs research, one line.

Plan §5⑤: the research side assumes the advisory's as-of CLOSE as the
execution price; the mirror books the owner's ACTUAL fills. Per month this
prints ONE line — how much worse (positive) or better (negative) the
owner's real execution was than the research assumption, in CNY and in
volume-weighted percent. A fill whose code has no delivered advisory
reference (Z-line-ish trades, discretionary names) is counted as
"uncovered" and excluded from the drift numbers, disclosed as a count.

Reference source: ``sleeve_advisory_history.jsonl`` (appended by the push
script at every DELIVERED advisory — the daily status JSON is overwritten,
so only the history carries the book as the owner saw it).

Usage::

    python scripts/mirror_drift_report.py            # all months, one line each
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.portfolio.mirror_ledger import DEFAULT_LEDGER as DEFAULT_MIRROR
from scripts.push_sleeve_advisory import DEFAULT_ADVISORY_HISTORY


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return sorted(rows, key=lambda r: str(r.get("asof", "")))


def reference_close(
    history: list[dict[str, Any]], *, code: str, fill_date: str
) -> float | None:
    """The advisory close for ``code`` from the latest delivery ≤ fill date."""
    for row in reversed(history):
        if str(row.get("asof", "")) > fill_date:
            continue
        for h in row.get("holdings", ()):
            if str(h.get("ts_code", "")).startswith(code):
                close = h.get("close")
                return float(close) if close is not None else None
    return None


def monthly_drift(
    mirror_path: Path, history_path: Path
) -> list[dict[str, Any]]:
    """One record per month: comparable fills, drift CNY, weighted pct."""
    history = load_history(history_path)
    fills = [
        json.loads(line)
        for line in (
            mirror_path.read_text(encoding="utf-8").splitlines()
            if mirror_path.exists()
            else []
        )
        if line.strip() and '"fill"' in line
    ]
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0, "uncovered": 0, "drift_yuan": 0.0, "gross_ref": 0.0}
    )
    for row in fills:
        if row.get("kind") != "fill":
            continue
        fill_date = str(row["executed_at"])[:10].replace("-", "")
        month = fill_date[:6]
        bucket = buckets[month]
        ref = reference_close(history, code=str(row["code"]), fill_date=fill_date)
        if ref is None or ref <= 0:
            bucket["uncovered"] += 1
            continue
        volume = int(row["volume"])
        price = float(row["price"])
        sign = 1.0 if row["side"] == "BUY" else -1.0
        # positive = the owner's real execution cost MORE than the research
        # assumption (bought above / sold below the advisory close).
        bucket["n"] += 1
        bucket["drift_yuan"] += sign * (price - ref) * volume
        bucket["gross_ref"] += ref * volume
    report = []
    for month in sorted(buckets):
        b = buckets[month]
        pct = (b["drift_yuan"] / b["gross_ref"] * 100) if b["gross_ref"] else 0.0
        report.append(
            {
                "month": month,
                "comparable_fills": int(b["n"]),
                "uncovered_fills": int(b["uncovered"]),
                "drift_yuan": round(b["drift_yuan"], 2),
                "drift_pct": round(pct, 4),
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", default=str(DEFAULT_MIRROR))
    parser.add_argument("--history", default=DEFAULT_ADVISORY_HISTORY)
    args = parser.parse_args()
    report = monthly_drift(Path(args.mirror), Path(args.history))
    if not report:
        print("no mirrored fills yet — nothing to disclose")
        return 0
    for r in report:
        print(
            f"{r['month']}: 可比 {r['comparable_fills']} 笔"
            f"(未覆盖 {r['uncovered_fills']} 笔), "
            f"镜像相对研究侧执行偏差 {r['drift_yuan']:+,.2f} 元"
            f" ({r['drift_pct']:+.4f}%, 正=实际成交更差)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
