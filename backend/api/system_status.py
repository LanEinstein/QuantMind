"""P1-5 §1.1 five-freeze-source system-status read-only endpoint.

Surfaces the five **independent** buy/sell freeze sources locked in
CLAUDE.md §2.7 so the front-end ``StatusBar`` and ``SystemStatus`` page
can render five independent status dots — **never** collapse to a single
``frozen=true`` flag (P1-5 §2 redline 4):

1. ``mode_switch``       — :class:`backend.services.mode_router.ModeSwitchState`
2. ``reconciliation_ticket``
3. ``circuit_breaker``   — :class:`backend.risk.circuit_breaker.CircuitBreaker`
4. ``data_quality``      — last evaluated :class:`DataQualityState` snapshot
5. ``eod_pipeline``      — :class:`backend.broker.scheduler.EodPipelineFreezeState`

Sources not yet wired into ``app.state`` return ``status="unavailable"``
so the UI can show a dedicated grey indicator instead of a misleading
"all green" — that satisfies P1-5 §1.1 fail-closed UX (operators must
know whether a probe is dark vs. green).

GET-only endpoint — write actions are forbidden by P1-5 §2 redline 1
(the two writes are scoped to ``/api/execution-reports`` +
``/api/reconciliation-tickets/{id}/decide``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Request

log = structlog.get_logger(component="api_system_status")

router = APIRouter(tags=["system-status"])

# Locked tuple — used by ``redline-check.sh`` to ensure the front-end and
# back-end agree on the five names; the StatusBar.vue checks the same set.
FREEZE_SOURCE_NAMES: tuple[str, ...] = (
    "mode_switch",
    "reconciliation_ticket",
    "circuit_breaker",
    "data_quality",
    "eod_pipeline",
)


def _resolve(obj: Any, attr_name: str, default: Any) -> Any:
    """Read ``attr_name`` from ``obj`` whether it is a property or method.

    Production state classes (e.g. :class:`backend.data.data_quality
    .DataQualityState`) expose breach predicates as ``@property``;
    lightweight test stubs may implement them as plain methods. Without
    this helper the probe would call the property's bool result like a
    method and raise ``'bool' object is not callable`` (codex cycle 1
    P1). Returns ``default`` on missing attribute / probe failure.
    """
    val = getattr(obj, attr_name, default)
    if callable(val):
        try:
            return val()
        except Exception:
            return default
    return val


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _mode_switch_probe(request: Request) -> dict[str, Any]:
    mode_router = getattr(request.app.state, "mode_router", None)
    if mode_router is None:
        return {
            "name": "mode_switch",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "context": None,
        }
    try:
        state = mode_router.mode_state
        active = bool(state.is_active())
        context = state.context() if active else None
        return {
            "name": "mode_switch",
            "active": active,
            "status": "ok",
            "reason": context.get("reason") if context else None,
            "context": context,
        }
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning("mode_switch_probe_failed", error=str(exc))
        return {
            "name": "mode_switch",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "context": None,
        }


def _reconciliation_ticket_probe(request: Request) -> dict[str, Any]:
    """Surface whether an OPEN/EXPIRED reconciliation ticket holds the route.

    Reads ``app.state.reconciliation_ticket_state`` — a lightweight
    snapshot updated by the (yet-to-be-wired) F-005 reconciliation
    listener. Until the listener is wired we report ``unavailable`` so
    operators don't read a false green.
    """
    state = getattr(request.app.state, "reconciliation_ticket_state", None)
    if state is None:
        return {
            "name": "reconciliation_ticket",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "ticket_id": None,
        }
    try:
        # Use ``_resolve`` to accept both ``@property`` and method shapes
        # on whichever ticket-state object Phase F wires up (codex cycle
        # 1 P1 generalised).
        active = bool(_resolve(state, "has_open_ticket", False))
        ticket_id = _resolve(state, "open_ticket_id", None)
        reason = _resolve(state, "reason", None) if active else None
        return {
            "name": "reconciliation_ticket",
            "active": active,
            "status": "ok",
            "reason": reason,
            "ticket_id": ticket_id,
        }
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning("reconciliation_ticket_probe_failed", error=str(exc))
        return {
            "name": "reconciliation_ticket",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "ticket_id": None,
        }


def _circuit_breaker_probe(request: Request) -> dict[str, Any]:
    breaker = getattr(request.app.state, "circuit_breaker", None)
    if breaker is None:
        return {
            "name": "circuit_breaker",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "halted_at": None,
            "consecutive_losses": None,
        }
    try:
        is_halted_fn = getattr(breaker, "is_halted", None)
        halted = bool(is_halted_fn()) if callable(is_halted_fn) else False
        halted_at = getattr(breaker, "_halted_at", None)
        halted_at_iso = (
            halted_at.astimezone(UTC).isoformat()
            if isinstance(halted_at, datetime)
            else None
        )
        cl = getattr(breaker, "_consecutive_losses", None)
        consecutive_losses = int(cl) if isinstance(cl, int) else None
        return {
            "name": "circuit_breaker",
            "active": halted,
            "status": "ok",
            "reason": "daily_loss_or_consecutive_losses" if halted else None,
            "halted_at": halted_at_iso,
            "consecutive_losses": consecutive_losses,
        }
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning("circuit_breaker_probe_failed", error=str(exc))
        return {
            "name": "circuit_breaker",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "halted_at": None,
            "consecutive_losses": None,
        }


def _data_quality_probe(request: Request) -> dict[str, Any]:
    """Surface the worst-of-watchlist data-quality breach.

    The C-004 :class:`DataQualityProvider` is per-stock async; an
    aggregated cache is populated by the 30s watchlist snapshot job
    (C-003 wiring lands in Phase G/F). Until then we read
    ``app.state.last_data_quality_state`` — when missing, status=unavailable.
    """
    last_state = getattr(request.app.state, "last_data_quality_state", None)
    if last_state is None:
        return {
            "name": "data_quality",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "code": None,
        }
    try:
        # The real ``DataQualityState`` (backend/data/data_quality.py) exposes
        # these as ``@property`` while test stubs may implement them as
        # methods. ``_resolve`` accepts both shapes (codex cycle 1 P1).
        is_acceptable = bool(_resolve(last_state, "is_acceptable_for_buy_sell", True))
        active = not is_acceptable
        reason: str | None = None
        if active:
            reason = _resolve(last_state, "degradation_reason", None)
        # Production ``DataQualityState`` does not carry the stock_code (it
        # is the lookup key), so callers wishing to surface it should set
        # ``app.state.last_data_quality_code`` alongside the state. We read
        # both so the test stubs that attach ``stock_code`` directly keep
        # working.
        code = getattr(last_state, "stock_code", None)
        if code is None:
            code = getattr(request.app.state, "last_data_quality_code", None)
        return {
            "name": "data_quality",
            "active": active,
            "status": "ok",
            "reason": reason,
            "code": code,
        }
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning("data_quality_probe_failed", error=str(exc))
        return {
            "name": "data_quality",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "code": None,
        }


def _eod_pipeline_probe(request: Request) -> dict[str, Any]:
    freeze_state = getattr(request.app.state, "eod_pipeline_freeze_state", None)
    if freeze_state is None:
        scheduler = getattr(request.app.state, "broker_scheduler", None)
        freeze_state = getattr(scheduler, "eod_pipeline_freeze_state", None)
    if freeze_state is None:
        return {
            "name": "eod_pipeline",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "raised_at": None,
            "trade_date": None,
        }
    try:
        active = bool(_resolve(freeze_state, "is_active", False))
        reason = _resolve(freeze_state, "reason", None) if active else None
        raised_at = _resolve(freeze_state, "raised_at", None)
        raised_at_iso = (
            raised_at.astimezone(UTC).isoformat()
            if isinstance(raised_at, datetime)
            else None
        )
        trade_date = _resolve(freeze_state, "raised_for_trade_date", None)
        return {
            "name": "eod_pipeline",
            "active": active,
            "status": "ok",
            "reason": reason,
            "raised_at": raised_at_iso,
            "trade_date": trade_date,
        }
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning("eod_pipeline_probe_failed", error=str(exc))
        return {
            "name": "eod_pipeline",
            "active": False,
            "status": "unavailable",
            "reason": None,
            "raised_at": None,
            "trade_date": None,
        }


@router.get("/api/system-status/freeze-sources")
async def freeze_sources(request: Request) -> dict[str, Any]:
    """Return the **five independent** freeze-source snapshots.

    Locked invariants (P1-5 §2 redline 4):

    * Exactly the five ``FREEZE_SOURCE_NAMES`` appear in ``data.sources``.
    * Each source is its own object — there is no top-level ``frozen``
      boolean (aggregation is forbidden because operators must see
      *which* source is blocking trade flow).
    * Probes never raise out of the handler; defensive ``Exception``
      catches downgrade to ``status="unavailable"`` so a failed probe
      cannot bring down the dashboard.
    """
    sources = [
        _mode_switch_probe(request),
        _reconciliation_ticket_probe(request),
        _circuit_breaker_probe(request),
        _data_quality_probe(request),
        _eod_pipeline_probe(request),
    ]

    any_unavailable = any(s["status"] == "unavailable" for s in sources)
    return _ok(
        {
            "sources": sources,
            "any_active": any(s["active"] for s in sources),
            "any_unavailable": any_unavailable,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    )
