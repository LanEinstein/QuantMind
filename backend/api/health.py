"""FastAPI routes for detailed health monitoring."""

from __future__ import annotations

import time
from typing import Any

import structlog
from fastapi import APIRouter, Request

log = structlog.get_logger(component="api_health")

router = APIRouter(tags=["health"])


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


async def _check_mongodb(request: Request) -> str:
    """Check MongoDB connectivity via admin ping."""
    try:
        mongo_client = getattr(request.app.state, "mongo_client", None)
        if mongo_client is None:
            return "unavailable"
        await mongo_client.admin.command("ping")
        return "ok"
    except Exception:
        return "error"


async def _check_redis(request: Request) -> str:
    """Check Redis connectivity via ping."""
    try:
        redis_client = getattr(request.app.state, "redis", None)
        if redis_client is None:
            return "unavailable"
        await redis_client.ping()
        return "ok"
    except Exception:
        return "error"


def _check_scheduler(request: Request, attr: str) -> str:
    """Check if a scheduler is running."""
    svc = getattr(request.app.state, attr, None)
    if svc is None:
        return "unavailable"
    scheduler = getattr(svc, "_scheduler", None)
    if scheduler is None:
        return "stopped"
    return "running" if getattr(scheduler, "running", False) else "stopped"


@router.get("/api/health/detailed")
async def detailed_health(request: Request) -> dict[str, Any]:
    """Return detailed system health status.

    Checks all components and returns per-component status.
    Overall status:
    - "ok": all components operational
    - "degraded": some non-critical components down
    - "critical": MongoDB or Redis down
    """
    mongodb_status = await _check_mongodb(request)
    redis_status = await _check_redis(request)
    llm_status = "ok" if hasattr(request.app.state, "llm_router") else "unavailable"
    data_sched = _check_scheduler(request, "scheduler")
    analysis_sched = _check_scheduler(request, "analysis_scheduler")

    start_time = getattr(request.app.state, "app_start_time", time.time())
    uptime = round(time.time() - start_time, 1)

    components = {
        "mongodb": mongodb_status,
        "redis": redis_status,
        "llm_router": llm_status,
        "data_scheduler": data_sched,
        "analysis_scheduler": analysis_sched,
    }

    # Determine overall status
    critical_components = {mongodb_status, redis_status}
    all_statuses = set(components.values())

    if critical_components & {"error"}:
        overall = "critical"
    elif all_statuses <= {"ok", "running"}:
        overall = "ok"
    else:
        overall = "degraded"

    return _ok({
        "status": overall,
        "components": components,
        "uptime_seconds": uptime,
    })
