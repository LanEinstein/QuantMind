"""Count how many round-one 520 trades ever reached the 5/20 upward cross.

The 2026-08-21 audio re-review confirmed the author frames the exit as
"当五日线上穿二十日线完全成立的时候，我们的离场点就是五日线只要一点点拐头我们就走"
— the exit trigger is introduced *after* the upward cross is established, while
`m3_520.simulate_trades` arms it immediately at entry.  This module answers one
structural question before any rule is rewritten: what share of the round-one
trades were closed without the cross ever happening, i.e. were never the trade
the author described.

It reports counts and holding lengths only.  No return, win rate, or p-value is
computed here, so the diagnostic cannot be used to pick a reading by profit.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scripts.yeren_research.m3_520 import (
    RuleFeatures,
    RuleSpec,
    SecuritySeries,
    Trade,
    compute_features,
    load_daily_panel,
    simulate_trades,
)
from scripts.yeren_research.market import load_trade_dates


@dataclass(frozen=True)
class GateCounts:
    """Structural tally for one window; every field is a count or a day length.

    The headline share is computed over closed trades only.  A position still
    open at the window edge has not finished, so counting it as "closed without
    the cross" would credit the gate with an outcome that has not happened yet.
    """

    closed: int
    closed_crossed: int
    closed_never_crossed: int
    open_at_window_end: int
    open_crossed: int
    holding_days_median: float | None
    holding_days_p10: float | None
    holding_days_p90: float | None
    holding_days_max: int | None

    def as_dict(self) -> dict[str, object]:
        share = (
            round(self.closed_never_crossed / self.closed, 6) if self.closed else None
        )
        return {
            "trades_total": self.closed + self.open_at_window_end,
            "closed": self.closed,
            "closed_crossed": self.closed_crossed,
            "closed_never_crossed": self.closed_never_crossed,
            "closed_never_crossed_share": share,
            "open_at_window_end": self.open_at_window_end,
            "open_crossed": self.open_crossed,
            "open_never_crossed": self.open_at_window_end - self.open_crossed,
            "holding_days_median": self.holding_days_median,
            "holding_days_p10": self.holding_days_p10,
            "holding_days_p90": self.holding_days_p90,
            "holding_days_max": self.holding_days_max,
        }


def held_bar_range(trade: Trade) -> tuple[int, int]:
    """Return the bars whose close was formed while the position was still held.

    A closed trade is sold at the open of ``exit_index``, so that day's close —
    and therefore that day's moving averages — belongs to a period when the
    position no longer existed.  An unfinished trade is still held on its final
    mark date, so that bar counts.
    """

    last = trade.exit_index - 1 if trade.status == "closed" else trade.exit_index
    return trade.entry_index, last


def crossed_during_trade(features: RuleFeatures, trade: Trade) -> bool:
    """Report whether 5SMA stood above 20SMA on any bar the position was held.

    Entry always happens with 5SMA below 20SMA, so a single bar above is proof
    the upward cross occurred while the position was open.
    """

    first, last = held_bar_range(trade)
    if last < first:
        return False
    window_short = features.ma_short[first : last + 1]
    window_mid = features.ma_mid[first : last + 1]
    above = np.isfinite(window_short) & np.isfinite(window_mid)
    above &= window_short > window_mid
    return bool(above.any())


def _percentile(values: list[int], q: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=float), q))


def tally(pairs: list[tuple[RuleFeatures, Trade]]) -> GateCounts:
    """Fold per-trade gate outcomes into one structural tally."""

    closed = [pair for pair in pairs if pair[1].status == "closed"]
    unfinished = [pair for pair in pairs if pair[1].status != "closed"]
    closed_crossed = sum(1 for f, t in closed if crossed_during_trade(f, t))
    holding = [trade.exit_index - trade.entry_index for _, trade in closed]
    return GateCounts(
        closed=len(closed),
        closed_crossed=closed_crossed,
        closed_never_crossed=len(closed) - closed_crossed,
        open_at_window_end=len(unfinished),
        open_crossed=sum(1 for f, t in unfinished if crossed_during_trade(f, t)),
        holding_days_median=_percentile(holding, 50),
        holding_days_p10=_percentile(holding, 10),
        holding_days_p90=_percentile(holding, 90),
        holding_days_max=max(holding) if holding else None,
    )


def diagnose_window(
    series: tuple[SecuritySeries, ...],
    *,
    spec: RuleSpec,
    start_date: int,
    end_date: int,
) -> GateCounts:
    """Replay the round-one early-turn rule and tally the cross gate only."""

    pairs: list[tuple[RuleFeatures, Trade]] = []
    for item in series:
        features = compute_features(item.closes, spec)
        for trade in simulate_trades(
            item,
            features,
            start_date=start_date,
            end_date=end_date,
            exit_kind="early_turn",
        ):
            pairs.append((features, trade))
    return tally(pairs)


def run_diagnostic(
    pit_root: Path,
    *,
    start_date: str = "20150105",
    split_date: str = "20221230",
    end_date: str | None = None,
    stop_days: int = 3,
) -> dict[str, object]:
    """Run the exact windows of the round-one report, counts only."""

    calendar = load_trade_dates(pit_root)
    study_end = end_date or calendar[-1]
    series, boundary = load_daily_panel(
        pit_root, start_date=start_date, end_date=study_end
    )
    spec = RuleSpec(stop_days=stop_days)
    split_end = min(split_date, study_end)
    oos_start = next((day for day in calendar if day > split_date), None)
    if oos_start is None or oos_start > study_end:
        raise ValueError("split_date leaves no out-of-sample trading date")
    windows = {
        "in_sample": (int(start_date), int(split_end)),
        "out_of_sample": (int(oos_start), int(study_end)),
    }
    return {
        "study": "m3-520-cross-gate-diagnostic",
        "stop_days": stop_days,
        "exit_kind": "early_turn",
        "question": (
            "share of closed round-one trades whose 5MA never stood above 20MA "
            "on any bar held"
        ),
        "load": boundary,
        "windows": {
            name: {
                "start_date": window[0],
                "end_date": window[1],
                **diagnose_window(
                    series, spec=spec, start_date=window[0], end_date=window[1]
                ).as_dict(),
            }
            for name, window in windows.items()
        },
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
    report = run_diagnostic(
        args.pit_root,
        start_date=args.start_date,
        split_date=args.split_date,
        end_date=args.end_date,
        stop_days=args.stop_days,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
