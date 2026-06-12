"""Z-005 — backend/api/dual_line_status.py tests (dual-line run-state surface)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.dual_line_status import router as dual_line_router


def _build_app(state: dict[str, Any]) -> FastAPI:
    app = FastAPI()
    for key, value in state.items():
        setattr(app.state, key, value)
    app.include_router(dual_line_router)
    return app


async def _get(app: FastAPI) -> dict[str, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/dual-line-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    return body["data"]


@pytest.mark.asyncio
async def test_all_unwired() -> None:
    data = await _get(_build_app({}))
    assert data["line1"]["wired"] is False
    assert data["line2"]["daily_wired"] is False
    assert data["line2"]["intraday_wired"] is False
    assert data["rotation"]["wired"] is False
    assert data["rotation"]["max_total_positions"] is None
    assert data["scheduler_wired"] is False


@pytest.mark.asyncio
async def test_all_wired_reports_caps() -> None:
    class _Rotation:
        max_total_positions = 5

    data = await _get(
        _build_app(
            {
                "line1_runner": object(),
                "line2_daily_runner": object(),
                "line2_intraday_runner": object(),
                "rotation_runner": _Rotation(),
                "broker_scheduler": object(),
            }
        )
    )
    assert data["line1"]["wired"] is True
    assert isinstance(data["line1"]["max_debates_per_day"], int)
    assert data["line2"]["daily_wired"] is True
    assert data["line2"]["intraday_wired"] is True
    assert data["rotation"]["wired"] is True
    assert data["rotation"]["max_total_positions"] == 5
    assert data["scheduler_wired"] is True


def test_router_is_get_only() -> None:
    source = Path("backend/api/dual_line_status.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    write_verbs = {"post", "put", "patch", "delete"}
    seen: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(
                    deco.func, ast.Attribute
                ):
                    if deco.func.attr in write_verbs:
                        seen.append(node.name)
    assert seen == []


def test_module_imports_no_trading_stack() -> None:
    source = Path("backend/api/dual_line_status.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"llm", "agents", "risk", "broker", "data", "mirofish"}
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in forbidden:
                bad.append(node.module)
    assert bad == []
