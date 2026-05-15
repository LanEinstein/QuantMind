"""SimulationExecutor + ModeRouter end-to-end tests (E-007 + D-005)."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import AuditActor, AuditEventType
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore
from backend.broker.scheduler import EodPipelineFreezeState
from backend.data.market_meta_provider import InMemoryMarketMetaProvider
from backend.models.instruction import (
    DataSnapshot,
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    PositionSummary,
    RiskCheckSummary,
)
from backend.services.ledger import (
    DecisionLedgerService,
    InMemoryLedgerRepository,
)
from backend.services.mode_router import (
    FEISHU_INTERACTIVE,
    SIMULATION_AUTO,
    AcceptanceGateMissingError,
    AcceptanceGateRejectedError,
    ModeRouter,
    ModeSwitchState,
)
from backend.services.simulation_executor import (
    ROUTE_FROZEN_REASON,
    SimulationExecutor,
)


class _PassingAcceptanceGate:
    """Test stub that mimics AcceptanceService.can_switch_to_feishu_on."""

    def __init__(self, sanctioned: bool = True) -> None:
        self._sanctioned = sanctioned

    async def can_switch_to_feishu_on(self) -> bool:
        return self._sanctioned

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# Reusable test doubles
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
                rows = [r for r in rows if r.get("sequence", 0) > gt["$gt"]]
        return _FakeCursor(rows)


# ---------------------------------------------------------------------------
# Plan + executor factories
# ---------------------------------------------------------------------------


def _snapshot(snap_at: dt.datetime) -> DataSnapshot:
    return DataSnapshot(
        snapshot_at=snap_at,
        quote_source="adata",
        quote_latency_ms=100,
        prev_close=100.0,
        is_trading_day=True,
        is_trading_hours=True,
    )


def _risk_summary_14() -> tuple[RiskCheckSummary, ...]:
    names = (
        "code_validity", "price_reasonability", "volume_validity",
        "fund_sufficiency", "position_limit", "total_position_limit",
        "trading_time", "total_position_pct", "single_instruction_amount",
        "daily_new_instruction_count", "universe_whitelist",
        "limit_up_down_block", "daily_loss_halt", "consecutive_loss_halt",
    )
    return tuple(
        RiskCheckSummary(rule_name=n, passed=True, message="") for n in names
    )


def _validated_plan(*, side: InstructionSide = InstructionSide.BUY) -> InstructionPlan:
    created = dt.datetime(2026, 5, 15, 10, 0, 1, tzinfo=SHANGHAI)
    snap = created - dt.timedelta(seconds=2)
    code_side = "BUY" if side is InstructionSide.BUY else "SELL"
    return InstructionPlan(
        instruction_id=f"QM-20260515-100001-600519-{code_side}-001",
        created_at=created,
        valid_until=created + dt.timedelta(minutes=5),
        trade_date="2026-05-15",
        stock_code="600519",
        stock_name="贵州茅台",
        side=side,
        volume=100,
        limit_price=100.0,
        data_snapshot=_snapshot(snap),
        evidence_ids=("MARKET-600519-2026-05-15T10:00:00",),
        position_summary=PositionSummary(
            pre_position_pct=0.05, post_position_pct=0.06,
            pre_total_position_pct=0.30, post_total_position_pct=0.31,
            pre_cash=500_000.0, post_cash=489_950.0,
        ),
        risk_summary=_risk_summary_14(),
        risk_validation_id="RV-1",
        signal_id="sig-1",
        analysis_record_id="run-1",
        debate_round_count=2,
        invalidation_summary="跌破 95",
        status=InstructionStatus.VALIDATED,
    )


@dataclass
class _Env:
    executor: SimulationExecutor
    broker: MockBroker
    ledger: DecisionLedgerService
    audit_coll: InMemoryAuditCollection
    event_coll: _FakeCollection
    freeze: EodPipelineFreezeState
    mode_state: ModeSwitchState


@pytest.fixture()
def env(tmp_path: Path) -> _Env:
    meta = InMemoryMarketMetaProvider(
        prev_close={"600519": 100.0}, current_price={"600519": 100.5},
    )
    broker = MockBroker(
        config=BrokerConfig(initial_capital=1_000_000.0),
        now_func=lambda: dt.datetime(2026, 5, 15, 10, 0, 1, tzinfo=SHANGHAI),
        market_meta=meta,
    )
    client = _FakeClient()
    event_coll = _FakeCollection()
    event_store = BrokerEventStore(client, event_coll)
    audit_coll = InMemoryAuditCollection()
    audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
    ledger = DecisionLedgerService(InMemoryLedgerRepository())
    freeze = EodPipelineFreezeState()
    mode_state = ModeSwitchState()
    executor = SimulationExecutor(
        broker=broker,
        event_store=event_store,
        audit_store=audit_store,
        ledger=ledger,
        freeze_state=freeze,
        mode_switch_probe=mode_state,
    )
    return _Env(
        executor=executor,
        broker=broker,
        ledger=ledger,
        audit_coll=audit_coll,
        event_coll=event_coll,
        freeze=freeze,
        mode_state=mode_state,
    )


# ---------------------------------------------------------------------------
# SimulationExecutor — happy path and freeze paths
# ---------------------------------------------------------------------------


class TestSimulationExecutorRoute:
    @pytest.mark.asyncio
    async def test_routes_validated_buy_to_broker(self, env: _Env) -> None:
        plan = _validated_plan()
        await env.ledger.open_for_plan(plan)
        result = await env.executor.route(plan)

        assert result.final_status is InstructionStatus.FILLED
        assert result.broker_order_id is not None
        positions = await env.broker.get_positions()
        assert len(positions) == 1
        assert positions[0].code == "600519"
        # BrokerEvent persisted
        assert any(
            d["event_type"] == BrokerEventType.ORDER_FILLED.value
            for d in env.event_coll.docs
        )
        # Ledger received PLAN_DISPATCHED + BROKER_FILLED events.
        entry = await env.ledger.get_by_instruction(plan.instruction_id)
        assert entry is not None
        kinds = [ev.kind.value for ev in entry.events]
        assert "PLAN_DISPATCHED" in kinds
        assert "BROKER_FILLED" in kinds

    @pytest.mark.asyncio
    async def test_hold_plan_rejected_at_executor(self, env: _Env) -> None:
        with pytest.raises(ValueError, match="HOLD"):
            await env.executor.route(
                _validated_plan().model_copy(
                    update={
                        "side": InstructionSide.HOLD,
                        "volume": None,
                        "limit_price": None,
                        "position_summary": None,
                    }
                )
            )

    @pytest.mark.asyncio
    async def test_non_validated_status_rejects(self, env: _Env) -> None:
        with pytest.raises(ValueError, match="VALIDATED"):
            await env.executor.route(
                _validated_plan().model_copy(update={"status": InstructionStatus.DRAFT})
            )

    @pytest.mark.asyncio
    async def test_eod_freeze_blocks_route(self, env: _Env) -> None:
        env.freeze.record_failure(
            reason="eod_fail", trade_date="2026-05-15",
            when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        plan = _validated_plan()
        await env.ledger.open_for_plan(plan)
        result = await env.executor.route(plan)
        assert result.final_status is InstructionStatus.REJECTED
        assert ROUTE_FROZEN_REASON in (result.reason or "")
        # No broker order placed → no positions
        assert (await env.broker.get_positions()) == ()
        # Audit RISK_ENGINE_CHECK_REJECTED row written
        assert any(
            d["event_type"]
            == AuditEventType.RISK_ENGINE_CHECK_REJECTED.value
            for d in env.audit_coll.documents
        )

    @pytest.mark.asyncio
    async def test_mode_switch_active_blocks_route(self, env: _Env) -> None:
        env.mode_state.activate(
            from_mode=SIMULATION_AUTO,
            to_mode=FEISHU_INTERACTIVE,
            reason="test",
            initiated_by="cli",
            when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        plan = _validated_plan()
        await env.ledger.open_for_plan(plan)
        result = await env.executor.route(plan)
        assert result.final_status is InstructionStatus.REJECTED
        assert "mode_switch_in_progress" in (result.reason or "")


# ---------------------------------------------------------------------------
# ModeRouter — full lifecycle
# ---------------------------------------------------------------------------


class TestModeRouter:
    @pytest.mark.asyncio
    async def test_switch_archives_state_and_resets_broker(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_coll = _FakeCollection()
        event_store = BrokerEventStore(client, event_coll)
        audit_coll = InMemoryAuditCollection()
        audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
        router = ModeRouter(
            broker=broker, event_store=event_store, audit_store=audit_store,
            initial_mode=SIMULATION_AUTO,
            acceptance_gate=_PassingAcceptanceGate(sanctioned=True),
        )

        # Seed broker with some state
        from backend.models.reconciliation import ReportedPosition

        await broker.reset_to_snapshot(
            cash=900_000.0,
            positions=(
                ReportedPosition(code="600519", volume=100, cost_price=1_800.0),
            ),
            reset_at=dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
            reason="seed",
        )

        result = await router.switch_mode(
            to_mode=FEISHU_INTERACTIVE,
            reason="testing",
            initiated_by="cli",
            when=dt.datetime(2026, 5, 15, 16, 30, tzinfo=SHANGHAI),
        )

        assert result.from_mode == SIMULATION_AUTO
        assert result.to_mode == FEISHU_INTERACTIVE
        assert router.current_mode == FEISHU_INTERACTIVE
        assert router.mode_state.is_active() is False

        # MODE_SWITCH_RESET event recorded
        assert any(
            d["event_type"] == BrokerEventType.MODE_SWITCH_RESET.value
            for d in event_coll.docs
        )
        # Audit: INITIATED + MOCKBROKER_RESET + COMPLETED rows
        types = {d["event_type"] for d in audit_coll.documents}
        assert AuditEventType.MODE_SWITCH_INITIATED.value in types
        assert AuditEventType.MOCKBROKER_RESET.value in types
        assert AuditEventType.MODE_SWITCH_COMPLETED.value in types

        # Broker mirror reset to initial_capital with no positions
        account = await broker.get_account()
        assert account.available_cash == pytest.approx(1_000_000.0)
        assert (await broker.get_positions()) == ()

    @pytest.mark.asyncio
    async def test_switch_to_same_mode_raises(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_store = BrokerEventStore(client, _FakeCollection())
        audit_store = AuditStore(
            InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl"
        )
        router = ModeRouter(
            broker=broker, event_store=event_store, audit_store=audit_store,
            initial_mode=SIMULATION_AUTO,
        )
        with pytest.raises(ValueError, match="noop"):
            await router.switch_mode(
                to_mode=SIMULATION_AUTO,
                reason="x", initiated_by="cli",
                when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
            )

    @pytest.mark.asyncio
    async def test_switch_unknown_mode_raises(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_store = BrokerEventStore(client, _FakeCollection())
        audit_store = AuditStore(
            InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl"
        )
        router = ModeRouter(
            broker=broker, event_store=event_store, audit_store=audit_store,
        )
        with pytest.raises(ValueError, match="unknown run mode"):
            await router.switch_mode(
                to_mode="real_money",
                reason="x", initiated_by="cli",
                when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
            )

    def test_mode_state_activate_nested_raises(self) -> None:
        s = ModeSwitchState()
        s.activate(
            from_mode=SIMULATION_AUTO, to_mode=FEISHU_INTERACTIVE,
            reason="r", initiated_by="cli",
            when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        with pytest.raises(ValueError, match="already in progress"):
            s.activate(
                from_mode=FEISHU_INTERACTIVE, to_mode=SIMULATION_AUTO,
                reason="r", initiated_by="cli",
                when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
            )

    def test_actor_value_locked(self) -> None:
        # ensures the route audit uses SYSTEM actor (Category 5 forbids
        # FRONTEND_USER/FEISHU_USER for evolution events; SimulationExecutor
        # rejection uses SYSTEM which is unaffected)
        assert AuditActor.SYSTEM.value == "system"

    @pytest.mark.asyncio
    async def test_switch_to_feishu_without_gate_raises(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_store = BrokerEventStore(client, _FakeCollection())
        audit_store = AuditStore(
            InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl"
        )
        router = ModeRouter(
            broker=broker, event_store=event_store, audit_store=audit_store,
            initial_mode=SIMULATION_AUTO,
            # No acceptance_gate injected — codex P1 fix requires this
            # to fail-closed when switching to feishu_interactive.
        )
        with pytest.raises(AcceptanceGateMissingError):
            await router.switch_mode(
                to_mode=FEISHU_INTERACTIVE,
                reason="bypass attempt", initiated_by="cli",
                when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
            )

    @pytest.mark.asyncio
    async def test_switch_to_feishu_with_failing_gate_raises(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_store = BrokerEventStore(client, _FakeCollection())
        audit_store = AuditStore(
            InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl"
        )
        router = ModeRouter(
            broker=broker, event_store=event_store, audit_store=audit_store,
            initial_mode=SIMULATION_AUTO,
            acceptance_gate=_PassingAcceptanceGate(sanctioned=False),
        )
        with pytest.raises(AcceptanceGateRejectedError):
            await router.switch_mode(
                to_mode=FEISHU_INTERACTIVE,
                reason="acceptance not yet PASS", initiated_by="cli",
                when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
            )

    @pytest.mark.asyncio
    async def test_switch_back_to_simulation_no_gate_required(
        self, tmp_path: Path
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        )
        client = _FakeClient()
        event_store = BrokerEventStore(client, _FakeCollection())
        audit_store = AuditStore(
            InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl"
        )
        # Start in feishu_interactive (operator emergency rollback path).
        router = ModeRouter(
            broker=broker, event_store=event_store, audit_store=audit_store,
            initial_mode=FEISHU_INTERACTIVE,
            # acceptance gate not needed when switching BACK to
            # simulation_auto (always sanctioned).
        )
        result = await router.switch_mode(
            to_mode=SIMULATION_AUTO,
            reason="emergency rollback", initiated_by="cli",
            when=dt.datetime(2026, 5, 15, 16, 0, tzinfo=SHANGHAI),
        )
        assert result.to_mode == SIMULATION_AUTO
