"""G-008 — read-only data-quality probe surface.

Surfaces the per-stock :class:`DataQualityState` evaluated by
:class:`DataQualityProvider` (C-004) so the Phase B-finale page can
render the 7 + 3 state matrix and highlight rows that would block
buy/sell routing (P0-8 §1.5.1).

Red lines:

* GET only — :func:`scripts/redline-check.sh` includes the global
  write-endpoint allowlist that forbids anything but the 2 locked
  POSTs (execution-reports + reconciliation decide).
* The endpoint never imports ``backend.{llm,agents}`` — only
  ``backend.data.data_quality`` which is the SSoT for the 4-blocking
  breach subset (C-004).
* When the provider is not wired (simulation_auto with the
  C-006 MiroFish hookup still deferred) the endpoint surfaces
  ``status="unavailable"`` and never 500s.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Query, Request

log = logging.getLogger("backend.api.data_quality")

router = APIRouter(tags=["data_quality"])


class DataQualityProbe(Protocol):
    """Duck-typed contract — wired in main.py after C-004 lands."""

    async def evaluate(self, *, stock_code: str) -> Any: ...


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _get_probe(request: Request) -> DataQualityProbe | None:
    probe = getattr(request.app.state, "data_quality_provider", None)
    if probe is None:
        return None
    if not hasattr(probe, "evaluate"):
        return None
    return probe


def _serialize_state(state: Any, *, stock_code: str, now: datetime) -> dict[str, Any]:
    """Project a :class:`DataQualityState` into the JSON wire shape.

    Mirrors the locked P1-2.B §1.5.1 schema (7 breach bools + 3 counters
    + 2 derived properties) so the front-end can render the full 7+3
    matrix once the C-004 provider is wired. Codex cycle 1 P2 catch —
    the previous shape (snapshot_outage / news_outage / mirofish_outage)
    used names that do not exist on the real ``DataQualityState`` and
    would have silently returned ``null`` for every breach once the
    real provider landed.
    """
    fields = (
        # 7 breach bools (4 blocking + 3 informational).
        "quote_unavailable",
        "quote_staleness_breach",
        "quote_divergence_breach",
        "minimum_freshness_breach",
        "news_outage_breach",
        "mirofish_unavailable",
        "watchlist_snapshot_outage",
        # 3 counters.
        "primary_quote_age_seconds",
        "backup_quote_age_seconds",
        "news_sources_alive_count",
    )
    payload: dict[str, Any] = {
        "stock_code": stock_code,
        "evaluated_at": now.astimezone(UTC).isoformat(),
    }
    for name in fields:
        value = getattr(state, name, None)
        payload[name] = value

    # Derived properties — pulled separately so the source of truth stays
    # in :class:`DataQualityState` (UI cannot drift).
    payload["is_acceptable_for_buy_sell"] = bool(
        getattr(state, "is_acceptable_for_buy_sell", False)
    )
    payload["degradation_reason"] = getattr(state, "degradation_reason", None)

    # Compose blocking_breaches from the 4 locked blockers so the UI can
    # render a clear "what would freeze routing" list even before the
    # provider exposes a property for it (P0-8 §2 redline 11 — blocking
    # set is locked to these four).
    payload["blocking_breaches"] = [
        name
        for name in (
            "quote_unavailable",
            "quote_staleness_breach",
            "quote_divergence_breach",
            "minimum_freshness_breach",
        )
        if bool(getattr(state, name, False))
    ]
    return payload


@router.get("/api/data-quality")
async def get_data_quality(
    request: Request,
    stock_code: str = Query(..., pattern=r"^\d{6}$"),
) -> dict[str, Any]:
    """Return the live :class:`DataQualityState` for one watchlist stock."""
    probe = _get_probe(request)
    now = datetime.now(tz=UTC)
    if probe is None:
        return _ok(
            {
                "status": "unavailable",
                "stock_code": stock_code,
                "reason": "data_quality_provider_not_wired",
                "timestamp": now.isoformat(),
            }
        )
    try:
        # Pass ``now`` because the real C-004 provider signature is
        # ``evaluate(stock_code: str, now: datetime)``. The argument is
        # also passed as a keyword so test doubles using ``AsyncMock``
        # see a consistent call signature (codex cycle 1 P2 RESOLVED).
        state = await probe.evaluate(stock_code=stock_code, now=now)
    except Exception as exc:  # noqa: BLE001 — operator visibility, fail-soft
        log.warning(
            "data_quality_probe_failed code=%s error=%s", stock_code, exc
        )
        return _ok(
            {
                "status": "unavailable",
                "stock_code": stock_code,
                "reason": "data_quality_probe_failed",
                "timestamp": now.isoformat(),
            }
        )
    if state is None:
        raise HTTPException(
            status_code=404,
            detail={
                "status": "error",
                "data": None,
                "error": f"no data-quality state for code {stock_code!r}",
            },
        )
    return _ok(
        {
            "status": "ok",
            "state": _serialize_state(state, stock_code=stock_code, now=now),
            "timestamp": now.isoformat(),
        }
    )


__all__ = ["router"]
