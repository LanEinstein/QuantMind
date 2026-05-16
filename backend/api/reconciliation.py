"""G-006 — reconciliation center endpoints.

* ``GET /api/reconciliation-tickets`` — list OPEN / EXPIRED tickets the
  operator must triage. Returns one row per ticket with the embedded
  :class:`DeviationReport` so the UI can render the diff inline.
* ``POST /api/reconciliation-tickets/{ticket_id}/decide`` — write
  endpoint matching the P1-5 §2 红线 1 allowlist. Forwards to
  :meth:`ReconciliationOrchestrator.decide_ticket` so the broker
  applier runs BEFORE the ticket is persisted as RESOLVED (fail-closed
  per P0-5 §1.5.3 / codex review session #14 cycle 1 P1).

Red lines:

* The list endpoint is **read-only**; the decide endpoint is the
  second of the two allowed write surfaces (the other is
  ``POST /api/execution-reports``).
* When the orchestrator is not wired (e.g. simulation_auto with the
  F-005 dependencies still unattached) both endpoints surface
  ``status="unavailable"`` rather than 500.
* The endpoint never composes Feishu text — the orchestrator owns the
  result message via :class:`MessageRenderer.render_reconciliation_result`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.broker.appliers import ApplyResult
from backend.integrations.feishu.reconciliation import (
    DecisionResult,
    ReconciliationOrchestrator,
)
from backend.models.reconciliation import (
    TICKET_ID_PATTERN,
    DeviationReport,
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
)

log = logging.getLogger("backend.api.reconciliation")

router = APIRouter(tags=["reconciliation"])


_DECIDE_VALID_STATUSES: frozenset[ReconciliationTicketStatus] = frozenset(
    {
        ReconciliationTicketStatus.RESOLVED_USER_AS_TRUTH,
        ReconciliationTicketStatus.RESOLVED_SYSTEM_AS_TRUTH,
        ReconciliationTicketStatus.RESOLVED_AMENDED,
    }
)


# ---------------------------------------------------------------------------
# Body schema
# ---------------------------------------------------------------------------


class _ReportedPositionDTO(BaseModel):
    """JSON-friendly mirror of :class:`ReportedPosition` for the API edge.

    Domain ``ReportedPosition`` is ``strict=True`` so it refuses the
    list→tuple coercion JSON requires. This DTO accepts the JSON shape
    and we project it into the strict domain model before forwarding.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    volume: int = Field(ge=0)
    cost_price: float = Field(ge=0.0)


class _AmendedSnapshotDTO(BaseModel):
    """JSON-friendly mirror of :class:`MockBrokerSnapshot`."""

    model_config = ConfigDict(extra="forbid")

    cash: float = Field(ge=0.0)
    snapshot_at: datetime
    positions: list[_ReportedPositionDTO] = Field(default_factory=list)


class _DecideBody(BaseModel):
    """POST /api/reconciliation-tickets/{ticket_id}/decide body.

    Not strict: JSON arrives as string for the ``resolution`` enum +
    embedded snapshots, and Pydantic ``strict=True`` would reject the
    standard str→enum coercion that every FastAPI app relies on. The
    ``extra='forbid'`` clause still locks the schema to the four
    documented fields so a side-channel cannot smuggle anything in.
    """

    model_config = ConfigDict(extra="forbid")

    resolution: ReconciliationTicketStatus
    amended_snapshot: _AmendedSnapshotDTO | None = None
    resolution_message_id: str | None = Field(default=None, max_length=128)
    actor_detail: str | None = Field(default=None, max_length=128)

    def to_domain_snapshot(self) -> MockBrokerSnapshot | None:
        if self.amended_snapshot is None:
            return None
        from backend.models.reconciliation import ReportedPosition

        return MockBrokerSnapshot(
            cash=self.amended_snapshot.cash,
            snapshot_at=self.amended_snapshot.snapshot_at,
            positions=tuple(
                ReportedPosition(
                    code=p.code, volume=p.volume, cost_price=p.cost_price
                )
                for p in self.amended_snapshot.positions
            ),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _get_orchestrator(request: Request) -> ReconciliationOrchestrator | None:
    orch = getattr(request.app.state, "reconciliation_orchestrator", None)
    if orch is None or not isinstance(orch, ReconciliationOrchestrator):
        return None
    return orch


def _serialize_deviation(dr: DeviationReport) -> dict[str, Any]:
    return {
        "ticket_id": dr.ticket_id,
        "overall_passed": dr.overall_passed,
        "deviations": [
            {
                "field": d.field,
                "expected": d.expected,
                "actual": d.actual,
                "abs_diff": d.abs_diff,
                "threshold": d.threshold,
                "passed": d.passed,
            }
            for d in dr.deviations
        ],
    }


def _serialize_ticket(ticket: ReconciliationTicket) -> dict[str, Any]:
    return {
        "ticket_id": ticket.ticket_id,
        "trade_date": ticket.trade_date,
        "created_at": ticket.created_at.astimezone(UTC).isoformat(),
        "status": ticket.status.value,
        "resolved_at": (
            ticket.resolved_at.astimezone(UTC).isoformat()
            if ticket.resolved_at is not None
            else None
        ),
        "resolution_message_id": ticket.resolution_message_id,
        "expected_snapshot_id": ticket.expected_snapshot_id,
        "actual_reconciliation_id": ticket.actual_reconciliation_id,
        "deviation_report": _serialize_deviation(ticket.deviation_report),
        "amended_snapshot": (
            {
                "cash": ticket.amended_snapshot.cash,
                "snapshot_at": ticket.amended_snapshot.snapshot_at.astimezone(
                    UTC
                ).isoformat(),
                "positions": [
                    {
                        "code": p.code,
                        "volume": p.volume,
                        "cost_price": p.cost_price,
                    }
                    for p in ticket.amended_snapshot.positions
                ],
            }
            if ticket.amended_snapshot is not None
            else None
        ),
    }


def _serialize_apply_result(apply_result: ApplyResult) -> dict[str, Any]:
    return {
        "cash_delta": apply_result.cash_delta,
        "positions_delta": list(apply_result.positions_delta),
        "broker_event_sequence": apply_result.broker_event_sequence,
        "reason": apply_result.reason,
    }


def _serialize_decision(decision: DecisionResult) -> dict[str, Any]:
    return {
        "ticket_id": decision.ticket_id,
        "status": decision.status.value,
        "apply_result": _serialize_apply_result(decision.apply_result),
    }


# ---------------------------------------------------------------------------
# Read surface
# ---------------------------------------------------------------------------


@router.get("/api/reconciliation-tickets")
async def list_reconciliation_tickets(
    request: Request,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """List OPEN + EXPIRED tickets the operator must triage.

    When ``trade_date`` is omitted the endpoint uses the orchestrator's
    own definition of "today" (the live ticket repository is the source
    of truth, not the API layer). Returns ``status="unavailable"`` when
    the orchestrator is not wired so the page surfaces a clear banner.
    """
    orch = _get_orchestrator(request)
    if orch is None:
        return _ok({"status": "unavailable", "tickets": [], "trade_date": trade_date})

    target_date = trade_date or datetime.now(tz=UTC).strftime("%Y-%m-%d")
    try:
        repo = orch._tickets  # type: ignore[attr-defined]
        tickets = await repo.list_open_for_date(target_date)
    except Exception as exc:  # noqa: BLE001 — fail-soft
        log.warning("reconciliation_list_failed error=%s", exc)
        return _ok({"status": "unavailable", "tickets": [], "trade_date": target_date})

    return _ok(
        {
            "status": "ok",
            "trade_date": target_date,
            "tickets": [_serialize_ticket(t) for t in tickets],
            "count": len(tickets),
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    )


# ---------------------------------------------------------------------------
# Write surface (P1-5 §2 红线 1 — 2/2 allowed write endpoints)
# ---------------------------------------------------------------------------


@router.post("/api/reconciliation-tickets/{ticket_id}/decide", status_code=200)
async def decide_reconciliation_ticket(
    request: Request,
    ticket_id: str = Path(..., pattern=TICKET_ID_PATTERN),
    body: _DecideBody = Body(...),
) -> dict[str, Any]:
    """Apply a ticket decision via :meth:`ReconciliationOrchestrator.decide_ticket`.

    The orchestrator runs the applier BEFORE persisting the resolved
    ticket so a broker write failure leaves the freeze intact (P0-5
    §1.5.3). Validation surfaces map cleanly to HTTP:

    * 503 → orchestrator unwired
    * 400 → resolution value outside the 3 RESOLVED_* options
    * 404 → ticket_id unknown
    * 409 → ticket already in terminal state OR amended_snapshot missing
              for RESOLVED_AMENDED
    """
    orch = _get_orchestrator(request)
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "data": None,
                "error": (
                    "ReconciliationOrchestrator is not wired yet "
                    "(Phase I-001 integration)."
                ),
            },
        )

    if body.resolution not in _DECIDE_VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "data": None,
                "error": (
                    f"resolution must be one of "
                    f"{sorted(s.value for s in _DECIDE_VALID_STATUSES)}; "
                    f"got {body.resolution.value}"
                ),
            },
        )
    if (
        body.resolution is ReconciliationTicketStatus.RESOLVED_AMENDED
        and body.amended_snapshot is None
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "status": "error",
                "data": None,
                "error": "RESOLVED_AMENDED requires amended_snapshot",
            },
        )
    if (
        body.resolution is not ReconciliationTicketStatus.RESOLVED_AMENDED
        and body.amended_snapshot is not None
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "data": None,
                "error": (
                    f"amended_snapshot only valid for "
                    f"RESOLVED_AMENDED; resolution={body.resolution.value}"
                ),
            },
        )

    try:
        decision = await orch.decide_ticket(
            ticket_id,
            resolution=body.resolution,
            amended_snapshot=body.to_domain_snapshot(),
            resolution_message_id=body.resolution_message_id,
            actor_detail=body.actor_detail,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "data": None,
                "error": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "error",
                "data": None,
                "error": str(exc),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001 — broker / mongo unexpected failures
        log.warning("reconciliation_decide_failed error=%s", exc)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "data": None,
                "error": "decide_ticket raised; see backend logs",
            },
        ) from exc

    return _ok(_serialize_decision(decision))


__all__ = ["router"]
