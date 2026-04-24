"""Unit tests for the settings API endpoints."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
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
    service.write_llm_config = AsyncMock(return_value={
        "providers": {},
        "agents": {},
        "defaults": {},
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
    service.write_yaml = AsyncMock()
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


# -- LLM Config: GET --


class TestGetLLMConfig:
    """Tests for GET /api/settings/llm-config."""

    async def test_returns_llm_config(self, client: AsyncClient) -> None:
        """GET returns structured config data."""
        resp = await client.get("/api/settings/llm-config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "providers" in data["data"]
        assert "agents" in data["data"]
        assert "defaults" in data["data"]

    async def test_api_keys_are_masked(self, client: AsyncClient) -> None:
        """All api_key fields contain mask value."""
        resp = await client.get("/api/settings/llm-config")
        providers = resp.json()["data"]["providers"]
        for provider in providers.values():
            assert provider["api_key"] == "***masked***"

    async def test_agents_structure(self, client: AsyncClient) -> None:
        """Agent entries contain required fields."""
        resp = await client.get("/api/settings/llm-config")
        agents = resp.json()["data"]["agents"]
        assert "news_crawler" in agents
        agent = agents["news_crawler"]
        assert agent["name"] == "新闻爬取员"
        assert agent["provider"] == "deepseek"
        assert agent["model"] == "deepseek-v4-pro"
        assert agent["fallback"]["provider"] == "qwen"


# -- LLM Config: POST --


class TestUpdateLLMConfig:
    """Tests for POST /api/settings/llm-config."""

    async def test_update_agent_provider(self, client: AsyncClient) -> None:
        """POST updates agent config successfully."""
        resp = await client.post(
            "/api/settings/llm-config",
            json={
                "agents": {
                    "news_crawler": {"provider": "qwen", "model": "qwen3.6-plus"},
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_invalid_provider_returns_422(
        self, client: AsyncClient
    ) -> None:
        """POST with invalid provider name returns 422."""
        resp = await client.post(
            "/api/settings/llm-config",
            json={"providers": {"invalid_provider": {"base_url": "http://x"}}},
        )
        assert resp.status_code == 422

    async def test_empty_update_returns_422(
        self, client: AsyncClient
    ) -> None:
        """POST with no fields returns 422."""
        resp = await client.post("/api/settings/llm-config", json={})
        assert resp.status_code == 422


# -- LLM Config: Test Connection --


class TestLLMConnectionTest:
    """Tests for POST /api/settings/llm-config/test."""

    async def test_successful_connection(
        self,
        client: AsyncClient,
        mock_config_service: AsyncMock,
    ) -> None:
        """Connection test returns success when provider responds."""
        mock_config_service.read_yaml.return_value = {
            "providers": {
                "deepseek": {
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-test",
                    "default_model": "deepseek-chat",
                },
            },
        }

        with patch(
            "backend.llm.connection_tester.test_llm_provider",
            new_callable=AsyncMock,
        ) as mock_test:
            from backend.llm.connection_tester import ConnectionTestResult

            mock_test.return_value = ConnectionTestResult(
                provider="deepseek",
                connected=True,
                latency_ms=150.5,
            )
            resp = await client.post(
                "/api/settings/llm-config/test",
                json={"provider": "deepseek"},
            )

        assert resp.status_code == 200
        result = resp.json()["data"]
        assert result["connected"] is True
        assert result["latency_ms"] == 150.5

    async def test_unknown_provider_returns_422(
        self, client: AsyncClient
    ) -> None:
        """Connection test with invalid provider returns 422."""
        resp = await client.post(
            "/api/settings/llm-config/test",
            json={"provider": "nonexistent"},
        )
        assert resp.status_code == 422

    async def test_timeout_returns_error(
        self,
        client: AsyncClient,
        mock_config_service: AsyncMock,
    ) -> None:
        """Connection test returns error details on timeout."""
        mock_config_service.read_yaml.return_value = {
            "providers": {
                "deepseek": {
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-test",
                    "default_model": "deepseek-chat",
                },
            },
        }

        with patch(
            "backend.llm.connection_tester.test_llm_provider",
            new_callable=AsyncMock,
        ) as mock_test:
            from backend.llm.connection_tester import ConnectionTestResult

            mock_test.return_value = ConnectionTestResult(
                provider="deepseek",
                connected=False,
                latency_ms=0.0,
                error="Connection timed out",
            )
            resp = await client.post(
                "/api/settings/llm-config/test",
                json={"provider": "deepseek"},
            )

        assert resp.status_code == 200
        result = resp.json()["data"]
        assert result["connected"] is False
        assert result["error"] == "Connection timed out"


# -- Data Sources --


class TestDataSources:
    """Tests for data source endpoints."""

    async def test_returns_data_sources(self, client: AsyncClient) -> None:
        """GET returns list of data sources with status."""
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

    async def test_individual_source_test(
        self, client: AsyncClient
    ) -> None:
        """POST test for a specific source returns result."""
        resp = await client.post(
            "/api/settings/data-sources/test",
            json={"source": "redis"},
        )
        assert resp.status_code == 200
        result = resp.json()["data"]
        assert result["name"] == "Redis"
        assert result["status"] == "connected"


# -- MiroFish Config --


class TestMiroFishConfig:
    """Tests for MiroFish configuration endpoints."""

    async def test_read_mirofish_config(
        self, client: AsyncClient
    ) -> None:
        """GET returns current MiroFish config."""
        resp = await client.get("/api/settings/mirofish")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "simulation" in data
        assert data["simulation"]["agent_count"] == 300

    async def test_update_mirofish_valid(
        self, client: AsyncClient
    ) -> None:
        """POST with valid params succeeds."""
        resp = await client.post(
            "/api/settings/mirofish",
            json={"agent_count": 500, "rounds": 25, "trigger_threshold": 8},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_agent_count_below_min_returns_422(
        self, client: AsyncClient
    ) -> None:
        """POST with agent_count below 100 returns 422."""
        resp = await client.post(
            "/api/settings/mirofish",
            json={"agent_count": 50},
        )
        assert resp.status_code == 422

    async def test_agent_count_above_max_returns_422(
        self, client: AsyncClient
    ) -> None:
        """POST with agent_count above 1000 returns 422."""
        resp = await client.post(
            "/api/settings/mirofish",
            json={"agent_count": 2000},
        )
        assert resp.status_code == 422

    async def test_rounds_out_of_range_returns_422(
        self, client: AsyncClient
    ) -> None:
        """POST with rounds out of [5, 50] returns 422."""
        resp = await client.post(
            "/api/settings/mirofish",
            json={"rounds": 100},
        )
        assert resp.status_code == 422

    async def test_threshold_out_of_range_returns_422(
        self, client: AsyncClient
    ) -> None:
        """POST with threshold out of [1, 10] returns 422."""
        resp = await client.post(
            "/api/settings/mirofish",
            json={"trigger_threshold": 0},
        )
        assert resp.status_code == 422


# -- Cost Stats --


class TestCostStats:
    """Tests for cost statistics endpoint."""

    async def test_returns_cost_summary(
        self, client: AsyncClient
    ) -> None:
        """GET returns cost summary structure."""
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
        """GET with no Redis data returns zero totals."""
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

    async def test_cost_calculation_accuracy(
        self, client: AsyncClient
    ) -> None:
        """Verify cost calculation matches expected value."""
        # DeepSeek: 0.2 RMB per million tokens for both input/output
        # 1000 prompt + 500 completion = 1500 tokens
        # Cost = (1000 * 0.2 / 1_000_000) + (500 * 0.2 / 1_000_000)
        #      = 0.0002 + 0.0001 = 0.0003 RMB
        from backend.llm.cost_tracker import calculate_cost

        cost = calculate_cost("deepseek", prompt_tokens=1000, completion_tokens=500)
        assert cost == pytest.approx(0.0003, abs=1e-6)

    async def test_invalid_period_returns_422(
        self, client: AsyncClient
    ) -> None:
        """GET with invalid period returns 422."""
        resp = await client.get(
            "/api/settings/cost-stats", params={"period": "hourly"}
        )
        assert resp.status_code == 422
