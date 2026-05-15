"""EquityPoint + EquityPointBuilder tests (E-006 / P1-2.B)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pytest

from backend.broker.equity import EquityPointBuilder
from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
from backend.data.market_meta_provider import (
    InMemoryMarketMetaProvider,
    StaleQuoteError,
)
from backend.models.equity import (
    EquityPoint,
    EquityPointPosition,
    EquityPointQuality,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class _StubAccount:
    available_cash: float
    frozen_cash: float
    initial_capital: float


@dataclass
class _StubPosition:
    code: str
    volume: int
    cost_price: float


class _StubBroker:
    def __init__(
        self,
        cash: float = 100_000.0,
        frozen: float = 0.0,
        initial: float = 1_000_000.0,
        positions: list[_StubPosition] | None = None,
    ) -> None:
        self._account = _StubAccount(cash, frozen, initial)
        self._positions = positions or []

    async def get_account(self) -> _StubAccount:
        return self._account

    async def get_positions(self) -> list[_StubPosition]:
        return list(self._positions)


@pytest.fixture()
def trading_time() -> dt.datetime:
    return dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)


class TestEquityPointSchema:
    def test_rejects_total_equity_drift(self, trading_time: dt.datetime) -> None:
        with pytest.raises(Exception, match="total_equity"):
            EquityPoint(
                snapshot_at=trading_time,
                trade_date="2026-05-15",
                cash=100.0,
                frozen_cash=0.0,
                market_value=100.0,
                total_equity=999.0,  # very wrong
                initial_capital=1_000_000.0,
                pnl=0.0,
                pnl_pct=0.0,
                quality=EquityPointQuality.FRESH,
            )

    def test_rejects_duplicate_codes(self, trading_time: dt.datetime) -> None:
        with pytest.raises(Exception, match="duplicate"):
            EquityPoint(
                snapshot_at=trading_time,
                trade_date="2026-05-15",
                cash=0.0,
                frozen_cash=0.0,
                market_value=200.0,
                total_equity=200.0,
                initial_capital=1_000_000.0,
                pnl=0.0,
                pnl_pct=0.0,
                quality=EquityPointQuality.FRESH,
                positions=(
                    EquityPointPosition(
                        code="600519", volume=1, cost_price=100.0,
                        last_price=100.0, market_value=100.0,
                        unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
                        price_quality=EquityPointQuality.FRESH,
                    ),
                    EquityPointPosition(
                        code="600519", volume=1, cost_price=100.0,
                        last_price=100.0, market_value=100.0,
                        unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
                        price_quality=EquityPointQuality.FRESH,
                    ),
                ),
            )


class TestEquityPointBuilder:
    @pytest.mark.asyncio
    async def test_fresh_path_marks_quality_fresh(
        self, trading_time: dt.datetime
    ) -> None:
        broker = _StubBroker(
            cash=820_000.0,
            positions=[_StubPosition("600519", 100, 1_800.0)],
        )
        meta = InMemoryMarketMetaProvider(current_price={"600519": 1_810.0})
        builder = EquityPointBuilder(broker, meta)
        point = await builder.build(now=trading_time)
        assert point.quality is EquityPointQuality.FRESH
        assert point.market_value == pytest.approx(181_000.0)
        assert point.positions[0].unrealized_pnl == pytest.approx(1_000.0)

    @pytest.mark.asyncio
    async def test_degraded_cached_fallback_when_provider_stale(
        self, trading_time: dt.datetime
    ) -> None:
        broker = _StubBroker(
            cash=820_000.0,
            positions=[_StubPosition("600519", 100, 1_800.0)],
        )
        meta = InMemoryMarketMetaProvider(current_price={"600519": 1_810.0})
        builder = EquityPointBuilder(broker, meta)
        # First call seeds last_known_cached
        await builder.build(now=trading_time)
        # Provider goes stale; builder should fall back to cached.
        meta.set_current_price_stale("600519")
        point = await builder.build(now=trading_time)
        assert point.quality is EquityPointQuality.DEGRADED
        assert point.positions[0].price_quality is EquityPointQuality.DEGRADED
        assert point.positions[0].last_price == pytest.approx(1_810.0)

    @pytest.mark.asyncio
    async def test_no_cached_price_propagates_stale_error(
        self, trading_time: dt.datetime
    ) -> None:
        broker = _StubBroker(
            cash=820_000.0,
            positions=[_StubPosition("600519", 100, 1_800.0)],
        )
        meta = InMemoryMarketMetaProvider()
        meta.set_current_price_stale("600519")
        builder = EquityPointBuilder(broker, meta)
        with pytest.raises(StaleQuoteError):
            await builder.build(now=trading_time)

    @pytest.mark.asyncio
    async def test_eod_fallback_uses_cost_price_and_locked_quality(
        self, trading_time: dt.datetime
    ) -> None:
        broker = _StubBroker(
            cash=820_000.0,
            positions=[_StubPosition("600519", 100, 1_800.0)],
        )
        meta = InMemoryMarketMetaProvider()
        builder = EquityPointBuilder(broker, meta)
        point = await builder.build_eod_fallback(now=trading_time)
        assert point.quality is EquityPointQuality.EOD_FALLBACK
        # Market value uses cost_price; the special quality flag tells
        # the UI / acceptance pipeline this point is synthesised.
        assert point.market_value == pytest.approx(180_000.0)
        assert point.positions[0].price_quality is EquityPointQuality.EOD_FALLBACK

    @pytest.mark.asyncio
    async def test_pnl_is_total_equity_minus_initial_capital(
        self, trading_time: dt.datetime
    ) -> None:
        broker = _StubBroker(
            cash=900_000.0,
            initial=1_000_000.0,
            positions=[_StubPosition("600519", 100, 1_000.0)],
        )
        meta = InMemoryMarketMetaProvider(current_price={"600519": 1_500.0})
        builder = EquityPointBuilder(broker, meta)
        point = await builder.build(now=trading_time)
        # cash 900_000 + market_value 150_000 = 1_050_000 → pnl 50_000
        assert point.total_equity == pytest.approx(1_050_000.0)
        assert point.pnl == pytest.approx(50_000.0)
        assert point.pnl_pct == pytest.approx(0.05)


class TestQualityWorstWins:
    """A single DEGRADED position drags the overall quality down."""

    @pytest.mark.asyncio
    async def test_mixed_positions_aggregate_to_worst(
        self, trading_time: dt.datetime
    ) -> None:
        broker = _StubBroker(
            cash=500_000.0,
            positions=[
                _StubPosition("600519", 100, 1_800.0),  # fresh
                _StubPosition("000001", 200, 10.0),     # cached fallback
            ],
        )
        meta = InMemoryMarketMetaProvider(
            current_price={"600519": 1_810.0, "000001": 11.0}
        )
        builder = EquityPointBuilder(broker, meta)
        # Seed both
        await builder.build(now=trading_time)
        # Now 000001 goes stale only
        meta.set_current_price_stale("000001")
        point = await builder.build(now=trading_time)
        assert point.quality is EquityPointQuality.DEGRADED
        # 600519 still fresh in the per-position row
        assert point.positions[0].price_quality is EquityPointQuality.FRESH
        # 000001 marked degraded
        assert point.positions[1].price_quality is EquityPointQuality.DEGRADED


class TestRealMockBrokerIntegration:
    @pytest.mark.asyncio
    async def test_builds_from_live_mock_broker(
        self, trading_time: dt.datetime
    ) -> None:
        broker = MockBroker(
            config=BrokerConfig(initial_capital=1_000_000.0),
            now_func=lambda: trading_time,
        )
        meta = InMemoryMarketMetaProvider(
            prev_close={"600519": 100.0},
            current_price={"600519": 105.0},
        )
        # Place a fill so the broker has a position
        broker._market_meta = meta  # inject for the at-fill recheck
        from backend.broker.models import OrderDirection, OrderType

        await broker.place_order(
            "600519", 100.0, 100, OrderDirection.BUY, OrderType.LIMIT
        )
        builder = EquityPointBuilder(broker, meta)
        point = await builder.build(now=trading_time)
        assert point.quality is EquityPointQuality.FRESH
        assert len(point.positions) == 1
        assert point.positions[0].code == "600519"
