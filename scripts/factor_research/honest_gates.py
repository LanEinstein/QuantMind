"""Honest-gate refinements: ONC effective-N + HAC SR variance (QGR-2 build-new ④).

The arena's significance gates must not over-count correlated/overlapping
evidence (QGR plan §4.1):

* **ONC effective N** — searching M *correlated* candidate strategies is not M
  independent tries. :func:`onc_effective_n` clusters the candidate return
  series by correlation (single-linkage at a threshold — a deterministic,
  dependency-light stand-in for de Prado's silhouette-optimal ONC; same purpose:
  correlated trials collapse into one effective trial) and returns the cluster
  count. That effective N — not the raw M — feeds the deflation (via the
  :mod:`trial_ledger`'s ``max(legacy, ONC)``), so a grid of near-duplicates
  cannot inflate the trial count and weaken the *legacy* floor while still being
  honest that they are few independent bets.
* **HAC SR variance** — a ≤5-slot gate holds positions for 5 td, so its
  per-period returns are autocorrelated; the IID SR-estimator variance
  ``(1 + ½·SR²)/T`` understates the spurious-max dispersion. :func:`newey_west_lrv`
  (Bartlett kernel) gives the long-run variance and :func:`hac_variance_inflation`
  the ``LRV/var`` factor (≥ 1) that inflates ``variance_of_sr`` before it reaches
  ``expected_max_sharpe`` — so overlapping holdings deflate the DSR at least as
  hard as IID, never less (Lo 2002; López de Prado AFML).

Pure + deterministic; numpy + scipy moments, reusing
``backend.strategy_evolution.anti_overfit``. No IO, no RNG, no wall-clock.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy.stats import kurtosis as _kurtosis
from scipy.stats import skew as _skew

from backend.strategy_evolution.anti_overfit import deflated_sharpe_ratio

DEFAULT_CORR_THRESHOLD = 0.5
"""Single-linkage correlation threshold: |ρ| ≥ this → same cluster (one trial)."""

_VAR_EPS = 1e-24
"""Variance floor — below this is a zero-variance series (float residual), far
below any real A-share return variance (~1e-4); treats it as degenerate."""


# ---------------------------------------------------------------------------
# ONC effective number of trials (correlation clustering)
# ---------------------------------------------------------------------------


def onc_effective_n(
    return_matrix: Sequence[Sequence[float]],
    *,
    corr_threshold: float = DEFAULT_CORR_THRESHOLD,
) -> int:
    """Effective independent-trial count = correlation single-linkage clusters.

    ``return_matrix`` is ``[candidate][period]`` (equal length). Two candidates
    join the same cluster when ``|corr| ≥ corr_threshold``; the number of
    connected components is the effective N. ``0`` for an empty matrix, ``1`` for
    a single candidate or a degenerate (zero-variance) set.
    """
    m = len(return_matrix)
    if m == 0:
        return 0
    if m == 1:
        return 1
    arr = np.asarray([list(r) for r in return_matrix], dtype=float)
    if arr.shape[1] < 2:
        return 1
    # Zero-variance (flat / never-trading) candidates carry no information and
    # would make np.corrcoef emit NaN (no unions → each counts separately,
    # overstating the effective trial count). Collapse ALL of them into a single
    # degenerate cluster; cluster the live (non-flat) rows by correlation.
    variances = arr.var(axis=1)
    live = [i for i in range(m) if variances[i] > _VAR_EPS]
    has_flat = len(live) < m
    if not live:
        return 1  # every candidate degenerate → one effective (no-edge) trial
    if len(live) == 1:
        return 1 + (1 if has_flat else 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(arr[live])
    parent = list(range(len(live)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            rho = corr[i, j]
            if np.isfinite(rho) and abs(rho) >= corr_threshold:
                union(i, j)
    live_clusters = len({find(i) for i in range(len(live))})
    return live_clusters + (1 if has_flat else 0)


# ---------------------------------------------------------------------------
# HAC (Newey-West) SR variance
# ---------------------------------------------------------------------------


def newey_west_lrv(series: Sequence[float], *, lag: int) -> float:
    """Newey-West long-run variance (Bartlett kernel) of ``series``.

    ``LRV = γ0 + 2·Σ_{l=1..lag} (1 − l/(lag+1))·γ_l``. ``lag=0`` returns the
    sample variance (population, /n). Zero-variance input returns ``0.0``.
    """
    if lag < 0:
        raise ValueError("lag must be >= 0")
    arr = np.asarray(list(series), dtype=float)
    n = len(arr)
    if n < 2:
        return 0.0
    mean = float(arr.mean())
    dev = arr - mean
    gamma0 = float(np.dot(dev, dev) / n)
    if gamma0 <= _VAR_EPS:
        return 0.0
    lrv = gamma0
    for l_idx in range(1, min(lag, n - 1) + 1):
        weight = 1.0 - l_idx / (lag + 1)
        gamma_l = float(np.dot(dev[l_idx:], dev[:-l_idx]) / n)
        lrv += 2.0 * weight * gamma_l
    return max(0.0, lrv)


def hac_variance_inflation(series: Sequence[float], *, lag: int) -> float:
    """Autocorrelation inflation factor ``LRV / sample_var`` (≥ 1.0).

    Clamped at 1.0 so negative autocorrelation never *reduces* the spurious-max
    dispersion below the IID bar (fail-safe one-sided deflation).
    """
    arr = np.asarray(list(series), dtype=float)
    n = len(arr)
    if n < 2:
        return 1.0
    var = float(((arr - arr.mean()) ** 2).sum() / n)
    if var <= _VAR_EPS:
        return 1.0
    lrv = newey_west_lrv(series, lag=lag)
    return max(1.0, lrv / var)


def deflated_sharpe_hac(
    net_rets: Sequence[float],
    *,
    n_trials: int,
    hac_lag: int = 0,
) -> float:
    """Deflated Sharpe with a HAC-inflated SR-estimator variance.

    ``hac_lag=0`` reproduces the IID deflation; ``hac_lag>0`` inflates
    ``variance_of_sr`` by the Newey-West factor so autocorrelated overlapping
    holdings deflate at least as hard.
    """
    arr = np.asarray(list(net_rets), dtype=float)
    n = len(arr)
    if n < 2:
        return 0.0
    std = float(arr.std(ddof=1))
    if std == 0.0:
        return 0.0
    sr = float(arr.mean()) / std
    var_sr_iid = (1.0 + 0.5 * sr * sr) / n
    var_sr = var_sr_iid * hac_variance_inflation(net_rets, lag=hac_lag)
    skew = float(_skew(arr))
    kurt = float(_kurtosis(arr, fisher=False))
    return deflated_sharpe_ratio(
        sr,
        n_trials=n_trials,
        variance_of_sr=var_sr,
        n_samples=n,
        skew=skew,
        kurtosis=kurt,
    )


__all__ = [
    "DEFAULT_CORR_THRESHOLD",
    "deflated_sharpe_hac",
    "hac_variance_inflation",
    "newey_west_lrv",
    "onc_effective_n",
]
