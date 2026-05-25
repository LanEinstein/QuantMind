"""Tests for backend.monitoring.sell_signal + Line-2 builder/renderer (N-002)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
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
from backend.data.data_quality import DataQualityState
from backend.integrations.feishu.renderer import MessageRenderer
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import InstructionSide, InstructionStatus
from backend.models.reconciliation import (
    DeviationReport,
    FieldDeviation,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)
from backend.monitoring.anomaly import AnomalyDetector, AnomalyKind
from backend.monitoring.sell_signal import (
    SellIntent,
    evaluate_sell_intents,
    is_sell_trigger,
    make_sell_context,
)
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata
from backend.services.instruction_plan_builder import (
    BuilderEarlyReturn,
    FreezeSource,
    InstructionPlanBuilder,
    MonitoringPlan,
    WatchlistMarketSignal,
)
from backend.services.universe_policy import UniversePolicy, load_policy

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=_SH)
_SNAP_AT = datetime(2026, 5, 15, 10, 29, 30, tzinfo=_SH)
_CODE = "510300"
_NAME = "沪深300ETF"
_HEADER = "ts_code,name,listed_trading_days,closes,amounts"


# ---------------------------------------------------------------------------
# snapshot / scan helpers
# ---------------------------------------------------------------------------


def _snapshot(csv_text: str) -> MarketDataSnapshot:
    raw = csv_text.encode("utf-8")
    return MarketDataSnapshot(
        vendor="tushare", endpoint="monitor", params={}, trade_date="20260515",
        raw_payload=raw, size=len(raw), encoding="csv", compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 15, 2, 29, 30, tzinfo=UTC),
    )


def _row(code: str, closes: list[float], amounts: list[float] | None = None) -> str:
    amts = amounts or [3e8] * len(closes)
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(v) for v in amts)
    return f"{code},ETF,300,{cs},{am}"


def _down_closes(base: float = 4.5, pct: float = 0.96, n: int = 30) -> list[float]:
    closes = [base + (0.001 if i % 2 else -0.001) for i in range(n)]
    closes[-1] = closes[-2] * pct  # adverse move on the last bar
    return closes


def _down_scan(code: str = _CODE, pct: float = 0.96):
    snap = _snapshot("\n".join([_HEADER, _row(code, _down_closes(pct=pct))]))
    return AnomalyDetector().scan(snap, [code], "LINE2-MON-20260515-1")


# ---------------------------------------------------------------------------
# fixtures (mirror the Line-1 assemble test fixtures)
# ---------------------------------------------------------------------------


@pytest.fixture
def policy() -> UniversePolicy:
    return load_policy(Path("config/universe_policy.yaml"))


@pytest.fixture
def passing_signal() -> WatchlistMarketSignal:
    return WatchlistMarketSignal(
        listed_at_trading_days=720,
        avg_amount_20d_yuan=1_000_000_000.0,
        last_price_yuan=4.5,
    )


@pytest.fixture
def clean_data_quality() -> DataQualityState:
    return DataQualityState(
        quote_unavailable=False, quote_staleness_breach=False,
        quote_divergence_breach=False, minimum_freshness_breach=False,
        news_outage_breach=False, mirofish_unavailable=False,
        watchlist_snapshot_outage=False, primary_quote_age_seconds=2,
        backup_quote_age_seconds=2, news_sources_alive_count=5,
    )


@pytest.fixture
def quiet_breaker() -> CircuitBreaker:
    return CircuitBreaker(CircuitBreakerConfig())


@pytest.fixture
def account() -> AccountInfo:
    return AccountInfo(
        total_assets=100_000.0, available_cash=90_000.0, frozen_cash=0.0,
        market_value=10_000.0, total_pnl=0.0, total_pnl_pct=0.0,
        initial_capital=100_000.0,
    )


@pytest.fixture
def held_position() -> Position:
    return Position(
        code=_CODE, volume=300, available_volume=300, cost_price=4.6,
        market_value=1296.0, unrealized_pnl=-84.0, unrealized_pnl_pct=-0.06,
    )


@pytest.fixture
def risk_engine() -> RiskEngine:
    return RiskEngine(
        RiskConfig(
            position_limits=PositionLimitsConfig(), stop_loss=StopLossConfig(),
            circuit_breaker=CircuitBreakerConfig(), universe=UniverseConfig(),
        )
    )


@pytest.fixture
def stock_meta() -> RiskStockMetadata:
    return RiskStockMetadata(
        code=_CODE, name=_NAME, board=RiskBoard.ETF, is_st=False,
        instrument_type="etf",
    )


@pytest.fixture
def daily_state() -> DailyTradingState:
    return DailyTradingState(
        today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(), current_price=4.32, is_in_halt_cooldown=False,
        halt_until=None,
    )


@pytest.fixture
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


def _ctx(intent, **over):
    """Assemble a SELL MonitoringAssemblyContext with sensible defaults."""
    base = dict(
        now=_NOW, signal_id="LINE2-MON-20260515-1", seq=1, snapshot_at=_SNAP_AT,
        prev_close=4.5, quote_source="primary",
    )
    base.update(over)
    return make_sell_context(intent, **base)


# ---------------------------------------------------------------------------
# evaluate_sell_intents — deterministic decision + sizing
# ---------------------------------------------------------------------------


def test_down_price_anomaly_yields_sell_intent(held_position) -> None:
    intents = evaluate_sell_intents(
        _down_scan(), (held_position,), name_by_code={_CODE: _NAME}
    )
    assert len(intents) == 1
    it = intents[0]
    assert it.code == _CODE
    assert it.available_volume == 300  # T+1 settled, lot-aligned
    # limit = the snapshot's last close (the adverse bar), below prev.
    assert 0 < it.limit_price < 4.5
    assert it.trigger_kind in {
        AnomalyKind.PRICE_ZSCORE,
        AnomalyKind.EWMA_DEVIATION,
        AnomalyKind.BOLLINGER_BREAKOUT,
    }


def test_volume_only_anomaly_not_a_sell_trigger(held_position) -> None:
    # A pure UP volume spike with calm price → no SELL (ambiguous w/o price).
    closes = [4.5 + (0.001 if i % 2 else -0.001) for i in range(30)]
    amounts = [3e8 + (1e6 if i % 2 else -1e6) for i in range(30)]
    amounts[-1] = 9e9
    snap = _snapshot("\n".join([_HEADER, _row(_CODE, closes, amounts)]))
    scan = AnomalyDetector().scan(snap, [_CODE], "LINE2-MON-20260515-2")
    assert any(s.kind == AnomalyKind.VOLUME_ZSCORE for s in scan.signals)
    intents = evaluate_sell_intents(scan, (held_position,))
    assert intents == ()


def test_zero_available_volume_no_intent() -> None:
    # All bought today → available_volume 0 (T+1): cannot sell.
    pos = Position(
        code=_CODE, volume=300, available_volume=0, cost_price=4.6,
        market_value=1296.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
    )
    intents = evaluate_sell_intents(_down_scan(), (pos,))
    assert intents == ()


def test_unheld_anomaly_ignored(held_position) -> None:
    # Anomaly on a code we do not hold → no intent.
    scan = _down_scan(code="600000")
    intents = evaluate_sell_intents(scan, (held_position,))
    assert intents == ()


def test_up_move_not_a_sell_trigger(held_position) -> None:
    intents = evaluate_sell_intents(
        _down_scan(pct=1.10), (held_position,)  # +10% UP
    )
    assert intents == ()


def test_is_sell_trigger_predicate() -> None:
    sig = _down_scan().signals[0]
    assert is_sell_trigger(sig) is True


# ---------------------------------------------------------------------------
# make_sell_context guards
# ---------------------------------------------------------------------------


def test_make_sell_context_rejects_non_line2_signal_id(
    held_position, account, risk_engine, stock_meta, daily_state, policy,
    passing_signal, clean_data_quality, quiet_breaker,
) -> None:
    intent = evaluate_sell_intents(_down_scan(), (held_position,))[0]
    with pytest.raises(ValueError, match="LINE2-MON-"):
        make_sell_context(
            intent, now=_NOW, signal_id="SIG-bad", seq=1, snapshot_at=_SNAP_AT,
            account=account, positions=(held_position,), prev_close=4.5,
            daily_state=daily_state, stock_meta=stock_meta,
            risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
            data_quality=clean_data_quality, watchlist_policy=policy,
            watchlist_signal=passing_signal,
        )


# ---------------------------------------------------------------------------
# end-to-end: anomaly → intent → context → builder → renderer
# ---------------------------------------------------------------------------


async def test_sell_validated_and_rendered(
    builder, held_position, account, risk_engine, stock_meta, daily_state,
    policy, passing_signal, clean_data_quality, quiet_breaker,
) -> None:
    intent = evaluate_sell_intents(_down_scan(), (held_position,))[0]
    ctx = make_sell_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=1,
        snapshot_at=_SNAP_AT, account=account, positions=(held_position,),
        prev_close=4.5, daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal,
    )
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, MonitoringPlan)
    plan = result.plan
    assert plan.side is InstructionSide.SELL
    assert plan.status is InstructionStatus.VALIDATED
    assert plan.volume == 300
    assert plan.signal_id.startswith("LINE2-MON-")
    assert plan.debate_round_count == 1
    assert plan.instruction_id.endswith("-SELL-001")
    assert all(r.passed for r in plan.risk_summary)  # 14 passed

    msg = MessageRenderer().render_monitoring_sell(
        plan, anomaly_reason=intent.anomaly_reason
    )
    assert "【QuantMind 持仓监控 · 卖出信号】" in msg
    assert "异动触发:" in msg
    assert plan.instruction_id in msg
    assert "卖出" in msg


async def test_sell_skips_watchlist_exclusion(
    builder, held_position, account, risk_engine, stock_meta, daily_state,
    policy, quiet_breaker, clean_data_quality,
) -> None:
    # A failing watchlist_signal (liquidity 0) would block a BUY at the 5th
    # early-return, but a SELL is an EXIT and must not be trapped — it proceeds
    # to RiskEngine and validates (RiskEngine check 11 uses board/ST, not
    # avg_amount). Proves the side-aware monitoring chain skips watchlist #5.
    failing_signal = WatchlistMarketSignal(
        listed_at_trading_days=720, avg_amount_20d_yuan=0.0, last_price_yuan=4.5
    )
    intent = evaluate_sell_intents(_down_scan(), (held_position,))[0]
    ctx = make_sell_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=2,
        snapshot_at=_SNAP_AT, account=account, positions=(held_position,),
        prev_close=4.5, daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=failing_signal,
    )
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, MonitoringPlan)
    assert result.plan.status is InstructionStatus.VALIDATED


async def test_sell_frozen_by_open_ticket(
    builder, held_position, account, risk_engine, stock_meta, daily_state,
    policy, passing_signal, quiet_breaker, clean_data_quality,
) -> None:
    ticket = ReconciliationTicket(
        ticket_id="RECON-20260515-001", trade_date="2026-05-15",
        created_at=_NOW,
        deviation_report=DeviationReport(
            ticket_id="RECON-20260515-001", overall_passed=False,
            deviations=(
                FieldDeviation(
                    field="cash", expected="90000.00", actual="89998.00",
                    abs_diff=2.0, threshold=1.0, passed=False,
                ),
            ),
        ),
        expected_snapshot_id="snap-001", actual_reconciliation_id="recon-001",
        status=ReconciliationTicketStatus.OPEN,
    )
    intent = evaluate_sell_intents(_down_scan(), (held_position,))[0]
    ctx = make_sell_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=3,
        snapshot_at=_SNAP_AT, account=account, positions=(held_position,),
        prev_close=4.5, daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(ticket,),
        circuit_breaker=quiet_breaker, data_quality=clean_data_quality,
        watchlist_policy=policy, watchlist_signal=passing_signal,
    )
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, BuilderEarlyReturn)
    assert result.source is FreezeSource.TICKET_OPEN


async def test_sell_freeze_audit_stamps_line2_signal_id(
    tmp_path, held_position, account, risk_engine, stock_meta, daily_state,
    policy, passing_signal, quiet_breaker, clean_data_quality,
) -> None:
    # A bounced Line-2 candidate's audit event must carry the LINE2-MON-
    # signal_id so it is distinguishable from a Line-1 freeze (codex N-005).
    collection = InMemoryAuditCollection()
    own_builder = InstructionPlanBuilder(
        audit_store=AuditStore(collection, jsonl_path=tmp_path / "a.jsonl")
    )
    ticket = ReconciliationTicket(
        ticket_id="RECON-20260515-002", trade_date="2026-05-15", created_at=_NOW,
        deviation_report=DeviationReport(
            ticket_id="RECON-20260515-002", overall_passed=False,
            deviations=(FieldDeviation(
                field="cash", expected="90000.00", actual="89998.00",
                abs_diff=2.0, threshold=1.0, passed=False),),
        ),
        expected_snapshot_id="s", actual_reconciliation_id="r",
        status=ReconciliationTicketStatus.OPEN,
    )
    intent = evaluate_sell_intents(_down_scan(), (held_position,))[0]
    ctx = make_sell_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-9", seq=1,
        snapshot_at=_SNAP_AT, account=account, positions=(held_position,),
        prev_close=4.5, daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(ticket,),
        circuit_breaker=quiet_breaker, data_quality=clean_data_quality,
        watchlist_policy=policy, watchlist_signal=passing_signal,
    )
    result = await own_builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, BuilderEarlyReturn)
    assert collection.documents
    payload = collection.documents[-1]["payload"]
    assert payload["signal_id"] == "LINE2-MON-20260515-9"
    assert payload["line"] == "line2"


async def test_sell_zero_nav_degrades_not_crash(
    builder, held_position, risk_engine, stock_meta, daily_state, policy,
    passing_signal, quiet_breaker, clean_data_quality,
) -> None:
    # A degenerate NAV (total_assets=0) must NOT crash a SELL exit (RiskEngine
    # checks 5/8 pass SELL without a zero-NAV guard) — it degrades to a zeroed
    # position summary (codex N-005). RiskEngine check 5 fails with zero assets
    # for... SELL passes check 5; the plan builds with a zeroed summary.
    zero_account = AccountInfo(
        total_assets=0.0, available_cash=0.0, frozen_cash=0.0, market_value=0.0,
        total_pnl=0.0, total_pnl_pct=0.0, initial_capital=100_000.0,
    )
    intent = evaluate_sell_intents(_down_scan(), (held_position,))[0]
    ctx = make_sell_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=7,
        snapshot_at=_SNAP_AT, account=zero_account, positions=(held_position,),
        prev_close=4.5, daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal,
    )
    # Must not raise ValueError — returns a MonitoringPlan (REJECTED by the
    # engine's zero-assets check, or VALIDATED with a zeroed summary).
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, MonitoringPlan)
    assert result.plan.side is InstructionSide.SELL


async def test_suffixed_position_code_sells_end_to_end(
    builder, account, risk_engine, stock_meta, daily_state, policy,
    passing_signal, clean_data_quality, quiet_breaker,
) -> None:
    # A position whose code carries a .SH suffix must (1) match the anomaly
    # detector's bare-6-digit code so an intent is produced, AND (2) be
    # normalised in the context so RiskEngine exact-matches the bare order code
    # downstream — otherwise the SELL is rejected as "No position" (codex N-005
    # end-to-end suffix safety, not just the intent lookup).
    suffixed = Position(
        code="510300.SH", volume=300, available_volume=300, cost_price=4.6,
        market_value=1296.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
    )
    intents = evaluate_sell_intents(_down_scan(), (suffixed,))
    assert len(intents) == 1 and intents[0].code == _CODE
    ctx = make_sell_context(
        intents[0], now=_NOW, signal_id="LINE2-MON-20260515-1", seq=1,
        snapshot_at=_SNAP_AT, account=account, positions=(suffixed,),
        prev_close=4.5, daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal,
    )
    # The downstream positions tuple is normalised to bare codes.
    assert all("." not in p.code for p in ctx.positions)
    result = await builder.assemble_monitoring_plan(ctx)
    # RiskEngine fund_sufficiency now finds the holding (bare match) → VALIDATED.
    assert isinstance(result, MonitoringPlan)
    assert result.plan.status is InstructionStatus.VALIDATED
    assert result.plan.stock_code == _CODE


async def test_sell_rejected_when_risk_engine_blocks(
    builder, held_position, account, risk_engine, stock_meta,
    policy, passing_signal, quiet_breaker, clean_data_quality,
) -> None:
    # A -10% ETF crash drives the SELL limit to/below the exchange limit-down
    # band; RiskEngine blocks it (price_reasonability / limit_up_down_block) →
    # MonitoringPlan REJECTED (recorded with the reason, never routed).
    intent = evaluate_sell_intents(_down_scan(pct=0.90), (held_position,))[0]
    ds = DailyTradingState(
        today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(), current_price=4.05, is_in_halt_cooldown=False,
        halt_until=None,
    )
    ctx = make_sell_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=4,
        snapshot_at=_SNAP_AT, account=account, positions=(held_position,),
        prev_close=4.5, daily_state=ds, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal,
    )
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, MonitoringPlan)
    assert result.plan.status is InstructionStatus.REJECTED
    assert result.plan.rejection_reason  # a RiskEngine rule fired
    assert any(r.passed is False for r in result.plan.risk_summary)


async def test_assemble_monitoring_plan_rejects_hold(
    builder, held_position, account, risk_engine, stock_meta, daily_state,
    policy, passing_signal, quiet_breaker, clean_data_quality,
) -> None:
    intent = SellIntent(
        code=_CODE, name=_NAME, available_volume=300, limit_price=4.5,
        anomaly_reason="x", trigger_kind=AnomalyKind.PRICE_ZSCORE,
    )
    ctx = make_sell_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=5,
        snapshot_at=_SNAP_AT, account=account, positions=(held_position,),
        prev_close=4.5, daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal,
    )
    # Force a HOLD side via dataclasses.replace to exercise the guard.
    import dataclasses

    bad = dataclasses.replace(ctx, side=InstructionSide.HOLD)
    with pytest.raises(ValueError, match="HOLD"):
        await builder.assemble_monitoring_plan(bad)


def test_multi_intent_distinct_correlation_ids(
    held_position, account, risk_engine, stock_meta, daily_state, policy,
    passing_signal, clean_data_quality, quiet_breaker,
) -> None:
    # Two held codes from ONE scan share the scan signal_id but must get
    # distinct per-plan analysis_record_id / risk_validation_id (codex N-002
    # P2). Build two contexts (distinct code + seq) and assert handles differ.
    intent_a = SellIntent(
        code="510300", name="A", available_volume=100, limit_price=4.3,
        anomaly_reason="x", trigger_kind=AnomalyKind.PRICE_ZSCORE,
    )
    intent_b = SellIntent(
        code="510500", name="B", available_volume=100, limit_price=6.3,
        anomaly_reason="y", trigger_kind=AnomalyKind.PRICE_ZSCORE,
    )
    shared = "LINE2-MON-20260515-1"
    ctx_a = _ctx(
        intent_a, signal_id=shared, seq=1, account=account,
        positions=(held_position,), daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal,
    )
    ctx_b = _ctx(
        intent_b, signal_id=shared, seq=2, account=account,
        positions=(held_position,), daily_state=daily_state, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal,
    )
    assert ctx_a.signal_id == ctx_b.signal_id == shared  # shared PIT link
    assert ctx_a.analysis_record_id != ctx_b.analysis_record_id
    assert ctx_a.risk_validation_id != ctx_b.risk_validation_id


def test_renderer_monitoring_sell_rejects_non_line2_plan() -> None:
    # A validated Line-1 SELL (no LINE2-MON- signal_id) must NOT render with
    # the Line-2 monitoring header — fail closed (codex N-002 P3).
    from backend.models.instruction import (
        DataSnapshot,
        InstructionPlan,
        PositionSummary,
        RiskCheckSummary,
    )

    rs = tuple(
        RiskCheckSummary(rule_name=f"r{i}", passed=True, message="")
        for i in range(14)
    )
    line1_sell = InstructionPlan(
        instruction_id="QM-20260515-103000-510300-SELL-009",
        created_at=_NOW, valid_until=datetime(2026, 5, 15, 10, 35, tzinfo=_SH),
        trade_date="2026-05-15", stock_code=_CODE, stock_name=_NAME,
        side=InstructionSide.SELL, volume=100, limit_price=4.5,
        data_snapshot=DataSnapshot(
            snapshot_at=_SNAP_AT, quote_source="p", is_trading_day=True,
            is_trading_hours=True,
        ),
        position_summary=PositionSummary(
            pre_position_pct=0.01, post_position_pct=0.0,
            pre_total_position_pct=0.01, post_total_position_pct=0.0,
            pre_cash=90_000.0, post_cash=90_450.0,
        ),
        risk_summary=rs, risk_validation_id="v",
        signal_id="SIG-line1-pick", analysis_record_id="a", debate_round_count=1,
        invalidation_summary="x", status=InstructionStatus.VALIDATED,
    )
    with pytest.raises(ValueError, match="LINE2-MON-"):
        MessageRenderer().render_monitoring_sell(line1_sell, anomaly_reason="x")


def test_renderer_monitoring_sell_rejects_buy_plan() -> None:
    from backend.models.instruction import (
        DataSnapshot,
        InstructionPlan,
        PositionSummary,
        RiskCheckSummary,
    )

    rs = tuple(
        RiskCheckSummary(rule_name=f"r{i}", passed=True, message="")
        for i in range(14)
    )
    buy = InstructionPlan(
        instruction_id="QM-20260515-103000-510300-BUY-001",
        created_at=_NOW, valid_until=datetime(2026, 5, 15, 10, 35, tzinfo=_SH),
        trade_date="2026-05-15", stock_code=_CODE, stock_name=_NAME,
        side=InstructionSide.BUY, volume=100, limit_price=4.5,
        data_snapshot=DataSnapshot(
            snapshot_at=_SNAP_AT, quote_source="p", is_trading_day=True,
            is_trading_hours=True,
        ),
        position_summary=PositionSummary(
            pre_position_pct=0.0, post_position_pct=0.01,
            pre_total_position_pct=0.0, post_total_position_pct=0.01,
            pre_cash=90_000.0, post_cash=89_550.0,
        ),
        risk_summary=rs, risk_validation_id="v",
        signal_id="LINE2-MON-x", analysis_record_id="a", debate_round_count=1,
        invalidation_summary="x", status=InstructionStatus.VALIDATED,
    )
    with pytest.raises(ValueError, match="SELL-only"):
        MessageRenderer().render_monitoring_sell(buy, anomaly_reason="x")
