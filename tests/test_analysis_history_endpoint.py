"""Tests for /api/analysis/history and /api/analysis/{record_id} endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from backend.main import app

_OID = ObjectId()


def _stored_record_doc(
    run_id: str = "run-001",
    stock_code: str = "600519",
    action: str = "买入",
) -> dict:
    iso = datetime(2026, 4, 24, 9, 50, tzinfo=UTC).isoformat()
    return {
        "_id": _OID,
        "run_id": run_id,
        "stock_code": stock_code,
        "stock_name": "贵州茅台",
        "trade_date": "2026-04-24",
        "status": "completed",
        "max_rounds": 2,
        "current_round": 2,
        "steps": [],
        "analysts": [],
        "intelligence_officer": None,
        "debates": [],
        "risk_assessment": None,
        "decision": {
            "action": action,
            "target_price": 1900.0,
            "confidence": 0.82,
            "risk_score": 0.3,
            "reasoning": "强势",
        },
        "signal_id": "signal-abc",
        "created_at": iso,
        "completed_at": iso,
        "error": None,
    }


@pytest.fixture()
def mock_mongodb() -> AsyncMock:
    m = AsyncMock()
    m.query_analysis_records = AsyncMock(
        return_value=[
            _stored_record_doc(run_id="run-001"),
            _stored_record_doc(run_id="run-002", action="持有"),
        ]
    )
    m.get_analysis_record_by_id = AsyncMock(return_value=None)
    return m


@pytest.fixture()
def app_state(mock_mongodb: AsyncMock) -> AsyncMock:
    app.state.mongodb = mock_mongodb
    yield mock_mongodb
    # restore state is not strictly needed; other tests reassign


@pytest.fixture()
async def client(app_state: AsyncMock) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestListHistory:
    @pytest.mark.asyncio
    async def test_returns_envelope_with_summaries(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/api/analysis/history?limit=5")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["error"] is None
        rows = body["data"]
        assert isinstance(rows, list)
        assert len(rows) == 2
        first = rows[0]
        assert first["run_id"] == "run-001"
        assert first["stock_code"] == "600519"
        assert first["action"] == "买入"
        assert first["id"] == str(_OID)

    @pytest.mark.asyncio
    async def test_filters_forwarded(
        self, client: AsyncClient, app_state: AsyncMock
    ) -> None:
        await client.get(
            "/api/analysis/history"
            "?stock_code=600519&trade_date=2026-04-24&limit=3"
        )
        call_kwargs = app_state.query_analysis_records.call_args.kwargs
        assert call_kwargs["stock_code"] == "600519"
        assert call_kwargs["trade_date"] == "2026-04-24"
        assert call_kwargs["limit"] == 3

    @pytest.mark.asyncio
    async def test_limit_bounds(self, client: AsyncClient) -> None:
        # limit > 500 should be rejected by query validator (422)
        resp = await client.get("/api/analysis/history?limit=10000")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_history_route_not_swallowed_by_wildcard(
        self, client: AsyncClient
    ) -> None:
        """Regression: /history must resolve to list, not detail with id=history."""
        resp = await client.get("/api/analysis/history")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["data"], list)


class TestGetDetail:
    @pytest.mark.asyncio
    async def test_returns_detail(
        self, client: AsyncClient, app_state: AsyncMock
    ) -> None:
        app_state.get_analysis_record_by_id.return_value = _stored_record_doc()
        resp = await client.get(f"/api/analysis/{_OID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["data"]["id"] == str(_OID)
        assert body["data"]["run_id"] == "run-001"
        # _id key should have been converted
        assert "_id" not in body["data"]

    @pytest.mark.asyncio
    async def test_404_when_missing(
        self, client: AsyncClient, app_state: AsyncMock
    ) -> None:
        app_state.get_analysis_record_by_id.return_value = None
        resp = await client.get("/api/analysis/some-missing-id")
        assert resp.status_code == 404
        body = resp.json()
        # Structured envelope error, not raw FastAPI default
        detail = body["detail"]
        assert detail["status"] == "error"
        assert detail["data"] is None
        assert "not found" in detail["error"].lower()

    @pytest.mark.asyncio
    async def test_garbage_id_does_not_500(
        self, client: AsyncClient, app_state: AsyncMock
    ) -> None:
        """ObjectId parse failure must not bubble up as 500."""
        app_state.get_analysis_record_by_id.return_value = None
        resp = await client.get("/api/analysis/@@not-valid@@")
        assert resp.status_code == 404
