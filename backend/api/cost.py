"""H-003 — GET /api/cost surface (P1-7 §1.7 / P1-5 §1.1).

Read-only window over the P1-7 budget state. Phase B-finale cost
breakdown page (G-008) hits this same router so 5min polling stays on
the GET-only spine the redline scanner enforces.

Red lines (CLAUDE.md §2.10 / P1-5 §2 红线 1+2):

* GET only — no POST / PUT / PATCH / DELETE handler may appear here.
* When Redis is not wired the endpoints degrade to ``status="unavailable"``
  rather than 500ing, so the front-end shows a clear "no data" panel.
* Plaintext credentials never appear in the response (the underlying
  cost probe only sees Redis usage keys).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Query, Request

from backend.services.cost_guard import (
    DailyBudgetState,
    FullBudgetState,
    KimiBudgetState,
    MonthlyBudgetState,
    get_full_budget_state,
)
from backend.services.cost_probe import scan_costs
from backend.services.soft_degrade_manager import SoftDegradeManager

log = structlog.get_logger(component="api_cost")

router = APIRouter(tags=["cost"])


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _unavailable(reason: str) -> dict[str, Any]:
    return _ok({"status": "unavailable", "reason": reason})


def _daily_dict(state: DailyBudgetState) -> dict[str, Any]:
    return asdict(state)


def _monthly_dict(state: MonthlyBudgetState) -> dict[str, Any]:
    return asdict(state)


def _kimi_dict(state: KimiBudgetState) -> dict[str, Any]:
    return asdict(state)


def _full_dict(state: FullBudgetState) -> dict[str, Any]:
    return {
        "daily": _daily_dict(state.daily),
        "monthly": _monthly_dict(state.monthly),
        "kimi": _kimi_dict(state.kimi),
    }


@router.get("/api/cost/budget")
async def get_budget(request: Request) -> dict[str, Any]:
    """Return daily + monthly + Kimi budget state."""
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        return _unavailable("redis_not_wired")
    try:
        state = await get_full_budget_state(redis_client)
    except Exception as exc:
        log.warning("cost_budget_failed", error=str(exc))
        return _unavailable("budget_probe_failed")
    return _ok({
        "status": "ok",
        **_full_dict(state),
        "timestamp": datetime.now(tz=UTC).isoformat(),
    })


@router.get("/api/cost/breakdown")
async def get_breakdown(
    request: Request,
    days: int = Query(default=7, ge=1, le=31),
) -> dict[str, Any]:
    """Return spend breakdown for the trailing ``days`` (default 7).

    Powers the P1-5 §1.1 (Phase B-finale) cost dashboard with 5min
    polling. Includes:

    * ``daily_totals`` — date → CNY map (most-recent first)
    * ``by_provider`` — provider → CNY map
    * ``by_provider_daily`` — provider → date → CNY map (for stacked area)
    * ``total_cost_rmb`` — sum across the window
    """
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        return _unavailable("redis_not_wired")
    try:
        summary = await scan_costs(redis_client, days=days)
    except Exception as exc:
        log.warning("cost_breakdown_failed", error=str(exc))
        return _unavailable("cost_probe_failed")

    daily_sorted = dict(
        sorted(summary.daily_totals.items(), key=lambda kv: kv[0], reverse=True)
    )
    return _ok({
        "status": "ok",
        "days": summary.days,
        "total_cost_rmb": summary.total_cost_rmb,
        "daily_totals": daily_sorted,
        "by_provider": summary.by_provider,
        "by_provider_daily": summary.by_provider_daily,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    })


@router.get("/api/cost/soft-degrade")
async def get_soft_degrade(request: Request) -> dict[str, Any]:
    """Return the current soft-degrade flags + monthly milestone state."""
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        return _unavailable("redis_not_wired")
    try:
        budget = await get_full_budget_state(redis_client)
        manager = SoftDegradeManager(redis_client)
        kimi_blocked = await manager.is_kimi_escalation_blocked()
    except Exception as exc:
        log.warning("cost_soft_degrade_failed", error=str(exc))
        return _unavailable("soft_degrade_probe_failed")

    return _ok({
        "status": "ok",
        "kimi_escalation_blocked": kimi_blocked,
        "daily_status": budget.daily.status,
        "monthly_status": budget.monthly.status,
        "kimi_status": budget.kimi.status,
        "monthly_threshold_reached": budget.monthly.threshold_reached,
        "timestamp": datetime.now(tz=UTC).isoformat(),
    })


__all__ = ["router"]
