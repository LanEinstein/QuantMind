"""W-001 evaluation — deterministic broken/intact/unevaluable rollup."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.models.position_thesis import ThesisHealth
from backend.position_thesis.derivation import build_position_thesis
from backend.position_thesis.evaluation import (
    ThesisObservation,
    evaluate_thesis_health,
)

_NOW = datetime(2026, 6, 2, 9, 35, tzinfo=UTC)


def _thesis(price: float = 10.0, score: float = 2.0):
    return build_position_thesis(
        instruction_id="QM-20260602-093500-600519-BUY-001",
        signal_id="SIG-1",
        stock_code="600519",
        stock_name="贵州茅台",
        created_at=_NOW,
        trade_date="2026-06-02",
        pillars=("a", "b", "c"),
        entry_price=price,
        entry_score=score,
        snapshot_id="snap-1",
    )


class TestRollup:
    @pytest.mark.unit
    def test_intact_when_all_conditions_hold(self) -> None:
        t = _thesis(price=10.0, score=2.0)
        res = evaluate_thesis_health(
            t, ThesisObservation(current_price=9.5, holding_trade_days=5,
                                 current_score=1.9)
        )
        assert res.health is ThesisHealth.INTACT
        assert res.broken == ()
        assert res.evaluated == 3

    @pytest.mark.unit
    def test_broken_on_anchor_drawdown(self) -> None:
        t = _thesis(price=10.0)  # anchor floor 8.8
        res = evaluate_thesis_health(
            t, ThesisObservation(current_price=8.0, holding_trade_days=1,
                                 current_score=2.0)
        )
        assert res.health is ThesisHealth.BROKEN
        assert any(c.metric_name == "price" for c in res.broken)

    @pytest.mark.unit
    def test_broken_on_time_stop(self) -> None:
        t = _thesis()  # time stop 30 td
        res = evaluate_thesis_health(
            t, ThesisObservation(current_price=10.0, holding_trade_days=31,
                                 current_score=2.0)
        )
        assert res.health is ThesisHealth.BROKEN
        assert any(c.metric_name == "holding_trade_days" for c in res.broken)

    @pytest.mark.unit
    def test_unavailable_metrics_are_skipped_not_broken(self) -> None:
        t = _thesis(price=10.0, score=2.0)
        # No price/score available intraday → only time stop evaluates.
        res = evaluate_thesis_health(
            t, ThesisObservation(current_price=None, holding_trade_days=5,
                                 current_score=None)
        )
        assert res.evaluated == 1
        assert res.health is ThesisHealth.INTACT

    @pytest.mark.unit
    def test_empty_observation_is_intact(self) -> None:
        t = _thesis()
        res = evaluate_thesis_health(t, ThesisObservation())
        assert res.evaluated == 0
        assert res.health is ThesisHealth.INTACT

    @pytest.mark.unit
    def test_deterministic(self) -> None:
        t = _thesis()
        obs = ThesisObservation(current_price=8.0, holding_trade_days=40,
                                current_score=0.1)
        assert evaluate_thesis_health(t, obs) == evaluate_thesis_health(t, obs)
