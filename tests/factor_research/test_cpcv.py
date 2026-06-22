"""Tests for the generic true-CPCV path engine (QGR-2 build-new ②).

Pins the correctness gap the QGR plan calls out: the legacy
``walk_forward_eval.combinatorial_purged_cv`` reported the ``C(N, k)`` held-out
*combinations* and mislabelled them "paths". The de Prado path count is
``φ = (k/N)·C(N, k) = C(N-1, k-1)`` (N=6, k=2 → 15 combinations / 5 paths).
"""

from __future__ import annotations

import math

import pytest

from scripts.factor_research.cpcv import (
    QGR_CPCV_K,
    QGR_N_GROUPS,
    cpcv_blocks,
    cpcv_combinations,
    cpcv_path_assignments,
    n_cpcv_paths,
    run_cpcv_fixed_series,
)


def test_qgr_canonical_path_count() -> None:
    # The doc's worked example: N=6, k=2 → 15 combinations, 5 paths.
    assert QGR_N_GROUPS == 6
    assert QGR_CPCV_K == 2
    assert len(cpcv_combinations(QGR_N_GROUPS, QGR_CPCV_K)) == 15
    assert n_cpcv_paths(QGR_N_GROUPS, QGR_CPCV_K) == 5


def test_path_count_matches_de_prado_identity() -> None:
    # φ = (k/N)·C(N,k) = C(N-1, k-1) for a range of (N, k).
    for n in range(3, 12):
        for k in range(1, n):
            phi = n_cpcv_paths(n, k)
            assert phi == math.comb(n - 1, k - 1)
            assert phi * n == k * math.comb(n, k)


def test_combinations_are_lexicographic_ksubsets() -> None:
    combos = cpcv_combinations(5, 2)
    assert combos[0] == (0, 1)
    assert combos[-1] == (3, 4)
    assert len(combos) == math.comb(5, 2)
    assert all(len(c) == 2 for c in combos)
    assert combos == tuple(sorted(combos))


def test_blocks_partition_periods_contiguously() -> None:
    blocks = cpcv_blocks(20, 6)
    assert len(blocks) == 6
    # contiguous, non-overlapping, covers [0, 20)
    assert blocks[0].start == 0
    assert blocks[-1].end == 20
    for a, b in zip(blocks, blocks[1:], strict=False):
        assert a.end == b.start
    sizes = [b.end - b.start for b in blocks]
    assert max(sizes) - min(sizes) <= 1  # near-equal


def test_path_assignments_cover_each_group_once_per_path() -> None:
    n_groups, k = QGR_N_GROUPS, QGR_CPCV_K
    combos = cpcv_combinations(n_groups, k)
    paths = cpcv_path_assignments(n_groups, k)
    assert len(paths) == n_cpcv_paths(n_groups, k)
    for path in paths:
        # one combo-index assigned per group; the assigned combo must actually
        # hold that group as a test group.
        assert len(path) == n_groups
        for group, combo_id in enumerate(path):
            assert group in combos[combo_id]


def test_path_assignments_use_every_test_occurrence_exactly_once() -> None:
    # Across the φ paths, group g's φ test-occurrences are each used once.
    n_groups, k = QGR_N_GROUPS, QGR_CPCV_K
    combos = cpcv_combinations(n_groups, k)
    paths = cpcv_path_assignments(n_groups, k)
    for group in range(n_groups):
        test_combos = [i for i, c in enumerate(combos) if group in c]
        used = sorted(path[group] for path in paths)
        assert used == sorted(test_combos)


def test_run_cpcv_fixed_series_counts_and_selfcheck() -> None:
    series = [0.01 + 0.002 * (i % 3) for i in range(60)]
    rep = run_cpcv_fixed_series(
        series, n_groups=6, k=2, embargo=0, horizon=5
    )
    assert rep.n_combinations == 15
    assert rep.n_paths == 5
    assert rep.path_count_verified is True
    assert len(rep.combinations) == 15
    assert len(rep.paths) == 5


def test_fixed_series_paths_are_degenerate_full_length() -> None:
    # A FIXED (non-refit) series has zero path dispersion by construction: each
    # path stitches the same per-group OOS slices → identical full-length series.
    series = [0.01 + 0.002 * (i % 3) for i in range(60)]
    rep = run_cpcv_fixed_series(series, n_groups=6, k=2, embargo=0, horizon=5)
    # every path spans all periods and they all share one total return.
    assert all(p.n_periods == 60 for p in rep.paths)
    totals = {round(p.total_return, 12) for p in rep.paths}
    assert len(totals) == 1


def test_embargo_drops_leading_period_of_each_test_block() -> None:
    series = [0.01] * 60
    rep = run_cpcv_fixed_series(series, n_groups=6, k=2, embargo=1, horizon=5)
    # 6 blocks of 10; each test block loses its leading period → 9/block,
    # 18 OOS periods per 2-block combination.
    assert all(c.n_periods == 18 for c in rep.combinations)


def test_combination_distribution_straddles_zero_on_alternating_signs() -> None:
    series = [0.02 if i % 2 == 0 else -0.02 for i in range(60)]
    rep = run_cpcv_fixed_series(series, n_groups=6, k=2, embargo=0, horizon=5)
    sharpes = [c.sharpe for c in rep.combinations]
    assert any(s <= 0 for s in sharpes) or rep.combo_sharpe_frac_positive < 1.0


def test_drawdown_measured_from_starting_capital() -> None:
    # codex P2: a path/combination opening with losses must count the drop from
    # the initial 1.0, not from its first post-loss equity. [-10%]*4 → 1-0.9^4.
    rep = run_cpcv_fixed_series([-0.1] * 12, n_groups=6, k=2, embargo=0, horizon=5)
    # combo over groups (0,1) = the first four all-loss periods.
    assert rep.combinations[0].max_drawdown == pytest.approx(1.0 - 0.9**4)


def test_too_few_periods_fail_closed() -> None:
    rep = run_cpcv_fixed_series([0.01] * 3, n_groups=6, k=2, embargo=0, horizon=5)
    assert rep.n_combinations == 0
    assert rep.combinations == ()
    assert rep.paths == ()


def test_embargo_exhausted_blocks_fail_closed() -> None:
    # codex P2: n=6, n_groups=6 → block size 1; embargo=1 drops every block's only
    # period → no OOS. The report must be empty, NOT a "verified" zero-period run.
    rep = run_cpcv_fixed_series([0.01] * 6, n_groups=6, k=2, embargo=1, horizon=5)
    assert rep.n_combinations == 0
    assert rep.combinations == ()
    assert rep.paths == ()
    assert rep.path_count_verified is False


def test_invalid_k_raises() -> None:
    with pytest.raises(ValueError):
        n_cpcv_paths(6, 0)
    with pytest.raises(ValueError):
        n_cpcv_paths(6, 6)
