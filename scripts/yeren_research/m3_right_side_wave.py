"""One-shot walk-forward for the right-side swing loop (card 1 + enhancements).

Every convention here is frozen in
`docs/research/yeren-system/m3-right-side-wave-preregistration-2026-08-23.md`,
which in turn cites the enhancement contract of the same date.  The author's
own semantics are the entry structure (5MA above 20MA, 20MA and 30MA both
rising); the universe, the "bottom", the "big bullish candle", the pullback
window and -- crucially -- the "trend failure" exit are enhancement-layer
definitions registered as owner-delegated or researcher-added.  Nothing here
may be changed to make the result look better; a wrong convention gets a dated
correction note in the preregistration, not a silent edit.

The rule itself lives in `m3_right_side_wave_rule`; this module owns the
universe, the windows, the disclosure statistics and the judgement.

Research-only.  No playbook, simulator order, broker instruction, or execution
service is created.  `real_broker_orders=False` always.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np

from scripts.yeren_research.m3_520 import (
    SecuritySeries,
    Trade,
    matched_horizon_placebo,
)
from scripts.yeren_research.m3_520_candidate_e import (
    CostModel,
    build_st_mask,
    load_st_timeline,
)
from scripts.yeren_research.m3_right_side_wave_rule import (
    WaveSpec,
    WaveTrade,
    compute_wave_features,
    simulate_wave_trades,
)
from scripts.yeren_research.market import load_trade_dates
from scripts.yeren_research.pit_limit_panel import align_limits, load_limit_panel
from scripts.yeren_research.pit_priced_panel import PricedSeries, load_priced_panel

PREREGISTRATION = (
    "docs/research/yeren-system/m3-right-side-wave-preregistration-2026-08-23.md"
)
ENHANCEMENT_CONTRACT = (
    "docs/research/yeren-system/"
    "m3-right-side-wave-enhancement-contract-2026-08-23.md"
)
MISALIGNED_ARTIFACT = Path(
    "data/yeren_research/inventory/m3-520-adjustment-audit-2026-08-21.json"
)
OUTPUT_PATH = Path(
    "data/yeren_research/inventory/m3-right-side-wave-2026-08-23.json"
)

PREREGISTERED_START_DATE = "20150105"
PREREGISTERED_SPLIT_DATE = "20221230"
PREREGISTERED_END_DATE = "20260819"
PREREGISTERED_PLACEBO_REPS = 200
PREREGISTERED_SEED = 1120260823
PREREGISTERED_COMMISSION_RATE = 0.00015
PREREGISTERED_MIN_COMMISSION = 5.0


def load_misaligned_codes(artifact: Path = MISALIGNED_ARTIFACT) -> frozenset[str]:
    """The 78 securities whose stored factors contradict the vendor's pct_chg."""

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    return frozenset(payload["convention_and_events"]["misaligned_security_codes"])


def build_universe(
    series: tuple[PricedSeries, ...], misaligned: frozenset[str]
) -> tuple[tuple[PricedSeries, ...], dict[str, object]]:
    """Apply the two static exclusions.  ST is per signal date, not here."""

    excluded_bj = frozenset(item.code for item in series if item.code.endswith(".BJ"))
    working = tuple(
        item
        for item in series
        if item.code not in misaligned and item.code not in excluded_bj
    )
    return working, {
        "securities_loaded": len(series),
        "securities_excluded_for_misalignment": sum(
            1 for item in series if item.code in misaligned
        ),
        "securities_excluded_for_bj_exchange": len(excluded_bj - misaligned),
        "securities_in_working_universe": len(working),
    }



def _percentile(values: np.ndarray, q: float) -> float | None:
    return float(np.percentile(values, q)) if len(values) else None


def _concentration(trades: list[WaveTrade]) -> dict[str, object]:
    """Who and when the closed sample actually came from.

    Disclosure only (preregistration section 9): a mean built out of one
    security or one year is a different claim than the same mean spread over
    the whole panel, and the reader cannot tell the two apart from the mean.
    """

    if not trades:
        return {"securities_with_trades": 0}
    by_code = Counter(trade.code for trade in trades)
    by_year = Counter(trade.entry_date // 10_000 for trade in trades)
    top_code, top_code_count = by_code.most_common(1)[0]
    top_year, top_year_count = by_year.most_common(1)[0]
    net = np.asarray([t.net_return_pct for t in trades], dtype=float)
    without_top = np.asarray(
        [t.net_return_pct for t in trades if t.code != top_code], dtype=float
    )
    # Trade COUNTS alone cannot tell whether one year carries the window's
    # mean: a year holding most of the trades can still be the drag. The
    # per-year mean is what the preregistration's "single-year contribution
    # concentration" disclosure actually needs.
    per_year: dict[str, dict[str, object]] = {}
    for year in sorted(by_year):
        year_net = np.asarray(
            [t.net_return_pct for t in trades if t.entry_date // 10_000 == year],
            dtype=float,
        )
        per_year[str(year)] = {
            "trades": int(len(year_net)),
            "mean_net_return_pct": float(year_net.mean()),
            "share_of_total_net_sum_pct": (
                100.0 * float(year_net.sum()) / float(net.sum())
                if net.sum() != 0
                else None
            ),
        }
    without_top_year = np.asarray(
        [t.net_return_pct for t in trades if t.entry_date // 10_000 != top_year],
        dtype=float,
    )
    return {
        "securities_with_trades": len(by_code),
        "top_security": top_code,
        "top_security_trade_share_pct": 100.0 * top_code_count / len(trades),
        "mean_net_excluding_top_security_pct": (
            float(without_top.mean()) if len(without_top) else None
        ),
        "top_year": int(top_year),
        "top_year_trade_share_pct": 100.0 * top_year_count / len(trades),
        "mean_net_excluding_top_year_pct": (
            float(without_top_year.mean()) if len(without_top_year) else None
        ),
        "by_year": per_year,
        "mean_net_all_pct": float(net.mean()) if len(net) else None,
    }


def _closed_stats(trades: list[WaveTrade]) -> dict[str, object]:
    gross = np.asarray([t.gross_return_pct for t in trades], dtype=float)
    net = np.asarray([t.net_return_pct for t in trades], dtype=float)
    mae = np.asarray([t.mae_pct for t in trades], dtype=float)
    mae = mae[np.isfinite(mae)]
    holding = np.asarray([t.holding_bars for t in trades], dtype=float)
    return {
        "trades": len(trades),
        "mean_gross_return_pct": float(gross.mean()) if len(gross) else None,
        "mean_net_return_pct": float(net.mean()) if len(net) else None,
        "median_net_return_pct": _percentile(net, 50),
        "p10_net_return_pct": _percentile(net, 10),
        "p90_net_return_pct": _percentile(net, 90),
        "win_rate_net_pct": float((net > 0).mean() * 100.0) if len(net) else None,
        "worst_net_pct": float(net.min()) if len(net) else None,
        "best_net_pct": float(net.max()) if len(net) else None,
        "mae_median_pct": _percentile(mae, 50),
        "mae_worst_pct": float(mae.min()) if len(mae) else None,
        "holding_bars_median": _percentile(holding, 50),
        "holding_bars_p90": _percentile(holding, 90),
        "holding_bars_max": float(holding.max()) if len(holding) else None,
        # Whether the rule is even CAPABLE of the author's self-described
        # multi-month hold is a tail question, not a median question; roughly
        # three months of trading days is 60 bars.
        "holding_bars_ge_60_share_pct": (
            float((holding >= 60).mean() * 100.0) if len(holding) else None
        ),
        "mae_drawdown_definition": (
            "single-trade close-only maximum adverse excursion; "
            "not a portfolio drawdown"
        ),
    }


def _adjusted_series(item: PricedSeries) -> SecuritySeries:
    return SecuritySeries(
        code=item.code,
        dates=item.dates,
        opens=item.adjusted_opens,
        closes=item.adjusted_closes,
    )


def evaluate_window(
    universe: tuple[PricedSeries, ...],
    *,
    spec: WaveSpec,
    costs: CostModel,
    st_timeline: dict[str, tuple[np.ndarray, np.ndarray]],
    limit_panel: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]],
    placebo_series: dict[str, SecuritySeries],
    calendar_index: dict[int, int],
    start_date: int,
    end_date: int,
    placebo_reps: int,
    seed: int,
    include_trades: bool = False,
) -> dict[str, object]:
    """Replay every security once inside one window and judge the result."""

    all_trades: list[WaveTrade] = []
    totals = {
        "entry_signals_st_voided": 0,
        "entry_signals_without_any_next_bar": 0,
        "entry_void_up_limit": 0,
        "entry_void_unusable_price": 0,
        "exit_no_fill_fact": 0,
    }
    for item in universe:
        features = compute_wave_features(item, spec)
        st_mask = build_st_mask(item.dates, item.code, st_timeline)
        up_limits, down_limits = align_limits(item, limit_panel)
        trades, counts = simulate_wave_trades(
            item,
            features,
            spec=spec,
            st_mask=st_mask,
            up_limits=up_limits,
            down_limits=down_limits,
            start_date=start_date,
            end_date=end_date,
            calendar_index=calendar_index,
            costs=costs,
        )
        all_trades.extend(trades)
        for key in totals:
            totals[key] += counts[key]

    closed = [t for t in all_trades if t.status == "closed"]
    open_end = [t for t in all_trades if t.status == "open_at_window_end"]
    no_fill = [t for t in all_trades if t.status == "no_fill_fact"]
    unverified = {
        "entry_limit_row_missing_trades": sum(
            1 for t in all_trades if t.entry_limit_row_missing
        ),
        "exit_limit_row_missing_trades": sum(
            1 for t in all_trades if t.exit_limit_row_missing
        ),
        "closed_trades_with_either": sum(
            1
            for t in closed
            if t.entry_limit_row_missing or t.exit_limit_row_missing
        ),
    }

    placebo = matched_horizon_placebo(
        tuple(
            Trade(
                code=t.code,
                entry_signal_date=t.entry_signal_date,
                entry_date=t.entry_date,
                exit_signal_date=t.exit_signal_date,
                exit_date=t.exit_date,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                return_pct=t.gross_return_pct,
                mae_pct=t.mae_pct,
                entry_index=t.entry_index,
                exit_index=t.exit_index,
                status="closed",
            )
            for t in closed
        ),
        placebo_series,
        start_date=start_date,
        end_date=end_date,
        reps=placebo_reps,
        seed=seed,
        warmup_bars=spec.lookback_bars,
    )

    stats = _closed_stats(closed)
    criteria = {
        "mean_net_return_positive": (
            stats["mean_net_return_pct"] is not None
            and stats["mean_net_return_pct"] > 0
        ),
        "placebo_upper_tail_p_le_0_05": (
            placebo["upper_tail_p_value"] is not None
            and placebo["upper_tail_p_value"] <= 0.05
        ),
    }
    criteria["pass"] = (
        criteria["mean_net_return_positive"]
        and criteria["placebo_upper_tail_p_le_0_05"]
    )

    result: dict[str, object] = {
        "start_date": start_date,
        "end_date": end_date,
        "primary_closed_trades": stats,
        "concentration_disclosure": _concentration(closed),
        "open_at_window_end": {
            "trades": len(open_end),
            "mean_marked_net_return_pct": (
                float(np.mean([t.net_return_pct for t in open_end]))
                if open_end
                else None
            ),
            "median_marked_net_return_pct": _percentile(
                np.asarray([t.net_return_pct for t in open_end], dtype=float), 50
            ),
        },
        "no_fill_fact": {"trades": len(no_fill)},
        "unverified_fill_disclosure": unverified,
        "signals_excluded": dict(totals),
        "fill_delays": {
            "entries_delayed_beyond_next_open": sum(
                1 for t in all_trades if t.entry_delay_days > 0
            ),
            "exits_delayed_beyond_next_open": sum(
                1 for t in all_trades if t.exit_delay_days > 0
            ),
            "max_exit_delay_days": max(
                (t.exit_delay_days for t in all_trades), default=0
            ),
        },
        "placebo": placebo,
        "judgment_criteria": criteria,
    }
    if include_trades:
        result["closed_trades"] = tuple(closed)
    return result


def run_right_side_wave(
    pit_root: Path,
    *,
    start_date: str = PREREGISTERED_START_DATE,
    split_date: str = PREREGISTERED_SPLIT_DATE,
    end_date: str | None = None,
    placebo_reps: int = PREREGISTERED_PLACEBO_REPS,
    seed: int = PREREGISTERED_SEED,
    commission_rate: float = PREREGISTERED_COMMISSION_RATE,
    min_commission: float = PREREGISTERED_MIN_COMMISSION,
    spec: WaveSpec = WaveSpec(),
) -> dict[str, object]:
    """Run the frozen walk-forward once.

    Leaving every keyword at its default is the only invocation that counts as
    *the* preregistered result; any override is a smoke run and shows up as
    ``preregistered_parameters_used: false`` in the output itself.
    """

    study_end = end_date or PREREGISTERED_END_DATE
    is_frozen_run = (
        start_date == PREREGISTERED_START_DATE
        and split_date == PREREGISTERED_SPLIT_DATE
        and study_end == PREREGISTERED_END_DATE
        and placebo_reps == PREREGISTERED_PLACEBO_REPS
        and seed == PREREGISTERED_SEED
        and commission_rate == PREREGISTERED_COMMISSION_RATE
        and min_commission == PREREGISTERED_MIN_COMMISSION
        and spec == WaveSpec()
    )
    calendar = load_trade_dates(pit_root)
    calendar_index = {int(day): position for position, day in enumerate(calendar)}
    series, coverage = load_priced_panel(
        pit_root, start_date=start_date, end_date=study_end
    )
    universe, universe_report = build_universe(series, load_misaligned_codes())
    del series
    limit_panel, limit_coverage = load_limit_panel(
        pit_root, start_date=start_date, end_date=study_end
    )
    st_timeline = load_st_timeline(pit_root)
    placebo_series = {item.code: _adjusted_series(item) for item in universe}
    costs = CostModel(commission_rate=commission_rate, min_commission=min_commission)

    oos_start = next(day for day in calendar if day > split_date)
    windows = {
        "in_sample": (int(start_date), int(min(split_date, study_end))),
        "out_of_sample": (int(oos_start), int(study_end)),
    }
    results = {
        name: evaluate_window(
            universe,
            spec=spec,
            costs=costs,
            st_timeline=st_timeline,
            limit_panel=limit_panel,
            placebo_series=placebo_series,
            calendar_index=calendar_index,
            start_date=window[0],
            end_date=window[1],
            placebo_reps=placebo_reps,
            seed=seed if name == "in_sample" else seed + 100,
        )
        for name, window in windows.items()
    }
    trade_level_pass = all(
        results[name]["judgment_criteria"]["pass"] for name in windows
    )
    return {
        "study": "m3-right-side-wave-trade-level",
        "preregistration": PREREGISTRATION,
        "enhancement_contract": ENHANCEMENT_CONTRACT,
        "preregistered_parameters_used": is_frozen_run,
        "rule_spec": {
            "short_window": spec.short_window,
            "mid_window": spec.mid_window,
            "long_window": spec.long_window,
            "lookback_bars": spec.lookback_bars,
            "range_position_max": spec.range_position_max,
            "activation_limit_fraction": spec.activation_limit_fraction,
            "entry_window_bars": spec.entry_window_bars,
            "moving_average_type": "SMA (research proxy; the author names no type)",
            "exit_rule": "MA5 < MA20 at the close (enhancement layer, owner-delegated)",
        },
        "cost_model": {
            "commission_rate": costs.commission_rate,
            "transfer_fee_rate": costs.transfer_fee_rate,
            "stamp_duty_rate": costs.stamp_duty_rate,
            "slippage_rate": costs.slippage_rate,
            "min_commission": costs.min_commission,
            "lot_shares": costs.lot_shares,
        },
        "panel_load": coverage,
        "limit_panel_load": limit_coverage,
        "universe": universe_report,
        "pit_disclaimer": (
            "single terminal-vintage reconstruction truncated by trade_date; "
            "algorithm-layer as-of only, knowledge-time-layer PIT is not provable"
        ),
        "windows": results,
        "trade_level_pass_all_four_conditions": trade_level_pass,
        "allowed_claim_if_pass": (
            "conditional on the holding length the exit rule produced, the entry "
            "timing carries information versus matched random entry; NOT a "
            "portfolio-profitability claim and NOT an independent validation of "
            "the trend-failure definition"
        ),
        "real_broker_orders": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit-root", type=Path, default=Path("data/marketdata_pit"))
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--start-date", default=PREREGISTERED_START_DATE)
    parser.add_argument("--split-date", default=PREREGISTERED_SPLIT_DATE)
    parser.add_argument(
        "--end-date",
        default=None,
        help=(
            "Omit for the frozen preregistered end date "
            f"({PREREGISTERED_END_DATE}). Any other value makes this a smoke "
            "run -- see preregistered_parameters_used in the output."
        ),
    )
    parser.add_argument("--placebo-reps", type=int, default=PREREGISTERED_PLACEBO_REPS)
    parser.add_argument("--seed", type=int, default=PREREGISTERED_SEED)
    parser.add_argument(
        "--commission-rate", type=float, default=PREREGISTERED_COMMISSION_RATE
    )
    parser.add_argument(
        "--min-commission", type=float, default=PREREGISTERED_MIN_COMMISSION
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_right_side_wave(
        args.pit_root,
        start_date=args.start_date,
        split_date=args.split_date,
        end_date=args.end_date,
        placebo_reps=args.placebo_reps,
        seed=args.seed,
        commission_rate=args.commission_rate,
        min_commission=args.min_commission,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        name: {
            "closed": block["primary_closed_trades"]["trades"],
            "mean_net_pct": block["primary_closed_trades"]["mean_net_return_pct"],
            "placebo_p": block["placebo"]["upper_tail_p_value"],
            "criteria": block["judgment_criteria"],
        }
        for name, block in report["windows"].items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("trade_level_pass:", report["trade_level_pass_all_four_conditions"])
    print("written:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
