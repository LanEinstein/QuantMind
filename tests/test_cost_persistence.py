"""Tests for LLM cost persistence (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.data.database import MongoDBService
from backend.llm.cost_tracker import DailyCostEntry, flush_to_mongodb


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
