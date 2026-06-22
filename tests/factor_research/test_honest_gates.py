"""Tests for the ONC effective-N + HAC SR-variance honest-gate refinements (④)."""

from __future__ import annotations

import math

import pytest

from scripts.factor_research.honest_gates import (
    deflated_sharpe_hac,
    hac_variance_inflation,
    newey_west_lrv,
    onc_effective_n,
)


def test_onc_collapses_perfectly_correlated_trials() -> None:
    # three identical series → one effective independent trial.
    base = [0.01, -0.02, 0.015, 0.0, -0.005] * 4
    matrix = [list(base), list(base), list(base)]
    assert onc_effective_n(matrix, corr_threshold=0.5) == 1


def test_onc_counts_independent_trials_separately() -> None:
    a = [0.01, -0.02, 0.015, 0.0, -0.005, 0.02] * 4
    b = [-0.01, 0.02, -0.015, 0.0, 0.005, -0.02] * 4  # anti-correlated to a
    c = [0.0, 0.01, -0.01, 0.02, -0.02, 0.005] * 4  # different shape
    # |corr| threshold: a,b are perfectly anti-correlated (|corr|=1 → same cluster);
    # c is distinct → 2 clusters.
    n = onc_effective_n([a, b, c], corr_threshold=0.9)
    assert 1 <= n <= 3


def test_onc_edge_cases() -> None:
    assert onc_effective_n([], corr_threshold=0.5) == 0
    assert onc_effective_n([[0.01, 0.02, 0.03]], corr_threshold=0.5) == 1


def test_onc_collapses_flat_zero_variance_trials() -> None:
    # codex P2: ≥2 flat (zero-variance) series → NaN correlations; they must
    # collapse to ONE degenerate cluster, not count separately.
    flats = [[0.0] * 12, [0.0] * 12, [0.0] * 12]
    assert onc_effective_n(flats, corr_threshold=0.5) == 1
    # one flat + one live, distinct shape → flat cluster + 1 live = 2.
    live = [0.01, -0.02, 0.015, 0.0, -0.005, 0.02] * 2
    assert onc_effective_n([[0.0] * 12, live], corr_threshold=0.5) == 2


def test_newey_west_lrv_inflates_under_positive_autocorrelation() -> None:
    # a strongly positively autocorrelated series → LRV > sample variance.
    series = [0.01 * (1 if (i // 5) % 2 == 0 else -1) for i in range(60)]
    lrv = newey_west_lrv(series, lag=4)
    var = sum((x - sum(series) / len(series)) ** 2 for x in series) / len(series)
    assert lrv > var


def test_hac_inflation_at_least_one() -> None:
    # white-ish noise → inflation close to (and never below) 1.0.
    series = [0.01 if i % 2 == 0 else -0.01 for i in range(60)]
    infl = hac_variance_inflation(series, lag=4)
    assert infl >= 1.0


def test_hac_dsr_is_not_above_iid_dsr() -> None:
    # overlapping/autocorrelated returns → HAC deflates at least as hard as IID.
    series = [0.02 if (i // 5) % 2 == 0 else 0.005 for i in range(120)]
    iid = deflated_sharpe_hac(series, n_trials=10, hac_lag=0)
    hac = deflated_sharpe_hac(series, n_trials=10, hac_lag=4)
    assert hac <= iid + 1e-9
    assert 0.0 <= hac <= 1.0


def test_hac_dsr_deflates_with_more_trials() -> None:
    series = [0.02 if i % 2 == 0 else 0.005 for i in range(120)]
    assert deflated_sharpe_hac(series, n_trials=10_000, hac_lag=4) <= (
        deflated_sharpe_hac(series, n_trials=5, hac_lag=4)
    )


def test_newey_west_invalid_lag() -> None:
    with pytest.raises(ValueError):
        newey_west_lrv([0.01, 0.02, 0.03], lag=-1)


def test_lrv_finite_on_constant_series() -> None:
    # zero variance → LRV 0.0, inflation 1.0 (fail-safe, no division blow-up).
    assert newey_west_lrv([0.01] * 20, lag=3) == 0.0
    assert math.isclose(hac_variance_inflation([0.01] * 20, lag=3), 1.0)
