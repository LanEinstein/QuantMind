"""G-005 — POST /api/execution-reports (frontend backup channel).

The frontend ExecutionReportEntry page submits a raw report string here;
the orchestrator funnels it through the same parser + applier as the
Feishu main path (F-004). Clarifications are surfaced inline in the
POST response — the frontend does **not** ride the Feishu reply leg
because it already renders the JS-regex preview before submit.

Red lines (P1-5 §2 红线 1 / CLAUDE.md §2.7):

* This is one of the **two** allowed write endpoints in the entire
  backend surface (POST /api/execution-reports +
  POST /api/reconciliation-tickets/{id}/decide). Adding any other
  write handler is a red-line violation enforced by
  ``scripts/redline-check.sh``.
* When the orchestrator is not wired (e.g. simulation_auto with no
  F-004 dependencies attached to ``app.state``) the endpoint surfaces
  ``status="unavailable"`` with HTTP 503 so the operator UI shows a
  clear banner — never 500.
* The orchestrator writes the audit + applier side-effects itself; this
  router only translates HTTP ↔ orchestrator and never composes
  buy/sell/clarification text.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.integrations.feishu.parser import (
    ExecutionReportOrchestrator,
    ParseOutcome,
)

log = logging.getLogger("backend.api.execution_reports")

router = APIRouter(tags=["execution_reports"])


class _SubmitBody(BaseModel):
    """Frontend submit payload — only the raw text is allowed.

    ``raw_text`` is the user-entered report copied verbatim into the
    backup form; it must match one of the locked
    :mod:`backend.execution.regex_patterns` shapes after the JS mirror
    preview gives a green light.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    raw_text: str = Field(min_length=1, max_length=4096)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _get_orchestrator(request: Request) -> ExecutionReportOrchestrator | None:
    orch = getattr(
        request.app.state, "execution_report_orchestrator", None
    )
    if orch is None or not isinstance(orch, ExecutionReportOrchestrator):
        return None
    return orch


def _serialize_outcome(outcome: ParseOutcome) -> dict[str, Any]:
    """Project a :class:`ParseOutcome` into the JSON wire shape."""
    return {
        "success": outcome.success,
        "ambiguous": outcome.ambiguous,
        "instruction_id": outcome.instruction_id,
        "template_id": (
            outcome.template_id.value
            if outcome.template_id is not None
            else None
        ),
        "apply_result": (
            {
                "cash_delta": outcome.apply_result.cash_delta,
                "positions_delta": list(outcome.apply_result.positions_delta),
                "broker_event_sequence": outcome.apply_result.broker_event_sequence,
                "reason": outcome.apply_result.reason,
            }
            if outcome.apply_result is not None
            else None
        ),
    }


@router.post("/api/execution-reports", status_code=200)
async def submit_execution_report(
    request: Request,
    body: _SubmitBody = Body(...),
) -> dict[str, Any]:
    """Backup-channel submission for a single user-typed execution report.

    The endpoint never composes Feishu text. The orchestrator handles
    the applier side-effects (success path) or returns an ambiguous
    template id (clarification path); both reach the operator as JSON
    so the front-end can render the same clarification template inline.
    """
    orch = _get_orchestrator(request)
    if orch is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "data": None,
                "error": (
                    "ExecutionReportOrchestrator is not wired yet "
                    "(Phase I-001 integration)."
                ),
            },
        )

    try:
        outcome = await orch.handle_frontend(
            body.raw_text, received_at=datetime.now(tz=UTC)
        )
    except Exception as exc:  # noqa: BLE001 — surface as 500 with audit log
        log.warning("execution_report_handler_failed error=%s", exc)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "data": None,
                "error": "orchestrator handler raised; see backend logs",
            },
        ) from exc

    return _ok(_serialize_outcome(outcome))


__all__ = ["router"]
