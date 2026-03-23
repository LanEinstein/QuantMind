"""Tests for shared broker+risk models."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    ValidationResult,
    load_broker_config,
    load_risk_config,
)


class TestEnums:
    def test_order_direction_values(self) -> None:
        assert OrderDirection.BUY == "BUY"
        assert OrderDirection.SELL == "SELL"

    def test_order_type_values(self) -> None:
        assert OrderType.LIMIT == "LIMIT"
        assert OrderType.MARKET == "MARKET"

    def test_order_status_values(self) -> None:
        assert OrderStatus.PENDING == "PENDING"
        assert OrderStatus.FILLED == "FILLED"
        assert OrderStatus.CANCELLED == "CANCELLED"
        assert OrderStatus.REJECTED == "REJECTED"


class TestOrderResult:
    def test_create(self) -> None:
        r = OrderResult(order_id="abc", success=True, message="ok")
        assert r.success is True

    def test_frozen(self) -> None:
        r = OrderResult(order_id="abc", success=True)
        with pytest.raises(ValidationError):
            r.success = False  # type: ignore[misc]


class TestOrder:
    def test_create_with_defaults(self) -> None:
        now = datetime(2026, 3, 23, tzinfo=UTC)
        o = Order(
            order_id="o1", code="600519", price=1800.0, volume=100,
            direction=OrderDirection.BUY, order_type=OrderType.LIMIT,
            created_at=now, updated_at=now,
        )
        assert o.status == OrderStatus.PENDING
        assert o.filled_volume == 0
        assert o.reject_reason is None

    def test_frozen(self) -> None:
        now = datetime(2026, 3, 23, tzinfo=UTC)
        o = Order(
            order_id="o1", code="600519", price=10.0, volume=100,
            direction=OrderDirection.BUY, order_type=OrderType.LIMIT,
            created_at=now, updated_at=now,
        )
        with pytest.raises(ValidationError):
            o.status = OrderStatus.FILLED  # type: ignore[misc]


class TestPosition:
    def test_create(self) -> None:
        p = Position(
            code="600519", volume=100, available_volume=100,
            cost_price=10.0, market_value=1100.0,
            unrealized_pnl=100.0, unrealized_pnl_pct=0.10,
        )
        assert p.code == "600519"

    def test_frozen(self) -> None:
        p = Position(
            code="600519", volume=100, available_volume=100,
            cost_price=10.0, market_value=1000.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        with pytest.raises(ValidationError):
            p.volume = 200  # type: ignore[misc]


class TestAccountInfo:
    def test_create(self) -> None:
        a = AccountInfo(
            total_assets=1_000_000.0, available_cash=900_000.0,
            frozen_cash=0.0, market_value=100_000.0,
            total_pnl=0.0, total_pnl_pct=0.0,
            initial_capital=1_000_000.0,
        )
        assert a.total_assets == 1_000_000.0

    def test_frozen(self) -> None:
        a = AccountInfo(
            total_assets=1e6, available_cash=1e6, frozen_cash=0.0,
            market_value=0.0, total_pnl=0.0, total_pnl_pct=0.0,
            initial_capital=1e6,
        )
        with pytest.raises(ValidationError):
            a.available_cash = 0.0  # type: ignore[misc]


class TestTrade:
    def test_create(self) -> None:
        now = datetime(2026, 3, 23, tzinfo=UTC)
        t = Trade(
            trade_id="t1", order_id="o1", code="600519",
            price=10.02, volume=100, amount=1002.0,
            direction=OrderDirection.BUY, commission=5.0,
            stamp_tax=0.0, slippage_cost=2.0,
            net_amount=1007.0, traded_at=now,
        )
        assert t.commission == 5.0

    def test_frozen(self) -> None:
        now = datetime(2026, 3, 23, tzinfo=UTC)
        t = Trade(
            trade_id="t1", order_id="o1", code="600519",
            price=10.0, volume=100, amount=1000.0,
            direction=OrderDirection.BUY, commission=5.0,
            stamp_tax=0.0, slippage_cost=0.0,
            net_amount=1005.0, traded_at=now,
        )
        with pytest.raises(ValidationError):
            t.price = 20.0  # type: ignore[misc]


class TestValidationResult:
    def test_passed(self) -> None:
        r = ValidationResult(passed=True)
        assert r.rule_name == ""
        assert r.message == ""

    def test_failed(self) -> None:
        r = ValidationResult(
            passed=False, rule_name="fund_sufficiency",
            message="Insufficient funds",
        )
        assert not r.passed
        assert r.rule_name == "fund_sufficiency"

    def test_frozen(self) -> None:
        r = ValidationResult(passed=True)
        with pytest.raises(ValidationError):
            r.passed = False  # type: ignore[misc]


BROKER_YAML = """\
active: mock
mock:
  initial_capital: 1000000
  commission_rate: 0.0003
  stamp_tax_rate: 0.001
  slippage_bps: 2
  min_commission: 5.0
"""

RISK_YAML = """\
position_limits:
  max_single_stock_pct: 0.20
  max_sector_pct: 0.40
  max_total_positions: 10
  price_deviation_limit: 0.05
  volume_lot_size: 100
stop_loss:
  single_stock_pct: 0.08
  portfolio_daily_pct: 0.05
  trailing_stop_pct: 0.10
circuit_breaker:
  daily_loss_limit_pct: 0.05
  consecutive_loss_count: 3
  cooldown_minutes: 60
"""


class TestBrokerConfig:
    def test_load_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "broker.yaml"
        path.write_text(BROKER_YAML, encoding="utf-8")
        cfg = load_broker_config(path)
        assert cfg.initial_capital == 1_000_000
        assert cfg.commission_rate == 0.0003
        assert cfg.min_commission == 5.0

    def test_defaults(self) -> None:
        cfg = BrokerConfig()
        assert cfg.initial_capital == 1_000_000
        assert cfg.slippage_bps == 2

    def test_frozen(self) -> None:
        cfg = BrokerConfig()
        with pytest.raises(ValidationError):
            cfg.initial_capital = 0.0  # type: ignore[misc]

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_broker_config(tmp_path / "nope.yaml")


class TestRiskConfig:
    def test_load_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "risk.yaml"
        path.write_text(RISK_YAML, encoding="utf-8")
        cfg = load_risk_config(path)
        assert cfg.position_limits.max_single_stock_pct == 0.20
        assert cfg.stop_loss.single_stock_pct == 0.08
        assert cfg.circuit_breaker.cooldown_minutes == 60

    def test_nested_access(self, tmp_path: Path) -> None:
        path = tmp_path / "risk.yaml"
        path.write_text(RISK_YAML, encoding="utf-8")
        cfg = load_risk_config(path)
        assert cfg.position_limits.volume_lot_size == 100
        assert cfg.position_limits.price_deviation_limit == 0.05

    def test_frozen(self, tmp_path: Path) -> None:
        path = tmp_path / "risk.yaml"
        path.write_text(RISK_YAML, encoding="utf-8")
        cfg = load_risk_config(path)
        with pytest.raises(ValidationError):
            cfg.position_limits.max_single_stock_pct = 0.5  # type: ignore[misc]

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_risk_config(tmp_path / "nope.yaml")

    def test_invalid_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("wrong_key: true", encoding="utf-8")
        with pytest.raises(ValidationError):
            load_risk_config(path)
