"""Tests for /api/portfolio/equity-points/latest (G-004)."""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.equity_points import EquityPointReadRepository
from backend.main import app
from backend.models.equity import (
    EquityPoint,
    EquityPointPosition,
    EquityPointQuality,
)


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    if hasattr(app.state, "equity_point_repository"):
        delattr(app.state, "equity_point_repository")


@pytest.fixture()
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _sample_point() -> EquityPoint:
    snap = dt.datetime(2026, 5, 15, 10, 30, tzinfo=dt.UTC)
    pos = EquityPointPosition(
        code="600519",
        volume=100,
        cost_price=100.0,
        last_price=105.0,
        market_value=10_500.0,
        unrealized_pnl=500.0,
        unrealized_pnl_pct=0.05,
        price_quality=EquityPointQuality.FRESH,
        last_price_at=snap,
    )
    return EquityPoint(
        snapshot_at=snap,
        trade_date="2026-05-15",
        cash=900_000.0,
        frozen_cash=0.0,
        market_value=10_500.0,
        total_equity=910_500.0,
        initial_capital=1_000_000.0,
        pnl=-89_500.0,
        pnl_pct=-0.0895,
        quality=EquityPointQuality.FRESH,
        positions=(pos,),
        last_broker_event_id=42,
    )


class _StubRepo:
    def __init__(self, point: EquityPoint | None = None) -> None:
        self._point = point

    async def get_latest(self) -> EquityPoint | None:
        return self._point


class TestUnwired:
    @pytest.mark.asyncio
    async def test_returns_unavailable_without_repository(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/portfolio/equity-points/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["repository_status"] == "unavailable"
        assert body["data"]["point"] is None

    @pytest.mark.asyncio
    async def test_runtime_protocol_mismatch_falls_back(
        self,
        client: AsyncClient,
    ) -> None:
        class _NotARepo:
            pass

        app.state.equity_point_repository = _NotARepo()
        resp = await client.get("/api/portfolio/equity-points/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["repository_status"] == "unavailable"


class TestWired:
    @pytest.mark.asyncio
    async def test_returns_serialized_latest_point(
        self,
        client: AsyncClient,
    ) -> None:
        app.state.equity_point_repository = _StubRepo(point=_sample_point())
        resp = await client.get("/api/portfolio/equity-points/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["repository_status"] == "ok"
        point = body["data"]["point"]
        assert point["trade_date"] == "2026-05-15"
        assert point["quality"] == "FRESH"
        assert point["last_broker_event_id"] == 42
        # locked position fields
        assert len(point["positions"]) == 1
        pos = point["positions"][0]
        assert pos["code"] == "600519"
        assert pos["price_quality"] == "FRESH"
        assert pos["last_price_at"] is not None
        # cost_price exposed for the operator but the price_quality enum
        # makes it clear when DEGRADED — never a silent fallback.

    @pytest.mark.asyncio
    async def test_returns_null_point_when_store_empty(
        self,
        client: AsyncClient,
    ) -> None:
        app.state.equity_point_repository = _StubRepo(point=None)
        resp = await client.get("/api/portfolio/equity-points/latest")
        body = resp.json()
        assert body["data"]["point"] is None
        assert body["data"]["repository_status"] == "ok"


class TestProbeFailureIsolation:
    @pytest.mark.asyncio
    async def test_a_failing_repository_does_not_crash_endpoint(
        self,
        client: AsyncClient,
    ) -> None:
        class _BrokenRepo:
            async def get_latest(self) -> EquityPoint | None:
                raise RuntimeError("intentional failure")

        app.state.equity_point_repository = _BrokenRepo()
        resp = await client.get("/api/portfolio/equity-points/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["repository_status"] == "unavailable"


class TestProtocolRuntimeCheck:
    def test_stub_satisfies_runtime_protocol(self) -> None:
        repo = _StubRepo()
        assert isinstance(repo, EquityPointReadRepository)
