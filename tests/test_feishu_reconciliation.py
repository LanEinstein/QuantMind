"""F-005 — ReconciliationOrchestrator tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from backend.broker.appliers import ApplyResult
from backend.integrations.feishu.client import SendMessageResult
from backend.integrations.feishu.reconciliation import (
    DecisionResult,
    InitiationResult,
    ReconciliationOrchestrator,
)
from backend.integrations.feishu.renderer import MessageRenderer
from backend.models.reconciliation import (
    DailyReconciliation,
    DeviationReport,
    FieldDeviation,
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
    ReportedPosition,
)
from backend.services.reconciliation_parser import ReconciliationReplyKind

_VALID_CHAT = "oc_" + "f" * 32
_TICKET_ID = "RECON-20260516-001"


# -----------------------------------------------------------------------------
# Test doubles
# -----------------------------------------------------------------------------


class _InMemoryTicketRepo:
    def __init__(self) -> None:
        self.tickets: dict[str, ReconciliationTicket] = {}

    async def get(self, ticket_id: str) -> ReconciliationTicket | None:
        return self.tickets.get(ticket_id)

    async def save(self, ticket: ReconciliationTicket) -> None:
        self.tickets[ticket.ticket_id] = ticket

    async def list_open_for_date(
        self, trade_date: str
    ) -> tuple[ReconciliationTicket, ...]:
        return tuple(
            t
            for t in self.tickets.values()
            if t.trade_date == trade_date
            and t.status == ReconciliationTicketStatus.OPEN
        )


class _InMemoryDailyStore:
    def __init__(self) -> None:
        self.dailies: dict[str, DailyReconciliation] = {}

    async def save(self, daily: DailyReconciliation) -> None:
        self.dailies[daily.trade_date] = daily

    async def get(
        self, trade_date: str
    ) -> DailyReconciliation | None:
        return self.dailies.get(trade_date)


class _RecordingApplier:
    def __init__(self) -> None:
        self.calls: list[ReconciliationTicket] = []

    async def reset_to_snapshot(
        self,
        ticket: ReconciliationTicket,
        *,
        now: datetime | None = None,
        **_: Any,
    ) -> ApplyResult:
        self.calls.append(ticket)
        return ApplyResult(
            cash_delta=10.0,
            positions_delta=({"code": "510300", "delta_volume": -100},),
            broker_event_sequence=5,
            reason="reset",
        )


class _RecordingFeishu:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def send_message(
        self, chat_id: str, content: str, **kwargs: Any
    ) -> SendMessageResult:
        self.calls.append((chat_id, content, kwargs))
        return SendMessageResult(
            ok=True, code=0, msg="success",
            message_id="om_a", log_id="log_a",
        )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _build_orchestrator(
    *,
    feishu: _RecordingFeishu | None = None,
    tickets: _InMemoryTicketRepo | None = None,
    daily: _InMemoryDailyStore | None = None,
    applier: _RecordingApplier | None = None,
    now: datetime | None = None,
) -> tuple[
    ReconciliationOrchestrator,
    _RecordingFeishu | None,
    _InMemoryTicketRepo,
    _InMemoryDailyStore,
    _RecordingApplier,
]:
    feishu_obj = feishu if feishu is not None else _RecordingFeishu()
    tickets_obj = tickets or _InMemoryTicketRepo()
    daily_obj = daily or _InMemoryDailyStore()
    applier_obj = applier or _RecordingApplier()
    fixed_now = now or datetime(2026, 5, 16, 16, 0, 0, tzinfo=UTC)
    orchestrator = ReconciliationOrchestrator(
        feishu=feishu_obj,  # type: ignore[arg-type]
        renderer=MessageRenderer(),
        ticket_repo=tickets_obj,  # type: ignore[arg-type]
        daily_store=daily_obj,  # type: ignore[arg-type]
        applier=applier_obj,  # type: ignore[arg-type]
        decision_chat_id=_VALID_CHAT,
        now=lambda: fixed_now,
    )
    return orchestrator, feishu_obj, tickets_obj, daily_obj, applier_obj


def _snapshot(
    *, cash: float = 200_000.0, vol: int = 1000
) -> MockBrokerSnapshot:
    return MockBrokerSnapshot(
        cash=cash,
        positions=(
            ReportedPosition(code="510300", volume=vol, cost_price=3.85),
        ),
        snapshot_at=datetime(2026, 5, 16, 16, 0, 0, tzinfo=UTC),
    )


def _open_ticket(
    *,
    ticket_id: str = _TICKET_ID,
    snapshot: MockBrokerSnapshot | None = None,
) -> ReconciliationTicket:
    snap = snapshot or _snapshot()
    dev = DeviationReport(
        ticket_id=ticket_id,
        overall_passed=False,
        deviations=(
            FieldDeviation(
                field="cash",
                expected=f"{snap.cash:.2f}",
                actual="0.00",
                abs_diff=snap.cash,
                threshold=1.0,
                passed=False,
            ),
        ),
    )
    return ReconciliationTicket(
        ticket_id=ticket_id,
        trade_date="2026-05-16",
        created_at=datetime(2026, 5, 16, 16, 0, 0, tzinfo=UTC),
        deviation_report=dev,
        expected_snapshot_id="snap-1",
        actual_reconciliation_id="reply-1",
    )


# -----------------------------------------------------------------------------
# Construction
# -----------------------------------------------------------------------------


class TestConstruction:
    def test_empty_chat_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="decision_chat_id"):
            ReconciliationOrchestrator(
                feishu=None,
                renderer=MessageRenderer(),
                ticket_repo=_InMemoryTicketRepo(),  # type: ignore[arg-type]
                daily_store=_InMemoryDailyStore(),  # type: ignore[arg-type]
                applier=_RecordingApplier(),  # type: ignore[arg-type]
                decision_chat_id="",
            )


# -----------------------------------------------------------------------------
# 1. Initiation
# -----------------------------------------------------------------------------


class TestInitiation:
    @pytest.mark.asyncio
    async def test_sends_to_decision_chat(self) -> None:
        orchestrator, feishu, *_ = _build_orchestrator()
        result = await orchestrator.initiate_reconciliation(
            ticket_id=_TICKET_ID,
            trade_date="2026-05-16",
            snapshot=_snapshot(),
        )
        assert result.sent is True
        assert isinstance(result, InitiationResult)
        assert len(feishu.calls) == 1
        chat_id, body, kwargs = feishu.calls[0]
        assert chat_id == _VALID_CHAT
        assert "【QuantMind 对账】" in body
        assert _TICKET_ID in body
        assert "200000.00" in body  # cash
        # uuid carries the ticket id for idempotency.
        assert kwargs["uuid"] == f"recon-init-{_TICKET_ID}"

    @pytest.mark.asyncio
    async def test_skipped_when_no_feishu_client(self) -> None:
        orchestrator, _, _, _, _ = _build_orchestrator(feishu=None)
        # Replace the recorded fixture client with None.
        orchestrator._feishu = None  # type: ignore[attr-defined]
        result = await orchestrator.initiate_reconciliation(
            ticket_id=_TICKET_ID,
            trade_date="2026-05-16",
            snapshot=_snapshot(),
        )
        assert result.sent is False
        assert result.send_result is None

    @pytest.mark.asyncio
    async def test_total_equity_estimate(self) -> None:
        """Total equity = cash + sum(cost × volume)."""
        orchestrator, feishu, *_ = _build_orchestrator()
        await orchestrator.initiate_reconciliation(
            ticket_id=_TICKET_ID,
            trade_date="2026-05-16",
            snapshot=_snapshot(cash=100_000.0, vol=1000),
        )
        body = feishu.calls[0][1]
        # 100_000 + 3.85 × 1000 = 103850.00
        assert "103850.00" in body


# -----------------------------------------------------------------------------
# 2. Reply handling
# -----------------------------------------------------------------------------


class TestReplyHandling:
    @pytest.mark.asyncio
    async def test_unrecognised_text_returns_not_handled(self) -> None:
        orchestrator, *_ = _build_orchestrator()
        outcome = await orchestrator.handle_reply("hello world")
        assert outcome.handled is False
        assert outcome.parse_error in {
            "no_pattern_match",
            "empty_payload",
            "positions_malformed",
        }

    @pytest.mark.asyncio
    async def test_empty_text(self) -> None:
        orchestrator, *_ = _build_orchestrator()
        outcome = await orchestrator.handle_reply("")
        assert outcome.handled is False
        assert outcome.parse_error == "empty_payload"

    @pytest.mark.asyncio
    async def test_ok_reply_acknowledged(self) -> None:
        orchestrator, *_ = _build_orchestrator()
        outcome = await orchestrator.handle_reply(
            f"对账无误 {_TICKET_ID}"
        )
        assert outcome.handled is True
        assert outcome.kind == ReconciliationReplyKind.OK
        assert outcome.ticket_id == _TICKET_ID

    @pytest.mark.asyncio
    async def test_mismatch_with_unknown_ticket(self) -> None:
        orchestrator, *_ = _build_orchestrator()
        outcome = await orchestrator.handle_reply(
            f"对账差异 {_TICKET_ID} 现金 199999.50 持仓 510300 1000股 成本 3.85"
        )
        assert outcome.handled is True
        assert outcome.parse_error == "unknown_ticket_id"
        assert outcome.deviation_report is None

    @pytest.mark.asyncio
    async def test_mismatch_records_daily_and_deviation(self) -> None:
        tickets = _InMemoryTicketRepo()
        tickets.tickets[_TICKET_ID] = _open_ticket()
        orchestrator, _, _, daily, _ = _build_orchestrator(tickets=tickets)
        outcome = await orchestrator.handle_reply(
            f"对账差异 {_TICKET_ID} 现金 199999.50 持仓 510300 1000股 成本 3.85"
        )
        assert outcome.handled is True
        assert outcome.kind == ReconciliationReplyKind.MISMATCH
        assert outcome.deviation_report is not None
        # DailyReconciliation persisted for the trade_date.
        assert daily.dailies["2026-05-16"].reported_cash == 199999.50

    @pytest.mark.asyncio
    async def test_amend_decides_ticket_resolved_amended(self) -> None:
        tickets = _InMemoryTicketRepo()
        tickets.tickets[_TICKET_ID] = _open_ticket()
        orchestrator, feishu, _, _, applier = _build_orchestrator(
            tickets=tickets
        )
        outcome = await orchestrator.handle_reply(
            f"对账更正 {_TICKET_ID} 现金 199999.50 持仓 510300 1000股 成本 3.85"
        )
        assert outcome.handled is True
        assert (
            outcome.ticket_status
            == ReconciliationTicketStatus.RESOLVED_AMENDED
        )
        assert len(applier.calls) == 1
        # Decision result message also dispatched.
        # 2 sends total: init wasn't called, so only the result.
        assert any("【QuantMind 对账已落账】" in c[1] for c in feishu.calls)

    @pytest.mark.asyncio
    async def test_resolve_user_decides_user_as_truth(self) -> None:
        tickets = _InMemoryTicketRepo()
        tickets.tickets[_TICKET_ID] = _open_ticket()
        orchestrator, _, _, _, applier = _build_orchestrator(
            tickets=tickets
        )
        # P0-5 §1.3.1.4 — 全角中文冒号 U+FF1A, never ASCII.
        outcome = await orchestrator.handle_reply(
            "对账采纳：用户回报 " + _TICKET_ID
        )
        assert (
            outcome.ticket_status
            == ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH
        )
        assert len(applier.calls) == 1

    @pytest.mark.asyncio
    async def test_resolve_system_decides_system_as_truth(self) -> None:
        tickets = _InMemoryTicketRepo()
        tickets.tickets[_TICKET_ID] = _open_ticket()
        orchestrator, _, _, _, applier = _build_orchestrator(
            tickets=tickets
        )
        outcome = await orchestrator.handle_reply(
            "对账采纳：系统镜像 " + _TICKET_ID
        )
        assert (
            outcome.ticket_status
            == ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH
        )
        assert len(applier.calls) == 1


# -----------------------------------------------------------------------------
# 3. decide_ticket — write endpoint surface
# -----------------------------------------------------------------------------


class TestDecideTicket:
    @pytest.mark.asyncio
    async def test_user_as_truth_applies(self) -> None:
        tickets = _InMemoryTicketRepo()
        tickets.tickets[_TICKET_ID] = _open_ticket()
        orchestrator, feishu, _, _, applier = _build_orchestrator(
            tickets=tickets
        )
        result = await orchestrator.decide_ticket(
            _TICKET_ID,
            resolution=ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
        )
        assert isinstance(result, DecisionResult)
        assert (
            result.status
            == ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH
        )
        assert applier.calls
        # Outbound summary message sent.
        assert any("【QuantMind 对账已落账】" in c[1] for c in feishu.calls)

    @pytest.mark.asyncio
    async def test_amended_requires_snapshot(self) -> None:
        tickets = _InMemoryTicketRepo()
        tickets.tickets[_TICKET_ID] = _open_ticket()
        orchestrator, *_ = _build_orchestrator(tickets=tickets)
        with pytest.raises(ValueError, match="amended_snapshot"):
            await orchestrator.decide_ticket(
                _TICKET_ID,
                resolution=ReconciliationTicketStatus.RESOLVED_AMENDED,
            )

    @pytest.mark.asyncio
    async def test_unknown_ticket_raises_keyerror(self) -> None:
        orchestrator, *_ = _build_orchestrator()
        with pytest.raises(KeyError, match="unknown ticket"):
            await orchestrator.decide_ticket(
                _TICKET_ID,
                resolution=ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
            )

    @pytest.mark.asyncio
    async def test_terminal_ticket_rejected(self) -> None:
        tickets = _InMemoryTicketRepo()
        ticket = _open_ticket()
        terminal = ticket.model_copy(
            update={
                "status": ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
                "resolved_at": datetime(2026, 5, 16, 17, 0, 0, tzinfo=UTC),
            }
        )
        tickets.tickets[_TICKET_ID] = terminal
        orchestrator, *_ = _build_orchestrator(tickets=tickets)
        with pytest.raises(ValueError, match="terminal"):
            await orchestrator.decide_ticket(
                _TICKET_ID,
                resolution=ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
            )

    @pytest.mark.asyncio
    async def test_open_to_resolved_writes_to_repo(self) -> None:
        tickets = _InMemoryTicketRepo()
        tickets.tickets[_TICKET_ID] = _open_ticket()
        orchestrator, *_ = _build_orchestrator(tickets=tickets)
        await orchestrator.decide_ticket(
            _TICKET_ID,
            resolution=ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
        )
        saved = tickets.tickets[_TICKET_ID]
        assert (
            saved.status
            == ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH
        )
        assert saved.resolved_at is not None

    @pytest.mark.asyncio
    async def test_non_resolved_status_rejected(self) -> None:
        orchestrator, *_ = _build_orchestrator()
        with pytest.raises(ValueError, match="cannot transition"):
            await orchestrator.decide_ticket(
                _TICKET_ID,
                resolution=ReconciliationTicketStatus.OPEN,
            )


# -----------------------------------------------------------------------------
# Red lines
# -----------------------------------------------------------------------------


class TestRedLines:
    def test_no_llm_imports(self) -> None:
        import ast
        import pathlib

        path = pathlib.Path("backend/integrations/feishu/reconciliation.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                parts = (node.module or "").split(".")
                assert not (
                    parts[:1] == ["backend"]
                    and len(parts) >= 2
                    and parts[1] in {"llm", "agents", "mirofish"}
                ), f"forbidden import {node.module}"

    @pytest.mark.asyncio
    async def test_alert_chat_id_never_used(self) -> None:
        """Decision + result messages flow to decision_chat_id only."""
        tickets = _InMemoryTicketRepo()
        tickets.tickets[_TICKET_ID] = _open_ticket()
        orchestrator, feishu, _, _, _ = _build_orchestrator(tickets=tickets)
        await orchestrator.initiate_reconciliation(
            ticket_id=_TICKET_ID,
            trade_date="2026-05-16",
            snapshot=_snapshot(),
        )
        await orchestrator.decide_ticket(
            _TICKET_ID,
            resolution=ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
        )
        for chat_id, _, _ in feishu.calls:
            assert chat_id == _VALID_CHAT  # always decision chat
