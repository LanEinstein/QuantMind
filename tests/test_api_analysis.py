"""Tests for analysis API endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.agents.models import TradingSignal
from backend.main import app


def _sample_signal() -> TradingSignal:
    return TradingSignal(
        action="买入",
        target_price=1900.0,
        confidence=0.8,
        risk_score=0.3,
        reasoning="分析完成",
        stock_code="600519",
        stock_name="贵州茅台",
        trade_date="2026-03-22",
    )


@pytest.fixture()
def mock_app_state() -> None:
    """Attach mock services to app.state."""
    app.state.llm_router = AsyncMock()
    app.state.market_data = AsyncMock()
    app.state.history_data = AsyncMock()
    app.state.news_crawler = AsyncMock()


@pytest.fixture()
async def client(mock_app_state: None) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestAnalyzeStock:
    @pytest.mark.asyncio
    async def test_success(self, client: AsyncClient) -> None:
        with patch(
            "backend.api.analysis.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_signal(),
        ):
            resp = await client.post(
                "/api/analysis/stock",
                json={"stock_code": "600519"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["action"] == "买入"
        assert body["data"]["stock_code"] == "600519"

    @pytest.mark.asyncio
    async def test_invalid_code_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/analysis/stock",
            json={"stock_code": "abc"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_custom_debate_rounds(self, client: AsyncClient) -> None:
        with patch(
            "backend.api.analysis.run_analysis",
            new_callable=AsyncMock,
            return_value=_sample_signal(),
        ):
            resp = await client.post(
                "/api/analysis/stock",
                json={"stock_code": "600519", "max_debate_rounds": 3},
            )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_server_error_500(self, client: AsyncClient) -> None:
        with patch(
            "backend.api.analysis.run_analysis",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            resp = await client.post(
                "/api/analysis/stock",
                json={"stock_code": "600519"},
            )
        assert resp.status_code == 500
