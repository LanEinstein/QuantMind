"""Tests for the production Line-2 context providers (Phase U-D1).

These prove the REAL providers (``backend.services.line2_context_providers``)
integrate with the deterministic Line-2 runners + the single-construction-point
builder end-to-end on fixture data — the same chain test_mvp_e2e / the U-C2/U-C3
runner tests exercise with hand-built fakes, but here driven by the production
provider the U-D1 scheduler wires.

Zero real network / LLM: a fake broker + fake MarketMetaProvider feed the async
assembly factories; the SELL/ADD direction stays deterministic.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import (
    MarketDataSnapshot,
    SnapshotStore,
)
from backend.models.market import WatchlistMarketSnapshot
from backend.monitoring.anomaly import AnomalyDetector
from backend.orchestration.instruction_dispatcher import (
    InMemoryOutboxRepository,
    InstructionDispatcher,
)
from backend.orchestration.intraday_manifest import IntradayTriggerManifestStore
from backend.orchestration.line2_daily_runner import Line2DailyRunner, SellRouteOutcome
from backend.orchestration.line2_intraday_runner import (
    Line2IntradayRunner,
    TriggerRouteOutcome,
)
from backend.orchestration.route_coordinator import RouteCoordinator, RouteMode
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.services.instruction_plan_builder import InstructionPlanBuilder
from backend.services.ledger import DecisionLedgerService, InMemoryLedgerRepository
from backend.services.line2_context_providers import (
    Line2CodeContext,
    Line2DailyProvider,
    Line2IntradayProvider,
    build_line2_code_contexts,
    build_line2_run_state,
    clean_data_quality,
    risk_meta_for,
)
from backend.services.simulation_executor import SimulationRouteResult
from backend.services.universe_policy import load_policy
from tests.orchestration.conftest import FakeFeishuSender

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_DECISION_CHAT = "oc_decision_group"

_SELL = "510300"  # crashing ETF → adverse anomaly → SELL
_CALM = "510500"  # flat ETF → no anomaly
_NAMES = {_SELL: "沪深300ETF", _CALM: "中证500ETF"}
_HEADER = "ts_code,name,listed_trading_days,closes,amounts"


# ---------------------------------------------------------------------------
# Fixtures
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


def _daily_frame() -> MarketDataSnapshot:
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


def _held() -> tuple[Position, ...]:
    return (
        Position(
            code=_SELL, volume=300, available_volume=300, cost_price=4.55,
            market_value=1350.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        ),
        Position(
            code=_CALM, volume=100, available_volume=100, cost_price=6.0,
            market_value=600.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        ),
    )


def _account() -> AccountInfo:
    return AccountInfo(
        total_assets=100_000.0, available_cash=98_000.0, frozen_cash=0.0,
        market_value=2_000.0, total_pnl=0.0, total_pnl_pct=0.0,
        initial_capital=100_000.0,
    )


class _FakeBroker:
    def __init__(self, account: AccountInfo, positions: tuple[Position, ...]) -> None:
        self._account = account
        self._positions = positions

    async def get_account(self) -> AccountInfo:
        return self._account

    async def get_positions(self) -> tuple[Position, ...]:
        return self._positions


class _FakeMarketMeta:
    """Returns the pre-crash bar as prev_close so the SELL price band passes."""

    def __init__(self, prev_close_by_code: dict[str, float]) -> None:
        self._prev = prev_close_by_code

    async def get_prev_close(self, code: str) -> float | None:
        return self._prev.get(code.split(".")[0])


def _risk_engine() -> RiskEngine:
    return RiskEngine(
        RiskConfig(
            position_limits=PositionLimitsConfig(), stop_loss=StopLossConfig(),
            circuit_breaker=CircuitBreakerConfig(), universe=UniverseConfig(),
        )
    )


class _FakeSimExecutor:
    def __init__(self) -> None:
        self.routed: list[str] = []

    async def route(self, plan, *, now):  # noqa: ANN001, ANN201
        self.routed.append(plan.instruction_id)
        return SimulationRouteResult(
            instruction_id=plan.instruction_id, final_status=plan.status,
            broker_order_id=None, trade_ids=(), reason=None,
        )


def _coordinator(*, mode: RouteMode, sender: FakeFeishuSender, tmp_path: Path):  # noqa: ANN202
    audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "d.jsonl")
    ledger = DecisionLedgerService(InMemoryLedgerRepository())
    dispatcher = InstructionDispatcher(
        feishu_client=sender, decision_chat_id=_DECISION_CHAT,
        outbox=InMemoryOutboxRepository(), ledger=ledger, audit_store=audit,
    )
    coordinator = RouteCoordinator(
        mode=mode, simulation_executor=_FakeSimExecutor(), dispatcher=dispatcher
    )
    return coordinator, ledger


@pytest.fixture
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


# ---------------------------------------------------------------------------
# Unit: helpers + async assembly factories
# ---------------------------------------------------------------------------


def test_risk_meta_for_etf() -> None:
    meta = risk_meta_for("510300", "沪深300ETF")
    assert meta is not None
    assert meta.board is RiskBoard.ETF
    assert meta.instrument_type == "etf"
    assert meta.code == "510300"


def test_risk_meta_for_sh_main() -> None:
    meta = risk_meta_for("600000.SH", "浦发银行")
    assert meta is not None
    assert meta.board is RiskBoard.SH_MAIN
    assert meta.instrument_type == "stock"
    assert meta.code == "600000"  # suffix stripped


def test_risk_meta_for_forbidden_returns_none() -> None:
    # 688xxx (STAR/科创) is forbidden — fail-closed to None, not a guessed board.
    assert risk_meta_for("688001", "科创板股") is None


def test_risk_meta_for_unknown_returns_none() -> None:
    assert risk_meta_for("999999", "未知") is None


async def test_build_run_state_pulls_broker_state() -> None:
    broker = _FakeBroker(_account(), _held())
    rs = await build_line2_run_state(
        broker=broker, risk_engine=_risk_engine(),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        now=_NOW,
    )
    assert rs.account.total_assets == 100_000.0
    assert len(rs.positions) == 2
    assert rs.halted is False
    ds = rs.daily_state(current_price=4.32)
    assert ds.current_price == 4.32
    assert ds.today_new_instruction_count == 0


async def test_build_code_contexts_assembles_per_code() -> None:
    frame = _daily_frame()
    meta = _FakeMarketMeta({_SELL: 4.501, _CALM: 6.0})
    contexts = await build_line2_code_contexts(
        codes=[_SELL, _CALM], name_by_code=_NAMES, market_meta=meta,
        frame=frame, data_quality_provider=None, now=_NOW,
    )
    assert set(contexts) == {_SELL, _CALM}
    sell_ctx = contexts[_SELL]
    assert sell_ctx.prev_close == 4.501
    assert sell_ctx.stock_meta is not None
    assert sell_ctx.stock_meta.board is RiskBoard.ETF
    # No DQ provider injected → clean (BUY/SELL-acceptable) fallback.
    assert sell_ctx.data_quality.is_acceptable_for_buy_sell is True
    # watchlist_signal derived from the frame's amounts (20d avg in CNY).
    assert sell_ctx.watchlist_signal.avg_amount_20d_yuan == pytest.approx(3e8)


async def test_build_code_contexts_prev_close_failure_falls_back_to_frame() -> None:
    class _BoomMeta:
        async def get_prev_close(self, code: str) -> float | None:
            raise RuntimeError("quote provider down")

    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_BoomMeta(),
        frame=_daily_frame(), data_quality_provider=None, now=_NOW,
    )
    # A meta failure degrades to the pinned frame's T-1 close — never aborts,
    # never goes dead (P0-8-amendment-2026-06-04-line2-prev-close-frame-fallback).
    assert contexts[_SELL].prev_close == pytest.approx(_crash()[-1])


async def test_build_code_contexts_prev_close_none_falls_back_to_frame() -> None:
    # The production incident shape (2026-06-04): kline_daily is EMPTY so the
    # meta provider returns None for every code — the pinned daily frame's
    # latest close must stand in (it IS the T-1 close the limit bands need).
    contexts = await build_line2_code_contexts(
        codes=[_SELL, _CALM], name_by_code=_NAMES, market_meta=_FakeMarketMeta({}),
        frame=_daily_frame(), data_quality_provider=None, now=_NOW,
    )
    assert contexts[_SELL].prev_close == pytest.approx(_crash()[-1])
    assert contexts[_CALM].prev_close == pytest.approx(_flat()[-1])


async def test_build_code_contexts_prev_close_meta_wins_over_frame() -> None:
    # kline_daily (authoritative EOD close) keeps precedence when present.
    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_FakeMarketMeta({_SELL: 4.501}),
        frame=_daily_frame(), data_quality_provider=None, now=_NOW,
    )
    assert contexts[_SELL].prev_close == 4.501


async def test_build_code_contexts_prev_close_none_without_frame() -> None:
    # Both sources absent → None → RiskEngine keeps rejecting (fail-closed).
    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_FakeMarketMeta({}),
        frame=None, data_quality_provider=None, now=_NOW,
    )
    assert contexts[_SELL].prev_close is None


async def test_build_code_contexts_prev_close_none_when_code_not_in_frame() -> None:
    # Held code missing from the frame rows → fallback yields nothing → None.
    contexts = await build_line2_code_contexts(
        codes=["600999"], name_by_code={"600999": "某股"},
        market_meta=_FakeMarketMeta({}),
        frame=_daily_frame(), data_quality_provider=None, now=_NOW,
    )
    assert contexts["600999"].prev_close is None


async def test_build_code_contexts_stale_frame_disables_fallback() -> None:
    # _ensure_daily_frame's fail-open can keep YESTERDAY's cached frame alive
    # when today's assembly fails; a stale close must never become the limit
    # band base — the trade-date pin disables ONLY the fallback (review
    # finding on P0-8-amendment-2026-06-04).
    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_FakeMarketMeta({}),
        frame=_daily_frame(), data_quality_provider=None, now=_NOW,
        expected_trade_date="20260515",  # frame pins 20260514 → stale
    )
    assert contexts[_SELL].prev_close is None


async def test_build_code_contexts_matching_pin_keeps_fallback() -> None:
    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_FakeMarketMeta({}),
        frame=_daily_frame(), data_quality_provider=None, now=_NOW,
        expected_trade_date="20260514",  # matches the frame's trade_date
    )
    assert contexts[_SELL].prev_close == pytest.approx(_crash()[-1])


async def test_build_code_contexts_stale_frame_keeps_watchlist_signal() -> None:
    # The pin gates ONLY the prev_close fallback: a stale frame still feeds
    # the watchlist signal (monitoring continuity, pre-existing semantics).
    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_FakeMarketMeta({}),
        frame=_daily_frame(), data_quality_provider=None, now=_NOW,
        expected_trade_date="20260515",
    )
    assert contexts[_SELL].watchlist_signal.avg_amount_20d_yuan == pytest.approx(3e8)


async def test_build_code_contexts_prev_close_malformed_row_degrades() -> None:
    # A structurally dropped row (wrong column count → parse_held_series drops
    # it) must degrade the FALLBACK only (None), never abort the run — mirrors
    # the meta-failure degradation contract.
    raw = "\n".join([_HEADER, f"{_SELL},{_NAMES[_SELL]},400,4.5"]).encode("utf-8")
    bad = MarketDataSnapshot(
        vendor="quantmind", endpoint="line1_screener_frame",
        params={"as_of": "20260514"}, trade_date="20260514",
        raw_payload=raw, size=len(raw), encoding="csv", compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 14, 9, 0, 0, tzinfo=UTC),
    )
    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_FakeMarketMeta({}),
        frame=bad, data_quality_provider=None, now=_NOW,
    )
    assert contexts[_SELL].prev_close is None


def test_clean_data_quality_is_acceptable() -> None:
    assert clean_data_quality().is_acceptable_for_buy_sell is True


async def test_build_code_contexts_dq_failure_fails_closed() -> None:
    # A DataQualityProvider outage must NOT be treated as a clean quote — the
    # per-code DQ fails closed (blocking) so Line-2 does not route during the
    # outage (Codex U-D1 P2).
    class _BoomDQ:
        async def evaluate(self, code: str, now: object) -> object:
            raise RuntimeError("dq probe down")

    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_FakeMarketMeta({_SELL: 4.5}),
        frame=_daily_frame(), data_quality_provider=_BoomDQ(), now=_NOW,
    )
    assert contexts[_SELL].data_quality.is_acceptable_for_buy_sell is False


def test_missing_code_context_degrades_fail_closed(tmp_path: Path) -> None:
    # A code with no assembled context → fail-closed (no stock_meta) rather than
    # crashing the run.
    rs_provider = Line2DailyProvider(
        run_state=_run_state_sync(),
        code_contexts={},  # empty → every code misses
        name_by_code=_NAMES,
        snapshot_at=_NOW - timedelta(minutes=5),
    )
    ctx = rs_provider._ctx_for("510300")  # noqa: SLF001
    assert isinstance(ctx, Line2CodeContext)
    assert ctx.stock_meta is None


def _run_state_sync():  # noqa: ANN202
    from backend.services.line2_context_providers import Line2RunState

    return Line2RunState(
        account=_account(), positions=_held(), risk_engine=_risk_engine(),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
    )


# ---------------------------------------------------------------------------
# Integration: real provider → Line-2 daily runner → routed SELL
# ---------------------------------------------------------------------------


async def _daily_provider(tmp_path: Path) -> Line2DailyProvider:
    frame = _daily_frame()
    broker = _FakeBroker(_account(), _held())
    meta = _FakeMarketMeta({_SELL: 4.501, _CALM: 6.0})
    rs = await build_line2_run_state(
        broker=broker, risk_engine=_risk_engine(),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        now=_NOW,
    )
    contexts = await build_line2_code_contexts(
        codes=[p.code for p in rs.positions], name_by_code=_NAMES,
        market_meta=meta, frame=frame, data_quality_provider=None, now=_NOW,
    )
    return Line2DailyProvider(
        run_state=rs, code_contexts=contexts, name_by_code=_NAMES,
        snapshot_at=frame.fetch_time_utc,
    )


async def test_daily_provider_routes_validated_sell(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    coordinator, ledger = _coordinator(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, tmp_path=tmp_path
    )
    runner = Line2DailyRunner(
        anomaly_detector=AnomalyDetector(), builder=builder,
        renderer=MessageRenderer(), coordinator=coordinator, ledger=ledger,
    )
    provider = await _daily_provider(tmp_path)
    result = await runner.run(frame=_daily_frame(), provider=provider, now=_NOW)
    routed = [r for r in result.sell_routes if r.outcome is SellRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].code == _SELL
    assert routed[0].route_outcome.action == "dispatched"
    assert len(sender.calls) == 1
    assert f"-{_SELL}-SELL-" in sender.calls[0]["content"]


async def test_daily_provider_simulation_mode_auto_fills(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    coordinator, ledger = _coordinator(
        mode=RouteMode.SIMULATION_AUTO, sender=sender, tmp_path=tmp_path
    )
    runner = Line2DailyRunner(
        anomaly_detector=AnomalyDetector(), builder=builder,
        renderer=MessageRenderer(), coordinator=coordinator, ledger=ledger,
    )
    provider = await _daily_provider(tmp_path)
    result = await runner.run(frame=_daily_frame(), provider=provider, now=_NOW)
    routed = [r for r in result.sell_routes if r.outcome is SellRouteOutcome.ROUTED]
    assert len(routed) == 1
    assert routed[0].route_outcome.action == "simulation_routed"
    assert sender.calls == []  # no Feishu send in simulation_auto


# ---------------------------------------------------------------------------
# Integration: real provider → Line-2 intraday runner → routed SELL on drawdown
# ---------------------------------------------------------------------------


def _intraday_spot(
    code: str, *, price: float, prev_close: float
) -> WatchlistMarketSnapshot:
    return WatchlistMarketSnapshot(
        code=code, name=_NAMES.get(code, code), price=price, open=prev_close,
        high=max(price, prev_close), low=min(price, prev_close), prev_close=prev_close,
        change_pct=(price - prev_close) / prev_close * 100, volume=1_000_000.0,
        amount=3.0e8, turnover_rate=1.0, source="adata",
        snapshot_at=_NOW - timedelta(seconds=2),
    )


async def test_intraday_provider_routes_sell_on_drawdown(builder, tmp_path) -> None:
    # Live price -8% vs prev_close → past the 5% drawdown threshold → SELL.
    frame = _daily_frame()
    broker = _FakeBroker(_account(), (_held()[0],))  # just the crashing ETF
    meta = _FakeMarketMeta({_SELL: 4.5})
    rs = await build_line2_run_state(
        broker=broker, risk_engine=_risk_engine(),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
        now=_NOW,
    )
    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=meta, frame=frame,
        data_quality_provider=None, now=_NOW,
    )
    spots = {_SELL: _intraday_spot(_SELL, price=4.14, prev_close=4.5)}

    async def _fetch(codes):  # noqa: ANN001, ANN202
        return spots

    provider = Line2IntradayProvider(
        run_state=rs, code_contexts=contexts, name_by_code=_NAMES,
        daily_frame=frame, index_closes=tuple(5.0 for _ in range(25)),
        fetch_spots_fn=_fetch,
    )
    sender = FakeFeishuSender()
    coordinator, ledger = _coordinator(
        mode=RouteMode.FEISHU_INTERACTIVE, sender=sender, tmp_path=tmp_path
    )
    runner = Line2IntradayRunner(
        builder=builder, renderer=MessageRenderer(), coordinator=coordinator,
        ledger=ledger, snapshot_store=SnapshotStore(root=tmp_path / "snaps"),
        manifest_store=IntradayTriggerManifestStore(tmp_path / "manifests"),
    )
    result = await runner.run(provider=provider, now=_NOW)
    routed = [r for r in result.routes if r.outcome is TriggerRouteOutcome.ROUTED]
    assert routed, f"expected a routed intraday SELL, got {result.routes}"
    assert routed[0].code == _SELL
    assert routed[0].side.value == "SELL"
    assert len(sender.calls) == 1


async def test_intraday_provider_build_add_context_is_buy() -> None:
    # build_add_context wraps make_add_context with the assembled bundle — assert
    # it produces a VALIDATED-bound BUY MonitoringAssemblyContext for an ADD.
    from backend.models.instruction import InstructionSide
    from backend.monitoring.add_position import AddIntent

    frame = _daily_frame()
    broker = _FakeBroker(_account(), (_held()[0],))
    rs = await build_line2_run_state(
        broker=broker, risk_engine=_risk_engine(),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        watchlist_policy=load_policy(Path("config/universe_policy.yaml")), now=_NOW,
    )
    contexts = await build_line2_code_contexts(
        codes=[_SELL], name_by_code=_NAMES, market_meta=_FakeMarketMeta({_SELL: 4.5}),
        frame=frame, data_quality_provider=None, now=_NOW,
    )

    async def _fetch(codes):  # noqa: ANN001, ANN202
        return {}

    provider = Line2IntradayProvider(
        run_state=rs, code_contexts=contexts, name_by_code=_NAMES,
        daily_frame=frame, index_closes=tuple(5.0 for _ in range(25)),
        fetch_spots_fn=_fetch,
    )
    intent = AddIntent(
        code=_SELL, name=_NAMES[_SELL], add_volume=100, limit_price=4.3,
        atr=0.1, stop_price=4.1, rsi=25.0, rationale="oversold dip vs cost",
    )
    ctx = provider.build_add_context(
        intent, signal_id="LINE2-MON-20260515-intraday-103000", seq=1,
        now=_NOW, snapshot_at=_NOW - timedelta(seconds=2),
    )
    assert ctx.side is InstructionSide.BUY
    assert ctx.stock_code == _SELL
    assert ctx.proposed_volume == 100
