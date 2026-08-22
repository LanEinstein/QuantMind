"""Execution-timing sensitivity driver (disclosure-only, preregistered).

Reprices candidate E's frozen decisions at the signal bar's adjusted close
instead of the next bar's adjusted open. The delta between the two bases is
the magnitude estimate for the registered half-day execution-timing bias
(`m3-cross-card-semantics-retrieval-2026-08-21.md` section 3: the author acts
intraday / at the tail; the research proxy executes at the next open).

Locked by `docs/research/yeren-system/m3-520-exec-timing-sensitivity-
preregistration-2026-08-22.md`: one variable (price basis), frozen fee model,
no placebo, no judgment criteria, conclusions may state magnitude only --
never which basis is "better". The signal-close basis embeds look-ahead by
construction and must never become an executable convention.

Research-only. No playbook, simulator order, broker instruction, or
execution service is created. `real_broker_orders=False` always.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.yeren_research.m3_520 import RuleSpec
from scripts.yeren_research.m3_520_candidate_e import (
    PREREGISTERED_END_DATE,
    PREREGISTERED_SPLIT_DATE,
    PREREGISTERED_START_DATE,
    PREREGISTERED_STOP_DAYS,
    CostModel,
    TradeE,
    build_universe,
    evaluate_window,
    load_st_timeline,
)
from scripts.yeren_research.market import load_trade_dates
from scripts.yeren_research.pit_priced_panel import load_priced_panel

PUBLISHED_BASELINE = Path(
    "data/yeren_research/inventory/m3-520-candidate-e-walkforward-2026-08-21.json"
)
OUTPUT_PATH = Path(
    "data/yeren_research/inventory/m3-520-exec-timing-sensitivity-2026-08-22.json"
)
PREREGISTRATION = (
    "docs/research/yeren-system/"
    "m3-520-exec-timing-sensitivity-preregistration-2026-08-22.md"
)
LOOK_AHEAD_DISCLAIMER = (
    "signal_close repricing embeds look-ahead: the signal forms at that very "
    "close, so same-close fills are not achievable in reality. This run is an "
    "upper-bound style magnitude disclosure only and can never become an "
    "executable convention."
)


def _trade_key(trade: TradeE) -> tuple[str, int, int]:
    return (trade.code, trade.entry_signal_date, trade.entry_index)


def _paired_delta_stats(deltas: list[float]) -> dict[str, float | int]:
    values = sorted(deltas)
    count = len(values)

    def pct(q: float) -> float:
        if not values:
            return float("nan")
        index = min(count - 1, max(0, round(q / 100.0 * (count - 1))))
        return values[index]

    return {
        "trades": count,
        "mean_pp": sum(values) / count if values else float("nan"),
        "median_pp": pct(50),
        "p10_pp": pct(10),
        "p90_pp": pct(90),
    }


def _basis_summary(res: dict[str, object]) -> dict[str, object]:
    stats = res["primary_cohort_s8_c"]
    return {
        "trades": stats["trades"],
        "mean_net_return_pct": stats["mean_net_return_pct"],
        "median_net_return_pct": stats["median_net_return_pct"],
        "win_rate_net_pct": stats["win_rate_net_pct"],
    }


def _evaluate_window_block(
    universe,
    spec,
    costs,
    st_timeline,
    pit_root,
    calendar_index,
    start_date,
    end_date,
) -> dict[str, object]:
    common = dict(
        universe=universe,
        spec=spec,
        costs=costs,
        st_timeline=st_timeline,
        pit_root=pit_root,
        calendar_index=calendar_index,
        start_date=start_date,
        end_date=end_date,
        placebo_reps=0,
        seed=0,
    )
    res_open = evaluate_window(
        **common, execution_basis="next_open", include_trades=True
    )
    res_close = evaluate_window(
        **common, execution_basis="signal_close", include_trades=True
    )

    open_by_key = {
        _trade_key(t): t for t in res_open["primary_trades"]
    }
    close_by_key = {
        _trade_key(t): t for t in res_close["primary_trades"]
    }
    identical_set = set(open_by_key) == set(close_by_key)
    deltas = [
        close.net_return_pct - open_by_key[key].net_return_pct
        for key, close in close_by_key.items()
        if key in open_by_key
    ]

    published = json.loads(PUBLISHED_BASELINE.read_text(encoding="utf-8"))
    published_stats = published["windows"][
        "in_sample" if start_date == int(PREREGISTERED_START_DATE) else "out_of_sample"
    ]["primary_cohort_s8_c"]
    crosscheck = {
        "published_trade_count": published_stats["trades"],
        "run_trade_count": res_open["primary_cohort_s8_c"]["trades"],
        "counts_match": (
            published_stats["trades"] == res_open["primary_cohort_s8_c"]["trades"]
        ),
        "mean_net_delta_vs_published_pp": (
            res_open["primary_cohort_s8_c"]["mean_net_return_pct"]
            - published_stats["mean_net_return_pct"]
        ),
    }

    return {
        "start_date": start_date,
        "end_date": end_date,
        "identical_trade_set_next_open_vs_signal_close": identical_set,
        "next_open_basis": _basis_summary(res_open),
        "signal_close_basis": _basis_summary(res_close),
        "per_trade_delta_pp_signal_close_minus_next_open": _paired_delta_stats(deltas),
        "published_baseline_crosscheck": crosscheck,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit-root", type=Path, default=Path("data/marketdata_pit"))
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args(argv)

    calendar = load_trade_dates(args.pit_root)
    calendar_index = {int(day): position for position, day in enumerate(calendar)}
    series, coverage = load_priced_panel(
        args.pit_root,
        start_date=PREREGISTERED_START_DATE,
        end_date=PREREGISTERED_END_DATE,
    )
    universe, universe_report = build_universe(series)
    st_timeline = load_st_timeline(args.pit_root)
    spec = RuleSpec(stop_days=PREREGISTERED_STOP_DAYS)
    # One variable only: the price basis. Fees stay at the frozen values.
    costs = CostModel()

    oos_start = int(
        next(day for day in calendar if day > PREREGISTERED_SPLIT_DATE)
    )
    windows = {
        "in_sample": (
            _evaluate_window_block(
                universe, spec, costs, st_timeline, args.pit_root,
                calendar_index, int(PREREGISTERED_START_DATE),
                int(min(PREREGISTERED_SPLIT_DATE, PREREGISTERED_END_DATE)),
            )
        ),
        "out_of_sample": (
            _evaluate_window_block(
                universe, spec, costs, st_timeline, args.pit_root,
                calendar_index, oos_start, int(PREREGISTERED_END_DATE),
            )
        ),
    }

    report = {
        "study": "m3-520-execution-timing-sensitivity",
        "preregistration": PREREGISTRATION,
        "definition": {
            "next_open": "candidate E's frozen convention: fill at T+1 open",
            "signal_close": "same decisions repriced at the signal bar's close",
            "delta_meaning": (
                "signal_close minus next_open per-trade net return difference "
                "= magnitude of the half-day execution-timing bias"
            ),
        },
        "allowed_conclusion": "magnitude only; never which basis is better",
        "look_ahead_disclaimer": LOOK_AHEAD_DISCLAIMER,
        "cost_model_note": (
            "frozen candidate E fees (commission 0.00025 with 5 CNY floor); "
            "one variable only"
        ),
        "panel_load": coverage,
        "universe": universe_report,
        "windows": windows,
        "real_broker_orders": False,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["windows"], ensure_ascii=False, indent=2))
    print("written:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
