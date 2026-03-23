"""Comprehensive tests for MockBroker — A-share simulation engine."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from backend.broker.mock_broker import MockBroker, get_price_limits
from backend.broker.models import (
    BrokerConfig,
    OrderDirection,
    OrderStatus,
    OrderType,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _trading_time() -> dt.datetime:
    return dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)


def _non_trading_time() -> dt.datetime:
    return dt.datetime(2026, 3, 21, 10, 0, tzinfo=SHANGHAI)


def _lunch_time() -> dt.datetime:
    return dt.datetime(2026, 3, 23, 12, 0, tzinfo=SHANGHAI)


@pytest.fixture()
def config() -> BrokerConfig:
    return BrokerConfig(
        initial_capital=1_000_000.0,
        commission_rate=0.0003,
        stamp_tax_rate=0.001,
        slippage_bps=2,
        min_commission=5.0,
    )


@pytest.fixture()
def broker(config: BrokerConfig) -> MockBroker:
    return MockBroker(
        config=config,
        now_func=_trading_time,
    )


# -- Price limits --


class TestPriceLimits:
    def test_main_board_sh_10_pct(self) -> None:
        low, high = get_price_limits("600519", 100.0)
        assert low == 90.0
        assert high == 110.0

    def test_main_board_sz_10_pct(self) -> None:
        low, high = get_price_limits("000001", 100.0)
        assert low == 90.0
        assert high == 110.0

    def test_chinext_20_pct(self) -> None:
        low, high = get_price_limits("300001", 100.0)
        assert low == 80.0
        assert high == 120.0

    def test_star_30_pct(self) -> None:
        low, high = get_price_limits("688001", 100.0)
        assert low == 70.0
        assert high == 130.0

    def test_rounding(self) -> None:
        low, high = get_price_limits("600519", 9.99)
        assert low == pytest.approx(8.99, abs=0.01)
        assert high == pytest.approx(10.99, abs=0.01)

    def test_zero_prev_close(self) -> None:
        low, high = get_price_limits("600519", 0.0)
        assert low == 0.0
        assert high == 0.0


# -- Buy lifecycle --


class TestBuyLifecycle:
    @pytest.mark.asyncio
    async def test_buy_creates_order_and_trade(
        self, broker: MockBroker
    ) -> None:
        result = await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert result.success
        orders = await broker.get_orders(OrderStatus.FILLED)
        assert len(orders) == 1
        trades = await broker.get_trades()
        assert len(trades) == 1

    @pytest.mark.asyncio
    async def test_buy_creates_position(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        positions = await broker.get_positions()
        assert len(positions) == 1
        assert positions[0].code == "600519"
        assert positions[0].volume == 100

    @pytest.mark.asyncio
    async def test_buy_deducts_cash(self, broker: MockBroker) -> None:
        account_before = await broker.get_account()
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        account_after = await broker.get_account()
        assert account_after.available_cash < account_before.available_cash


# -- Sell lifecycle --


class TestSellLifecycle:
    @pytest.mark.asyncio
    async def test_sell_after_advance_day(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.advance_day()
        result = await broker.place_order(
            "600519", 101.0, 100, OrderDirection.SELL, OrderType.LIMIT
        )
        assert result.success
        positions = await broker.get_positions()
        # Position should be gone or zero volume
        assert len(positions) == 0 or positions[0].volume == 0

    @pytest.mark.asyncio
    async def test_sell_stamp_tax_applied(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.advance_day()
        await broker.place_order(
            "600519", 101.0, 100, OrderDirection.SELL, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        sell_trade = [t for t in trades if t.direction == OrderDirection.SELL]
        assert len(sell_trade) == 1
        assert sell_trade[0].stamp_tax > 0

    @pytest.mark.asyncio
    async def test_buy_has_zero_stamp_tax(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        assert trades[0].stamp_tax == 0.0


# -- Rejections --


class TestRejections:
    @pytest.mark.asyncio
    async def test_insufficient_funds(self, broker: MockBroker) -> None:
        result = await broker.place_order(
            "600519", 50000.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success
        assert "insufficient" in result.message.lower()

    @pytest.mark.asyncio
    async def test_volume_not_multiple_of_100(
        self, broker: MockBroker
    ) -> None:
        result = await broker.place_order(
            "600519", 10.0, 150, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success
        assert "volume" in result.message.lower()

    @pytest.mark.asyncio
    async def test_volume_zero(self, broker: MockBroker) -> None:
        result = await broker.place_order(
            "600519", 10.0, 0, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_sell_without_position(self, broker: MockBroker) -> None:
        result = await broker.place_order(
            "600519", 100.0, 100, OrderDirection.SELL, OrderType.LIMIT
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_sell_exceeds_position(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.advance_day()
        result = await broker.place_order(
            "600519", 100.0, 200, OrderDirection.SELL, OrderType.LIMIT
        )
        assert not result.success


# -- T+1 --


class TestTPlusOne:
    @pytest.mark.asyncio
    async def test_cannot_sell_today_buy(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        result = await broker.place_order(
            "600519", 101.0, 100, OrderDirection.SELL, OrderType.LIMIT
        )
        assert not result.success
        assert "T+1" in result.message or "available" in result.message.lower()

    @pytest.mark.asyncio
    async def test_can_sell_after_advance_day(
        self, broker: MockBroker
    ) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.advance_day()
        result = await broker.place_order(
            "600519", 101.0, 100, OrderDirection.SELL, OrderType.LIMIT
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_partial_availability(self, broker: MockBroker) -> None:
        # Buy 200 yesterday (simulate by advance_day), buy 100 today
        await broker.place_order(
            "600519", 100.0, 200, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.advance_day()
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        positions = await broker.get_positions()
        pos = positions[0]
        # 200 from yesterday available, 100 from today not available
        assert pos.available_volume == 200
        assert pos.volume == 300


# -- Trading hours --


class TestTradingHours:
    @pytest.mark.asyncio
    async def test_rejected_outside_hours(self, config: BrokerConfig) -> None:
        broker = MockBroker(config=config, now_func=_non_trading_time)
        result = await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success
        assert "trading hours" in result.message.lower()

    @pytest.mark.asyncio
    async def test_rejected_lunch_break(self, config: BrokerConfig) -> None:
        broker = MockBroker(config=config, now_func=_lunch_time)
        result = await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_accepted_during_hours(self, broker: MockBroker) -> None:
        result = await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert result.success


# -- Fee calculations --


class TestFeeCalculations:
    @pytest.mark.asyncio
    async def test_commission_normal(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 1000.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        # amount ~= 100020 (with slippage). 100020 * 0.0003 = 30.006
        assert trades[0].commission >= 30.0

    @pytest.mark.asyncio
    async def test_commission_minimum(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 10.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        # amount ~= 1000. 1000 * 0.0003 = 0.30 < min 5.0
        assert trades[0].commission == 5.0

    @pytest.mark.asyncio
    async def test_slippage_buy_increases_price(
        self, broker: MockBroker
    ) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        assert trades[0].price > 100.0  # slippage makes buy more expensive

    @pytest.mark.asyncio
    async def test_slippage_sell_decreases_price(
        self, broker: MockBroker
    ) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.advance_day()
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.SELL, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        sell_trade = [t for t in trades if t.direction == OrderDirection.SELL]
        assert sell_trade[0].price < 100.0


# -- Cancel --


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_filled_fails(self, broker: MockBroker) -> None:
        result = await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        cancelled = await broker.cancel_order(result.order_id)
        assert cancelled is False  # already filled

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, broker: MockBroker) -> None:
        cancelled = await broker.cancel_order("nonexistent_id")
        assert cancelled is False


# -- Position cost averaging --


class TestCostAveraging:
    @pytest.mark.asyncio
    async def test_two_buys_average(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.place_order(
            "600519", 120.0, 200, OrderDirection.BUY, OrderType.LIMIT
        )
        positions = await broker.get_positions()
        assert positions[0].volume == 300
        # Cost average ~= (100*100 + 120*200) / 300 = 113.33 (plus slippage)
        assert positions[0].cost_price == pytest.approx(113.33, abs=1.0)

    @pytest.mark.asyncio
    async def test_independent_positions(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.place_order(
            "000001", 10.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        positions = await broker.get_positions()
        codes = {p.code for p in positions}
        assert codes == {"600519", "000001"}


# -- Account state --


class TestAccountState:
    @pytest.mark.asyncio
    async def test_initial_state(self, broker: MockBroker) -> None:
        account = await broker.get_account()
        assert account.total_assets == 1_000_000.0
        assert account.available_cash == 1_000_000.0
        assert account.market_value == 0.0
        assert account.total_pnl == 0.0

    @pytest.mark.asyncio
    async def test_after_buy(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        account = await broker.get_account()
        assert account.available_cash < 1_000_000.0
        assert account.market_value > 0.0
        # total_assets should be roughly initial_capital minus fees
        assert account.total_assets == pytest.approx(
            1_000_000.0, abs=100.0
        )

    @pytest.mark.asyncio
    async def test_get_orders_filter(self, broker: MockBroker) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        filled = await broker.get_orders(OrderStatus.FILLED)
        pending = await broker.get_orders(OrderStatus.PENDING)
        all_orders = await broker.get_orders()
        assert len(filled) == 1
        assert len(pending) == 0
        assert len(all_orders) == 1
