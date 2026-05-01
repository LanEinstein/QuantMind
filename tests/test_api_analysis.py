"""Tests for analysis API endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.agents.models import TradingSignal
from backend.agents.records import AnalysisRecord, AnalysisRunResult
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


def _sample_result() -> AnalysisRunResult:
    signal = _sample_signal()
    record = AnalysisRecord(
        run_id="00000000-0000-0000-0000-000000000001",
        stock_code=signal.stock_code,
        stock_name=signal.stock_name,
        trade_date=signal.trade_date,
        status="completed",
        max_rounds=2,
        current_round=2,
        created_at=datetime.now(tz=UTC),
        completed_at=datetime.now(tz=UTC),
    )
    return AnalysisRunResult(signal=signal, record=record)


@pytest.fixture()
def mock_app_state() -> None:
    """Attach mock services to app.state.

    Explicitly reset mongodb so another test's lingering fixture cannot
    trigger the signal/record persist path during these unit tests.

    ``llm_router.preflight`` is provided as a plain lambda (not an
    AsyncMock coroutine) because the API layer calls it synchronously;
    the previous AsyncMock-everything pattern returned a coroutine that
    silently bypassed the 503 guard and made the tests exercise a
    non-real path.
    """
    from unittest.mock import MagicMock

    router = AsyncMock()
    router.preflight = MagicMock(return_value={"deepseek": True})
    app.state.llm_router = router
    app.state.market_data = AsyncMock()
    app.state.history_data = AsyncMock()
    app.state.news_crawler = AsyncMock()
    app.state.mongodb = None


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
            return_value=_sample_result(),
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
            return_value=_sample_result(),
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
