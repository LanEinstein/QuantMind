"""Tests for the Line-2 30s intraday trigger runner (Phase U-C3).

The runner composes the deterministic intraday chain into one 30s production
tick: held positions → fetch live spots (timeout) → partition_by_suspension →
filter_fresh_quotes → intraday triggers (drawdown / ATR → SELL; oversold-vs-cost
→ ADD) → [persist quote snapshot + manifest BEFORE routing] →
assemble_monitoring_plan (single construction point, 14-check) →
RouteCoordinator. Zero LLM, zero redis — the direction is derived
deterministically.

The runner stays import-clean (no backend.{risk,broker,data,agents_team});
the heavy risk/broker objects + the live-quote fetch + the stores are built
HERE (tests may import them freely), exactly as the U-D1 scheduler will.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.models import (
    AccountInfo,
    CircuitBreakerConfig,
    Position,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    UniverseConfig,
)
from backend.data.data_quality import DataQualityState
from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot, SnapshotStore, row_sha256
from backend.models.instruction import InstructionSide
from backend.models.market import WatchlistMarketSnapshot
from backend.monitoring.add_position import AddIntent, make_add_context
from backend.monitoring.intraday_triggers import (
    IntradaySellIntent,
    make_intraday_sell_context,
)
from backend.orchestration.instruction_dispatcher import (
    InMemoryOutboxRepository,
    InstructionDispatcher,
)
from backend.orchestration.intraday_manifest import (
    INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION,
    IntradayTriggerManifest,
    IntradayTriggerManifestStore,
)
from backend.orchestration.line2_intraday_runner import (
    IntradayTickOutcome,
    Line2IntradayRunner,
    TriggerRouteOutcome,
)
from backend.orchestration.route_coordinator import RouteCoordinator, RouteMode
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata
from backend.services.instruction_plan_builder import (
    InstructionPlanBuilder,
    MonitoringAssemblyContext,
    WatchlistMarketSignal,
)
from backend.services.ledger import DecisionLedgerService, InMemoryLedgerRepository
from backend.services.universe_policy import load_policy
from tests.orchestration.conftest import FakeFeishuSender

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)  # Fri, trading hours
_SNAP_AT = _NOW - timedelta(seconds=2)
_HEADER = "ts_code,name,listed_trading_days,closes,amounts"
_DECISION_CHAT = "oc_decision_group"

_NAMES = {
    "510300": "沪深300ETF",
    "510500": "中证500ETF",
    "159949": "创业板50ETF",
}
_NEUTRAL_INDEX = tuple(5.0 for _ in range(25))


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _volatile_closes(n: int = 30) -> list[float]:
    return [4.2 if i % 2 else 4.8 for i in range(n)]


def _rising_closes(n: int = 21) -> list[float]:
    return [round(4.0 + 0.05 * i, 4) for i in range(n)]


def _add_closes(n: int = 30) -> list[float]:
    return [4.5 if i % 2 else 5.1 for i in range(n)]


def _daily_frame(closes_by_code: dict[str, list[float]]) -> MarketDataSnapshot:
    lines = [_HEADER]
    for code in sorted(closes_by_code):
        closes = closes_by_code[code]
        cs = "|".join(repr(v) for v in closes)
        am = "|".join(repr(3e8) for _ in closes)
        lines.append(f"{code},{_NAMES.get(code, code)},400,{cs},{am}")
    raw = "\n".join(lines).encode("utf-8")
    return MarketDataSnapshot(
        vendor="quantmind",
        endpoint="line1_screener_frame",
        params={"as_of": "20260514"},
        trade_date="20260514",
        raw_payload=raw,
        size=len(raw),
        encoding="csv",
        compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 14, 9, 0, 0, tzinfo=UTC),
    )


def _spot(
    code: str,
    *,
    price: float,
    prev_close: float,
    snapshot_at: datetime | None = None,
    volume: float = 1_000_000.0,
    amount: float = 3.0e8,
) -> WatchlistMarketSnapshot:
    return WatchlistMarketSnapshot(
        code=code,
        name=_NAMES.get(code, code),
        price=price,
        open=prev_close,
        high=max(price, prev_close),
        low=min(price, prev_close),
        prev_close=prev_close,
        change_pct=(price - prev_close) / prev_close * 100 if prev_close else 0.0,
        volume=volume,
        amount=amount,
        turnover_rate=1.0,
        source="adata",
        snapshot_at=snapshot_at or _SNAP_AT,
    )


def _position(
    code: str, *, volume: int = 300, available: int = 300, cost: float = 4.0
) -> Position:
    return Position(
        code=code,
        volume=volume,
        available_volume=available,
        cost_price=cost,
        market_value=volume * cost,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def _account(total: float = 100_000.0) -> AccountInfo:
    return AccountInfo(
        total_assets=total,
        available_cash=total * 0.9,
        frozen_cash=0.0,
        market_value=total * 0.1,
        total_pnl=0.0,
        total_pnl_pct=0.0,
        initial_capital=total,
    )


def _risk_engine() -> RiskEngine:
    return RiskEngine(
        RiskConfig(
            position_limits=PositionLimitsConfig(),
            stop_loss=StopLossConfig(),
            circuit_breaker=CircuitBreakerConfig(),
            universe=UniverseConfig(),
        )
    )


def _clean_dq() -> DataQualityState:
    return DataQualityState(
        quote_unavailable=False,
        quote_staleness_breach=False,
        quote_divergence_breach=False,
        minimum_freshness_breach=False,
        news_outage_breach=False,
        mirofish_unavailable=False,
        watchlist_snapshot_outage=False,
        primary_quote_age_seconds=2,
        backup_quote_age_seconds=2,
        news_sources_alive_count=5,
    )


def _daily_state(price: float) -> DailyTradingState:
    return DailyTradingState(
        today_new_instruction_count=0,
        today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(),
        current_price=price,
        is_in_halt_cooldown=False,
        halt_until=None,
    )


def _etf_meta(code: str, name: str) -> RiskStockMetadata:
    return RiskStockMetadata(
        code=code, name=name, board=RiskBoard.ETF, is_st=False, instrument_type="etf"
    )


class FakeIntradayProvider:
    """Builds per-code SELL/ADD contexts + supplies the live spots (duck-typed)."""

    def __init__(
        self,
        *,
        positions: tuple[Position, ...],
        spots: dict[str, WatchlistMarketSnapshot],
        closes_by_code: dict[str, list[float]],
        index_closes: tuple[float, ...] = _NEUTRAL_INDEX,
        total_assets: float = 100_000.0,
        fetch_event: asyncio.Event | None = None,
        fetch_delay: float = 0.0,
    ) -> None:
        self._positions = positions
        self._spots = spots
        self._frame = _daily_frame(closes_by_code)
        self._index = index_closes
        self._total = total_assets
        self._fetch_event = fetch_event
        self._fetch_delay = fetch_delay
        self.fetch_calls = 0

    @property
    def held_positions(self) -> tuple[Position, ...]:
        return self._positions

    @property
    def name_by_code(self) -> dict[str, str]:
        return _NAMES

    @property
    def account(self) -> AccountInfo:
        return _account(self._total)

    @property
    def daily_frame(self) -> MarketDataSnapshot:
        return self._frame

    @property
    def index_closes(self) -> tuple[float, ...]:
        return self._index

    async def fetch_spots(
        self, codes
    ) -> dict[str, WatchlistMarketSnapshot]:  # noqa: ANN001
        self.fetch_calls += 1
        if self._fetch_event is not None:
            await self._fetch_event.wait()
        if self._fetch_delay:
            await asyncio.sleep(self._fetch_delay)
        return self._spots

    def build_sell_context(
        self, intent: IntradaySellIntent, *, signal_id, seq, now, snapshot_at
    ) -> MonitoringAssemblyContext:  # noqa: ANN001
        spot = self._spots[intent.code]
        return make_intraday_sell_context(
            intent,
            now=now,
            signal_id=signal_id,
            seq=seq,
            snapshot_at=snapshot_at,
            account=self.account,
            positions=self._positions,
            prev_close=spot.prev_close,
            daily_state=_daily_state(intent.limit_price),
            stock_meta=_etf_meta(intent.code, intent.name),
            risk_engine=_risk_engine(),
            open_tickets=(),
            circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
            data_quality=_clean_dq(),
            watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
            watchlist_signal=WatchlistMarketSignal(
                listed_at_trading_days=720,
                avg_amount_20d_yuan=1_000_000_000.0,
                last_price_yuan=intent.limit_price,
            ),
        )

    def build_add_context(
        self, intent: AddIntent, *, signal_id, seq, now, snapshot_at
    ) -> MonitoringAssemblyContext:  # noqa: ANN001
        spot = self._spots[intent.code]
        return make_add_context(
            intent,
            now=now,
            signal_id=signal_id,
            seq=seq,
            snapshot_at=snapshot_at,
            account=self.account,
            positions=self._positions,
            prev_close=spot.prev_close,
            daily_state=_daily_state(intent.limit_price),
            stock_meta=_etf_meta(intent.code, intent.name),
            risk_engine=_risk_engine(),
            open_tickets=(),
            circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
            data_quality=_clean_dq(),
            watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
            watchlist_signal=WatchlistMarketSignal(
                listed_at_trading_days=720,
                avg_amount_20d_yuan=1_000_000_000.0,
                last_price_yuan=intent.limit_price,
            ),
        )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class _FakeSimExecutor:
    def __init__(self) -> None:
        self.routed: list[str] = []

    async def route(self, plan, *, now):  # noqa: ANN001, ANN201
        self.routed.append(plan.instruction_id)
        from backend.services.simulation_executor import SimulationRouteResult

        return SimulationRouteResult(
            instruction_id=plan.instruction_id,
            final_status=plan.status,
            broker_order_id=None,
            trade_ids=(),
            reason=None,
        )


class _BoomSimExecutor:
    async def route(self, plan, *, now):  # noqa: ANN001, ANN201
        raise RuntimeError("boom — routing blew up after persistence")


def _make_runner(
    *,
    mode: RouteMode,
    sender: FakeFeishuSender,
    builder: InstructionPlanBuilder,
    tmp_path: Path,
    tick_timeout_seconds: float = 10.0,
    sim_executor=None,  # noqa: ANN001
    dry_sink=None,  # noqa: ANN001
) -> tuple[Line2IntradayRunner, SnapshotStore, IntradayTriggerManifestStore]:
    audit = AuditStore(
        InMemoryAuditCollection(), jsonl_path=tmp_path / "dispatch_audit.jsonl"
    )
    ledger = DecisionLedgerService(InMemoryLedgerRepository())
    dispatcher = InstructionDispatcher(
        feishu_client=sender,
        decision_chat_id=_DECISION_CHAT,
        outbox=InMemoryOutboxRepository(),
        ledger=ledger,
        audit_store=audit,
    )
    coordinator = RouteCoordinator(
        mode=mode,
        simulation_executor=sim_executor or _FakeSimExecutor(),
        dispatcher=dispatcher,
        dry_run_sink=dry_sink,
    )
    snapshot_store = SnapshotStore(tmp_path / "snapshots")
    manifest_store = IntradayTriggerManifestStore(tmp_path / "manifests")
    runner = Line2IntradayRunner(
        builder=builder,
        renderer=MessageRenderer(),
        coordinator=coordinator,
        ledger=ledger,
        snapshot_store=snapshot_store,
        manifest_store=manifest_store,
        tick_timeout_seconds=tick_timeout_seconds,
    )
    return runner, snapshot_store, manifest_store


@pytest.fixture
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


def _drawdown_provider() -> FakeIntradayProvider:
    return FakeIntradayProvider(
        positions=(_position("510300", cost=4.0),),
        spots={"510300": _spot("510300", price=4.185, prev_close=4.5)},
        closes_by_code={"510300": _volatile_closes()},
    )


def _add_provider() -> FakeIntradayProvider:
    return FakeIntradayProvider(
        positions=(_position("159949", volume=100, available=100, cost=5.0),),
        spots={"159949": _spot("159949", price=4.8, prev_close=4.85)},
        closes_by_code={"159949": _add_closes()},
    )


# ---------------------------------------------------------------------------
# Deterministic triggers + routing modes
# ---------------------------------------------------------------------------


async def test_feishu_mode_routes_drawdown_sell(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner, snaps, manifests = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=_drawdown_provider(), now=_NOW)
    assert result.tick_outcome is IntradayTickOutcome.SCANNED
    assert result.signal_id.startswith("LINE2-MON-")
    routed = [r for r in result.routes if r.outcome is TriggerRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].code == "510300"
    assert routed[0].kind == "drawdown_stop"
    assert routed[0].route_outcome.action == "dispatched"
    assert len(sender.calls) == 1
    assert "持仓监控 · 卖出信号" in sender.calls[0]["content"]
    assert "-510300-SELL-" in sender.calls[0]["content"]


async def test_feishu_mode_routes_atr_trailing_stop_sell(builder, tmp_path) -> None:
    provider = FakeIntradayProvider(
        positions=(_position("510500", cost=4.0),),
        spots={"510500": _spot("510500", price=4.85, prev_close=5.0)},
        closes_by_code={"510500": _rising_closes()},
    )
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=provider, now=_NOW)
    routed = [r for r in result.routes if r.outcome is TriggerRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].kind == "atr_trailing_stop"
    assert "-510500-SELL-" in sender.calls[0]["content"]


async def test_feishu_mode_routes_add_buy(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=_add_provider(), now=_NOW)
    routed = [r for r in result.routes if r.outcome is TriggerRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].code == "159949"
    assert routed[0].kind == "add"
    assert "持仓监控 · 补仓信号" in sender.calls[0]["content"]
    assert "-159949-BUY-" in sender.calls[0]["content"]


async def test_simulation_mode_auto_fills_no_feishu(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.SIMULATION_AUTO, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=_drawdown_provider(), now=_NOW)
    routed = [r for r in result.routes if r.outcome is TriggerRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].route_outcome.action == "simulation_routed"
    assert sender.calls == []


async def test_dry_run_mode_render_only(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    rendered: list[str] = []
    runner, _, _ = _make_runner(
        mode=RouteMode.DRY_RUN, sender=sender, builder=builder, tmp_path=tmp_path,
        dry_sink=rendered.append,
    )
    result = await runner.run(provider=_drawdown_provider(), now=_NOW)
    routed = [r for r in result.routes if r.outcome is TriggerRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].route_outcome.action == "dry_run_rendered"
    assert sender.calls == []
    assert len(rendered) == 1
    assert "持仓监控 · 卖出信号" in rendered[0]


# ---------------------------------------------------------------------------
# §设计4 — seven defensive invariants
# ---------------------------------------------------------------------------


async def test_invariant1_overlapping_tick_skipped(builder, tmp_path) -> None:
    event = asyncio.Event()
    provider = FakeIntradayProvider(
        positions=(_position("510300", cost=4.0),),
        spots={"510300": _spot("510300", price=4.185, prev_close=4.5)},
        closes_by_code={"510300": _volatile_closes()},
        fetch_event=event,
    )
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    task1 = asyncio.create_task(runner.run(provider=provider, now=_NOW))
    await asyncio.sleep(0)  # let task1 park at the fetch event with in_flight set
    second = await runner.run(provider=provider, now=_NOW)
    assert second.tick_outcome is IntradayTickOutcome.SKIPPED_OVERLAP
    event.set()
    first = await task1
    assert first.tick_outcome is IntradayTickOutcome.SCANNED
    assert len(sender.calls) == 1  # only the first tick routed


async def test_invariant2_fetch_timeout_skips(builder, tmp_path) -> None:
    provider = FakeIntradayProvider(
        positions=(_position("510300", cost=4.0),),
        spots={"510300": _spot("510300", price=4.185, prev_close=4.5)},
        closes_by_code={"510300": _volatile_closes()},
        fetch_delay=0.2,
    )
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path, tick_timeout_seconds=0.05,
    )
    result = await runner.run(provider=provider, now=_NOW)
    assert result.tick_outcome is IntradayTickOutcome.SKIPPED_TIMEOUT
    assert sender.calls == []


async def test_invariant3_stale_quote_fails_closed(builder, tmp_path) -> None:
    stale_spot = _spot(
        "510300", price=4.185, prev_close=4.5,
        snapshot_at=_NOW - timedelta(seconds=120),  # older than 60s
    )
    provider = FakeIntradayProvider(
        positions=(_position("510300", cost=4.0),),
        spots={"510300": stale_spot},
        closes_by_code={"510300": _volatile_closes()},
    )
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=provider, now=_NOW)
    assert "510300" in result.stale_codes
    assert result.routes == ()
    assert sender.calls == []


async def test_invariant3_same_instant_quote_fails_closed(builder, tmp_path) -> None:
    # A quote tagged exactly at the tick ``now`` is NOT strictly before the
    # decision time; it must fail closed BEFORE building (no trigger, no
    # persist) rather than crash the plan build after persisting (codex U-C3 P1).
    provider = FakeIntradayProvider(
        positions=(_position("510300", cost=4.0),),
        spots={
            "510300": _spot("510300", price=4.185, prev_close=4.5, snapshot_at=_NOW)
        },
        closes_by_code={"510300": _volatile_closes()},
    )
    sender = FakeFeishuSender()
    runner, snaps, manifests = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=provider, now=_NOW, signal_id="LINE2-MON-SI")
    assert result.tick_outcome is IntradayTickOutcome.SCANNED
    assert "510300" in result.stale_codes
    assert result.routes == ()  # no trigger
    assert result.quote_snapshot_id is None  # nothing persisted
    assert manifests.get("LINE2-MON-SI") is None
    assert sender.calls == []


async def test_invariant4_non_trading_day_skipped(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    provider = _drawdown_provider()
    saturday = datetime(2026, 5, 16, 10, 30, 0, tzinfo=_SH)  # weekend
    result = await runner.run(provider=provider, now=saturday)
    assert result.tick_outcome is IntradayTickOutcome.SKIPPED_NON_TRADING_DAY
    assert provider.fetch_calls == 0  # no fetch on a non-trading day


async def test_invariant5_off_hours_skipped(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    provider = _drawdown_provider()
    lunch = datetime(2026, 5, 15, 12, 0, 0, tzinfo=_SH)  # lunch break
    result = await runner.run(provider=provider, now=lunch)
    assert result.tick_outcome is IntradayTickOutcome.SKIPPED_OFF_HOURS
    assert provider.fetch_calls == 0


async def test_invariant6_suspended_holding_degrades(builder, tmp_path) -> None:
    provider = FakeIntradayProvider(
        positions=(_position("510300", cost=4.0),),
        spots={"510300": _spot("510300", price=0.0, prev_close=4.5)},  # halted
        closes_by_code={"510300": _volatile_closes()},
    )
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=provider, now=_NOW)
    assert "510300" in result.degraded_codes
    assert result.active_count == 0
    assert sender.calls == []  # halted holding never routes


async def test_invariant7_persists_quote_snapshot_and_manifest(
    builder, tmp_path
) -> None:
    provider = _drawdown_provider()
    sender = FakeFeishuSender()
    runner, snaps, manifests = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=provider, now=_NOW, signal_id="LINE2-MON-T7")
    # The fired quotes are persisted as a verify-before-adopt snapshot.
    assert result.quote_snapshot_id is not None
    snap = snaps.get(UUID(result.quote_snapshot_id))  # checksum self-verified
    assert "510300" in snap.raw_payload.decode("utf-8")
    # The manifest pins the parent snapshot + the daily frame + consumed rows.
    manifest = manifests.get("LINE2-MON-T7")
    assert manifest is not None
    assert manifest.quote_snapshot_id == UUID(result.quote_snapshot_id)
    assert manifest.daily_frame_snapshot_ids == (provider.daily_frame.snapshot_id,)
    assert len(manifest.triggers) == 1
    assert manifest.triggers[0].code == "510300"
    assert manifest.triggers[0].side == "sell"
    # Consumed-row lineage: the recorded sha matches the persisted row bytes
    # (offline replay can rebuild the exact consumed quote).
    consumed = manifest.consumed_quotes[0]
    row_line = next(
        line
        for line in snap.raw_payload.decode("utf-8").splitlines()
        if line.startswith("510300,")
    )
    assert consumed.row_sha256 == row_sha256(row_line.encode("utf-8"))


async def test_add_manifest_records_decision_inputs(builder, tmp_path) -> None:
    # The ADD trigger record must capture the dip-vs-cost + sizing inputs the
    # BUY gate used beyond the quote, so the verdict is auditable/recomputable
    # (codex U-C3 P2).
    provider = _add_provider()  # 159949 cost 5.0 vol 100, 100k equity, neutral
    sender = FakeFeishuSender()
    runner, _, manifests = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    await runner.run(provider=provider, now=_NOW, signal_id="LINE2-MON-ADD")
    manifest = manifests.get("LINE2-MON-ADD")
    assert manifest is not None
    rec = manifest.triggers[0]
    assert rec.side == "buy"
    assert rec.kind == "add"
    assert rec.cost_price == 5.0
    assert rec.position_volume == 100
    assert rec.total_assets == 100_000.0
    assert rec.regime == "neutral"
    assert rec.ma_long is not None
    assert rec.threshold_params["breakdown_tolerance"] == 0.10
    assert rec.threshold_params["ma_long_window"] == 20.0


async def test_persist_happens_before_route(builder, tmp_path) -> None:
    # A routing failure must not lose the replay lineage: the snapshot +
    # manifest are written BEFORE any signal routes (invariant 7 ordering).
    provider = _drawdown_provider()
    sender = FakeFeishuSender()
    runner, snaps, manifests = _make_runner(
        mode=RouteMode.SIMULATION_AUTO, sender=sender, builder=builder,
        tmp_path=tmp_path, sim_executor=_BoomSimExecutor(),
    )
    with pytest.raises(RuntimeError, match="boom"):
        await runner.run(provider=provider, now=_NOW, signal_id="LINE2-MON-ORDER")
    # Persistence already happened despite the routing blow-up.
    manifest = manifests.get("LINE2-MON-ORDER")
    assert manifest is not None
    assert snaps.get(manifest.quote_snapshot_id) is not None


# ---------------------------------------------------------------------------
# Single construction point, prefix guard, dedup, empty portfolio
# ---------------------------------------------------------------------------


async def test_routed_plan_carries_line2_prefix(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=_drawdown_provider(), now=_NOW)
    plan = result.routes[0].plan
    assert plan is not None
    assert plan.signal_id.startswith("LINE2-MON-")
    assert plan.signal_id == result.signal_id


async def test_rejects_non_line2_signal_id(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    with pytest.raises(ValueError, match="LINE2-MON-"):
        await runner.run(
            provider=_drawdown_provider(), now=_NOW, signal_id="SIG-not-monitoring"
        )
    # An explicit empty string must NOT be coerced to the default (codex U-C2 P2).
    with pytest.raises(ValueError, match="LINE2-MON-"):
        await runner.run(provider=_drawdown_provider(), now=_NOW, signal_id="")


async def test_dedup_same_code_side_within_day(builder, tmp_path) -> None:
    provider = _drawdown_provider()
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    first = await runner.run(provider=provider, now=_NOW)
    assert first.routes[0].outcome is TriggerRouteOutcome.ROUTED
    # Same (code, side) 30s later → deduped, no second send.
    later = _NOW + timedelta(seconds=30)
    second = await runner.run(provider=provider, now=later)
    assert second.routes[0].outcome is TriggerRouteOutcome.DEDUP_SKIPPED
    assert len(sender.calls) == 1
    # A deduped repeat routes no new signal, so it is NOT re-persisted — the
    # originating signal's lineage is already durable from the first fire.
    assert second.quote_snapshot_id is None


async def test_empty_portfolio_short_circuits(builder, tmp_path) -> None:
    provider = FakeIntradayProvider(
        positions=(), spots={}, closes_by_code={}
    )
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=provider, now=_NOW)
    assert result.tick_outcome is IntradayTickOutcome.EMPTY_PORTFOLIO
    assert provider.fetch_calls == 0
    assert sender.calls == []


async def test_second_triggered_tick_same_day_persists(builder, tmp_path) -> None:
    # Regression (codex U-C3 P1): a second triggered tick on the SAME trade
    # date must persist without colliding on the snapshot key. Two distinct
    # codes fire on two ticks (neither deduped) → both snapshots are stored.
    sender = FakeFeishuSender()
    runner, snaps, manifests = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    p1 = FakeIntradayProvider(
        positions=(_position("510300", cost=4.0),),
        spots={"510300": _spot("510300", price=4.185, prev_close=4.5)},
        closes_by_code={"510300": _volatile_closes()},
    )
    tick2_now = _NOW + timedelta(seconds=60)
    p2 = FakeIntradayProvider(
        positions=(_position("510500", cost=4.0),),
        spots={
            "510500": _spot(
                "510500", price=4.185, prev_close=4.5,
                snapshot_at=tick2_now - timedelta(seconds=2),  # fresh at tick2
            )
        },
        closes_by_code={"510500": _volatile_closes()},
    )
    first = await runner.run(provider=p1, now=_NOW, signal_id="LINE2-MON-T1")
    second = await runner.run(provider=p2, now=tick2_now, signal_id="LINE2-MON-T2")
    assert first.tick_outcome is IntradayTickOutcome.SCANNED
    assert second.tick_outcome is IntradayTickOutcome.SCANNED  # no overwrite raise
    assert first.quote_snapshot_id != second.quote_snapshot_id
    assert snaps.get(UUID(first.quote_snapshot_id)) is not None
    assert snaps.get(UUID(second.quote_snapshot_id)) is not None
    assert len(sender.calls) == 2


async def test_sell_suppresses_add_on_same_code(builder, tmp_path) -> None:
    # Regression (codex U-C3 P1): a holding that satisfies BOTH the risk-exit
    # SELL and the dip-vs-cost ADD this tick must only SELL — never route a
    # contradictory BUY on the same code.
    provider = FakeIntradayProvider(
        positions=(_position("510300", cost=4.3),),  # live below cost → ADD gate
        spots={"510300": _spot("510300", price=4.18, prev_close=4.5)},  # -7% → SELL
        closes_by_code={"510300": _volatile_closes()},
    )
    sender = FakeFeishuSender()
    runner, _, _ = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(provider=provider, now=_NOW)
    sells = [r for r in result.routes if r.side is InstructionSide.SELL]
    buys = [r for r in result.routes if r.side is InstructionSide.BUY]
    assert len(sells) == 1
    assert sells[0].outcome is TriggerRouteOutcome.ROUTED
    assert buys == []  # the ADD is suppressed entirely (not even dedup-skipped)
    assert len(sender.calls) == 1


def test_intraday_manifest_fails_closed_on_schema_drift() -> None:
    # A future / stale schema_version must be rejected, not silently mis-parsed
    # (mirrors MarketDataSnapshot — codex U-C3 P2).
    from uuid import uuid4

    kwargs = dict(
        signal_id="LINE2-MON-x",
        created_at=_NOW,
        tick_at=_NOW,
        quote_snapshot_id=uuid4(),
        feature_code_version="monitoring.intraday_triggers/v1",
        config_hash="0" * 64,
    )
    # Current version validates.
    ok = IntradayTriggerManifest(
        schema_version=INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION, **kwargs
    )
    assert ok.schema_version == INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION
    # A drifted version fails closed.
    with pytest.raises(ValueError, match="schema_version"):
        IntradayTriggerManifest(
            schema_version=INTRADAY_TRIGGER_MANIFEST_SCHEMA_VERSION + 1, **kwargs
        )


# ---------------------------------------------------------------------------
# Import isolation (orchestration boundary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        "backend/orchestration/line2_intraday_runner.py",
        "backend/orchestration/intraday_manifest.py",
    ],
)
def test_runner_and_manifest_are_import_clean(module_path: str) -> None:
    """No backend.{api,broker,risk,llm,agents,agents_team,mirofish,data} import."""
    import ast

    src = Path(module_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {
        "api", "broker", "risk", "llm", "agents", "agents_team", "mirofish", "data",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in banned:
                raise AssertionError(
                    f"{module_path} imports forbidden backend.{parts[1]} "
                    f"({node.module}) — orchestration isolation broken"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                p = alias.name.split(".")
                if len(p) >= 2 and p[0] == "backend" and p[1] in banned:
                    raise AssertionError(
                        f"{module_path} imports forbidden {alias.name}"
                    )
