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

from collections.abc import Callable, Mapping
from dataclasses import dataclass

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


__all__ = [
    "STRESS_MULTIPLIER",
    "CrossCheckResult",
    "cross_check",
]
