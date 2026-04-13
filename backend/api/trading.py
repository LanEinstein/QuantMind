"""FastAPI routes for virtual trading account management."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

from backend.broker.models import OrderStatus, RiskConfig
from backend.data.publisher import publish_portfolio_event

log = structlog.get_logger(component="api_trading")

router = APIRouter()

_CODE_RE = re.compile(r"^\d{6}$")


def _parse_traded_at(value: str | datetime) -> datetime:
    """Parse a traded_at value into a timezone-aware datetime."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


def _get_registry(request: Request):
    """Extract BrokerRegistry from app state."""
    try:
        return request.app.state.broker_registry
    except AttributeError:
        _err("Trading system not initialized", 503)


def _get_approval_queue(request: Request):
    """Extract ApprovalQueue from app state."""
    try:
        return request.app.state.approval_queue
    except AttributeError:
        _err("Approval queue not initialized", 503)


def _get_risk_config(request: Request) -> RiskConfig | None:
    """Extract RiskConfig from app state, or None if not loaded."""
    return getattr(request.app.state, "risk_config", None)


def _get_redis(request: Request):
    """Extract Redis client from app state, or None."""
    return getattr(request.app.state, "redis", None)


async def _publish_position_update(
    request: Request, account_id: str
) -> None:
    """Fetch current enriched positions and push them via WebSocket."""
    redis_client = _get_redis(request)
    if redis_client is None:
        return
    try:
        registry = _get_registry(request)
        broker = registry.get_broker(account_id)
        account = await broker.get_account()
        positions = await broker.get_positions()
        risk_config = _get_risk_config(request)
        enriched = [
            _enrich_position(
                p.model_dump(mode="json"), account.total_assets, risk_config
            )
            for p in positions
        ]
        await publish_portfolio_event(
            redis_client,
            "position_update",
            {"account_id": account_id, "positions": enriched},
        )
    except Exception as exc:
        log.debug("position_update_publish_failed", error=str(exc))


def _enrich_position(
    pos_dict: dict[str, Any],
    total_assets: float,
    risk_config: RiskConfig | None,
) -> dict[str, Any]:
    """Add risk fields to a position dict.

    Computes stop_loss_line, stop_loss_distance, position_pct, and risk_status
    from the risk configuration. Uses cost_price as a proxy for current_price
    when market data is not available.
    """
    cost = pos_dict.get("cost_price", 0.0)
    market_value = pos_dict.get("market_value", 0.0)

    # Stop-loss computation
    sl_pct = risk_config.stop_loss.single_stock_pct if risk_config else 0.08
    max_single_pct = (
        risk_config.position_limits.max_single_stock_pct if risk_config else 0.20
    )

    stop_loss_line = round(cost * (1 - sl_pct), 2) if cost > 0 else 0.0
    stop_loss_distance = round(sl_pct, 4) if cost > 0 else 0.0

    # Position weight
    position_pct = (
        round(market_value / total_assets, 4) if total_assets > 0 else 0.0
    )

    # Risk status determination
    if position_pct > max_single_pct:
        risk_status = "over_limit"
    elif stop_loss_distance < 0.02:
        risk_status = "triggered"
    elif stop_loss_distance < 0.05:
        risk_status = "near_stop"
    else:
        risk_status = "normal"

    return {
        **pos_dict,
        "stop_loss_line": stop_loss_line,
        "stop_loss_distance": stop_loss_distance,
        "position_pct": position_pct,
        "risk_status": risk_status,
    }


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


@router.get("/api/trading/accounts")
async def get_accounts(request: Request) -> dict[str, Any]:
    """List all virtual trading accounts."""
    registry = _get_registry(request)
    accounts = registry.list_accounts()
    return _ok([a.model_dump(mode="json") for a in accounts])


@router.get("/api/trading/account")
async def get_account(
    request: Request,
    account_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Get account info for a virtual trading account."""
    registry = _get_registry(request)
    try:
        broker = registry.get_broker(account_id)
    except KeyError:
        _err(f"Account '{account_id}' not found", 404)
        return _ok(None)  # unreachable

    account = await broker.get_account()
    return _ok(account.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


@router.get("/api/trading/positions")
async def get_positions(
    request: Request,
    account_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Get positions enriched with risk status for a virtual account."""
    registry = _get_registry(request)
    try:
        broker = registry.get_broker(account_id)
    except KeyError:
        _err(f"Account '{account_id}' not found", 404)
        return _ok(None)  # unreachable

    account = await broker.get_account()
    positions = await broker.get_positions()
    risk_config = _get_risk_config(request)

    enriched = [
        _enrich_position(
            p.model_dump(mode="json"), account.total_assets, risk_config
        )
        for p in positions
    ]
    return _ok(enriched)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@router.get("/api/trading/orders")
async def get_orders(
    request: Request,
    account_id: str = Query(default="default"),
    status: str | None = Query(default=None),
) -> dict[str, Any]:
    """Get orders for a virtual account, optionally filtered by status."""
    registry = _get_registry(request)
    try:
        broker = registry.get_broker(account_id)
    except KeyError:
        _err(f"Account '{account_id}' not found", 404)
        return _ok(None)  # unreachable

    order_status = None
    if status is not None:
        try:
            order_status = OrderStatus(status)
        except ValueError:
            _err(
                f"Invalid status '{status}'. Must be one of: "
                f"{', '.join(s.value for s in OrderStatus)}",
                422,
            )

    orders = await broker.get_orders(status=order_status)
    return _ok([o.model_dump(mode="json") for o in orders])


@router.post("/api/trading/cancel/{order_id}")
async def cancel_order(
    request: Request,
    order_id: str,
    account_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Cancel a pending order."""
    registry = _get_registry(request)
    try:
        broker = registry.get_broker(account_id)
    except KeyError:
        _err(f"Account '{account_id}' not found", 404)
        return _ok(None)  # unreachable

    success = await broker.cancel_order(order_id)
    if not success:
        _err(f"Cannot cancel order '{order_id}': not found or not pending", 400)
    await _publish_position_update(request, account_id)
    return _ok({"success": True, "order_id": order_id})


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


@router.get("/api/trading/trades")
async def get_trades(
    request: Request,
    account_id: str = Query(default="default"),
    code: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
) -> dict[str, Any]:
    """Get trade history with optional filters."""
    registry = _get_registry(request)
    try:
        broker = registry.get_broker(account_id)
    except KeyError:
        _err(f"Account '{account_id}' not found", 404)
        return _ok(None)  # unreachable

    if code is not None and not _CODE_RE.match(code):
        _err("Stock code must be 6 digits", 422)

    trades = await broker.get_trades()
    result = [t.model_dump(mode="json") for t in trades]

    # Apply filters in-memory (acceptable for mock broker)
    if code:
        result = [t for t in result if t["code"] == code]

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            result = [
                t for t in result
                if _parse_traded_at(t["traded_at"]) >= start_dt
            ]
        except (ValueError, TypeError):
            _err("Invalid start_date format (use ISO 8601)", 422)

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date)
            if end_dt.tzinfo is None:
                # Date-only input: make inclusive of the entire day
                end_dt = end_dt.replace(
                    hour=23, minute=59, second=59, tzinfo=timezone.utc
                )
            result = [
                t for t in result
                if _parse_traded_at(t["traded_at"]) <= end_dt
            ]
        except (ValueError, TypeError):
            _err("Invalid end_date format (use ISO 8601)", 422)

    return _ok(result)


# ---------------------------------------------------------------------------
# Approval queue
# ---------------------------------------------------------------------------


@router.get("/api/trading/pending-approvals")
async def get_pending_approvals(
    request: Request,
    account_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """List pending order approvals."""
    queue = _get_approval_queue(request)
    pending = queue.list_pending(account_id=account_id)
    return _ok([p.model_dump(mode="json") for p in pending])


@router.post("/api/trading/approve/{approval_id}")
async def approve_order(
    request: Request,
    approval_id: str,
) -> dict[str, Any]:
    """Approve a pending order and send it to the broker."""
    queue = _get_approval_queue(request)
    # Resolve the approval's account_id before approving (approve removes it)
    pending = queue.list_pending()
    approval_account = "default"
    for p in pending:
        if p.id == approval_id:
            approval_account = p.account_id
            break

    try:
        result = await queue.approve(approval_id)
    except KeyError:
        _err(f"Pending approval '{approval_id}' not found", 404)
        return _ok(None)  # unreachable

    redis_client = _get_redis(request)
    await _publish_position_update(request, approval_account)
    await publish_portfolio_event(
        redis_client,
        "approval_update",
        {"account_id": approval_account, "action": "approved", "approval_id": approval_id},
    )

    return _ok(result.model_dump(mode="json"))


@router.post("/api/trading/reject/{approval_id}")
async def reject_order(
    request: Request,
    approval_id: str,
) -> dict[str, Any]:
    """Reject a pending order."""
    queue = _get_approval_queue(request)
    # Resolve account_id before rejecting (reject removes it)
    pending = queue.list_pending()
    reject_account = "default"
    for p in pending:
        if p.id == approval_id:
            reject_account = p.account_id
            break

    success = queue.reject(approval_id)
    if not success:
        _err(f"Pending approval '{approval_id}' not found", 404)

    redis_client = _get_redis(request)
    await publish_portfolio_event(
        redis_client,
        "approval_update",
        {"account_id": reject_account, "action": "rejected", "approval_id": approval_id},
    )

    return _ok({"success": True, "id": approval_id})


# ---------------------------------------------------------------------------
# Circuit breaker status
# ---------------------------------------------------------------------------


@router.get("/api/trading/circuit-breaker-status")
async def get_circuit_breaker_status(request: Request) -> dict[str, Any]:
    """Return current circuit breaker state for portfolio display."""
    cb = getattr(request.app.state, "circuit_breaker", None)
    if cb is None:
        return _ok({
            "halted": False,
            "daily_pnl_pct": 0.0,
            "consecutive_losses": 0,
        })
    return _ok({
        "halted": cb.is_halted(),
        "daily_pnl_pct": cb._daily_pnl_pct,
        "consecutive_losses": cb._consecutive_losses,
    })
