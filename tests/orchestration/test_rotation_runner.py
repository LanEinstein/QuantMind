"""V-004 — ≤5-slot rotation runner (decision + SELL routing + intent + expiry).

Adversarial-first: a healthy incumbent is never rotated to chase a strong
challenger; a full portfolio + weak incumbent + margin challenger routes ONE
rotation SELL (and never a same-day buy); a protective stop pre-empts rotation;
and the expiry fallback never silently under-invests.

The runner stays import-clean (no backend.{risk,broker,data}); the heavy
risk/broker objects are built HERE (tests may import them freely), exactly as
the scheduler / ``main.py`` builds them in production. The rotation SELL is
routed through the REAL builder + a FEISHU_INTERACTIVE coordinator with a fake
Feishu sender so the full single-construction-point → 14-check → render → route
path is exercised.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
from backend.monitoring.anomaly import AnomalyKind
from backend.monitoring.sell_signal import SellIntent, make_sell_context
from backend.orchestration.instruction_dispatcher import (
    InMemoryOutboxRepository,
    InstructionDispatcher,
)
from backend.orchestration.rotation_runner import (
    IncumbentHealth,
    RotationRunner,
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
from backend.slot_portfolio.entry_rank import EntryRankStore
from backend.slot_portfolio.policy import (
    ChallengerMarginConfig,
    ChurnConfig,
    ExpiryConfig,
    IncumbentWeakConfig,
    RotationPolicyConfig,
    RotationProposal,
)
from backend.slot_portfolio.rotation_intent import (
    RotationIntentStore,
    build_rotation_intent,
)
from tests.orchestration.conftest import FakeFeishuSender

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 20, 10, 30, 0, tzinfo=_SH)
_SNAP_AT = datetime(2026, 5, 20, 10, 29, 30, tzinfo=_SH)
_TRADE_DATE = "20260520"
_DECISION_CHAT = "oc_decision_group"

_INC = "510300"   # weak incumbent ETF (to be rotated out)
_HEALTHY = "510500"  # healthy incumbent ETF (must NOT be rotated)
_CHAL = "000001"  # strong qualified challenger (bought T+1, not this run)
_NAMES = {_INC: "沪深300ETF", _HEALTHY: "中证500ETF", _CHAL: "平安银行"}

POLICY = RotationPolicyConfig(
    version="test",
    incumbent_weak=IncumbentWeakConfig(
        min_holding_age_trading_days=5, max_line1_percentile=0.40,
        min_rank_deterioration_pct=0.20, score_below_median_mad_mult=0.75,
        drawdown_soft_threshold=0.08,
    ),
    challenger_margin=ChallengerMarginConfig(
        min_percentile=0.75, min_rank_lead_pct=0.25, min_composite_score_margin=0.10,
    ),
    churn=ChurnConfig(
        max_rotations_per_day=1, max_open_intents=1, rotation_subcap=1,
        same_incumbent_cooldown_td=20, same_pair_cooldown_td=30,
    ),
    expiry=ExpiryConfig(max_trading_days=3),
    config_hash="cfghash",
)


# ---------------------------------------------------------------------------
# Lightweight screen stand-in (the runner only reads candidates[].{code,score})
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Cand:
    code: str
    score: float


@dataclass(frozen=True)
class _Screen:
    candidates: tuple[_Cand, ...]


def _screen() -> _Screen:
    # Sorted score desc → percentile: 000001→1.0, 510500→0.5, 510300→0.0.
    return _Screen(
        candidates=(_Cand(_CHAL, 0.95), _Cand(_HEALTHY, 0.60), _Cand(_INC, 0.20))
    )


def _td_between(earlier: str, later: str) -> int:
    a = datetime.strptime(earlier, "%Y%m%d")
    b = datetime.strptime(later, "%Y%m%d")
    return (b - a).days


def _td_ahead(trade_date: str, n: int) -> str:
    from datetime import timedelta

    return (datetime.strptime(trade_date, "%Y%m%d") + timedelta(days=n)).strftime(
        "%Y%m%d"
    )


# ---------------------------------------------------------------------------
# Fake rotation context provider — builds the rotation SELL context
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
        quote_unavailable=False, quote_staleness_breach=False,
        quote_divergence_breach=False, minimum_freshness_breach=False,
        news_outage_breach=False, mirofish_unavailable=False,
        watchlist_snapshot_outage=False, primary_quote_age_seconds=2,
        backup_quote_age_seconds=2, news_sources_alive_count=5,
    )


def _positions() -> tuple[Position, ...]:
    return (
        Position(code=_INC, volume=300, available_volume=300, cost_price=4.55,
                 market_value=1290.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0),
        Position(code=_HEALTHY, volume=100, available_volume=100, cost_price=6.0,
                 market_value=600.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0),
    )


@dataclass
class FakeRotationProvider:
    """Implements RotationContextProvider; builds the rotation SELL context."""

    held: frozenset[str] = field(default_factory=lambda: frozenset({_INC, _HEALTHY}))
    rotations_today_n: int = 0
    cap_remaining: int = 3
    protective_needs_cap: bool = False
    weak_incumbent: str = _INC

    @property
    def held_codes(self) -> frozenset[str]:
        return self.held

    def incumbent_health(self, code: str) -> IncumbentHealth:
        weak = code == self.weak_incumbent
        return IncumbentHealth(
            name=_NAMES.get(code, code),
            available_volume=300 if code == _INC else 100,
            sell_limit_price=4.30,  # ~-4.4% vs prev_close 4.5 (not limit-down)
            protective_stop_active=False,
            hard_exit_pending=False,
            anomaly_flag_active=False,
            drawdown_from_local_high=0.12 if weak else 0.0,  # confirmation 6c
            suspended=False,
            limit_down_unsellable=False,
            corporate_action_unsafe=False,
        )

    @property
    def rotations_today(self) -> int:
        return self.rotations_today_n

    @property
    def daily_new_instruction_budget_remaining(self) -> int:
        return self.cap_remaining

    @property
    def protective_action_needs_cap_today(self) -> bool:
        return self.protective_needs_cap

    def trading_days_between(self, earlier: str, later: str) -> int:
        return _td_between(earlier, later)

    def trading_day_ahead(self, trade_date: str, n: int) -> str:
        return _td_ahead(trade_date, n)

    def build_rotation_sell_context(
        self, *, code: str, name: str, available_volume: int, limit_price: float,
        reason: str, signal_id: str, seq: int, now: datetime,
    ) -> MonitoringAssemblyContext:
        intent = SellIntent(
            code=code, name=name, available_volume=available_volume,
            limit_price=limit_price, anomaly_reason=reason,
            trigger_kind=AnomalyKind.ROTATION,
        )
        account = AccountInfo(
            total_assets=100_000.0, available_cash=98_000.0, frozen_cash=0.0,
            market_value=2_000.0, total_pnl=0.0, total_pnl_pct=0.0,
            initial_capital=100_000.0,
        )
        return make_sell_context(
            intent, now=now, signal_id=signal_id, seq=seq, snapshot_at=_SNAP_AT,
            account=account, positions=_positions(), prev_close=4.50,
            daily_state=DailyTradingState(
                today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
                last_3_trade_pnls=(), current_price=limit_price,
                is_in_halt_cooldown=False, halt_until=None,
            ),
            stock_meta=RiskStockMetadata(
                code=code, name=name, board=RiskBoard.ETF, is_st=False,
                instrument_type="etf",
            ),
            risk_engine=_risk_engine(), open_tickets=(),
            circuit_breaker=CircuitBreaker(CircuitBreakerConfig()),
            data_quality=_clean_dq(),
            watchlist_policy=load_policy(Path("config/universe_policy.yaml")),
            watchlist_signal=WatchlistMarketSignal(
                listed_at_trading_days=720, avg_amount_20d_yuan=1_000_000_000.0,
                last_price_yuan=limit_price,
            ),
        )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@pytest.fixture
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


def _make_runner(
    *, sender: FakeFeishuSender, builder: InstructionPlanBuilder, tmp_path: Path,
    intent_store: RotationIntentStore, entry_store: EntryRankStore,
    max_positions: int = 2,
) -> RotationRunner:
    audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "d.jsonl")
    ledger = DecisionLedgerService(InMemoryLedgerRepository())
    dispatcher = InstructionDispatcher(
        feishu_client=sender, decision_chat_id=_DECISION_CHAT,
        outbox=InMemoryOutboxRepository(), ledger=ledger, audit_store=audit,
    )
    coordinator = RouteCoordinator(
        mode=RouteMode.FEISHU_INTERACTIVE,
        simulation_executor=_FakeSimExecutor(), dispatcher=dispatcher,
    )
    return RotationRunner(
        policy_config=POLICY, intent_store=intent_store, entry_store=entry_store,
        builder=builder, renderer=MessageRenderer(), coordinator=coordinator,
        ledger=ledger, max_total_positions=max_positions,
    )


class _FakeSimExecutor:
    async def route(self, plan, *, now):  # noqa: ANN001, ANN201
        from backend.services.simulation_executor import SimulationRouteResult

        return SimulationRouteResult(
            instruction_id=plan.instruction_id, final_status=plan.status,
            broker_order_id=None, trade_ids=(), reason=None,
        )


def _stores(tmp_path: Path) -> tuple[RotationIntentStore, EntryRankStore]:
    intents = RotationIntentStore(tmp_path / "rot.jsonl")
    entries = EntryRankStore(tmp_path / "entry.jsonl")
    return intents, entries


def _seed_entry(entries: EntryRankStore, code: str, *, pct: float, date: str) -> None:
    entries.sync_holdings(
        frozenset({code}), trade_date=date,
        percentile_by_code={code: pct}, score_by_code={code: pct},
    )


async def _run(runner: RotationRunner, provider: FakeRotationProvider) -> Any:
    return await runner.run(
        screen=_screen(), provider=provider, qualified_codes=frozenset({_CHAL}),
        now=_NOW, trade_date=_TRADE_DATE, signal_id="LINE2-MON-20260520-rotation",
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


async def test_full_weak_plus_margin_routes_rotation_sell(builder, tmp_path) -> None:
    intents, entries = _stores(tmp_path)
    # entry baseline 0.70 → current 0.0 = deterioration well past the 0.20 gate.
    _seed_entry(entries, _INC, pct=0.70, date="20260501")
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries,
    )
    result = await _run(runner, FakeRotationProvider())

    assert result.portfolio_full and result.sell_routed
    assert result.proposal.should_rotate
    assert result.proposal.incumbent_code == _INC
    assert result.proposal.challenger_code == _CHAL
    assert result.sell_plan is not None and result.sell_plan.stock_code == _INC
    assert result.route_outcome.action == "dispatched"
    # The rotation SELL went to the decision group, LINE2-MON- + SELL wire.
    assert len(sender.calls) == 1
    assert f"-{_INC}-SELL-" in sender.calls[0]["content"]
    # An append-only intent was recorded (T-day leg); NO same-day buy.
    assert result.intent_id is not None
    assert len(intents.open_intents()) == 1


async def test_healthy_portfolio_never_rotates(builder, tmp_path) -> None:
    # No incumbent independently weak → a screaming challenger never displaces.
    intents, entries = _stores(tmp_path)
    _seed_entry(entries, _INC, pct=0.70, date="20260501")
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries,
    )
    # No incumbent is weak (weak_incumbent points at a non-held code).
    result = await _run(runner, FakeRotationProvider(weak_incumbent="999999"))
    assert result.portfolio_full and not result.sell_routed
    assert not result.proposal.should_rotate
    assert len(sender.calls) == 0
    assert intents.open_intents() == ()


async def test_not_full_no_rotation(builder, tmp_path) -> None:
    intents, entries = _stores(tmp_path)
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries, max_positions=5,
    )
    result = await _run(runner, FakeRotationProvider())
    assert not result.portfolio_full and not result.sell_routed
    assert result.proposal is None
    assert len(sender.calls) == 0


async def test_incumbent_cooldown_blocks_rotation(builder, tmp_path) -> None:
    # V-003/V-004 integration: a recently-rotated incumbent is inside its
    # same_incumbent_cooldown_td window (the runner folds the prior PROPOSED date
    # from the ledger + the provider's trading-day distance into the churn gate).
    # The prior rotation is CLOSED (resolved) so open_intent_cap does not pre-empt
    # the cooldown gate — isolating the cooldown↔trading-day wiring.
    intents, entries = _stores(tmp_path)
    _seed_entry(entries, _INC, pct=0.70, date="20260501")
    prior = build_rotation_intent(
        RotationProposal(
            should_rotate=True, incumbent_code=_INC, challenger_code="000999",
            incumbent_score=0.2, challenger_score=0.9,
            incumbent_percentile=0.2, challenger_percentile=0.9,
            reason="prior", weak_incumbents=(_INC,),
        ),
        created_trade_date="20260518",  # 2 trading days before 20260520 < 20 td
        expires_at_trade_date="20260521",
        sell_instruction_id="QM-20260518-093500-000001-SELL-001",
        signal_id="LINE2-MON-20260518-rotation", config=POLICY,
    )
    intents.record_proposed(prior)
    intents.record_resolved(prior.intent_id, trade_date="20260519")  # close it
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries,
    )
    result = await _run(runner, FakeRotationProvider())
    assert result.proposal.should_rotate  # the decision still stands...
    assert "incumbent_cooldown" in result.gate_blocked_by  # ...but cooldown gates
    assert not result.sell_routed
    assert len(sender.calls) == 0


async def test_yield_to_protective_stop_blocks_rotation(builder, tmp_path) -> None:
    intents, entries = _stores(tmp_path)
    _seed_entry(entries, _INC, pct=0.70, date="20260501")
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries,
    )
    result = await _run(runner, FakeRotationProvider(protective_needs_cap=True))
    assert result.portfolio_full and not result.sell_routed
    assert result.proposal.should_rotate  # the decision stands...
    assert "yield_to_protective_stop" in result.gate_blocked_by  # ...but is gated
    assert len(sender.calls) == 0


async def test_records_entry_baseline_for_new_holdings(builder, tmp_path) -> None:
    intents, entries = _stores(tmp_path)
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries, max_positions=5,
    )
    result = await _run(runner, FakeRotationProvider())
    # First time both held codes are seen → baselines recorded from the Line-1
    # composite score (used directly as line1_percentile, codex-review V-004 fix).
    assert set(result.newly_opened_entries) == {_INC, _HEALTHY}
    assert entries.entry_percentile_for(_INC) == 0.20  # the weak ETF's score
    assert entries.entry_percentile_for(_HEALTHY) == 0.60


# -- expiry resolution ------------------------------------------------------


def _proposal() -> RotationProposal:
    return RotationProposal(
        should_rotate=True, incumbent_code=_INC, challenger_code=_CHAL,
        incumbent_score=0.20, challenger_score=0.95,
        incumbent_percentile=0.0, challenger_percentile=1.0,
        reason="r", weak_incumbents=(_INC,),
    )


def _open_expired_intent(intents: RotationIntentStore) -> None:
    intent = build_rotation_intent(
        _proposal(), created_trade_date="20260514",
        expires_at_trade_date="20260518",  # already past on 20260520
        sell_instruction_id="QM-20260514-093500-000001-SELL-001",
        signal_id="LINE2-MON-20260514-rotation", config=POLICY,
    )
    intents.record_proposed(intent)


async def test_expiry_resolved_when_challenger_rebought(builder, tmp_path) -> None:
    intents, entries = _stores(tmp_path)
    _open_expired_intent(intents)
    _seed_entry(entries, _INC, pct=0.70, date="20260501")
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries,
    )
    # Challenger now held (incumbent sold, replacement bought) → RESOLVED.
    provider = FakeRotationProvider(held=frozenset({_CHAL, _HEALTHY}))
    result = await _run(runner, provider)
    assert "ROT-20260514-510300-000001" in result.resolved_intents
    assert intents.open_intents() == ()
    assert not intents.underinvested_block_active()


async def test_expiry_underinvested_blocks_rotation(builder, tmp_path) -> None:
    intents, entries = _stores(tmp_path)
    _open_expired_intent(intents)
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries,
    )
    # Incumbent sold (not held), NO qualified ≥P75 replacement available → cash
    # held + UNDERINVESTED block. held = only _HEALTHY (incumbent gone); the only
    # qualified code is _CHAL but it is not ≥P75 in THIS run's screen... force it
    # by passing an empty qualified set so no fallback challenger exists.
    result = await runner.run(
        screen=_screen(), provider=FakeRotationProvider(held=frozenset({_HEALTHY})),
        qualified_codes=frozenset(), now=_NOW, trade_date=_TRADE_DATE,
        signal_id="LINE2-MON-20260520-rotation",
    )
    assert "ROT-20260514-510300-000001" in result.expired_intents
    assert intents.underinvested_block_active()


async def test_expiry_lapses_when_sell_not_executed(builder, tmp_path) -> None:
    intents, entries = _stores(tmp_path)
    _open_expired_intent(intents)
    _seed_entry(entries, _INC, pct=0.70, date="20260501")
    sender = FakeFeishuSender()
    runner = _make_runner(
        sender=sender, builder=builder, tmp_path=tmp_path,
        intent_store=intents, entry_store=entries,
    )
    # Incumbent STILL held (owner never executed the SELL) → lapse, no block.
    result = await _run(runner, FakeRotationProvider())
    assert "ROT-20260514-510300-000001" in result.resolved_intents
    assert not intents.underinvested_block_active()
