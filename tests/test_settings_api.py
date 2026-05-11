"""Unit tests for the settings API endpoints (GET-only per P1-5 §2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.llm.cost_tracker import CostSummary, DailyCostEntry
from backend.main import app


@pytest.fixture()
def mock_config_service() -> AsyncMock:
    """Create a mock ConfigService."""
    service = AsyncMock()
    service.read_llm_config = AsyncMock(return_value={
        "providers": {
            "deepseek": {
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "***masked***",
                "default_model": "deepseek-v4-pro",
            },
            "qwen": {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "***masked***",
                "default_model": "qwen3.6-plus",
            },
            "kimi": {
                "base_url": "https://api.moonshot.ai/v1",
                "api_key": "***masked***",
                "default_model": "kimi-k2.6",
            },
        },
        "defaults": {"temperature": 0.3, "max_tokens": 4096},
        "agents": {
            "news_crawler": {
                "name": "新闻爬取员",
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "fallback": {"provider": "qwen", "model": "qwen3.6-plus"},
                "frequency": "every_5min",
                "task": "财经新闻摘要、分类、重要性评分(0-10)",
            },
        },
    })
    service.read_yaml = AsyncMock(return_value={
        "simulation": {
            "enabled": True,
            "agent_count": 300,
            "rounds": 20,
            "model": "kimi-k2.6",
            "trigger_threshold": 7,
        },
        "cost_estimate": {
            "input_price_per_1k": 0.0021,
            "output_price_per_1k": 0.0084,
        },
    })
    return service


@pytest.fixture()
def mock_redis_client() -> AsyncMock:
    """Create a mock Redis client."""
    redis_mock = AsyncMock()
    redis_mock.ping = AsyncMock(return_value=True)
    redis_mock.scan = AsyncMock(return_value=(0, []))
    return redis_mock


@pytest.fixture()
def mock_mongo_client() -> MagicMock:
    """Create a mock MongoDB client."""
    client = MagicMock()
    client.admin.command = AsyncMock(return_value={"ok": 1})
    return client


@pytest.fixture()
async def client(
    mock_config_service: AsyncMock,
    mock_redis_client: AsyncMock,
    mock_mongo_client: MagicMock,
) -> AsyncClient:
    """Create an async HTTP test client with mocked services."""
    app.state.config_service = mock_config_service
    app.state.redis = mock_redis_client
    app.state.mongo_client = mock_mongo_client
    app.state.mongodb = MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# -- GET /api/settings/llm-config --


class TestGetLLMConfig:
    async def test_returns_llm_config(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/llm-config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "providers" in data["data"]
        assert "agents" in data["data"]
        assert "defaults" in data["data"]

    async def test_api_keys_are_masked(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/llm-config")
        providers = resp.json()["data"]["providers"]
        for provider in providers.values():
            assert provider["api_key"] == "***masked***"


# -- Settings write routes destructively removed in Phase A --


class TestSettingsWriteRoutesRemoved:
    """P1-5 §2: settings POST handlers were destructively deleted. The
    runtime is no longer permitted to mutate config/agent_models.yaml,
    config/mirofish.yaml, or config/data_sources.yaml over HTTP."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path, body",
        [
            ("/api/settings/llm-config", {"agents": {"news_crawler": {"provider": "qwen"}}}),
            ("/api/settings/llm-config/test", {"provider": "deepseek"}),
            ("/api/settings/data-sources/test", {"source": "redis"}),
            ("/api/settings/mirofish", {"agent_count": 500}),
        ],
    )
    async def test_post_returns_404_or_405(
        self, client: AsyncClient, path: str, body: dict
    ) -> None:
        resp = await client.post(path, json=body)
        assert resp.status_code in {404, 405}


# -- GET /api/settings/data-sources --


class TestDataSources:
    async def test_returns_data_sources(self, client: AsyncClient) -> None:
        app.state.config_service.read_yaml.return_value = {
            "market_data": {"primary": "adata", "fallback": "akshare"},
            "history_data": {"primary": "adata", "fallback": "baostock"},
            "news": {"refresh_interval_seconds": 300},
        }
        resp = await client.get("/api/settings/data-sources")
        assert resp.status_code == 200
        sources = resp.json()["data"]
        names = [s["name"] for s in sources]
        assert "adata" in names
        assert "MongoDB" in names
        assert "Redis" in names


# -- GET /api/settings/mirofish --


class TestMiroFishConfig:
    async def test_read_mirofish_config(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/mirofish")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "simulation" in data
        assert data["simulation"]["agent_count"] == 300


# -- Cost Stats --


class TestCostStats:
    async def test_returns_cost_summary(
        self, client: AsyncClient
    ) -> None:
        with patch(
            "backend.llm.cost_tracker.aggregate_costs",
            new_callable=AsyncMock,
        ) as mock_agg:
            mock_agg.return_value = CostSummary(
                period="daily",
                days=30,
                entries=(
                    DailyCostEntry(
                        date="2026-03-25",
                        agent_name="news_crawler",
                        provider="deepseek",
                        prompt_tokens=1000,
                        completion_tokens=500,
                        requests=10,
                        cost_rmb=0.0003,
                    ),
                ),
                total_cost_rmb=0.0003,
                total_requests=10,
                total_prompt_tokens=1000,
                total_completion_tokens=500,
                by_agent={"news_crawler": 0.0003},
                by_provider={"deepseek": 0.0003},
                daily_totals={"2026-03-25": 0.0003},
            )
            resp = await client.get(
                "/api/settings/cost-stats", params={"period": "daily", "days": 30}
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["period"] == "daily"
        assert data["total_requests"] == 10
        assert "by_agent" in data
        assert "by_provider" in data
        assert "daily_totals" in data

    async def test_empty_redis_returns_zeros(
        self, client: AsyncClient
    ) -> None:
        with patch(
            "backend.llm.cost_tracker.aggregate_costs",
            new_callable=AsyncMock,
        ) as mock_agg:
            mock_agg.return_value = CostSummary(
                period="daily",
                days=30,
                entries=(),
                total_cost_rmb=0.0,
                total_requests=0,
                total_prompt_tokens=0,
                total_completion_tokens=0,
                by_agent={},
                by_provider={},
                daily_totals={},
            )
            resp = await client.get("/api/settings/cost-stats")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total_cost_rmb"] == 0.0
        assert data["total_requests"] == 0

    async def test_invalid_period_returns_422(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get(
            "/api/settings/cost-stats", params={"period": "hourly"}
        )
        assert resp.status_code == 422
