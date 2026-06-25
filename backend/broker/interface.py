"""IBroker abstract interface — all broker implementations conform to this."""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.broker.models import (
    AccountInfo,
    Order,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    Trade,
)


class IBroker(ABC):
    """Unified trading interface for MockBroker/QMTBroker/VNPyBroker."""

    @abstractmethod
    async def place_order(
        self,
        code: str,
        price: float,
        volume: int,
        direction: OrderDirection,
        order_type: OrderType,
    ) -> OrderResult:
        """Place a new order."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True if cancelled."""
        ...

    @abstractmethod
    async def get_positions(self) -> tuple[Position, ...]:
        """Get all current positions."""
        ...

    @abstractmethod
    async def get_account(self) -> AccountInfo:
        """Get current account information."""
        ...

    @abstractmethod
    async def get_orders(
        self, status: OrderStatus | None = None
    ) -> tuple[Order, ...]:
        """Get orders, optionally filtered by status."""
        ...

    @abstractmethod
    async def get_trades(self) -> tuple[Trade, ...]:
        """Get all executed trades."""
        ...

    @abstractmethod
    async def get_trade(self, trade_id: str | None) -> Trade | None:
        """Resolve a single trade by its ``trade_id``, or ``None`` if absent.

        B5 (production-hardening 2026-06-25): part of the broker contract so a
        caller can resolve the EXACT fill its ``place_order`` produced
        (:attr:`OrderResult.trade_id`) instead of racing on ``get_trades()[-1]``.
        Every implementation MUST expose it — the executor's fill-event
        persistence depends on it.
        """
        ...
