"""Tier-threshold calibration from the universe's 1-lot cost distribution.

P0-7-amendment-2026-05-24 §2.5: the Micro/Small cash thresholds in
``config/risk.yaml`` are *initial* values that should be **derived** from
the full-market tradable universe's actual 1-lot cost distribution, not
hard-coded. This module provides the pure derivation: given the per-lot
costs of the screened universe + the P0-7 single-stock pct, it recommends
the two thresholds at the point where the cheapest / median 1-lot just
fits the 15% rule.

The principle (so the numbers are explainable, not magic):

* ``micro_max_cash = p10_lot / max_single_stock_pct`` — below this even a
  cheap (10th-percentile) 1-lot cannot satisfy the 15% single-stock rule,
  so the account is ETF-only / exception territory (Micro).
* ``small_max_cash = median_lot / max_single_stock_pct`` — at/above this a
  *median* 1-lot fits within 15%, so the full P0-7 trio applies cleanly
  (Normal). Between the two is Small.

For a plausible A-share distribution (p10 ≈ ¥300 broad ETF lot, median ≈
¥1,500 low-price stock lot) at 15% this reproduces the shipped
¥2,000 / ¥10,000 — i.e. the locked values are calibrated, not arbitrary.

This is an offline derivation helper for the operator (run it, then edit
``config/risk.yaml`` via amendment + restart). It never mutates config and
never touches the runtime path. Pure stdlib; no
``backend.{llm,agents,mirofish}`` import.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.budget_policy.policy import BudgetPolicyError

DEFAULT_MICRO_PERCENTILE: float = 10.0
DEFAULT_SMALL_PERCENTILE: float = 50.0


@dataclass(frozen=True)
class TierCalibration:
    """Recommended tier thresholds + the distribution stats behind them."""

    micro_max_cash_yuan: float
    small_max_cash_yuan: float
    micro_percentile_lot_cost: float
    small_percentile_lot_cost: float
    sample_size: int


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile of an already-sorted, non-empty list."""
    if not 0.0 <= pct <= 100.0:
        raise BudgetPolicyError(f"percentile must be in [0, 100], got {pct}")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[lo]
    frac = rank - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def calibrate_tiers(
    per_lot_costs: list[float],
    max_single_stock_pct: float,
    *,
    micro_percentile: float = DEFAULT_MICRO_PERCENTILE,
    small_percentile: float = DEFAULT_SMALL_PERCENTILE,
) -> TierCalibration:
    """Derive recommended Micro/Small cash thresholds from a lot-cost sample.

    Args:
        per_lot_costs: One-lot costs (¥) of the tradable universe. Non-finite
            / non-positive entries are dropped fail-closed.
        max_single_stock_pct: The P0-7 single-stock pct (0.15) — passed in
            so the calibration shares the one locked source of truth.
        micro_percentile / small_percentile: distribution points used for
            the Micro / Small ceilings (defaults 10th / 50th).

    Raises:
        BudgetPolicyError: no valid costs, bad pct, or a distribution too
            narrow to yield distinct (micro < small) tiers.
    """
    if not 0.0 < max_single_stock_pct <= 1.0:
        raise BudgetPolicyError(
            f"max_single_stock_pct must be in (0, 1], got {max_single_stock_pct}"
        )
    if micro_percentile >= small_percentile:
        raise BudgetPolicyError(
            "micro_percentile must be < small_percentile "
            f"({micro_percentile} >= {small_percentile})"
        )
    costs = sorted(c for c in per_lot_costs if math.isfinite(c) and c > 0)
    if not costs:
        raise BudgetPolicyError("no valid (finite, positive) lot costs to calibrate")

    p_micro = _percentile(costs, micro_percentile)
    p_small = _percentile(costs, small_percentile)
    micro_max = p_micro / max_single_stock_pct
    small_max = p_small / max_single_stock_pct
    if micro_max >= small_max:
        raise BudgetPolicyError(
            "distribution too narrow to derive distinct tiers "
            f"(micro {micro_max:.1f} >= small {small_max:.1f}) — "
            "review the universe sample"
        )
    return TierCalibration(
        micro_max_cash_yuan=micro_max,
        small_max_cash_yuan=small_max,
        micro_percentile_lot_cost=p_micro,
        small_percentile_lot_cost=p_small,
        sample_size=len(costs),
    )


__all__ = [
    "DEFAULT_MICRO_PERCENTILE",
    "DEFAULT_SMALL_PERCENTILE",
    "TierCalibration",
    "calibrate_tiers",
]
