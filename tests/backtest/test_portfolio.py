"""AE-004 portfolio accounting — integer-分 cash + holdings + MTM."""

from __future__ import annotations

import pytest

from backend.backtest.portfolio import (
    AppliedFill,
    BacktestPortfolio,
    EquitySnapshot,
    OpeningLot,
    PortfolioError,
    PositionMark,
)


def _buy(code: str, volume: int, price: int, net: int) -> AppliedFill:
    return AppliedFill(
        trade_date="20260102",
        code=code,
        side_is_buy=True,
        volume=volume,
        fill_price_cents=price,
        gross_cents=price * volume,
        commission_cents=net - price * volume,
        stamp_tax_cents=0,
        transfer_fee_cents=0,
        slippage_cents=0,
        net_cents=net,
        board="sh_main",
        transfer_fee_applies=False,
    )


def _sell(code: str, volume: int, price: int, net: int) -> AppliedFill:
    return AppliedFill(
        trade_date="20260103",
        code=code,
        side_is_buy=False,
        volume=volume,
        fill_price_cents=price,
        gross_cents=price * volume,
        commission_cents=price * volume - net,
        stamp_tax_cents=0,
        transfer_fee_cents=0,
        slippage_cents=0,
        net_cents=net,
        board="sh_main",
        transfer_fee_applies=False,
    )


def test_buy_debits_cash_and_adds_shares() -> None:
    p = BacktestPortfolio(initial_cash_cents=1_000_000)
    p.apply(_buy("600000", 100, 1_000, net=100_500))
    assert p.cash_cents == 1_000_000 - 100_500
    assert p.held_volume("600000") == 100


def test_sell_credits_cash_and_removes_shares() -> None:
    p = BacktestPortfolio(initial_cash_cents=1_000_000)
    p.apply(_buy("600000", 100, 1_000, net=100_500))
    p.apply(_sell("600000", 100, 1_100, net=109_500))
    assert p.held_volume("600000") == 0
    assert "600000" not in p.holdings_snapshot()
    assert p.cash_cents == 1_000_000 - 100_500 + 109_500


def test_oversell_fails_closed() -> None:
    p = BacktestPortfolio(initial_cash_cents=1_000_000)
    p.apply(_buy("600000", 100, 1_000, net=100_500))
    with pytest.raises(PortfolioError):
        p.apply(_sell("600000", 200, 1_100, net=219_000))


def test_mark_computes_equity_with_frozen_cash() -> None:
    p = BacktestPortfolio(initial_cash_cents=1_000_000, frozen_cash_cents=50_000)
    p.apply(_buy("600000", 100, 1_000, net=100_000))
    snap = p.mark(trade_date="20260102", close_marks_cents={"600000": 1_200})
    assert snap.market_value_cents == 100 * 1_200
    assert snap.cash_cents == 900_000
    assert snap.total_equity_cents == 900_000 + 50_000 + 120_000


def test_mark_missing_mark_fails_closed() -> None:
    p = BacktestPortfolio(initial_cash_cents=1_000_000)
    p.apply(_buy("600000", 100, 1_000, net=100_000))
    with pytest.raises(PortfolioError):
        p.mark(trade_date="20260102", close_marks_cents={})


def test_duplicate_opening_lot_rejected() -> None:
    with pytest.raises(PortfolioError):
        BacktestPortfolio(
            initial_cash_cents=1_000_000,
            opening_positions=(
                OpeningLot("600000", 100, 1_000),
                OpeningLot("600000", 200, 1_000),
            ),
        )


def test_equity_snapshot_rejects_duplicate_codes() -> None:
    with pytest.raises(PortfolioError):
        EquitySnapshot(
            trade_date="20260102",
            cash_cents=0,
            market_value_cents=0,
            total_equity_cents=0,
            positions=(
                PositionMark("600000", 100, 1_000, 100_000),
                PositionMark("600000", 100, 1_000, 100_000),
            ),
        )


def test_holdings_snapshot_is_a_fresh_dict() -> None:
    p = BacktestPortfolio(initial_cash_cents=1_000_000)
    p.apply(_buy("600000", 100, 1_000, net=100_000))
    snap = p.holdings_snapshot()
    snap["600000"] = 999  # mutating the copy must not affect the portfolio
    assert p.held_volume("600000") == 100
