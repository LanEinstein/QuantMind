"""Unit tests for MarketMetaProvider (E-003 / P1-2.B)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.data.market_meta_provider import (
    MONGO_FRESHNESS_SECONDS,
    REDIS_FRESHNESS_SECONDS,
    InMemoryMarketMetaProvider,
    MongoBackedMarketMetaProvider,
    StaleQuoteError,
)


class TestInMemoryProvider:
    @pytest.mark.asyncio
    async def test_returns_set_prev_close(self) -> None:
        m = InMemoryMarketMetaProvider(prev_close={"600519": 100.0})
        assert await m.get_prev_close("600519") == 100.0

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_prev_close(self) -> None:
        m = InMemoryMarketMetaProvider()
        assert await m.get_prev_close("600519") is None

    @pytest.mark.asyncio
    async def test_returns_current_price(self) -> None:
        m = InMemoryMarketMetaProvider(current_price={"600519": 100.5})
        assert await m.get_current_price("600519") == 100.5

    @pytest.mark.asyncio
    async def test_stale_marker_raises(self) -> None:
        m = InMemoryMarketMetaProvider(current_price={"600519": 100.0})
        m.set_current_price_stale("600519")
        with pytest.raises(StaleQuoteError, match="no fresh quote"):
            await m.get_current_price("600519")

    @pytest.mark.asyncio
    async def test_missing_current_price_raises(self) -> None:
        m = InMemoryMarketMetaProvider()
        with pytest.raises(StaleQuoteError, match="no quote at all"):
            await m.get_current_price("600519")

    def test_constants_are_locked(self) -> None:
        assert REDIS_FRESHNESS_SECONDS == 60
        assert MONGO_FRESHNESS_SECONDS == 300


class TestMongoBackedProvider:
    @pytest.fixture()
    def mongodb(self) -> MagicMock:
        db = MagicMock()
        # market_realtime + kline_daily are dict-style accessed
        coll = MagicMock()
        cursor = AsyncMock()
        cursor.to_list = AsyncMock(return_value=[])
        cursor.sort = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        coll.find = MagicMock(return_value=cursor)
        db._db = {"market_realtime": coll, "kline_daily": coll}
        return db

    @pytest.mark.asyncio
    async def test_prev_close_reads_kline_daily(self) -> None:
        ref = datetime(2026, 5, 15, 10, 0)

        async def _kline_iter() -> object:
            for doc in [{"close": 100.0}]:
                yield doc

        kline_cursor = MagicMock()
        kline_cursor.sort = MagicMock(return_value=kline_cursor)
        kline_cursor.limit = MagicMock(return_value=kline_cursor)
        kline_cursor.__aiter__ = lambda self: _kline_iter()
        kline_coll = MagicMock()
        kline_coll.find = MagicMock(return_value=kline_cursor)
        db = MagicMock()
        db._db = {"kline_daily": kline_coll, "market_realtime": MagicMock()}
        provider = MongoBackedMarketMetaProvider(db, redis_client=None)
        out = await provider.get_prev_close("600519")
        _ = ref  # not used here
        assert out == 100.0

    @pytest.mark.asyncio
    async def test_redis_hit_short_circuits_mongo(self) -> None:
        ref = datetime(2026, 5, 15, 10, 0)
        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value=json.dumps(
                {"price": 100.5, "timestamp": ref.isoformat()}
            )
        )
        provider = MongoBackedMarketMetaProvider(
            mongodb=MagicMock(), redis_client=redis
        )
        out = await provider.get_current_price("600519", now=ref)
        assert out == 100.5

    @pytest.mark.asyncio
    async def test_stale_redis_falls_through_to_mongo(self) -> None:
        ref = datetime(2026, 5, 15, 10, 0)
        old = (ref - timedelta(seconds=120)).isoformat()
        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value=json.dumps({"price": 99.0, "timestamp": old})
        )

        async def _mongo_iter() -> object:
            yield {
                "price": 100.5,
                "timestamp": ref - timedelta(seconds=10),
            }

        market_cursor = MagicMock()
        market_cursor.sort = MagicMock(return_value=market_cursor)
        market_cursor.limit = MagicMock(return_value=market_cursor)
        market_cursor.__aiter__ = lambda self: _mongo_iter()
        market_coll = MagicMock()
        market_coll.find = MagicMock(return_value=market_cursor)
        db = MagicMock()
        db._db = {"market_realtime": market_coll, "kline_daily": MagicMock()}
        provider = MongoBackedMarketMetaProvider(
            mongodb=db, redis_client=redis
        )
        out = await provider.get_current_price("600519", now=ref)
        assert out == 100.5

    @pytest.mark.asyncio
    async def test_both_tiers_stale_raises(self) -> None:
        ref = datetime(2026, 5, 15, 10, 0)
        old = (ref - timedelta(seconds=120)).isoformat()
        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value=json.dumps({"price": 99.0, "timestamp": old})
        )

        async def _mongo_iter() -> object:
            yield {
                "price": 100.5,
                "timestamp": ref - timedelta(seconds=500),
            }

        market_cursor = MagicMock()
        market_cursor.sort = MagicMock(return_value=market_cursor)
        market_cursor.limit = MagicMock(return_value=market_cursor)
        market_cursor.__aiter__ = lambda self: _mongo_iter()
        market_coll = MagicMock()
        market_coll.find = MagicMock(return_value=market_cursor)
        db = MagicMock()
        db._db = {"market_realtime": market_coll, "kline_daily": MagicMock()}
        provider = MongoBackedMarketMetaProvider(
            mongodb=db, redis_client=redis
        )
        with pytest.raises(StaleQuoteError, match="no fresh quote"):
            await provider.get_current_price("600519", now=ref)

    @pytest.mark.asyncio
    async def test_invalid_redis_blob_falls_through(self) -> None:
        ref = datetime(2026, 5, 15, 10, 0)
        redis = AsyncMock()
        redis.get = AsyncMock(return_value="not-json")

        async def _mongo_iter() -> object:
            yield {
                "price": 100.5,
                "timestamp": ref - timedelta(seconds=10),
            }

        market_cursor = MagicMock()
        market_cursor.sort = MagicMock(return_value=market_cursor)
        market_cursor.limit = MagicMock(return_value=market_cursor)
        market_cursor.__aiter__ = lambda self: _mongo_iter()
        market_coll = MagicMock()
        market_coll.find = MagicMock(return_value=market_cursor)
        db = MagicMock()
        db._db = {"market_realtime": market_coll, "kline_daily": MagicMock()}
        provider = MongoBackedMarketMetaProvider(
            mongodb=db, redis_client=redis
        )
        out = await provider.get_current_price("600519", now=ref)
        assert out == 100.5
