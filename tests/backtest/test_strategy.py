"""AE-004 daily-rhythm strategy adapter — selector + ≤5-slot rotation."""

from __future__ import annotations

from backend.backtest.strategy import (
    CodeHealth,
    DailySignals,
    HeldPosition,
    PortfolioView,
    decide_day,
)
from backend.slot_portfolio import ChallengerState, IncumbentState, propose_rotation
from tests.backtest._builders import (
    candidate,
    make_bar,
    make_rotation_config,
    make_strategy_config,
    strong_challenger_health,
    weak_incumbent_health,
)


def _health(pct: float, score: float) -> CodeHealth:
    return CodeHealth(line1_percentile=pct, composite_score=score)


def test_buys_fill_open_slots_from_shortlist() -> None:
    signals = DailySignals(
        trade_date="20260102",
        quant_candidates=(candidate("600000", 0.9), candidate("600001", 0.8)),
        health={"600000": _health(0.9, 0.9), "600001": _health(0.8, 0.8)},
    )
    view = PortfolioView(
        trade_date="20260102", total_equity_cents=900_000, cash_cents=900_000
    )
    bars = {
        "600000": make_bar("600000", "20260102", open_cents=1_000),
        "600001": make_bar("600001", "20260102", open_cents=1_000),
    }
    decision = decide_day(
        signals=signals, view=view, bars=bars, config=make_strategy_config()
    )
    assert decision.sell_codes == ()
    assert set(decision.buy_codes) == {"600000", "600001"}
    for order in decision.orders:
        assert order.side_is_buy
        assert order.volume % 100 == 0 and order.volume > 0


def test_single_stock_cap_limits_volume() -> None:
    signals = DailySignals(
        trade_date="20260102",
        quant_candidates=(candidate("600000", 0.9),),
        health={"600000": _health(0.9, 0.9)},
    )
    view = PortfolioView(
        trade_date="20260102", total_equity_cents=900_000, cash_cents=900_000
    )
    bars = {"600000": make_bar("600000", "20260102", open_cents=100)}
    tight = decide_day(
        signals=signals,
        view=view,
        bars=bars,
        config=make_strategy_config(single_stock_cap_percent=5),
    )
    (order,) = tight.orders
    # bought notional must respect the 5% cap.
    assert order.volume * 100 <= 900_000 * 5 // 100


def test_rotation_sells_weak_incumbent() -> None:
    # Sanity: the rotation engine itself fires on this state.
    proposal = propose_rotation(
        [
            IncumbentState(
                code="600000",
                line1_percentile=0.30,
                composite_score=0.10,
                entry_percentile=0.60,
                holding_age_trading_days=10,
                protective_stop_active=False,
                hard_exit_pending=False,
                score_median_20d=0.0,
                score_mad_20d=0.0,
                anomaly_flag_active=False,
                drawdown_from_local_high=0.20,
                suspended=False,
                limit_down_unsellable=False,
                corporate_action_unsafe=False,
            )
        ],
        [
            ChallengerState(
                code="600001",
                qualified=True,
                line1_percentile=0.90,
                composite_score=0.90,
            )
        ],
        make_rotation_config(),
    )
    assert proposal.should_rotate

    signals = DailySignals(
        trade_date="20260110",
        quant_candidates=(candidate("600001", 0.9), candidate("600000", 0.1)),
        health={
            "600000": weak_incumbent_health(),
            "600001": strong_challenger_health(),
        },
    )
    view = PortfolioView(
        trade_date="20260110",
        total_equity_cents=900_000,
        cash_cents=400_000,
        holdings=(HeldPosition("600000", 1_000, holding_age_trading_days=10),),
    )
    bars = {
        "600000": make_bar("600000", "20260110", open_cents=1_000),
        "600001": make_bar("600001", "20260110", open_cents=1_000),
    }
    decision = decide_day(
        signals=signals, view=view, bars=bars, config=make_strategy_config()
    )
    assert decision.sell_codes == ("600000",)
    # the evicted incumbent is not re-bought the same day.
    assert "600000" not in decision.buy_codes


def test_no_rotation_when_incumbent_not_weak() -> None:
    signals = DailySignals(
        trade_date="20260110",
        quant_candidates=(candidate("600001", 0.9), candidate("600000", 0.8)),
        health={
            "600000": _health(0.95, 0.95),  # strong incumbent — not weak
            "600001": strong_challenger_health(),
        },
    )
    view = PortfolioView(
        trade_date="20260110",
        total_equity_cents=900_000,
        cash_cents=0,
        holdings=(HeldPosition("600000", 1_000, holding_age_trading_days=10),),
    )
    bars = {
        "600000": make_bar("600000", "20260110", open_cents=1_000),
        "600001": make_bar("600001", "20260110", open_cents=1_000),
    }
    decision = decide_day(
        signals=signals, view=view, bars=bars, config=make_strategy_config()
    )
    assert decision.sell_codes == ()
