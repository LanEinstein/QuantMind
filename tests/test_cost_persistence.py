"""Tests for LLM cost persistence (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.data.database import MongoDBService
from backend.llm.cost_tracker import (
    DailyCostEntry,
    _parse_usage_key,
    flush_to_mongodb,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_db() -> MagicMock:
    """Create a mock database with cost_tracking collection."""
    db = MagicMock()
    coll = AsyncMock()
    coll.create_index = AsyncMock()
    coll.update_one = AsyncMock()

    cursor = AsyncMock()
    cursor.to_list = AsyncMock(return_value=[])
    cursor.sort = MagicMock(return_value=cursor)
    coll.find = MagicMock(return_value=cursor)

    default_coll = AsyncMock()
    default_coll.create_index = AsyncMock()

    def getitem(name: str) -> AsyncMock:
        if name == "cost_tracking":
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
# Tests: save_cost_entry
# ---------------------------------------------------------------------------


class TestSaveCostEntry:
    """Tests for MongoDBService.save_cost_entry."""

    @pytest.mark.asyncio
    async def test_upserts_entry(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        entry = {
            "date": "2026-04-13",
            "agent_name": "news_crawler",
            "provider": "deepseek",
            "prompt_tokens": 500,
            "completion_tokens": 200,
            "requests": 3,
            "cost_rmb": 0.001,
        }
        await service.save_cost_entry(entry)

        coll = mock_db["cost_tracking"]
        coll.update_one.assert_called_once()
        call_args = coll.update_one.call_args
        assert call_args[0][0] == {
            "date": "2026-04-13",
            "agent_name": "news_crawler",
            "provider": "deepseek",
        }
        assert call_args[1]["upsert"] is True


# ---------------------------------------------------------------------------
# Tests: get_cost_history
# ---------------------------------------------------------------------------


class TestGetCostHistory:
    """Tests for MongoDBService.get_cost_history."""

    @pytest.mark.asyncio
    async def test_returns_sorted(
        self, service: MongoDBService, mock_db: MagicMock
    ) -> None:
        coll = mock_db["cost_tracking"]
        cursor = coll.find.return_value
        cursor.to_list.return_value = [
            {"date": "2026-04-13", "cost_rmb": 0.5},
            {"date": "2026-04-12", "cost_rmb": 0.3},
        ]

        result = await service.get_cost_history(days=7)

        assert isinstance(result, list)
        assert len(result) == 2
        coll.find.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: flush_to_mongodb
# ---------------------------------------------------------------------------


class TestFlushToMongoDB:
    """Tests for flush_to_mongodb function."""

    @pytest.mark.asyncio
    async def test_persists_all_entries(self) -> None:
        from backend.llm.cost_tracker import CostSummary

        entries = (
            DailyCostEntry("2026-04-13", "news_crawler", "deepseek", 500, 200, 3, 0.001),
            DailyCostEntry("2026-04-13", "fund_manager", "qwen", 1000, 500, 2, 0.005),
        )
        summary = CostSummary(
            period="daily", days=1, entries=entries,
            total_cost_rmb=0.006, total_requests=5,
            total_prompt_tokens=1500, total_completion_tokens=700,
            by_agent={"news_crawler": 0.001, "fund_manager": 0.005},
            by_provider={"deepseek": 0.001, "qwen": 0.005},
            daily_totals={"2026-04-13": 0.006},
        )

        mock_redis = AsyncMock()
        mock_mongodb = AsyncMock()
        mock_mongodb.save_cost_entry = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "backend.llm.cost_tracker.aggregate_costs",
                AsyncMock(return_value=summary),
            )
            count = await flush_to_mongodb(mock_redis, mock_mongodb, days=1)

        assert count == 2
        assert mock_mongodb.save_cost_entry.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_empty(self) -> None:
        from backend.llm.cost_tracker import CostSummary

        summary = CostSummary(
            period="daily", days=1, entries=(),
            total_cost_rmb=0.0, total_requests=0,
            total_prompt_tokens=0, total_completion_tokens=0,
            by_agent={}, by_provider={}, daily_totals={},
        )

        mock_redis = AsyncMock()
        mock_mongodb = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "backend.llm.cost_tracker.aggregate_costs",
                AsyncMock(return_value=summary),
            )
            count = await flush_to_mongodb(mock_redis, mock_mongodb, days=1)

        assert count == 0
        mock_mongodb.save_cost_entry.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _parse_usage_key validation (drops corrupt cost entries)
# ---------------------------------------------------------------------------


class TestParseUsageKeyCostValidation:
    """Per-entry guard against negative or non-finite cost_rmb.

    A single corrupt entry with cost_rmb=-1.0 or "nan" must NOT be able
    to offset legitimate spend in the daily aggregate, which would
    silently undercut the cost_guard hard cap. The parser drops the
    entry and logs a warning so cost_guard works on clean data.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_cost",
        ["-1.0", "-100.5", "nan", "inf", "-inf"],
    )
    async def test_drops_invalid_cost_entries(self, bad_cost: str) -> None:
        redis_client = AsyncMock()
        redis_client.hgetall = AsyncMock(
            return_value={
                "prompt_tokens": "100",
                "completion_tokens": "200",
                "requests": "1",
                "cost_rmb": bad_cost,
            }
        )
        result = await _parse_usage_key(
            redis_client,
            "llm:usage:2026-05-01:bull_researcher:kimi",
            "2026-05-01",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_accepts_zero_cost(self) -> None:
        """Zero cost is a valid case (e.g., cached/free responses)."""
        redis_client = AsyncMock()
        redis_client.hgetall = AsyncMock(
            return_value={
                "prompt_tokens": "100",
                "completion_tokens": "0",
                "requests": "1",
                "cost_rmb": "0.0",
            }
        )
        result = await _parse_usage_key(
            redis_client,
            "llm:usage:2026-05-01:news_crawler:deepseek",
            "2026-05-01",
        )
        assert result is not None
        assert result.cost_rmb == 0.0

    @pytest.mark.asyncio
    async def test_accepts_positive_cost(self) -> None:
        redis_client = AsyncMock()
        redis_client.hgetall = AsyncMock(
            return_value={
                "prompt_tokens": "1000",
                "completion_tokens": "500",
                "requests": "2",
                "cost_rmb": "0.34",
            }
        )
        result = await _parse_usage_key(
            redis_client,
            "llm:usage:2026-05-01:bull_researcher:kimi",
            "2026-05-01",
        )
        assert result is not None
        assert result.cost_rmb == 0.34

