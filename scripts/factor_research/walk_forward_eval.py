"""Anchored walk-forward + combinatorial purged-CV robustness (R2-4 / S4).

DEVELOPMENT robustness disclosure for a benchmark-relative config — NOT the
selector (the unique strategy is picked by ``round2_search`` on the inner
train/val split; the verdict is R2-6 forward). For a GIVEN (weights, constraint,
k, a_max) it answers "is the per-period excess stable across sub-windows, or a
one-regime artefact?":

* **anchored walk-forward** — expanding-window cumulative IR path: fold ``j``
  reports the IR over periods ``[0 .. end of block j]`` so a reader sees whether
  the edge accrues steadily or collapses after one window.
* **combinatorial purged CV (CPCV)** — partition the periods into ``n_groups``
  contiguous blocks and, for every ``C(n_groups, k)`` choice of held-out blocks,
  report the OOS IR over their union (dropping ``embargo`` periods at each block's
  leading edge so a 20-td forward label cannot straddle the boundary). The
  distribution (mean / min / fraction positive) across the combinations is the de
  Prado overfitting signal: an edge that is positive on most held-out
  combinations is far harder to have data-mined than one that needs the whole
  window.

The config is FIXED here (no per-fold re-fit), so the splits measure the excess
series' stability, not a re-search. Firewall (defence-in-depth): the panel dates
are re-checked through :meth:`LockedSplit.assert_all_not_test`; the caller must
pass a benchmark restricted to ``< test_start`` (see ``round2_search``). Pure +
deterministic; reuses the live evolution lane's purged splitter.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from .benchmark_relative import (
    BenchmarkRelativeResult,
    benchmark_relative_backtest,
)
from .exposure_constraints import DEFAULT_NONCONST_CAP
from .locked_split import LockedSplit

_PERIODS_PER_YEAR_BASE: int = 252
DEFAULT_N_FOLDS: int = 5
DEFAULT_N_GROUPS: int = 10
DEFAULT_CPCV_K: int = 2
# Embargo ≥ max forward-label horizon (20 td ≈ 4 rebalances at the 5-td cadence).
DEFAULT_EMBARGO: int = 4


@dataclass(frozen=True)
class FoldStat:
    """One fold/path's excess summary (immutable)."""

    label: str
    n_periods: int
    total_excess: float
    annual_excess: float
    information_ratio: float


@dataclass(frozen=True)
class WalkForwardReport:
    """Anchored expanding-window path + CPCV OOS distribution (immutable)."""

    n_periods: int
    anchored: tuple[FoldStat, ...]
    cpcv_paths: tuple[FoldStat, ...]
    cpcv_ir_mean: float
    cpcv_ir_min: float
    cpcv_ir_frac_positive: float


def _ir_stats(excess: Sequence[float], horizon: int) -> tuple[float, float, float]:
    """``(total_excess, annual_excess, information_ratio)`` of an excess series."""
    arr = np.asarray(list(excess), dtype=float)
    n = len(arr)
    if n == 0:
        return 0.0, 0.0, 0.0
    total = float(np.cumprod(1.0 + arr)[-1] - 1.0)
    ppy = _PERIODS_PER_YEAR_BASE / horizon
    annual = float((1.0 + total) ** (ppy / n) - 1.0) if total > -1.0 else -1.0
    std = float(arr.std(ddof=1)) if n > 1 else 0.0
    ir = float(arr.mean() / std * np.sqrt(ppy)) if std > 0 else 0.0
    return total, annual, ir


def anchored_walk_forward(
    excess: Sequence[float], *, n_folds: int = DEFAULT_N_FOLDS, horizon: int = 5
) -> list[FoldStat]:
    """Expanding-window cumulative IR path over ``n_folds`` sequential blocks.

    Fold ``j`` covers periods ``[0 .. end of block j]`` (cumulative), so the path
    shows whether the edge persists as the window grows. ``[]`` if too few
    periods to form the folds.
    """
    n = len(excess)
    if n < n_folds or n_folds < 1:
        return []
    bounds = _block_bounds(n, n_folds)
    out: list[FoldStat] = []
    for j, (_, end) in enumerate(bounds):
        seg = excess[:end]
        total, annual, ir = _ir_stats(seg, horizon)
        out.append(
            FoldStat(
                label=f"anchored[0:{end}]",
                n_periods=end,
                total_excess=total,
                annual_excess=annual,
                information_ratio=ir,
            )
        )
        _ = j
    return out


def combinatorial_purged_cv(
    excess: Sequence[float],
    *,
    n_groups: int = DEFAULT_N_GROUPS,
    k: int = DEFAULT_CPCV_K,
    embargo: int = DEFAULT_EMBARGO,
    horizon: int = 5,
) -> list[FoldStat]:
    """OOS IR for every ``C(n_groups, k)`` held-out-block combination.

    Partitions the periods into ``n_groups`` contiguous blocks; for each choice of
    ``k`` blocks as OOS, the test set is their union minus the first ``embargo``
    periods of each block (so a forward label cannot straddle the preceding block
    — purge/embargo). ``[]`` if too few periods.
    """
    n = len(excess)
    if n < n_groups or n_groups < 2 or not 1 <= k < n_groups:
        return []
    bounds = _block_bounds(n, n_groups)
    out: list[FoldStat] = []
    for combo in combinations(range(n_groups), k):
        idx: list[int] = []
        for b in combo:
            start, end = bounds[b]
            idx.extend(range(min(start + embargo, end), end))
        if not idx:
            continue
        seg = [excess[i] for i in idx]
        total, annual, ir = _ir_stats(seg, horizon)
        out.append(
            FoldStat(
                label="cpcv[" + ",".join(str(b) for b in combo) + "]",
                n_periods=len(idx),
                total_excess=total,
                annual_excess=annual,
                information_ratio=ir,
            )
        )
    return out


def _block_bounds(n: int, n_blocks: int) -> list[tuple[int, int]]:
    """Contiguous near-equal ``[start, end)`` block boundaries over ``n`` periods."""
    base = n // n_blocks
    sizes = [base + (1 if i < n % n_blocks else 0) for i in range(n_blocks)]
    bounds: list[tuple[int, int]] = []
    start = 0
    for size in sizes:
        bounds.append((start, start + size))
        start += size
    return bounds


def evaluate_walk_forward(
    panel: pd.DataFrame,
    bench_asof: Callable[[str], dict[str, float]],
    index_returns: Mapping[str, float],
    *,
    weights: Mapping[str, float],
    exposure_constraint: str = "unconstrained",
    k: float = 0.1,
    a_max: float = 0.02,
    nonconst_cap: float = DEFAULT_NONCONST_CAP,
    horizon: int = 5,
    n_folds: int = DEFAULT_N_FOLDS,
    n_groups: int = DEFAULT_N_GROUPS,
    cpcv_k: int = DEFAULT_CPCV_K,
    embargo: int = DEFAULT_EMBARGO,
    split: LockedSplit | None = None,
) -> tuple[BenchmarkRelativeResult, WalkForwardReport]:
    """Backtest a config once, then report anchored-WF + CPCV robustness.

    ``split`` defaults to the on-disk locked split, used only to re-assert no
    panel date is in the sacred test window (defence-in-depth; the benchmark must
    already be restricted to ``< test_start`` by the caller).
    """
    if split is None:
        split = LockedSplit.load()
    split.assert_all_not_test(sorted(panel["date"].astype(str).unique()))  # firewall
    res = benchmark_relative_backtest(
        panel,
        bench_asof,
        index_returns,
        weights=weights,
        horizon=horizon,
        k=k,
        a_max=a_max,
        exposure_constraint=exposure_constraint,
        nonconst_cap=nonconst_cap,
    )
    report = build_report(
        res,
        horizon=horizon,
        n_folds=n_folds,
        n_groups=n_groups,
        cpcv_k=cpcv_k,
        embargo=embargo,
    )
    return res, report


def build_report(
    res: BenchmarkRelativeResult,
    *,
    horizon: int = 5,
    n_folds: int = DEFAULT_N_FOLDS,
    n_groups: int = DEFAULT_N_GROUPS,
    cpcv_k: int = DEFAULT_CPCV_K,
    embargo: int = DEFAULT_EMBARGO,
) -> WalkForwardReport:
    """Assemble the anchored + CPCV report from a backtest's excess series."""
    excess = res.excess_returns
    anchored = anchored_walk_forward(excess, n_folds=n_folds, horizon=horizon)
    cpcv = combinatorial_purged_cv(
        excess, n_groups=n_groups, k=cpcv_k, embargo=embargo, horizon=horizon
    )
    irs = [f.information_ratio for f in cpcv]
    return WalkForwardReport(
        n_periods=res.n_periods,
        anchored=tuple(anchored),
        cpcv_paths=tuple(cpcv),
        cpcv_ir_mean=float(np.mean(irs)) if irs else 0.0,
        cpcv_ir_min=float(np.min(irs)) if irs else 0.0,
        cpcv_ir_frac_positive=(
            float(np.mean([1.0 if x > 0 else 0.0 for x in irs])) if irs else 0.0
        ),
    )


__all__ = [
    "DEFAULT_CPCV_K",
    "DEFAULT_EMBARGO",
    "DEFAULT_N_FOLDS",
    "DEFAULT_N_GROUPS",
    "FoldStat",
    "WalkForwardReport",
    "anchored_walk_forward",
    "build_report",
    "combinatorial_purged_cv",
    "evaluate_walk_forward",
]
