"""Tests for analysis API endpoints (GET-only per P1-5 §2)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app


@pytest.fixture()
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestAnalysisWriteRoutesRemoved:
    """P1-5 §2: ``/api/analysis/stock`` and ``/api/analysis/jobs`` were
    destructively deleted in Phase A. Analysis is driven exclusively by
    the Fast/Slow scheduler; manual triggers are forbidden."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/api/analysis/stock",
            "/api/analysis/jobs",
        ],
    )
    async def test_post_returns_404_or_405(
        self, client: AsyncClient, path: str
    ) -> None:
        resp = await client.post(path, json={"stock_code": "600519"})
        # Routes were destructively deleted; FastAPI returns 404 for a
        # path with no registered handler at any method.
        assert resp.status_code in {404, 405}
