"""Tests for the Layer-B forward-confirmation skeleton (QGR-2 build-new ⑨)."""

from __future__ import annotations

import math

import pytest

from scripts.factor_research.forward_gate_test import (
    PreRegistration,
    evaluate_forward,
    non_overlapping_bets,
    obf_spend,
    pocock_spend,
)

_PREREG = PreRegistration(
    strategy_artifact_sha256="a" * 64,
    freeze_date="2026-06-21",
    bet_horizon_td=5,
    min_observations=20,
    target_observations=40,
    success_criteria={
        "net_total_return_min": 0.0,
        "max_drawdown_max": 0.08,
        "sharpe_min": 0.0,
    },
    overall_alpha=0.05,
)


def test_prereg_content_address_is_deterministic_and_sensitive() -> None:
    other = PreRegistration(
        strategy_artifact_sha256="b" * 64,
        freeze_date="2026-06-21",
        bet_horizon_td=5,
        min_observations=20,
        target_observations=40,
        success_criteria={"net_total_return_min": 0.0},
        overall_alpha=0.05,
    )
    assert _PREREG.content_address == _PREREG.content_address
    assert _PREREG.content_address != other.content_address


def test_non_overlapping_bets_counts_complete_horizons() -> None:
    assert non_overlapping_bets(47, 5) == 9
    assert non_overlapping_bets(4, 5) == 0  # not even one complete bet


def test_alpha_spending_is_monotone_and_caps_at_overall() -> None:
    assert obf_spend(0.05, 0.25) < obf_spend(0.05, 1.0)
    assert pocock_spend(0.05, 0.25) < pocock_spend(0.05, 1.0)
    assert math.isclose(pocock_spend(0.05, 1.0), 0.05, rel_tol=1e-9)
    assert obf_spend(0.05, 1.0) <= 0.05 + 1e-9
    # OBF spends LESS early than Pocock (conservative early peeks).
    assert obf_spend(0.05, 0.25) < pocock_spend(0.05, 0.25)


def test_accruing_when_below_min_observations_never_a_verdict() -> None:
    status = evaluate_forward(
        prereg=_PREREG,
        observations=8,
        metrics={"net_total_return": 0.10, "max_drawdown": 0.03, "sharpe": 1.5},
        look_index=1,
        total_looks=4,
    )
    assert status.status == "ACCRUING"
    assert status.passed is None
    assert status.criteria is None


def test_verdict_passes_when_all_criteria_and_significance_hold() -> None:
    status = evaluate_forward(
        prereg=_PREREG,
        observations=40,
        metrics={
            "net_total_return": 0.12,
            "max_drawdown": 0.05,
            "sharpe": 1.2,
            "pvalue": 0.001,  # well inside the spending budget
        },
        look_index=4,
        total_looks=4,
    )
    assert status.status == "VERDICT"
    assert status.passed is True
    assert status.criteria["net_total_return_min"] is True
    assert status.criteria["max_drawdown_max"] is True


def test_verdict_fails_on_drawdown_breach() -> None:
    status = evaluate_forward(
        prereg=_PREREG,
        observations=40,
        metrics={"net_total_return": 0.12, "max_drawdown": 0.15, "sharpe": 1.2},
        look_index=4,
        total_looks=4,
    )
    assert status.status == "VERDICT"
    assert status.passed is False
    assert status.criteria["max_drawdown_max"] is False


def test_verdict_fails_when_pvalue_exceeds_spending_budget() -> None:
    # criteria met but the interim significance does not clear the spent alpha.
    status = evaluate_forward(
        prereg=_PREREG,
        observations=20,  # info fraction 0.5 → small OBF budget
        metrics={
            "net_total_return": 0.02,
            "max_drawdown": 0.04,
            "sharpe": 0.3,
            "pvalue": 0.04,
        },
        look_index=2,
        total_looks=4,
    )
    assert status.status == "VERDICT"
    assert status.passed is False


def test_invalid_horizon_raises() -> None:
    with pytest.raises(ValueError):
        non_overlapping_bets(40, 0)


def test_unknown_spending_mode_rejected_at_preregistration() -> None:
    # codex P2: the spending schedule is part of the frozen, hashed prereg — a
    # typo'd mode is rejected at construction, never silently looser at eval time.
    with pytest.raises(ValueError):
        PreRegistration(
            strategy_artifact_sha256="a" * 64,
            freeze_date="2026-06-21",
            bet_horizon_td=5,
            min_observations=20,
            target_observations=40,
            overall_alpha=0.05,
            success_criteria={"net_total_return_min": 0.0},
            spending="obrien",
        )


def test_spending_schedule_is_part_of_content_address() -> None:
    base = PreRegistration(
        strategy_artifact_sha256="a" * 64, freeze_date="2026-06-21",
        bet_horizon_td=5, min_observations=20, target_observations=40,
        overall_alpha=0.05, success_criteria={"net_total_return_min": 0.0},
        spending="obf",
    )
    pocock = PreRegistration(
        strategy_artifact_sha256="a" * 64, freeze_date="2026-06-21",
        bet_horizon_td=5, min_observations=20, target_observations=40,
        overall_alpha=0.05, success_criteria={"net_total_return_min": 0.0},
        spending="pocock",
    )
    assert base.content_address != pocock.content_address
