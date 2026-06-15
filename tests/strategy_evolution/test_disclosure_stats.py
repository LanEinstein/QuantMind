"""Tests for the AE-005 batch overfit disclosure statistics."""

from __future__ import annotations

import math

import pytest

from backend.strategy_evolution.disclosure_stats import (
    admit_batch,
    minimum_backtest_length,
    pbo_cscv,
    spa_disclosure,
)


class TestMinimumBacktestLength:
    def test_more_trials_need_longer_history(self) -> None:
        short = minimum_backtest_length(n_trials=2)
        longer = minimum_backtest_length(n_trials=200)
        assert longer > short

    def test_single_trial_floor(self) -> None:
        assert minimum_backtest_length(n_trials=1) == 2

    def test_higher_target_sharpe_needs_less_history(self) -> None:
        easy = minimum_backtest_length(n_trials=64, target_sharpe_annual=2.0)
        hard = minimum_backtest_length(n_trials=64, target_sharpe_annual=0.5)
        assert hard > easy

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(ValueError):
            minimum_backtest_length(n_trials=0)
        with pytest.raises(ValueError):
            minimum_backtest_length(n_trials=4, target_sharpe_annual=0.0)
        with pytest.raises(ValueError):
            minimum_backtest_length(n_trials=4, periods_per_year=0)

    def test_deterministic(self) -> None:
        assert minimum_backtest_length(n_trials=37) == minimum_backtest_length(
            n_trials=37
        )


class TestAdmitBatch:
    def test_long_history_admits(self) -> None:
        assert admit_batch(n_trials=16, n_observations=10_000) is True

    def test_short_history_rejects_whole_batch(self) -> None:
        # Many trials, only a few weeks of data — cannot earn significance.
        assert admit_batch(n_trials=256, n_observations=20) is False

    def test_degenerate_observation_count_rejects(self) -> None:
        assert admit_batch(n_trials=4, n_observations=1) is False
        assert admit_batch(n_trials=4, n_observations=0) is False

    def test_boundary_is_inclusive(self) -> None:
        need = minimum_backtest_length(n_trials=16)
        assert admit_batch(n_trials=16, n_observations=need) is True
        assert admit_batch(n_trials=16, n_observations=need - 1) is False


def _rng_series(seed: int, n: int, mean: float, scale: float) -> list[float]:
    import random

    rng = random.Random(seed)
    return [mean + scale * (rng.random() - 0.5) for _ in range(n)]


class TestPBOcscv:
    def test_identical_noise_strategies_are_not_confidently_overfit(self) -> None:
        # All strategies are the same pure noise → IS winner is essentially a
        # coin flip OOS; PBO sits around 0.5, never a confident 0.
        rows = [_rng_series(s, 240, 0.0, 0.02) for s in range(8)]
        result = pbo_cscv(rows, n_splits=8)
        assert 0.0 <= result.pbo <= 1.0
        assert result.n_strategies == 8
        assert result.n_combinations > 0

    def test_pure_noise_field_overfits_more_than_a_genuine_edge(self) -> None:
        # A field of pure-noise strategies: the IS winner has no OOS
        # persistence, so PBO is high. A field where ONE candidate carries a
        # true persistent edge: that candidate wins IS *and* OOS, so PBO is
        # low. The disclosure must rank these correctly.
        n = 240
        noise_field = [_rng_series(s, n, 0.0, 0.02) for s in range(8)]
        edge = [v + 0.012 for v in _rng_series(99, n, 0.0, 0.02)]
        edge_field = [edge, *[_rng_series(s, n, 0.0, 0.02) for s in range(7)]]

        noise_pbo = pbo_cscv(noise_field, n_splits=8).pbo
        edge_pbo = pbo_cscv(edge_field, n_splits=8).pbo
        assert edge_pbo < noise_pbo
        assert edge_pbo < 0.5

    def test_deterministic(self) -> None:
        rows = [_rng_series(s, 120, 0.001, 0.02) for s in range(5)]
        assert pbo_cscv(rows, n_splits=6) == pbo_cscv(rows, n_splits=6)

    def test_fail_closed_too_few_strategies(self) -> None:
        assert pbo_cscv([[0.1, 0.2, 0.3]], n_splits=2).pbo == 1.0

    def test_fail_closed_too_few_periods(self) -> None:
        rows = [[0.1, 0.2], [0.2, 0.1]]
        assert pbo_cscv(rows, n_splits=10).pbo == 1.0

    def test_unequal_rows_raise(self) -> None:
        with pytest.raises(ValueError):
            pbo_cscv([[0.1, 0.2, 0.3], [0.1, 0.2]], n_splits=2)

    def test_odd_splits_fail_closed(self) -> None:
        rows = [_rng_series(s, 90, 0.0, 0.01) for s in range(4)]
        assert pbo_cscv(rows, n_splits=5).pbo == 1.0


class TestSPADisclosure:
    def test_clear_winner_has_low_pvalue(self) -> None:
        # One candidate with a strong positive excess vs the incumbent (real
        # series carry variance — a studentized statistic needs it).
        n = 250
        winner = [v + 0.012 for v in _rng_series(7, n, 0.0, 0.01)]
        noise = [_rng_series(s, n, 0.0, 0.02) for s in range(4)]
        result = spa_disclosure([winner, *noise])
        assert result.p_value < 0.05
        assert result.best_candidate_index == 0
        assert result.spa_statistic > 0.0

    def test_pure_noise_excess_is_not_significant(self) -> None:
        rows = [_rng_series(s, 250, 0.0, 0.02) for s in range(6)]
        result = spa_disclosure(rows)
        assert result.p_value > 0.10

    def test_deterministic_fixed_seed(self) -> None:
        rows = [_rng_series(s, 200, 0.0005, 0.02) for s in range(5)]
        first = spa_disclosure(rows)
        second = spa_disclosure(rows)
        assert first.p_value == second.p_value
        assert first.spa_statistic == second.spa_statistic

    def test_empty_fail_closed(self) -> None:
        result = spa_disclosure([])
        assert result.p_value == 1.0
        assert result.best_candidate_index == -1

    def test_single_observation_fail_closed(self) -> None:
        result = spa_disclosure([[0.5], [0.3]])
        assert result.p_value == 1.0

    def test_unequal_rows_raise(self) -> None:
        with pytest.raises(ValueError):
            spa_disclosure([[0.1, 0.2, 0.3], [0.1, 0.2]])

    def test_inferior_candidate_does_not_inflate_significance(self) -> None:
        # A strongly negative (inferior) candidate must be recentered out and
        # must not lower the p-value of the (flat) field.
        n = 250
        inferior = [-0.02] * n
        flat = [[0.0] * n for _ in range(3)]
        result = spa_disclosure([inferior, *flat])
        assert result.p_value > 0.10
        assert not math.isnan(result.p_value)


__all__: list[str] = []
