"""Stop-loss monitoring — pure stateless functions."""

from __future__ import annotations

import structlog

from backend.broker.models import Position

log = structlog.get_logger(component="risk.stop_loss")


def check_stop_loss(
    cost_price: float, current_price: float, threshold: float
) -> bool:
    """Check if a position should be stopped out.

    Args:
        cost_price: Average cost per share.
        current_price: Current market price.
        threshold: Maximum acceptable loss ratio (e.g. 0.08 for -8%).

    Returns:
        True if the loss exceeds or equals the threshold.
    """
    if cost_price <= 0:
        return False
    loss_pct = (current_price - cost_price) / cost_price
    triggered = loss_pct <= -threshold
    if triggered:
        log.warning(
            "stop_loss_triggered",
            cost_price=cost_price,
            current_price=current_price,
            loss_pct=f"{loss_pct:.2%}",
        )
    return triggered


def check_trailing_stop(
    peak_price: float, current_price: float, threshold: float
) -> bool:
    """Check if a trailing stop is triggered.

    Args:
        peak_price: Highest price since position was opened.
        current_price: Current market price.
        threshold: Maximum acceptable drawdown from peak (e.g. 0.10 for -10%).

    Returns:
        True if drawdown from peak exceeds or equals the threshold.
    """
    if peak_price <= 0:
        return False
    drawdown = (current_price - peak_price) / peak_price
    triggered = drawdown <= -threshold
    if triggered:
        log.warning(
            "trailing_stop_triggered",
            peak_price=peak_price,
            current_price=current_price,
            drawdown=f"{drawdown:.2%}",
        )
    return triggered


def scan_positions(
    positions: tuple[Position, ...],
    prices: dict[str, float],
    stop_loss_pct: float,
    trailing_peaks: dict[str, float],
    trailing_stop_pct: float,
) -> tuple[str, ...]:
    """Scan all positions for stop-loss triggers.

    Args:
        positions: Current positions.
        prices: Current market prices keyed by stock code.
        stop_loss_pct: Fixed stop-loss threshold.
        trailing_peaks: Peak prices per stock code (maintained by caller).
        trailing_stop_pct: Trailing stop threshold.

    Returns:
        Tuple of stock codes that should be stopped out.
    """
    triggered: list[str] = []
    for pos in positions:
        current = prices.get(pos.code)
        if current is None:
            log.warning("price_missing_for_stop_check", code=pos.code)
            continue
        if check_stop_loss(pos.cost_price, current, stop_loss_pct):
            triggered.append(pos.code)
            continue
        peak = trailing_peaks.get(pos.code)
        if peak is not None and check_trailing_stop(
            peak, current, trailing_stop_pct
        ):
            triggered.append(pos.code)
    return tuple(triggered)
