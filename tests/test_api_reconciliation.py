"""G-006 — backend/api/reconciliation.py tests.

Coverage:
- GET list endpoint unwired → status='unavailable'
- GET list happy path with tickets
- GET list trade_date filter passes through to orchestrator repo
- POST decide unwired → 503
- POST decide happy path returns DecisionResult JSON
- POST decide bad resolution → 400
- POST decide RESOLVED_AMENDED without amended_snapshot → 409
- POST decide non-AMEND with amended_snapshot → 400 (consistency)
- POST decide unknown ticket → 404
- POST decide value error (terminal ticket) → 409
- POST decide ticket_id pattern → 422
- AST scan: list is GET-only, decide is POST-only
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.reconciliation import router as reconciliation_router
from backend.broker.appliers import ApplyResult
from backend.integrations.feishu.reconciliation import (
    DecisionResult,
    ReconciliationOrchestrator,
)
from backend.models.reconciliation import (
    DeviationReport,
    FieldDeviation,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)


def _build_app(*, orchestrator: object | None = None) -> FastAPI:
    app = FastAPI()
    app.state.reconciliation_orchestrator = orchestrator
    app.include_router(reconciliation_router)
    return app


def _make_ticket(
    ticket_id: str = "RECON-20260516-001",
    status: ReconciliationTicketStatus = ReconciliationTicketStatus.OPEN,
) -> ReconciliationTicket:
    deviation_report = DeviationReport(
        ticket_id=ticket_id,
        overall_passed=False,
        deviations=(
            FieldDeviation(
                field="cash",
                expected="100000.00",
                actual="99999.00",
                abs_diff=1.0,
                threshold=1.0,
                passed=True,
            ),
            FieldDeviation(
                field="position[600519].volume",
                expected="100",
                actual="200",
                abs_diff=100.0,
                threshold=0.0,
                passed=False,
            ),
        ),
    )
    return ReconciliationTicket(
        ticket_id=ticket_id,
        trade_date="2026-05-16",
        created_at=datetime(2026, 5, 16, 16, 0, tzinfo=UTC),
        deviation_report=deviation_report,
        expected_snapshot_id="snap-1",
        actual_reconciliation_id="recon-1",
        status=status,
    )


class _FakeRepo:
    def __init__(self, rows: tuple[ReconciliationTicket, ...]) -> None:
        self._rows = rows
        self.last_trade_date: str | None = None
        self.fail = False

    async def get(self, ticket_id: str) -> ReconciliationTicket | None:
        for row in self._rows:
            if row.ticket_id == ticket_id:
                return row
        return None

    async def save(self, ticket: ReconciliationTicket) -> None:
        pass

    async def list_open_for_date(
        self, trade_date: str
    ) -> tuple[ReconciliationTicket, ...]:
        if self.fail:
            raise RuntimeError("mongo down")
        self.last_trade_date = trade_date
        return tuple(t for t in self._rows if t.trade_date == trade_date)


def _fake_orchestrator(
    tickets: tuple[ReconciliationTicket, ...] = (),
    *,
    decide_result: DecisionResult | None = None,
    decide_side_effect: Exception | None = None,
) -> ReconciliationOrchestrator:
    orch = ReconciliationOrchestrator.__new__(ReconciliationOrchestrator)
    orch._tickets = _FakeRepo(tickets)  # type: ignore[attr-defined]

    async def fake_decide(
        ticket_id: str,
        *,
        resolution: ReconciliationTicketStatus,
        amended_snapshot: Any = None,
        resolution_message_id: str | None = None,
        actor_detail: str | None = None,
    ) -> DecisionResult:
        if decide_side_effect:
            raise decide_side_effect
        if decide_result is not None:
            return decide_result
        return DecisionResult(
            ticket_id=ticket_id,
            status=resolution,
            apply_result=ApplyResult(
                cash_delta=0.0,
                positions_delta=(),
                broker_event_sequence=99,
                reason="reset",
            ),
            send_result=None,
        )

    orch.decide_ticket = fake_decide  # type: ignore[assignment]
    return orch


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_unavailable_when_orchestrator_missing() -> None:
    app = _build_app(orchestrator=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/reconciliation-tickets")
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert body["data"]["status"] == "unavailable"
    assert body["data"]["tickets"] == []


@pytest.mark.asyncio
async def test_list_misregistered_orchestrator_unavailable() -> None:
    app = _build_app(orchestrator=AsyncMock())  # wrong type
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/reconciliation-tickets")
    assert resp.json()["data"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_list_happy_path() -> None:
    ticket = _make_ticket()
    orch = _fake_orchestrator((ticket,))
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/reconciliation-tickets",
            params={"trade_date": "2026-05-16"},
        )
    body = resp.json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["count"] == 1
    row = body["data"]["tickets"][0]
    assert row["ticket_id"] == ticket.ticket_id
    assert row["status"] == "OPEN"
    assert len(row["deviation_report"]["deviations"]) == 2
    assert orch._tickets.last_trade_date == "2026-05-16"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_list_repo_failure_degrades() -> None:
    orch = _fake_orchestrator((_make_ticket(),))
    orch._tickets.fail = True  # type: ignore[attr-defined]
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/reconciliation-tickets")
    assert resp.json()["data"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# POST decide
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decide_unavailable_when_orchestrator_missing() -> None:
    app = _build_app(orchestrator=None)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={"resolution": "RESOLVED_SYSTEM_AS_TRUTH"},
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_decide_happy_path_system_as_truth() -> None:
    orch = _fake_orchestrator((_make_ticket(),))
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={"resolution": "RESOLVED_SYSTEM_AS_TRUTH"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["ticket_id"] == "RECON-20260516-001"
    assert body["data"]["status"] == "RESOLVED_SYSTEM_AS_TRUTH"
    assert body["data"]["apply_result"]["broker_event_sequence"] == 99


@pytest.mark.asyncio
async def test_decide_amend_requires_amended_snapshot() -> None:
    orch = _fake_orchestrator((_make_ticket(),))
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={"resolution": "RESOLVED_AMENDED"},
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_decide_amend_with_snapshot_happy_path() -> None:
    orch = _fake_orchestrator((_make_ticket(),))
    app = _build_app(orchestrator=orch)
    snapshot = {
        "cash": 100000.0,
        "snapshot_at": "2026-05-16T16:00:00+00:00",
        "positions": [
            {"code": "600519", "volume": 100, "cost_price": 1800.5},
        ],
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={
                "resolution": "RESOLVED_AMENDED",
                "amended_snapshot": snapshot,
            },
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "RESOLVED_AMENDED"


@pytest.mark.asyncio
async def test_decide_non_amend_with_snapshot_rejected() -> None:
    orch = _fake_orchestrator((_make_ticket(),))
    app = _build_app(orchestrator=orch)
    snapshot = {
        "cash": 100.0,
        "snapshot_at": "2026-05-16T16:00:00+00:00",
        "positions": [],
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={
                "resolution": "RESOLVED_USER_AS_TRUTH",
                "amended_snapshot": snapshot,
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_decide_unknown_ticket_returns_404() -> None:
    orch = _fake_orchestrator((), decide_side_effect=KeyError("unknown ticket"))
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-999/decide",
            json={"resolution": "RESOLVED_SYSTEM_AS_TRUTH"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_decide_terminal_state_returns_409() -> None:
    orch = _fake_orchestrator(
        (_make_ticket(),),
        decide_side_effect=ValueError("ticket already terminal"),
    )
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={"resolution": "RESOLVED_SYSTEM_AS_TRUTH"},
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_decide_bad_resolution_value_returns_400() -> None:
    """OPEN parses as a valid enum value, then the handler rejects it.

    OPEN / EXPIRED are valid :class:`ReconciliationTicketStatus` members
    but not allowed as a decision target. The handler maps the rejection
    to HTTP 400 with an explanatory body (not 422 — Pydantic accepted
    the enum value).
    """
    orch = _fake_orchestrator((_make_ticket(),))
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={"resolution": "OPEN"},
        )
    assert resp.status_code == 400
    assert (
        "resolution must be one of"
        in resp.json()["detail"]["error"]
    )


@pytest.mark.asyncio
async def test_decide_invalid_enum_value_returns_422() -> None:
    """A truly invalid enum value (not in the StrEnum) → 422 from Pydantic."""
    orch = _fake_orchestrator((_make_ticket(),))
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={"resolution": "RESOLVED_BY_FIAT"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_decide_bad_ticket_id_pattern_returns_422() -> None:
    orch = _fake_orchestrator((_make_ticket(),))
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/not-a-valid-id/decide",
            json={"resolution": "RESOLVED_SYSTEM_AS_TRUTH"},
        )
    # Path regex mismatch → 422
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_decide_unexpected_exception_returns_500() -> None:
    orch = _fake_orchestrator(
        (_make_ticket(),),
        decide_side_effect=RuntimeError("broker explode"),
    )
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={"resolution": "RESOLVED_SYSTEM_AS_TRUTH"},
        )
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_decide_extra_field_rejected() -> None:
    orch = _fake_orchestrator((_make_ticket(),))
    app = _build_app(orchestrator=orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/reconciliation-tickets/RECON-20260516-001/decide",
            json={
                "resolution": "RESOLVED_SYSTEM_AS_TRUTH",
                "side_channel": "leak",
            },
        )
    assert resp.status_code == 422


def test_router_write_surface_is_only_decide_post() -> None:
    """Exactly one POST handler (the decide endpoint), no other writes."""
    source = Path("backend/api/reconciliation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    write_verbs = {"post", "put", "patch", "delete"}
    seen: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                    if deco.func.attr in write_verbs:
                        seen.append((node.name, deco.func.attr))
    assert seen == [("decide_reconciliation_ticket", "post")], seen
