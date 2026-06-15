"""AE-004 friction model — broker-mirrored integer-分 economics."""

from __future__ import annotations

import pytest

from backend.backtest.friction import (
    FrictionError,
    apply_board_slippage_cents,
    compute_fill_economics,
)
from tests.backtest._builders import BROKER_FRICTION


def test_slippage_buy_up_sell_down() -> None:
    buy = apply_board_slippage_cents(
        order_price_cents=10_000, side_is_buy=True, slippage_bps=1.5
    )
    sell = apply_board_slippage_cents(
        order_price_cents=10_000, side_is_buy=False, slippage_bps=1.5
    )
    assert buy > 10_000
    assert sell < 10_000


def test_commission_floor_applies_on_small_gross() -> None:
    econ = compute_fill_economics(
        side_is_buy=True,
        order_price_cents=1_000,
        volume=100,
        board="sh_main",
        transfer_fee_applies=False,
        params=BROKER_FRICTION,
        apply_board_slippage=False,
    )
    # gross = 100_000 分; gross*0.00015 = 15 分 < 500 floor.
    assert econ.commission_cents == 500


def test_commission_scales_above_floor() -> None:
    econ = compute_fill_economics(
        side_is_buy=True,
        order_price_cents=100_000,
        volume=1_000,
        board="sh_main",
        transfer_fee_applies=False,
        params=BROKER_FRICTION,
        apply_board_slippage=False,
    )
    # gross = 100_000_000 分; *0.00015 = 15_000 分 > 500 floor.
    assert econ.commission_cents == 15_000


def test_stamp_tax_only_on_sell() -> None:
    common = dict(
        order_price_cents=100_000,
        volume=1_000,
        board="sh_main",
        transfer_fee_applies=False,
        params=BROKER_FRICTION,
        apply_board_slippage=False,
    )
    buy = compute_fill_economics(side_is_buy=True, **common)
    sell = compute_fill_economics(side_is_buy=False, **common)
    assert buy.stamp_tax_cents == 0
    assert sell.stamp_tax_cents > 0
    # gross 100_000_000 * 0.001 = 100_000 分.
    assert sell.stamp_tax_cents == 100_000


def test_transfer_fee_toggle() -> None:
    common = dict(
        side_is_buy=True,
        order_price_cents=100_000,
        volume=1_000,
        board="sz_main",
        params=BROKER_FRICTION,
        apply_board_slippage=False,
    )
    off = compute_fill_economics(transfer_fee_applies=False, **common)
    on = compute_fill_economics(transfer_fee_applies=True, **common)
    assert off.transfer_fee_cents == 0
    assert on.transfer_fee_cents > 0


def test_net_cash_impact_semantics() -> None:
    buy = compute_fill_economics(
        side_is_buy=True,
        order_price_cents=100_000,
        volume=1_000,
        board="sz_main",
        transfer_fee_applies=True,
        params=BROKER_FRICTION,
        apply_board_slippage=False,
    )
    assert buy.net_cents == (
        buy.gross_cents + buy.commission_cents + buy.transfer_fee_cents
    )
    sell = compute_fill_economics(
        side_is_buy=False,
        order_price_cents=100_000,
        volume=1_000,
        board="sz_main",
        transfer_fee_applies=True,
        params=BROKER_FRICTION,
        apply_board_slippage=False,
    )
    assert sell.net_cents == (
        sell.gross_cents
        - sell.commission_cents
        - sell.stamp_tax_cents
        - sell.transfer_fee_cents
    )


def test_no_board_slippage_leaves_price_verbatim() -> None:
    econ = compute_fill_economics(
        side_is_buy=True,
        order_price_cents=12_345,
        volume=100,
        board="sh_main",
        transfer_fee_applies=False,
        params=BROKER_FRICTION,
        apply_board_slippage=False,
    )
    assert econ.fill_price_cents == 12_345
    assert econ.slippage_cents == 0


def test_board_slippage_charges_slippage_cost() -> None:
    econ = compute_fill_economics(
        side_is_buy=True,
        order_price_cents=100_000,
        volume=1_000,
        board="chuangye",
        transfer_fee_applies=False,
        params=BROKER_FRICTION,
        apply_board_slippage=True,
    )
    # chuangye 3.5bp on a 100_000 分 price moves it up.
    assert econ.fill_price_cents > 100_000
    assert econ.slippage_cents > 0


def test_fail_closed_on_bad_inputs() -> None:
    with pytest.raises(FrictionError):
        compute_fill_economics(
            side_is_buy=True,
            order_price_cents=1_000,
            volume=0,
            board="sh_main",
            transfer_fee_applies=False,
            params=BROKER_FRICTION,
        )
    with pytest.raises(FrictionError):
        compute_fill_economics(
            side_is_buy=True,
            order_price_cents=0,
            volume=100,
            board="sh_main",
            transfer_fee_applies=False,
            params=BROKER_FRICTION,
        )
    with pytest.raises(FrictionError):
        compute_fill_economics(
            side_is_buy=True,
            order_price_cents=1_000,
            volume=100,
            board="unknown_board",
            transfer_fee_applies=False,
            params=BROKER_FRICTION,
            apply_board_slippage=True,
        )


def test_friction_params_reject_negative_rate() -> None:
    from backend.backtest.friction import FrictionParams

    with pytest.raises(FrictionError):
        FrictionParams(
            commission_rate=-0.1,
            min_commission_cents=500,
            stamp_tax_rate=0.001,
            transfer_fee_rate=0.0,
            slippage_bps_by_board={"sh_main": 1.5},
        )
