"""Tests for market and news API endpoints (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from backend.data.database import MongoDBService
from backend.data.history_data import HistoryDataService
from backend.data.market_data import MarketDataService
from backend.main import app
from backend.models.market import (
    CapitalFlowData,
    FinancialData,
    IndexQuote,
    SectorQuote,
    StockQuote,
)


def _sample_index() -> IndexQuote:
    return IndexQuote(
        code="000001",
        name="上证指数",
        price=3150.5,
        change_pct=0.85,
        volume=3_500_000_000.0,
        amount=450_000_000_000.0,
        timestamp=datetime(2026, 3, 22, 10, 30, tzinfo=UTC),
    )


def _sample_stock() -> StockQuote:
    return StockQuote(
        code="600519",
        name="贵州茅台",
        price=1800.0,
        open=1790.0,
        high=1810.0,
        low=1785.0,
        prev_close=1795.0,
        change_pct=0.28,
        volume=5_000_000.0,
        amount=9_000_000_000.0,
        turnover_rate=0.63,
        timestamp=datetime(2026, 3, 22, 10, 30, tzinfo=UTC),
    )


def _sample_sector() -> SectorQuote:
    return SectorQuote(
        name="白酒",
        change_pct=2.15,
        leader_code="600519",
        leader_name="贵州茅台",
        leader_change_pct=3.50,
        timestamp=datetime(2026, 3, 22, tzinfo=UTC),
    )


def _sample_flow() -> CapitalFlowData:
    return CapitalFlowData(
        north_net_inflow=3_200_000_000.0,
        main_net_inflow=-1_500_000_000.0,
        timestamp=datetime(2026, 3, 22, tzinfo=UTC),
    )


def _sample_financial() -> FinancialData:
    return FinancialData(
        code="600519",
        name="贵州茅台",
        pe_ratio=32.5,
        pb_ratio=10.2,
        roe=30.5,
        eps=45.8,
        revenue_growth=15.3,
        report_date="2025-12-31",
        timestamp=datetime(2026, 3, 22, tzinfo=UTC),
    )


@pytest.fixture()
def mock_services() -> dict[str, AsyncMock]:
    """Create mock services and attach to app.state."""
    market = AsyncMock(spec=MarketDataService)
    market.get_index_realtime = AsyncMock(return_value=[_sample_index()])
    market.get_stock_realtime = AsyncMock(return_value=_sample_stock())
    market.get_sector_overview = AsyncMock(return_value=[_sample_sector()])
    market.get_capital_flow = AsyncMock(return_value=_sample_flow())

    history = AsyncMock(spec=HistoryDataService)
    history.get_kline = AsyncMock(
        return_value=pd.DataFrame(
            [{"date": "2026-03-20", "open": 1790, "high": 1810, "low": 1785,
              "close": 1800, "volume": 5000000, "amount": 9000000000}]
        )
    )
    history.get_financial_data = AsyncMock(return_value=_sample_financial())

    mongodb = AsyncMock(spec=MongoDBService)
    mongodb.query_news = AsyncMock(
        return_value=[
            {
                "title": "Test",
                "content": "Test content",
                "source": "eastmoney",
                "url": "https://example.com/1",
                "publish_time": "2026-03-22T09:00:00Z",
                "stock_codes": [],
                "importance_score": 0,
            }
        ]
    )

    app.state.market_data = market
    app.state.history_data = history
    app.state.mongodb = mongodb

    return {"market": market, "history": history, "mongodb": mongodb}


@pytest.fixture()
async def client(mock_services: dict[str, AsyncMock]) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestMarketIndices:
    @pytest.mark.asyncio
    async def test_returns_indices(self, client: AsyncClient) -> None:
        resp = await client.get("/api/market/indices")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["data"]) == 1
        assert body["data"][0]["code"] == "000001"


class TestMarketStock:
    @pytest.mark.asyncio
    async def test_returns_stock(self, client: AsyncClient) -> None:
        resp = await client.get("/api/market/stock/600519")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["code"] == "600519"

    @pytest.mark.asyncio
    async def test_invalid_code_422(self, client: AsyncClient) -> None:
        resp = await client.get("/api/market/stock/abc")
        assert resp.status_code == 422


class TestMarketSectors:
    @pytest.mark.asyncio
    async def test_returns_sectors(self, client: AsyncClient) -> None:
        resp = await client.get("/api/market/sectors")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["data"]) == 1


class TestCapitalFlow:
    @pytest.mark.asyncio
    async def test_returns_flow(self, client: AsyncClient) -> None:
        resp = await client.get("/api/market/capital-flow")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "north_net_inflow" in body["data"]


class TestKline:
    @pytest.mark.asyncio
    async def test_returns_kline(self, client: AsyncClient) -> None:
        resp = await client.get("/api/market/kline/600519")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["data"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_period_422(self, client: AsyncClient) -> None:
        resp = await client.get("/api/market/kline/600519?period=hourly")
        assert resp.status_code == 422


class TestFinancial:
    @pytest.mark.asyncio
    async def test_returns_financial(self, client: AsyncClient) -> None:
        resp = await client.get("/api/market/financial/600519")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["code"] == "600519"


class TestNewsLatest:
    @pytest.mark.asyncio
    async def test_returns_news(self, client: AsyncClient) -> None:
        resp = await client.get("/api/news/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert len(body["data"]) == 1


class TestNewsStock:
    @pytest.mark.asyncio
    async def test_returns_stock_news(self, client: AsyncClient) -> None:
        resp = await client.get("/api/news/stock/600519")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
