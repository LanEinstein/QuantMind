"""Generic true Combinatorial Purged Cross-Validation path engine (QGR-2 ②).

The reusable arena needs a *correct* CPCV — the legacy
:func:`walk_forward_eval.combinatorial_purged_cv` reports the ``C(N, k)``
held-out **combinations** and mislabels them "paths". de Prado's actual method
assembles ``φ = (k/N)·C(N, k) = C(N-1, k-1)`` complete-length OOS **paths**
(N=6, k=2 → 15 combinations / 5 paths; QGR plan §4.1 / §10).

This module is **strategy-agnostic and decoupled** from the deprecated
``benchmark_relative`` enhanced-index path (which ``walk_forward_eval`` is wired
to). It exposes two layers:

* **pure combinatorics** (no data) — :func:`cpcv_combinations`,
  :func:`cpcv_path_assignments`, :func:`n_cpcv_paths`. The path assignment
  follows the standard construction: each group ``g`` is a test group in
  exactly ``φ`` combinations; path ``p`` assigns group ``g`` its ``p``-th
  test-occurrence, so the φ paths each traverse all N groups and, across the φ
  paths, every (group, test-combination) pairing is used exactly once.
* **applied to a fixed per-period series** — :func:`run_cpcv_fixed_series`
  reports the per-combination OOS distribution (the real overfitting signal for
  a *fixed* config) **and** the φ stitched full-length paths.

HONEST NOTE (baked into the report): a *fixed, non-refit* return series has
**zero path dispersion by construction** — each path stitches the same per-group
OOS slices, so all φ paths share one full-length series. The path machinery is
not idle, though: QGR-4 drives it with a selection procedure (IS-best chosen on
each combination's complement) whose OOS values are combination-dependent, so
the φ paths genuinely diverge there. ``purge``/``embargo`` (≥ the label horizon)
keep a forward-horizon label from straddling a block boundary; an overlapping
path is **never** fed to DSR as independent samples (QGR plan §4.1).

Pure + deterministic; stdlib + numpy only. No ``backend`` import, no IO, no RNG.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np

# Canonical QGR CPCV configuration (frozen here; the arena freeze pins these).
QGR_N_GROUPS: int = 6
QGR_CPCV_K: int = 2
_PERIODS_PER_YEAR_BASE: int = 252


@dataclass(frozen=True)
class CpcvBlock:
    """One contiguous group of period indices ``[start, end)`` (immutable)."""

    group: int
    start: int
    end: int


@dataclass(frozen=True)
class PathStat:
    """One combination's or path's OOS summary (immutable)."""

    label: str
    n_periods: int
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float


@dataclass(frozen=True)
class CpcvReport:
    """Per-combination OOS distribution + φ stitched paths (immutable)."""

    n_groups: int
    k: int
    n_combinations: int
    n_paths: int
    path_count_verified: bool
    combinations: tuple[PathStat, ...]
    paths: tuple[PathStat, ...]
    combo_sharpe_mean: float
    combo_sharpe_min: float
    combo_sharpe_frac_positive: float
    combo_return_mean: float
    combo_return_frac_positive: float


# ---------------------------------------------------------------------------
# Pure combinatorics (no data)
# ---------------------------------------------------------------------------


def n_cpcv_paths(n_groups: int, k: int) -> int:
    """de Prado path count ``φ = (k/N)·C(N, k) = C(N-1, k-1)``.

    Raises:
        ValueError: ``k`` outside ``1 <= k < n_groups`` or ``n_groups < 2``.
    """
    if n_groups < 2:
        raise ValueError("n_groups must be >= 2")
    if not 1 <= k < n_groups:
        raise ValueError("k must satisfy 1 <= k < n_groups")
    return math.comb(n_groups - 1, k - 1)


def cpcv_combinations(n_groups: int, k: int) -> tuple[tuple[int, ...], ...]:
    """All ``k``-subsets of ``range(n_groups)`` (lexicographic, deterministic)."""
    if n_groups < 2 or not 1 <= k < n_groups:
        raise ValueError("need n_groups >= 2 and 1 <= k < n_groups")
    return tuple(combinations(range(n_groups), k))


def cpcv_path_assignments(n_groups: int, k: int) -> tuple[tuple[int, ...], ...]:
    """The φ paths as ``(combo_id per group)`` tuples.

    For each group ``g`` collect, in combination order, the ids of the
    combinations in which ``g`` is a test group (there are exactly ``φ`` of
    them). Path ``p`` assigns group ``g`` its ``p``-th such occurrence — so each
    path covers all ``N`` groups and, across the φ paths, group ``g``'s φ
    test-occurrences are each used exactly once (standard de Prado construction,
    as in the mlfinlab reference; re-derived, no code copied).
    """
    combos = cpcv_combinations(n_groups, k)
    phi = n_cpcv_paths(n_groups, k)
    occurrences: list[list[int]] = [
        [cid for cid, combo in enumerate(combos) if g in combo]
        for g in range(n_groups)
    ]
    paths: list[tuple[int, ...]] = []
    for p in range(phi):
        paths.append(tuple(occurrences[g][p] for g in range(n_groups)))
    return tuple(paths)


def cpcv_blocks(n_periods: int, n_groups: int) -> tuple[CpcvBlock, ...]:
    """Contiguous near-equal ``[start, end)`` blocks over ``n_periods``."""
    if n_groups < 2:
        raise ValueError("n_groups must be >= 2")
    base = n_periods // n_groups
    sizes = [base + (1 if i < n_periods % n_groups else 0) for i in range(n_groups)]
    blocks: list[CpcvBlock] = []
    start = 0
    for g, size in enumerate(sizes):
        blocks.append(CpcvBlock(group=g, start=start, end=start + size))
        start += size
    return tuple(blocks)


# ---------------------------------------------------------------------------
# Metric helpers (shared with the arena's primary metric conventions)
# ---------------------------------------------------------------------------


def _metric(returns: Sequence[float], label: str, horizon: int) -> PathStat:
    """Compounded total/annual return, annualised Sharpe, and max drawdown."""
    arr = np.asarray(list(returns), dtype=float)
    n = len(arr)
    if n == 0:
        return PathStat(label, 0, 0.0, 0.0, 0.0, 0.0)
    equity = np.cumprod(1.0 + arr)
    total = float(equity[-1] - 1.0)
    ppy = _PERIODS_PER_YEAR_BASE / horizon
    annual = float((1.0 + total) ** (ppy / n) - 1.0) if total > -1.0 else -1.0
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    sharpe = float(arr.mean() / std * math.sqrt(ppy)) if std > 0 else 0.0
    # Drawdown is measured from the starting capital (1.0) — prepend it so a path
    # that opens with a loss is not credited a peak at its first post-loss equity
    # (which would understate the true drawdown).
    curve = np.concatenate(([1.0], equity))
    peak = np.maximum.accumulate(curve)
    mdd = float((1.0 - curve / peak).max())
    return PathStat(label, n, total, annual, sharpe, mdd)


def _embargoed_indices(block: CpcvBlock, embargo: int) -> list[int]:
    """A test block's period indices after dropping its leading ``embargo``."""
    lead = min(block.start + max(0, embargo), block.end)
    return list(range(lead, block.end))


# ---------------------------------------------------------------------------
# Applied: fixed per-period series
# ---------------------------------------------------------------------------


def combination_oos_indices(
    blocks: Sequence[CpcvBlock], test_groups: Sequence[int], embargo: int
) -> list[int]:
    """Time-ordered OOS period indices for a combination (purged/embargoed)."""
    idx: list[int] = []
    for g in sorted(test_groups):
        idx.extend(_embargoed_indices(blocks[g], embargo))
    return idx


def stitch_paths(
    *,
    blocks: Sequence[CpcvBlock],
    combos: Sequence[tuple[int, ...]],
    assignments: Sequence[tuple[int, ...]],
    oos_values: Callable[[tuple[int, ...]], Mapping[int, float]],
    embargo: int,
) -> list[list[float]]:
    """Assemble the φ stitched, time-ordered OOS value series.

    ``oos_values(test_groups)`` returns ``{period_index: value}`` for a
    combination's OOS set — for a fixed series this is just the embargoed slice
    of those blocks; QGR-4 returns the *selection procedure's* OOS values, which
    are combination-dependent (so the paths genuinely diverge). Path ``p`` pulls
    each group ``g``'s slice from the combination ``assignments[p][g]``.
    """
    n_groups = len(blocks)
    series: list[list[float]] = []
    for path in assignments:
        ordered: list[float] = []
        for g in range(n_groups):
            combo_id = path[g]
            values = oos_values(combos[combo_id])
            for i in _embargoed_indices(blocks[g], embargo):
                if i in values:
                    ordered.append(values[i])
        series.append(ordered)
    return series


def run_cpcv_fixed_series(
    series: Sequence[float],
    *,
    n_groups: int = QGR_N_GROUPS,
    k: int = QGR_CPCV_K,
    embargo: int = 0,
    horizon: int = 5,
) -> CpcvReport:
    """True CPCV over a FIXED per-period return series.

    Reports the per-combination OOS distribution (the genuine overfitting signal
    for a fixed config) and the φ stitched full-length paths. Fail-closed: fewer
    periods than groups, or an invalid ``k``, returns an empty report.
    """
    n = len(series)
    try:
        combos = cpcv_combinations(n_groups, k)
        phi = n_cpcv_paths(n_groups, k)
    except ValueError:
        return _empty_report(n_groups, k)
    if n < n_groups:
        return _empty_report(n_groups, k)

    arr = list(series)
    blocks = cpcv_blocks(n, n_groups)

    def _fixed_oos(test_groups: tuple[int, ...]) -> dict[int, float]:
        return {
            i: arr[i]
            for g in test_groups
            for i in _embargoed_indices(blocks[g], embargo)
        }

    combo_stats: list[PathStat] = []
    for cid, test_groups in enumerate(combos):
        idx = combination_oos_indices(blocks, test_groups, embargo)
        combo_stats.append(
            _metric(
                [arr[i] for i in idx],
                f"combo[{','.join(map(str, test_groups))}]",
                horizon,
            )
        )
    # Fail closed if embargo exhausted every block's OOS (block size ≤ embargo on
    # a short history): an all-zero-period report must NOT masquerade as a
    # verified CPCV run downstream.
    if all(c.n_periods == 0 for c in combo_stats):
        return _empty_report(n_groups, k)

    assignments = cpcv_path_assignments(n_groups, k)
    path_series = stitch_paths(
        blocks=blocks,
        combos=combos,
        assignments=assignments,
        oos_values=_fixed_oos,
        embargo=embargo,
    )
    path_stats = [
        _metric(s, f"path[{p}]", horizon) for p, s in enumerate(path_series)
    ]

    sharpes = [c.sharpe for c in combo_stats]
    returns = [c.total_return for c in combo_stats]
    verified = len(path_stats) == phi and phi * n_groups == k * len(combos)
    return CpcvReport(
        n_groups=n_groups,
        k=k,
        n_combinations=len(combo_stats),
        n_paths=len(path_stats),
        path_count_verified=verified,
        combinations=tuple(combo_stats),
        paths=tuple(path_stats),
        combo_sharpe_mean=float(np.mean(sharpes)) if sharpes else 0.0,
        combo_sharpe_min=float(np.min(sharpes)) if sharpes else 0.0,
        combo_sharpe_frac_positive=(
            float(np.mean([1.0 if s > 0 else 0.0 for s in sharpes]))
            if sharpes
            else 0.0
        ),
        combo_return_mean=float(np.mean(returns)) if returns else 0.0,
        combo_return_frac_positive=(
            float(np.mean([1.0 if r > 0 else 0.0 for r in returns]))
            if returns
            else 0.0
        ),
    )


def _empty_report(n_groups: int, k: int) -> CpcvReport:
    return CpcvReport(
        n_groups=n_groups,
        k=k,
        n_combinations=0,
        n_paths=0,
        path_count_verified=False,
        combinations=(),
        paths=(),
        combo_sharpe_mean=0.0,
        combo_sharpe_min=0.0,
        combo_sharpe_frac_positive=0.0,
        combo_return_mean=0.0,
        combo_return_frac_positive=0.0,
    )


__all__ = [
    "QGR_CPCV_K",
    "QGR_N_GROUPS",
    "CpcvBlock",
    "CpcvReport",
    "PathStat",
    "combination_oos_indices",
    "cpcv_blocks",
    "cpcv_combinations",
    "cpcv_path_assignments",
    "n_cpcv_paths",
    "run_cpcv_fixed_series",
    "stitch_paths",
]
