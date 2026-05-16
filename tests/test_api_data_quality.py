"""G-008 — backend/api/data_quality.py tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.data_quality import router as data_quality_router


@dataclass(frozen=True)
class _StubState:
    """Mirror of the locked P1-2.B §1.5.1 DataQualityState schema.

    Same 7 breach bools + 3 counter ints the real provider exposes;
    derived ``is_acceptable_for_buy_sell`` / ``degradation_reason`` are
    properties so the API serializer reads them the same way as in
    production (codex cycle 1 P2 RESOLVED — using the real names lets
    the regression catch a schema drift at unit-test time).
    """

    quote_unavailable: bool
    quote_staleness_breach: bool
    quote_divergence_breach: bool
    minimum_freshness_breach: bool
    news_outage_breach: bool
    mirofish_unavailable: bool
    watchlist_snapshot_outage: bool

    primary_quote_age_seconds: int
    backup_quote_age_seconds: int
    news_sources_alive_count: int

    @property
    def is_acceptable_for_buy_sell(self) -> bool:
        return not (
            self.quote_unavailable
            or self.quote_staleness_breach
            or self.quote_divergence_breach
            or self.minimum_freshness_breach
        )

    @property
    def degradation_reason(self) -> str | None:
        reasons: list[str] = []
        if self.quote_unavailable:
            reasons.append("quote_unavailable")
        if self.quote_staleness_breach:
            reasons.append("quote_staleness_breach")
        if self.quote_divergence_breach:
            reasons.append("quote_divergence_breach")
        if self.minimum_freshness_breach:
            reasons.append("minimum_freshness_breach")
        return "+".join(reasons) if reasons else None


def _make_state(*, acceptable: bool = True) -> _StubState:
    return _StubState(
        quote_unavailable=False,
        quote_staleness_breach=False,
        quote_divergence_breach=not acceptable,
        minimum_freshness_breach=False,
        news_outage_breach=False,
        mirofish_unavailable=False,
        watchlist_snapshot_outage=False,
        primary_quote_age_seconds=2,
        backup_quote_age_seconds=3,
        news_sources_alive_count=5,
    )


def _build_app(probe: object | None) -> FastAPI:
    app = FastAPI()
    app.state.data_quality_provider = probe
    app.include_router(data_quality_router)
    return app


@pytest.mark.asyncio
async def test_unavailable_when_probe_missing() -> None:
    app = _build_app(probe=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/data-quality", params={"stock_code": "600519"}
        )
    body = resp.json()
    assert resp.status_code == 200
    assert body["data"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_unavailable_when_probe_lacks_evaluate() -> None:
    class _Broken:
        pass

    app = _build_app(probe=_Broken())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/data-quality", params={"stock_code": "600519"}
        )
    assert resp.json()["data"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_happy_path_serializes_state() -> None:
    state = _make_state()
    probe = AsyncMock()
    probe.evaluate = AsyncMock(return_value=state)
    app = _build_app(probe=probe)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/data-quality", params={"stock_code": "600519"}
        )
    body = resp.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["state"]["stock_code"] == "600519"
    assert body["data"]["state"]["is_acceptable_for_buy_sell"] is True
    assert body["data"]["state"]["blocking_breaches"] == []
    # Real provider signature is ``evaluate(stock_code, now)``; the
    # endpoint must forward ``now`` so the C-004 production wiring
    # works (codex cycle 1 P2 regression).
    probe.evaluate.assert_awaited_once()
    call_kwargs = probe.evaluate.await_args.kwargs
    assert call_kwargs["stock_code"] == "600519"
    assert "now" in call_kwargs
    assert call_kwargs["now"].tzinfo is not None


@pytest.mark.asyncio
async def test_blocking_breaches_serialised() -> None:
    state = _make_state(acceptable=False)
    probe = AsyncMock()
    probe.evaluate = AsyncMock(return_value=state)
    app = _build_app(probe=probe)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/data-quality", params={"stock_code": "600519"}
        )
    body = resp.json()
    serialised = body["data"]["state"]
    assert serialised["is_acceptable_for_buy_sell"] is False
    # 1 of the 4 blockers fires → blocking_breaches list lists exactly it.
    assert serialised["blocking_breaches"] == ["quote_divergence_breach"]
    assert serialised["degradation_reason"] == "quote_divergence_breach"


@pytest.mark.asyncio
async def test_real_field_names_present_in_payload() -> None:
    """Codex cycle 1 P2 regression — serializer must surface the locked
    P1-2.B §1.5.1 field names, not the synthetic snapshot/news/mirofish
    outage names from the placeholder schema.
    """
    state = _make_state()
    probe = AsyncMock()
    probe.evaluate = AsyncMock(return_value=state)
    app = _build_app(probe=probe)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/data-quality", params={"stock_code": "600519"}
        )
    serialised = resp.json()["data"]["state"]
    locked_fields = {
        "quote_unavailable",
        "quote_staleness_breach",
        "quote_divergence_breach",
        "minimum_freshness_breach",
        "news_outage_breach",
        "mirofish_unavailable",
        "watchlist_snapshot_outage",
        "primary_quote_age_seconds",
        "backup_quote_age_seconds",
        "news_sources_alive_count",
        "is_acceptable_for_buy_sell",
        "degradation_reason",
        "blocking_breaches",
        "stock_code",
        "evaluated_at",
    }
    assert locked_fields.issubset(serialised.keys())


@pytest.mark.asyncio
async def test_probe_exception_degrades_to_unavailable() -> None:
    probe = AsyncMock()
    probe.evaluate = AsyncMock(side_effect=RuntimeError("boom"))
    app = _build_app(probe=probe)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/data-quality", params={"stock_code": "600519"}
        )
    assert resp.json()["data"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_probe_returns_none_yields_404() -> None:
    probe = AsyncMock()
    probe.evaluate = AsyncMock(return_value=None)
    app = _build_app(probe=probe)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/data-quality", params={"stock_code": "600519"}
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_stock_code_returns_422() -> None:
    app = _build_app(probe=AsyncMock())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/data-quality", params={"stock_code": "abc"}
        )
    assert resp.status_code == 422


def test_router_is_get_only() -> None:
    source = Path("backend/api/data_quality.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    write_verbs = {"post", "put", "patch", "delete"}
    seen: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                    if deco.func.attr in write_verbs:
                        seen.append(node.name)
    assert seen == []
