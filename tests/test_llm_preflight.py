"""Tests for LLM preflight + 503 cascade (Session D.1)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.llm.providers import (
    AgentConfig,
    DefaultsConfig,
    ProviderConfig,
    RouterConfig,
)
from backend.llm.router import LLMRouter
from backend.main import app
from backend.monitoring.alerter import Alerter


def _build_router_config(
    deepseek_ref: str = "${DEEPSEEK_API_KEY}",
    dashscope_ref: str = "${DASHSCOPE_API_KEY}",
    moonshot_ref: str = "${MOONSHOT_API_KEY}",
) -> RouterConfig:
    return RouterConfig(
        providers={
            "deepseek": ProviderConfig(
                base_url="https://api.deepseek.com",
                api_key=deepseek_ref,
                default_model="deepseek-v4-pro",
            ),
            "qwen": ProviderConfig(
                base_url="https://dashscope.aliyuncs.com",
                api_key=dashscope_ref,
                default_model="qwen3.6-plus",
            ),
            "kimi": ProviderConfig(
                base_url="https://api.moonshot.ai",
                api_key=moonshot_ref,
                default_model="kimi-k2.6",
            ),
        },
        agents={
            "fund_manager": AgentConfig(
                name="fund_manager",
                provider="kimi",
                model="kimi-k2.6",
            ),
        },
        defaults=DefaultsConfig(),
    )


class TestRouterPreflight:
    def test_all_keys_present(self, monkeypatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k1")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "k2")
        monkeypatch.setenv("MOONSHOT_API_KEY", "k3")
        r = LLMRouter(config_path="config/agent_models.yaml")
        r._config = _build_router_config()
        snap = r.preflight()
        assert snap == {"deepseek": True, "qwen": True, "kimi": True}

    def test_all_keys_missing(self, monkeypatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
        r = LLMRouter(config_path="config/agent_models.yaml")
        r._config = _build_router_config()
        snap = r.preflight()
        assert snap == {"deepseek": False, "qwen": False, "kimi": False}

    def test_mixed(self, monkeypatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k1")
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
        r = LLMRouter(config_path="config/agent_models.yaml")
        r._config = _build_router_config()
        snap = r.preflight()
        assert snap["deepseek"] is True
        assert snap["qwen"] is False
        assert snap["kimi"] is False

    def test_literal_key_always_present(self) -> None:
        r = LLMRouter(config_path="config/agent_models.yaml")
        r._config = _build_router_config(
            deepseek_ref="literal-key",
            dashscope_ref="literal-key",
            moonshot_ref="literal-key",
        )
        snap = r.preflight()
        assert all(snap.values())


@pytest.fixture()
async def mock_state(monkeypatch):
    """Reset app.state per test; shut down the hub in teardown so the
    background task that POST /jobs spawns does not leak into later
    tests (R3 HIGH #1)."""
    # Default: all keys present so non-503 tests can run.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k2")
    monkeypatch.setenv("MOONSHOT_API_KEY", "k3")

    router_mock = MagicMock()
    router_mock.preflight.return_value = {
        "deepseek": True,
        "qwen": True,
        "kimi": True,
    }
    app.state.llm_router = router_mock
    app.state.market_data = AsyncMock()
    app.state.history_data = AsyncMock()
    app.state.news_crawler = AsyncMock()
    app.state.mongodb = None
    from backend.services.analysis_stream import AnalysisStreamHub

    hub = AnalysisStreamHub()
    app.state.analysis_stream_hub = hub
    app.state.alerter = Alerter()
    try:
        yield router_mock
    finally:
        await hub.shutdown()


@pytest.fixture()
async def client(mock_state: MagicMock) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestPreflightCascade:
    @pytest.mark.asyncio
    async def test_jobs_503_when_all_providers_missing(
        self, client: AsyncClient, mock_state: MagicMock
    ) -> None:
        mock_state.preflight.return_value = {
            "deepseek": False,
            "qwen": False,
            "kimi": False,
        }
        resp = await client.post(
            "/api/analysis/jobs", json={"stock_code": "600519"}
        )
        assert resp.status_code == 503
        body = resp.json()
        assert "LLM providers" in body["detail"]["error"]

    @pytest.mark.asyncio
    async def test_stock_503_when_all_providers_missing(
        self, client: AsyncClient, mock_state: MagicMock
    ) -> None:
        mock_state.preflight.return_value = {
            "deepseek": False,
            "qwen": False,
            "kimi": False,
        }
        resp = await client.post(
            "/api/analysis/stock", json={"stock_code": "600519"}
        )
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_jobs_pass_when_at_least_one_provider_ok(
        self, client: AsyncClient, mock_state: MagicMock
    ) -> None:
        mock_state.preflight.return_value = {
            "deepseek": True,
            "qwen": False,
            "kimi": False,
        }

        async def fake_run_analysis(*args, **kwargs):
            # Use a do-nothing pipeline so we don't hit real services.
            from backend.agents.models import TradingSignal
            from backend.agents.records import AnalysisRecord, AnalysisRunResult
            from datetime import UTC, datetime

            now = datetime.now(tz=UTC)
            return AnalysisRunResult(
                signal=TradingSignal(
                    action="持有",
                    confidence=0.5,
                    risk_score=0.5,
                    reasoning="mock",
                    stock_code="600519",
                    stock_name="茅台",
                    trade_date="2026-04-24",
                ),
                record=AnalysisRecord(
                    run_id="run-preflight",
                    stock_code="600519",
                    stock_name="茅台",
                    trade_date="2026-04-24",
                    status="completed",
                    created_at=now,
                    completed_at=now,
                ),
            )

        with patch("backend.api.analysis.run_analysis", side_effect=fake_run_analysis):
            resp = await client.post(
                "/api/analysis/jobs", json={"stock_code": "600519"}
            )
        assert resp.status_code == 200
