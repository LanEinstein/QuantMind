"""AD-005 — manual-trade third write endpoint (P1-5-amendment-2026-06-12).

Covers the domain model, ManualTradeApplier (mirror mutation + idempotency +
broker-event/audit trail + origin tagging), recovery replay, the Feishu
"已记录" ack adversarial parser-immunity, the 3-way performance split, the
service fail-open ack, and the endpoint mode/wiring guards.
"""

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
    ManualTradeApplier,
    compute_manual_trade_idempotency_key,
)
from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig, TradeOrigin
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore
from backend.integrations.feishu.renderer import MessageRenderer
from backend.models.execution import ExecutionReportChannel
from backend.models.manual_trade import (
    ExternalExecutionEvent,
    ManualTradeReason,
    ManualTradeSide,
)
from backend.services.execution_report_parser import (
    ExecutionReportParseError,
    parse_execution_report,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# Reusable fakes (mirror tests/test_broker_appliers.py)
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
        self._docs = sorted(
            self._docs, key=lambda d: d.get(field, 0), reverse=reverse
        )
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
        now_func=lambda: dt.datetime(2026, 6, 12, 10, 0, tzinfo=SHANGHAI),
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


def _buy_event(
    *,
    external_trade_id: str = "UT-20260612-100500-600519-BUY-001",
    code: str = "600519",
    volume: int = 100,
    price: float = 1800.0,
    reason: ManualTradeReason = ManualTradeReason.USER_ADD,
    note: str = "",
) -> ExternalExecutionEvent:
    return ExternalExecutionEvent(
        external_trade_id=external_trade_id,
        code=code,
        side=ManualTradeSide.BUY,
        volume=volume,
        price=price,
        executed_at=dt.datetime(2026, 6, 12, 10, 5, tzinfo=SHANGHAI),
        reason=reason,
        note=note,
    )


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class TestExternalExecutionEvent:
    def test_valid_buy(self) -> None:
        ev = _buy_event()
        assert ev.side_is_buy is True
        assert ev.origin is TradeOrigin.USER_DISCRETIONARY

    def test_ut_id_disjoint_from_qm(self) -> None:
        """A QM- id can never validate as an external_trade_id."""
        with pytest.raises(ValueError):
            _buy_event(external_trade_id="QM-20260612-100500-600519-BUY-001")

    def test_code_must_match_embedded(self) -> None:
        with pytest.raises(ValueError, match="code"):
            ExternalExecutionEvent(
                external_trade_id="UT-20260612-100500-600519-BUY-001",
                code="000001",
                side=ManualTradeSide.BUY,
                volume=100,
                price=10.0,
                executed_at=dt.datetime(2026, 6, 12, 10, 5, tzinfo=SHANGHAI),
                reason=ManualTradeReason.USER_OTHER,
            )

    def test_side_must_match_embedded(self) -> None:
        with pytest.raises(ValueError, match="side"):
            ExternalExecutionEvent(
                external_trade_id="UT-20260612-100500-600519-BUY-001",
                code="600519",
                side=ManualTradeSide.SELL,
                volume=100,
                price=10.0,
                executed_at=dt.datetime(2026, 6, 12, 10, 5, tzinfo=SHANGHAI),
                reason=ManualTradeReason.USER_OTHER,
            )

    def test_volume_must_be_whole_lot(self) -> None:
        with pytest.raises(ValueError, match="lot"):
            _buy_event(volume=150)

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValueError):
            ExternalExecutionEvent(
                external_trade_id="UT-20260612-100500-600519-BUY-001",
                code="600519",
                side=ManualTradeSide.BUY,
                volume=100,
                price=10.0,
                executed_at=dt.datetime(2026, 6, 12, 10, 5, tzinfo=SHANGHAI),
                reason=ManualTradeReason.USER_OTHER,
                instruction_side="BUY",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# ManualTradeApplier
# ---------------------------------------------------------------------------


class TestManualTradeApplier:
    @pytest.mark.asyncio
    async def test_buy_mutates_mirror_and_tags_origin(self, env: _Env) -> None:
        applier = ManualTradeApplier(
            env.broker, env.event_store, env.audit_store
        )
        result = await applier.apply(_buy_event())

        assert isinstance(result, ApplyResult)
        assert result.reason == "manual_trade_applied"
        # 100 @ 1800 SH_MAIN → gross 180_000, commission 27 → net 180_027.
        assert result.cash_delta == pytest.approx(-180_027.0)
        account = await env.broker.get_account()
        assert account.available_cash == pytest.approx(1_000_000.0 - 180_027.0)
        positions = await env.broker.get_positions()
        assert [p.code for p in positions] == ["600519"]
        # The synthetic Trade is tagged USER_DISCRETIONARY for the split.
        trades = await env.broker.get_trades()
        assert trades[-1].origin is TradeOrigin.USER_DISCRETIONARY
        # MANUAL_TRADE_APPLIED broker event with UT- correlation id.
        assert any(
            d["event_type"] == BrokerEventType.MANUAL_TRADE_APPLIED.value
            and d["correlation_id"] == "UT-20260612-100500-600519-BUY-001"
            and d["payload"]["origin"] == "user_discretionary"
            for d in env.event_coll.docs
        )
        # Category-1 audit under FEISHU_USER actor.
        assert any(
            d["event_type"] == AuditEventType.MANUAL_TRADE_SUBMITTED.value
            and d["actor"] == AuditActor.FEISHU_USER.value
            for d in env.audit_coll.documents
        )

    @pytest.mark.asyncio
    async def test_idempotent_on_external_trade_id(self, env: _Env) -> None:
        applier = ManualTradeApplier(
            env.broker, env.event_store, env.audit_store
        )
        first = await applier.apply(_buy_event())
        second = await applier.apply(_buy_event())

        assert first.reason == "manual_trade_applied"
        assert second.reason == "manual_trade_duplicate_skipped"
        assert second.cash_delta == 0.0
        # Only ONE mirror mutation despite two submits.
        account = await env.broker.get_account()
        assert account.available_cash == pytest.approx(1_000_000.0 - 180_027.0)
        applied_events = [
            d
            for d in env.event_coll.docs
            if d["event_type"] == BrokerEventType.MANUAL_TRADE_APPLIED.value
        ]
        assert len(applied_events) == 1

    @pytest.mark.asyncio
    async def test_unaffordable_buy_raises_and_releases_claim(
        self, env: _Env
    ) -> None:
        applier = ManualTradeApplier(
            env.broker, env.event_store, env.audit_store
        )
        # 10_000 lots @ 1800 ≈ 18bn — unaffordable on 1M capital.
        huge = _buy_event(volume=1_000_000)
        with pytest.raises(ValueError):
            await applier.apply(huge)
        # Mirror unchanged; no event/audit written.
        account = await env.broker.get_account()
        assert account.available_cash == pytest.approx(1_000_000.0)
        assert not env.event_coll.docs
        # Claim was released — a corrected (affordable) retry can proceed.
        ok = await applier.apply(_buy_event())
        assert ok.reason == "manual_trade_applied"

    @pytest.mark.asyncio
    async def test_sell_clamped_to_settled_volume_t1(self, env: _Env) -> None:
        applier = ManualTradeApplier(
            env.broker, env.event_store, env.audit_store
        )
        await applier.apply(_buy_event())  # buys 100 today
        # Same-day SELL of the just-bought lot is over the settled (T+1)
        # volume → raises before mutating.
        same_day_sell = ExternalExecutionEvent(
            external_trade_id="UT-20260612-140000-600519-SELL-001",
            code="600519",
            side=ManualTradeSide.SELL,
            volume=100,
            price=1810.0,
            executed_at=dt.datetime(2026, 6, 12, 14, 0, tzinfo=SHANGHAI),
            reason=ManualTradeReason.USER_TAKE_PROFIT,
        )
        with pytest.raises(ValueError):
            await applier.apply(same_day_sell)

    def test_idempotency_key_is_stable(self) -> None:
        ev = _buy_event()
        assert compute_manual_trade_idempotency_key(
            ev
        ) == compute_manual_trade_idempotency_key(ev)
        other = _buy_event(
            external_trade_id="UT-20260612-100500-600519-BUY-002"
        )
        assert compute_manual_trade_idempotency_key(
            ev
        ) != compute_manual_trade_idempotency_key(other)


# ---------------------------------------------------------------------------
# Recovery replay
# ---------------------------------------------------------------------------


class TestManualTradeRecovery:
    @pytest.mark.asyncio
    async def test_manual_trade_applied_replays(self) -> None:
        from backend.broker.persistence.recovery import recover_state
        from backend.broker.persistence.store import BrokerSnapshotStore

        client = _FakeClient()
        event_coll = _FakeCollection()
        snap_coll = _FakeCollection()
        es = BrokerEventStore(client, event_coll)
        ss = BrokerSnapshotStore(client, snap_coll)

        await es.append(
            event_type=BrokerEventType.MANUAL_TRADE_APPLIED,
            occurred_at=dt.datetime(2026, 6, 12, 10, 5, tzinfo=SHANGHAI),
            correlation_id="UT-20260612-100500-600519-BUY-001",
            payload={
                "external_trade_id": "UT-20260612-100500-600519-BUY-001",
                "report_schema_version": 2,
                "cash_delta": -180_027.0,
                "net": 180_027.0,
                "commission": 27.0,
                "positions_delta": [
                    {"code": "600519", "volume_delta": 100, "cost_price": 1800.27}
                ],
                "origin": "user_discretionary",
            },
        )
        state = await recover_state(es, ss, initial_capital=1_000_000.0)
        assert state.positions["600519"].volume == 100
        assert state.cash == pytest.approx(1_000_000.0 - 180_027.0)


# ---------------------------------------------------------------------------
# Renderer — adversarial parser immunity
# ---------------------------------------------------------------------------


class TestManualTradeRenderer:
    def test_ack_never_parses_as_execution_report(self) -> None:
        renderer = MessageRenderer()
        body = renderer.render_manual_trade_ack(
            event=_buy_event(note="手动止盈一半,已成交"),
            cash_delta=-180_027.0,
            broker_event_sequence=42,
        )
        assert "【QuantMind 已记录-用户自主操作】" in body
        # No QM- instruction id anywhere.
        assert "QM-" not in body
        # The inbound parser must reject it (no_pattern_match) — a recording,
        # never an instruction (codex P0-6 / P1-7).
        with pytest.raises(ExecutionReportParseError) as exc:
            parse_execution_report(
                body,
                channel=ExecutionReportChannel.FEISHU,
                received_at=dt.datetime(2026, 6, 12, 14, 0, tzinfo=SHANGHAI),
            )
        assert exc.value.reason == "no_pattern_match"

    def test_duplicate_ack_distinct(self) -> None:
        renderer = MessageRenderer()
        body = renderer.render_manual_trade_ack(
            event=_buy_event(),
            cash_delta=0.0,
            broker_event_sequence=None,
            is_duplicate=True,
        )
        assert "幂等保护" in body


# ---------------------------------------------------------------------------
# Performance 3-way split
# ---------------------------------------------------------------------------


class TestPerformanceSplit:
    def test_split_buckets_by_origin(self) -> None:
        from backend.api.performance import compute_performance_split

        @dataclass
        class _T:
            net_amount: float
            origin: TradeOrigin
            direction: str  # "BUY" / "SELL"

        # net_amount is sign-free; net_cash_flow signs it by direction:
        # SELL +proceeds, BUY -cost. system bucket: -180000 (buy) + 185000
        # (sell) = +5000; user bucket: -90000 (buy).
        trades = (
            _T(180_000.0, TradeOrigin.SYSTEM_SUGGESTED, "BUY"),
            _T(185_000.0, TradeOrigin.SYSTEM_SUGGESTED, "SELL"),
            _T(90_000.0, TradeOrigin.USER_DISCRETIONARY, "BUY"),
        )
        split = compute_performance_split(trades)
        assert split["system_suggested"]["trade_count"] == 2
        assert split["system_suggested"]["net_cash_flow"] == pytest.approx(5_000.0)
        assert split["user_discretionary"]["trade_count"] == 1
        assert split["user_discretionary"]["net_cash_flow"] == pytest.approx(-90_000.0)
        # reconciliation_reset bucket always present, always zero (no Trade).
        assert split["reconciliation_reset"]["trade_count"] == 0
        assert split["reconciliation_reset"]["net_cash_flow"] == 0.0

    def test_missing_origin_defaults_system(self) -> None:
        from backend.api.performance import _trade_origin

        @dataclass
        class _Legacy:
            net_amount: float

        assert _trade_origin(_Legacy(1.0)) == "system_suggested"


# ---------------------------------------------------------------------------
# Endpoint guards (direct call with a fake Request)
# ---------------------------------------------------------------------------


class _FakeState:
    def __init__(self, service: Any) -> None:
        self.manual_trade_service = service


class _FakeApp:
    def __init__(self, service: Any) -> None:
        self.state = _FakeState(service)


class _FakeRequest:
    def __init__(self, service: Any) -> None:
        self.app = _FakeApp(service)


def _submit_body() -> Any:
    from backend.api.manual_trades import _SubmitBody

    return _SubmitBody(
        external_trade_id="UT-20260612-100500-600519-BUY-001",
        code="600519",
        side=ManualTradeSide.BUY,
        volume=100,
        price=1800.0,
        executed_at=dt.datetime(2026, 6, 12, 10, 5, tzinfo=SHANGHAI),
        reason=ManualTradeReason.USER_ADD,
    )


class TestManualTradeEndpoint:
    @pytest.mark.asyncio
    async def test_pure_sim_returns_403(self, monkeypatch) -> None:
        import backend.api.manual_trades as ep

        monkeypatch.setattr(ep, "feishu_interactive_enabled", lambda: False)
        with pytest.raises(ep.HTTPException) as exc:
            await ep.submit_manual_trade(_FakeRequest(None), _submit_body())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_unwired_returns_503(self, monkeypatch) -> None:
        import backend.api.manual_trades as ep

        monkeypatch.setattr(ep, "feishu_interactive_enabled", lambda: True)
        with pytest.raises(ep.HTTPException) as exc:
            await ep.submit_manual_trade(_FakeRequest(None), _submit_body())
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_success_returns_outcome(self, monkeypatch, env: _Env) -> None:
        import backend.api.manual_trades as ep
        from backend.services.manual_trade_service import ManualTradeService

        monkeypatch.setattr(ep, "feishu_interactive_enabled", lambda: True)
        service = ManualTradeService(
            applier=ManualTradeApplier(
                env.broker, env.event_store, env.audit_store
            ),
            renderer=MessageRenderer(),
            feishu=None,
            decision_chat_id=None,
        )
        resp = await ep.submit_manual_trade(_FakeRequest(service), _submit_body())
        assert resp["status"] == "ok"
        assert resp["data"]["apply_result"]["reason"] == "manual_trade_applied"
        assert resp["data"]["feishu_sent"] is False

    @pytest.mark.asyncio
    async def test_impossible_fill_returns_409(self, monkeypatch, env: _Env) -> None:
        import backend.api.manual_trades as ep
        from backend.services.manual_trade_service import ManualTradeService

        monkeypatch.setattr(ep, "feishu_interactive_enabled", lambda: True)
        service = ManualTradeService(
            applier=ManualTradeApplier(
                env.broker, env.event_store, env.audit_store
            ),
            renderer=MessageRenderer(),
            feishu=None,
            decision_chat_id=None,
        )
        # SELL with no position → impossible → 409.
        from backend.api.manual_trades import _SubmitBody

        body = _SubmitBody(
            external_trade_id="UT-20260612-140000-600519-SELL-001",
            code="600519",
            side=ManualTradeSide.SELL,
            volume=100,
            price=1810.0,
            executed_at=dt.datetime(2026, 6, 12, 14, 0, tzinfo=SHANGHAI),
            reason=ManualTradeReason.USER_STOP_LOSS,
        )
        with pytest.raises(ep.HTTPException) as exc:
            await ep.submit_manual_trade(_FakeRequest(service), body)
        assert exc.value.status_code == 409
