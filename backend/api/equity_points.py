"""GET endpoint exposing the latest :class:`EquityPoint` MTM snapshot.

The MockBroker mirror tells the operator what they own; the
:class:`backend.models.equity.EquityPoint` written every 30 seconds by
the BrokerScheduler's ``intraday_mtm`` cron tells them what it is
*worth right now* — including per-position ``price_quality`` and
``last_price_at`` so the Portfolio page can flag stale or degraded
prices instead of silently displaying a cost-price fallback (P1-2.B §2
red line 6).

Read-only. The Phase F integration wires the real
:class:`EquityPointRepository` onto ``app.state``; until then the
endpoint reports ``repository_status="unavailable"`` so the UI shows
"MTM 未就绪" instead of crashing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog
from fastapi import APIRouter, Request

from backend.models.equity import EquityPoint

log = structlog.get_logger(component="api_equity_points")

router = APIRouter(tags=["equity-points"])


@runtime_checkable
class EquityPointReadRepository(Protocol):
    """Read-only contract for the Phase F-wired equity point store."""

    async def get_latest(self) -> EquityPoint | None: ...


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _serialize(point: EquityPoint) -> dict[str, Any]:
    return {
        "snapshot_at": point.snapshot_at.astimezone(UTC).isoformat(),
        "trade_date": point.trade_date,
        "cash": point.cash,
        "frozen_cash": point.frozen_cash,
        "market_value": point.market_value,
        "total_equity": point.total_equity,
        "initial_capital": point.initial_capital,
        "pnl": point.pnl,
        "pnl_pct": point.pnl_pct,
        "quality": point.quality.value,
        "last_broker_event_id": point.last_broker_event_id,
        "policy_hash": getattr(point, "policy_hash", None),
        "positions": [
            {
                "code": p.code,
                "volume": p.volume,
                "cost_price": p.cost_price,
                "last_price": p.last_price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pnl_pct": p.unrealized_pnl_pct,
                "price_quality": p.price_quality.value,
                "last_price_at": (
                    p.last_price_at.astimezone(UTC).isoformat()
                    if p.last_price_at is not None
                    else None
                ),
            }
            for p in point.positions
        ],
    }


@router.get("/api/portfolio/equity-points/latest")
async def get_latest_equity_point(request: Request) -> dict[str, Any]:
    """Return the latest MTM tick + per-position quality.

    Graceful degradation rules:

    * ``repository_status="unavailable"`` + ``point=None`` when no
      repository is wired (Phase F TODO);
    * ``point=None`` + ``repository_status="ok"`` when the store is
      wired but no point has been written yet (e.g. fresh deploy);
    * Probe-internal exceptions degrade to unavailable so the Portfolio
      page never 500s on an MTM glitch.
    """
    repo = getattr(request.app.state, "equity_point_repository", None)
    if repo is None or not isinstance(repo, EquityPointReadRepository):
        return _ok(
            {
                "point": None,
                "repository_status": "unavailable",
            }
        )

    try:
        point = await repo.get_latest()
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning("equity_point_latest_failed", error=str(exc))
        return _ok(
            {
                "point": None,
                "repository_status": "unavailable",
            }
        )

    return _ok(
        {
            "point": _serialize(point) if point is not None else None,
            "repository_status": "ok",
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    )
