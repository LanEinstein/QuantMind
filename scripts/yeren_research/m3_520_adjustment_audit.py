"""Audit whether raw daily prices are a usable input for the 520 study.

Round one computed the 5/20/30 averages on raw closes.  A split or dividend
puts a mechanical gap into a raw series, and the 520 rule reads nothing but
moving-average slopes and orderings, so such a gap can fabricate the very
"5-day average turns up" or "turns down" events the rule trades on.

The audit answers input-quality questions only: which adjustment convention
matches the vendor's own ``pct_chg``, how many signals sit on a window holding
a corporate action, and how far the signal set itself moves between raw and
adjusted prices.  It never computes a return, win rate, or p-value, so no price
convention can be picked here by profit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from scripts.yeren_research.m3_520 import RuleSpec, compute_features, simulate_trades
from scripts.yeren_research.market import load_trade_dates
from scripts.yeren_research.pit_priced_panel import (
    PricedSeries,
    factor_change_mask,
    load_priced_panel,
)

# The multiply convention's residual against pct_chg is a four-decimal rounding
# artefact of order 1e-5.  A whole percentage point of daily return is three
# orders of magnitude above that, so it cannot be rounding: it means the stored
# factor history does not describe this security's actual price adjustments.
ALIGNMENT_TOLERANCE = 0.01


def _daily_ratio(values: np.ndarray) -> np.ndarray:
    """Bar-over-bar ratio with a leading NaN so index alignment is preserved."""

    ratio = np.full(len(values), np.nan, dtype=float)
    if len(values) > 1:
        previous = values[:-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio[1:] = np.where(previous > 0, values[1:] / previous, np.nan)
    return ratio


def convention_errors(series: PricedSeries) -> dict[str, np.ndarray]:
    """Compare candidate adjustment conventions against the vendor's pct_chg.

    ``pct_chg`` is computed by Tushare against the ex-rights previous close, so
    on a corporate-action bar it already states the true holder return.  Only
    the convention that reproduces it is the one the stored factor encodes.
    """

    truth = 1.0 + series.pct_chg / 100.0
    lagged_adj = np.concatenate(([np.nan], series.adj[:-1]))
    return {
        "raw": _daily_ratio(series.closes) - truth,
        "multiply": _daily_ratio(series.closes * series.adj) - truth,
        "divide": _daily_ratio(series.closes / series.adj) - truth,
        "multiply_lagged_factor": _daily_ratio(series.closes * lagged_adj) - truth,
    }


def _percentile(values: np.ndarray, q: float) -> float | None:
    usable = values[np.isfinite(values)]
    return float(np.percentile(usable, q)) if len(usable) else None


def audit_convention_and_events(
    series: tuple[PricedSeries, ...],
) -> tuple[dict[str, object], frozenset[str]]:
    """Fold the convention test and the corporate-action census into one pass.

    Also names the securities whose stored factor history cannot reproduce the
    vendor's own return on a corporate-action bar.  Those histories would
    fabricate adjusted signals, so they are reported and excluded downstream
    rather than averaged away by a panel-wide median.
    """

    names = ("raw", "multiply", "divide", "multiply_lagged_factor")
    abs_error: dict[str, list[np.ndarray]] = {name: [] for name in names}
    jumps: list[np.ndarray] = []
    misaligned: set[str] = set()
    misaligned_bars = 0
    change_bars = 0
    total_bars = 0
    securities_with_change = 0
    for item in series:
        changed = factor_change_mask(item)
        total_bars += len(item.dates)
        change_bars += int(changed.sum())
        if not changed.any():
            continue
        securities_with_change += 1
        errors = convention_errors(item)
        for name in names:
            abs_error[name].append(np.abs(errors[name][changed]))
        multiply_error = np.abs(errors["multiply"][changed])
        bad = int(np.count_nonzero(multiply_error > ALIGNMENT_TOLERANCE))
        if bad:
            misaligned.add(item.code)
            misaligned_bars += bad
        ratio = _daily_ratio(item.adj)
        jumps.append(ratio[changed])
    multiply_errors = np.concatenate(abs_error["multiply"]) if jumps else np.empty(0)
    report = {
        "corporate_action_bars": change_bars,
        "total_bars": total_bars,
        "securities_with_any_change": securities_with_change,
        "convention_median_abs_error_vs_pct_chg": {
            name: _percentile(np.concatenate(values), 50) if values else None
            for name, values in abs_error.items()
        },
        "multiply_abs_error_percentiles": {
            str(q): _percentile(multiply_errors, q) for q in (50, 90, 99, 99.9)
        },
        "alignment_tolerance": ALIGNMENT_TOLERANCE,
        "misaligned_bars": misaligned_bars,
        "misaligned_securities": len(misaligned),
        "misaligned_security_sample": sorted(misaligned)[:10],
        "factor_jump_ratio_percentiles": {
            str(q): _percentile(np.concatenate(jumps), q) if jumps else None
            for q in (1, 25, 50, 75, 99)
        },
    }
    return report, frozenset(misaligned)


def audit_contamination(
    series: tuple[PricedSeries, ...],
    *,
    spec: RuleSpec,
    start_date: int,
    end_date: int,
    excluded: frozenset[str],
) -> dict[str, object]:
    """Count round-one signals whose input window holds a corporate action."""

    signals = contaminated_signals = 0
    trades = contaminated_trades = closed_trades = exit_near_change = 0
    for item in series:
        if item.code in excluded:
            continue
        changed = factor_change_mask(item)
        features = compute_features(item.closes, spec)
        in_window = features.entry_signal & (item.dates >= start_date)
        in_window &= item.dates <= end_date
        # The signal at bar i depends on closes back to i - long_window: the
        # 30-day slope compares ma_long[i] (closes i-29..i) against ma_long[i-1]
        # (closes i-30..i-1), so the window is long_window + 1 bars, not 30.
        lookback = spec.long_window
        for index in np.flatnonzero(in_window):
            signals += 1
            first = max(0, int(index) - lookback)
            contaminated_signals += bool(changed[first : int(index) + 1].any())
        for trade in simulate_trades(
            item,
            features,
            start_date=start_date,
            end_date=end_date,
            exit_kind="early_turn",
        ):
            trades += 1
            # The position is bought at the entry bar's open, i.e. already
            # ex-rights, so an action on that bar is not something it lived
            # through; an action on the exit bar is, because the holder of
            # record on that ex-date is still this position.
            held = changed[trade.entry_index + 1 : trade.exit_index + 1]
            contaminated_trades += bool(held.any())
            if trade.status != "closed" or trade.exit_signal_date is None:
                continue
            closed_trades += 1
            exit_near_change += _exit_touches_change(changed, trade.exit_index - 1)
    return {
        "securities_excluded_for_misalignment": len(excluded),
        "entry_signals": signals,
        "entry_signals_with_action_in_ma_window": contaminated_signals,
        "trades": trades,
        "trades_with_action_while_held": contaminated_trades,
        "closed_trades": closed_trades,
        "closed_trades_with_action_within_one_bar_of_exit_signal": exit_near_change,
    }


def _exit_touches_change(changed: np.ndarray, signal_index: int) -> bool:
    """Report a corporate action on the exit signal bar or either neighbour."""

    first = max(0, signal_index - 1)
    return bool(changed[first : signal_index + 2].any())


def _entry_dates(
    item: PricedSeries,
    closes: np.ndarray,
    *,
    spec: RuleSpec,
    start_date: int,
    end_date: int,
) -> np.ndarray:
    features = compute_features(closes, spec)
    selected = features.entry_signal & (item.dates >= start_date)
    selected &= item.dates <= end_date
    return item.dates[selected]


def audit_signal_difference(
    series: tuple[PricedSeries, ...],
    *,
    spec: RuleSpec,
    start_date: int,
    end_date: int,
    excluded: frozenset[str],
) -> dict[str, object]:
    """Count how the entry set moves when the same rule reads adjusted closes.

    This is the number that decides whether round one has to be rebuilt: a
    window merely containing a corporate action is harmless if the signal it
    produced is the same one either way.
    """

    shared = raw_only = adjusted_only = 0
    for item in series:
        if item.code in excluded:
            continue
        raw = _entry_dates(
            item, item.closes, spec=spec, start_date=start_date, end_date=end_date
        )
        adjusted = _entry_dates(
            item,
            item.adjusted_closes,
            spec=spec,
            start_date=start_date,
            end_date=end_date,
        )
        common = np.intersect1d(raw, adjusted, assume_unique=True)
        shared += len(common)
        raw_only += len(raw) - len(common)
        adjusted_only += len(adjusted) - len(common)
    return {
        "securities_excluded_for_misalignment": len(excluded),
        "entries_on_both_price_forms": shared,
        "entries_only_on_raw": raw_only,
        "entries_only_on_adjusted": adjusted_only,
    }


def run_audit(
    pit_root: Path,
    *,
    start_date: str = "20150105",
    split_date: str = "20221230",
    end_date: str | None = None,
    stop_days: int = 3,
) -> dict[str, object]:
    """Run every input-quality check over the round-one out-of-sample window."""

    calendar = load_trade_dates(pit_root)
    study_end = end_date or calendar[-1]
    series, coverage = load_priced_panel(
        pit_root, start_date=start_date, end_date=study_end
    )
    spec = RuleSpec(stop_days=stop_days)
    oos_start = next(day for day in calendar if day > split_date)
    window = (int(oos_start), int(study_end))
    convention, misaligned = audit_convention_and_events(series)
    return {
        "study": "m3-520-adjustment-audit",
        "stop_days": stop_days,
        "window": {"start_date": window[0], "end_date": window[1]},
        "coverage": coverage,
        "convention_and_events": convention,
        "contamination": audit_contamination(
            series,
            spec=spec,
            start_date=window[0],
            end_date=window[1],
            excluded=misaligned,
        ),
        "signal_difference": audit_signal_difference(
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
