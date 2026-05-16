"""G-005 — POST /api/execution-reports backend tests.

Coverage:
- 503 when ExecutionReportOrchestrator is not wired
- 422 on malformed body (extra field, empty text, > 4096 chars)
- happy path: orchestrator returns success outcome → JSON envelope
- ambiguous path: outcome.ambiguous=True surfaces template_id
- handler exception → 500
- POST is the ONLY write verb on this router (AST scan)
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.execution_reports import router as execution_reports_router
from backend.broker.appliers import ApplyResult
from backend.integrations.feishu.parser import (
    ExecutionReportOrchestrator,
    ParseOutcome,
)
from backend.integrations.feishu.renderer import ClarificationTemplate


def _build_app(*, orchestrator: object | None = None) -> FastAPI:
    app = FastAPI()
    app.state.execution_report_orchestrator = orchestrator
    app.include_router(execution_reports_router)
    return app


def _fake_orchestrator(outcome: ParseOutcome) -> ExecutionReportOrchestrator:
    orch = ExecutionReportOrchestrator.__new__(ExecutionReportOrchestrator)
    orch.handle_frontend = AsyncMock(return_value=outcome)  # type: ignore[assignment]
    return orch


def _make_success_outcome() -> ParseOutcome:
    apply_result = ApplyResult(
        cash_delta=-1800.5 * 100,
        positions_delta=({"code": "600519", "volume": 100, "cost_price": 1800.5},),
        broker_event_sequence=42,
        reason="filled",
    )
    return ParseOutcome(
        success=True,
        ambiguous=False,
        instruction_id="QM-20260516-093001-600519-BUY-001",
        template_id=None,
        apply_result=apply_result,
        send_result=None,
    )


def _make_ambiguous_outcome() -> ParseOutcome:
    return ParseOutcome(
        success=False,
        ambiguous=True,
        instruction_id=None,
        template_id=ClarificationTemplate.NO_PATTERN_MATCH,
        apply_result=None,
        send_result=None,
    )


@pytest.mark.asyncio
async def test_submit_unavailable_when_orchestrator_missing() -> None:
    app = _build_app(orchestrator=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/execution-reports",
            json={"raw_text": "已执行 ..."},
        )
    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_submit_unavailable_when_wrong_type() -> None:
    """A misregistered orchestrator (e.g. AsyncMock instead of the real
    class) must degrade to 503 rather than 500."""
    app = _build_app(orchestrator=AsyncMock())  # not an ExecutionReportOrchestrator
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/execution-reports", json={"raw_text": "已执行 x"}
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_submit_happy_path() -> None:
    outcome = _make_success_outcome()
    app = _build_app(orchestrator=_fake_orchestrator(outcome))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/execution-reports",
            json={"raw_text": "已执行 ..."},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["success"] is True
    assert body["data"]["ambiguous"] is False
    assert body["data"]["instruction_id"] == outcome.instruction_id
    assert body["data"]["apply_result"]["cash_delta"] == outcome.apply_result.cash_delta
    assert body["data"]["apply_result"]["broker_event_sequence"] == 42
    assert body["data"]["apply_result"]["reason"] == "filled"


@pytest.mark.asyncio
async def test_submit_ambiguous_surfaces_template_id() -> None:
    outcome = _make_ambiguous_outcome()
    app = _build_app(orchestrator=_fake_orchestrator(outcome))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/execution-reports",
            json={"raw_text": "garbage"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["success"] is False
    assert body["data"]["ambiguous"] is True
    assert body["data"]["template_id"] == ClarificationTemplate.NO_PATTERN_MATCH.value
    assert body["data"]["apply_result"] is None


@pytest.mark.asyncio
async def test_submit_empty_text_rejected() -> None:
    app = _build_app(orchestrator=_fake_orchestrator(_make_success_outcome()))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/execution-reports",
            json={"raw_text": ""},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_extra_field_rejected() -> None:
    app = _build_app(orchestrator=_fake_orchestrator(_make_success_outcome()))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/execution-reports",
            json={"raw_text": "已执行", "side_channel": "leaked"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_too_long_text_rejected() -> None:
    app = _build_app(orchestrator=_fake_orchestrator(_make_success_outcome()))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/execution-reports",
            json={"raw_text": "x" * 4097},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_handler_exception_returns_500() -> None:
    orch = ExecutionReportOrchestrator.__new__(ExecutionReportOrchestrator)
    orch.handle_frontend = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[assignment]
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/execution-reports",
            json={"raw_text": "已执行 ..."},
        )
    assert resp.status_code == 500


def test_router_only_uses_post_for_execution_reports() -> None:
    """The router exposes exactly one write handler, and it is a POST.

    Combined with the global P1-5 redline-check (max 2 write endpoints),
    this guards against an accidental PUT/PATCH/DELETE here.
    """
    source = Path("backend/api/execution_reports.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    write_verbs = {"post", "put", "patch", "delete"}
    seen: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                    if deco.func.attr in write_verbs:
                        seen.append((node.name, deco.func.attr))
    # Exactly one write handler, and it must be a POST.
    assert seen == [("submit_execution_report", "post")], seen


@pytest.mark.asyncio
async def test_submit_received_at_pinned_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint must hand the orchestrator a UTC ``datetime`` so the
    parser's valid_until check stays timezone-correct on any host."""
    captured = {}

    async def fake_handle(raw_text: str, *, received_at: datetime) -> ParseOutcome:
        captured["raw_text"] = raw_text
        captured["received_at"] = received_at
        return _make_success_outcome()

    orch = ExecutionReportOrchestrator.__new__(ExecutionReportOrchestrator)
    orch.handle_frontend = fake_handle  # type: ignore[assignment]
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/execution-reports", json={"raw_text": "已执行"})

    assert captured["raw_text"] == "已执行"
    received = captured["received_at"]
    assert isinstance(received, datetime)
    assert received.tzinfo is not None
    assert received.utcoffset() == UTC.utcoffset(received)
