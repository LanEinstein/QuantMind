"""Unit tests for the portfolio validation scheduler (synthetic streams).

These tests exercise the capital layer only -- take-or-skip rules, cash
accounting, corporate-action marks -- on hand-built trade streams, never
the real panel. The rule semantics themselves are candidate E's and are
covered by test_m3_520_candidate_e.py.
"""

import numpy as np

from scripts.yeren_research.m3_520_candidate_e import CostModel, TradeE
from scripts.yeren_research.m3_520_portfolio_validation import (
    PortfolioConfig,
    _buy_cost,
    _curve_metrics,
    _sell_proceeds,
    run_portfolio,
)
from scripts.yeren_research.pit_priced_panel import PricedSeries


def _series(code, dates, opens, closes, adj):
    length = len(dates)
    return PricedSeries(
        code=code,
        dates=np.asarray(dates, dtype=np.int32),
        opens=np.asarray(opens, dtype=float),
        closes=np.asarray(closes, dtype=float),
        pct_chg=np.zeros(length),
        adj=np.asarray(adj, dtype=float),
    )


def _trade(code, signal_date, entry_index, exit_index, status="closed"):
    # Dates are one past the bar index in these fixtures (dates[i] == i+1),
    # mirroring how candidate E derives entry_date/exit_date from
    # series.dates[bar_index].
    return TradeE(
        code=code,
        entry_signal_date=signal_date,
        entry_date=entry_index + 1,
        exit_signal_date=None if status != "closed" else exit_index - 1,
        exit_date=exit_index + 1 if status == "closed" else 99_999,
        entry_price=1.0,
        exit_price=1.0 if status == "closed" else float("nan"),
        gross_return_pct=0.0,
        net_return_pct=0.0,
        mae_pct=0.0,
        entry_index=entry_index,
        exit_index=exit_index,
        status=status,
        cohort="primary",
        entry_delay_days=0,
        exit_delay_days=0,
        entry_limit_row_missing=False,
        exit_limit_row_missing=False,
    )


def _flat_series(code, days=10, price=100.0):
    dates = list(range(1, days + 1))
    return _series(
        code,
        dates,
        [price] * days,
        [price] * days,
        [2.0] * days,
    )


def _run(trades, series, **config_kwargs):
    config = PortfolioConfig(**config_kwargs)
    calendar = tuple(str(day) for day in range(1, 11))
    return run_portfolio(
        tuple(trades),
        {s.code: s for s in series},
        calendar,
        start_date=1,
        end_date=10,
        config=config,
        costs=CostModel(
            commission_rate=0.0,
            transfer_fee_rate=0.0,
            stamp_duty_rate=0.0,
            slippage_rate=0.0,
            min_commission=0.0,
        ),
    )


def test_slot_overflow_takes_codes_in_ascending_order():
    # Three same-evening signals but only two slots: the two lowest codes
    # must win, deterministically.
    trades = [
        _trade("000003.SZ", 1, 2, 9),
        _trade("000001.SZ", 1, 2, 9),
        _trade("000002.SZ", 1, 2, 9),
    ]
    result = _run(
        trades,
        [
            _flat_series("000001.SZ"),
            _flat_series("000002.SZ"),
            _flat_series("000003.SZ"),
        ],
        max_positions=2,
    )

    assert result["trades_taken"] == 2
    assert result["dropped"]["slots_full"] == 1
    assert result["dropped"]["duplicate_or_pending_security"] == 0


def test_a_signal_for_a_held_security_is_dropped_not_queued():
    trades = [
        _trade("000001.SZ", 1, 2, 9),  # held from day 2 to day 9
        _trade("000001.SZ", 3, 4, 8),  # mid-hold re-signal: dropped
    ]
    result = _run(trades, [_flat_series("000001.SZ")])

    assert result["trades_taken"] == 1
    assert result["dropped"]["duplicate_or_pending_security"] == 1


def test_exposure_cap_blocks_an_entry_that_would_breach_ten_percent():
    # Two slots at 6% each = 12% > cap 10% with max_positions raised, so the
    # second decision must be rejected by the exposure check, not by slots.
    trades = [
        _trade("000001.SZ", 1, 2, 9),
        _trade("000002.SZ", 1, 2, 9),
    ]
    result = _run(
        trades,
        [_flat_series("000001.SZ"), _flat_series("000002.SZ")],
        max_positions=5,
        position_fraction=0.06,
    )

    assert result["trades_taken"] == 1
    assert result["dropped"]["exposure_cap"] == 1


def test_insufficient_cash_at_fill_drops_the_entry():
    # Decision passes (exposure headroom raised), but the floored commission
    # dwarfs the account at fill: sizing happens at fill, the entry cannot
    # fund, and the drop is counted with equity untouched.
    trades = [_trade("000001.SZ", 1, 2, 9)]
    config = PortfolioConfig(
        initial_capital=100.0,
        position_fraction=0.5,
        total_exposure_cap=1.0,
    )
    calendar = tuple(str(day) for day in range(1, 11))
    result = run_portfolio(
        tuple(trades),
        {"000001.SZ": _flat_series("000001.SZ")},
        calendar,
        start_date=1,
        end_date=10,
        config=config,
        costs=CostModel(min_commission=1_000.0),
    )

    assert result["trades_taken"] == 1  # decided
    assert result["dropped"]["insufficient_cash_at_fill"] == 1
    assert result["equity_curve"][0] == result["equity_curve"][-1]
    assert result["open_at_window_end"]["count"] == 0


def test_mark_survives_a_corporate_action_between_entry_and_window_end():
    # Entry at raw 10 (factor 2). Later the stock does a 2-for-1 style event:
    # raw halves to 5 while the factor doubles to 4. Economic value must be
    # unchanged.
    series = _series(
        "000001.SZ",
        list(range(1, 11)),
        [10, 10, 10, 10, 5, 5, 5, 5, 5, 5],
        [10, 10, 10, 10, 5, 5, 5, 5, 5, 5],
        [2, 2, 2, 2, 4, 4, 4, 4, 4, 4],
    )
    trades = [_trade("000001.SZ", 1, 2, 10, status="open_at_window_end")]
    result = _run(trades, [series], position_fraction=0.10)

    target = PortfolioConfig().initial_capital * 0.10
    # shares = target / raw_open(10); mark = shares * raw_close(5) * (4/2)
    expected_mark = (target / 10.0) * 5.0 * 2.0
    assert np.isclose(result["open_at_window_end"]["marked_value"], expected_mark)
    assert np.isclose(result["equity_curve"][-1], PortfolioConfig().initial_capital)


def test_open_and_no_fill_positions_never_credit_exit_cash():
    trades = [
        _trade("000001.SZ", 1, 2, 9, status="open_at_window_end"),
        _trade("000002.SZ", 1, 2, 9, status="no_fill_fact"),
    ]
    flat = [_flat_series("000001.SZ"), _flat_series("000002.SZ")]
    result = _run(trades, flat)

    assert result["open_at_window_end"]["count"] == 2
    # Flat prices + zero costs: equity equals initial capital throughout.
    initial = PortfolioConfig().initial_capital
    assert all(np.isclose(v, initial) for v in result["equity_curve"])


def test_a_skipped_signals_exit_never_closes_another_position():
    # Regression: exit days must be registered at fill time. Security X's
    # stream holds three sequential signals; the middle one is skipped
    # (duplicate while the first is held). The skipped trade's exit date
    # must have no power over the position the third signal opens.
    opens = [10, 10, 10, 10, 10, 10, 30, 10, 40, 50]
    series = _series(
        "000001.SZ",
        list(range(1, 11)),
        opens,
        [10] * 10,
        [1.0] * 10,
    )
    trades = [
        _trade("000001.SZ", 1, 1, 4),  # taken: entry date 2 (open 10), exit date 5
        _trade("000001.SZ", 3, 3, 7),  # decision date 3: duplicate -> dropped
        _trade("000001.SZ", 6, 6, 8),  # taken: entry date 7 (open 30), exit date 9
    ]
    config = PortfolioConfig(
        position_fraction=0.5, max_positions=1, total_exposure_cap=1.0
    )
    calendar = tuple(str(day) for day in range(1, 11))
    result = run_portfolio(
        tuple(trades),
        {"000001.SZ": series},
        calendar,
        start_date=1,
        end_date=10,
        config=config,
        costs=CostModel(
            commission_rate=0.0,
            transfer_fee_rate=0.0,
            stamp_duty_rate=0.0,
            slippage_rate=0.0,
            min_commission=0.0,
        ),
    )

    assert result["trades_taken"] == 2
    assert result["dropped"]["duplicate_or_pending_security"] == 1
    assert result["open_at_window_end"]["count"] == 0
    # Third trade buys at the date-7 open (30) with 50% of 1,000,000, sells
    # at the date-9 open (40). The skipped trade's date-8 exit bar (open 10)
    # must play no role: crediting at it would strand ~500,000 of cash.
    shares = 500_000.0 / 30.0
    assert np.isclose(result["equity_curve"][-1], 500_000.0 + shares * 40.0)


def test_buy_sell_cash_round_trip_with_floored_commission():
    costs = CostModel(
        commission_rate=0.0,
        transfer_fee_rate=0.0,
        stamp_duty_rate=0.0,
        slippage_rate=0.0,
        min_commission=5.0,
    )
    # 100 shares at 10.0: rate commission would be 0 -> floor 5 applies.
    paid = _buy_cost(100.0, 10.0, costs)
    received = _sell_proceeds(100.0, 10.0, costs)

    assert np.isclose(paid, 1005.0)
    assert np.isclose(received, 995.0)


def test_curve_metrics_max_drawdown_and_annualization():
    curve = [100.0, 110.0, 88.0, 99.0]
    metrics = _curve_metrics(curve, PortfolioConfig())

    assert np.isclose(metrics["max_drawdown_pct"], 20.0)
    assert metrics["total_return_pct"] < 0 or metrics["total_return_pct"] > 0
    years = 4 / PortfolioConfig().trading_days_per_year
    expected_ann = ((99.0 / 100.0) ** (1.0 / years) - 1.0) * 100.0
    assert np.isclose(metrics["annualized_return_pct"], expected_ann)
