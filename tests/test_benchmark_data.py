"""Tests for CSI300 benchmark data (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from backend.api.performance import compute_equity_curve
from backend.data.database import MongoDBService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_db() -> MagicMock:
    """Create a mock database with index_prices collection."""
    db = MagicMock()
    coll = AsyncMock()
    coll.create_index = AsyncMock()
    bulk_result = MagicMock(upserted_count=2, modified_count=0)
    coll.bulk_write = AsyncMock(return_value=bulk_result)

    cursor = AsyncMock()
    cursor.to_list = AsyncMock(return_value=[])
    cursor.sort = MagicMock(return_value=cursor)
    coll.find = MagicMock(return_value=cursor)

    default_coll = AsyncMock()
    default_coll.create_index = AsyncMock()

    def getitem(name: str) -> AsyncMock:
        if name == "index_prices":
            return coll
        return default_coll

    db.__getitem__ = MagicMock(side_effect=getitem)
    return db


@pytest.fixture()
def mock_db() -> MagicMock:
    return _make_mock_db()


@pytest.fixture()
def service(mock_db: MagicMock) -> MongoDBService:
    return MongoDBService(mock_db)


# ---------------------------------------------------------------------------
# Tests: get_index_history
# ---------------------------------------------------------------------------


class TestGetIndexHistory:
    """Tests for MarketDataService.get_index_history."""

    @pytest.mark.asyncio
    async def test_returns_dataframe(self) -> None:
        from backend.data.market_data import MarketDataService

        config = MagicMock()
        svc = MarketDataService(config)

        mock_df = pd.DataFrame([
            {
                "日期": "2026-04-10", "开盘": 3800, "最高": 3850,
                "最低": 3780, "收盘": 3830, "成交量": 1000000,
            },
            {
                "日期": "2026-04-11", "开盘": 3830, "最高": 3860,
                "最低": 3810, "收盘": 3850, "成交量": 1100000,
            },
        ])

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "backend.data.market_data._fetch_index_history_akshare",
                lambda code, start, end: mock_df,
            )
            result = await svc.get_index_history("000300", days=5)

        assert isinstance(result, pd.DataFrame)
        assert "date" in result.columns
        assert "close" in result.columns
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Tests: save_index_prices / get_index_prices
# ---------------------------------------------------------------------------


class TestSaveIndexPrices:
    """Tests for MongoDBService.save_index_prices."""

    @pytest.mark.asyncio
    async def test_upserts_prices(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        prices = [
            {"date": "2026-04-10", "close": 3830.0, "open": 3800.0},
            {"date": "2026-04-11", "close": 3850.0, "open": 3830.0},
        ]
        count = await service.save_index_prices("000300", prices)

        coll = mock_db["index_prices"]
        coll.bulk_write.assert_called_once()
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_empty_prices_returns_zero(
        self, service: MongoDBService
    ) -> None:
        count = await service.save_index_prices("000300", [])
        assert count == 0


class TestGetIndexPrices:
    """Tests for MongoDBService.get_index_prices."""

    @pytest.mark.asyncio
    async def test_returns_sorted_list(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["index_prices"]
        cursor = coll.find.return_value
        cursor.to_list.return_value = [
            {"index_code": "000300", "date": "2026-04-10", "close": 3830.0},
            {"index_code": "000300", "date": "2026-04-11", "close": 3850.0},
        ]

        result = await service.get_index_prices("000300")

        assert isinstance(result, list)
        assert len(result) == 2
        coll.find.assert_called_once()
        query = coll.find.call_args[0][0]
        assert query["index_code"] == "000300"


# ---------------------------------------------------------------------------
# Tests: compute_equity_curve with benchmark
# ---------------------------------------------------------------------------


class TestComputeEquityCurveWithBenchmark:
    """Tests for compute_equity_curve with real benchmark data."""

    def test_with_benchmark_prices(self) -> None:
        benchmark_prices = [
            {"date": "2026-04-07", "close": 3800.0},
            {"date": "2026-04-08", "close": 3830.0},
            {"date": "2026-04-09", "close": 3810.0},
            {"date": "2026-04-10", "close": 3850.0},
            {"date": "2026-04-11", "close": 3870.0},
        ]
        result = compute_equity_curve(
            trades=(),
            initial_capital=1_000_000,
            start=date(2026, 4, 7),
            end=date(2026, 4, 11),
            benchmark_prices=benchmark_prices,
        )

        assert len(result) > 0
        # First benchmark point should be 100.0 (normalized)
        assert result[0]["benchmark"] == 100.0
        # Subsequent points should track actual index movement
        # 3830/3800 * 100 = 100.79
        assert result[1]["benchmark"] != 100.0

    def test_flat_fallback_without_benchmark(self) -> None:
        result = compute_equity_curve(
            trades=(),
            initial_capital=1_000_000,
            start=date(2026, 4, 7),
            end=date(2026, 4, 11),
            benchmark_prices=None,
        )

        assert len(result) > 0
        # All benchmark values should be flat at 100.0
        for point in result:
            assert point["benchmark"] == 100.0
