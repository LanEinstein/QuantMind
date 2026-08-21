"""Audit whether the 520 study's next-open execution is actually reachable.

Round one assumed every signal could be filled at the next available open.
Three stored facts can contradict that assumption without any simulation:
a limit-up open cannot be bought, a limit-down open cannot be sold, and a
missing daily row means the security did not trade at all that day.

The audit reports only counts and gap lengths.  It never filters trades, never
computes a return, and never invents a fill price, so it cannot be used to make
the round-one numbers look better.
"""

from __future__ import annotations

import argparse
import collections
import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.m3_520 import RuleSpec, compute_features, simulate_trades
from scripts.yeren_research.m3_520_adjustment_audit import audit_convention_and_events
from scripts.yeren_research.market import load_trade_dates
from scripts.yeren_research.pit_priced_panel import PricedSeries, load_priced_panel

VENDOR = "tushare"
# Tushare stores "no limit applies today" as an out-of-range sentinel rather
# than a null, so a plain comparison would call every such open a limit hit.
NO_UP_LIMIT = 9_999.0
NO_DOWN_LIMIT = 0.02
PRICE_TOLERANCE = 1e-6


@dataclass(frozen=True)
class ExecutionBar:
    """One bar the study would have had to trade on."""

    code: str
    trade_date: int
    side: str
    open_price: float
    calendar_gap: int


def collect_execution_bars(
    series: tuple[PricedSeries, ...],
    *,
    spec: RuleSpec,
    start_date: int,
    end_date: int,
    calendar_index: dict[int, int],
    excluded: frozenset[str],
) -> tuple[tuple[ExecutionBar, ...], dict[str, int]]:
    """Replay the round-one rule on adjusted closes and list every fill bar.

    A signal on a security's last stored bar has no next open at all.  Those
    fills are permanently impossible rather than merely delayed, and
    ``simulate_trades`` drops them, so they are counted here instead of
    disappearing from the executability picture.
    """

    bars: list[ExecutionBar] = []
    unfillable = {
        "entry_signals_without_any_next_bar": 0,
        "trades_without_exit_execution_bar": 0,
        "exit_signals_on_the_final_stored_bar": 0,
    }
    for item in series:
        if item.code in excluded:
            continue
        features = compute_features(item.adjusted_closes, spec)
        last_index = int(np.searchsorted(item.dates, end_date, side="right")) - 1
        if last_index < 0:
            continue
        in_window = features.entry_signal & (item.dates >= start_date)
        in_window &= item.dates <= end_date
        unfillable["entry_signals_without_any_next_bar"] += int(
            np.count_nonzero(np.flatnonzero(in_window) >= last_index)
        )
        for trade in simulate_trades(
            item,
            features,
            start_date=start_date,
            end_date=end_date,
            exit_kind="early_turn",
        ):
            bars.append(_bar(item, trade.entry_index, "entry", calendar_index))
            if trade.status == "closed":
                bars.append(_bar(item, trade.exit_index, "exit", calendar_index))
                continue
            unfillable["trades_without_exit_execution_bar"] += 1
            if features.early_exit_signal[last_index]:
                unfillable["exit_signals_on_the_final_stored_bar"] += 1
    return tuple(bars), unfillable


def _bar(
    item: PricedSeries, index: int, side: str, calendar_index: dict[int, int]
) -> ExecutionBar:
    """Build one fill bar and measure how far it sits from the signal bar."""

    current = int(item.dates[index])
    previous = int(item.dates[index - 1]) if index else current
    gap = calendar_index.get(current, 0) - calendar_index.get(previous, 0) - 1
    return ExecutionBar(
        code=item.code,
        trade_date=current,
        side=side,
        open_price=float(item.opens[index]),
        calendar_gap=max(0, gap),
    )


def load_constraints(
    pit_root: Path, needed: set[tuple[str, int]]
) -> dict[tuple[str, int], dict[str, object]]:
    """Read price limits and suspension marks for the wanted bars only.

    The study touches a few hundred thousand bars out of fourteen million, so
    the snapshots are streamed and filtered instead of being joined whole.
    """

    store = SnapshotStore(pit_root)
    wanted_dates = sorted({str(date) for _, date in needed})
    out: dict[tuple[str, int], dict[str, object]] = {}
    for trade_date in wanted_dates:
        limits = store.latest(
            vendor=VENDOR, endpoint="stk_limit", trade_date=trade_date
        )
        if limits is not None:
            frame = pd.read_csv(
                io.BytesIO(limits.raw_payload),
                usecols=["ts_code", "up_limit", "down_limit"],
            )
            # read_csv keeps the file's column order, not the usecols order.
            frame = frame[["ts_code", "up_limit", "down_limit"]]
            for code, up, down in frame.itertuples(index=False):
                key = (str(code), int(trade_date))
                if key in needed:
                    out.setdefault(key, {})["up_limit"] = float(up)
                    out[key]["down_limit"] = float(down)
        halts = store.latest(
            vendor=VENDOR, endpoint="suspend_d", trade_date=trade_date
        )
        if halts is None:
            continue
        frame = pd.read_csv(
            io.BytesIO(halts.raw_payload), usecols=["ts_code", "suspend_timing"]
        )
        frame = frame[["ts_code", "suspend_timing"]]
        for code, timing in frame.itertuples(index=False):
            key = (str(code), int(trade_date))
            if key in needed:
                out.setdefault(key, {})["suspend_timing"] = timing
    return out


def _blocked(bar: ExecutionBar, limits: dict[str, object]) -> str | None:
    """Name the stored fact that contradicts a fill, or None if none does."""

    up = limits.get("up_limit")
    down = limits.get("down_limit")
    if bar.side == "entry" and isinstance(up, float) and up < NO_UP_LIMIT:
        if bar.open_price >= up - PRICE_TOLERANCE:
            return "open_at_up_limit"
    if bar.side == "exit" and isinstance(down, float) and down > NO_DOWN_LIMIT:
        if bar.open_price <= down + PRICE_TOLERANCE:
            return "open_at_down_limit"
    return None


def audit_bars(
    bars: tuple[ExecutionBar, ...],
    constraints: dict[tuple[str, int], dict[str, object]],
) -> dict[str, object]:
    """Count unreachable fills, missing limit facts, halts, and delay gaps."""

    tally: dict[str, collections.Counter] = {
        "entry": collections.Counter(),
        "exit": collections.Counter(),
    }
    gaps: dict[str, list[int]] = {"entry": [], "exit": []}
    for bar in bars:
        limits = constraints.get((bar.code, bar.trade_date), {})
        counter = tally[bar.side]
        counter["bars"] += 1
        if "up_limit" not in limits:
            counter["no_limit_row"] += 1
        blocked = _blocked(bar, limits)
        if blocked:
            counter[blocked] += 1
        if isinstance(limits.get("suspend_timing"), str):
            counter["intraday_halt_on_fill_day"] += 1
        if bar.calendar_gap:
            counter["fill_delayed_by_missing_days"] += 1
        gaps[bar.side].append(bar.calendar_gap)
    return {
        side: {
            **dict(tally[side]),
            "max_calendar_gap": max(gaps[side]) if gaps[side] else None,
            "gap_p99": (
                float(np.percentile(np.asarray(gaps[side]), 99)) if gaps[side] else None
            ),
        }
        for side in ("entry", "exit")
    }


def audit_holding_order(
    series: tuple[PricedSeries, ...],
    *,
    spec: RuleSpec,
    start_date: int,
    end_date: int,
    excluded: frozenset[str],
) -> dict[str, object]:
    """Check the T+1 ordering the rule implies, without changing the rule."""

    same_day = next_day = closed = 0
    for item in series:
        if item.code in excluded:
            continue
        features = compute_features(item.adjusted_closes, spec)
        for trade in simulate_trades(
            item,
            features,
            start_date=start_date,
            end_date=end_date,
            exit_kind="early_turn",
        ):
            if trade.status != "closed":
                continue
            closed += 1
            span = trade.exit_index - trade.entry_index
            same_day += span == 0
            next_day += span == 1
    return {
        "closed_trades": closed,
        "sold_on_the_purchase_bar": same_day,
        "sold_on_the_bar_after_purchase": next_day,
    }


def run_audit(
    pit_root: Path,
    *,
    start_date: str = "20150105",
    split_date: str = "20221230",
    end_date: str | None = None,
    stop_days: int = 3,
) -> dict[str, object]:
    """Audit executability over the round-one out-of-sample window."""

    calendar = load_trade_dates(pit_root)
    study_end = end_date or calendar[-1]
    calendar_index = {int(day): position for position, day in enumerate(calendar)}
    series, coverage = load_priced_panel(
        pit_root, start_date=start_date, end_date=study_end
    )
    spec = RuleSpec(stop_days=stop_days)
    oos_start = next(day for day in calendar if day > split_date)
    window = (int(oos_start), int(study_end))
    # Same exclusion as the adjustment audit: a factor history that cannot
    # reproduce the vendor's own return would put fabricated fills in this list.
    _, misaligned = audit_convention_and_events(series)
    bars, unfillable = collect_execution_bars(
        series,
        spec=spec,
        start_date=window[0],
        end_date=window[1],
        calendar_index=calendar_index,
        excluded=misaligned,
    )
    constraints = load_constraints(
        pit_root, {(bar.code, bar.trade_date) for bar in bars}
    )
    return {
        "study": "m3-520-executability-audit",
        "price_input": "adjusted closes (close x adj_factor) per the 2026-08-21 audit",
        "stop_days": stop_days,
        "window": {"start_date": window[0], "end_date": window[1]},
        "panel": {
            "security_count_with_30_rows": coverage["security_count_with_30_rows"],
            "joined_rows": coverage["joined_rows"],
            "securities_excluded_for_misalignment": len(misaligned),
        },
        "execution_bars": audit_bars(bars, constraints),
        "signals_with_no_execution_bar_at_all": unfillable,
        "holding_order": audit_holding_order(
            series,
            spec=spec,
            start_date=window[0],
            end_date=window[1],
            excluded=misaligned,
        ),
        "real_broker_orders": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit-root", type=Path, default=Path("data/marketdata_pit"))
    parser.add_argument("--start-date", default="20150105")
    parser.add_argument("--split-date", default="20221230")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--stop-days", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(
        json.dumps(
            run_audit(
                args.pit_root,
                start_date=args.start_date,
                split_date=args.split_date,
                end_date=args.end_date,
                stop_days=args.stop_days,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
