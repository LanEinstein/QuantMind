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
    REPORT_SCHEMA_V1_OWNER_FEE,
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
    side_zh: str = "买入",
    channel: ExecutionReportChannel = ExecutionReportChannel.FEISHU,
) -> ExecutionReport:
    # P0-4-amendment-2026-05-27 §2.4 — v2 (current) FILLED: owner reports
    # 「price + volume」only; the system derives the fee-inclusive cost.
    # 600519 is SH_MAIN, so 100@1800 → gross 180_000, commission
    # max(180_000*0.00015, 5) = 27.0, transfer fee 0 → net 180_027.
    return ExecutionReport(
        report_id="r-1",
        instruction_id=instruction_id,
        kind=ExecutionReportKind.FILLED,
        channel=channel,
        side_zh=side_zh,
        stock_code="600519",
        filled_volume=filled_volume,
        fill_price=fill_price,
        raw_text="FILLED 600519 买入 100@1800.0",
        received_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
        parsed_at=dt.datetime(2026, 5, 15, 10, 5, 1, tzinfo=SHANGHAI),
    )


def _filled_report_v1(
    *,
    fee: float = 5.0,
    fill_price: float = 1800.0,
    filled_volume: int = 100,
) -> ExecutionReport:
    """Legacy v1 report (owner-reported fee). Kept for backward-compat
    coverage — never produced by the current parser."""
    return ExecutionReport(
        report_id="r-1-v1",
        instruction_id="QM-20260515-100000-600519-BUY-001",
        kind=ExecutionReportKind.FILLED,
        channel=ExecutionReportChannel.FEISHU,
        report_schema_version=REPORT_SCHEMA_V1_OWNER_FEE,
        side_zh="买入",
        stock_code="600519",
        filled_volume=filled_volume,
        fill_price=fill_price,
        fee=fee,
        raw_text="FILLED 600519 买入 100@1800.0 手续费 5.0",
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

        # v2: cash dropped by gross 180_000 + system commission 27 (no
        # transfer fee on SH_MAIN) = 180_027 (P0-4-amendment §2.2).
        assert isinstance(result, ApplyResult)
        assert result.cash_delta == pytest.approx(-180_027.0)
        account = await env.broker.get_account()
        assert account.available_cash == pytest.approx(1_000_000.0 - 180_027.0)
        positions = await env.broker.get_positions()
        assert len(positions) == 1
        assert positions[0].code == "600519"
        assert positions[0].volume == 100
        # Fee-inclusive cost basis: 180_027 / 100 = 1800.27.
        assert positions[0].cost_price == pytest.approx(1800.27)
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
        # PARTIAL is always v2 — the system now computes the fee on the
        # filled leg (previously applied fee=0). 100@1800 SH_MAIN →
        # gross 180_000 + commission 27 = 180_027 (P0-4-amendment §2.2).
        assert result.cash_delta == pytest.approx(-180_027.0)
        positions = await env.broker.get_positions()
        assert positions[0].volume == 100


# ---------------------------------------------------------------------------
# ExecutionReportApplier — durable report_id idempotency (U-D4)
# ---------------------------------------------------------------------------


class TestExecutionReportIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_report_id_does_not_double_mutate(
        self, env: _Env
    ) -> None:
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )
        first = await applier.apply(_filled_report(), side_is_buy=True)
        after_first = await env.broker.get_account()

        # Same report_id ("r-1") submitted again — e.g. a Feishu
        # redelivery that slipped past the event_id dedupe, or a
        # frontend double-submit.
        second = await applier.apply(_filled_report(), side_is_buy=True)
        after_second = await env.broker.get_account()

        assert first.reason == "execution_report_applied"
        assert second.reason == "execution_report_duplicate_skipped"
        assert second.cash_delta == 0.0
        assert second.broker_event_sequence is None
        # Broker mutated exactly once.
        assert after_second.available_cash == pytest.approx(
            after_first.available_cash
        )
        positions = await env.broker.get_positions()
        assert len(positions) == 1
        assert positions[0].volume == 100
        # Only one EXECUTION_REPORT_APPLIED event persisted.
        applied_events = [
            d
            for d in env.event_coll.docs
            if d["event_type"]
            == BrokerEventType.EXECUTION_REPORT_APPLIED.value
        ]
        assert len(applied_events) == 1

    @pytest.mark.asyncio
    async def test_distinct_report_ids_same_content_deduped(
        self, env: _Env
    ) -> None:
        # The real Feishu/frontend path mints a FRESH report_id per parse,
        # so a double-click / redelivery arrives with a DIFFERENT
        # report_id but identical content — the content key must still
        # dedupe it (Codex U-D4 P1).
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )
        first = await applier.apply(
            _filled_report(), side_is_buy=True
        )
        # Same content, different random report_id (as the parser produces).
        dup = _filled_report()
        dup = dup.model_copy(update={"report_id": "erp-totally-different"})
        second = await applier.apply(dup, side_is_buy=True)

        assert first.reason == "execution_report_applied"
        assert second.reason == "execution_report_duplicate_skipped"
        applied_events = [
            d
            for d in env.event_coll.docs
            if d["event_type"]
            == BrokerEventType.EXECUTION_REPORT_APPLIED.value
        ]
        assert len(applied_events) == 1

    @pytest.mark.asyncio
    async def test_different_content_not_deduped(self, env: _Env) -> None:
        # A correction with a different fill price is a genuinely
        # different reported outcome and must NOT be suppressed.
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )
        await applier.apply(_filled_report(fill_price=1800.0), side_is_buy=True)
        second = await applier.apply(
            _filled_report(fill_price=1801.0), side_is_buy=True
        )
        assert second.reason == "execution_report_applied"

    @pytest.mark.asyncio
    async def test_post_mutation_failure_keeps_claim(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If the BrokerEvent append fails AFTER the broker has mutated,
        # the claim must NOT be released — a retry has to be rejected so
        # the cash/position delta is never applied twice (Codex U-D4 P1).
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )

        async def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("event store down")

        monkeypatch.setattr(env.event_store, "append", _boom)
        with pytest.raises(RuntimeError, match="event store down"):
            await applier.apply(_filled_report(), side_is_buy=True)

        after_fail = await env.broker.get_account()
        # The broker mutated exactly once despite the post-mutation error.
        assert after_fail.available_cash == pytest.approx(
            1_000_000.0 - 180_027.0
        )

        # Retry the same report — the held claim short-circuits to a
        # no-op BEFORE _apply_fill, so the (still-broken) append is never
        # reached and the broker is not double-mutated.
        retry = await applier.apply(_filled_report(), side_is_buy=True)
        assert retry.reason == "execution_report_duplicate_skipped"
        final = await env.broker.get_account()
        assert final.available_cash == pytest.approx(after_fail.available_cash)

    @pytest.mark.asyncio
    async def test_release_on_failure_allows_retry(self, env: _Env) -> None:
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )
        sell = ExecutionReport(
            report_id="r-sell-retry",
            instruction_id="QM-20260515-100000-600519-SELL-001",
            kind=ExecutionReportKind.FILLED,
            channel=ExecutionReportChannel.FEISHU,
            side_zh="卖出",
            stock_code="600519",
            filled_volume=100,
            fill_price=1810.0,
            raw_text="FILLED 600519 卖出 100@1810",
            received_at=dt.datetime(2026, 5, 15, 10, 6, tzinfo=SHANGHAI),
            parsed_at=dt.datetime(2026, 5, 15, 10, 6, 1, tzinfo=SHANGHAI),
        )
        # First attempt fails — no position to sell. The claim must be
        # released so the same report_id can be retried after the
        # position exists.
        with pytest.raises(ValueError, match="SELL"):
            await applier.apply(sell, side_is_buy=False)

        await applier.apply(_filled_report(), side_is_buy=True)  # buy 100

        retry = await applier.apply(sell, side_is_buy=False)
        assert retry.reason == "execution_report_applied"
        positions = await env.broker.get_positions()
        assert len(positions) == 0 or positions[0].volume == 0

    @pytest.mark.asyncio
    async def test_unfilled_duplicate_skipped(self, env: _Env) -> None:
        applier = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store
        )
        first = await applier.apply(_unfilled_report(), side_is_buy=True)
        second = await applier.apply(_unfilled_report(), side_is_buy=True)
        assert first.reason == "execution_report_unfilled"
        assert second.reason == "execution_report_duplicate_skipped"
        # The duplicate UNFILLED writes no second audit row.
        submitted = [
            d
            for d in env.audit_coll.documents
            if d["event_type"]
            == AuditEventType.EXECUTION_REPORT_SUBMITTED.value
        ]
        assert len(submitted) == 1

    @pytest.mark.asyncio
    async def test_shared_guard_dedupes_across_applier_instances(
        self, env: _Env
    ) -> None:
        # Models the production Redis guard surviving a process restart:
        # a fresh applier instance backed by the same durable store still
        # recognises an already-applied report_id.
        from backend.broker.applied_report_guard import (
            InMemoryAppliedReportGuard,
        )

        guard = InMemoryAppliedReportGuard()
        applier1 = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store, applied_guard=guard
        )
        await applier1.apply(_filled_report(), side_is_buy=True)
        before = await env.broker.get_account()

        applier2 = ExecutionReportApplier(
            env.broker, env.event_store, env.audit_store, applied_guard=guard
        )
        result = await applier2.apply(_filled_report(), side_is_buy=True)
        after = await env.broker.get_account()

        assert result.reason == "execution_report_duplicate_skipped"
        assert after.available_cash == pytest.approx(before.available_cash)


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
            side_is_buy=True,
            traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
            report_id="r-1",
            kind="FILLED",
            report_schema_version=2,
        )
        trades = await broker.get_trades()
        assert len(trades) == 1
        assert trades[0].code == "600519"
        assert trades[0].direction == OrderDirection.BUY
        # v2: gross 180_000 + system commission 27 = 180_027.
        assert out["cash_delta"] == pytest.approx(-180_027.0)
        assert out["commission"] == pytest.approx(27.0)
        assert out["report_schema_version"] == 2

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
            side_is_buy=True,
            traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
            report_id="r-pre",
            kind="FILLED",
            report_schema_version=2,
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
                fill_price=1800.0, side_is_buy=True,
                traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
                report_id="r", kind="FILLED", report_schema_version=2,
            )
        with pytest.raises(ValueError, match="fill_price"):
            await broker.apply_external_fill(
                order_id_hint="x", code="600519", volume=100,
                fill_price=0.0, side_is_buy=True,
                traded_at=dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI),
                report_id="r", kind="FILLED", report_schema_version=2,
            )


# ---------------------------------------------------------------------------
# P0-4-amendment-2026-05-27 — v1 (owner fee) vs v2 (system-computed fee)
# ---------------------------------------------------------------------------


def _fresh_broker() -> MockBroker:
    return MockBroker(
        config=BrokerConfig(initial_capital=1_000_000.0),
        now_func=lambda: dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI),
    )


_TRADED_AT = dt.datetime(2026, 5, 15, 10, 5, tzinfo=SHANGHAI)


class TestExternalFillCostSchemas:
    @pytest.mark.asyncio
    async def test_v2_buy_sh_main_commission_only(self) -> None:
        broker = _fresh_broker()
        out = await broker.apply_external_fill(
            order_id_hint="x", code="600519", volume=100, fill_price=1800.0,
            side_is_buy=True, traded_at=_TRADED_AT, report_id="r",
            kind="FILLED", report_schema_version=2,
        )
        # gross 180_000, commission max(180_000*0.00015, 5) = 27.0,
        # SH_MAIN transfer fee 0 → net 180_027.
        assert out["commission"] == pytest.approx(27.0)
        assert out["transfer_fee"] == 0.0
        assert out["stamp_tax"] == 0.0
        assert out["net"] == pytest.approx(180_027.0)
        assert out["cash_delta"] == pytest.approx(-180_027.0)
        positions = await broker.get_positions()
        # Fee-inclusive cost basis 180_027 / 100 = 1800.27.
        assert positions[0].cost_price == pytest.approx(1800.27)
        trades = await broker.get_trades()
        assert trades[0].commission == pytest.approx(27.0)
        assert trades[0].slippage_cost == 0.0  # no simulation slippage

    @pytest.mark.asyncio
    async def test_v2_buy_sz_main_includes_transfer_fee(self) -> None:
        broker = _fresh_broker()
        out = await broker.apply_external_fill(
            order_id_hint="x", code="000001", volume=1_000, fill_price=10.0,
            side_is_buy=True, traded_at=_TRADED_AT, report_id="r",
            kind="FILLED", report_schema_version=2,
        )
        # gross 10_000, commission max(10_000*0.00015, 5) = 5.0 (floor),
        # SZ transfer fee 10_000*0.0000341 = 0.34, net 10_005.34.
        assert out["commission"] == pytest.approx(5.0)
        assert out["transfer_fee"] == pytest.approx(0.34)
        assert out["net"] == pytest.approx(10_005.34)
        assert out["cash_delta"] == pytest.approx(-10_005.34)

    @pytest.mark.asyncio
    async def test_v2_sell_subtracts_commission_stamp_transfer(self) -> None:
        broker = _fresh_broker()
        await broker.apply_external_fill(
            order_id_hint="x", code="000001", volume=1_000, fill_price=10.0,
            side_is_buy=True, traded_at=_TRADED_AT, report_id="r-buy",
            kind="FILLED", report_schema_version=2,
        )
        out = await broker.apply_external_fill(
            order_id_hint="x", code="000001", volume=1_000, fill_price=11.0,
            side_is_buy=False, traded_at=_TRADED_AT, report_id="r-sell",
            kind="FILLED", report_schema_version=2,
        )
        # gross 11_000; commission max(11_000*0.00015,5)=5.0; stamp
        # 11_000*0.001=11.0; transfer 11_000*0.0000341=0.38;
        # net = 11_000 - 5 - 11 - 0.38 = 10_983.62.
        assert out["commission"] == pytest.approx(5.0)
        assert out["stamp_tax"] == pytest.approx(11.0)
        assert out["transfer_fee"] == pytest.approx(0.38)
        assert out["net"] == pytest.approx(10_983.62)
        assert out["cash_delta"] == pytest.approx(10_983.62)

    @pytest.mark.asyncio
    async def test_v2_weighted_average_blend_is_fee_inclusive(self) -> None:
        broker = _fresh_broker()
        await broker.apply_external_fill(
            order_id_hint="x", code="600519", volume=100, fill_price=1800.0,
            side_is_buy=True, traded_at=_TRADED_AT, report_id="r1",
            kind="FILLED", report_schema_version=2,
        )
        await broker.apply_external_fill(
            order_id_hint="x", code="600519", volume=100, fill_price=1820.0,
            side_is_buy=True, traded_at=_TRADED_AT, report_id="r2",
            kind="FILLED", report_schema_version=2,
        )
        positions = await broker.get_positions()
        # basis1 = 1800.27 (net 180_027/100); basis2 = 1820.273
        # (net 182_027.3/100). Weighted avg over 200 shares = 1810.27.
        assert positions[0].volume == 200
        assert positions[0].cost_price == pytest.approx(1810.27)

    @pytest.mark.asyncio
    async def test_v2_min_commission_floor(self) -> None:
        broker = _fresh_broker()
        out = await broker.apply_external_fill(
            order_id_hint="x", code="600519", volume=100, fill_price=1.0,
            side_is_buy=True, traded_at=_TRADED_AT, report_id="r",
            kind="FILLED", report_schema_version=2,
        )
        # gross 100, 100*0.00015 = 0.015 << 5 → commission floored at 5.0.
        assert out["commission"] == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_v2_rejects_owner_fee(self) -> None:
        broker = _fresh_broker()
        with pytest.raises(ValueError, match="must not receive"):
            await broker.apply_external_fill(
                order_id_hint="x", code="600519", volume=100,
                fill_price=1800.0, side_is_buy=True, traded_at=_TRADED_AT,
                report_id="r", kind="FILLED", report_schema_version=2, fee=5.0,
            )

    @pytest.mark.asyncio
    async def test_v1_legacy_owner_fee_path(self) -> None:
        broker = _fresh_broker()
        out = await broker.apply_external_fill(
            order_id_hint="x", code="600519", volume=100, fill_price=1800.0,
            side_is_buy=True, traded_at=_TRADED_AT, report_id="r",
            kind="FILLED", report_schema_version=REPORT_SCHEMA_V1_OWNER_FEE,
            fee=5.0,
        )
        # v1: owner fee applied verbatim as commission; cost basis = raw
        # fill price (no fee folded in) — the legacy behaviour.
        assert out["commission"] == pytest.approx(5.0)
        assert out["net"] == pytest.approx(180_005.0)
        assert out["cash_delta"] == pytest.approx(-180_005.0)
        positions = await broker.get_positions()
        assert positions[0].cost_price == pytest.approx(1800.0)

    @pytest.mark.asyncio
    async def test_v1_requires_fee(self) -> None:
        broker = _fresh_broker()
        with pytest.raises(ValueError, match="requires fee"):
            await broker.apply_external_fill(
                order_id_hint="x", code="600519", volume=100,
                fill_price=1800.0, side_is_buy=True, traded_at=_TRADED_AT,
                report_id="r", kind="FILLED",
                report_schema_version=REPORT_SCHEMA_V1_OWNER_FEE,
            )

    @pytest.mark.asyncio
    async def test_v1_vs_v2_idempotency_keys_differ(self) -> None:
        # Same fill reported under v1 vs v2 must NOT collide in the
        # idempotency guard (version is part of the key).
        from backend.broker.appliers import compute_idempotency_key

        v1 = _filled_report_v1()
        v2 = _filled_report()
        assert compute_idempotency_key(v1) != compute_idempotency_key(v2)
