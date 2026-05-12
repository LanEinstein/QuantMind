"""Tests for MongoDBService (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.data.database import MongoDBService
from backend.models.market import (
    FinancialData,
    IndexQuote,
    NewsArticle,
    StockQuote,
    WatchlistMarketSnapshot,
)


@pytest.fixture()
def mock_db() -> MagicMock:
    """Create a mock motor AsyncIOMotorDatabase."""
    db = MagicMock()
    for coll_name in [
        "market_realtime",
        "kline_daily",
        "financial_data",
        "news_articles",
    ]:
        coll = AsyncMock()
        coll.create_index = AsyncMock()
        bulk_result = MagicMock(upserted_count=1, modified_count=0)
        coll.bulk_write = AsyncMock(return_value=bulk_result)
        coll.update_one = AsyncMock()
        coll.find = MagicMock()
        # find() returns an async cursor-like object
        cursor = AsyncMock()
        cursor.to_list = AsyncMock(return_value=[])
        cursor.sort = MagicMock(return_value=cursor)
        cursor.limit = MagicMock(return_value=cursor)
        coll.find.return_value = cursor
        db.__getitem__ = MagicMock(side_effect=lambda name: {
            "market_realtime": coll,
            "kline_daily": coll,
            "financial_data": coll,
            "news_articles": coll,
        }.get(name, coll))
    return db


@pytest.fixture()
def service(mock_db: MagicMock) -> MongoDBService:
    return MongoDBService(mock_db)


def _sample_index_quote() -> IndexQuote:
    return IndexQuote(
        code="000001",
        name="上证指数",
        price=3150.5,
        change_pct=0.85,
        volume=3_500_000_000.0,
        amount=450_000_000_000.0,
        timestamp=datetime(2026, 3, 22, 10, 30, tzinfo=UTC),
    )


def _sample_stock_quote() -> StockQuote:
    return StockQuote(
        code="600519",
        name="贵州茅台",
        price=1800.0,
        open=1790.0,
        high=1810.0,
        low=1785.0,
        prev_close=1795.0,
        change_pct=0.28,
        volume=5_000_000.0,
        amount=9_000_000_000.0,
        turnover_rate=0.63,
        timestamp=datetime(2026, 3, 22, 10, 30, tzinfo=UTC),
    )


def _sample_watchlist_snapshot(
    code: str = "600519",
    snapshot_at: datetime | None = None,
) -> WatchlistMarketSnapshot:
    return WatchlistMarketSnapshot(
        code=code,
        name="贵州茅台",
        price=1800.0,
        open=1790.0,
        high=1810.0,
        low=1785.0,
        prev_close=1795.0,
        change_pct=0.28,
        volume=5_000_000.0,
        amount=9_000_000_000.0,
        turnover_rate=0.63,
        source="adata",
        snapshot_at=snapshot_at or datetime(2026, 5, 12, 6, 0, tzinfo=UTC),
    )


def _sample_news() -> NewsArticle:
    return NewsArticle(
        title="Test News",
        content="Test content",
        source="eastmoney",
        url="https://example.com/1",
        publish_time=datetime(2026, 3, 22, 9, 0, tzinfo=UTC),
    )


def _sample_financial() -> FinancialData:
    return FinancialData(
        code="600519",
        name="贵州茅台",
        pe_ratio=32.5,
        pb_ratio=10.2,
        roe=30.5,
        eps=45.8,
        revenue_growth=15.3,
        report_date="2025-12-31",
        timestamp=datetime(2026, 3, 22, tzinfo=UTC),
    )


class TestInitialize:
    """Tests for index creation."""

    @pytest.mark.asyncio
    async def test_creates_indexes(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        await service.initialize()
        # Should have called create_index on collections
        coll = mock_db["market_realtime"]
        assert coll.create_index.call_count >= 1

    @pytest.mark.asyncio
    async def test_creates_shadow_decisions_indexes(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        # codex P5B-exit R4 LOW: lock the Phase 5B shadow-test indexes
        # so a regression that drops them fails this test instead of
        # silently degrading shadow_decisions reads/writes to scans.
        await service.initialize()
        coll = mock_db["shadow_decisions"]
        all_calls = coll.create_index.call_args_list
        # Required: unique run_id index for upserts.
        assert any(
            call.args[0] == [("run_id", 1)] and call.kwargs.get("unique")
            for call in all_calls
        ), "shadow_decisions.run_id unique index missing"
        # Required: descending created_at TTL index for the lookback
        # query. The TTL itself prevents indefinite retention of
        # decision telemetry (codex P5B-exit R5 MED).
        ttl_calls = [
            call
            for call in all_calls
            if call.args[0] == [("created_at", -1)]
            and "expireAfterSeconds" in call.kwargs
        ]
        assert ttl_calls, "shadow_decisions.created_at TTL index missing"
        # 30 days in seconds — pin the magic number so a naive cleanup
        # cannot quietly drop or shorten it.
        assert ttl_calls[0].kwargs["expireAfterSeconds"] == 30 * 86400


class TestSaveMarketSnapshot:
    """Tests for save_market_snapshot."""

    @pytest.mark.asyncio
    async def test_saves_quotes(self, service: MongoDBService) -> None:
        quotes = [_sample_index_quote(), _sample_stock_quote()]
        count = await service.save_market_snapshot(quotes)
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_empty_list(self, service: MongoDBService) -> None:
        count = await service.save_market_snapshot([])
        assert count == 0


class TestWatchlistSnapshotIndexes:
    """C-003: watchlist_market_snapshots collection index lock."""

    @pytest.mark.asyncio
    async def test_creates_unique_and_window_indexes(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        await service.initialize()
        coll = mock_db["watchlist_market_snapshots"]
        all_calls = coll.create_index.call_args_list

        # Required: unique (code, snapshot_at DESC) for idempotent bulk upsert
        assert any(
            call.args[0] == [("code", 1), ("snapshot_at", -1)]
            and call.kwargs.get("unique") is True
            for call in all_calls
        ), "watchlist_market_snapshots.(code, snapshot_at) unique missing"

        # Required: standalone (snapshot_at DESC) for window scans
        assert any(
            call.args[0] == [("snapshot_at", -1)]
            and call.kwargs.get("unique") is not True
            for call in all_calls
        ), "watchlist_market_snapshots.snapshot_at scan index missing"


class TestSaveWatchlistSnapshot:
    """Tests for save_watchlist_snapshot bulk upsert."""

    @pytest.mark.asyncio
    async def test_saves_snapshots(self, service: MongoDBService) -> None:
        snapshots = [
            _sample_watchlist_snapshot("600519"),
            _sample_watchlist_snapshot("510300"),
        ]
        count = await service.save_watchlist_snapshot(snapshots)
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_empty_list_short_circuits(
        self, service: MongoDBService
    ) -> None:
        count = await service.save_watchlist_snapshot([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_swallows_bulk_write_failure(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["watchlist_market_snapshots"]
        coll.bulk_write = AsyncMock(side_effect=RuntimeError("mongo down"))
        count = await service.save_watchlist_snapshot(
            [_sample_watchlist_snapshot()]
        )
        assert count == 0


class TestCountWatchlistSnapshotsInWindow:
    """Backs missing-rate windowed scoring."""

    @pytest.mark.asyncio
    async def test_returns_count(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["watchlist_market_snapshots"]
        coll.count_documents = AsyncMock(return_value=42)
        start = datetime(2026, 5, 12, 1, 30, tzinfo=UTC)
        end = datetime(2026, 5, 12, 7, 0, tzinfo=UTC)
        assert (
            await service.count_watchlist_snapshots_in_window(start, end) == 42
        )

    @pytest.mark.asyncio
    async def test_swallows_driver_error(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["watchlist_market_snapshots"]
        coll.count_documents = AsyncMock(side_effect=RuntimeError("oops"))
        start = datetime(2026, 5, 12, 1, 30, tzinfo=UTC)
        end = datetime(2026, 5, 12, 7, 0, tzinfo=UTC)
        assert await service.count_watchlist_snapshots_in_window(
            start, end
        ) == 0


class TestSaveNews:
    """Tests for save_news."""

    @pytest.mark.asyncio
    async def test_saves_articles(self, service: MongoDBService) -> None:
        articles = [_sample_news()]
        count = await service.save_news(articles)
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_empty_list(self, service: MongoDBService) -> None:
        count = await service.save_news([])
        assert count == 0


class TestSaveFinancialData:
    """Tests for save_financial_data."""

    @pytest.mark.asyncio
    async def test_saves_data(self, service: MongoDBService) -> None:
        await service.save_financial_data(_sample_financial())
        coll = service._db["financial_data"]
        assert coll.update_one.call_count == 1


class TestQueryNews:
    """Tests for query_news."""

    @pytest.mark.asyncio
    async def test_query_returns_list(self, service: MongoDBService) -> None:
        result = await service.query_news(limit=10)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_query_with_stock_code(self, service: MongoDBService) -> None:
        result = await service.query_news(limit=10, stock_code="600519")
        assert isinstance(result, list)
