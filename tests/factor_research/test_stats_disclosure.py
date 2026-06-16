"""Tests for the honest multiple-testing disclosure wrapper."""

from __future__ import annotations

from scripts.factor_research.stats_disclosure import (
    DSR_FLOOR,
    deflated_sharpe,
    disclose,
)

# Deterministic per-period return series (no RNG -> stable assertions).
_GOOD = [0.005, 0.015] * 30  # mean 0.01, std ~0.005 -> high Sharpe
_BAD = [-0.015, -0.005] * 30  # negative mean -> Sharpe < 0


def test_good_strategy_passes_dsr_with_few_trials() -> None:
    dsr = deflated_sharpe(_GOOD, n_trials=5)
    assert dsr >= DSR_FLOOR


def test_losing_strategy_fails_dsr() -> None:
    assert deflated_sharpe(_BAD, n_trials=5) < DSR_FLOOR


def test_more_trials_lowers_dsr() -> None:
    # Deflation: the same Sharpe is less significant after more trials.
    assert deflated_sharpe(_GOOD, n_trials=10000) <= deflated_sharpe(_GOOD, n_trials=5)


def test_minbtl_rejects_history_too_short_for_trial_count() -> None:
    rep = disclose(
        selected_net_rets=_GOOD,
        candidate_return_matrix=[_GOOD, _BAD],
        incumbent_excess_matrix=[[a - b for a, b in zip(_GOOD, _BAD, strict=False)]],
        n_trials=100_000,
        n_observations=30,  # far too short for 100k trials
    )
    assert rep.minbtl_admits is False
    assert rep.min_backtest_length > rep.n_observations


def test_disclose_populates_all_fields() -> None:
    rep = disclose(
        selected_net_rets=_GOOD,
        candidate_return_matrix=[_GOOD, _BAD],
        incumbent_excess_matrix=[[a - b for a, b in zip(_GOOD, _BAD, strict=False)]],
        n_trials=9,
        n_observations=2509,
    )
    assert rep.n_periods == len(_GOOD)
    assert 0.0 <= rep.pbo <= 1.0
    assert 0.0 <= rep.spa_p_value <= 1.0
    assert rep.dsr_passes is True
    assert rep.minbtl_admits is True
