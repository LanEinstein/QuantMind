"""Z-004 — read-only ≤5-slot portfolio + rotation surface.

Surfaces the deterministic slot-rotation read model (V-003: append-only
``RotationIntent`` ledger — 卖谁换谁 / 在位分 / 挑战分 / 到期 / T+1 跨日态 /
UNDERINVESTED) so the ``Portfolio`` page can render a 5-slot + rotation panel
(P1-5-amendment-2026-06-01 §1.2 direction③). Slot occupancy = held count
(from the existing positions API, cross-referenced client-side) vs the ≤5 cap.

Red lines:

* GET only — the global write-endpoint allowlist forbids any non-GET here.
* No ``backend.{llm,agents,risk,broker,data}`` imports — only the runner's
  read-only ``intent_store`` (a pure append-only JSONL ledger). The cap value
  comes from the wired runner (config-sourced, never hardcoded here).
* When the rotation runner is unwired the endpoint returns ``available=False``
  and never 500s.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

log = logging.getLogger("backend.api.slot_rotation")

router = APIRouter(tags=["slot_rotation"])

_DEFAULT_EVENT_LIMIT = 20
_MAX_EVENT_LIMIT = 200


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _get_runner(request: Request) -> Any | None:
    runner = getattr(request.app.state, "rotation_runner", None)
    if runner is None or not hasattr(runner, "intent_store"):
        return None
    return runner


def _serialize_intent(intent: Any) -> dict[str, Any]:
    return {
        "intent_id": intent.intent_id,
        "created_trade_date": intent.created_trade_date,
        "expires_at_trade_date": intent.expires_at_trade_date,
        "sell_instruction_id": intent.sell_instruction_id,
        "incumbent_code": intent.incumbent_code,
        "challenger_code": intent.challenger_code,
        "incumbent_score": intent.incumbent_score,
        "challenger_score": intent.challenger_score,
        "incumbent_percentile": intent.incumbent_percentile,
        "challenger_percentile": intent.challenger_percentile,
    }


def _serialize_event(
    event: Any, legs: dict[str, tuple[str, str]]
) -> dict[str, Any]:
    outcome = event.outcome_kind
    # Terminal events (RESOLVED / EXPIRED) carry only ``intent_id`` — the store
    # does not re-embed the intent. Fold the sell→challenger legs from the
    # earlier PROPOSED event so the panel keeps the rotation context after the
    # intent leaves the open set (codex P2).
    if event.intent is not None:
        incumbent: str | None = event.intent.incumbent_code
        challenger: str | None = event.intent.challenger_code
    else:
        leg = legs.get(event.intent_id) if event.intent_id else None
        incumbent, challenger = leg if leg is not None else (None, None)
    return {
        "event_type": getattr(event.event_type, "value", str(event.event_type)),
        "trade_date": event.trade_date,
        "intent_id": event.intent_id,
        "incumbent_code": incumbent,
        "challenger_code": challenger,
        "outcome_kind": getattr(outcome, "value", outcome) if outcome else None,
        "buy_code": event.buy_code,
        "blocks_further_rotation": event.blocks_further_rotation,
        "note": event.note,
    }


def _unavailable(note: str) -> dict[str, Any]:
    return _ok(
        {
            "available": False,
            "note": note,
            "max_total_positions": None,
            "underinvested_block_active": False,
            "open_intent_count": 0,
            "open_intents": [],
            "recent_events": [],
        }
    )


@router.get("/api/slot-rotation")
async def get_slot_rotation(
    request: Request,
    event_limit: int = Query(_DEFAULT_EVENT_LIMIT, ge=1, le=_MAX_EVENT_LIMIT),
) -> dict[str, Any]:
    """Return open rotation intents + recent lifecycle events + the ≤5 cap.

    Display-only (P1-5-amendment-2026-06-01 §1.2). ``available=False`` when the
    rotation runner is unwired; a wired-but-idle runner returns ``available=True``
    with empty collections (no rotation has been proposed yet).
    """
    runner = _get_runner(request)
    if runner is None:
        return _unavailable("槽位轮动 runner 未接线(系统停机 / 轮动未启用)。")

    try:
        store = runner.intent_store
        open_intents = [_serialize_intent(i) for i in store.open_intents()]
        all_events = list(store.load_events())
        # Map intent_id → (incumbent, challenger) from every PROPOSED event so a
        # terminal event in the window can recover its legs even when the
        # originating PROPOSED row predates the window.
        legs: dict[str, tuple[str, str]] = {
            e.intent_id: (e.intent.incumbent_code, e.intent.challenger_code)
            for e in all_events
            if e.intent is not None and e.intent_id
        }
        # Most-recent-first, bounded — the T+1 cross-day / expiry / UNDERINVESTED
        # transitions live in the tail of the append-only ledger.
        tail = all_events[-event_limit:]
        recent_events = [_serialize_event(e, legs) for e in reversed(tail)]
        underinvested = bool(store.underinvested_block_active())
        max_positions = getattr(runner, "max_total_positions", None)
    except Exception:  # noqa: BLE001 — read endpoint never 500s (house style)
        log.exception("slot_rotation_read_failed")
        return _unavailable("槽位轮动读取失败(已记录,fail-closed 不报 500)。")

    return _ok(
        {
            "available": True,
            "note": "",
            "max_total_positions": max_positions,
            "underinvested_block_active": underinvested,
            "open_intent_count": len(open_intents),
            "open_intents": open_intents,
            "recent_events": recent_events,
        }
    )


__all__ = ["router"]
