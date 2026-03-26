"""Tests for simulation API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.simulation import _doc_to_history_item, _doc_to_result
from backend.main import app


def _sample_sim_doc(oid: str = "aabbccddeeff00112233aabb") -> dict:
    """Return a sample MongoDB simulation document."""
    from bson import ObjectId

    return {
        "_id": ObjectId(oid),
        "event": {
            "title": "央行宣布降准50个基点",
            "content": "降准释放长期资金1.2万亿",
            "importance_score": 9,
            "sectors": ["银行", "房地产"],
            "stocks": ["601318"],
        },
        "event_summary": "央行降准50个基点",
        "simulation_config": {
            "agent_count": 300,
            "rounds": 20,
            "model": "MiniMax-M2.5",
        },
        "sentiment_evolution": [
            {"round": 1, "bullish": 0.45, "bearish": 0.30, "neutral": 0.25},
        ],
        "hidden_variables": [
            {
                "variable": "外资加速流入概率",
                "probability": 0.72,
                "reasoning": "test reasoning",
            },
        ],
        "key_inflection_points": [{"day": 3, "event": "情绪高点"}],
        "extreme_scenarios": [
            {"scenario": "超预期利好", "probability": 0.15, "impact": "+3%"},
        ],
        "recommended_action": "短期看多",
        "cost_rmb": 3.42,
        "duration_seconds": 238.5,
        "created_at": "2026-03-24T09:30:00Z",
    }


class TestDocConversion:
    def test_doc_to_result_maps_id(self) -> None:
        doc = _sample_sim_doc()
        result = _doc_to_result(doc)
        assert result["id"] == "aabbccddeeff00112233aabb"
        assert "_id" not in result
        assert result["event_summary"] == "央行降准50个基点"

    def test_doc_to_history_item_flattens(self) -> None:
        doc = _sample_sim_doc()
        item = _doc_to_history_item(doc)
        assert item["id"] == "aabbccddeeff00112233aabb"
        assert item["event_title"] == "央行宣布降准50个基点"
        assert item["importance_score"] == 9
        assert item["agent_count"] == 300
        assert item["rounds"] == 20
        assert item["cost_rmb"] == 3.42


@pytest.fixture()
def mock_mongodb() -> MagicMock:
    """Create a mock MongoDB service with simulations collection."""
    coll = AsyncMock()
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=coll)

    mongodb = MagicMock()
    mongodb._db = db
    app.state.mongodb = mongodb
    return coll


@pytest.fixture()
async def client(mock_mongodb: MagicMock) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestGetLatest:
    @pytest.mark.asyncio
    async def test_returns_latest(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        mock_mongodb.find_one = AsyncMock(return_value=_sample_sim_doc())
        resp = await client.get("/api/simulation/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["id"] == "aabbccddeeff00112233aabb"

    @pytest.mark.asyncio
    async def test_returns_404_when_empty(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        mock_mongodb.find_one = AsyncMock(return_value=None)
        resp = await client.get("/api/simulation/latest")
        assert resp.status_code == 404


class TestGetById:
    @pytest.mark.asyncio
    async def test_returns_by_valid_id(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        mock_mongodb.find_one = AsyncMock(return_value=_sample_sim_doc())
        resp = await client.get("/api/simulation/aabbccddeeff00112233aabb")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["event_summary"] == "央行降准50个基点"

    @pytest.mark.asyncio
    async def test_rejects_invalid_id(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        resp = await client.get("/api/simulation/not-a-valid-id")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_404_when_missing(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        mock_mongodb.find_one = AsyncMock(return_value=None)
        resp = await client.get("/api/simulation/aabbccddeeff00112233aabb")
        assert resp.status_code == 404


class TestGetHistory:
    @pytest.mark.asyncio
    async def test_returns_history_list(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        cursor_mock = AsyncMock()
        cursor_mock.to_list = AsyncMock(return_value=[_sample_sim_doc()])
        # Chain: coll.find().sort().limit()
        find_mock = MagicMock()
        sort_mock = MagicMock()
        find_mock.sort = MagicMock(return_value=sort_mock)
        sort_mock.limit = MagicMock(return_value=cursor_mock)
        mock_mongodb.find = MagicMock(return_value=find_mock)

        resp = await client.get("/api/simulation/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["data"]) == 1
        assert body["data"][0]["event_title"] == "央行宣布降准50个基点"

    @pytest.mark.asyncio
    async def test_search_filter(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        cursor_mock = AsyncMock()
        cursor_mock.to_list = AsyncMock(return_value=[])
        find_mock = MagicMock()
        sort_mock = MagicMock()
        find_mock.sort = MagicMock(return_value=sort_mock)
        sort_mock.limit = MagicMock(return_value=cursor_mock)
        mock_mongodb.find = MagicMock(return_value=find_mock)

        resp = await client.get("/api/simulation/history?search=降准")
        assert resp.status_code == 200
        # Verify find was called with regex query
        call_args = mock_mongodb.find.call_args
        query = call_args[0][0]
        assert "event.title" in query
        assert query["event.title"]["$regex"] == "降准"


class TestCompare:
    @pytest.mark.asyncio
    async def test_compare_returns_both(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        doc_a = _sample_sim_doc("aabbccddeeff00112233aa01")
        doc_b = _sample_sim_doc("aabbccddeeff00112233aa02")
        mock_mongodb.find_one = AsyncMock(side_effect=[doc_a, doc_b])

        resp = await client.get(
            "/api/simulation/compare"
            "?a=aabbccddeeff00112233aa01&b=aabbccddeeff00112233aa02"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["a"]["id"] == "aabbccddeeff00112233aa01"
        assert body["data"]["b"]["id"] == "aabbccddeeff00112233aa02"

    @pytest.mark.asyncio
    async def test_rejects_invalid_ids(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        resp = await client.get("/api/simulation/compare?a=bad&b=bad")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_404_when_missing(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        mock_mongodb.find_one = AsyncMock(return_value=None)
        resp = await client.get(
            "/api/simulation/compare"
            "?a=aabbccddeeff00112233aa01&b=aabbccddeeff00112233aa02"
        )
        assert resp.status_code == 404
