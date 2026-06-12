"""Z-003 — read-only position-thesis tracking surface.

Surfaces the persisted :class:`PositionThesis` read model (W-001:
3–5 LLM advisory pillars + deterministic quantitative invalidation thresholds +
time-stop / catalyst window) so the ``Portfolio`` page can render a
long-hold-vs-take-profit panel (P1-5-amendment-2026-06-01 §1.2 direction②).

Red lines:

* GET only — the global write-endpoint allowlist forbids any non-GET here.
* No ``backend.{llm,agents,risk,broker,data}`` imports — only the file-backed
  ``backend.position_thesis`` store (which is itself 0-LLM). The endpoint never
  fetches market data, so it cannot derive a live health verdict; it serves the
  durable thesis structure + its deterministic invalidation thresholds, which
  the panel renders display-only.
* When the store is not wired (system stopped / no theses yet) the endpoint
  returns ``available=False`` and never 500s.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

log = logging.getLogger("backend.api.position_theses")

router = APIRouter(tags=["position_theses"])


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _get_store(request: Request) -> Any | None:
    store = getattr(request.app.state, "position_thesis_store", None)
    if store is None or not hasattr(store, "open_theses"):
        return None
    return store


def _serialize_condition(cond: Any) -> dict[str, Any]:
    return {
        "template": getattr(cond.template, "value", str(cond.template)),
        "metric_name": cond.metric_name,
        "comparator": getattr(cond.comparator, "value", str(cond.comparator)),
        "threshold": cond.threshold,
        "anchor": cond.anchor,
    }


def _serialize_thesis(thesis: Any) -> dict[str, Any]:
    catalyst = thesis.catalyst_window_end
    return {
        "stock_code": thesis.stock_code,
        "stock_name": thesis.stock_name,
        "instruction_id": thesis.instruction_id,
        "trade_date": thesis.trade_date,
        "created_at": thesis.created_at.isoformat(),
        "entry_price": thesis.entry_price,
        "entry_score": thesis.entry_score,
        "time_stop_trade_days": thesis.time_stop_trade_days,
        "catalyst_window_end": catalyst.isoformat() if catalyst else None,
        "pillars": list(thesis.pillars),
        "invalidation_conditions": [
            _serialize_condition(c) for c in thesis.invalidation_conditions
        ],
        "evidence_ids": list(thesis.evidence_ids),
        # AD-004 — the deterministic buy-time style label (AC-001),
        # display-only. None on legacy theses / the pure-quant path.
        "style": getattr(getattr(thesis, "style", None), "value", None),
    }


_ADVISORY_NOTE = (
    "LLM advisory 盘后复盘为 evidence-only(display-only);运行期接线待 owner "
    "重启,monitoring 仍零 LLM。失效阈值为确定性量化派生,LLM 文本永不影响阈值。"
)


@router.get("/api/position-theses")
async def get_position_theses(request: Request) -> dict[str, Any]:
    """Return the open position theses (pillars + invalidation thresholds).

    Display-only (P1-5-amendment-2026-06-01 §1.2). ``available=False`` when the
    thesis store is unwired; an empty wired store returns ``available=True`` with
    an empty list (no held position carries a thesis yet).
    """
    store = _get_store(request)
    if store is None:
        return _ok(
            {
                "available": False,
                "note": "持仓 thesis 存储未接线(系统停机 / Line-1 尚未落 thesis)。",
                "thesis_count": 0,
                "theses": [],
                "advisory": {"note": _ADVISORY_NOTE},
            }
        )
    try:
        open_theses = store.open_theses()
        theses = [
            _serialize_thesis(open_theses[code]) for code in sorted(open_theses)
        ]
    except Exception:  # noqa: BLE001 — read endpoint never 500s (house style)
        log.exception("position_theses_read_failed")
        return _ok(
            {
                "available": False,
                "note": "持仓 thesis 读取失败(已记录,fail-closed 不报 500)。",
                "thesis_count": 0,
                "theses": [],
                "advisory": {"note": _ADVISORY_NOTE},
            }
        )

    return _ok(
        {
            "available": True,
            "note": "",
            "thesis_count": len(theses),
            "theses": theses,
            "advisory": {"note": _ADVISORY_NOTE},
        }
    )


__all__ = ["router"]
