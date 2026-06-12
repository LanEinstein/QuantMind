"""AD-005 — POST /api/manual-trades (third write endpoint).

The owner records a user-discretionary trade they executed on their own
(took profit / cut a loss / added) that the system did NOT instruct. The
endpoint funnels a strict payload through :class:`ManualTradeService`, which
applies it to the single MockBroker mirror via the dedicated
``ManualTradeApplier`` and sends a display-only "已记录" Feishu ack.

Red lines (P1-5-amendment-2026-06-12 / P1-2.A-amendment-2026-06-12):

* This is the **third** (and only newly-added) allowed write endpoint —
  POST /api/execution-reports + POST /api/reconciliation-tickets/{id}/decide
  + POST /api/manual-trades. A fourth still requires an amendment; enforced
  by ``scripts/redline-check.sh``.
* Accepted **only in feishu_interactive mode** — pure simulation_auto is
  fully automated and has no human operator, so a manual record there is a
  category error → HTTP 403.
* The payload never fabricates an InstructionPlan: the ``UT-`` id lives in a
  regex space disjoint from ``QM-`` and the event never enters
  ``instruction_plans`` / the decision ledger / acceptance denominators.
* When the service is not wired the endpoint returns ``status="unavailable"``
  with HTTP 503 (never 500).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.models.manual_trade import (
    EXTERNAL_TRADE_ID_PATTERN,
    ExternalExecutionEvent,
    ManualTradeReason,
    ManualTradeSide,
)
from backend.services.manual_trade_service import (
    ManualTradeOutcome,
    ManualTradeService,
)
from backend.services.run_mode import feishu_interactive_enabled

log = logging.getLogger("backend.api.manual_trades")

router = APIRouter(tags=["manual_trades"])


class _SubmitBody(BaseModel):
    """Frontend submit payload for a single user-discretionary trade.

    The frontend mints ``external_trade_id`` once per form so a resubmit
    (double-click / retry) dedupes idempotently on the same id. Strict +
    ``extra='forbid'`` so any stray field fails validation rather than being
    silently dropped.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    external_trade_id: str = Field(pattern=EXTERNAL_TRADE_ID_PATTERN)
    code: str = Field(pattern=r"^\d{6}$")
    side: ManualTradeSide
    volume: int = Field(gt=0)
    price: float = Field(gt=0.0)
    executed_at: datetime
    reason: ManualTradeReason
    note: str = Field(default="", max_length=256)
    related_instruction_id: str | None = Field(
        default=None, pattern=r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL)-\d{3}$"
    )

    def as_event(self) -> ExternalExecutionEvent:
        return ExternalExecutionEvent(
            external_trade_id=self.external_trade_id,
            code=self.code,
            side=self.side,
            volume=self.volume,
            price=self.price,
            executed_at=self.executed_at,
            reason=self.reason,
            note=self.note,
            related_instruction_id=self.related_instruction_id,
        )


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _get_service(request: Request) -> ManualTradeService | None:
    svc = getattr(request.app.state, "manual_trade_service", None)
    if isinstance(svc, ManualTradeService):
        return svc
    return None


def _serialize_outcome(outcome: ManualTradeOutcome) -> dict[str, Any]:
    return {
        "external_trade_id": outcome.external_trade_id,
        "feishu_sent": outcome.feishu_sent,
        "apply_result": {
            "cash_delta": outcome.apply_result.cash_delta,
            "positions_delta": list(outcome.apply_result.positions_delta),
            "broker_event_sequence": outcome.apply_result.broker_event_sequence,
            "reason": outcome.apply_result.reason,
        },
    }


@router.post("/api/manual-trades", status_code=200)
async def submit_manual_trade(
    request: Request,
    body: _SubmitBody = Body(...),
) -> dict[str, Any]:
    """Record a user-discretionary trade (feishu_interactive only)."""
    if not feishu_interactive_enabled():
        # Pure simulation_auto is fully automated — there is no human
        # operator to record a manual trade. Reject as a category error.
        raise HTTPException(
            status_code=403,
            detail={
                "status": "forbidden",
                "data": None,
                "error": (
                    "manual-trades is only accepted in feishu_interactive "
                    "mode (pure simulation_auto is fully automated)."
                ),
            },
        )

    svc = _get_service(request)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "data": None,
                "error": "ManualTradeService is not wired yet.",
            },
        )

    event = body.as_event()
    try:
        outcome = await svc.record(event)
    except ValueError as exc:
        # Impossible fill: unaffordable BUY / over-sell beyond settled (T+1)
        # holdings — the broker raised BEFORE mutating, so the mirror is
        # unchanged. Surface as a 409 the operator UI can render inline.
        log.info("manual_trade_rejected error=%s", exc)
        raise HTTPException(
            status_code=409,
            detail={
                "status": "rejected",
                "data": None,
                "error": str(exc),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001 — surface as 500 with audit log
        log.warning("manual_trade_handler_failed error=%s", exc)
        raise HTTPException(
            status_code=500,
            detail={
                "status": "error",
                "data": None,
                "error": "manual-trade handler raised; see backend logs",
            },
        ) from exc

    return _ok(_serialize_outcome(outcome))


__all__ = ["router"]
