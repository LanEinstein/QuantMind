"""Tests for backend/models/instruction.py — InstructionPlan strict schema.

Locks:
- P0-3 §2 red lines 1, 2, 3, 4, 5, 6, 8, 11, 12
- P0-7 amendment: risk_summary length must be 14 (passed: bool | None)
- P0-8 §1.6.2: evidence_ids prefix set
- LLM red line: strict + extra='forbid' on every model
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.models.instruction import (
    DataSnapshot,
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    PositionSummary,
    RiskCheckSummary,
)

SH = ZoneInfo("Asia/Shanghai")


def _snapshot(snap_at: datetime) -> DataSnapshot:
    return DataSnapshot(
        snapshot_at=snap_at,
        quote_source="adata",
        quote_latency_ms=120,
        news_sources_by_domain={
            "finance": ("stock_news_em",),
            "global": ("stock_info_global_em",),
        },
        news_window_seconds=300,
        prev_close=1700.0,
        is_trading_day=True,
        is_trading_hours=True,
    )


def _position_summary() -> PositionSummary:
    return PositionSummary(
        pre_position_pct=0.05,
        post_position_pct=0.085,
        pre_total_position_pct=0.40,
        post_total_position_pct=0.43,
        pre_cash=500_000.0,
        post_cash=331_500.0,
    )


def _risk_summary_14(passed: bool = True) -> tuple[RiskCheckSummary, ...]:
    """Build a length-14 RiskCheckSummary tuple (P0-7 amendment).

    Indices 0-6 mirror P0-3 7-check; 7-13 mirror P0-7 expansion to 14.
    Phase D will populate 7-13 with real data; until then test code
    builds them as `passed=None` sentinels for forward-compatibility.
    """
    rule_names = (
        "code_validity",
        "price_reasonability",
        "volume_validity",
        "fund_sufficiency",
        "position_limit",
        "total_position_limit",
        "trading_time",
        "total_position_pct",
        "single_instruction_amount",
        "daily_new_instruction_count",
        "universe_whitelist",
        "limit_up_down_block",
        "daily_loss_halt",
        "consecutive_loss_halt",
    )
    return tuple(
        RiskCheckSummary(
            rule_name=name,
            passed=passed if idx < 7 else None,
            threshold=None,
            actual=None,
            message="",
        )
        for idx, name in enumerate(rule_names)
    )


def _make_plan(**overrides: object) -> InstructionPlan:
    created = datetime(2026, 5, 12, 9, 30, 1, tzinfo=SH)
    snap = created - timedelta(seconds=2)
    base = dict(
        instruction_id="QM-20260512-093001-600519-BUY-001",
        created_at=created,
        valid_until=created.replace(hour=14, minute=55, second=0, microsecond=0),
        trade_date="2026-05-12",
        stock_code="600519",
        stock_name="贵州茅台",
        side=InstructionSide.BUY,
        volume=100,
        limit_price=1680.0,
        data_snapshot=_snapshot(snap),
        evidence_ids=("NEWS-em-12345", "MARKET-600519-2026-05-12T09:30:00"),
        position_summary=_position_summary(),
        risk_summary=_risk_summary_14(),
        risk_validation_id="RV-abc123",
        signal_id="sig-xyz",
        analysis_record_id="run-abc",
        debate_round_count=2,
        invalidation_summary="跌破 1650 / 资讯反转",
        status=InstructionStatus.DRAFT,
        rejection_reason=None,
    )
    base.update(overrides)
    return InstructionPlan(**base)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------


class TestEnums:
    def test_instruction_side_values(self) -> None:
        assert {s.value for s in InstructionSide} == {"BUY", "SELL", "HOLD"}

    def test_instruction_status_values(self) -> None:
        assert {s.value for s in InstructionStatus} == {
            "DRAFT",
            "VALIDATED",
            "DISPATCHED",
            "FILLED",
            "EXPIRED",
            "REJECTED",
            "AMBIGUOUS",
        }


# -----------------------------------------------------------------------------
# DataSnapshot
# -----------------------------------------------------------------------------


class TestDataSnapshot:
    def test_valid(self) -> None:
        snap = _snapshot(datetime(2026, 5, 12, 9, 29, 59, tzinfo=SH))
        assert snap.is_trading_hours is True
        assert "finance" in snap.news_sources_by_domain

    def test_frozen(self) -> None:
        snap = _snapshot(datetime(2026, 5, 12, 9, 29, 59, tzinfo=SH))
        with pytest.raises(ValidationError):
            snap.prev_close = 0.0  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            DataSnapshot(  # type: ignore[call-arg]
                snapshot_at=datetime(2026, 5, 12, tzinfo=SH),
                quote_source="adata",
                news_sources_by_domain={},
                is_trading_day=True,
                is_trading_hours=True,
                surprise_field="x",
            )


# -----------------------------------------------------------------------------
# PositionSummary
# -----------------------------------------------------------------------------


class TestPositionSummary:
    def test_valid_range(self) -> None:
        ps = _position_summary()
        assert 0.0 <= ps.post_position_pct <= 1.0

    @pytest.mark.parametrize(
        "field,value",
        [
            ("pre_position_pct", -0.1),
            ("post_position_pct", 1.1),
            ("pre_total_position_pct", -0.01),
            ("post_total_position_pct", 1.5),
            ("pre_cash", -1.0),
            ("post_cash", -0.01),
        ],
    )
    def test_out_of_range_rejected(self, field: str, value: float) -> None:
        base = dict(
            pre_position_pct=0.05,
            post_position_pct=0.10,
            pre_total_position_pct=0.40,
            post_total_position_pct=0.50,
            pre_cash=500_000.0,
            post_cash=400_000.0,
        )
        base[field] = value
        with pytest.raises(ValidationError):
            PositionSummary(**base)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# RiskCheckSummary
# -----------------------------------------------------------------------------


class TestRiskCheckSummary:
    def test_passed_can_be_bool_or_none(self) -> None:
        # bool — 7-check populated
        a = RiskCheckSummary(rule_name="code_validity", passed=True)
        assert a.passed is True
        # None — Phase D 8-14 still deferred
        b = RiskCheckSummary(rule_name="daily_loss_halt", passed=None)
        assert b.passed is None

    def test_rule_name_required(self) -> None:
        with pytest.raises(ValidationError):
            RiskCheckSummary(passed=True)  # type: ignore[call-arg]


# -----------------------------------------------------------------------------
# InstructionPlan — id regex
# -----------------------------------------------------------------------------


class TestInstructionPlanId:
    @pytest.mark.parametrize(
        "iid,side,code,created_hms",
        [
            (
                "QM-20260512-093001-600519-BUY-001",
                InstructionSide.BUY,
                "600519",
                (9, 30, 1),
            ),
            (
                "QM-20260512-103015-000001-SELL-002",
                InstructionSide.SELL,
                "000001",
                (10, 30, 15),
            ),
            (
                "QM-20260512-141500-300750-HOLD-001",
                InstructionSide.HOLD,
                "300750",
                (14, 15, 0),
            ),
        ],
    )
    def test_valid_id(
        self,
        iid: str,
        side: InstructionSide,
        code: str,
        created_hms: tuple[int, int, int],
    ) -> None:
        h, m, s = created_hms
        created = datetime(2026, 5, 12, h, m, s, tzinfo=SH)
        overrides: dict[str, object] = {
            "instruction_id": iid,
            "side": side,
            "stock_code": code,
            "created_at": created,
            "valid_until": created.replace(
                hour=14, minute=55, second=0, microsecond=0
            ),
            "data_snapshot": _snapshot(created - timedelta(seconds=1)),
        }
        if side is InstructionSide.HOLD:
            overrides["volume"] = None
            overrides["limit_price"] = None
            overrides["position_summary"] = None
        plan = _make_plan(**overrides)
        assert plan.instruction_id == iid

    @pytest.mark.parametrize(
        "iid",
        [
            "qm-20260512-093001-600519-BUY-001",  # lowercase
            "QM-2026512-093001-600519-BUY-001",  # 7-digit date
            "QM-20260512-093001-60051-BUY-001",  # 5-digit code
            "QM-20260512-093001-600519-BAD-001",  # invalid side
            "QM-20260512-093001-600519-BUY-1000",  # seq too long
            "QM-20260512-093001-600519-BUY-01",  # seq too short
            "QM-20260512-093001-600519-BUY",  # missing seq
            "x" * 50,
        ],
    )
    def test_invalid_id(self, iid: str) -> None:
        with pytest.raises(ValidationError):
            _make_plan(instruction_id=iid)


# -----------------------------------------------------------------------------
# InstructionPlan — HOLD constraints
# -----------------------------------------------------------------------------


def _hold_overrides(**extra: object) -> dict[str, object]:
    created = datetime(2026, 5, 12, 14, 15, 0, tzinfo=SH)
    base: dict[str, object] = {
        "instruction_id": "QM-20260512-141500-300750-HOLD-001",
        "side": InstructionSide.HOLD,
        "stock_code": "300750",
        "created_at": created,
        "valid_until": created.replace(
            hour=14, minute=55, second=0, microsecond=0
        ),
        "data_snapshot": _snapshot(created - timedelta(seconds=1)),
        "volume": None,
        "limit_price": None,
        "position_summary": None,
    }
    base.update(extra)
    return base


class TestInstructionPlanHold:
    def test_hold_requires_no_volume_or_price(self) -> None:
        plan = _make_plan(**_hold_overrides())
        assert plan.side is InstructionSide.HOLD
        assert plan.volume is None and plan.limit_price is None

    def test_hold_with_volume_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(**_hold_overrides(volume=100))

    def test_hold_with_price_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(**_hold_overrides(limit_price=1700.0))

    def test_hold_with_position_summary_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(**_hold_overrides(position_summary=_position_summary()))


# -----------------------------------------------------------------------------
# InstructionPlan — BUY/SELL constraints
# -----------------------------------------------------------------------------


class TestInstructionPlanBuySell:
    def test_buy_requires_volume_and_price(self) -> None:
        plan = _make_plan()
        assert plan.volume == 100 and plan.limit_price == 1680.0

    def test_buy_without_volume_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(volume=None)

    def test_buy_without_price_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(limit_price=None)

    def test_volume_must_be_lot_multiple(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(volume=150)

    def test_volume_min_100(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(volume=50)

    def test_limit_price_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(limit_price=0.0)

    def test_buy_requires_position_summary(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(position_summary=None)


# -----------------------------------------------------------------------------
# InstructionPlan — valid_until 3-way constraint
# -----------------------------------------------------------------------------


class TestValidUntilConstraints:
    def test_valid_until_must_be_strictly_after_created(self) -> None:
        created = datetime(2026, 5, 12, 10, 0, 0, tzinfo=SH)
        with pytest.raises(ValidationError):
            _make_plan(created_at=created, valid_until=created)

    def test_valid_until_must_be_same_local_date(self) -> None:
        created = datetime(2026, 5, 12, 10, 0, 0, tzinfo=SH)
        next_day = created + timedelta(days=1)
        with pytest.raises(ValidationError):
            _make_plan(created_at=created, valid_until=next_day)

    def test_valid_until_must_be_before_14_55(self) -> None:
        created = datetime(2026, 5, 12, 10, 0, 0, tzinfo=SH)
        too_late = created.replace(hour=14, minute=56)
        with pytest.raises(ValidationError):
            _make_plan(created_at=created, valid_until=too_late)

    def test_snapshot_must_be_before_created(self) -> None:
        created = datetime(2026, 5, 12, 10, 0, 0, tzinfo=SH)
        cutoff = created.replace(hour=14, minute=55, second=0, microsecond=0)
        late_snap = _snapshot(created + timedelta(seconds=1))
        with pytest.raises(ValidationError):
            _make_plan(
                created_at=created,
                valid_until=cutoff,
                data_snapshot=late_snap,
            )


# -----------------------------------------------------------------------------
# InstructionPlan — risk_summary length 14
# -----------------------------------------------------------------------------


class TestRiskSummaryLength:
    def test_length_14_accepted(self) -> None:
        plan = _make_plan()
        assert len(plan.risk_summary) == 14

    def test_length_7_rejected(self) -> None:
        short = _risk_summary_14()[:7]
        with pytest.raises(ValidationError):
            _make_plan(risk_summary=short)

    def test_length_13_rejected(self) -> None:
        short = _risk_summary_14()[:13]
        with pytest.raises(ValidationError):
            _make_plan(risk_summary=short)

    def test_length_15_rejected(self) -> None:
        too_long = _risk_summary_14() + (
            RiskCheckSummary(rule_name="extra", passed=True),
        )
        with pytest.raises(ValidationError):
            _make_plan(risk_summary=too_long)


# -----------------------------------------------------------------------------
# InstructionPlan — evidence_ids prefix enforcement
# -----------------------------------------------------------------------------


class TestEvidenceIdsPrefix:
    def test_all_five_prefixes_allowed(self) -> None:
        plan = _make_plan(
            evidence_ids=(
                "NEWS-em-1",
                "MIROFISH-run-2",
                "MARKET-600519-2026-05-12T09:30:00",
                "RISK-QM-20260512-093001-600519-BUY-001",
                "DEBATE-run-1-r2",
            )
        )
        assert len(plan.evidence_ids) == 5

    def test_unknown_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(evidence_ids=("FOO-bar",))

    def test_empty_tuple_allowed(self) -> None:
        plan = _make_plan(evidence_ids=())
        assert plan.evidence_ids == ()


# -----------------------------------------------------------------------------
# InstructionPlan — debate_round_count >= 1
# -----------------------------------------------------------------------------


class TestDebateRoundCount:
    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(debate_round_count=0)

    def test_one_accepted(self) -> None:
        plan = _make_plan(debate_round_count=1)
        assert plan.debate_round_count == 1


# -----------------------------------------------------------------------------
# InstructionPlan — frozen / immutable / extra=forbid
# -----------------------------------------------------------------------------


class TestImmutability:
    def test_frozen(self) -> None:
        plan = _make_plan()
        with pytest.raises(ValidationError):
            plan.status = InstructionStatus.VALIDATED  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(unknown_field="oops")

    def test_status_default_draft(self) -> None:
        plan = _make_plan()
        assert plan.status is InstructionStatus.DRAFT

    def test_model_copy_status_change(self) -> None:
        plan = _make_plan()
        updated = plan.model_copy(update={"status": InstructionStatus.VALIDATED})
        assert plan.status is InstructionStatus.DRAFT
        assert updated.status is InstructionStatus.VALIDATED


# -----------------------------------------------------------------------------
# InstructionPlan — rejection_reason gating
# -----------------------------------------------------------------------------


class TestRejectionReason:
    def test_rejected_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(status=InstructionStatus.REJECTED, rejection_reason=None)

    def test_ambiguous_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(status=InstructionStatus.AMBIGUOUS, rejection_reason=None)

    def test_non_rejected_must_not_have_reason(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(status=InstructionStatus.DRAFT, rejection_reason="x")

    def test_rejected_with_reason_ok(self) -> None:
        plan = _make_plan(
            status=InstructionStatus.REJECTED,
            rejection_reason="position_limit failed",
        )
        assert plan.rejection_reason == "position_limit failed"


# -----------------------------------------------------------------------------
# InstructionPlan — trade_date matches created_at
# -----------------------------------------------------------------------------


class TestTradeDateConsistency:
    def test_trade_date_must_match_created_local_date(self) -> None:
        created = datetime(2026, 5, 12, 10, 0, 0, tzinfo=SH)
        with pytest.raises(ValidationError):
            _make_plan(created_at=created, trade_date="2026-05-13")


class TestInstructionIdHHMMSSCrossCheck:
    """Codex-review cycle 1 — HHMMSS segment must match created_at."""

    def test_hhmmss_must_match_created(self) -> None:
        created = datetime(2026, 5, 12, 9, 30, 1, tzinfo=SH)
        with pytest.raises(ValidationError):
            _make_plan(
                instruction_id="QM-20260512-103015-600519-BUY-001",
                created_at=created,
            )


# -----------------------------------------------------------------------------
# stock_name sanity
# -----------------------------------------------------------------------------


class TestStockName:
    def test_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(stock_name="x" * 65)

    def test_control_char_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(stock_name="贵州\n茅台")


# -----------------------------------------------------------------------------
# invalidation_summary length
# -----------------------------------------------------------------------------


class TestInvalidationSummary:
    def test_max_200(self) -> None:
        with pytest.raises(ValidationError):
            _make_plan(invalidation_summary="x" * 201)

    def test_at_200_ok(self) -> None:
        plan = _make_plan(invalidation_summary="x" * 200)
        assert len(plan.invalidation_summary) == 200
