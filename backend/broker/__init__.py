"""QuantMind broker: trading interface, MockBroker, and shared models.

``MockBroker`` is exposed via lazy ``__getattr__`` so a fresh import of
``backend.risk.*`` does not transitively load ``backend.data`` — E-003
added :mod:`backend.data.market_meta_provider` /
:mod:`backend.data.stock_metadata` to the MockBroker imports, and the
risk-isolation redline (``tests/test_risk_isolation_redline.py``)
forbids ``backend.risk`` from pulling ``backend.data`` into
``sys.modules``. Callers that need :class:`MockBroker` import it
directly from :mod:`backend.broker.mock_broker` or via attribute access
on this package — both paths still work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.broker.interface import IBroker
from backend.broker.models import (
    AccountInfo,
    BrokerConfig,
    CircuitBreakerConfig,
    Order,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    Trade,
    ValidationResult,
    load_broker_config,
    load_risk_config,
)

if TYPE_CHECKING:
    from backend.broker.mock_broker import MockBroker  # noqa: F401


def __getattr__(name: str) -> Any:
    if name == "MockBroker":
        from backend.broker.mock_broker import MockBroker as _MockBroker

        return _MockBroker
    raise AttributeError(f"module 'backend.broker' has no attribute {name!r}")


__all__ = [
    "AccountInfo",
    "BrokerConfig",
    "CircuitBreakerConfig",
    "IBroker",
    "MockBroker",
    "Order",
    "OrderDirection",
    "OrderResult",
    "OrderStatus",
    "OrderType",
    "Position",
    "PositionLimitsConfig",
    "RiskConfig",
    "StopLossConfig",
    "Trade",
    "ValidationResult",
    "load_broker_config",
    "load_risk_config",
]
