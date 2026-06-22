"""Multi-strategy fair-comparison harness (QGR-2 build-new ③).

When the arena scores many candidate gates against the baseline panel, a naive
"best p < 0.05" is a data-snooping trap. This harness compares a **pre-declared
family** of candidates against a benchmark with three complementary, deterministic
multiple-testing procedures over a time-series block bootstrap (QGR plan §4.1):

* **Hansen SPA** (reused from ``backend.strategy_evolution.disclosure_stats``) —
  is the single best candidate genuinely superior to the benchmark once the whole
  search is accounted for? Robust to many junk candidates.
* **Romano-Wolf StepM** (:func:`romano_wolf_stepdown`) — a step-down max-t
  procedure controlling the family-wise error rate; tells you *which* candidates
  are superior (strong FWER control), not just whether the best is.
* **BH / BY FDR** (:func:`bh_fdr` / :func:`by_fdr`) — for a large family, control
  the false-discovery rate instead (BY is valid under arbitrary dependence).

The family must be declared up front (the ``labels`` + ``family`` are recorded);
the bootstrap is a fixed-seed stationary (block) bootstrap so the report is
bit-identical across runs. Pure + deterministic; numpy + stdlib RNG only.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from backend.strategy_evolution.disclosure_stats import spa_disclosure

_BOOT_RESAMPLES = 1000
_BOOT_SEED = 20260622
_AVG_BLOCK_LENGTH = 5  # serial dependence in 5-td-held gate returns.


# ---------------------------------------------------------------------------
# Block bootstrap + studentized statistics
# ---------------------------------------------------------------------------


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
        idx = rng.randrange(n) if rng.random() < p else (idx + 1) % n
    return out


def _studentized(excess_matrix: Sequence[Sequence[float]]) -> tuple[
    list[float], list[float], list[float], int
]:
    """Per-candidate ``(t_k, mean_k, std_k)`` + n_obs (one-sided, beats bench)."""
    n_obs = len(excess_matrix[0]) if excess_matrix else 0
    means: list[float] = []
    stds: list[float] = []
    t_stats: list[float] = []
    for row in excess_matrix:
        if len(row) != n_obs:
            raise ValueError("excess rows must be equal length")
        arr = np.asarray(list(row), dtype=float)
        mean = float(arr.mean())
        std = float(arr.std(ddof=1)) if n_obs > 1 else 0.0
        means.append(mean)
        stds.append(std)
        t_stats.append(math.sqrt(n_obs) * mean / std if std > 0 else 0.0)
    return t_stats, means, stds, n_obs


# ---------------------------------------------------------------------------
# Romano-Wolf step-down maxT
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RomanoWolfResult:
    """Step-down maxT FWER result (immutable)."""

    t_stats: tuple[float, ...]
    adjusted_pvalues: tuple[float, ...]
    rejected: tuple[bool, ...]
    order: tuple[int, ...]
    n_observations: int


def romano_wolf_stepdown(
    excess_matrix: Sequence[Sequence[float]],
    *,
    alpha: float = 0.05,
    n_boot: int = _BOOT_RESAMPLES,
    seed: int = _BOOT_SEED,
    avg_block: int = _AVG_BLOCK_LENGTH,
) -> RomanoWolfResult:
    """Romano-Wolf (2005) step-down max-t test that candidates beat the benchmark.

    ``excess_matrix`` is ``[candidate][period]`` of candidate-minus-benchmark
    per-period returns. Studentized one-sided statistics are compared to a
    fixed-seed block-bootstrap null of the centered series, stepping down through
    the t-ordering and shrinking the active set; adjusted p-values are enforced
    monotone non-decreasing. Rejects ⇒ superior at FWER ``alpha``.
    """
    m = len(excess_matrix)
    if m == 0:
        return RomanoWolfResult((), (), (), (), 0)
    t_stats, means, stds, n_obs = _studentized(excess_matrix)
    if n_obs < 2:
        return RomanoWolfResult(
            tuple(t_stats), (1.0,) * m, (False,) * m, tuple(range(m)), n_obs
        )

    centered = [
        [x - means[k] for x in excess_matrix[k]] for k in range(m)
    ]
    rng = random.Random(seed)
    # Pre-draw bootstrap maxT contributions per candidate (studentized w/ orig std).
    boot_t: list[list[float]] = [[] for _ in range(m)]
    for _ in range(n_boot):
        idx = _stationary_bootstrap_indices(n_obs, rng, avg_block)
        for k in range(m):
            if stds[k] <= 0.0:
                boot_t[k].append(0.0)
                continue
            bmean = sum(centered[k][i] for i in idx) / n_obs
            boot_t[k].append(math.sqrt(n_obs) * bmean / stds[k])

    order = sorted(range(m), key=lambda k: t_stats[k], reverse=True)
    adjusted = [1.0] * m
    active = list(order)
    prev_p = 0.0
    for k in order:
        # null max over the still-active (not-yet-rejected) hypotheses.
        ge = 0
        for b in range(n_boot):
            max_null = max(boot_t[j][b] for j in active)
            if max_null >= t_stats[k]:
                ge += 1
        p = (ge + 1) / (n_boot + 1)
        p = max(p, prev_p)  # enforce monotonicity along the ordering
        adjusted[k] = p
        prev_p = p
        if p <= alpha and active and active[0] == k:
            active.pop(0)
        # once a hypothesis is not rejected, the active set stops shrinking;
        # remaining (smaller-t) hypotheses inherit a p ≥ this one.
    rejected = tuple(adjusted[k] <= alpha for k in range(m))
    return RomanoWolfResult(
        tuple(t_stats), tuple(adjusted), rejected, tuple(order), n_obs
    )


# ---------------------------------------------------------------------------
# FDR (Benjamini-Hochberg / Benjamini-Yekutieli)
# ---------------------------------------------------------------------------


def bh_fdr(pvalues: Sequence[float], *, q: float = 0.1) -> tuple[bool, ...]:
    """Benjamini-Hochberg step-up FDR — rejected mask (independent/PRDS)."""
    return _fdr(pvalues, q=q, by=False)


def by_fdr(pvalues: Sequence[float], *, q: float = 0.1) -> tuple[bool, ...]:
    """Benjamini-Yekutieli step-up FDR — valid under arbitrary dependence."""
    return _fdr(pvalues, q=q, by=True)


def _fdr(pvalues: Sequence[float], *, q: float, by: bool) -> tuple[bool, ...]:
    m = len(pvalues)
    if m == 0:
        return ()
    c = sum(1.0 / j for j in range(1, m + 1)) if by else 1.0
    order = sorted(range(m), key=lambda i: pvalues[i])
    max_rank = 0
    for rank, i in enumerate(order, start=1):
        if pvalues[i] <= (rank / m) * q / c:
            max_rank = rank
    rejected = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= max_rank:
            rejected[i] = True
    return tuple(rejected)


# ---------------------------------------------------------------------------
# Individual bootstrap p-values + the tying harness
# ---------------------------------------------------------------------------


def _individual_pvalues(
    excess_matrix: Sequence[Sequence[float]],
    *,
    n_boot: int,
    seed: int,
    avg_block: int,
) -> tuple[float, ...]:
    """Per-candidate one-sided block-bootstrap p-value (beats benchmark)."""
    t_stats, means, stds, n_obs = _studentized(excess_matrix)
    if n_obs < 2:
        return (1.0,) * len(excess_matrix)
    rng = random.Random(seed)
    centered = [[x - means[k] for x in excess_matrix[k]] for k in range(len(means))]
    ge = [0] * len(means)
    for _ in range(n_boot):
        idx = _stationary_bootstrap_indices(n_obs, rng, avg_block)
        for k in range(len(means)):
            if stds[k] <= 0.0:
                continue
            bmean = sum(centered[k][i] for i in idx) / n_obs
            if math.sqrt(n_obs) * bmean / stds[k] >= t_stats[k]:
                ge[k] += 1
    # A zero-variance excess (e.g. a candidate identical to the benchmark) cannot
    # be assessed by the studentized bootstrap — every draw is skipped, leaving
    # ge=0, which would otherwise return the MINIMUM p-value and be spuriously
    # FDR-rejected as superior. Fail closed to p=1.0 (no evidence of an edge).
    return tuple(
        1.0 if stds[k] <= 0.0 else (ge[k] + 1) / (n_boot + 1)
        for k in range(len(means))
    )


@dataclass(frozen=True)
class ComparisonReport:
    """Pre-declared family comparison across SPA + Romano-Wolf + BH/BY (immutable)."""

    family: str
    labels: tuple[str, ...]
    n_candidates: int
    n_observations: int
    t_stats: tuple[float, ...]
    best_index: int
    best_label: str
    spa_p_value: float
    rw_adjusted_pvalues: tuple[float, ...]
    rw_rejected: tuple[bool, ...]
    individual_pvalues: tuple[float, ...]
    bh_rejected: tuple[bool, ...]
    by_rejected: tuple[bool, ...]


def compare_strategies(
    *,
    candidate_returns: Sequence[Sequence[float]],
    benchmark_returns: Sequence[float],
    labels: Sequence[str],
    family: str,
    alpha: float = 0.05,
    fdr_q: float = 0.1,
    n_boot: int = _BOOT_RESAMPLES,
    seed: int = _BOOT_SEED,
    avg_block: int = _AVG_BLOCK_LENGTH,
) -> ComparisonReport:
    """Compare a pre-declared candidate family vs a benchmark (all three lenses)."""
    m = len(candidate_returns)
    if len(labels) != m:
        raise ValueError("labels must match candidate_returns length")
    excess = [
        [c - b for c, b in zip(row, benchmark_returns, strict=True)]
        for row in candidate_returns
    ]
    t_stats, _, _, n_obs = _studentized(excess) if m else ([], [], [], 0)
    best_idx = max(range(m), key=lambda k: t_stats[k]) if m else -1
    spa = spa_disclosure(excess) if m else None
    rw = romano_wolf_stepdown(
        excess, alpha=alpha, n_boot=n_boot, seed=seed, avg_block=avg_block
    )
    individual = _individual_pvalues(
        excess, n_boot=n_boot, seed=seed, avg_block=avg_block
    )
    return ComparisonReport(
        family=family,
        labels=tuple(labels),
        n_candidates=m,
        n_observations=n_obs,
        t_stats=tuple(t_stats),
        best_index=best_idx,
        best_label=labels[best_idx] if best_idx >= 0 else "",
        spa_p_value=spa.p_value if spa is not None else 1.0,
        rw_adjusted_pvalues=rw.adjusted_pvalues,
        rw_rejected=rw.rejected,
        individual_pvalues=individual,
        bh_rejected=bh_fdr(individual, q=fdr_q),
        by_rejected=by_fdr(individual, q=fdr_q),
    )


__all__ = [
    "ComparisonReport",
    "RomanoWolfResult",
    "bh_fdr",
    "by_fdr",
    "compare_strategies",
    "romano_wolf_stepdown",
]
