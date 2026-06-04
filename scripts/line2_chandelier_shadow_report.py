#!/usr/bin/env python
"""Aggregate the chandelier shadow logs into a daily counterfactual table.

P0-7-amendment-2026-06-04-entry-anchored-chandelier §1.4: during the 10-15
trading-day shadow the runner logs one ``chandelier_shadow_compare`` event
per held code per day (old window stop vs new entry-anchored stop, raw
would-fire booleans at the day's first fresh price). This script scans
``logs/quantmind.jsonl*`` and prints the per-day comparison so the owner
reviews the counterfactual in one glance before activating.

NOTE: ``new_brch`` is the RAW breach at the day's first fresh tick — the
live feature additionally gates a shallow breach behind the 14:30-14:55
confirmation window, so only ``new_deep`` rows are guaranteed live fires;
a shallow ``new_brch`` would have routed only if still breached late
session (faithfulness disclosure, review P1).

Usage:
    python scripts/line2_chandelier_shadow_report.py [--logs-dir logs]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _iter_events(logs_dir: Path):
    for path in sorted(logs_dir.glob("quantmind.jsonl*")):
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if "chandelier_shadow_compare" not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("event") == "chandelier_shadow_compare":
                        yield row
        except OSError as exc:
            print(f"!! cannot read {path}: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", default="logs", type=Path)
    args = parser.parse_args()

    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in _iter_events(args.logs_dir):
        day = str(row.get("timestamp", ""))[:10]
        by_day[day].append(row)

    if not by_day:
        print("no chandelier_shadow_compare events found "
              "(is QUANTMIND_LINE2_ENTRY_ANCHORED_STOP_SHADOW=1 ?)")
        return

    divergent_days = 0
    for day in sorted(by_day):
        rows = sorted(by_day[day], key=lambda r: str(r.get("code")))
        print(f"\n=== {day} ===")
        print(f"{'code':<8} {'price':>9} {'old_stop':>9} {'new_stop':>9} "
              f"{'anchor':>9} {'govern':<10} {'old_fire':>8} "
              f"{'new_brch':>8} {'new_deep':>8}")
        for r in rows:
            old_f = bool(r.get("would_fire_old"))
            new_f = bool(r.get("would_breach_new"))
            deep_f = bool(r.get("deep_breach_new"))
            mark = "  <— diverges" if old_f != new_f else ""
            if old_f != new_f:
                divergent_days += 1
            print(
                f"{r.get('code', '?'):<8} "
                f"{r.get('price', float('nan')):>9} "
                f"{r.get('old_stop') if r.get('old_stop') is not None else '—':>9} "
                f"{r.get('new_stop') if r.get('new_stop') is not None else '—':>9} "
                f"{r.get('anchor') if r.get('anchor') is not None else '—':>9} "
                f"{r.get('governing') or '—':<10} "
                f"{str(old_f):>8} {str(new_f):>8} {str(deep_f):>8}{mark}"
            )
    print(f"\ndays observed: {len(by_day)}; divergent code-days: {divergent_days}")


if __name__ == "__main__":
    main()
