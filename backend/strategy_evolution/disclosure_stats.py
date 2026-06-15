"""Batch overfit disclosure statistics (AE-005, amendment 2026-06-14 §2.1/§2.3).

Pure, deterministic math — no IO, no clock, no LLM. Three statistics that the
quant-parameter evolution lane attaches to a :class:`CandidateBatch`:

* :func:`minimum_backtest_length` / :func:`admit_batch` — the **MinBTL
  admission gate** (amendment §2.1, the only batch-level gate with veto power
  alongside DSR). Given ``N`` cumulative trials, a history too short to tell a
  real edge from the expected-maximum spurious Sharpe of ``N`` zero-skill
  tries is rejected for the WHOLE batch — you cannot earn statistical
  significance you never had the sample size for.
* :func:`pbo_cscv` — the **Probability of Backtest Overfitting** via
  Combinatorially-Symmetric Cross-Validation (Bailey, Borwein, López de Prado,
  Zhu 2017). DISCLOSURE ONLY (amendment §2.3 — "CPCV-PBO/PBO 降披露"): the
  fraction of IS/OOS splits where the in-sample winner underperformed the OOS
  median. High PBO ⇒ the search is selecting noise.
* :func:`spa_disclosure` — a self-contained, deterministic **Superior
  Predictive Ability** statistic (Hansen 2005; the canonical reference
  implementation is ``arch``'s NCSA — not vendored, this is a fixed-seed
  stationary-bootstrap re-derivation). SPA's benchmark is the incumbent
  pinned parameter set (amendment §2.3 — "SPA 基准 = 现役 pinned 参数"):
  it asks whether the batch's best candidate genuinely beats the incumbent
  once the whole search is accounted for. DISCLOSURE ONLY.

Only DSR (in :mod:`backend.strategy_evolution.anti_overfit`) and the MinBTL
admission gate have veto power; PBO and SPA are reported, never auto-reject
(amendment §2.3 — "门做减法" prevents the small-A-share-sample power collapse).

References: Bailey & López de Prado (2014), "The Deflated Sharpe Ratio" and
"Pseudo-Mathematics and Financial Charlatanism" (MinBTL); Bailey, Borwein,
López de Prado, Zhu (2017), "The Probability of Backtest Overfitting" (PBO);
Hansen (2005), "A Test for Superior Predictive Ability" (SPA). All formulas
re-derived; no code copied.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

from backend.strategy_evolution.anti_overfit import expected_max_sharpe

_TRADING_DAYS_PER_YEAR = 252
"""Daily → annual Sharpe scaling (A-share calendar approximation)."""

DEFAULT_TARGET_SHARPE_ANNUAL = 1.0
"""The annualised Sharpe the admission gate sizes history against — a strategy
worth shadowing must clear a Sharpe of ~1 net of the spurious-max bar."""

_SPA_BOOTSTRAP_RESAMPLES = 500
"""Stationary-bootstrap resamples for the SPA null (fixed, disclosure-only)."""

_SPA_BOOTSTRAP_SEED = 20260614
"""Locked seed — SPA disclosure is bit-identical across runs."""

_SPA_AVG_BLOCK_LENGTH = 5
"""Expected geometric block length for the stationary bootstrap (serial
dependence in daily PnL)."""


# ---------------------------------------------------------------------------
# MinBTL admission gate (veto)
# ---------------------------------------------------------------------------


def minimum_backtest_length(
    *,
    n_trials: int,
    target_sharpe_annual: float = DEFAULT_TARGET_SHARPE_ANNUAL,
    periods_per_year: int = _TRADING_DAYS_PER_YEAR,
) -> int:
    """Minimum number of daily observations to out-rank ``N`` spurious tries.

    The expected maximum annualised Sharpe of ``n_trials`` zero-skill strategies
    over ``T`` daily samples is ``sqrt(periods_per_year) · E_unit / sqrt(T)``
    where ``E_unit = E[max SR]`` at unit variance. The admission length is the
    smallest ``T`` for which that spurious bar drops below ``target_sharpe_annual``
    — below it, even a true edge of ``target`` is indistinguishable from the best
    of ``N`` coin flips.

    Returns the integer observation count (ceil); ``n_trials <= 1`` needs no
    multiple-testing margin, so it returns the 2-observation floor.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if target_sharpe_annual <= 0.0:
        raise ValueError("target_sharpe_annual must be > 0")
    if periods_per_year < 1:
        raise ValueError("periods_per_year must be >= 1")
    e_unit = expected_max_sharpe(n_trials, variance_of_sr=1.0)
    if e_unit <= 0.0:
        return 2  # single trial: no spurious-max margin to clear
    # sqrt(ppy)·e_unit/sqrt(T) < target  ⇒  T > ppy·(e_unit/target)²
    min_obs = periods_per_year * (e_unit / target_sharpe_annual) ** 2
    return max(2, math.ceil(min_obs))


def admit_batch(
    *,
    n_trials: int,
    n_observations: int,
    target_sharpe_annual: float = DEFAULT_TARGET_SHARPE_ANNUAL,
) -> bool:
    """Batch admission: is the history long enough for ``n_trials`` (veto gate).

    ``True`` ⇒ the batch may proceed to per-candidate evaluation; ``False`` ⇒
    the whole batch is rejected (amendment §2.1 — "历史长度配不上试验数 N 即拒整批").
    """
    if n_observations < 2:
        return False
    return n_observations >= minimum_backtest_length(
        n_trials=n_trials, target_sharpe_annual=target_sharpe_annual
    )


# ---------------------------------------------------------------------------
# PBO via CSCV (disclosure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PBOResult:
    """Probability-of-backtest-overfitting disclosure (never a veto)."""

    pbo: float
    n_combinations: int
    n_strategies: int
    median_logit: float


def _sharpe(series: Sequence[float]) -> float:
    """Sample Sharpe (mean / std, ddof=1); 0.0 when undefined/degenerate."""
    n = len(series)
    if n < 2:
        return 0.0
    mean = sum(series) / n
    var = sum((x - mean) ** 2 for x in series) / (n - 1)
    if var <= 0.0:
        return 0.0
    return mean / math.sqrt(var)


def _combinations_indices(n: int, k: int) -> list[tuple[int, ...]]:
    """All k-subsets of ``range(n)`` (deterministic lexicographic order)."""
    from itertools import combinations

    return [combo for combo in combinations(range(n), k)]


def pbo_cscv(
    returns_matrix: Sequence[Sequence[float]],
    *,
    n_splits: int = 10,
) -> PBOResult:
    """CSCV probability of backtest overfitting (Bailey et al. 2017, disclosure).

    ``returns_matrix`` is ``[strategy][period]`` — every candidate's per-period
    return over the SHARED window (equal length; the batch shares one data
    window by construction). The time axis is partitioned into ``n_splits``
    contiguous blocks; for each balanced way to assign half the blocks to IS and
    the rest to OOS, the IS-best strategy's OOS rank ``ω`` yields the logit
    ``λ = ln(ω/(1-ω))``. ``λ <= 0`` (IS winner below the OOS median) is an
    overfit instance; PBO is their fraction.

    Fail-closed: < 2 strategies or < ``n_splits`` periods returns ``pbo=1.0``
    (cannot disprove overfitting from too little data).
    """
    n_strategies = len(returns_matrix)
    if n_strategies < 2:
        return PBOResult(
            pbo=1.0,
            n_combinations=0,
            n_strategies=n_strategies,
            median_logit=0.0,
        )
    n_periods = len(returns_matrix[0])
    if any(len(row) != n_periods for row in returns_matrix):
        raise ValueError("returns_matrix rows must be equal length")
    if n_splits < 2 or n_splits % 2 != 0 or n_periods < n_splits:
        return PBOResult(
            pbo=1.0,
            n_combinations=0,
            n_strategies=n_strategies,
            median_logit=0.0,
        )

    # Contiguous, near-equal block boundaries on the time axis.
    base = n_periods // n_splits
    sizes = [base + (1 if i < n_periods % n_splits else 0) for i in range(n_splits)]
    bounds: list[tuple[int, int]] = []
    start = 0
    for size in sizes:
        bounds.append((start, start + size))
        start += size

    logits: list[float] = []
    half = n_splits // 2
    for is_blocks in _combinations_indices(n_splits, half):
        is_set = set(is_blocks)
        is_idx = [i for b in is_blocks for i in range(*bounds[b])]
        oos_idx = [
            i for b in range(n_splits) if b not in is_set for i in range(*bounds[b])
        ]
        is_perf = [_sharpe([row[i] for i in is_idx]) for row in returns_matrix]
        oos_perf = [_sharpe([row[i] for i in oos_idx]) for row in returns_matrix]
        winner = max(range(n_strategies), key=lambda s: is_perf[s])
        # OOS relative rank of the IS winner in (0, 1): how many strategies it
        # beats OOS, smoothed to avoid 0/1 logit singularities.
        beaten = sum(1 for s in range(n_strategies) if oos_perf[winner] > oos_perf[s])
        omega = (beaten + 0.5) / n_strategies
        omega = min(max(omega, 1e-9), 1.0 - 1e-9)
        logits.append(math.log(omega / (1.0 - omega)))

    overfit = sum(1 for lam in logits if lam <= 0.0)
    ordered = sorted(logits)
    mid = len(ordered) // 2
    median = (
        ordered[mid]
        if len(ordered) % 2 == 1
        else (ordered[mid - 1] + ordered[mid]) / 2.0
    )
    return PBOResult(
        pbo=overfit / len(logits),
        n_combinations=len(logits),
        n_strategies=n_strategies,
        median_logit=median,
    )


# ---------------------------------------------------------------------------
# SPA disclosure (deterministic stationary bootstrap)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SPAResult:
    """Hansen SPA disclosure vs the incumbent baseline (never a veto)."""

    spa_statistic: float
    p_value: float
    n_candidates: int
    n_observations: int
    best_candidate_index: int


def _stationary_bootstrap_indices(
    n: int, rng: random.Random, avg_block: int
) -> list[int]:
    """Politis-Romano stationary bootstrap index draw (geometric blocks)."""
    if n <= 0:
        return []
    p = 1.0 / max(1, avg_block)
    out: list[int] = []
    idx = rng.randrange(n)
    while len(out) < n:
        out.append(idx)
        if rng.random() < p:
            idx = rng.randrange(n)
        else:
            idx = (idx + 1) % n
    return out


def spa_disclosure(
    excess_matrix: Sequence[Sequence[float]],
) -> SPAResult:
    """Hansen (2005) consistent-SPA p-value vs the incumbent (disclosure only).

    ``excess_matrix`` is ``[candidate][period]`` of the candidate-minus-incumbent
    per-period PnL (the incumbent pinned parameter set is the benchmark, so each
    row is already an excess series). The studentized max statistic
    ``T_SPA = max_k max(0, sqrt(T)·mean_k/std_k)`` is compared to a fixed-seed
    stationary-bootstrap null with Hansen's consistent recentering (only
    candidates that are not detectably inferior contribute to the null).

    Deterministic: the bootstrap uses the locked seed, so the p-value is
    bit-identical across runs. Fail-closed: empty input or a single
    observation returns ``p_value=1.0`` (no evidence of superiority).
    """
    n_candidates = len(excess_matrix)
    if n_candidates == 0:
        return SPAResult(
            spa_statistic=0.0,
            p_value=1.0,
            n_candidates=0,
            n_observations=0,
            best_candidate_index=-1,
        )
    n_obs = len(excess_matrix[0])
    if any(len(row) != n_obs for row in excess_matrix):
        raise ValueError("excess_matrix rows must be equal length")
    if n_obs < 2:
        return SPAResult(
            spa_statistic=0.0,
            p_value=1.0,
            n_candidates=n_candidates,
            n_observations=n_obs,
            best_candidate_index=-1,
        )

    means = [sum(row) / n_obs for row in excess_matrix]
    stds: list[float] = []
    for row, mean in zip(excess_matrix, means, strict=True):
        var = sum((x - mean) ** 2 for x in row) / (n_obs - 1)
        stds.append(math.sqrt(var) if var > 0.0 else 0.0)

    def _z(mean: float, std: float) -> float:
        if std <= 0.0:
            return 0.0
        return math.sqrt(n_obs) * mean / std

    z_scores = [_z(m, s) for m, s in zip(means, stds, strict=True)]
    observed = max((max(0.0, z) for z in z_scores), default=0.0)
    best_idx = max(range(n_candidates), key=lambda k: z_scores[k])

    # Hansen's consistent recentering threshold A_k = sqrt(omega_k²/T · 2 loglogT):
    # only candidates not demonstrably inferior contribute to the null.
    loglog = math.log(math.log(n_obs)) if n_obs > math.e else 1.0
    recenter: list[float] = []
    for mean, std in zip(means, stds, strict=True):
        if std <= 0.0:
            recenter.append(0.0)
            continue
        threshold = -(std / math.sqrt(n_obs)) * math.sqrt(max(2.0 * loglog, 0.0))
        recenter.append(mean if mean <= threshold else 0.0)

    rng = random.Random(_SPA_BOOTSTRAP_SEED)
    ge = 0
    for _ in range(_SPA_BOOTSTRAP_RESAMPLES):
        idx = _stationary_bootstrap_indices(n_obs, rng, _SPA_AVG_BLOCK_LENGTH)
        boot_stat = 0.0
        for k in range(n_candidates):
            std = stds[k]
            if std <= 0.0:
                continue
            row = excess_matrix[k]
            boot_mean = sum(row[i] for i in idx) / n_obs
            # Hansen consistent SPA: the pivotal (boot_mean − sample_mean) is
            # mean-zero noise; the recentering offset g_k (≤0, negative only for
            # demonstrably-inferior candidates) pulls inferior models out of the
            # max so they cannot inflate the null distribution.
            boot_z = math.sqrt(n_obs) * (boot_mean - means[k] + recenter[k]) / std
            boot_stat = max(boot_stat, max(0.0, boot_z))
        if boot_stat >= observed:
            ge += 1

    return SPAResult(
        spa_statistic=observed,
        p_value=ge / _SPA_BOOTSTRAP_RESAMPLES,
        n_candidates=n_candidates,
        n_observations=n_obs,
        best_candidate_index=best_idx,
    )


__all__ = [
    "DEFAULT_TARGET_SHARPE_ANNUAL",
    "PBOResult",
    "SPAResult",
    "admit_batch",
    "minimum_backtest_length",
    "pbo_cscv",
    "spa_disclosure",
]
