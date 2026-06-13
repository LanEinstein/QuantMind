"""N-005 ★MVP gate — two-line end-to-end on a versioned snapshot.

Proves the MVP milestone (R0 §7): the full double line composes on one PIT
:class:`MarketDataSnapshot`, with **zero real LLM** (a stub router is injected),
through the single-construction-point builder, and survives an N-day J-005
pinned-clock preflight with no reset triggers.

* **Line-1 (BUY)**: screen → budget tier → candidate select → 4-agent debate
  (stub LLM) → ``to_fund_manager_output`` bridge → ``assemble_plan`` 14-check →
  ``render_buy_signal``. This is the first time ``run_shortlist`` is wired all
  the way into the single construction point.
* **Line-2 (SELL/ADD)**: suspension partition → anomaly scan → SELL intent →
  ``assemble_monitoring_plan`` → ``render_monitoring_sell``; oversold dip → ADD
  intent → ``assemble_monitoring_plan`` (BUY) → ``render_add_position``.
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

from backend.agents_team.persona_registry import TraderPersonaRegistry
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
from backend.candidate_selector.selector import (
    CandidateSelector,
    QuantCandidate,
    SelectorConfig,
)
from backend.data.data_quality import DataQualityState
from backend.integrations.feishu.renderer import BuySignalTemplate, MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import (
    DataSnapshot,
    InstructionSide,
    InstructionStatus,
)
from backend.monitoring.add_position import (
    MarketRegime,
    evaluate_add_intents,
    make_add_context,
    parse_held_series,
)
from backend.monitoring.anomaly import AnomalyConfig, AnomalyDetector
from backend.monitoring.degrade import partition_by_suspension
from backend.monitoring.sell_signal import evaluate_sell_intents, make_sell_context
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata
from backend.screening.screener import Screener
from backend.services import cost_guard
from backend.services.instruction_plan_builder import (
    AssemblyContext,
    BuilderPlan,
    InstructionPlanBuilder,
    MandatoryAgentRecords,
    MonitoringPlan,
    WatchlistMarketSignal,
)
from backend.services.universe_policy import ExclusionRules, load_policy
from scripts.simulate_n_trading_days import run_simulation

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_SNAP_AT = datetime(2026, 5, 15, 10, 29, 30, tzinfo=_SH)
_HEADER = "ts_code,name,listed_trading_days,closes,amounts"

# Line-1 BUY lead candidate (cheap sh_main, strong uptrend).
_BUY = "600000"
# Line-2 held positions: a SELL (adverse crash) + an ADD (oversold dip).
_SELL = "510300"
_ADD = "510500"


# ---------------------------------------------------------------------------
# Fixture snapshot: a small market frame covering all three roles
# ---------------------------------------------------------------------------


def _uptrend(n: int = 30) -> list[float]:
    return [10.0 + 0.10 * i for i in range(n)]  # 10.0 → ~12.9, strong momentum


def _crash(n: int = 30) -> list[float]:
    closes = [4.5 + (0.001 if i % 2 else -0.001) for i in range(n)]
    closes[-1] = closes[-2] * 0.96  # -4% adverse move (oversold, not limit-down)
    return closes


def _dip(n: int = 30) -> list[float]:
    rise = [6.0 + 0.02 * i for i in range(20)]
    fall = [rise[-1] - 0.04 * (j + 1) for j in range(10)]
    return rise + fall


def _row(code: str, name: str, closes: list[float]) -> str:
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(3e8) for _ in closes)
    return f"{code},{name},400,{cs},{am}"


def _snapshot() -> MarketDataSnapshot:
    frame = "\n".join(
        [
            _HEADER,
            _row(_BUY, "浦发银行", _uptrend()),
            _row(_SELL, "沪深300ETF", _crash()),
            _row(_ADD, "中证500ETF", _dip()),
        ]
    )
    raw = frame.encode("utf-8")
    return MarketDataSnapshot(
        vendor="tushare", endpoint="mvp_frame", params={"trade_date": "20260515"},
        trade_date="20260515", raw_payload=raw, size=len(raw), encoding="csv",
        compression="none", raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 15, 2, 29, 30, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Stub LLM router — zero real provider calls
# ---------------------------------------------------------------------------


class _StubRouter:
    """LLMCompleter stub: 4-agent debate with a canned bullish fund_manager."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, agent_name: str, messages: list[dict[str, str]], **_: Any
    ) -> Any:
        self.calls += 1
        if agent_name == "fund_manager":
            content = '{"action": "买入", "reasoning": "stub bullish thesis"}'
        else:
            content = f"{agent_name} stub analysis report"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeRedis:
    """Minimal in-memory Redis for the debate budget reservation."""

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
# Shared builders
# ---------------------------------------------------------------------------


def _risk_engine() -> RiskEngine:
    return RiskEngine(
        RiskConfig(
            position_limits=PositionLimitsConfig(), stop_loss=StopLossConfig(),
            circuit_breaker=CircuitBreakerConfig(), universe=UniverseConfig(),
        )
    )


def _account() -> AccountInfo:
    # 100k account: keeps every Van Tharp ADD + BUY comfortably under the
    # ¥50k single-instruction cap (RiskEngine check 9) on this fixture.
    return AccountInfo(
        total_assets=100_000.0, available_cash=98_000.0, frozen_cash=0.0,
        market_value=2_000.0, total_pnl=0.0, total_pnl_pct=0.0,
        initial_capital=100_000.0,
    )


def _held() -> tuple[Position, ...]:
    return (
        Position(code=_SELL, volume=300, available_volume=300, cost_price=4.55,
                 market_value=1350.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0),
        Position(code=_ADD, volume=100, available_volume=100, cost_price=6.35,
                 market_value=635.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0),
    )


def _clean_dq() -> DataQualityState:
    return DataQualityState(
        quote_unavailable=False, quote_staleness_breach=False,
        quote_divergence_breach=False, minimum_freshness_breach=False,
        news_outage_breach=False, mirofish_unavailable=False,
        watchlist_snapshot_outage=False, primary_quote_age_seconds=2,
        backup_quote_age_seconds=2, news_sources_alive_count=5,
    )


def _good_signal() -> WatchlistMarketSignal:
    return WatchlistMarketSignal(
        listed_at_trading_days=720, avg_amount_20d_yuan=1_000_000_000.0,
        last_price_yuan=13.0,
    )


def _stock_meta(code: str, board: RiskBoard, name: str) -> RiskStockMetadata:
    return RiskStockMetadata(
        code=code, name=name, board=board, is_st=False,
        instrument_type="stock" if board is not RiskBoard.ETF else "etf",
    )


@dataclass
class DoubleLineResult:
    buy_msg: str | None
    sell_msgs: tuple[str, ...]
    add_msgs: tuple[str, ...]
    stub_calls: int
    # T-003/T-005: the anomaly scan's reproducibility lineage + fired kinds, so
    # a full-stack test can PROVE the v2 path was exercised (not just that the
    # core detectors happened to fire).
    anomaly_feature_version: str = ""
    anomaly_kinds: tuple[str, ...] = ()


async def _run_double_line(
    builder: InstructionPlanBuilder,
    *,
    trader_personas: tuple = (),
    anomaly_config: object = None,
) -> DoubleLineResult:
    """Run BOTH lines once on the fixture snapshot; return the rendered wire.

    ``trader_personas`` (T-002) and ``anomaly_config`` (T-003 full stack) default
    to the MVP behaviour (no traders, core 4 detectors) so the N-005 gate tests
    are bit-identical; the T-005 full-stack test injects both.
    """
    snap = _snapshot()
    renderer = MessageRenderer()
    risk_engine = _risk_engine()
    account = _account()
    held = _held()
    policy = load_policy(Path("config/universe_policy.yaml"))
    stub = _StubRouter()

    # === Line-1: screen → select → debate → assemble_plan → render BUY ===
    screen = Screener(ExclusionRules()).screen(snap, "SIG-mvp-line1")
    quant = [QuantCandidate(code=c.code, score=c.score) for c in screen.candidates]
    selection = CandidateSelector(
        SelectorConfig(
            version="mvp/v1", final_shortlist_size=5, min_quant_slots=3,
            max_percentile_shift=0.01, advisory_weight=0.0, feature_def_hash="h",
        )
    ).select(quant)
    assert selection.shortlist, "Line-1 produced no shortlist"

    from backend.agents_team.agents import to_fund_manager_output
    from backend.agents_team.graph import run_shortlist
    from backend.agents_team.state import CandidateBrief, TeamContext

    lead_code = selection.shortlist[0]
    lead = next(c for c in screen.candidates if c.code == lead_code)
    brief = CandidateBrief(
        code=lead.code, name=lead.name, proposed_volume=300,
        proposed_limit_price=round(lead.last_price, 2),
    )
    ctx = TeamContext(
        risk_engine=risk_engine, account=account, positions=held,
        prev_close=round(lead.last_price * 0.99, 2),
        daily_state=DailyTradingState(
            today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
            last_3_trade_pnls=(), current_price=round(lead.last_price, 2),
            is_in_halt_cooldown=False, halt_until=None,
        ),
        stock_meta=_stock_meta(lead.code, RiskBoard.SH_MAIN, lead.name),
        now=_NOW, llm_router=stub, trader_personas=trader_personas,
    )
    debate = await run_shortlist(ctx, [brief], redis_client=_FakeRedis())
    fmo = to_fund_manager_output(debate.state)
    assert fmo.side is InstructionSide.BUY  # stub fund_manager proposed 买入

    buy_ctx = AssemblyContext(
        stock_code=lead.code, stock_name=lead.name, now=_NOW, open_tickets=(),
        circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
        data_quality=_clean_dq(), watchlist_policy=policy,
        watchlist_signal=_good_signal(), risk_engine=risk_engine, account=account,
        positions=held, prev_close=ctx.prev_close, daily_state=ctx.daily_state,
        stock_meta=ctx.stock_meta, proposed_volume=300,
        proposed_limit_price=round(lead.last_price, 2), seq=1,
        signal_id="SIG-mvp-line1", analysis_record_id="ar-1",
        risk_validation_id="rv-1",
        debate_round_count=debate.state["debate_round_count"],
        evidence_ids=(), data_snapshot=DataSnapshot(
            snapshot_at=_SNAP_AT, quote_source="primary", is_trading_day=True,
            is_trading_hours=True, prev_close=ctx.prev_close,
        ),
        invalidation_summary="MVP Line-1 BUY",
    )
    records = MandatoryAgentRecords(
        fundamental_analyst_record_id="fa", technical_analyst_record_id="ta",
        risk_officer_record_id="ro", fund_manager_record_id="fm",
    )
    plan_result = await builder.assemble_plan(
        fund_manager_output=fmo, mandatory_records=records, context=buy_ctx
    )
    buy_msg: str | None = None
    if isinstance(plan_result, BuilderPlan) and (
        plan_result.plan.status is InstructionStatus.VALIDATED
        and plan_result.plan.side is InstructionSide.BUY
    ):
        buy_msg = renderer.render_buy_signal(
            plan_result.plan, template=BuySignalTemplate.NORMAL_COMPLIANT
        )

    # === Line-2: suspension partition → anomaly SELL + oversold ADD ===
    held_codes = [p.code for p in held]
    series = parse_held_series(snap, held_codes)
    # Per-code prev_close (the prior bar) so each plan's price-reasonability
    # check uses the right band — a sharp pullback and a gentle dip sit at
    # different price levels.
    prev_by_code = {c: closes[-2] for c, (closes, _amts) in series.items()}
    names = {_SELL: "沪深300ETF", _ADD: "中证500ETF"}

    def _ds(current: float) -> DailyTradingState:
        return DailyTradingState(
            today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
            last_3_trade_pnls=(), current_price=current,
            is_in_halt_cooldown=False, halt_until=None,
        )

    partition = partition_by_suspension(held_codes, {})  # no suspensions
    scan = AnomalyDetector(anomaly_config).scan(
        snap, partition.active_codes, "LINE2-MON-20260515-1"
    )
    sell_intents = evaluate_sell_intents(scan, held, name_by_code=names)

    def _etf_meta(code: str) -> RiskStockMetadata:
        return _stock_meta(code, RiskBoard.ETF, names.get(code, "ETF"))

    def _validated(res: object) -> bool:
        return (
            isinstance(res, MonitoringPlan)
            and res.plan.status is InstructionStatus.VALIDATED
        )

    sell_msgs: list[str] = []
    for i, intent in enumerate(sell_intents, start=1):
        sctx = make_sell_context(
            intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=i,
            snapshot_at=_SNAP_AT, account=account, positions=held,
            prev_close=prev_by_code.get(intent.code, intent.limit_price),
            daily_state=_ds(intent.limit_price), stock_meta=_etf_meta(intent.code),
            risk_engine=risk_engine, open_tickets=(),
            circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
            data_quality=_clean_dq(), watchlist_policy=policy,
            watchlist_signal=_good_signal(),
        )
        res = await builder.assemble_monitoring_plan(sctx)
        if _validated(res):
            sell_msgs.append(
                renderer.render_monitoring_sell(
                    res.plan, anomaly_reason=intent.anomaly_reason
                )
            )

    add_eval = evaluate_add_intents(
        series, held, account, regime=MarketRegime.NEUTRAL, name_by_code=names,
    )
    add_msgs: list[str] = []
    for i, intent in enumerate(add_eval.intents, start=10):
        actx = make_add_context(
            intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=i,
            snapshot_at=_SNAP_AT, account=account, positions=held,
            prev_close=prev_by_code.get(intent.code, intent.limit_price),
            daily_state=_ds(intent.limit_price), stock_meta=_etf_meta(intent.code),
            risk_engine=risk_engine, open_tickets=(),
            circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
            data_quality=_clean_dq(), watchlist_policy=policy,
            watchlist_signal=WatchlistMarketSignal(
                listed_at_trading_days=720, avg_amount_20d_yuan=1e9,
                last_price_yuan=intent.limit_price,
            ),
        )
        res = await builder.assemble_monitoring_plan(actx)
        if _validated(res):
            add_msgs.append(
                renderer.render_add_position(
                    res.plan, add_rationale=intent.rationale,
                    stop_price=intent.stop_price,
                )
            )

    return DoubleLineResult(
        buy_msg=buy_msg, sell_msgs=tuple(sell_msgs), add_msgs=tuple(add_msgs),
        stub_calls=stub.calls,
        anomaly_feature_version=scan.manifest.feature_code_version,
        anomaly_kinds=tuple(s.kind.value for s in scan.signals),
    )


@pytest.fixture(autouse=True)
def _zero_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _spent(_redis, *, today=None):  # noqa: ANN001
        return 0.0

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)


@pytest.fixture
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


async def test_two_line_end_to_end_on_snapshot(builder) -> None:
    result = await _run_double_line(builder)
    # Line-1: a VALIDATED BUY rendered to the decision chat.
    assert result.buy_msg is not None
    assert "【QuantMind 买入信号 · 合规】" in result.buy_msg
    assert f"-{_BUY}-BUY-" in result.buy_msg
    # Line-2: at least the adverse SELL on the crashing ETF.
    assert result.sell_msgs
    assert any("持仓监控 · 卖出信号" in m for m in result.sell_msgs)
    assert any(_SELL in m for m in result.sell_msgs)
    # Line-2: the oversold ADD on the dipping ETF.
    assert result.add_msgs
    assert any("持仓监控 · 补仓信号" in m for m in result.add_msgs)
    # Exactly the 4 mandatory-agent LLM calls (stub) — no fan-out per candidate.
    assert result.stub_calls == 4


async def test_zero_real_llm_only_stub_used(builder) -> None:
    # The debate runs entirely through the injected stub router; no real LLM
    # provider is touched (the stub counts its own calls).
    result = await _run_double_line(builder)
    assert result.stub_calls == 4  # 3 analysts + fund_manager, stubbed


@pytest.mark.asyncio
async def test_n_day_preflight_no_reset_triggers(
    builder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Drive the double line once per day across a 5-day J-005 pinned-clock
    # preflight; assert the harness walks all days with no reset triggers, no
    # callback errors, and the LLM-stub contract honoured (0 real LLM).
    monkeypatch.setenv("QUANTMIND_LLM_STUB", "1")
    runs = {"count": 0}

    async def _tick(_when: datetime, label: str) -> None:
        if label != "morning_open":
            return
        result = await _run_double_line(builder)
        runs["count"] += 1
        # Every day the double line must still produce a compliant BUY signal.
        assert result.buy_msg is not None

    outcome = await run_simulation(
        days=5,
        start_date=datetime(2026, 5, 18).date(),
        tick_callback=_tick,
        real_llm_call_observer=lambda: 0,  # stub router → 0 real provider calls
    )
    assert outcome.ok
    assert outcome.trading_days_walked == 5
    assert outcome.reset_triggers_fired == ()
    assert runs["count"] == 5


# ---------------------------------------------------------------------------
# T-005 — full-stack 联调: the SAME dual-line gate with the T-001/T-002 trader
# personas + the T-003 full anomaly stack BOTH enabled, proving they compose
# without breaking the single construction point / 14-check / render path.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_full_stack_two_line_e2e(builder) -> None:
    """≥2 trader personas (T-002) + full anomaly stack (T-003) compose end-to-end.

    The traders enrich the debate (3 analysts + 2 traders + fund_manager = 6 LLM
    calls) yet the BUY is still a deterministically-sized VALIDATED plan (the
    builder derives volume — single construction point intact); the enabled
    anomaly stack still flows its Line-2 SELL through the monitoring construction
    point + 14-check + renderer."""
    personas = TraderPersonaRegistry.from_lockfile(
        _REPO_ROOT / "config" / "prompts" / "traders.lock.json",
        repo_root=_REPO_ROOT,
        require_full_coverage=True,
    ).personas()
    assert len(personas) >= 2

    result = await _run_double_line(
        builder,
        trader_personas=personas,
        anomaly_config=AnomalyConfig(full_anomaly_stack=True),
    )
    # Line-1: the traders did not break the deterministic BUY path.
    assert result.buy_msg is not None
    assert "【QuantMind 买入信号 · 合规】" in result.buy_msg
    # 3 analysts + 2 traders + fund_manager = 6 stubbed LLM calls.
    assert result.stub_calls == 6
    # Line-2: the full anomaly stack still drives the monitoring SELL.
    assert result.sell_msgs
    assert any("持仓监控 · 卖出信号" in m for m in result.sell_msgs)
    # PROVE the T-003 path was actually exercised (codex T-005 P2): the manifest
    # must carry the v2 feature version AND the IsolationForest detector must
    # have fired on the crash bar — not just the core N-001 detectors.
    assert result.anomaly_feature_version == "monitoring.anomaly/v2"
    assert "isolation_forest" in result.anomaly_kinds


async def test_full_stack_preserves_mvp_gate(builder) -> None:
    """The default call path (no traders, core detectors) is unchanged — the
    N-005 MVP gate stays bit-identical (4 LLM calls), so the new knobs are
    strictly additive."""
    result = await _run_double_line(builder)
    assert result.stub_calls == 4
    assert result.buy_msg is not None
    # The core path stays on the v1 feature version (no full-stack kinds).
    assert result.anomaly_feature_version == "monitoring.anomaly/v1"
    assert "isolation_forest" not in result.anomaly_kinds
    assert "changepoint" not in result.anomaly_kinds
