"""Tests for B-003: regex patterns + parser + state machine."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.execution.regex_patterns import (
    PATTERNS_AS_DICT,
    R_AMEND_FILLED,
    R_AMEND_PARTIAL,
    R_AMEND_UNFILLED,
    R_FILLED,
    R_PARTIAL,
    R_POST_CLOSE_FILLED,
    R_POST_CLOSE_PARTIAL,
    R_POST_CLOSE_UNFILLED,
    R_UNFILLED,
)
from backend.models.execution import (
    REPORT_SCHEMA_V1_OWNER_FEE,
    ExecutionReport,
    ExecutionReportChannel,
    ExecutionReportKind,
    ExecutionReportPrefix,
)
from backend.models.instruction import InstructionStatus
from backend.services.execution_report_parser import (
    ExecutionReportParseError,
    parse_execution_report,
)
from backend.services.instruction_state_machine import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    PostCloseFreezeError,
    transition,
)
from tests.test_instruction_models import _hold_overrides, _make_plan

SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 5, 12, 10, 0, 0, tzinfo=SH)


# -----------------------------------------------------------------------------
# regex_patterns
# -----------------------------------------------------------------------------


class TestRegexAcceptance:
    def test_filled(self) -> None:
        # P0-4-amendment-2026-05-27 §2.1 — FILLED v2 is「成交价 + 股数」only.
        text = (
            "已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 "
            "成交价 1678.50"
        )
        m = R_FILLED.fullmatch(text)
        assert m is not None
        assert m["instruction_id"] == "QM-20260512-093001-600519-BUY-001"
        assert m["volume"] == "100"
        assert m["fill_price"] == "1678.50"
        assert "fee" not in m.groupdict()

    def test_partial(self) -> None:
        text = (
            "部分执行 QM-20260512-093001-600519-BUY-001 买入 600519 60股 "
            "成交价 1678.50 剩余未成交 40股"
        )
        m = R_PARTIAL.fullmatch(text)
        assert m is not None
        assert m["filled_volume"] == "60"
        assert m["remain_volume"] == "40"

    def test_unfilled_full_width_colon(self) -> None:
        text = "未执行 QM-20260512-093001-600519-BUY-001 原因: 价格未到"
        assert R_UNFILLED.fullmatch(text) is not None

    def test_unfilled_ascii_colon(self) -> None:
        text = "未执行 QM-20260512-093001-600519-BUY-001 原因:价格未到"
        assert R_UNFILLED.fullmatch(text) is not None

    @pytest.mark.parametrize(
        "pattern,prefix",
        [
            (R_AMEND_FILLED, "更正"),
            (R_POST_CLOSE_FILLED, "盘后补录"),
        ],
    )
    def test_prefixed_filled(self, pattern, prefix: str) -> None:
        text = (
            f"{prefix} 已执行 QM-20260512-093001-600519-BUY-001 买入 600519 "
            f"100股 成交价 1678.50"
        )
        assert pattern.fullmatch(text) is not None

    @pytest.mark.parametrize(
        "pattern,prefix",
        [
            (R_AMEND_PARTIAL, "更正"),
            (R_POST_CLOSE_PARTIAL, "盘后补录"),
            (R_AMEND_UNFILLED, "更正"),
            (R_POST_CLOSE_UNFILLED, "盘后补录"),
        ],
    )
    def test_prefixed_other(self, pattern, prefix: str) -> None:
        partial = (
            f"{prefix} 部分执行 QM-20260512-093001-600519-BUY-001 买入 600519 "
            f"60股 成交价 1678.50 剩余未成交 40股"
        )
        unfilled = (
            f"{prefix} 未执行 QM-20260512-093001-600519-BUY-001 原因: 临时改主意"
        )
        text = partial if "部分" in pattern.pattern else unfilled
        assert pattern.fullmatch(text) is not None


class TestRegexRejection:
    @pytest.mark.parametrize(
        "bad",
        [
            "已执行 ",
            (
                "已执行 QM-20260512-093001-600519-HOLD-001 买入 600519 100股 "
                "成交价 1.00 手续费 0.00"
            ),
            (
                "已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 "
                "成交价 abc 手续费 0.00"
            ),
            (
                "已执行  QM-20260512-093001-600519-BUY-001 买入 600519 100股 "
                "成交价 1.00 手续费 0.00"
            ),
            (
                "已执行QM-20260512-093001-600519-BUY-001 买入 600519 100股 "
                "成交价 1.00 手续费 0.00"
            ),
        ],
    )
    def test_filled_bad(self, bad: str) -> None:
        assert R_FILLED.fullmatch(bad) is None


class TestPatternsAsDict:
    def test_keys_locked(self) -> None:
        assert set(PATTERNS_AS_DICT.keys()) == {
            "FILLED",
            "PARTIAL",
            "UNFILLED",
            "AMEND_FILLED",
            "AMEND_PARTIAL",
            "AMEND_UNFILLED",
            "POST_CLOSE_FILLED",
            "POST_CLOSE_PARTIAL",
            "POST_CLOSE_UNFILLED",
        }

    def test_immutable(self) -> None:
        with pytest.raises(TypeError):
            PATTERNS_AS_DICT["FILLED"] = "x"  # type: ignore[index]


# -----------------------------------------------------------------------------
# parser
# -----------------------------------------------------------------------------


class TestParser:
    def test_filled(self) -> None:
        report = parse_execution_report(
            "已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 "
            "成交价 1678.50",
            channel=ExecutionReportChannel.FEISHU,
            received_at=NOW,
        )
        assert report.kind is ExecutionReportKind.FILLED
        assert report.prefix is ExecutionReportPrefix.NONE
        assert report.filled_volume == 100
        assert report.fill_price == 1678.5
        # P0-4-amendment-2026-05-27 §2.4 — owner no longer reports fee;
        # every parsed report is v2 with fee absent (system computes it).
        assert report.fee is None
        assert report.report_schema_version == 2

    def test_partial(self) -> None:
        report = parse_execution_report(
            "部分执行 QM-20260512-093001-600519-BUY-001 买入 600519 60股 "
            "成交价 1678.50 剩余未成交 40股",
            channel=ExecutionReportChannel.FRONTEND,
            received_at=NOW,
        )
        assert report.kind is ExecutionReportKind.PARTIAL
        assert report.filled_volume == 60
        assert report.remain_volume == 40

    def test_unfilled(self) -> None:
        report = parse_execution_report(
            "未执行 QM-20260512-093001-600519-BUY-001 原因: 价格未到",
            channel=ExecutionReportChannel.FEISHU,
            received_at=NOW,
        )
        assert report.kind is ExecutionReportKind.UNFILLED
        assert report.reason == "价格未到"

    def test_amend_filled(self) -> None:
        report = parse_execution_report(
            "更正 已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 "
            "成交价 1677.80",
            channel=ExecutionReportChannel.FEISHU,
            received_at=NOW,
        )
        assert report.prefix is ExecutionReportPrefix.AMEND
        assert report.kind is ExecutionReportKind.FILLED

    def test_post_close_unfilled(self) -> None:
        text = (
            "盘后补录 未执行 QM-20260512-103015-000001-SELL-002 "
            "原因: 临时去开会忘了下单"
        )
        report = parse_execution_report(
            text,
            channel=ExecutionReportChannel.FEISHU,
            received_at=NOW,
        )
        assert report.prefix is ExecutionReportPrefix.POST_CLOSE
        assert report.kind is ExecutionReportKind.UNFILLED

    def test_whitespace_collapsed(self) -> None:
        report = parse_execution_report(
            "  已执行   QM-20260512-093001-600519-BUY-001 买入 600519 100股 "
            "成交价 1678.50  ",
            channel=ExecutionReportChannel.FEISHU,
            received_at=NOW,
        )
        assert report.kind is ExecutionReportKind.FILLED

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(ExecutionReportParseError) as ei:
            parse_execution_report(
                "成交了 600519",
                channel=ExecutionReportChannel.FEISHU,
                received_at=NOW,
            )
        assert ei.value.reason == "no_pattern_match"

    def test_empty_text_raises(self) -> None:
        with pytest.raises(ExecutionReportParseError) as ei:
            parse_execution_report(
                "   ",
                channel=ExecutionReportChannel.FEISHU,
                received_at=NOW,
            )
        assert ei.value.reason == "empty_payload"

    def test_prefix_order_wins(self) -> None:
        # 更正 应优先匹配 amend 形式而非裸已执行
        report = parse_execution_report(
            "更正 已执行 QM-20260512-093001-600519-BUY-001 买入 600519 100股 "
            "成交价 1.00",
            channel=ExecutionReportChannel.FEISHU,
            received_at=NOW,
        )
        assert report.prefix is ExecutionReportPrefix.AMEND


# -----------------------------------------------------------------------------
# ExecutionReport schema invariants
# -----------------------------------------------------------------------------


class TestExecutionReportInvariants:
    def test_filled_v2_forbids_fee(self) -> None:
        # P0-4-amendment-2026-05-27 §2.4 — a v2 (default) FILLED report
        # must NOT carry a fee; the system derives it.
        with pytest.raises(ValidationError):
            ExecutionReport(
                report_id="r1",
                instruction_id="QM-20260512-093001-600519-BUY-001",
                kind=ExecutionReportKind.FILLED,
                channel=ExecutionReportChannel.FEISHU,
                side_zh="买入",
                stock_code="600519",
                filled_volume=100,
                fill_price=1.0,
                fee=5.0,  # forbidden on v2
                raw_text="x",
                received_at=NOW,
                parsed_at=NOW,
            )

    def test_filled_v2_without_fee_is_valid(self) -> None:
        report = ExecutionReport(
            report_id="r1",
            instruction_id="QM-20260512-093001-600519-BUY-001",
            kind=ExecutionReportKind.FILLED,
            channel=ExecutionReportChannel.FEISHU,
            side_zh="买入",
            stock_code="600519",
            filled_volume=100,
            fill_price=1.0,
            raw_text="x",
            received_at=NOW,
            parsed_at=NOW,
        )
        assert report.report_schema_version == 2
        assert report.fee is None

    def test_filled_v1_requires_fee(self) -> None:
        # Legacy v1 keeps the original invariant — fee must be present.
        with pytest.raises(ValidationError):
            ExecutionReport(
                report_id="r1",
                instruction_id="QM-20260512-093001-600519-BUY-001",
                kind=ExecutionReportKind.FILLED,
                channel=ExecutionReportChannel.FEISHU,
                report_schema_version=REPORT_SCHEMA_V1_OWNER_FEE,
                side_zh="买入",
                stock_code="600519",
                filled_volume=100,
                fill_price=1.0,
                # fee missing — invalid for v1
                raw_text="x",
                received_at=NOW,
                parsed_at=NOW,
            )

    def test_filled_v1_with_fee_is_valid(self) -> None:
        report = ExecutionReport(
            report_id="r1",
            instruction_id="QM-20260512-093001-600519-BUY-001",
            kind=ExecutionReportKind.FILLED,
            channel=ExecutionReportChannel.FEISHU,
            report_schema_version=REPORT_SCHEMA_V1_OWNER_FEE,
            side_zh="买入",
            stock_code="600519",
            filled_volume=100,
            fill_price=1.0,
            fee=5.0,
            raw_text="x",
            received_at=NOW,
            parsed_at=NOW,
        )
        assert report.fee == 5.0

    def test_v1_only_valid_for_filled(self) -> None:
        # v1 is the legacy owner-fee FILLED schema only — a v1 PARTIAL is
        # rejected at the model boundary (would crash in the broker).
        with pytest.raises(ValidationError, match="v1 is only valid"):
            ExecutionReport(
                report_id="r1",
                instruction_id="QM-20260512-093001-600519-BUY-001",
                kind=ExecutionReportKind.PARTIAL,
                channel=ExecutionReportChannel.FEISHU,
                report_schema_version=REPORT_SCHEMA_V1_OWNER_FEE,
                side_zh="买入",
                stock_code="600519",
                filled_volume=60,
                remain_volume=40,
                fill_price=1.0,
                raw_text="x",
                received_at=NOW,
                parsed_at=NOW,
            )

    def test_unfilled_must_not_carry_price(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionReport(
                report_id="r1",
                instruction_id="QM-20260512-093001-600519-BUY-001",
                kind=ExecutionReportKind.UNFILLED,
                channel=ExecutionReportChannel.FEISHU,
                reason="x",
                fill_price=1.0,  # forbidden for UNFILLED
                raw_text="未执行 QM-20260512-093001-600519-BUY-001 原因: x",
                received_at=NOW,
                parsed_at=NOW,
            )

    def test_frozen(self) -> None:
        r = ExecutionReport(
            report_id="r1",
            instruction_id="QM-20260512-093001-600519-BUY-001",
            kind=ExecutionReportKind.UNFILLED,
            channel=ExecutionReportChannel.FEISHU,
            reason="x",
            raw_text="未执行 QM-20260512-093001-600519-BUY-001 原因: x",
            received_at=NOW,
            parsed_at=NOW,
        )
        with pytest.raises(ValidationError):
            r.reason = "y"  # type: ignore[misc]


# -----------------------------------------------------------------------------
# state machine
# -----------------------------------------------------------------------------


class TestStateMachine:
    def test_allowed_pairs_count(self) -> None:
        # 7 base + 8 P0-4 extensions + 2 same-state = 17
        assert len(ALLOWED_TRANSITIONS) == 17

    def test_draft_to_validated(self) -> None:
        plan = _make_plan()
        updated = transition(
            plan,
            InstructionStatus.VALIDATED,
            at=NOW,
        )
        assert updated.status is InstructionStatus.VALIDATED

    def test_illegal_transition(self) -> None:
        plan = _make_plan()
        with pytest.raises(InvalidTransitionError):
            transition(plan, InstructionStatus.FILLED, at=NOW)

    def test_reject_requires_reason(self) -> None:
        plan = _make_plan()
        with pytest.raises(ValueError):
            transition(plan, InstructionStatus.REJECTED, at=NOW, reason=None)

    def test_reject_with_reason(self) -> None:
        plan = _make_plan()
        rej = transition(
            plan,
            InstructionStatus.REJECTED,
            at=NOW,
            reason="position_limit failed",
        )
        assert rej.status is InstructionStatus.REJECTED
        assert rej.rejection_reason == "position_limit failed"

    def test_dispatched_to_ambiguous_with_reason(self) -> None:
        plan = _make_plan(status=InstructionStatus.DISPATCHED)
        amb = transition(
            plan,
            InstructionStatus.AMBIGUOUS,
            at=NOW,
            reason="no_pattern_match",
        )
        assert amb.status is InstructionStatus.AMBIGUOUS

    def test_post_close_blocked(self) -> None:
        plan = _make_plan(status=InstructionStatus.DISPATCHED)
        at = datetime(2026, 5, 12, 16, 0, 1, tzinfo=SH)
        with pytest.raises(PostCloseFreezeError):
            transition(plan, InstructionStatus.FILLED, at=at)

    def test_post_close_scheduler_allowed(self) -> None:
        plan = _make_plan(status=InstructionStatus.DISPATCHED)
        at = datetime(2026, 5, 12, 16, 0, 1, tzinfo=SH)
        updated = transition(
            plan, InstructionStatus.FILLED, at=at, allow_post_close=True
        )
        assert updated.status is InstructionStatus.FILLED

    def test_post_close_at_exact_cutoff_allowed(self) -> None:
        plan = _make_plan(status=InstructionStatus.DISPATCHED)
        at = datetime(2026, 5, 12, 16, 0, 0, tzinfo=SH)
        updated = transition(plan, InstructionStatus.FILLED, at=at)
        assert updated.status is InstructionStatus.FILLED

    def test_clears_rejection_reason_on_non_reject_target(self) -> None:
        plan = _make_plan(
            status=InstructionStatus.AMBIGUOUS,
            rejection_reason="prior parse fail",
        )
        updated = transition(
            plan, InstructionStatus.DISPATCHED, at=NOW
        )
        assert updated.rejection_reason is None

    def test_filled_same_state_amend(self) -> None:
        plan = _make_plan(status=InstructionStatus.FILLED)
        updated = transition(plan, InstructionStatus.FILLED, at=NOW)
        assert updated.status is InstructionStatus.FILLED

    def test_hold_cannot_validate_to_filled(self) -> None:
        plan = _make_plan(**_hold_overrides())
        # HOLD is DRAFT by default; DRAFT → FILLED isn't in the allowlist.
        with pytest.raises(InvalidTransitionError):
            transition(plan, InstructionStatus.FILLED, at=NOW)


# -----------------------------------------------------------------------------
# Cross-check: parsed report instruction_id matches a plan
# -----------------------------------------------------------------------------


class TestParserCrossCheck:
    """Codex-review cycle 1 — field cross-check + ValidationError trap."""

    def test_side_zh_mismatch_yields_parse_error(self) -> None:
        # instruction_id encodes BUY but the body says 卖出 — P0-4 §1.2.1
        # requires this to surface as AMBIGUOUS, not a silent acceptance.
        text = (
            "已执行 QM-20260512-093001-600519-BUY-001 卖出 600519 100股 "
            "成交价 1678.50"
        )
        with pytest.raises(ExecutionReportParseError) as ei:
            parse_execution_report(
                text,
                channel=ExecutionReportChannel.FEISHU,
                received_at=NOW,
            )
        assert ei.value.reason == "field_cross_check_failed"

    def test_stock_code_mismatch_yields_parse_error(self) -> None:
        text = (
            "已执行 QM-20260512-093001-600519-BUY-001 买入 000001 100股 "
            "成交价 1678.50"
        )
        with pytest.raises(ExecutionReportParseError) as ei:
            parse_execution_report(
                text,
                channel=ExecutionReportChannel.FEISHU,
                received_at=NOW,
            )
        assert ei.value.reason == "field_cross_check_failed"

    def test_zero_filled_volume_yields_parse_error(self) -> None:
        # `0股` matches the regex but fails model invariants — must
        # surface as parse_error, not raw ValidationError.
        text = (
            "已执行 QM-20260512-093001-600519-BUY-001 买入 600519 0股 "
            "成交价 1.00"
        )
        with pytest.raises(ExecutionReportParseError):
            parse_execution_report(
                text,
                channel=ExecutionReportChannel.FEISHU,
                received_at=NOW,
            )


class TestParserPlanLink:
    def test_round_trip(self) -> None:
        plan = _make_plan()
        text = (
            f"已执行 {plan.instruction_id} 买入 {plan.stock_code} 100股 "
            f"成交价 1678.50"
        )
        # ``received_at`` is set after `plan.created_at` to imitate a
        # human Feishu reply; the parser does not check timing, but the
        # downstream applier will.
        report = parse_execution_report(
            text,
            channel=ExecutionReportChannel.FEISHU,
            received_at=plan.created_at + timedelta(minutes=5),
        )
        assert report.instruction_id == plan.instruction_id
        assert report.filled_volume == plan.volume
