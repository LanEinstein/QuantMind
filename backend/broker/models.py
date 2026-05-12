"""Shared enums, frozen Pydantic models, and config loaders for broker+risk."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

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
    """Position limit parameters (P0-7 locked; runtime immutable)."""

    model_config = ConfigDict(frozen=True)

    max_single_stock_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    max_sector_pct: float = Field(default=0.40, ge=0.0, le=1.0)
    max_total_positions: int = Field(default=10, ge=1)
    price_deviation_limit: float = Field(default=0.05, ge=0.0, le=1.0)
    volume_lot_size: int = Field(default=100, ge=1)
    max_total_position_pct: float = Field(default=0.70, ge=0.0, le=1.0)
    max_single_instruction_amount: float = Field(default=50_000.0, gt=0.0)
    max_daily_new_instructions: int = Field(default=5, ge=1)


class StopLossConfig(BaseModel):
    """Stop-loss parameters."""

    model_config = ConfigDict(frozen=True)

    single_stock_pct: float = 0.08
    portfolio_daily_pct: float = 0.05
    trailing_stop_pct: float = 0.10


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker parameters (P0-7 locked; runtime immutable)."""

    model_config = ConfigDict(frozen=True)

    daily_loss_limit_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    consecutive_loss_count: int = Field(default=3, ge=1)
    cooldown_minutes: int = Field(default=60, ge=1)
    halt_priority_order: tuple[str, ...] = Field(
        default=("daily_loss", "consecutive_loss"),
    )
    apply_to_sell_orders: bool = Field(default=False)


class UniverseConfig(BaseModel):
    """Universe whitelist + price-limit gating (P0-7 locked; runtime immutable).

    Why a separate sub-config: keeps board-keyed price-limit table next to the
    universe rules that consume it (check 11/12), so amending one of them
    cannot silently desync the other.

    ``price_limit_pct_by_board`` is wrapped in a ``MappingProxyType`` after
    validation so ``frozen=True`` is not bypassed by per-key mutation
    (``cfg.universe.price_limit_pct_by_board["sh_main"] = 0.99``). Codex
    cycle 1 P1.
    """

    model_config = ConfigDict(frozen=True)

    allowed_boards: tuple[str, ...] = Field(
        default=("sh_main", "sz_main", "chuangye", "etf"),
    )
    forbidden_st: bool = Field(default=True)
    forbid_buy_at_limit_up: bool = Field(default=True)
    forbid_sell_at_limit_down: bool = Field(default=True)
    price_limit_pct_by_board: dict[str, float] = Field(
        default_factory=lambda: {
            "sh_main": 0.10,
            "sz_main": 0.10,
            "chuangye": 0.20,
            "etf": 0.10,
        },
    )

    @model_validator(mode="after")
    def _seal_price_limit_map(self) -> UniverseConfig:
        current = self.price_limit_pct_by_board
        if isinstance(current, MappingProxyType):
            return self
        # ``frozen=True`` blocks normal attribute assignment, so use
        # ``object.__setattr__`` to swap in the read-only proxy. The
        # MappingProxyType satisfies ``Mapping[str, float]`` for read
        # access (``cfg["sh_main"]``) while raising TypeError on any
        # mutation — keeping the P0-7 §2 redline 1 runtime-immutability
        # guarantee intact.
        object.__setattr__(
            self,
            "price_limit_pct_by_board",
            MappingProxyType(dict(current)),
        )
        return self

    @field_serializer("price_limit_pct_by_board")
    def _serialize_price_limit_map(
        self, value: Any,
    ) -> dict[str, float]:
        # Pydantic v2 cannot natively serialize ``MappingProxyType`` —
        # ``model_dump(mode="json")`` would raise
        # ``PydanticSerializationError``. Convert back to a plain dict
        # on the way out; the proxy still blocks mutation while in-
        # memory. Codex cycle 2 P2.
        return dict(value)


class RiskConfig(BaseModel):
    """Complete risk engine configuration from risk.yaml (P0-7 locked)."""

    model_config = ConfigDict(frozen=True)

    position_limits: PositionLimitsConfig
    stop_loss: StopLossConfig
    circuit_breaker: CircuitBreakerConfig
    universe: UniverseConfig = Field(default_factory=UniverseConfig)


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
