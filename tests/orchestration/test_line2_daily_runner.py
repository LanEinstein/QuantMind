"""Tests for the Line-2 daily anomaly runner (Phase U-C2).

The runner composes the proven ``test_mvp_e2e`` Line-2 chain into one daily
production entry point: T-1 EOD frame → partition_by_suspension →
AnomalyDetector.scan → evaluate_sell_intents (settled available_volume) →
assemble_monitoring_plan (single construction point, 14-check) →
RouteCoordinator. Zero LLM, zero redis — the SELL direction is derived
deterministically.

The runner stays import-clean (no backend.{risk,broker,data,agents_team});
the heavy risk/broker objects are built HERE (tests may import them freely),
exactly as the U-D1 scheduler will build them in production.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
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
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.monitoring.anomaly import AnomalyDetector
from backend.monitoring.sell_signal import SellIntent, make_sell_context
from backend.orchestration.instruction_dispatcher import (
    InMemoryOutboxRepository,
    InstructionDispatcher,
)
from backend.orchestration.line2_daily_runner import (
    Line2DailyRunner,
    SellRouteOutcome,
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
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_SNAP_AT = datetime(2026, 5, 15, 10, 29, 30, tzinfo=_SH)
_HEADER = "ts_code,name,listed_trading_days,closes,amounts"
_DECISION_CHAT = "oc_decision_group"

_SELL = "510300"  # crashing ETF → adverse anomaly → SELL
_CALM = "510500"  # flat ETF → no anomaly
_NAMES = {_SELL: "沪深300ETF", _CALM: "中证500ETF"}


# ---------------------------------------------------------------------------
# Fixture frame: one crashing ETF + one calm ETF
# ---------------------------------------------------------------------------


def _crash(n: int = 30) -> list[float]:
    closes = [4.5 + (0.001 if i % 2 else -0.001) for i in range(n)]
    closes[-1] = closes[-2] * 0.96  # -4% adverse (oversold, not limit-down)
    return closes


def _flat(n: int = 30) -> list[float]:
    return [6.0 + (0.001 if i % 2 else -0.001) for i in range(n)]


def _row(code: str, name: str, closes: list[float]) -> str:
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(3e8) for _ in closes)
    return f"{code},{name},400,{cs},{am}"


def _snapshot() -> MarketDataSnapshot:
    frame = "\n".join(
        [
            _HEADER,
            _row(_SELL, _NAMES[_SELL], _crash()),
            _row(_CALM, _NAMES[_CALM], _flat()),
        ]
    )
    raw = frame.encode("utf-8")
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


# ---------------------------------------------------------------------------
# Fake context provider — builds the SELL MonitoringAssemblyContext
# ---------------------------------------------------------------------------


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


def _held() -> tuple[Position, ...]:
    return (
        Position(
            code=_SELL,
            volume=300,
            available_volume=300,
            cost_price=4.55,
            market_value=1350.0,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
        ),
        Position(
            code=_CALM,
            volume=100,
            available_volume=100,
            cost_price=6.0,
            market_value=600.0,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
        ),
    )


@dataclass
class FakeLine2Provider:
    """Builds the SELL MonitoringAssemblyContext per intent (duck-typed)."""

    positions: tuple[Position, ...] = field(default_factory=_held)
    spots: dict[str, Any] = field(default_factory=dict)

    @property
    def held_positions(self) -> tuple[Position, ...]:
        return self.positions

    @property
    def spot_by_code(self) -> dict[str, Any]:
        return self.spots

    @property
    def name_by_code(self) -> dict[str, str]:
        return _NAMES

    def build_sell_context(
        self, intent: SellIntent, *, signal_id: str, seq: int, now: datetime
    ) -> MonitoringAssemblyContext:
        account = AccountInfo(
            total_assets=100_000.0,
            available_cash=98_000.0,
            frozen_cash=0.0,
            market_value=2_000.0,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            initial_capital=100_000.0,
        )
        prev_close = round(intent.limit_price / 0.96, 3)  # the pre-crash bar
        return make_sell_context(
            intent,
            now=now,
            signal_id=signal_id,
            seq=seq,
            snapshot_at=_SNAP_AT,
            account=account,
            positions=self.positions,
            prev_close=prev_close,
            daily_state=DailyTradingState(
                today_new_instruction_count=0,
                today_portfolio_pnl_pct=0.0,
                last_3_trade_pnls=(),
                current_price=intent.limit_price,
                is_in_halt_cooldown=False,
                halt_until=None,
            ),
            stock_meta=RiskStockMetadata(
                code=intent.code,
                name=intent.name,
                board=RiskBoard.ETF,
                is_st=False,
                instrument_type="etf",
            ),
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


def _make_runner(
    *,
    mode: RouteMode,
    sender: FakeFeishuSender,
    builder: InstructionPlanBuilder,
    tmp_path: Path,
) -> Line2DailyRunner:
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
        simulation_executor=_FakeSimExecutor(),
        dispatcher=dispatcher,
    )
    return Line2DailyRunner(
        anomaly_detector=AnomalyDetector(),
        builder=builder,
        renderer=MessageRenderer(),
        coordinator=coordinator,
        ledger=ledger,
    )


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


@pytest.fixture
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


async def test_feishu_mode_routes_validated_sell(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(frame=_snapshot(), provider=FakeLine2Provider(), now=_NOW)
    assert result.signal_id.startswith("LINE2-MON-")
    assert result.held_count == 2
    # The crashing ETF produces exactly one VALIDATED SELL; the calm one none.
    routed = [r for r in result.sell_routes if r.outcome is SellRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].code == _SELL
    assert routed[0].route_outcome.action == "dispatched"
    assert len(sender.calls) == 1
    assert "持仓监控 · 卖出信号" in sender.calls[0]["content"]
    assert f"-{_SELL}-SELL-" in sender.calls[0]["content"]


async def test_signal_id_carries_line2_prefix(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(frame=_snapshot(), provider=FakeLine2Provider(), now=_NOW)
    assert result.signal_id == "LINE2-MON-20260514-daily"
    assert result.sell_routes[0].plan.signal_id.startswith("LINE2-MON-")


async def test_rejects_non_line2_signal_id(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    with pytest.raises(ValueError, match="LINE2-MON-"):
        await runner.run(
            frame=_snapshot(),
            provider=FakeLine2Provider(),
            now=_NOW,
            signal_id="SIG-not-monitoring",
        )
    # An explicit empty string must NOT be silently coerced to the default
    # prefix (Codex U-C2 verify P2) — it fails the prefix check too.
    with pytest.raises(ValueError, match="LINE2-MON-"):
        await runner.run(
            frame=_snapshot(), provider=FakeLine2Provider(), now=_NOW, signal_id=""
        )


async def test_empty_portfolio_short_circuits(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(
        frame=_snapshot(), provider=FakeLine2Provider(positions=()), now=_NOW
    )
    assert result.held_count == 0
    assert result.sell_routes == ()
    assert sender.calls == []


async def test_suspended_holding_degrades_not_sold(builder, tmp_path) -> None:
    # Mark the crashing ETF suspended → it must be partitioned OUT of the
    # active scan (no SELL on a halted instrument), even though it crashed.
    # price<=0 is the locked suspension heuristic (backend.data.suspension).
    from backend.models.market import WatchlistMarketSnapshot

    suspended_spot = WatchlistMarketSnapshot(
        code=_SELL,
        name=_NAMES[_SELL],
        price=0.0,
        open=0.0,
        high=0.0,
        low=0.0,
        prev_close=4.5,
        change_pct=0.0,
        volume=0,
        amount=0.0,
        turnover_rate=0.0,
        source="adata",
        snapshot_at=_SNAP_AT,
    )
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeLine2Provider(spots={_SELL: suspended_spot}),
        now=_NOW,
    )
    assert _SELL in result.degraded_codes
    assert result.active_count == 1  # only the calm ETF stays active
    assert sender.calls == []  # halted holding never routes a SELL


async def test_simulation_mode_auto_fills_no_feishu(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.SIMULATION_AUTO,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    result = await runner.run(frame=_snapshot(), provider=FakeLine2Provider(), now=_NOW)
    routed = [r for r in result.sell_routes if r.outcome is SellRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].route_outcome.action == "simulation_routed"
    assert sender.calls == []  # no Feishu send in simulation_auto


def test_runner_is_import_clean() -> None:
    """The runner must not import backend.{risk,broker,data,llm,agents,...}."""
    import ast

    src = Path("backend/orchestration/line2_daily_runner.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    banned = {
        "risk",
        "broker",
        "data",
        "llm",
        "agents",
        "agents_team",
        "mirofish",
        "api",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in banned:
                raise AssertionError(
                    f"line2_daily_runner imports forbidden backend.{parts[1]} "
                    f"({node.module}) — Line-2 isolation broken"
                )
