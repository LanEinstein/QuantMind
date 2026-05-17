"""ExecutionReportApplier + ReconciliationApplier unit tests (E-004)."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import AuditActor, AuditEventType
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.appliers import (
    ApplyResult,
    ExecutionReportApplier,
    ReconciliationApplier,
)
from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig, OrderDirection
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore
from backend.models.execution import (
    ExecutionReport,
    ExecutionReportChannel,
    ExecutionReportKind,
)
from backend.models.reconciliation import (
    DailyReconciliation,
    DeviationReport,
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
    ReportedPosition,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# Reusable fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    @asynccontextmanager
    async def start_transaction(self) -> AsyncIterator[None]:
        yield

    async def commit_transaction(self) -> None:
        return None

    async def abort_transaction(self) -> None:
        return None

    async def end_session(self) -> None:
        return None


@dataclass
class _FakeClient:
    async def start_session(self) -> _FakeSession:
        return _FakeSession()


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int) -> _FakeCursor:
        reverse = direction == -1
        self._docs = sorted(self._docs, key=lambda d: d.get(field, 0), reverse=reverse)
        return self

    def limit(self, n: int) -> _FakeCursor:
        self._docs = self._docs[:n]
        return self

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any], session=None) -> None:
        self.docs.append(dict(document))

    def find(self, filter=None, projection=None) -> _FakeCursor:
        rows = list(self.docs)
        if filter:
            gt = filter.get("sequence", {})
            if isinstance(gt, dict) and "$gt" in gt:
                threshold = gt["$gt"]
                rows = [r for r in rows if r.get("sequence", 0) > threshold]
        return _FakeCursor(rows)

    async def find_one(self, filter=None) -> dict[str, Any] | None:
        return self.docs[0] if self.docs else None


@dataclass
class _Env:
    broker: MockBroker
    event_store: BrokerEventStore
    audit_store: AuditStore
    audit_coll: InMemoryAuditCollection
    event_coll: _FakeCollection = field(default_factory=_FakeCollection)


@pytest.fixture()
def env(tmp_path: Path) -> _Env:
    config = BrokerConfig(initial_capital=1_000_000.0)
    broker = MockBroker(
        config=config,
        now_func=lambda: dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI),
    )
    client = _FakeClient()
    event_coll = _FakeCollection()
    event_store = BrokerEventStore(client, event_coll)
    audit_coll = InMemoryAuditCollection()
    audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
    return _Env(
        broker=broker,
        event_store=event_store,
        audit_store=audit_store,
        audit_coll=audit_coll,
        event_coll=event_coll,
    )


def _filled_report(
    *,
    instruction_id: str = "QM-20260515-100000-600519-BUY-001",
    fill_price: float = 1800.0,
    filled_volume: int = 100,
    fee: float = 5.0,
    side_zh: str = "买入",
    channel: ExecutionReportChannel = ExecutionReportChannel.FEISHU,
) -> ExecutionReport:
    return ExecutionReport(
        report_id="r-1",
        instruction_id=instruction_id,
        kind=ExecutionReportKind.FILLED,
        channel=channel,
        side_zh=side_zh,
        stock_code="600519",
        filled_volume=filled_volume,
        fill_price=fill_price,
        fee=fee,
        raw_text="FILLED 600519 买入 100@1800.0",
        received_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
        parsed_at=dt.datetime(2026, 5, 15, 10, 5, 1, tzinfo=SHANGHAI),
    )


def _unfilled_report() -> ExecutionReport:
    return ExecutionReport(
        report_id="r-2",
        instruction_id="QM-20260515-100000-600519-BUY-001",
        kind=ExecutionReportKind.UNFILLED,
        channel=ExecutionReportChannel.FRONTEND,
        reason="券商系统故障",
        raw_text="UNFILLED 券商系统故障",
        received_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
        parsed_at=dt.datetime(2026, 5, 15, 10, 5, 1, tzinfo=SHANGHAI),
    )


_DEFAULT_TICKET_STATUS = ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH


def _ticket(
    *,
    status: ReconciliationTicketStatus = _DEFAULT_TICKET_STATUS,
    amended_cash: float | None = None,
) -> ReconciliationTicket:
    dev = DeviationReport(
        ticket_id="RECON-20260515-001",
        overall_passed=False,
        deviations=(),
    )
    amended_snapshot = (
        MockBrokerSnapshot(
            cash=amended_cash,
            positions=(),
            snapshot_at=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        if amended_cash is not None
        else None
    )
    resolved_at = (
        dt.datetime(2026, 5, 15, 17, 0, tzinfo=SHANGHAI)
        if status != ReconciliationTicketStatus.OPEN
        else None
    )
    return ReconciliationTicket(
        ticket_id="RECON-20260515-001",
        trade_date="2026-05-15",
        created_at=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        deviation_report=dev,
        expected_snapshot_id="snap-1",
        actual_reconciliation_id="recon-1",
        status=status,
        resolved_at=resolved_at,
        amended_snapshot=amended_snapshot,
    )


# ---------------------------------------------------------------------------
# ExecutionReportApplier — FILLED / PARTIAL / UNFILLED
# ---------------------------------------------------------------------------


class TestExecutionReportApplier:
    @pytest.mark.asyncio
    async def test_filled_buy_deducts_cash_and_creates_position(
        self, env: _Env
    ) -> None:
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )

        result = await applier.apply(_filled_report(), side_is_buy=True)

        # Cash dropped by 1800*100 + 5 fee = 180_005.
        assert isinstance(result, ApplyResult)
        assert result.cash_delta == pytest.approx(-180_005.0)
        account = await env.broker.get_account()
        assert account.available_cash == pytest.approx(1_000_000.0 - 180_005.0)
        positions = await env.broker.get_positions()
        assert len(positions) == 1
        assert positions[0].code == "600519"
        assert positions[0].volume == 100
        # BrokerEvent persisted with the right type + correlation_id.
        assert any(
            doc["event_type"] == BrokerEventType.EXECUTION_REPORT_APPLIED.value
            and doc["correlation_id"] == "QM-20260515-100000-600519-BUY-001"
            for doc in env.event_coll.docs
        )
        # Audit row written under Category 1 with channel-matching actor.
        assert any(
            d["event_type"]
            == AuditEventType.EXECUTION_REPORT_SUBMITTED.value
            and d["actor"] == AuditActor.FEISHU_USER.value
            for d in env.audit_coll.documents
        )

    @pytest.mark.asyncio
    async def test_filled_sell_requires_existing_position(
        self, env: _Env
    ) -> None:
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )
        sell = ExecutionReport(
            report_id="r-sell",
            instruction_id="QM-20260515-100000-600519-SELL-001",
            kind=ExecutionReportKind.FILLED,
            channel=ExecutionReportChannel.FEISHU,
            side_zh="卖出",
            stock_code="600519",
            filled_volume=100,
            fill_price=1810.0,
            fee=5.0,
            raw_text="FILLED 600519 卖出 100@1810",
            received_at=dt.datetime(2026, 5, 15, 10, 6, tzinfo=SHANGHAI),
            parsed_at=dt.datetime(2026, 5, 15, 10, 6, 1, tzinfo=SHANGHAI),
        )
        with pytest.raises(ValueError, match="SELL"):
            await applier.apply(sell, side_is_buy=False)

    @pytest.mark.asyncio
    async def test_unfilled_is_a_no_op_on_broker_state(self, env: _Env) -> None:
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )
        before = await env.broker.get_account()
        result = await applier.apply(_unfilled_report(), side_is_buy=True)
        after = await env.broker.get_account()
        assert result.cash_delta == 0.0
        assert result.broker_event_sequence is None
        assert before.available_cash == after.available_cash
        # Audit row still written for the UNFILLED case.
        assert any(
            d["event_type"]
            == AuditEventType.EXECUTION_REPORT_SUBMITTED.value
            for d in env.audit_coll.documents
        )

    @pytest.mark.asyncio
    async def test_partial_uses_filled_volume_only(self, env: _Env) -> None:
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )
        partial = ExecutionReport(
            report_id="r-partial",
            instruction_id="QM-20260515-100000-600519-BUY-001",
            kind=ExecutionReportKind.PARTIAL,
            channel=ExecutionReportChannel.FRONTEND,
            side_zh="买入",
            stock_code="600519",
            filled_volume=100,
            remain_volume=100,
            fill_price=1800.0,
            raw_text="PARTIAL 600519 买入 100/200@1800",
            received_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
            parsed_at=dt.datetime(2026, 5, 15, 10, 5, 1, tzinfo=SHANGHAI),
        )
        result = await applier.apply(partial, side_is_buy=True)
        # PARTIAL has no fee field — applier applies just the gross.
        assert result.cash_delta == pytest.approx(-180_000.0)
        positions = await env.broker.get_positions()
        assert positions[0].volume == 100


# ---------------------------------------------------------------------------
# ReconciliationApplier — RESOLVED paths
# ---------------------------------------------------------------------------


class TestReconciliationApplier:
    @pytest.mark.asyncio
    async def test_open_ticket_raises(self, env: _Env) -> None:
        applier = ReconciliationApplier(
            env.broker, env.event_store, env.audit_store
        )
        with pytest.raises(ValueError, match="RESOLVED_"):
            await applier.reset_to_snapshot(
                _ticket(status=ReconciliationTicketStatus.OPEN)
            )

    @pytest.mark.asyncio
    async def test_user_as_truth_rewrites_state(self, env: _Env) -> None:
        # Seed prior state so we can see the rewrite.
        await env.broker.reset_to_snapshot(
            cash=500_000.0,
            positions=(
                ReportedPosition(code="600519", volume=200, cost_price=1800.0),
            ),
            reset_at=dt.datetime(2026, 5, 15, 9, 0, tzinfo=SHANGHAI),
            reason="seed",
        )

        daily = DailyReconciliation(
            ticket_id="RECON-20260515-001",
            trade_date="2026-05-15",
            received_at=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
            reported_cash=600_000.0,
            reported_positions=(
                ReportedPosition(code="000001", volume=1_000, cost_price=10.0),
            ),
            raw_text="user-reply",
        )
        applier = ReconciliationApplier(
            env.broker,
            env.event_store,
            env.audit_store,
            daily_reconciliations={daily.ticket_id: daily},
        )

        result = await applier.reset_to_snapshot(_ticket())
        assert result.reason == "reset_to_user_snapshot"
        account = await env.broker.get_account()
        assert account.available_cash == pytest.approx(600_000.0)
        positions = await env.broker.get_positions()
        assert {p.code: p.volume for p in positions} == {"000001": 1_000}
        # BrokerEvent type emitted + audit row written
        assert any(
            doc["event_type"] == BrokerEventType.RECONCILIATION_RESET.value
            for doc in env.event_coll.docs
        )
        assert any(
            d["event_type"]
            == AuditEventType.RECONCILIATION_TICKET_DECIDED.value
            for d in env.audit_coll.documents
        )

    @pytest.mark.asyncio
    async def test_amended_uses_ticket_snapshot(self, env: _Env) -> None:
        applier = ReconciliationApplier(
            env.broker, env.event_store, env.audit_store
        )
        result = await applier.reset_to_snapshot(
            _ticket(
                status=ReconciliationTicketStatus.RESOLVED_AMENDED,
                amended_cash=750_000.0,
            )
        )
        assert result.reason == "reset_to_amended_snapshot"
        account = await env.broker.get_account()
        assert account.available_cash == pytest.approx(750_000.0)

    @pytest.mark.asyncio
    async def test_system_as_truth_skips_state_change(self, env: _Env) -> None:
        applier = ReconciliationApplier(
            env.broker, env.event_store, env.audit_store
        )
        before = await env.broker.get_account()
        result = await applier.reset_to_snapshot(
            _ticket(status=ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH)
        )
        after = await env.broker.get_account()
        assert result.reason == "reset_skipped_system_as_truth"
        assert result.broker_event_sequence is None
        assert before.available_cash == after.available_cash
        # Audit row still written so the decision is auditable.
        assert any(
            d["event_type"]
            == AuditEventType.RECONCILIATION_TICKET_DECIDED.value
            for d in env.audit_coll.documents
        )

    @pytest.mark.asyncio
    async def test_user_as_truth_without_daily_lookup_raises(
        self, env: _Env
    ) -> None:
        applier = ReconciliationApplier(
            env.broker, env.event_store, env.audit_store
        )
        with pytest.raises(ValueError, match="DailyReconciliation"):
            await applier.reset_to_snapshot(_ticket())


# ---------------------------------------------------------------------------
# MockBroker external-write entries — direct mutation forbidden
# ---------------------------------------------------------------------------


class TestMockBrokerExternalWrites:
    @pytest.mark.asyncio
    async def test_apply_external_fill_records_trade(self) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI),
        )
        out = await broker.apply_external_fill(
            order_id_hint="QM-20260515-100000-600519-BUY-001",
            code="600519",
            volume=100,
            fill_price=1800.0,
            fee=5.0,
            side_is_buy=True,
            traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
            report_id="r-1",
            kind="FILLED",
        )
        trades = await broker.get_trades()
        assert len(trades) == 1
        assert trades[0].code == "600519"
        assert trades[0].direction == OrderDirection.BUY
        assert out["cash_delta"] == pytest.approx(-180_005.0)

    @pytest.mark.asyncio
    async def test_reset_to_snapshot_clears_and_rewrites(self) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI),
        )
        await broker.apply_external_fill(
            order_id_hint="qm-x",
            code="600519",
            volume=100,
            fill_price=1800.0,
            fee=5.0,
            side_is_buy=True,
            traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
            report_id="r-pre",
            kind="FILLED",
        )

        await broker.reset_to_snapshot(
            cash=750_000.0,
            positions=(
                ReportedPosition(code="000001", volume=2_000, cost_price=10.0),
            ),
            reset_at=dt.datetime(2026, 5, 15, 17, 0, tzinfo=SHANGHAI),
            reason="test",
        )
        account = await broker.get_account()
        assert account.available_cash == pytest.approx(750_000.0)
        positions = await broker.get_positions()
        assert {p.code: p.volume for p in positions} == {"000001": 2_000}

    @pytest.mark.asyncio
    async def test_apply_external_fill_negative_inputs_reject(self) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI),
        )
        with pytest.raises(ValueError, match="volume"):
            await broker.apply_external_fill(
                order_id_hint="x", code="600519", volume=0,
                fill_price=1800.0, fee=5.0, side_is_buy=True,
                traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
                report_id="r", kind="FILLED",
            )
        with pytest.raises(ValueError, match="fill_price"):
            await broker.apply_external_fill(
                order_id_hint="x", code="600519", volume=100,
                fill_price=0.0, fee=5.0, side_is_buy=True,
                traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
                report_id="r", kind="FILLED",
            )
