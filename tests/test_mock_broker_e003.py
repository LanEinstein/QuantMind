"""E-003 specific tests — at-fill price-limit recheck, transfer fee on Trade,
ALL_OR_NONE semantics, MarketMetaProvider injection.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from backend.broker.mock_broker import (
    PRICE_LIMIT_VIOLATION_REASON,
    MockBroker,
)
from backend.broker.models import (
    BrokerConfig,
    OrderDirection,
    OrderStatus,
    OrderType,
)
from backend.data.market_meta_provider import InMemoryMarketMetaProvider

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _trading_time() -> dt.datetime:
    return dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)


@pytest.fixture()
def config() -> BrokerConfig:
    return BrokerConfig()


@pytest.fixture()
def meta() -> InMemoryMarketMetaProvider:
    return InMemoryMarketMetaProvider(
        prev_close={
            "600519": 100.0,
            "000001": 10.0,
            "300750": 200.0,
            "159949": 1.50,
        },
        current_price={
            "600519": 100.5,
            "000001": 10.1,
            "300750": 201.0,
            "159949": 1.51,
        },
    )


@pytest.fixture()
def broker(config: BrokerConfig, meta: InMemoryMarketMetaProvider) -> MockBroker:
    return MockBroker(config=config, now_func=_trading_time, market_meta=meta)


class TestAllOrNone:
    @pytest.mark.asyncio
    async def test_filled_volume_equals_order_volume(
        self, broker: MockBroker
    ) -> None:
        result = await broker.place_order(
            "600519", 100.0, 200, OrderDirection.BUY, OrderType.LIMIT
        )
        assert result.success
        orders = await broker.get_orders(OrderStatus.FILLED)
        assert orders[0].filled_volume == 200

    @pytest.mark.asyncio
    async def test_no_partial_fill_when_insufficient_cash(
        self, config: BrokerConfig, meta: InMemoryMarketMetaProvider
    ) -> None:
        # Capital just under the order net amount → fully reject.
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1.0),
            now_func=_trading_time,
            market_meta=meta,
        )
        result = await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success
        assert (await broker.get_orders(OrderStatus.FILLED)) == ()


class TestAtFillPriceLimitRecheck:
    @pytest.mark.asyncio
    async def test_buy_at_limit_up_rejects_with_locked_reason(
        self, broker: MockBroker, meta: InMemoryMarketMetaProvider
    ) -> None:
        # prev_close=100 -> SH main upper=110. Place a BUY at 110.
        result = await broker.place_order(
            "600519", 110.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success
        assert PRICE_LIMIT_VIOLATION_REASON in result.message

    @pytest.mark.asyncio
    async def test_sell_at_limit_down_rejects_with_locked_reason(
        self, broker: MockBroker
    ) -> None:
        # Set up a 600519 position first via a BUY at non-limit price.
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        await broker.advance_day()  # T+1 → sellable
        # prev_close=100 -> SH main lower=90. Place a SELL at 90.
        result = await broker.place_order(
            "600519", 90.0, 100, OrderDirection.SELL, OrderType.LIMIT
        )
        assert not result.success
        assert PRICE_LIMIT_VIOLATION_REASON in result.message

    @pytest.mark.asyncio
    async def test_buy_just_below_limit_passes(
        self, broker: MockBroker
    ) -> None:
        # Just below limit-up. slippage_bps=1.5 → fill_price≈109.98 <
        # 110.0 (SH main 10% limit on prev_close 100).
        result = await broker.place_order(
            "600519", 109.9, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert result.success

    @pytest.mark.asyncio
    async def test_recheck_skipped_when_no_provider(
        self, config: BrokerConfig
    ) -> None:
        broker = MockBroker(config=config, now_func=_trading_time)
        # Without a MarketMetaProvider the recheck no-ops; this kept
        # legacy fixtures working before the production wiring lands.
        result = await broker.place_order(
            "600519", 110.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        # The order still passes preflight; at-fill recheck is bypassed.
        assert result.success


class TestTradeRecordsTransferFee:
    @pytest.mark.asyncio
    async def test_sz_buy_records_transfer_fee_on_trade(
        self, broker: MockBroker
    ) -> None:
        await broker.place_order(
            "000001", 10.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        assert trades[0].transfer_fee > 0

    @pytest.mark.asyncio
    async def test_sh_buy_does_not_record_transfer_fee(
        self, broker: MockBroker
    ) -> None:
        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        assert trades[0].transfer_fee == 0.0

    @pytest.mark.asyncio
    async def test_chuangye_charges_higher_slippage(
        self, broker: MockBroker
    ) -> None:
        await broker.place_order(
            "300750", 200.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        trades = await broker.get_trades()
        # ChiNext 3.5 bps vs Main 1.5 — slippage cost on 100*200 should
        # be 3.5*200*100/10000 = 7 (vs 3 on main).
        assert trades[0].slippage_cost == pytest.approx(7.0, abs=0.01)


class TestForbiddenBoardRejection:
    @pytest.mark.asyncio
    async def test_star_code_rejected(
        self, broker: MockBroker
    ) -> None:
        result = await broker.place_order(
            "688001", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success
        assert "forbidden" in result.message.lower()

    @pytest.mark.asyncio
    async def test_unknown_code_rejected(
        self, broker: MockBroker
    ) -> None:
        result = await broker.place_order(
            "999999", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        assert not result.success


class TestStrictModels:
    def test_trade_rejects_extra_fields(self) -> None:
        from backend.broker.models import Trade

        with pytest.raises(Exception):
            Trade(
                trade_id="t1",
                order_id="o1",
                code="600519",
                price=100.0,
                volume=100,
                amount=10_000.0,
                direction=OrderDirection.BUY,
                commission=5.0,
                stamp_tax=0.0,
                slippage_cost=0.15,
                transfer_fee=0.0,
                net_amount=10_005.0,
                traded_at=_trading_time(),
                rogue="nope",  # type: ignore[call-arg]
            )

    def test_broker_config_rejects_extra_fields(self) -> None:
        with pytest.raises(Exception):
            BrokerConfig(rogue=1)  # type: ignore[call-arg]

    def test_trade_default_transfer_fee_is_zero(self) -> None:
        from backend.broker.models import Trade

        t = Trade(
            trade_id="t1",
            order_id="o1",
            code="600519",
            price=100.0,
            volume=100,
            amount=10_000.0,
            direction=OrderDirection.BUY,
            commission=5.0,
            stamp_tax=0.0,
            slippage_cost=0.15,
            net_amount=10_005.15,
            traded_at=_trading_time(),
        )
        assert t.transfer_fee == 0.0
