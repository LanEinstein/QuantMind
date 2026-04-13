"""Tests for signal persistence in MongoDBService (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from backend.data.database import MongoDBService

# Valid ObjectId strings for testing
_OID_1 = str(ObjectId())
_OID_2 = str(ObjectId())


def _make_signals_collection() -> AsyncMock:
    """Create a mock 'trading_signals' collection with cursor chain."""
    coll = AsyncMock()
    coll.create_index = AsyncMock()

    # update_one returns a result with upserted_id
    upsert_result = MagicMock(upserted_id=_OID_1)
    coll.update_one = AsyncMock(return_value=upsert_result)

    # find_one returns a document
    coll.find_one = AsyncMock(return_value={
        "_id": _OID_1,
        "stock_code": "600519",
        "trade_date": "2026-04-13",
        "action": "买入",
        "confidence": 0.8,
    })

    # find returns an async cursor chain
    cursor = AsyncMock()
    cursor.to_list = AsyncMock(return_value=[])
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    coll.find = MagicMock(return_value=cursor)

    return coll


def _make_mock_db() -> MagicMock:
    """Create a mock database with trading_signals collection."""
    db = MagicMock()
    signals_coll = _make_signals_collection()
    # Also provide stubs for other collections used in initialize()
    default_coll = AsyncMock()
    default_coll.create_index = AsyncMock()

    def getitem(name: str) -> AsyncMock:
        if name == "trading_signals":
            return signals_coll
        return default_coll

    db.__getitem__ = MagicMock(side_effect=getitem)
    return db


@pytest.fixture()
def mock_db() -> MagicMock:
    return _make_mock_db()


@pytest.fixture()
def service(mock_db: MagicMock) -> MongoDBService:
    return MongoDBService(mock_db)


def _sample_signal_dict() -> dict:
    return {
        "action": "买入",
        "target_price": 1900.0,
        "confidence": 0.8,
        "risk_score": 0.3,
        "reasoning": "基本面强劲",
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "trade_date": "2026-04-13",
        "created_at": datetime(2026, 4, 13, 9, 45, tzinfo=UTC).isoformat(),
    }


class TestSaveSignal:
    """Tests for save_signal method."""

    @pytest.mark.asyncio
    async def test_save_signal_inserts_and_returns_id(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        signal = _sample_signal_dict()
        result_id = await service.save_signal(signal)

        assert isinstance(result_id, str)
        coll = mock_db["trading_signals"]
        coll.update_one.assert_called_once()
        call_args = coll.update_one.call_args
        # First arg: filter key
        assert call_args[0][0] == {
            "stock_code": "600519",
            "trade_date": "2026-04-13",
        }
        # Second arg: $set
        assert call_args[0][1] == {"$set": signal}
        # Third: upsert=True
        assert call_args[1]["upsert"] is True

    @pytest.mark.asyncio
    async def test_save_signal_upsert_prevents_duplicates(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        """Saving same stock_code + trade_date uses upsert, not insert."""
        signal = _sample_signal_dict()

        # First save
        await service.save_signal(signal)
        # Second save with different confidence
        updated = {**signal, "confidence": 0.9}
        await service.save_signal(updated)

        coll = mock_db["trading_signals"]
        assert coll.update_one.call_count == 2
        # Both calls use the same upsert key
        for call in coll.update_one.call_args_list:
            assert call[0][0] == {
                "stock_code": "600519",
                "trade_date": "2026-04-13",
            }
            assert call[1]["upsert"] is True

    @pytest.mark.asyncio
    async def test_save_signal_returns_existing_id_on_update(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        """When upserted_id is None (update), falls back to find_one."""
        coll = mock_db["trading_signals"]
        coll.update_one.return_value = MagicMock(upserted_id=None)
        coll.find_one.return_value = {"_id": _OID_2}

        result_id = await service.save_signal(_sample_signal_dict())
        assert result_id == _OID_2
        coll.find_one.assert_called_once()


class TestQuerySignals:
    """Tests for query_signals method."""

    @pytest.mark.asyncio
    async def test_query_signals_returns_recent(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["trading_signals"]
        sample = [{**_sample_signal_dict(), "_id": _OID_1}]
        cursor = coll.find.return_value
        cursor.to_list.return_value = sample

        result = await service.query_signals(days=30)

        assert isinstance(result, list)
        assert len(result) == 1
        coll.find.assert_called_once()
        query = coll.find.call_args[0][0]
        assert "trade_date" in query
        assert "$gte" in query["trade_date"]

    @pytest.mark.asyncio
    async def test_query_signals_filters_by_stock_code(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        await service.query_signals(stock_code="600519", days=30)

        coll = mock_db["trading_signals"]
        query = coll.find.call_args[0][0]
        assert query["stock_code"] == "600519"

    @pytest.mark.asyncio
    async def test_query_signals_no_stock_code_filter(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        await service.query_signals(stock_code=None, days=30)

        coll = mock_db["trading_signals"]
        query = coll.find.call_args[0][0]
        assert "stock_code" not in query

    @pytest.mark.asyncio
    async def test_query_signals_days_zero(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        """days=0 produces a cutoff of today, returning effectively nothing."""
        result = await service.query_signals(days=0)

        assert isinstance(result, list)
        coll = mock_db["trading_signals"]
        query = coll.find.call_args[0][0]
        # Cutoff date should be today (0 days ago)
        assert "$gte" in query["trade_date"]


class TestGetSignalById:
    """Tests for get_signal_by_id method."""

    @pytest.mark.asyncio
    async def test_get_signal_by_id_returns_document(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["trading_signals"]
        expected = {**_sample_signal_dict(), "_id": _OID_1}
        coll.find_one.return_value = expected

        result = await service.get_signal_by_id(_OID_1)

        assert result is not None
        assert result["stock_code"] == "600519"
        coll.find_one.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_signal_by_id_returns_none_when_missing(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["trading_signals"]
        coll.find_one.return_value = None

        missing_id = str(ObjectId())
        result = await service.get_signal_by_id(missing_id)
        assert result is None
