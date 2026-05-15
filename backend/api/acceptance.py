"""P1-5 §1.1 MVP page 7 — 验收报告 (Acceptance Reports).

Exposes the latest :class:`AcceptanceReport` so the front-end can render:

* The 45-trading-day rolling window (``window_start`` / ``window_end`` /
  ``trading_days_in_window``) per P0-6 §1.
* The 5-stability + 3-strategy 8-metric gate table with each row's
  ``threshold`` / ``value`` / ``direction`` / ``passed`` tag.
* The ``outcome`` enum (PASS / FAIL / PAUSED / INSUFFICIENT_DATA) plus
  a boolean ``can_switch_to_feishu_on`` flag that surfaces the locked
  P0-6 §2 redline 5 mode-switch gate.

Read-only — there is no write surface. ``can_switch_to_feishu_on`` is a
*display* boolean, not an action button; the actual switch goes through
:class:`backend.services.mode_router.ModeRouter` which itself calls
:meth:`AcceptanceService.can_switch_to_feishu_on`. Environment-variable
bypass is forbidden.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Request

from backend.services.acceptance_report import (
    AcceptanceReport,
    AcceptanceService,
)

log = structlog.get_logger(component="api_acceptance")

router = APIRouter(tags=["acceptance"])


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _serialize_report(report: AcceptanceReport) -> dict[str, Any]:
    return {
        "report_id": str(report.report_id),
        "computed_at": report.computed_at.astimezone(UTC).isoformat(),
        "trade_date": report.trade_date,
        "window_start": report.window_start,
        "window_end": report.window_end,
        "trading_days_in_window": report.trading_days_in_window,
        "outcome": report.outcome.value,
        "metrics": [
            {
                "name": m.name,
                "value": m.value,
                "threshold": m.threshold,
                "direction": m.direction,
                "passed": m.passed,
            }
            for m in report.metrics
        ],
        "notes": report.notes,
    }


def _get_service(request: Request) -> AcceptanceService | None:
    service = getattr(request.app.state, "acceptance_service", None)
    if service is None or not isinstance(service, AcceptanceService):
        return None
    return service


@router.get("/api/acceptance/latest")
async def get_latest_acceptance_report(request: Request) -> dict[str, Any]:
    """Return the latest acceptance report + the feishu-on gate flag.

    Graceful degradation:

    * ``service_status="unavailable"`` + ``report=None`` when the
      AcceptanceService is not wired yet (Phase F integration TODO).
    * ``report=None`` + ``service_status="ok"`` when the service is
      wired but no acceptance row has been written yet (cold start).
    * Probe-level Exception catches degrade the response to
      ``service_status="unavailable"`` instead of returning 500 so the
      Acceptance page never breaks the menu.

    The ``can_switch_to_feishu_on`` boolean mirrors
    :meth:`AcceptanceService.can_switch_to_feishu_on` so the page can
    show a green "可切换" badge **only** when the gate would actually
    pass. The endpoint never *takes* the action — display only (P0-6
    §2 redline 5 forbids env-var / CLI bypass anyway).
    """
    service = _get_service(request)
    if service is None:
        return _ok(
            {
                "report": None,
                "can_switch_to_feishu_on": False,
                "service_status": "unavailable",
            }
        )

    try:
        latest = await service.latest()
        can_switch = await service.can_switch_to_feishu_on()
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning("acceptance_latest_failed", error=str(exc))
        return _ok(
            {
                "report": None,
                "can_switch_to_feishu_on": False,
                "service_status": "unavailable",
            }
        )

    return _ok(
        {
            "report": _serialize_report(latest) if latest is not None else None,
            "can_switch_to_feishu_on": can_switch,
            "service_status": "ok",
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    )
