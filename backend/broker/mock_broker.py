"""MockBroker — full A-share simulation trading engine."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import structlog

from backend.broker.interface import IBroker
from backend.broker.models import (
    AccountInfo,
    BrokerConfig,
    Order,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    Trade,
)
from backend.utils.trading_hours import SHANGHAI, is_trading_hours

log = structlog.get_logger(component="mock_broker")


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def get_price_limits(code: str, prev_close: float) -> tuple[float, float]:
    """Get price limits (涨跌停) for a stock based on board type.

    Args:
        code: 6-digit stock code.
        prev_close: Previous trading day's close price.

    Returns:
        (lower_limit, upper_limit) rounded to 2 decimal places.
    """
    if prev_close <= 0:
        return (0.0, 0.0)

    # Check most specific prefixes first
    if code.startswith("688"):
        pct = 0.30  # STAR market 科创板
    elif code.startswith("300"):
        pct = 0.20  # ChiNext 创业板
    else:
        pct = 0.10  # Main board

    return (
        round(prev_close * (1 - pct), 2),
        round(prev_close * (1 + pct), 2),
    )


def _calc_commission(
    amount: float, rate: float, min_commission: float
) -> float:
    return round(max(amount * rate, min_commission), 2)


def _calc_stamp_tax(
    amount: float, direction: OrderDirection, rate: float
) -> float:
    if direction == OrderDirection.SELL:
        return round(amount * rate, 2)
    return 0.0


def _apply_slippage(
    price: float, direction: OrderDirection, bps: int
) -> float:
    factor = bps / 10_000
    if direction == OrderDirection.BUY:
        return round(price * (1 + factor), 2)
    return round(price * (1 - factor), 2)


# ---------------------------------------------------------------------------
# Internal mutable state types
# ---------------------------------------------------------------------------


@dataclass
class _MutablePosition:
    code: str
    volume: int = 0
    today_bought_volume: int = 0
    cost_price: float = 0.0

    @property
    def available_volume(self) -> int:
        return self.volume - self.today_bought_volume


@dataclass
class _MutableOrder:
    order_id: str
    code: str
    price: float
    volume: int
    direction: OrderDirection
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING
    filled_volume: int = 0
    avg_fill_price: float = 0.0
    reject_reason: str | None = None
    frozen_amount: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=SHANGHAI))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=SHANGHAI))


# ---------------------------------------------------------------------------
# MockBroker
# ---------------------------------------------------------------------------


class MockBroker(IBroker):
    """Full A-share simulation trading engine.

    Simulates virtual account, order matching, T+1, price limits,
    and friction costs (commission, stamp tax, slippage).
    """

    def __init__(
        self,
        config: BrokerConfig,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        self._now = now_func or (lambda: datetime.now(tz=SHANGHAI))
        self._cash: float = config.initial_capital
        self._frozen_cash: float = 0.0
        self._initial_capital: float = config.initial_capital
        self._orders: dict[str, _MutableOrder] = {}
        self._positions: dict[str, _MutablePosition] = {}
        self._trades: list[Trade] = []
        self._lock = asyncio.Lock()
        self._log = log

    async def place_order(
        self,
        code: str,
        price: float,
        volume: int,
        direction: OrderDirection,
        order_type: OrderType,
    ) -> OrderResult:
        """Place and immediately attempt to fill an order."""
        async with self._lock:
            now = self._now()
            order_id = uuid.uuid4().hex[:12]

            # Validate
            valid, msg = self._validate(code, price, volume, direction, now)
            if not valid:
                order = _MutableOrder(
                    order_id=order_id, code=code, price=price,
                    volume=volume, direction=direction,
                    order_type=order_type, status=OrderStatus.REJECTED,
                    reject_reason=msg, created_at=now, updated_at=now,
                )
                self._orders[order_id] = order
                return OrderResult(
                    order_id=order_id, success=False, message=msg
                )

            # Create order
            order = _MutableOrder(
                order_id=order_id, code=code, price=price,
                volume=volume, direction=direction,
                order_type=order_type, created_at=now, updated_at=now,
            )

            # Freeze cash for BUY
            if direction == OrderDirection.BUY:
                estimated = (
                    price * volume * (1 + self._config.slippage_bps / 10_000)
                    + _calc_commission(
                        price * volume, self._config.commission_rate,
                        self._config.min_commission,
                    )
                )
                order.frozen_amount = estimated
                self._cash -= estimated
                self._frozen_cash += estimated

            # Fill immediately
            self._fill_order(order, now)
            self._orders[order_id] = order

            self._log.info(
                "order_placed",
                order_id=order_id, code=code,
                direction=direction, status=order.status,
            )
            return OrderResult(
                order_id=order_id, success=True, message="Order filled"
            )

    def _validate(
        self,
        code: str,
        price: float,
        volume: int,
        direction: OrderDirection,
        now: datetime,
    ) -> tuple[bool, str]:
        """Pre-trade validation checks."""
        if not is_trading_hours(now):
            return False, "Order rejected: outside trading hours"

        if volume <= 0 or volume % 100 != 0:
            return (
                False,
                f"Order rejected: volume {volume} must be "
                f"a positive multiple of 100",
            )

        if price <= 0:
            return False, "Order rejected: price must be positive"

        if direction == OrderDirection.BUY:
            cost = price * volume * (1 + self._config.slippage_bps / 10_000)
            fee = _calc_commission(
                price * volume, self._config.commission_rate,
                self._config.min_commission,
            )
            if self._cash < cost + fee:
                return (
                    False,
                    f"Order rejected: insufficient funds "
                    f"(need {cost + fee:.2f}, available {self._cash:.2f})",
                )
        else:
            pos = self._positions.get(code)
            if pos is None or pos.volume == 0:
                return False, f"Order rejected: no position for {code}"
            if pos.available_volume < volume:
                return (
                    False,
                    f"Order rejected: insufficient available shares "
                    f"(T+1 restriction, available: {pos.available_volume}, "
                    f"requested: {volume})",
                )

        return True, ""

    def _fill_order(self, order: _MutableOrder, now: datetime) -> None:
        """Fill an order immediately at the order price with slippage."""
        fill_price = _apply_slippage(
            order.price, order.direction, self._config.slippage_bps
        )
        amount = fill_price * order.volume
        commission = _calc_commission(
            amount, self._config.commission_rate, self._config.min_commission
        )
        stamp_tax = _calc_stamp_tax(
            amount, order.direction, self._config.stamp_tax_rate
        )
        slippage_cost = round(
            abs(fill_price - order.price) * order.volume, 2
        )

        # Update order
        order.status = OrderStatus.FILLED
        order.filled_volume = order.volume
        order.avg_fill_price = fill_price
        order.updated_at = now

        # Create trade
        if order.direction == OrderDirection.BUY:
            net_amount = amount + commission
        else:
            net_amount = amount - commission - stamp_tax

        trade = Trade(
            trade_id=uuid.uuid4().hex[:12],
            order_id=order.order_id,
            code=order.code,
            price=fill_price,
            volume=order.volume,
            amount=amount,
            direction=order.direction,
            commission=commission,
            stamp_tax=stamp_tax,
            slippage_cost=slippage_cost,
            net_amount=net_amount,
            traded_at=now,
        )
        self._trades.append(trade)

        # Update position
        if order.direction == OrderDirection.BUY:
            self._apply_buy(order.code, fill_price, order.volume)
            # Settle cash: unfreeze and deduct actual cost
            self._frozen_cash -= order.frozen_amount
            actual_cost = amount + commission
            delta = order.frozen_amount - actual_cost
            self._cash += delta  # return over-frozen amount
            if self._cash < -0.01:
                self._log.error(
                    "cash_underflow_detected", cash=self._cash, delta=delta
                )
                self._cash = 0.0
        else:
            self._apply_sell(order.code, order.volume)
            self._cash += net_amount

    def _apply_buy(
        self, code: str, fill_price: float, volume: int
    ) -> None:
        """Update position for a buy fill."""
        pos = self._positions.get(code)
        if pos is None:
            self._positions[code] = _MutablePosition(
                code=code,
                volume=volume,
                today_bought_volume=volume,
                cost_price=fill_price,
            )
        else:
            # Cost averaging
            total_cost = pos.cost_price * pos.volume + fill_price * volume
            new_volume = pos.volume + volume
            pos.cost_price = total_cost / new_volume
            pos.volume = new_volume
            pos.today_bought_volume += volume

    def _apply_sell(self, code: str, volume: int) -> None:
        """Update position for a sell fill."""
        pos = self._positions.get(code)
        if pos is None:
            self._log.error("sell_position_missing", code=code, volume=volume)
            return
        pos.volume -= volume
        if pos.volume <= 0:
            del self._positions[code]

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        async with self._lock:
            order = self._orders.get(order_id)
            if order is None or order.status != OrderStatus.PENDING:
                return False

            order.status = OrderStatus.CANCELLED
            order.updated_at = self._now()

            # Unfreeze cash for BUY orders
            if order.direction == OrderDirection.BUY:
                self._frozen_cash -= order.frozen_amount
                self._cash += order.frozen_amount

            self._log.info("order_cancelled", order_id=order_id)
            return True

    async def get_positions(self) -> tuple[Position, ...]:
        """Get all current positions as frozen models."""
        async with self._lock:
            return self._build_positions()

    def _build_positions(self) -> tuple[Position, ...]:
        """Build position snapshots (must be called under lock)."""
        result: list[Position] = []
        for pos in self._positions.values():
            if pos.volume <= 0:
                continue
            mv = pos.cost_price * pos.volume
            pnl = 0.0
            pnl_pct = 0.0
            result.append(
                Position(
                    code=pos.code,
                    volume=pos.volume,
                    available_volume=pos.available_volume,
                    cost_price=round(pos.cost_price, 2),
                    market_value=round(mv, 2),
                    unrealized_pnl=round(pnl, 2),
                    unrealized_pnl_pct=round(pnl_pct, 4),
                )
            )
        return tuple(result)

    async def get_account(self) -> AccountInfo:
        """Get current account snapshot as a frozen model."""
        async with self._lock:
            positions = self._build_positions()
            market_value = sum(p.market_value for p in positions)
            total = self._cash + self._frozen_cash + market_value
            pnl = total - self._initial_capital
            pnl_pct = (
                pnl / self._initial_capital
                if self._initial_capital > 0
                else 0.0
            )
            return AccountInfo(
                total_assets=round(total, 2),
                available_cash=round(self._cash, 2),
                frozen_cash=round(self._frozen_cash, 2),
                market_value=round(market_value, 2),
                total_pnl=round(pnl, 2),
                total_pnl_pct=round(pnl_pct, 6),
                initial_capital=self._initial_capital,
            )

    async def get_orders(
        self, status: OrderStatus | None = None
    ) -> tuple[Order, ...]:
        """Get orders, optionally filtered by status."""
        result: list[Order] = []
        for o in self._orders.values():
            if status is not None and o.status != status:
                continue
            result.append(
                Order(
                    order_id=o.order_id,
                    code=o.code,
                    price=o.price,
                    volume=o.volume,
                    filled_volume=o.filled_volume,
                    avg_fill_price=o.avg_fill_price,
                    direction=o.direction,
                    order_type=o.order_type,
                    status=o.status,
                    created_at=o.created_at,
                    updated_at=o.updated_at,
                    reject_reason=o.reject_reason,
                )
            )
        return tuple(result)

    async def get_trades(self) -> tuple[Trade, ...]:
        """Get all executed trades."""
        return tuple(self._trades)

    async def advance_day(self) -> None:
        """Advance to the next trading day (T+1 resolution).

        Makes all today's bought shares available for selling.
        """
        async with self._lock:
            for pos in self._positions.values():
                pos.today_bought_volume = 0
            self._log.info("day_advanced")
