"""Tests for backend/services/instruction_plan.py — pure helper functions.

Covers:
- `make_instruction_id` format + seq bounds (P0-3 §1.1.5, §2 red line 11)
- `derive_order_from_plan` (P0-3 §1.1.4)
- `is_routable` rules (P0-3 §1.3.1, §2 red line 2)
- `validate_valid_until` reuses the model validator semantics (P0-3 §1.4.1)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.broker.models import OrderDirection, OrderStatus, OrderType
from backend.models.instruction import (
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
)
from backend.services.instruction_plan import (
    derive_order_from_plan,
    is_routable,
    make_instruction_id,
    validate_valid_until,
)
from tests.test_instruction_models import _hold_overrides, _make_plan

SH = ZoneInfo("Asia/Shanghai")


# -----------------------------------------------------------------------------
# make_instruction_id
# -----------------------------------------------------------------------------


class TestMakeInstructionId:
    def test_basic_format(self) -> None:
        created = datetime(2026, 5, 12, 9, 30, 1, tzinfo=SH)
        iid = make_instruction_id(created, "600519", InstructionSide.BUY, 1)
        assert iid == "QM-20260512-093001-600519-BUY-001"

    def test_uses_asia_shanghai_local(self) -> None:
        utc = ZoneInfo("UTC")
        created = datetime(2026, 5, 12, 1, 30, 1, tzinfo=utc)  # 09:30:01 SH
        iid = make_instruction_id(created, "600519", InstructionSide.BUY, 1)
        assert iid == "QM-20260512-093001-600519-BUY-001"

    def test_seq_zero_padded(self) -> None:
        created = datetime(2026, 5, 12, 9, 30, 1, tzinfo=SH)
        iid = make_instruction_id(created, "600519", InstructionSide.SELL, 42)
        assert iid.endswith("-SELL-042")

    def test_seq_lower_bound_rejected(self) -> None:
        created = datetime(2026, 5, 12, 9, 30, 1, tzinfo=SH)
        with pytest.raises(ValueError):
            make_instruction_id(created, "600519", InstructionSide.BUY, 0)

    def test_seq_upper_bound_rejected(self) -> None:
        created = datetime(2026, 5, 12, 9, 30, 1, tzinfo=SH)
        with pytest.raises(ValueError):
            make_instruction_id(created, "600519", InstructionSide.BUY, 1000)

    def test_invalid_code_rejected(self) -> None:
        created = datetime(2026, 5, 12, 9, 30, 1, tzinfo=SH)
        with pytest.raises(ValueError):
            make_instruction_id(created, "60051", InstructionSide.BUY, 1)

    def test_naive_datetime_rejected(self) -> None:
        with pytest.raises(ValueError):
            make_instruction_id(
                datetime(2026, 5, 12, 9, 30, 1),
                "600519",
                InstructionSide.BUY,
                1,
            )


# -----------------------------------------------------------------------------
# derive_order_from_plan
# -----------------------------------------------------------------------------


class TestDeriveOrderFromPlan:
    def test_buy_plan_maps_to_buy_limit_order(self) -> None:
        plan = _make_plan()
        order = derive_order_from_plan(plan)
        assert order.order_id == plan.instruction_id
        assert order.code == plan.stock_code
        assert order.direction is OrderDirection.BUY
        assert order.order_type is OrderType.LIMIT
        assert order.status is OrderStatus.PENDING
        assert order.price == 1680.0
        assert order.volume == 100
        assert order.created_at == plan.created_at

    def test_sell_plan_maps_to_sell(self) -> None:
        created = datetime(2026, 5, 12, 10, 30, 15, tzinfo=SH)
        plan = _make_plan(
            instruction_id="QM-20260512-103015-000001-SELL-002",
            side=InstructionSide.SELL,
            stock_code="000001",
            created_at=created,
            valid_until=created.replace(hour=14, minute=55, second=0, microsecond=0),
            data_snapshot=_make_plan().data_snapshot.model_copy(
                update={
                    "snapshot_at": created - timedelta(seconds=1),
                }
            ),
        )
        order = derive_order_from_plan(plan)
        assert order.direction is OrderDirection.SELL

    def test_hold_plan_rejected(self) -> None:
        plan = _make_plan(**_hold_overrides())
        with pytest.raises(ValueError):
            derive_order_from_plan(plan)


# -----------------------------------------------------------------------------
# is_routable
# -----------------------------------------------------------------------------


class TestIsRoutable:
    def test_hold_never_routable(self) -> None:
        plan = _make_plan(
            **_hold_overrides(status=InstructionStatus.VALIDATED),
        )
        assert is_routable(plan) is False

    def test_buy_draft_not_routable(self) -> None:
        plan = _make_plan(status=InstructionStatus.DRAFT)
        assert is_routable(plan) is False

    def test_buy_validated_routable(self) -> None:
        plan = _make_plan(status=InstructionStatus.VALIDATED)
        assert is_routable(plan) is True

    def test_buy_rejected_not_routable(self) -> None:
        plan = _make_plan(
            status=InstructionStatus.REJECTED,
            rejection_reason="position_limit failed",
        )
        assert is_routable(plan) is False


# -----------------------------------------------------------------------------
# validate_valid_until
# -----------------------------------------------------------------------------


class TestValidateValidUntil:
    def test_valid(self) -> None:
        plan = _make_plan()
        validate_valid_until(plan)  # must not raise

    def test_uses_shanghai_local(self) -> None:
        """A UTC-encoded valid_until that is 14:54 Shanghai must pass; one
        that is 14:56 Shanghai must fail."""
        utc = ZoneInfo("UTC")
        created_utc = datetime(2026, 5, 12, 1, 30, 1, tzinfo=utc)  # 09:30:01 SH
        ok_utc = datetime(2026, 5, 12, 6, 54, 0, tzinfo=utc)  # 14:54:00 SH
        bad_utc = datetime(2026, 5, 12, 6, 56, 0, tzinfo=utc)  # 14:56:00 SH

        # Build a plan with the UTC values; the model accepts any zone-aware
        # datetime as long as the local validation passes.
        snap = created_utc - timedelta(seconds=2)
        plan_ok = _make_plan(
            created_at=created_utc,
            valid_until=ok_utc,
            data_snapshot=_make_plan().data_snapshot.model_copy(
                update={"snapshot_at": snap}
            ),
            trade_date="2026-05-12",
        )
        validate_valid_until(plan_ok)
        with pytest.raises(ValueError):
            # Re-using model_copy bypasses the model-level cross-field check,
            # so we exercise the standalone validator directly.
            plan_bad = plan_ok.model_copy(update={"valid_until": bad_utc})
            validate_valid_until(plan_bad)


# -----------------------------------------------------------------------------
# Cross-check: instruction_id must be unique per (timestamp, code, side, seq)
# -----------------------------------------------------------------------------


class TestMakeInstructionIdRoundTrip:
    def test_id_validates_against_model_pattern(self) -> None:
        created = datetime(2026, 5, 12, 9, 30, 1, tzinfo=SH)
        iid = make_instruction_id(created, "600519", InstructionSide.BUY, 1)
        plan = _make_plan(instruction_id=iid)
        assert isinstance(plan, InstructionPlan)
