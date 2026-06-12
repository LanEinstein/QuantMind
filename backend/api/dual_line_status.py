"""Z-005 — read-only dual-line daily run-state surface.

Surfaces whether the two daily lines are wired + their bounded caps so the
``Dashboard`` can render a parallel-run-state panel (P1-5-amendment-2026-06-01
§1.2 编排). Line-1 = LLM multi-candidate selection; Line-2 = deterministic
zero-LLM monitoring + the ≤5-slot rotation. Polling-based — it adds NO new WS
class (P1-5-amendment-2026-06-01 §1.3).

Red lines:

* GET only — the global write-endpoint allowlist forbids any non-GET here.
* No ``backend.{llm,agents,risk,broker,data}`` imports — only the cost-guard
  config reader for the Line-1 fan-out cap (a pure env/config read). Runner
  handles are read from ``app.state`` by attribute, never imported.
* Liveness is derived from ``app.state`` presence; an unwired line reports
  ``wired=False`` and the endpoint never 500s.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

log = logging.getLogger("backend.api.dual_line_status")

router = APIRouter(tags=["dual_line_status"])

_NOTE = (
    "双线每日并行:Line-1 选股(LLM 多候选辩论)+ Line-2 监控(确定性零 LLM)+ "
    "≤5 槽轮动;轮询刷新,不扩 WS 协议。详细产出见 InstructionPlan 池 / 组合页。"
)


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _wired(request: Request, name: str) -> bool:
    return getattr(request.app.state, name, None) is not None


def _max_debates_per_day() -> int | None:
    try:
        from backend.services.cost_guard import get_max_debates_per_day

        return get_max_debates_per_day()
    except Exception:  # noqa: BLE001 — display-only cap, never 500 the panel
        log.warning("max_debates_per_day_read_failed")
        return None


@router.get("/api/dual-line-status")
async def get_dual_line_status(request: Request) -> dict[str, Any]:
    """Return per-line liveness + bounded caps (display-only, polling)."""
    rotation = getattr(request.app.state, "rotation_runner", None)
    max_positions = getattr(rotation, "max_total_positions", None)

    return _ok(
        {
            "line1": {
                "label": "Line-1 选股(LLM 多候选辩论)",
                "wired": _wired(request, "line1_runner"),
                "max_debates_per_day": _max_debates_per_day(),
            },
            "line2": {
                "label": "Line-2 监控(确定性零 LLM)",
                "daily_wired": _wired(request, "line2_daily_runner"),
                "intraday_wired": _wired(request, "line2_intraday_runner"),
            },
            "rotation": {
                "label": "≤5 槽轮动(确定性)",
                "wired": rotation is not None,
                "max_total_positions": max_positions,
            },
            "scheduler_wired": _wired(request, "broker_scheduler"),
            "note": _NOTE,
        }
    )


__all__ = ["router"]
