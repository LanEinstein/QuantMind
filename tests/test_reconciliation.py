"""Tests for B-004 reconciliation: models, parser, threshold, state machine."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.models.reconciliation import (
    CASH_TOLERANCE_CNY,
    COST_PRICE_TOLERANCE_CNY,
    DailyReconciliation,
    DeviationReport,
    FieldDeviation,
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
    ReportedPosition,
)
from backend.services.reconciliation_parser import (
    ReconciliationParseError,
    ReconciliationReplyKind,
    parse_reconciliation_reply,
)
from backend.services.reconciliation_state_machine import (
    ALLOWED_TICKET_TRANSITIONS,
    InvalidTicketTransitionError,
    is_freeze_active,
    transition_ticket,
)
from backend.services.reconciliation_threshold import detect_deviations

SH = ZoneInfo("Asia/Shanghai")
T_CREATE = datetime(2026, 5, 12, 16, 0, 0, tzinfo=SH)


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------


class TestReportedPosition:
    def test_lot_size(self) -> None:
        with pytest.raises(ValidationError):
            ReportedPosition(code="600519", volume=150, cost_price=1.0)

    def test_zero_volume_allowed(self) -> None:
        p = ReportedPosition(code="600519", volume=0, cost_price=0.0)
        assert p.volume == 0


class TestDailyReconciliation:
    def test_duplicate_code_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DailyReconciliation(
                ticket_id="RECON-20260512-001",
                trade_date="2026-05-12",
                received_at=T_CREATE,
                reported_cash=10000.0,
                reported_positions=(
                    ReportedPosition(code="600519", volume=100, cost_price=1.0),
                    ReportedPosition(code="600519", volume=200, cost_price=2.0),
                ),
                raw_text="x",
            )

    def test_ticket_id_pattern(self) -> None:
        with pytest.raises(ValidationError):
            DailyReconciliation(
                ticket_id="RECON-bad",
                trade_date="2026-05-12",
                received_at=T_CREATE,
                reported_cash=1.0,
                raw_text="x",
            )

    def test_cash_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            DailyReconciliation(
                ticket_id="RECON-20260512-001",
                trade_date="2026-05-12",
                received_at=T_CREATE,
                reported_cash=1e11,  # 100B
                raw_text="x",
            )


class TestReconciliationTicket:
    def _ticket(self, **kwargs: object) -> ReconciliationTicket:
        base: dict[str, object] = {
            "ticket_id": "RECON-20260512-001",
            "trade_date": "2026-05-12",
            "created_at": T_CREATE,
            "deviation_report": DeviationReport(
                ticket_id="RECON-20260512-001",
                overall_passed=False,
                deviations=(
                    FieldDeviation(
                        field="cash",
                        expected="1.00",
                        actual="2.00",
                        abs_diff=1.0,
                        threshold=1.0,
                        passed=False,
                    ),
                ),
            ),
            "expected_snapshot_id": "snap-1",
            "actual_reconciliation_id": "recon-1",
        }
        base.update(kwargs)
        return ReconciliationTicket(**base)  # type: ignore[arg-type]

    def test_open_default(self) -> None:
        t = self._ticket()
        assert t.status is ReconciliationTicketStatus.OPEN

    def test_open_must_not_set_resolved_at(self) -> None:
        with pytest.raises(ValidationError):
            self._ticket(resolved_at=T_CREATE + timedelta(hours=1))

    def test_resolved_amended_requires_snapshot(self) -> None:
        with pytest.raises(ValidationError):
            self._ticket(
                status=ReconciliationTicketStatus.RESOLVED_AMENDED,
                resolved_at=T_CREATE + timedelta(hours=1),
                resolution_message_id="msg-1",
            )

    def test_resolved_user_must_not_carry_snapshot(self) -> None:
        with pytest.raises(ValidationError):
            self._ticket(
                status=ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
                resolved_at=T_CREATE + timedelta(hours=1),
                resolution_message_id="msg-1",
                amended_snapshot=MockBrokerSnapshot(
                    cash=1.0, positions=(), snapshot_at=T_CREATE
                ),
            )

    def test_deviation_report_ticket_id_must_match(self) -> None:
        with pytest.raises(ValidationError):
            ReconciliationTicket(
                ticket_id="RECON-20260512-001",
                trade_date="2026-05-12",
                created_at=T_CREATE,
                deviation_report=DeviationReport(
                    ticket_id="RECON-99999999-001",
                    overall_passed=False,
                    deviations=(),
                ),
                expected_snapshot_id="snap-1",
                actual_reconciliation_id="recon-1",
            )


# -----------------------------------------------------------------------------
# Threshold checker
# -----------------------------------------------------------------------------


def _snap(
    cash: float, positions: tuple[ReportedPosition, ...] = ()
) -> MockBrokerSnapshot:
    return MockBrokerSnapshot(cash=cash, positions=positions, snapshot_at=T_CREATE)


def _recon(
    cash: float,
    positions: tuple[ReportedPosition, ...] = (),
    ticket_id: str = "RECON-20260512-001",
) -> DailyReconciliation:
    return DailyReconciliation(
        ticket_id=ticket_id,
        trade_date="2026-05-12",
        received_at=T_CREATE,
        reported_cash=cash,
        reported_positions=positions,
        raw_text="x",
    )


class TestThreshold:
    def test_exact_match_passes(self) -> None:
        snap = _snap(
            100.0, (ReportedPosition(code="600519", volume=100, cost_price=10.0),)
        )
        recon = _recon(
            100.0, (ReportedPosition(code="600519", volume=100, cost_price=10.0),)
        )
        report = detect_deviations(snap, recon)
        assert report.overall_passed is True

    def test_cash_within_tolerance(self) -> None:
        snap = _snap(100.0)
        recon = _recon(100.0 + CASH_TOLERANCE_CNY)
        report = detect_deviations(snap, recon)
        assert report.overall_passed is True

    def test_cash_outside_tolerance(self) -> None:
        snap = _snap(100.0)
        recon = _recon(100.0 + CASH_TOLERANCE_CNY + 0.01)
        report = detect_deviations(snap, recon)
        assert report.overall_passed is False
        cash_field = next(d for d in report.deviations if d.field == "cash")
        assert cash_field.passed is False

    def test_volume_zero_pct(self) -> None:
        snap = _snap(
            0.0, (ReportedPosition(code="600519", volume=100, cost_price=10.0),)
        )
        recon = _recon(
            0.0, (ReportedPosition(code="600519", volume=200, cost_price=10.0),)
        )
        report = detect_deviations(snap, recon)
        vol_field = next(
            d for d in report.deviations if d.field == "positions[600519].volume"
        )
        assert vol_field.passed is False
        assert vol_field.threshold == 0.0

    def test_cost_within_tolerance(self) -> None:
        snap = _snap(
            0.0, (ReportedPosition(code="600519", volume=100, cost_price=10.00),)
        )
        recon = _recon(
            0.0, (ReportedPosition(code="600519", volume=100, cost_price=10.01),)
        )
        report = detect_deviations(snap, recon)
        assert report.overall_passed is True

    def test_cost_outside_tolerance(self) -> None:
        snap = _snap(
            0.0, (ReportedPosition(code="600519", volume=100, cost_price=10.00),)
        )
        recon = _recon(
            0.0, (ReportedPosition(code="600519", volume=100, cost_price=10.05),)
        )
        report = detect_deviations(snap, recon)
        assert report.overall_passed is False

    def test_missing_position_on_expected_side(self) -> None:
        snap = _snap(
            0.0, (ReportedPosition(code="600519", volume=100, cost_price=10.0),)
        )
        recon = _recon(0.0, ())
        report = detect_deviations(snap, recon)
        assert report.overall_passed is False
        assert any(
            d.field == "positions[600519].presence" and not d.passed
            for d in report.deviations
        )

    def test_missing_position_on_actual_side(self) -> None:
        snap = _snap(0.0)
        recon = _recon(
            0.0, (ReportedPosition(code="600519", volume=100, cost_price=10.0),)
        )
        report = detect_deviations(snap, recon)
        assert report.overall_passed is False

    def test_thresholds_locked(self) -> None:
        assert CASH_TOLERANCE_CNY == 1.0
        assert COST_PRICE_TOLERANCE_CNY == 0.01


# -----------------------------------------------------------------------------
# Parser
# -----------------------------------------------------------------------------


class TestReconciliationParser:
    def test_ok(self) -> None:
        r = parse_reconciliation_reply("对账无误 RECON-20260512-001")
        assert r.kind is ReconciliationReplyKind.OK
        assert r.ticket_id == "RECON-20260512-001"

    def test_mismatch_with_positions(self) -> None:
        text = (
            "对账差异 RECON-20260512-001 现金 832146.70 持仓 "
            "600519 100股 成本 1678.50; 000001 2000股 成本 10.20"
        )
        r = parse_reconciliation_reply(text)
        assert r.kind is ReconciliationReplyKind.MISMATCH
        assert r.cash == 832146.70
        assert r.positions is not None
        assert len(r.positions) == 2
        assert r.positions[0].code == "600519"

    def test_mismatch_no_positions(self) -> None:
        r = parse_reconciliation_reply(
            "对账差异 RECON-20260512-001 现金 850000.00 持仓 无"
        )
        assert r.kind is ReconciliationReplyKind.MISMATCH
        assert r.positions == ()

    def test_amend(self) -> None:
        r = parse_reconciliation_reply(
            "对账更正 RECON-20260512-001 现金 1.00 持仓 600519 100股 成本 1.00"
        )
        assert r.kind is ReconciliationReplyKind.AMEND
        assert r.cash == 1.0

    def test_resolve_user(self) -> None:
        r = parse_reconciliation_reply(
            "对账采纳" + "：" + "用户回报 RECON-20260512-001"
        )
        assert r.kind is ReconciliationReplyKind.RESOLVE_USER

    def test_resolve_system(self) -> None:
        r = parse_reconciliation_reply(
            "对账采纳" + "：" + "系统镜像 RECON-20260512-001"
        )
        assert r.kind is ReconciliationReplyKind.RESOLVE_SYSTEM

    def test_resolve_half_width_colon_rejected(self) -> None:
        # P0-5 §1.3.1.4 红线: only full-width 「:」 (U+FF1A) is accepted;
        # the half-width ASCII colon (U+003A) must surface as AMBIGUOUS
        # so we never silently accept a half-width variant.
        text = "对账采纳" + ":" + "用户回报 RECON-20260512-001"
        with pytest.raises(ReconciliationParseError):
            parse_reconciliation_reply(text)

    def test_empty_text(self) -> None:
        with pytest.raises(ReconciliationParseError) as ei:
            parse_reconciliation_reply("   ")
        assert ei.value.reason == "empty_payload"

    def test_unknown_text(self) -> None:
        with pytest.raises(ReconciliationParseError) as ei:
            parse_reconciliation_reply("收到了 RECON-20260512-001")
        assert ei.value.reason == "no_pattern_match"

    def test_position_lot_size_violation(self) -> None:
        with pytest.raises(ReconciliationParseError):
            parse_reconciliation_reply(
                "对账差异 RECON-20260512-001 现金 0.00 持仓 600519 150股 成本 10.00"
            )

    def test_position_duplicate_code(self) -> None:
        with pytest.raises(ReconciliationParseError) as ei:
            parse_reconciliation_reply(
                "对账差异 RECON-20260512-001 现金 0.00 持仓 600519 100股 成本 10.00; "
                "600519 200股 成本 11.00"
            )
        assert ei.value.reason == "positions_duplicate"

    def test_whitespace_normalised(self) -> None:
        r = parse_reconciliation_reply("   对账无误   RECON-20260512-001   ")
        assert r.kind is ReconciliationReplyKind.OK


# -----------------------------------------------------------------------------
# State machine
# -----------------------------------------------------------------------------


@pytest.fixture
def open_ticket() -> ReconciliationTicket:
    return ReconciliationTicket(
        ticket_id="RECON-20260512-001",
        trade_date="2026-05-12",
        created_at=T_CREATE,
        deviation_report=DeviationReport(
            ticket_id="RECON-20260512-001",
            overall_passed=False,
            deviations=(),
        ),
        expected_snapshot_id="snap-1",
        actual_reconciliation_id="recon-1",
    )


class TestTicketStateMachine:
    def test_allowed_transitions_locked(self) -> None:
        # OPEN → 4 + EXPIRED → 3 = 7 transitions
        assert len(ALLOWED_TICKET_TRANSITIONS) == 7

    def test_open_to_resolved_user(self, open_ticket: ReconciliationTicket) -> None:
        resolved = transition_ticket(
            open_ticket,
            ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
            at=T_CREATE + timedelta(hours=1),
            resolution_message_id="msg-1",
        )
        assert resolved.status is ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH
        assert resolved.resolved_at == T_CREATE + timedelta(hours=1)
        assert resolved.resolution_message_id == "msg-1"

    def test_resolved_amended_requires_snapshot(
        self, open_ticket: ReconciliationTicket
    ) -> None:
        with pytest.raises(ValueError):
            transition_ticket(
                open_ticket,
                ReconciliationTicketStatus.RESOLVED_AMENDED,
                at=T_CREATE + timedelta(hours=1),
                resolution_message_id="msg-1",
            )

    def test_resolved_amended_ok(self, open_ticket: ReconciliationTicket) -> None:
        snap = MockBrokerSnapshot(cash=999.0, positions=(), snapshot_at=T_CREATE)
        resolved = transition_ticket(
            open_ticket,
            ReconciliationTicketStatus.RESOLVED_AMENDED,
            at=T_CREATE + timedelta(hours=1),
            resolution_message_id="msg-1",
            amended_snapshot=snap,
        )
        assert resolved.amended_snapshot == snap

    def test_invalid_transition(self, open_ticket: ReconciliationTicket) -> None:
        with pytest.raises(InvalidTicketTransitionError):
            transition_ticket(
                open_ticket,
                ReconciliationTicketStatus.OPEN,
                at=T_CREATE + timedelta(hours=1),
            )

    def test_expired_then_late_resolution(
        self, open_ticket: ReconciliationTicket
    ) -> None:
        expired = transition_ticket(
            open_ticket,
            ReconciliationTicketStatus.EXPIRED,
            at=T_CREATE + timedelta(hours=20),
        )
        assert expired.status is ReconciliationTicketStatus.EXPIRED

        resolved = transition_ticket(
            expired,
            ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
            at=T_CREATE + timedelta(hours=24),
            resolution_message_id="msg-2",
        )
        assert resolved.status is ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH

    def test_expired_must_not_carry_resolution_fields(
        self, open_ticket: ReconciliationTicket
    ) -> None:
        with pytest.raises(ValueError):
            transition_ticket(
                open_ticket,
                ReconciliationTicketStatus.EXPIRED,
                at=T_CREATE + timedelta(hours=20),
                resolution_message_id="msg-x",
            )

    def test_freeze_active_for_open_and_expired(
        self, open_ticket: ReconciliationTicket
    ) -> None:
        assert is_freeze_active(open_ticket) is True
        expired = transition_ticket(
            open_ticket,
            ReconciliationTicketStatus.EXPIRED,
            at=T_CREATE + timedelta(hours=20),
        )
        assert is_freeze_active(expired) is True

    def test_freeze_clears_on_resolution(
        self, open_ticket: ReconciliationTicket
    ) -> None:
        resolved = transition_ticket(
            open_ticket,
            ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
            at=T_CREATE + timedelta(hours=1),
            resolution_message_id="msg-1",
        )
        assert is_freeze_active(resolved) is False
