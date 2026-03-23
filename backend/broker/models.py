"""Shared enums, frozen Pydantic models, and config loaders for broker+risk."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OrderDirection(StrEnum):
    """Trade direction."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Order type."""

    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(StrEnum):
    """Order lifecycle status."""

    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Frozen Pydantic models
# ---------------------------------------------------------------------------


class OrderResult(BaseModel):
    """Result of a place_order call."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    success: bool
    message: str = ""


class Order(BaseModel):
    """Immutable snapshot of an order."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    code: str
    price: float
    volume: int
    filled_volume: int = 0
    avg_fill_price: float = 0.0
    direction: OrderDirection
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime
    updated_at: datetime
    reject_reason: str | None = None


class Position(BaseModel):
    """Immutable snapshot of a stock position."""

    model_config = ConfigDict(frozen=True)

    code: str
    volume: int
    available_volume: int
    cost_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


class AccountInfo(BaseModel):
    """Immutable snapshot of account state."""

    model_config = ConfigDict(frozen=True)

    total_assets: float
    available_cash: float
    frozen_cash: float
    market_value: float
    total_pnl: float
    total_pnl_pct: float
    initial_capital: float


class Trade(BaseModel):
    """Immutable record of an executed trade."""

    model_config = ConfigDict(frozen=True)

    trade_id: str
    order_id: str
    code: str
    price: float
    volume: int
    amount: float
    direction: OrderDirection
    commission: float
    stamp_tax: float
    slippage_cost: float
    net_amount: float
    traded_at: datetime


class ValidationResult(BaseModel):
    """Result of a risk validation check."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    rule_name: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Broker config
# ---------------------------------------------------------------------------


class BrokerConfig(BaseModel):
    """MockBroker configuration loaded from broker.yaml."""

    model_config = ConfigDict(frozen=True)

    initial_capital: float = 1_000_000.0
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_bps: int = 2
    min_commission: float = 5.0


def load_broker_config(yaml_path: str | Path) -> BrokerConfig:
    """Load broker configuration from YAML file.

    Reads the section matching the 'active' key (default: 'mock').

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        pydantic.ValidationError: If the schema is invalid.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    active = raw.get("active", "mock")
    return BrokerConfig.model_validate(raw.get(active, {}))


# ---------------------------------------------------------------------------
# Risk config
# ---------------------------------------------------------------------------


class PositionLimitsConfig(BaseModel):
    """Position limit parameters."""

    model_config = ConfigDict(frozen=True)

    max_single_stock_pct: float = 0.20
    max_sector_pct: float = 0.40
    max_total_positions: int = 10
    price_deviation_limit: float = 0.05
    volume_lot_size: int = 100


class StopLossConfig(BaseModel):
    """Stop-loss parameters."""

    model_config = ConfigDict(frozen=True)

    single_stock_pct: float = 0.08
    portfolio_daily_pct: float = 0.05
    trailing_stop_pct: float = 0.10


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker parameters."""

    model_config = ConfigDict(frozen=True)

    daily_loss_limit_pct: float = 0.05
    consecutive_loss_count: int = 3
    cooldown_minutes: int = 60


class RiskConfig(BaseModel):
    """Complete risk engine configuration from risk.yaml."""

    model_config = ConfigDict(frozen=True)

    position_limits: PositionLimitsConfig
    stop_loss: StopLossConfig
    circuit_breaker: CircuitBreakerConfig


def load_risk_config(yaml_path: str | Path) -> RiskConfig:
    """Load risk configuration from YAML file.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        pydantic.ValidationError: If the schema is invalid.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return RiskConfig.model_validate(raw)
