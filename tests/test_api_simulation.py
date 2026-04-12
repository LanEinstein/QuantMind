"""Tests for simulation API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.simulation import _doc_to_history_item, _doc_to_result
from backend.main import app


def _sample_sim_doc(oid: str = "aabbccddeeff00112233aabb") -> dict:
    """Return a sample MongoDB simulation document with all enriched fields."""
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
            {
                "round": 1,
                "bullish": 0.45,
                "bearish": 0.30,
                "neutral": 0.25,
                "dominant_narrative": "降准预期升温",
                "intensity": 0.75,
            },
        ],
        "hidden_variables": [
            {
                "variable": "外资加速流入概率",
                "probability": 0.72,
                "reasoning": "降准叠加汇率企稳",
                "agent_consensus_ratio": 0.68,
                "is_absent_from_original": True,
            },
        ],
        "key_inflection_points": [
            {
                "day": 3,
                "event": "情绪高点",
                "inflection_type": "sentiment_reversal",
                "before_sentiment": {"bullish": 0.3, "bearish": 0.5, "neutral": 0.2},
                "after_sentiment": {"bullish": 0.6, "bearish": 0.2, "neutral": 0.2},
                "confidence": 0.85,
            },
        ],
        "extreme_scenarios": [
            {
                "scenario": "超预期利好",
                "probability": 0.15,
                "impact": "+3%",
                "direction": "upside",
                "trigger_conditions": "美联储超预期降息",
                "early_warning_signals": "外资持续流入",
            },
        ],
        "momentum_shifts": [
            {
                "round_number": 3,
                "direction": "bullish_to_bearish",
                "magnitude": 0.23,
                "trigger_narrative": "获利回吐",
            },
        ],
        "recommended_action": "短期看多",
        "cost_rmb": 3.42,
        "duration_seconds": 238.5,
        "created_at": "2026-03-24T09:30:00Z",
    }


def _legacy_sim_doc(oid: str = "aabbccddeeff00112233aacc") -> dict:
    """Return a legacy MongoDB document missing all enriched fields."""
    from bson import ObjectId

    return {
        "_id": ObjectId(oid),
        "event": {
            "title": "旧版仿真事件",
            "content": "旧版内容",
            "importance_score": 7,
            "sectors": [],
            "stocks": [],
        },
        "event_summary": "旧版事件摘要",
        "simulation_config": {
            "agent_count": 300,
            "rounds": 20,
            "model": "MiniMax-M2.5",
        },
        "sentiment_evolution": [
            {"round": 1, "bullish": 0.4, "bearish": 0.3, "neutral": 0.3},
        ],
        "hidden_variables": [
            {"variable": "x", "probability": 0.5, "reasoning": "r"},
        ],
        "key_inflection_points": [{"day": 2, "event": "旧拐点"}],
        "extreme_scenarios": [
            {"scenario": "旧场景", "probability": 0.1, "impact": "+2%"},
        ],
        "recommended_action": "观望",
        "cost_rmb": 1.5,
        "duration_seconds": 60.0,
        "created_at": "2025-01-01T00:00:00Z",
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


class TestEnrichedFieldsInAPI:
    @pytest.mark.asyncio
    async def test_latest_endpoint_returns_enriched_hidden_variables(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        mock_mongodb.find_one = AsyncMock(return_value=_sample_sim_doc())
        resp = await client.get("/api/simulation/latest")
        assert resp.status_code == 200
        hv = resp.json()["data"]["hidden_variables"][0]
        assert hv["agent_consensus_ratio"] == 0.68
        assert hv["is_absent_from_original"] is True
        # reasoning must be the clean string, no stringified suffixes
        assert "[consensus=" not in hv["reasoning"]

    @pytest.mark.asyncio
    async def test_get_by_id_returns_inflection_metadata(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        mock_mongodb.find_one = AsyncMock(return_value=_sample_sim_doc())
        resp = await client.get("/api/simulation/aabbccddeeff00112233aabb")
        assert resp.status_code == 200
        ip = resp.json()["data"]["key_inflection_points"][0]
        assert ip["inflection_type"] == "sentiment_reversal"
        assert ip["confidence"] == 0.85
        assert ip["before_sentiment"]["bullish"] == 0.3

    @pytest.mark.asyncio
    async def test_get_by_id_returns_extreme_scenario_direction(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        mock_mongodb.find_one = AsyncMock(return_value=_sample_sim_doc())
        resp = await client.get("/api/simulation/aabbccddeeff00112233aabb")
        assert resp.status_code == 200
        es = resp.json()["data"]["extreme_scenarios"][0]
        assert es["direction"] == "upside"
        assert es["trigger_conditions"] == "美联储超预期降息"
        assert es["early_warning_signals"] == "外资持续流入"

    @pytest.mark.asyncio
    async def test_compare_endpoint_returns_enriched_for_both_sims(
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
        body = resp.json()["data"]
        for side in ("a", "b"):
            hv = body[side]["hidden_variables"][0]
            assert hv["agent_consensus_ratio"] == 0.68

    @pytest.mark.asyncio
    async def test_history_endpoint_does_not_include_enriched_payload(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        cursor_mock = AsyncMock()
        cursor_mock.to_list = AsyncMock(return_value=[_sample_sim_doc()])
        find_mock = MagicMock()
        sort_mock = MagicMock()
        find_mock.sort = MagicMock(return_value=sort_mock)
        sort_mock.limit = MagicMock(return_value=cursor_mock)
        mock_mongodb.find = MagicMock(return_value=find_mock)

        resp = await client.get("/api/simulation/history")
        assert resp.status_code == 200
        item = resp.json()["data"][0]
        # History items should NOT contain full enriched payload
        assert "hidden_variables" not in item
        assert "key_inflection_points" not in item
        # But basic history fields must be present
        assert item["event_title"] == "央行宣布降准50个基点"

    @pytest.mark.asyncio
    async def test_legacy_doc_without_enriched_fields_deserializes_on_fetch(
        self, client: AsyncClient, mock_mongodb: MagicMock
    ) -> None:
        """Legacy docs (missing enriched fields) must not cause 500 errors."""
        mock_mongodb.find_one = AsyncMock(return_value=_legacy_sim_doc())
        resp = await client.get("/api/simulation/aabbccddeeff00112233aacc")
        # API must return 200, not crash on missing fields
        assert resp.status_code == 200
        data = resp.json()["data"]
        # Basic fields from legacy doc must still be present
        assert data["event_summary"] == "旧版事件摘要"
        assert data["recommended_action"] == "观望"
        # Sentiment evolution passes through as-is (no Pydantic enrichment at API layer)
        snap = data["sentiment_evolution"][0]
        assert snap["round"] == 1
        assert snap["bullish"] == 0.4
