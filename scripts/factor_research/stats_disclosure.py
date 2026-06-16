"""Honest multiple-testing disclosure gates (Phase 3).

Thin reuse of the already-built ``backend.strategy_evolution`` statistics so
the factor research and the live evolution lane share one implementation:

* **Deflated Sharpe Ratio** (Bailey-López de Prado) — the MAIN statistical
  gate: is the selected strategy's Sharpe significant *after* deflating for the
  number of trials run during the search? Floor 0.95.
* **Minimum Backtest Length** (MinBTL) — admission: is the train_val history
  long enough to support the pre-declared trial count, or would a Sharpe of 1
  arise by luck alone?
* **PBO via CSCV** — disclosure: probability the in-sample-best weighting is
  out-of-sample sub-median (overfit selection).
* **Hansen SPA** — disclosure: does the best candidate beat the incumbent
  (the live hand-set FACTOR_WEIGHTS) after data-snooping correction?

These do not manufacture alpha — they only lower the false-positive rate so the
search stays honest. The verdict on real profitability is the locked test set
(Phase 4). ``backend.strategy_evolution`` is import-allowed here (it is not in
the TID251-banned decision-path set). Deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import kurtosis as _kurtosis
from scipy.stats import skew as _skew

from backend.strategy_evolution.anti_overfit import (
    deflated_sharpe_ratio,
    meets_anti_overfit_bar,
)
from backend.strategy_evolution.disclosure_stats import (
    admit_batch,
    minimum_backtest_length,
    pbo_cscv,
    spa_disclosure,
)

DSR_FLOOR: float = 0.95


@dataclass(frozen=True)
class DisclosureReport:
    """The full honest-disclosure panel for a selected strategy + its search."""

    observed_sharpe_per_period: float
    n_periods: int
    n_trials: int
    dsr: float
    dsr_passes: bool
    min_backtest_length: int
    n_observations: int
    minbtl_admits: bool
    pbo: float
    spa_p_value: float


def _sharpe_moments(net_rets: Sequence[float]) -> tuple[float, float, float, float]:
    """Per-period Sharpe + variance-of-SR estimate + skew + kurtosis."""
    arr = np.asarray(net_rets, dtype=float)
    n = len(arr)
    if n < 2:
        return 0.0, 0.0, 0.0, 3.0
    std = float(arr.std(ddof=1))
    if std == 0.0:
        return 0.0, 0.0, 0.0, 3.0
    sr = float(arr.mean()) / std
    var_sr = (1.0 + 0.5 * sr * sr) / n  # standard SR-variance approximation
    skew = float(_skew(arr))
    kurt = float(_kurtosis(arr, fisher=False))  # non-excess (normal == 3)
    return sr, var_sr, skew, kurt


def deflated_sharpe(net_rets: Sequence[float], *, n_trials: int) -> float:
    """Deflated Sharpe of a net-return series given the search's trial count."""
    sr, var_sr, skew, kurt = _sharpe_moments(net_rets)
    n = len(net_rets)
    if n < 2 or var_sr == 0.0:
        return 0.0
    return deflated_sharpe_ratio(
        sr,
        n_trials=n_trials,
        variance_of_sr=var_sr,
        n_samples=n,
        skew=skew,
        kurtosis=kurt,
    )


def disclose(
    *,
    selected_net_rets: Sequence[float],
    candidate_return_matrix: Sequence[Sequence[float]],
    incumbent_excess_matrix: Sequence[Sequence[float]],
    n_trials: int,
    n_observations: int,
    target_sharpe_annual: float = 1.0,
) -> DisclosureReport:
    """Run all four gates for the selected strategy + its candidate search.

    Args:
        selected_net_rets: the chosen strategy's per-period net returns.
        candidate_return_matrix: ``[candidate][period]`` net returns of every
            searched weighting (the full pool — never just the survivors).
        incumbent_excess_matrix: ``[candidate][period]`` of candidate-minus-
            incumbent (live FACTOR_WEIGHTS) net returns, for SPA.
        n_trials: the cumulative pre-declared trial count (includes failures).
        n_observations: the train_val history length (periods) for MinBTL.
    """
    sr, _, _, _ = _sharpe_moments(selected_net_rets)
    dsr = deflated_sharpe(selected_net_rets, n_trials=n_trials)
    minbtl = minimum_backtest_length(
        n_trials=n_trials, target_sharpe_annual=target_sharpe_annual
    )
    admits = admit_batch(
        n_trials=n_trials,
        n_observations=n_observations,
        target_sharpe_annual=target_sharpe_annual,
    )
    pbo = (
        pbo_cscv(candidate_return_matrix).pbo
        if len(candidate_return_matrix) >= 2
        else 1.0
    )
    spa_p = (
        spa_disclosure(incumbent_excess_matrix).p_value
        if len(incumbent_excess_matrix) >= 1
        else 1.0
    )
    return DisclosureReport(
        observed_sharpe_per_period=sr,
        n_periods=len(selected_net_rets),
        n_trials=n_trials,
        dsr=dsr,
        dsr_passes=meets_anti_overfit_bar(dsr, floor=DSR_FLOOR),
        min_backtest_length=minbtl,
        n_observations=n_observations,
        minbtl_admits=admits,
        pbo=pbo,
        spa_p_value=spa_p,
    )


__all__ = [
    "DSR_FLOOR",
    "DisclosureReport",
    "deflated_sharpe",
    "disclose",
]
