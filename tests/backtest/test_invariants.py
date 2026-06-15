"""AE-004 closed-form invariants — cash/position/fee/exposure (break N=2)."""

from __future__ import annotations

from backend.backtest.invariants import (
    ExposureObservation,
    InvariantVerdict,
    check_invariants,
)
from backend.backtest.portfolio import AppliedFill, OpeningLot
from tests.backtest._builders import BROKER_FRICTION


def _fill(
    *,
    side_is_buy: bool,
    code: str = "600000",
    volume: int = 1_000,
    fill_price_cents: int = 100_000,
    board: str = "sh_main",
    transfer: bool = False,
    tamper_commission: int | None = None,
) -> AppliedFill:
    """A faithfully-priced fill (so the fee-recompute invariant passes)."""
    gross = fill_price_cents * volume
    commission = round(max(gross * 0.00015, 500))
    stamp = 0 if side_is_buy else round(gross * 0.001)
    transfer_fee = round(gross * 0.0000341) if transfer else 0
    if side_is_buy:
        net = gross + commission + transfer_fee
    else:
        net = gross - commission - stamp - transfer_fee
    return AppliedFill(
        trade_date="20260102",
        code=code,
        side_is_buy=side_is_buy,
        volume=volume,
        fill_price_cents=fill_price_cents,
        gross_cents=gross,
        commission_cents=tamper_commission
        if tamper_commission is not None
        else commission,
        stamp_tax_cents=stamp,
        transfer_fee_cents=transfer_fee,
        slippage_cents=0,
        net_cents=net,
        board=board,
        transfer_fee_applies=transfer,
    )


def test_clean_run_is_consistent() -> None:
    buy = _fill(side_is_buy=True)
    report = check_invariants(
        initial_cash_cents=1_000_000_000,
        fills=[buy],
        final_cash_cents=1_000_000_000 - buy.net_cents,
        final_positions=(("600000", 1_000),),
        params=BROKER_FRICTION,
        exposures=(
            ExposureObservation(
                trade_date="20260102",
                code="600000",
                position_value_cents=100_000_000,
                total_holdings_value_cents=100_000_000,
                total_equity_cents=1_000_000_000,
            ),
        ),
    )
    assert report.verdict is InvariantVerdict.CONSISTENT
    assert report.consistent


def test_cash_conservation_violation() -> None:
    buy = _fill(side_is_buy=True)
    report = check_invariants(
        initial_cash_cents=1_000_000_000,
        fills=[buy],
        final_cash_cents=999_999_999,  # wrong final cash
        final_positions=(("600000", 1_000),),
        params=BROKER_FRICTION,
    )
    assert report.verdict is InvariantVerdict.DIVERGENT
    assert any(v.kind == "cash_conservation" for v in report.violations)


def test_position_conservation_final_mismatch() -> None:
    buy = _fill(side_is_buy=True)
    report = check_invariants(
        initial_cash_cents=1_000_000_000,
        fills=[buy],
        final_cash_cents=1_000_000_000 - buy.net_cents,
        final_positions=(("600000", 500),),  # recomputed is 1000
        params=BROKER_FRICTION,
    )
    assert report.verdict is InvariantVerdict.DIVERGENT
    assert any(v.kind == "position_conservation" for v in report.violations)


def test_position_conservation_oversell_negative() -> None:
    sell = _fill(side_is_buy=False, volume=1_000)
    report = check_invariants(
        initial_cash_cents=1_000_000_000,
        fills=[sell],  # sold with no opening / prior buy → net -1000
        final_cash_cents=1_000_000_000 + sell.net_cents,
        final_positions=(),
        params=BROKER_FRICTION,
    )
    assert report.verdict is InvariantVerdict.DIVERGENT
    assert any(v.kind == "position_conservation" for v in report.violations)


def test_fee_recompute_catches_tampered_commission() -> None:
    buy = _fill(side_is_buy=True, tamper_commission=1)  # commission lied about
    report = check_invariants(
        initial_cash_cents=1_000_000_000,
        fills=[buy],
        final_cash_cents=1_000_000_000 - buy.net_cents,
        final_positions=(("600000", 1_000),),
        params=BROKER_FRICTION,
    )
    assert report.verdict is InvariantVerdict.DIVERGENT
    assert any(v.kind == "fee_recompute" for v in report.violations)


def test_single_stock_cap_violation() -> None:
    report = check_invariants(
        initial_cash_cents=1_000_000,
        fills=[],
        final_cash_cents=1_000_000,
        params=BROKER_FRICTION,
        exposures=(
            ExposureObservation(
                trade_date="20260102",
                code="600000",
                position_value_cents=200,  # 20% of 1000 equity > 15%
                total_holdings_value_cents=200,
                total_equity_cents=1_000,
            ),
        ),
    )
    assert report.verdict is InvariantVerdict.DIVERGENT
    assert any(v.kind == "single_stock_cap" for v in report.violations)


def test_total_position_cap_violation() -> None:
    report = check_invariants(
        initial_cash_cents=1_000_000,
        fills=[],
        final_cash_cents=1_000_000,
        params=BROKER_FRICTION,
        single_stock_cap_percent=100,  # disable single-stock so total fires alone
        exposures=(
            ExposureObservation(
                trade_date="20260102",
                code="600000",
                position_value_cents=800,
                total_holdings_value_cents=800,  # 80% of 1000 > 70%
                total_equity_cents=1_000,
            ),
        ),
    )
    assert report.verdict is InvariantVerdict.DIVERGENT
    assert any(v.kind == "total_position_cap" for v in report.violations)


def test_exposure_exactly_at_cap_is_consistent() -> None:
    report = check_invariants(
        initial_cash_cents=1_000_000,
        fills=[],
        final_cash_cents=1_000_000,
        params=BROKER_FRICTION,
        exposures=(
            ExposureObservation(
                trade_date="20260102",
                code="600000",
                position_value_cents=150,  # exactly 15%
                total_holdings_value_cents=700,  # exactly 70%
                total_equity_cents=1_000,
            ),
        ),
    )
    assert report.verdict is InvariantVerdict.CONSISTENT


def test_opening_position_conservation() -> None:
    sell = _fill(side_is_buy=False, volume=400)
    report = check_invariants(
        initial_cash_cents=1_000_000_000,
        fills=[sell],
        final_cash_cents=1_000_000_000 + sell.net_cents,
        opening_positions=(OpeningLot("600000", 1_000, 100_000),),
        final_positions=(("600000", 600),),
        params=BROKER_FRICTION,
    )
    assert report.verdict is InvariantVerdict.CONSISTENT
