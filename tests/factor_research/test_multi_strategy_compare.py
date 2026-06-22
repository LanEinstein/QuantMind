"""Tests for the multi-strategy fair-comparison harness (QGR-2 build-new ③)."""

from __future__ import annotations

import math

from scripts.factor_research.multi_strategy_compare import (
    bh_fdr,
    by_fdr,
    compare_strategies,
    romano_wolf_stepdown,
)

# Deterministic series with REALISTIC variance (pure alternating series have
# near-zero variance → tiny drifts look hugely significant). The benchmark and
# the null candidates carry noisy, mean-≈0 excess; only the winner has a real edge.
_N = 80
_BENCH = [0.012 * math.sin(i) for i in range(_N)]
_WINNER = [_BENCH[i] + 0.010 + 0.005 * math.cos(i) for i in range(_N)]  # +1% mean edge
_NULL_A = [_BENCH[i] + 0.004 * math.cos(i + 1.0) for i in range(_N)]  # mean ≈ 0 excess
_NULL_B = [_BENCH[i] - 0.003 * math.sin(i + 2.0) for i in range(_N)]  # mean ≈ 0 excess


def _excess(cand: list[float]) -> list[float]:
    return [c - b for c, b in zip(cand, _BENCH, strict=True)]


def test_bh_rejects_subset_and_by_is_more_conservative() -> None:
    pvals = (0.001, 0.02, 0.2, 0.6)
    bh = bh_fdr(pvals, q=0.1)
    by = by_fdr(pvals, q=0.1)
    assert bh[0] is True  # the strongest survives BH
    # BY controls FDR under dependence → rejects a subset of BH.
    assert sum(by) <= sum(bh)


def test_bh_fdr_rejects_none_when_all_large() -> None:
    assert bh_fdr((0.4, 0.5, 0.9), q=0.1) == (False, False, False)


def test_romano_wolf_rejects_the_real_winner_only() -> None:
    excess = [_excess(_WINNER), _excess(_NULL_A), _excess(_NULL_B)]
    res = romano_wolf_stepdown(excess, alpha=0.05)
    assert res.rejected[0] is True  # the winner beats the benchmark
    assert res.rejected[1] is False
    assert res.rejected[2] is False
    # adjusted p-values are monotone non-decreasing along the t-ordering.
    ordered = [res.adjusted_pvalues[i] for i in res.order]
    assert ordered == sorted(ordered)


def test_romano_wolf_rejects_nothing_under_the_null() -> None:
    excess = [_excess(_NULL_A), _excess(_NULL_B)]
    res = romano_wolf_stepdown(excess, alpha=0.05)
    assert not any(res.rejected)


def test_compare_strategies_ties_spa_rw_fdr_together() -> None:
    rep = compare_strategies(
        candidate_returns=[_WINNER, _NULL_A, _NULL_B],
        benchmark_returns=_BENCH,
        labels=("winner", "null_a", "null_b"),
        family="qgr.demo",
        alpha=0.05,
        fdr_q=0.1,
    )
    assert rep.family == "qgr.demo"
    assert rep.n_candidates == 3
    assert rep.best_label == "winner"
    assert rep.spa_p_value <= 0.10  # SPA finds a genuine superior model
    assert rep.rw_rejected[0] is True
    assert rep.bh_rejected[0] is True
    # the two nulls are not declared superior by any procedure.
    assert rep.rw_rejected[1:] == (False, False)


def test_zero_excess_candidate_not_fdr_rejected() -> None:
    # codex P2: a candidate identical to the benchmark has zero excess variance;
    # the bootstrap skips it → it must fail closed to p=1.0, NEVER be FDR-rejected
    # as "superior".
    rep = compare_strategies(
        candidate_returns=[_WINNER, list(_BENCH)],  # second ≡ benchmark
        benchmark_returns=_BENCH,
        labels=("winner", "is_benchmark"),
        family="qgr.zero",
        alpha=0.05,
        fdr_q=0.1,
    )
    assert rep.individual_pvalues[1] == 1.0
    assert rep.bh_rejected[1] is False
    assert rep.by_rejected[1] is False
    assert rep.rw_rejected[1] is False


def test_compare_strategies_is_deterministic() -> None:
    kwargs = dict(
        candidate_returns=[_WINNER, _NULL_A, _NULL_B],
        benchmark_returns=_BENCH,
        labels=("winner", "null_a", "null_b"),
        family="qgr.demo",
        alpha=0.05,
        fdr_q=0.1,
    )
    a = compare_strategies(**kwargs)
    b = compare_strategies(**kwargs)
    assert a.rw_adjusted_pvalues == b.rw_adjusted_pvalues
    assert a.spa_p_value == b.spa_p_value
