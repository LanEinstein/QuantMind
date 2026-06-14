"""Tests for MongoDBService (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.data.database import (
    MongoDBService,
    ReplicaSetUnavailableError,
)
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

    @pytest.mark.asyncio
    async def test_upsert_key_is_url_and_domain(
        self, service: MongoDBService
    ) -> None:
        """C-005 (codex P2): cross-domain duplicates must survive Mongo.

        The upsert filter must include ``domain`` so two rows with the
        same URL but different domains land as two documents instead of
        clobbering each other.
        """
        await service.save_news([_sample_news()])
        coll = service._db["news_articles"]
        ops_arg = coll.bulk_write.call_args.args[0]
        assert len(ops_arg) == 1
        upsert_filter = ops_arg[0]._filter
        assert "url" in upsert_filter
        assert "domain" in upsert_filter


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


# ---------------------------------------------------------------------------
# E-001 — Mongo single-node replica-set boot fence
# ---------------------------------------------------------------------------


def _replica_set_db(hello_response: dict | Exception) -> MagicMock:
    """Build a minimal mock motor DB whose admin.command returns hello_response.

    Accepts either a dict (success path) or an Exception instance (the
    probe should reraise as ReplicaSetUnavailableError).
    """
    admin = MagicMock()
    if isinstance(hello_response, Exception):
        admin.command = AsyncMock(side_effect=hello_response)
    else:
        admin.command = AsyncMock(return_value=hello_response)
    client = MagicMock()
    client.admin = admin
    db = MagicMock()
    db.client = client
    return db


class TestReplicaSetFence:
    """E-001 acceptance: hello.setName drives BrokerScheduler boot."""

    @pytest.mark.asyncio
    async def test_is_replica_set_true_when_setname_present(self) -> None:
        db = _replica_set_db({"setName": "rs0", "ismaster": True})
        service = MongoDBService(db)
        assert await service.is_replica_set() is True

    @pytest.mark.asyncio
    async def test_is_replica_set_false_for_standalone(self) -> None:
        # Standalone mongod returns hello without a setName key.
        db = _replica_set_db({"ismaster": True, "msg": "isdbgrid"})
        service = MongoDBService(db)
        assert await service.is_replica_set() is False

    @pytest.mark.asyncio
    async def test_is_replica_set_false_when_command_fails(self) -> None:
        db = _replica_set_db(ConnectionError("no route to host"))
        service = MongoDBService(db)
        assert await service.is_replica_set() is False

    @pytest.mark.asyncio
    async def test_assert_replica_set_returns_setname(self) -> None:
        db = _replica_set_db({"setName": "rs0"})
        service = MongoDBService(db)
        assert await service.assert_replica_set() == "rs0"

    @pytest.mark.asyncio
    async def test_assert_replica_set_raises_on_standalone(self) -> None:
        db = _replica_set_db({"ismaster": True})
        service = MongoDBService(db)
        with pytest.raises(ReplicaSetUnavailableError, match="not a replica-set"):
            await service.assert_replica_set()

    @pytest.mark.asyncio
    async def test_assert_replica_set_raises_on_probe_failure(self) -> None:
        db = _replica_set_db(RuntimeError("boom"))
        service = MongoDBService(db)
        with pytest.raises(ReplicaSetUnavailableError, match="hello probe failed"):
            await service.assert_replica_set()

    @pytest.mark.asyncio
    async def test_assert_replica_set_raises_when_setname_empty_string(
        self,
    ) -> None:
        # Defensive: an empty setName must NOT pass — Mongo never returns
        # an empty string in practice, but a misbehaving proxy could,
        # and the boot fence should refuse to start the scheduler.
        db = _replica_set_db({"setName": ""})
        service = MongoDBService(db)
        with pytest.raises(ReplicaSetUnavailableError):
            await service.assert_replica_set()


# ---------------------------------------------------------------------------
# AE-001 — save_daily_frame (offline historical ingest secondary row writer)
# ---------------------------------------------------------------------------


def test_iso_date_normalisation() -> None:
    from backend.data.database import _iso_date

    assert _iso_date("20180102") == "2018-01-02"
    assert _iso_date(" 20180102 ") == "2018-01-02"
    assert _iso_date("2018-01-02") == "2018-01-02"  # already ISO → unchanged


@pytest.mark.asyncio
async def test_save_daily_frame_bulk_upserts(
    service: MongoDBService, mock_db: MagicMock
) -> None:
    import pandas as pd

    df = pd.DataFrame(
        {"ts_code": ["600519.SH", "000001.SZ"], "close": [1700.0, 10.5]}
    )
    await service.save_daily_frame("20180102", df)
    coll = mock_db["kline_daily"]
    coll.bulk_write.assert_awaited_once()
    ops = coll.bulk_write.call_args.args[0]
    assert len(ops) == 2  # one upsert per delivered code
    # Keys must match the shape existing kline_daily readers query:
    # bare 6-digit code + ISO date (codex AE-001 P1).
    filters = [op._filter for op in ops]
    assert {"code": "600519", "date": "2018-01-02"} in filters
    assert {"code": "000001", "date": "2018-01-02"} in filters
    # The original Tushare ts_code is preserved on the document.
    set_docs = [op._doc["$set"] for op in ops]
    assert any(d.get("ts_code") == "600519.SH" for d in set_docs)


@pytest.mark.asyncio
async def test_save_daily_frame_empty_returns_zero(
    service: MongoDBService,
) -> None:
    import pandas as pd

    assert await service.save_daily_frame("20180102", pd.DataFrame()) == 0


@pytest.mark.asyncio
async def test_save_daily_frame_skips_rows_without_ts_code(
    service: MongoDBService, mock_db: MagicMock
) -> None:
    import pandas as pd

    df = pd.DataFrame({"ts_code": ["", "600519.SH"], "close": [1.0, 2.0]})
    await service.save_daily_frame("20180102", df)
    ops = mock_db["kline_daily"].bulk_write.call_args.args[0]
    assert len(ops) == 1  # the blank-code row is skipped
