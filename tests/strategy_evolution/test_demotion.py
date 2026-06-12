"""AB-004 auto-demotion tests (relative baseline + cooldown)."""

from __future__ import annotations

import datetime as dt

from backend.strategy_evolution.demotion import (
    DEMOTION_OBSERVATION_DAYS,
    DEMOTION_RELATIVE_FLOOR_BPS,
    PROMOTION_COOLDOWN_DAYS,
    evaluate_demotion,
    is_in_promotion_cooldown,
)

NOW = dt.datetime(2026, 6, 12, 22, 0, tzinfo=dt.UTC)
HASH = "a" * 64
EQUITY = 100_000.0


def _decision(
    challenger: list[float],
    incumbent: list[float],
    **overrides: object,
):
    kwargs: dict[str, object] = {
        "family": "line2.drawdown_stop",
        "artifact_hash": HASH,
        "challenger_daily_pnl": challenger,
        "incumbent_counterfactual_daily_pnl": incumbent,
        "equity_base": EQUITY,
    }
    kwargs.update(overrides)
    return evaluate_demotion(**kwargs)  # type: ignore[arg-type]


class TestRelativeBaseline:
    def test_relative_underperformance_demotes(self) -> None:
        # Challenger loses ¥200/day vs the incumbent counterfactual:
        # 12 days × -200 = -2400 = -240bps <= -150bps floor.
        decision = _decision([-100.0] * 12, [100.0] * 12)
        assert decision.demote
        assert decision.cumulative_excess_bps <= (
            DEMOTION_RELATIVE_FLOOR_BPS
        )

    def test_bear_market_does_not_kill_better_loser(self) -> None:
        """Both lose money, challenger loses LESS — never demoted."""
        decision = _decision([-300.0] * 15, [-500.0] * 15)
        assert not decision.demote
        assert decision.cumulative_excess_bps > 0

    def test_observation_floor_blocks_early_verdict(self) -> None:
        days = DEMOTION_OBSERVATION_DAYS - 1
        decision = _decision([-500.0] * days, [500.0] * days)
        assert not decision.demote
        assert "observation days" in decision.detail

    def test_mild_underperformance_within_floor_survives(self) -> None:
        # -10bps total — inside the -150bps tolerance.
        decision = _decision([-5.0] * 20, [0.0] * 20)
        assert not decision.demote


class TestFailClosedInputs:
    def test_length_mismatch_no_verdict(self) -> None:
        decision = _decision([-500.0] * 12, [500.0] * 10)
        assert not decision.demote
        assert "mismatch" in decision.detail

    def test_invalid_equity_base_no_verdict(self) -> None:
        decision = _decision(
            [-500.0] * 12, [500.0] * 12, equity_base=0.0
        )
        assert not decision.demote
        assert "equity base" in decision.detail


class TestCooldown:
    def test_inside_cooldown(self) -> None:
        last = NOW - dt.timedelta(days=PROMOTION_COOLDOWN_DAYS - 1)
        assert is_in_promotion_cooldown(last_action_at=last, now=NOW)

    def test_outside_cooldown(self) -> None:
        last = NOW - dt.timedelta(days=PROMOTION_COOLDOWN_DAYS + 1)
        assert not is_in_promotion_cooldown(last_action_at=last, now=NOW)

    def test_no_history_means_no_cooldown(self) -> None:
        assert not is_in_promotion_cooldown(last_action_at=None, now=NOW)
