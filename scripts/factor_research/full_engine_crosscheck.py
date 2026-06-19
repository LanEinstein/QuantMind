"""R2-5 engine cross-check of the selected benchmark-relative strategy.

Before the strategy is git-frozen and read against the locked test set, confirm
the portfolio-sort excess is not optimistic under harsher trading friction. The
benchmark-relative backtest already charges a conservative buy/sell-split cost
(buy ≈ 3 bp, sell ≈ 13 bp incl. stamp); this re-runs the SAME strategy under a
STRESSED cost model and verifies the net excess only worsens (more friction can
never manufacture excess) and by a bounded amount — so the R2-6 verdict is robust
to the cost assumption rather than balanced on it.

Scope honesty (documented, not hidden): a faithful full ``backend.backtest``
event-loop / rqalpha differential for a ~300-name WEIGHTED enhanced-index book
(limit-up/down at-fill rejection per name, per-board slippage, integer-lot
rounding) is a large integration out of this session's scope. Following the
established ``backend.strategy_evolution.backtest_oracle`` discipline, the rqalpha
oracle is recorded as UNAVAILABLE (``oracle_cross_checked=False``) — NOT a silent
pass — and the cost-stress cross-check is the engine confirmation we DO run. The
round-1 finding holds by construction: additional friction only lowers net excess,
so it can make a FAIL more robust but can never flip a FAIL into a PASS.

Deterministic, train_val/development use (R2-5 runs it on the selected strategy
before the freeze); LLM-zero.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .benchmark_relative import (
    BUY_COST,
    SELL_COST,
    BenchmarkRelativeResult,
    benchmark_relative_backtest,
)
from .exposure_constraints import DEFAULT_NONCONST_CAP

# Stress multiplier on the conservative buy/sell-split cost (doubles slippage +
# stamp on every turned-over unit of weight).
STRESS_MULTIPLIER: float = 2.0


@dataclass(frozen=True)
class CrossCheckResult:
    """Cost-stress engine cross-check of a benchmark-relative strategy (immutable)."""

    n_periods: int
    base_total_excess: float  # benchmark_relative_backtest is already net-of-cost
    stressed_total_excess: float
    excess_delta: float  # stressed − base (≤ 0 when friction is monotone)
    base_information_ratio: float
    stressed_information_ratio: float
    avg_turnover: float
    excess_max_drawdown: float  # worst cumulative-excess decline (robustness)
    monotone_friction: bool  # stressed ≤ base (more cost never helped)
    oracle_status: str
    oracle_cross_checked: bool


def _excess_max_drawdown(excess: tuple[float, ...]) -> float:
    """Worst peak-to-trough decline of the cumulative excess curve."""
    if not excess:
        return 0.0
    curve = np.cumprod([1.0 + e for e in excess])
    equity = np.concatenate([[1.0], curve])  # count a first-period loss in the peak
    peak = np.maximum.accumulate(equity)
    return float((1.0 - equity / peak).max())


def _rqalpha_oracle_status() -> tuple[str, bool]:
    """Record the rqalpha-oracle status (UNAVAILABLE by scope — documented).

    A full data-bundle event-loop backtest of a weighted enhanced-index book is
    out of R2-5 scope; mirror ``backtest_oracle.run_differential_check`` and
    record UNAVAILABLE rather than claim a pass.
    """
    return (
        "UNAVAILABLE — a full backend.backtest/rqalpha event-loop for a ~300-name "
        "weighted enhanced-index book (per-name limit at-fill, per-board slippage, "
        "integer lots) is out of R2-5 scope; the cost-stress cross-check is the "
        "engine confirmation. More friction only lowers excess (round-1 §7), so "
        "this cannot flip a FAIL into a PASS.",
        False,
    )


def cross_check(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    exposure_constraint: str,
    k: float,
    a_max: float,
    nonconst_cap: float = DEFAULT_NONCONST_CAP,
    horizon: int = 5,
    stress_multiplier: float = STRESS_MULTIPLIER,
) -> CrossCheckResult:
    """Run the strategy at base + stressed cost; confirm friction is monotone.

    ``panel`` must be NEUTRALIZED and (for R2-5) train_val only. Returns the
    base/stressed excess + the monotonicity check + the (UNAVAILABLE) oracle
    status.
    """

    def _run(buy: float, sell: float) -> BenchmarkRelativeResult:
        return benchmark_relative_backtest(
            panel,
            bench_asof,
            index_returns,
            weights=weights,
            horizon=horizon,
            k=k,
            a_max=a_max,
            buy_cost=buy,
            sell_cost=sell,
            exposure_constraint=exposure_constraint,
            nonconst_cap=nonconst_cap,
        )

    base = _run(BUY_COST, SELL_COST)
    stressed = _run(BUY_COST * stress_multiplier, SELL_COST * stress_multiplier)
    delta = stressed.total_excess - base.total_excess
    oracle_status, oracle_ok = _rqalpha_oracle_status()
    return CrossCheckResult(
        n_periods=base.n_periods,
        base_total_excess=base.total_excess,
        stressed_total_excess=stressed.total_excess,
        excess_delta=delta,
        base_information_ratio=base.information_ratio,
        stressed_information_ratio=stressed.information_ratio,
        avg_turnover=base.avg_turnover,
        excess_max_drawdown=_excess_max_drawdown(base.excess_returns),
        monotone_friction=delta <= 1e-12,
        oracle_status=oracle_status,
        oracle_cross_checked=oracle_ok,
    )


def load_selected_strategy(
    path: str,
) -> tuple[str, float, float, float, dict[str, float]]:
    """``(constraint, k, a_max, nonconst_cap, weights)`` from a search-result JSON.

    Pre-freeze read (R2-5/R3-5 run BEFORE the strategy is git-frozen), so unlike
    the locked-test ``load_frozen_strategy`` this does NOT assert against a
    committed pre-commitment — it just reads the selected strategy to stress-test.
    """
    art = json.loads(Path(path).read_text(encoding="utf-8"))
    weights = {k: float(v) for k, v in art["selected_weights"].items()}
    return (
        str(art["selected_constraint"]),
        float(art["selected_k"]),
        float(art["selected_a_max"]),
        float(art["selected_nonconst_cap"]),
        weights,
    )


def _result_dict(res: CrossCheckResult) -> dict[str, object]:
    """Serialize with the round-2 artifact's key names (stable across rounds)."""
    d = asdict(res)
    d["base_ir"] = d.pop("base_information_ratio")
    d["stressed_ir"] = d.pop("stressed_information_ratio")
    return d


def main() -> None:
    from .r2_benchmark_relative_diagnostics import pretest_benchmark_inputs
    from .round2_search import WINSOR_QUANTILE, resolve_carry_inputs

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--benchmark", default="data/factor_research/csi300_daily.csv")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--carry", choices=("r2", "r3"), default="r2")
    # Empty → resolved per --carry (panel from resolve_carry_inputs; search-result
    # / out from the per-round map below). Explicit values win.
    parser.add_argument("--panel", default="")
    parser.add_argument("--search-result", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    carry, panel_path, _, search_default = resolve_carry_inputs(
        args.carry, panel=args.panel
    )
    search_result = args.search_result or search_default
    # Canonical per-round artifact paths (match the committed round-2 artifact +
    # docs: round2_crosscheck_result.json, not r2_crosscheck_result.json).
    crosscheck_out = {
        "r2": "data/factor_research/round2_crosscheck_result.json",
        "r3": "data/factor_research/round3_crosscheck_result.json",
    }
    out_path = args.out or crosscheck_out[args.carry]

    constraint, k, a_max, nonconst_cap, weights = load_selected_strategy(search_result)

    from backend.marketdata_snapshot.store import SnapshotStore

    from .locked_split import LockedSplit
    from .neutralize import neutralize_panel

    # Firewall FIRST: preflight ONLY the date column and assert it is train_val
    # only, BEFORE the factor/label columns are ever read or neutralized. The
    # pre-freeze cross-check covenant is that test data is not consumed at all,
    # so a mis-pointed --panel must fail before any test row is materialized.
    split = LockedSplit.load(args.lock, args.snapshot_root)
    preflight = pd.read_csv(panel_path, usecols=["date"], dtype={"date": str})
    dates = sorted(preflight["date"].astype(str).unique())
    split.assert_all_not_test(dates)
    # Stricter than not-test: the scored rows must be a SUBSET of train_val. An
    # embargo (purge-gap) row is not "test" yet its forward-return label can
    # straddle the test boundary, so a mis-pointed --panel that includes embargo
    # rows must be rejected before any factor/label column is read (fail-closed).
    non_train_val = sorted(set(dates) - set(split.train_val_dates))
    if non_train_val:
        raise ValueError(
            f"--panel has {len(non_train_val)} non-train_val date(s) "
            f"(e.g. {non_train_val[:3]}) — the pre-freeze cross-check scores "
            "train_val ONLY (embargo forward-labels can straddle test)."
        )
    panel = pd.read_csv(panel_path, dtype={"date": str, "code": str, "ts_code": str})
    panel = neutralize_panel(panel, list(carry), winsor_quantile=WINSOR_QUANTILE)

    # Firewall: benchmark weights + CSI300 closes STRICTLY before test_start.
    bench_pit, index_returns = pretest_benchmark_inputs(
        SnapshotStore(args.snapshot_root),
        args.snapshot_root,
        args.benchmark,
        split.test_dates[0],
        dates,
        args.horizon,
    )
    res = cross_check(
        panel,
        bench_pit.asof,
        index_returns,
        weights=weights,
        exposure_constraint=constraint,
        k=k,
        a_max=a_max,
        nonconst_cap=nonconst_cap,
        horizon=args.horizon,
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_result_dict(res), indent=2), encoding="utf-8")
    print(
        f"cross-check [{args.carry}] {constraint}: base_excess="
        f"{res.base_total_excess:+.2%} stressed={res.stressed_total_excess:+.2%} "
        f"delta={res.excess_delta:+.2%} monotone={res.monotone_friction} "
        f"oracle={res.oracle_cross_checked} -> {out}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "STRESS_MULTIPLIER",
    "CrossCheckResult",
    "cross_check",
    "load_selected_strategy",
]
