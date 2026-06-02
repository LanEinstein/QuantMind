"""W-001 PositionThesis model — validation + no-decision-field red line."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.models.position_thesis import (
    Comparator,
    InvalidationTemplate,
    PositionThesis,
    ThesisHealth,
    ThesisInvalidationCondition,
)

_NOW = datetime(2026, 6, 2, 9, 35, tzinfo=UTC)


def _cond() -> ThesisInvalidationCondition:
    return ThesisInvalidationCondition(
        template=InvalidationTemplate.ANCHOR_DRAWDOWN,
        metric_name="price",
        comparator=Comparator.LT,
        threshold=8.8,
        anchor=10.0,
        feature_code_version="position_thesis/v1",
    )


def _thesis(**overrides: object) -> PositionThesis:
    base: dict[str, object] = dict(
        instruction_id="QM-20260602-093500-600519-BUY-001",
        signal_id="SIG-20260601-line1",
        stock_code="600519",
        stock_name="贵州茅台",
        created_at=_NOW,
        trade_date="2026-06-02",
        pillars=("龙头护城河", "估值合理", "动量确认"),
        invalidation_conditions=(_cond(),),
        time_stop_trade_days=30,
        entry_price=10.0,
        entry_score=1.5,
        snapshot_id="snap-abc",
        feature_code_version="position_thesis/v1",
    )
    base.update(overrides)
    return PositionThesis(**base)


class TestValidation:
    @pytest.mark.unit
    def test_valid_thesis_round_trips_json(self) -> None:
        t = _thesis()
        again = PositionThesis.model_validate_json(t.model_dump_json())
        assert again == t

    @pytest.mark.unit
    def test_frozen(self) -> None:
        t = _thesis()
        with pytest.raises(ValidationError):
            t.entry_price = 99.0  # type: ignore[misc]

    @pytest.mark.unit
    @pytest.mark.parametrize("n", [0, 1, 2, 6])
    def test_pillar_count_bounds(self, n: int) -> None:
        with pytest.raises(ValidationError):
            _thesis(pillars=tuple(f"p{i}" for i in range(n)))

    @pytest.mark.unit
    def test_blank_pillar_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _thesis(pillars=("ok", "   ", "ok2"))

    @pytest.mark.unit
    def test_bad_instruction_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _thesis(instruction_id="not-an-id")

    @pytest.mark.unit
    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _thesis(side="BUY")

    @pytest.mark.unit
    def test_bad_evidence_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _thesis(evidence_ids=("BOGUS-123",))

    @pytest.mark.unit
    def test_known_evidence_prefix_accepted(self) -> None:
        t = _thesis(evidence_ids=("DEBATE-run1-r1", "MARKET-600519-x"))
        assert len(t.evidence_ids) == 2


class TestNoDecisionFields:
    """The thesis can never carry an order/decision field (P0-10 red line)."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "field", ["side", "volume", "limit_price", "risk_summary", "status"]
    )
    def test_decision_fields_absent(self, field: str) -> None:
        assert field not in PositionThesis.model_fields


class TestIsBroken:
    @pytest.mark.unit
    def test_lt_breaks_below_threshold(self) -> None:
        c = _cond()  # LT 8.8
        assert c.is_broken(8.0) is True
        assert c.is_broken(9.0) is False

    @pytest.mark.unit
    def test_gt_breaks_above_threshold(self) -> None:
        c = ThesisInvalidationCondition(
            template=InvalidationTemplate.TIME_STOP,
            metric_name="holding_trade_days",
            comparator=Comparator.GT,
            threshold=30.0,
            anchor=0.0,
            feature_code_version="position_thesis/v1",
        )
        assert c.is_broken(31.0) is True
        assert c.is_broken(30.0) is False

    @pytest.mark.unit
    def test_non_finite_is_not_broken(self) -> None:
        c = _cond()
        assert c.is_broken(float("nan")) is False
        assert c.is_broken(float("-inf")) is False

    @pytest.mark.unit
    def test_health_enum_values(self) -> None:
        assert {h.value for h in ThesisHealth} == {"intact", "weakening", "broken"}
