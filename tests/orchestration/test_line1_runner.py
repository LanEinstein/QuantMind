"""Tests for the Line-1 production runner (Phase U-C1).

The runner composes the proven ``test_mvp_e2e`` Line-1 chain into a single
production entry point: screen → budget tier → candidate select → ONE
4-agent debate (cost_guard真·预留 inside ``run_shortlist``) →
``to_fund_manager_output`` → ``assemble_plan`` (14-check single
construction point) → ``RouteCoordinator``.

Zero real LLM: a stub router is injected via the test's context provider.
The runner stays import-clean (no backend.{risk,broker,data}); the heavy
risk/broker objects are built HERE (tests may import them freely), exactly
as the U-D1 scheduler will build them in production.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
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
from backend.budget_policy.policy import BudgetTierConfig, BudgetTierPolicy
from backend.candidate_selector.selector import CandidateSelector, SelectorConfig
from backend.data.data_quality import DataQualityState
from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import DataSnapshot
from backend.orchestration.instruction_dispatcher import (
    InMemoryOutboxRepository,
    InstructionDispatcher,
)
from backend.orchestration.line1_runner import (
    Line1LeadContext,
    Line1Outcome,
    Line1Runner,
)
from backend.orchestration.route_coordinator import RouteCoordinator, RouteMode
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata
from backend.screening.screener import CandidateRow, Screener
from backend.services import cost_guard
from backend.services.instruction_plan_builder import (
    AssemblyContext,
    InstructionPlanBuilder,
    WatchlistMarketSignal,
)
from backend.services.universe_policy import ExclusionRules, load_policy
from tests.orchestration.conftest import FakeFeishuSender

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_SNAP_AT = datetime(2026, 5, 15, 10, 29, 30, tzinfo=_SH)
_HEADER = "ts_code,name,listed_trading_days,closes,amounts"
_DECISION_CHAT = "oc_decision_group"


# ---------------------------------------------------------------------------
# Fixture market frame: several liquid sh_main uptrend stocks
# ---------------------------------------------------------------------------


def _uptrend(base: float, n: int = 30) -> list[float]:
    return [base + 0.10 * i for i in range(n)]


def _row(code: str, name: str, closes: list[float]) -> str:
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(3e8) for _ in closes)
    return f"{code},{name},400,{cs},{am}"


def _snapshot() -> MarketDataSnapshot:
    frame = "\n".join(
        [
            _HEADER,
            _row("600000", "浦发银行", _uptrend(10.0)),
            _row("600004", "白云机场", _uptrend(9.0)),
            _row("600006", "东风汽车", _uptrend(8.0)),
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


def _empty_snapshot() -> MarketDataSnapshot:
    """A structurally-valid frame whose only row is ST → excluded → empty."""
    frame = "\n".join([_HEADER, _row("600000", "ST浦发", _uptrend(10.0))])
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
# Stub LLM router + fake redis (mirrors test_mvp_e2e)
# ---------------------------------------------------------------------------


class _StubRouter:
    """4-agent debate stub. ``action`` is configurable (买入/卖出/持有)."""

    def __init__(self, *, action: str = "买入", silent: bool = False) -> None:
        self.calls = 0
        self._action = action
        self._silent = silent

    async def complete(
        self, agent_name: str, messages: list[dict[str, str]], **_: Any
    ) -> Any:
        self.calls += 1
        if self._silent:
            content = ""
        elif agent_name == "fund_manager":
            content = f'{{"action": "{self._action}", "reasoning": "stub thesis"}}'
        else:
            content = f"{agent_name} stub analysis report"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, float] = {}

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.store[key] = float(self.store.get(key, 0.0)) + float(amount)
        return self.store[key]

    async def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) + 1
        return int(self.store[key])

    async def decr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) - 1
        return int(self.store[key])

    async def get(self, key: str):  # noqa: ANN201
        v = self.store.get(key)
        return None if v is None else str(v)

    async def expire(self, key: str, ttl: int) -> bool:
        return True


# ---------------------------------------------------------------------------
# Fake context provider — builds the heavy risk/broker context the runner
# is forbidden to import (this is the U-D1 scheduler's job in production).
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


@dataclass
class FakeProvider:
    """Builds TeamContext + AssemblyContext for the lead candidate.

    Structurally satisfies ``Line1ContextProvider`` (duck-typed, no Protocol
    inheritance to keep the dataclass machinery simple).
    """

    cash: float
    router: _StubRouter
    lot_size: int = 100
    hold_lead: bool = False

    @property
    def available_cash(self) -> float:
        return self.cash

    def per_lot_cost(self, code: str, last_price: float) -> float:
        return last_price * self.lot_size

    def build_lead_context(self, lead: CandidateRow) -> Line1LeadContext:
        risk_engine = _risk_engine()
        limit_price = round(lead.last_price, 2)
        account = AccountInfo(
            total_assets=self.cash + 2_000.0,
            available_cash=self.cash,
            frozen_cash=0.0,
            market_value=2_000.0,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            initial_capital=100_000.0,
        )
        # ``hold_lead`` makes the account already hold the lead with ample
        # available volume, so a fund_manager SELL proposal can VALIDATE — the
        # path the non-BUY-discard guard protects against.
        positions: tuple[Position, ...] = (
            (
                Position(
                    code=lead.code,
                    volume=1_000,
                    available_volume=1_000,
                    cost_price=limit_price,
                    market_value=limit_price * 1_000,
                    unrealized_pnl=0.0,
                    unrealized_pnl_pct=0.0,
                ),
            )
            if self.hold_lead
            else ()
        )
        prev_close = round(lead.last_price * 0.99, 2)
        daily_state = DailyTradingState(
            today_new_instruction_count=0,
            today_portfolio_pnl_pct=0.0,
            last_3_trade_pnls=(),
            current_price=limit_price,
            is_in_halt_cooldown=False,
            halt_until=None,
        )
        stock_meta = RiskStockMetadata(
            code=lead.code,
            name=lead.name,
            board=RiskBoard.SH_MAIN,
            is_st=False,
            instrument_type="stock",
        )
        from backend.agents_team.state import CandidateBrief, TeamContext

        brief = CandidateBrief(
            code=lead.code,
            name=lead.name,
            proposed_volume=200,
            proposed_limit_price=limit_price,
        )
        team_ctx = TeamContext(
            risk_engine=risk_engine,
            account=account,
            positions=positions,
            prev_close=prev_close,
            daily_state=daily_state,
            stock_meta=stock_meta,
            now=_NOW,
            llm_router=self.router,
        )
        policy = load_policy(Path("config/universe_policy.yaml"))

        def make_assembly_context(
            *,
            signal_id: str,
            seq: int,
            debate_round_count: int,
            analysis_record_id: str,
            risk_validation_id: str,
        ) -> AssemblyContext:
            return AssemblyContext(
                stock_code=lead.code,
                stock_name=lead.name,
                now=_NOW,
                open_tickets=(),
                circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
                data_quality=_clean_dq(),
                watchlist_policy=policy,
                watchlist_signal=WatchlistMarketSignal(
                    listed_at_trading_days=720,
                    avg_amount_20d_yuan=1_000_000_000.0,
                    last_price_yuan=limit_price,
                ),
                risk_engine=risk_engine,
                account=account,
                positions=positions,
                prev_close=prev_close,
                daily_state=daily_state,
                stock_meta=stock_meta,
                proposed_volume=200,
                proposed_limit_price=limit_price,
                seq=seq,
                signal_id=signal_id,
                analysis_record_id=analysis_record_id,
                risk_validation_id=risk_validation_id,
                debate_round_count=debate_round_count,
                evidence_ids=(),
                data_snapshot=DataSnapshot(
                    snapshot_at=_SNAP_AT,
                    quote_source="primary",
                    is_trading_day=True,
                    is_trading_hours=True,
                    prev_close=prev_close,
                ),
                invalidation_summary="Line-1 BUY",
            )

        return Line1LeadContext(
            brief=brief,
            team_context=team_ctx,
            make_assembly_context=make_assembly_context,
        )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _zero_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _spent(_redis, *, today=None):  # noqa: ANN001
        return 0.0

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)


def _budget_policy() -> BudgetTierPolicy:
    return BudgetTierPolicy(
        BudgetTierConfig(
            micro_max_cash_yuan=2_000.0,
            small_max_cash_yuan=10_000.0,
            max_single_stock_pct=0.15,
            lot_size=100,
            etf_whitelist=frozenset({"510300", "510500", "159949"}),
        )
    )


def _make_runner(
    *,
    mode: RouteMode,
    sender: FakeFeishuSender,
    builder: InstructionPlanBuilder,
    tmp_path: Path,
) -> Line1Runner:
    audit = AuditStore(
        InMemoryAuditCollection(), jsonl_path=tmp_path / "dispatch_audit.jsonl"
    )
    from backend.services.ledger import (
        DecisionLedgerService,
        InMemoryLedgerRepository,
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
    return Line1Runner(
        screener=Screener(ExclusionRules()),
        budget_policy=_budget_policy(),
        selector=CandidateSelector(
            SelectorConfig(
                version="u-c1/v1",
                final_shortlist_size=5,
                min_quant_slots=3,
                max_percentile_shift=0.01,
                advisory_weight=0.0,
                feature_def_hash="h",
            )
        ),
        builder=builder,
        renderer=MessageRenderer(),
        coordinator=coordinator,
        ledger=ledger,
        redis_client=_FakeRedis(),
    )


class _FakeSimExecutor:
    """Records auto-fill routing without a live MockBroker."""

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


async def test_feishu_mode_routes_validated_buy(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    # The deterministic screener picks the strongest *percentage* momentum
    # (lowest base, same absolute step) — assert dynamically, not a guess.
    assert result.lead_code in {"600000", "600004", "600006"}
    assert result.route_outcome is not None
    assert result.route_outcome.action == "dispatched"
    # The BUY message reached the decision group.
    assert len(sender.calls) == 1
    assert "【QuantMind 买入信号 · 合规】" in sender.calls[0]["content"]
    assert f"-{result.lead_code}-BUY-" in sender.calls[0]["content"]


async def test_exactly_one_debate_not_per_candidate(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    # 3 screened candidates, but exactly ONE 4-agent debate (P1-7-amendment
    # fan-out cap: one debate per daily shortlist, never per candidate).
    assert len(result.shortlist) >= 2
    assert router.calls == 4


async def test_no_compliant_trade_when_budget_blocks(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    # Micro tier (¥500 < ¥2k): individual stocks are not in the tier universe.
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=500.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.NO_COMPLIANT_TRADE
    assert result.route_outcome is None
    assert router.calls == 0  # no debate spun up — no LLM spend
    assert sender.calls == []


async def test_simulation_mode_auto_fills_no_feishu(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.SIMULATION_AUTO,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    assert result.route_outcome.action == "simulation_routed"
    assert sender.calls == []  # no Feishu send in simulation_auto


async def test_dry_run_renders_only(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    sink: list[str] = []
    audit = AuditStore(
        InMemoryAuditCollection(), jsonl_path=tmp_path / "dispatch_audit.jsonl"
    )
    from backend.services.ledger import (
        DecisionLedgerService,
        InMemoryLedgerRepository,
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
        mode=RouteMode.DRY_RUN,
        simulation_executor=_FakeSimExecutor(),
        dispatcher=dispatcher,
        dry_run_sink=sink.append,
    )
    runner = Line1Runner(
        screener=Screener(ExclusionRules()),
        budget_policy=_budget_policy(),
        selector=CandidateSelector(
            SelectorConfig(
                version="u-c1/v1",
                final_shortlist_size=5,
                min_quant_slots=3,
                max_percentile_shift=0.01,
                advisory_weight=0.0,
                feature_def_hash="h",
            )
        ),
        builder=builder,
        renderer=MessageRenderer(),
        coordinator=coordinator,
        ledger=ledger,
        redis_client=_FakeRedis(),
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.ROUTED
    assert result.route_outcome.action == "dry_run_rendered"
    assert sender.calls == []  # never sent
    assert sink and "【QuantMind 买入信号 · 合规】" in sink[0]


async def test_hold_recommendation_not_routed(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="持有")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.HOLD
    assert sender.calls == []  # HOLD never routes/sends (CLAUDE.md §2.7)


async def test_validated_sell_is_discarded_not_rendered_as_buy(
    builder, tmp_path
) -> None:
    # fund_manager proposes 卖出 on a held lead → assemble_plan can VALIDATE a
    # SELL. Line-1 must NOT feed it to the BUY-only renderer (would raise) and
    # must NOT auto-sell (SELL is Line-2's job) — discard fail-closed.
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="卖出")
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router, hold_lead=True),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.NON_BUY_DISCARDED
    assert result.plan is not None and result.plan.side.value == "SELL"
    assert result.route_outcome is None
    assert sender.calls == []  # no BUY sent, no crash


async def test_silent_agents_degrade_to_hold(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    # Silent router → every agent report empty → the 4-agent gate degrades.
    router = _StubRouter(silent=True)
    result = await runner.run(
        frame=_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.DEGRADED
    assert sender.calls == []


async def test_empty_universe_short_circuits(builder, tmp_path) -> None:
    sender = FakeFeishuSender()
    runner = _make_runner(
        mode=RouteMode.FEISHU_INTERACTIVE,
        sender=sender,
        builder=builder,
        tmp_path=tmp_path,
    )
    router = _StubRouter(action="买入")
    result = await runner.run(
        frame=_empty_snapshot(),
        provider=FakeProvider(cash=98_000.0, router=router),
        now=_NOW,
    )
    assert result.outcome is Line1Outcome.EMPTY_UNIVERSE
    assert router.calls == 0  # no debate when no candidate survives the screen
    assert sender.calls == []


def test_runner_is_import_clean() -> None:
    """The runner module must not import backend.{risk,broker,data,llm,...}."""
    import ast

    src = Path("backend/orchestration/line1_runner.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"risk", "broker", "data", "llm", "agents", "mirofish", "api"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in banned:
                raise AssertionError(
                    f"line1_runner imports forbidden backend.{parts[1]} "
                    f"({node.module}) — orchestration isolation broken"
                )
