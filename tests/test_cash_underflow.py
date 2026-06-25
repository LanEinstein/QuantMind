"""Cash-underflow fail-closed guard (Batch-3 B4, 2026-06-23).

The BUY-fill cash-underflow branch is unreachable in normal operation (the
affordability preflight + the broker lock + synchronous fill guarantee
``frozen_amount == net_amount``). If the invariant is ever broken it must fail
CLOSED (raise) rather than the old silent ``cash = 0.0`` clamp that fabricated
phantom cash and destroyed the ``cash + frozen + market_value`` audit identity.
"""

from __future__ import annotations

import datetime as dt

import pytest

from backend.broker.cost_calculator import calculate_cost
from backend.broker.mock_broker import (
    CashUnderflowError,
    MockBroker,
    _MutableOrder,
)
from backend.broker.models import BrokerConfig, OrderDirection, OrderType
from backend.data.stock_metadata import Board
from backend.utils.trading_hours import SHANGHAI

_NOW = dt.datetime(2026, 5, 15, 10, 0, tzinfo=SHANGHAI)


def test_cash_underflow_fails_closed_not_silent_zero() -> None:
    broker = MockBroker(
        config=BrokerConfig(initial_capital=1_000.0), now_func=lambda: _NOW
    )
    cost = calculate_cost(
        code="600519",
        board=Board.SH_MAIN,
        order_price=10.0,
        volume=100,
        direction=OrderDirection.BUY,
        config=broker._config,  # noqa: SLF001
    )
    # frozen_amount far below the real net cost → the BUY settle drives cash
    # negative (the unreachable-in-prod invariant breach).
    order = _MutableOrder(
        order_id="ord-x",
        code="600519",
        price=10.0,
        volume=100,
        direction=OrderDirection.BUY,
        order_type=OrderType.LIMIT,
        frozen_amount=1.0,
    )
    broker._cash = 0.0  # noqa: SLF001
    with pytest.raises(CashUnderflowError):
        broker._fill_order(order, cost, _NOW)  # noqa: SLF001
