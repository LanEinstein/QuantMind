"""R-002 de Prado anti-overfit toolbox tests (pure math)."""

from __future__ import annotations

import pytest

from backend.strategy_evolution.anti_overfit import (
    DSR_CONFIDENCE_FLOOR,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    meets_anti_overfit_bar,
    probabilistic_sharpe_ratio,
    purged_kfold_splits,
)


class TestPurgedKfold:
    def test_folds_cover_all_samples_exactly_once(self) -> None:
        folds = purged_kfold_splits(100, n_splits=5)
        test_union = [i for f in folds for i in f.test_indices]
        assert sorted(test_union) == list(range(100))

    def test_purge_removes_pre_test_train_rows(self) -> None:
        folds = purged_kfold_splits(100, n_splits=5, purge=3)
        # Second fold tests [20, 40) — train must exclude 17..19.
        fold = folds[1]
        assert fold.test_indices[0] == 20
        for idx in (17, 18, 19):
            assert idx not in fold.train_indices
        assert 16 in fold.train_indices

    def test_embargo_removes_post_test_train_rows(self) -> None:
        folds = purged_kfold_splits(100, n_splits=5, embargo=4)
        fold = folds[1]  # tests [20, 40)
        for idx in (40, 41, 42, 43):
            assert idx not in fold.train_indices
        assert 44 in fold.train_indices

    def test_train_never_overlaps_test(self) -> None:
        for fold in purged_kfold_splits(53, n_splits=4, purge=2, embargo=2):
            assert not set(fold.train_indices) & set(fold.test_indices)

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(ValueError):
            purged_kfold_splits(0, n_splits=5)
        with pytest.raises(ValueError):
            purged_kfold_splits(10, n_splits=1)
        with pytest.raises(ValueError):
            purged_kfold_splits(3, n_splits=5)
        with pytest.raises(ValueError):
            purged_kfold_splits(10, n_splits=2, purge=-1)


class TestExpectedMaxSharpe:
    def test_single_trial_has_zero_benchmark(self) -> None:
        assert expected_max_sharpe(1, 0.5) == 0.0

    def test_monotone_in_trials(self) -> None:
        values = [
            expected_max_sharpe(n, 0.25) for n in (2, 10, 100, 1000)
        ]
        assert values == sorted(values)
        assert values[0] > 0.0

    def test_scales_with_variance(self) -> None:
        low = expected_max_sharpe(100, 0.01)
        high = expected_max_sharpe(100, 1.0)
        assert high == pytest.approx(low * 10.0, rel=1e-9)

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(ValueError):
            expected_max_sharpe(0, 0.5)
        with pytest.raises(ValueError):
            expected_max_sharpe(10, -0.1)


class TestProbabilisticSharpe:
    def test_sr_equal_to_benchmark_is_half(self) -> None:
        psr = probabilistic_sharpe_ratio(
            0.1, benchmark_sr=0.1, n_samples=252
        )
        assert psr == pytest.approx(0.5, abs=1e-9)

    def test_higher_sr_raises_confidence(self) -> None:
        low = probabilistic_sharpe_ratio(0.05, benchmark_sr=0.0, n_samples=252)
        high = probabilistic_sharpe_ratio(0.20, benchmark_sr=0.0, n_samples=252)
        assert high > low > 0.5

    def test_negative_skew_hurts_confidence(self) -> None:
        normal = probabilistic_sharpe_ratio(
            0.15, benchmark_sr=0.0, n_samples=252, skew=0.0
        )
        skewed = probabilistic_sharpe_ratio(
            0.15, benchmark_sr=0.0, n_samples=252, skew=-1.5
        )
        assert skewed < normal

    def test_pathological_moments_fail_closed(self) -> None:
        # Denominator ≤ 0 → zero confidence, never a crash.
        psr = probabilistic_sharpe_ratio(
            5.0, benchmark_sr=0.0, n_samples=252, skew=2.0, kurtosis=1.0
        )
        assert psr == 0.0

    def test_too_few_samples_raise(self) -> None:
        with pytest.raises(ValueError):
            probabilistic_sharpe_ratio(0.1, benchmark_sr=0.0, n_samples=1)


class TestDeflatedSharpe:
    def test_more_trials_deflate_confidence(self) -> None:
        kwargs = {
            "variance_of_sr": 0.04,
            "n_samples": 252,
        }
        few = deflated_sharpe_ratio(0.15, n_trials=2, **kwargs)
        many = deflated_sharpe_ratio(0.15, n_trials=500, **kwargs)
        assert many < few

    def test_genuine_edge_survives_modest_search(self) -> None:
        dsr = deflated_sharpe_ratio(
            0.30, n_trials=5, variance_of_sr=0.01, n_samples=500
        )
        assert meets_anti_overfit_bar(dsr)

    def test_marginal_sharpe_after_heavy_search_fails_bar(self) -> None:
        dsr = deflated_sharpe_ratio(
            0.08, n_trials=1000, variance_of_sr=0.04, n_samples=252
        )
        assert not meets_anti_overfit_bar(dsr)

    def test_bar_uses_locked_floor(self) -> None:
        assert meets_anti_overfit_bar(DSR_CONFIDENCE_FLOOR)
        assert not meets_anti_overfit_bar(DSR_CONFIDENCE_FLOOR - 1e-9)
