"""Tests for WatchlistService (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.data.watchlist import WatchlistService


def _make_mock_db() -> MagicMock:
    """Create a mock database with watchlist collection."""
    db = MagicMock()
    coll = AsyncMock()
    coll.create_index = AsyncMock()
    coll.update_one = AsyncMock()
    coll.update_many = AsyncMock()

    # find returns an async cursor
    cursor = AsyncMock()
    cursor.to_list = AsyncMock(return_value=[])
    coll.find = MagicMock(return_value=cursor)

    db.__getitem__ = MagicMock(return_value=coll)
    return db


@pytest.fixture()
def mock_db() -> MagicMock:
    return _make_mock_db()


@pytest.fixture()
def service(mock_db: MagicMock) -> WatchlistService:
    return WatchlistService(mock_db)


class TestInitialize:
    """Tests for index creation."""

    @pytest.mark.asyncio
    async def test_creates_unique_index(
        self, service: WatchlistService, mock_db: MagicMock
    ) -> None:
        await service.initialize()
        coll = mock_db["watchlist"]
        coll.create_index.assert_called_once_with("stock_code", unique=True)


class TestAddStock:
    """Tests for add_stock method."""

    @pytest.mark.asyncio
    async def test_add_stock_upserts_with_active_true(
        self, service: WatchlistService, mock_db: MagicMock
    ) -> None:
        await service.add_stock("600519", "贵州茅台")

        coll = mock_db["watchlist"]
        coll.update_one.assert_called_once()
        call_args = coll.update_one.call_args
        # Filter by stock_code
        assert call_args[0][0] == {"stock_code": "600519"}
        # $set includes required fields
        set_doc = call_args[0][1]["$set"]
        assert set_doc["stock_code"] == "600519"
        assert set_doc["stock_name"] == "贵州茅台"
        assert set_doc["active"] is True
        assert "added_at" in set_doc
        # upsert=True
        assert call_args[1]["upsert"] is True

    @pytest.mark.asyncio
    async def test_add_duplicate_reactivates(
        self, service: WatchlistService, mock_db: MagicMock
    ) -> None:
        """Adding the same stock_code twice upserts with active=True."""
        await service.add_stock("600519", "贵州茅台")
        await service.add_stock("600519", "贵州茅台")

        coll = mock_db["watchlist"]
        assert coll.update_one.call_count == 2
        # Both calls set active=True (reactivation)
        for call in coll.update_one.call_args_list:
            assert call[0][1]["$set"]["active"] is True


class TestListStocks:
    """Tests for list_stocks method."""

    @pytest.mark.asyncio
    async def test_list_stocks_returns_only_active(
        self, service: WatchlistService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["watchlist"]
        cursor = coll.find.return_value
        cursor.to_list.return_value = [
            {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
        ]

        result = await service.list_stocks()

        assert isinstance(result, list)
        assert len(result) == 1
        coll.find.assert_called_once_with({"active": True})


class TestRemoveStock:
    """Tests for remove_stock method."""

    @pytest.mark.asyncio
    async def test_remove_stock_sets_active_false(
        self, service: WatchlistService, mock_db: MagicMock
    ) -> None:
        await service.remove_stock("600519")

        coll = mock_db["watchlist"]
        coll.update_one.assert_called_once_with(
            {"stock_code": "600519"},
            {"$set": {"active": False}},
        )


class TestClear:
    """Tests for clear method."""

    @pytest.mark.asyncio
    async def test_clear_sets_all_inactive(
        self, service: WatchlistService, mock_db: MagicMock
    ) -> None:
        await service.clear()

        coll = mock_db["watchlist"]
        coll.update_many.assert_called_once_with(
            {}, {"$set": {"active": False}}
        )
