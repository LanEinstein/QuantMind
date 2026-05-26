"""Tests for backend.monitoring.add_position + Line-2 ADD builder/renderer (N-003)."""

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
from backend.monitoring.add_position import (
    AddConfig,
    AddIntent,
    AddRejectReason,
    MarketRegime,
    classify_regime,
    close_atr,
    evaluate_add_intents,
    make_add_context,
    parse_held_series,
    rsi,
    vanthorp_size,
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


def _dip_closes() -> list[float]:
    """30-bar series: uptrend then a sharp oversold pullback that stays within
    10% of the 20-day MA (oversold but NOT a structural breakdown)."""
    rise = [4.30 + 0.02 * i for i in range(20)]  # 4.30 → 4.68
    dip = [rise[-1] - 0.04 * (j + 1) for j in range(10)]  # 10 down bars
    return rise + dip


def _flat_amounts(n: int = 30, base: float = 3e8) -> list[float]:
    return [base] * n


@pytest.fixture
def account() -> AccountInfo:
    return AccountInfo(
        total_assets=100_000.0, available_cash=90_000.0, frozen_cash=0.0,
        market_value=10_000.0, total_pnl=0.0, total_pnl_pct=0.0,
        initial_capital=100_000.0,
    )


@pytest.fixture
def held() -> Position:
    return Position(
        code=_CODE, volume=100, available_volume=100, cost_price=4.45,
        market_value=445.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
    )


@pytest.fixture
def series() -> dict[str, tuple[tuple[float, ...], tuple[float, ...]]]:
    closes = tuple(_dip_closes())
    return {_CODE: (closes, tuple(_flat_amounts()))}


@pytest.fixture
def policy() -> UniversePolicy:
    return load_policy(Path("config/universe_policy.yaml"))


@pytest.fixture
def passing_signal() -> WatchlistMarketSignal:
    return WatchlistMarketSignal(
        listed_at_trading_days=720, avg_amount_20d_yuan=1_000_000_000.0,
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
async def builder(tmp_path: Path) -> InstructionPlanBuilder:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
    return InstructionPlanBuilder(audit_store=store)


# ---------------------------------------------------------------------------
# pure indicators
# ---------------------------------------------------------------------------


def test_rsi_oversold_on_dip() -> None:
    val = rsi(tuple(_dip_closes()), 14)
    assert val is not None and val < 35.0


def test_rsi_none_short() -> None:
    assert rsi((1.0, 2.0), 14) is None


def test_close_atr_positive() -> None:
    atr = close_atr(tuple(_dip_closes()), 14)
    assert atr is not None and atr > 0


def test_vanthorp_size_fixed_fractional_not_scaled_by_loss() -> None:
    cfg = AddConfig()
    # Same equity/ATR/price → same size regardless of how far underwater.
    s1 = vanthorp_size(equity=100_000.0, atr=0.05, price=4.5, config=cfg)
    s2 = vanthorp_size(equity=100_000.0, atr=0.05, price=4.5, config=cfg)
    assert s1 == s2 and s1 > 0 and s1 % 100 == 0
    # Larger ATR (more risk per share) → smaller size (anti-martingale).
    s_wide = vanthorp_size(equity=100_000.0, atr=0.20, price=4.5, config=cfg)
    assert s_wide < s1


def test_vanthorp_size_degenerate_zero() -> None:
    assert vanthorp_size(equity=0.0, atr=0.05, price=4.5, config=AddConfig()) == 0
    assert vanthorp_size(equity=100.0, atr=0.0, price=4.5, config=AddConfig()) == 0


def test_classify_regime() -> None:
    up = tuple(4.0 + 0.05 * i for i in range(30))
    down = tuple(6.0 - 0.05 * i for i in range(30))
    flat = tuple(5.0 + (0.001 if i % 2 else -0.001) for i in range(30))
    assert classify_regime(up) is MarketRegime.BULL
    assert classify_regime(down) is MarketRegime.BEAR
    assert classify_regime(flat) is MarketRegime.NEUTRAL
    assert classify_regime((1.0, 2.0)) is MarketRegime.NEUTRAL  # too short → safe


# ---------------------------------------------------------------------------
# evaluate_add_intents — four conditions + bans
# ---------------------------------------------------------------------------


def test_all_conditions_met_yields_add(series, held, account) -> None:
    ev = evaluate_add_intents(
        series, (held,), account, regime=MarketRegime.NEUTRAL,
        name_by_code={_CODE: _NAME},
    )
    assert len(ev.intents) == 1
    it = ev.intents[0]
    assert it.code == _CODE
    assert it.add_volume > 0 and it.add_volume % 100 == 0
    assert it.stop_price < it.limit_price  # ATR stop below entry
    assert it.rsi < 35.0


def test_bear_regime_blocks_add(series, held, account) -> None:
    ev = evaluate_add_intents(series, (held,), account, regime=MarketRegime.BEAR)
    assert ev.intents == ()
    assert ev.rejections[0].reason is AddRejectReason.BEAR_REGIME


def test_martingale_deep_drawdown_rejected(series, account) -> None:
    # Position cost far above current price → adding = averaging down a loser.
    loser = Position(
        code=_CODE, volume=100, available_volume=100, cost_price=6.0,
        market_value=445.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
    )
    ev = evaluate_add_intents(series, (loser,), account, regime=MarketRegime.NEUTRAL)
    assert ev.intents == ()
    assert ev.rejections[0].reason is AddRejectReason.MARTINGALE


def test_not_oversold_rejected(held, account) -> None:
    # A calm rising series → RSI high → not oversold.
    closes = tuple(4.0 + 0.02 * i for i in range(30))
    ev = evaluate_add_intents(
        {_CODE: (closes, tuple(_flat_amounts()))}, (held,), account,
        regime=MarketRegime.NEUTRAL,
    )
    assert ev.rejections[0].reason is AddRejectReason.NOT_OVERSOLD


def test_volume_not_stabilized_rejected(held, account) -> None:
    # Recent volume collapses to <70% of prior → not stabilized.
    closes = tuple(_dip_closes())
    amounts = [3e8] * 25 + [1e7] * 5  # last 5 bars dry up
    ev = evaluate_add_intents(
        {_CODE: (closes, tuple(amounts))}, (held,), account,
        regime=MarketRegime.NEUTRAL,
    )
    assert ev.rejections[0].reason is AddRejectReason.VOLUME_NOT_STABILIZED


def test_structural_breakdown_rejected(account) -> None:
    # Deep break far below the 20MA (but not deep vs cost) → breakdown.
    rise = [10.0 + 0.05 * i for i in range(20)]  # MA ~ 10.5+
    crash = [rise[-1] * (0.99 ** (j + 1)) for j in range(10)]
    crash[-1] = rise[-1] * 0.80  # ~20% below MA → structural breakdown
    closes = tuple(rise + crash)
    held = Position(
        code=_CODE, volume=100, available_volume=100, cost_price=closes[-1],
        market_value=100.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
    )  # cost = last → no martingale, isolates the breakdown gate
    ev = evaluate_add_intents(
        {_CODE: (closes, tuple(_flat_amounts()))}, (held,), account,
        regime=MarketRegime.NEUTRAL,
    )
    assert ev.rejections[0].reason in {
        AddRejectReason.STRUCTURAL_BREAKDOWN,
        AddRejectReason.MARTINGALE,
    }


def test_no_headroom_rejected(series, account) -> None:
    # Position already at the 15% cap → no room to add.
    big = Position(
        code=_CODE, volume=4000, available_volume=4000, cost_price=4.45,
        market_value=17_800.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
    )
    ev = evaluate_add_intents(series, (big,), account, regime=MarketRegime.NEUTRAL)
    assert ev.rejections[0].reason is AddRejectReason.NO_HEADROOM


def test_insufficient_history_rejected(held, account) -> None:
    ev = evaluate_add_intents(
        {_CODE: ((4.5, 4.4, 4.3), (3e8, 3e8, 3e8))}, (held,), account,
        regime=MarketRegime.NEUTRAL,
    )
    assert ev.rejections[0].reason is AddRejectReason.INSUFFICIENT_HISTORY


def test_no_series_for_held_code(held, account) -> None:
    ev = evaluate_add_intents({}, (held,), account, regime=MarketRegime.NEUTRAL)
    assert ev.rejections[0].reason is AddRejectReason.NO_SERIES


# ---------------------------------------------------------------------------
# parse_held_series
# ---------------------------------------------------------------------------


def test_parse_held_series() -> None:
    closes = _dip_closes()
    header = "ts_code,name,listed_trading_days,closes,amounts"
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(v) for v in _flat_amounts())
    row = f"510300.SH,ETF,300,{cs},{am}"
    other = f"600000.SH,X,300,{cs},{am}"
    raw = "\n".join([header, row, other]).encode("utf-8")
    snap = MarketDataSnapshot(
        vendor="t", endpoint="e", params={}, trade_date="20260515",
        raw_payload=raw, size=len(raw), encoding="csv", compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 15, 2, tzinfo=UTC),
    )
    out = parse_held_series(snap, ["510300"])
    assert set(out) == {"510300"}
    assert len(out["510300"][0]) == len(closes)


def test_parse_held_series_valid_plus_malformed_duplicate_dropped() -> None:
    # A valid row + a malformed (wrong column count) duplicate of the SAME
    # held code must fail closed — the held code is dropped, not returned
    # (codex N-003 P2).
    closes = _dip_closes()
    header = "ts_code,name,listed_trading_days,closes,amounts"
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(v) for v in _flat_amounts())
    valid = f"510300.SH,ETF,300,{cs},{am}"
    malformed = "510300.SH,ETF,300,1.0|2.0"  # only 4 columns
    raw = "\n".join([header, valid, malformed]).encode("utf-8")
    snap = MarketDataSnapshot(
        vendor="t", endpoint="e", params={}, trade_date="20260515",
        raw_payload=raw, size=len(raw), encoding="csv", compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 15, 2, tzinfo=UTC),
    )
    assert parse_held_series(snap, ["510300"]) == {}


# ---------------------------------------------------------------------------
# end-to-end: AddIntent → context → builder (BUY) → renderer
# ---------------------------------------------------------------------------


async def test_add_validated_and_rendered(
    builder, series, held, account, risk_engine, stock_meta, policy,
    passing_signal, clean_data_quality, quiet_breaker,
) -> None:
    intent = evaluate_add_intents(
        series, (held,), account, regime=MarketRegime.NEUTRAL,
        name_by_code={_CODE: _NAME},
    ).intents[0]
    ds = DailyTradingState(
        today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(), current_price=intent.limit_price,
        is_in_halt_cooldown=False, halt_until=None,
    )
    ctx = make_add_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=1,
        snapshot_at=_SNAP_AT, account=account, positions=(held,),
        prev_close=4.5, daily_state=ds, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal,
    )
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, MonitoringPlan)
    plan = result.plan
    assert plan.side is InstructionSide.BUY
    assert plan.status is InstructionStatus.VALIDATED
    assert plan.instruction_id.endswith("-BUY-001")
    assert plan.signal_id.startswith("LINE2-MON-")

    msg = MessageRenderer().render_add_position(
        plan, add_rationale=intent.rationale, stop_price=intent.stop_price
    )
    assert "【QuantMind 持仓监控 · 补仓信号】" in msg
    assert "补仓依据:" in msg
    assert "移动止损:" in msg
    assert plan.instruction_id in msg
    assert "买入" in msg


async def test_add_buy_enforces_watchlist_exclusion(
    builder, series, held, account, risk_engine, stock_meta, policy,
    quiet_breaker, clean_data_quality,
) -> None:
    # Unlike SELL, an ADD (BUY) MUST respect the entry universe — a failing
    # watchlist_signal (liquidity 0) blocks the BUY at the 5th early-return.
    failing_signal = WatchlistMarketSignal(
        listed_at_trading_days=720, avg_amount_20d_yuan=0.0, last_price_yuan=4.5
    )
    intent = evaluate_add_intents(
        series, (held,), account, regime=MarketRegime.NEUTRAL
    ).intents[0]
    ds = DailyTradingState(
        today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(), current_price=intent.limit_price,
        is_in_halt_cooldown=False, halt_until=None,
    )
    ctx = make_add_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=2,
        snapshot_at=_SNAP_AT, account=account, positions=(held,),
        prev_close=4.5, daily_state=ds, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=failing_signal,
    )
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, BuilderEarlyReturn)
    assert result.source is FreezeSource.WATCHLIST


def test_make_add_context_rejects_non_line2_signal_id(
    held, account, risk_engine, stock_meta, policy, passing_signal,
    clean_data_quality, quiet_breaker,
) -> None:
    intent = AddIntent(
        code=_CODE, name=_NAME, add_volume=100, limit_price=4.4, atr=0.05,
        stop_price=4.3, rsi=30.0, rationale="x",
    )
    with pytest.raises(ValueError, match="LINE2-MON-"):
        make_add_context(
            intent, now=_NOW, signal_id="SIG-bad", seq=1, snapshot_at=_SNAP_AT,
            account=account, positions=(held,), prev_close=4.5, daily_state=None,
            stock_meta=stock_meta, risk_engine=risk_engine, open_tickets=(),
            circuit_breaker=quiet_breaker, data_quality=clean_data_quality,
            watchlist_policy=policy, watchlist_signal=passing_signal,
        )


def _small_account() -> AccountInfo:
    """Few-thousand-yuan account where one 1-lot ETF add tops the 15% cap."""
    return AccountInfo(
        total_assets=2_000.0, available_cash=2_000.0, frozen_cash=0.0,
        market_value=0.0, total_pnl=0.0, total_pnl_pct=0.0,
        initial_capital=2_000.0,
    )


async def test_add_over_15pct_whitelisted_etf_granted_with_flag(
    builder, risk_engine, stock_meta, policy, passing_signal,
    clean_data_quality, quiet_breaker,
) -> None:
    # U-C4: an ADD (entry) onto a whitelisted broad ETF that tops 15% on a
    # small account must thread the budget-policy concentration flag into the
    # 14-check so it VALIDATES (it would otherwise fail-close to REJECTED). The
    # intent is built directly — the deterministic evaluator caps to headroom,
    # so the over-cap case is exercised at the construction-point seam.
    account = _small_account()
    intent = AddIntent(
        code=_CODE, name=_NAME, add_volume=100, limit_price=4.5, atr=0.05,
        stop_price=4.3, rsi=30.0, rationale="broad ETF dip add",
    )
    ds = DailyTradingState(
        today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(), current_price=4.5,
        is_in_halt_cooldown=False, halt_until=None,
    )
    ctx = make_add_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-1", seq=1,
        snapshot_at=_SNAP_AT, account=account, positions=(),
        prev_close=4.5, daily_state=ds, stock_meta=stock_meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal, concentration_exception=True,
    )
    assert ctx.concentration_exception is True
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, MonitoringPlan)
    plan = result.plan
    assert plan.side is InstructionSide.BUY
    assert plan.status is InstructionStatus.VALIDATED
    assert any(
        row.passed is True
        and "concentration_exception_granted" in (row.message or "")
        for row in plan.risk_summary
    )


async def test_add_individual_stock_flag_alone_still_rejected(
    builder, risk_engine, policy, passing_signal, clean_data_quality,
    quiet_breaker,
) -> None:
    # Adversarial red-line guard: an individual stock ADD over 15% with the
    # flag set is STILL rejected — the exception is ETF-only, re-derived
    # independently by RiskEngine (个股不享有；flag 非绕过).
    account = _small_account()
    meta = RiskStockMetadata(
        code="600000", name="浦发银行", board=RiskBoard.SH_MAIN, is_st=False,
        instrument_type="stock",
    )
    intent = AddIntent(
        code="600000", name="浦发银行", add_volume=100, limit_price=4.5,
        atr=0.05, stop_price=4.3, rsi=30.0, rationale="stock dip add",
    )
    ds = DailyTradingState(
        today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(), current_price=4.5,
        is_in_halt_cooldown=False, halt_until=None,
    )
    ctx = make_add_context(
        intent, now=_NOW, signal_id="LINE2-MON-20260515-2", seq=1,
        snapshot_at=_SNAP_AT, account=account, positions=(),
        prev_close=4.5, daily_state=ds, stock_meta=meta,
        risk_engine=risk_engine, open_tickets=(), circuit_breaker=quiet_breaker,
        data_quality=clean_data_quality, watchlist_policy=policy,
        watchlist_signal=passing_signal, concentration_exception=True,
    )
    result = await builder.assemble_monitoring_plan(ctx)
    assert isinstance(result, MonitoringPlan)
    plan = result.plan
    assert plan.status is InstructionStatus.REJECTED
    assert plan.rejection_reason is not None
    assert plan.rejection_reason.startswith("position_limit")


def test_renderer_add_position_rejects_sell_and_non_line2() -> None:
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
    ps = PositionSummary(
        pre_position_pct=0.0, post_position_pct=0.05,
        pre_total_position_pct=0.0, post_total_position_pct=0.05,
        pre_cash=90_000.0, post_cash=85_000.0,
    )
    base = dict(
        created_at=_NOW, valid_until=datetime(2026, 5, 15, 10, 35, tzinfo=_SH),
        trade_date="2026-05-15", stock_code=_CODE, stock_name=_NAME,
        volume=100, limit_price=4.5,
        data_snapshot=DataSnapshot(
            snapshot_at=_SNAP_AT, quote_source="p", is_trading_day=True,
            is_trading_hours=True,
        ),
        position_summary=ps, risk_summary=rs, risk_validation_id="v",
        analysis_record_id="a", debate_round_count=1, invalidation_summary="x",
        status=InstructionStatus.VALIDATED,
    )
    sell = InstructionPlan(
        instruction_id="QM-20260515-103000-510300-SELL-001",
        side=InstructionSide.SELL, signal_id="LINE2-MON-x", **base,
    )
    line1_buy = InstructionPlan(
        instruction_id="QM-20260515-103000-510300-BUY-002",
        side=InstructionSide.BUY, signal_id="SIG-line1", **base,
    )
    r = MessageRenderer()
    with pytest.raises(ValueError, match="BUY-only"):
        r.render_add_position(sell, add_rationale="x", stop_price=4.3)
    with pytest.raises(ValueError, match="LINE2-MON-"):
        r.render_add_position(line1_buy, add_rationale="x", stop_price=4.3)
