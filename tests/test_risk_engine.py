"""Exhaustive tests for RiskEngine — >95% coverage required."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.broker.models import (
    AccountInfo,
    Order,
    OrderDirection,
    OrderType,
    Position,
    RiskConfig,
    load_risk_config,
)
from backend.risk.engine import RiskEngine

SHANGHAI = ZoneInfo("Asia/Shanghai")

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


@pytest.fixture()
def risk_config(tmp_path: Path) -> RiskConfig:
    path = tmp_path / "risk.yaml"
    path.write_text(RISK_YAML, encoding="utf-8")
    return load_risk_config(path)


def _trading_time() -> dt.datetime:
    """Monday 10:00 Beijing time — valid trading hour."""
    return dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)


def _non_trading_time() -> dt.datetime:
    """Saturday 10:00 — not a trading day."""
    return dt.datetime(2026, 3, 21, 10, 0, tzinfo=SHANGHAI)


def _lunch_time() -> dt.datetime:
    """Monday 12:00 — lunch break."""
    return dt.datetime(2026, 3, 23, 12, 0, tzinfo=SHANGHAI)


def _make_order(
    code: str = "600519",
    price: float = 100.0,
    volume: int = 100,
    direction: OrderDirection = OrderDirection.BUY,
    order_type: OrderType = OrderType.LIMIT,
) -> Order:
    now = _trading_time()
    return Order(
        order_id="test", code=code, price=price, volume=volume,
        direction=direction, order_type=order_type,
        created_at=now, updated_at=now,
    )


def _make_account(
    total_assets: float = 1_000_000.0,
    available_cash: float = 500_000.0,
    positions: tuple[Position, ...] = (),
) -> AccountInfo:
    mv = sum(p.market_value for p in positions)
    return AccountInfo(
        total_assets=total_assets, available_cash=available_cash,
        frozen_cash=0.0, market_value=mv,
        total_pnl=total_assets - 1_000_000.0,
        total_pnl_pct=(total_assets - 1_000_000.0) / 1_000_000.0,
        initial_capital=1_000_000.0,
    )


def _make_position(
    code: str = "600519", volume: int = 100, cost_price: float = 100.0,
    market_value: float = 10_000.0,
) -> Position:
    return Position(
        code=code, volume=volume, available_volume=volume,
        cost_price=cost_price, market_value=market_value,
        unrealized_pnl=market_value - cost_price * volume,
        unrealized_pnl_pct=(market_value - cost_price * volume)
        / (cost_price * volume) if cost_price > 0 else 0.0,
    )


# -- Code validity --


class TestCheckCodeValidity:
    def test_valid_6_digit(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(code="600519"), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_code_too_short(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(code="6005"), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "code_validity"

    def test_code_too_long(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(code="6005199"), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "code_validity"

    def test_code_with_letters(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(code="60051a"), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed

    def test_code_with_prefix(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(code="sh600519"), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed


# -- Price reasonability --


class TestCheckPriceReasonability:
    def test_within_5_percent(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=104.0), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_at_5_percent_boundary(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=105.0), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed  # exactly at limit passes

    def test_exceeds_5_percent(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=106.0), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "price_reasonability"

    def test_below_negative_5_percent(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=94.0), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "price_reasonability"

    def test_market_order_skips(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=200.0, order_type=OrderType.MARKET),
            _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_no_prev_close_skips(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=100.0), _make_account(), (),
            prev_close=None, now=_trading_time(),
        )
        assert r.passed


# -- Volume validity --


class TestCheckVolumeValidity:
    def test_valid_100(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(volume=100), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_not_multiple_of_100(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(volume=150), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "volume_validity"

    def test_zero(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(volume=0), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed

    def test_negative(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(volume=-100), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed


# -- Fund sufficiency --


class TestCheckFundSufficiency:
    def test_buy_sufficient(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=10.0, volume=100),
            _make_account(available_cash=50_000.0), (),
            prev_close=10.0, now=_trading_time(),
        )
        assert r.passed

    def test_buy_insufficient(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=100.0, volume=10000),
            _make_account(available_cash=500_000.0), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "fund_sufficiency"

    def test_sell_skips_fund_check(self, risk_config: RiskConfig) -> None:
        pos = _make_position(volume=200, market_value=20_000.0)
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(direction=OrderDirection.SELL, volume=100),
            _make_account(available_cash=0.0), (pos,),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_sell_insufficient_available(
        self, risk_config: RiskConfig
    ) -> None:
        pos = Position(
            code="600519", volume=100, available_volume=50,
            cost_price=100.0, market_value=10_000.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(direction=OrderDirection.SELL, volume=100),
            _make_account(), (pos,),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "fund_sufficiency"

    def test_sell_no_position(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(direction=OrderDirection.SELL, volume=100),
            _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed

    def test_buy_exact_boundary(self, risk_config: RiskConfig) -> None:
        # Cash exactly covers cost + small overhead
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=10.0, volume=100),
            _make_account(available_cash=1001.0), (),
            prev_close=10.0, now=_trading_time(),
        )
        assert r.passed


# -- Position limit --


class TestCheckPositionLimit:
    def test_within_20_percent(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        # Buy 100 at 100 = 10,000. total_assets=1M. 1% well under 20%
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(total_assets=1_000_000.0), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_exceeds_20_percent(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        # Buy 2500 at 100 = 250,000. total_assets=1M. 25% > 20%
        r = engine.validate_order(
            _make_order(price=100.0, volume=2500),
            _make_account(total_assets=1_000_000.0, available_cash=500_000.0),
            (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "position_limit"

    def test_existing_plus_new_exceeds(self, risk_config: RiskConfig) -> None:
        existing = _make_position(market_value=150_000.0, volume=1500)
        engine = RiskEngine(risk_config)
        # Existing 150k + new 100*100=10k. But existing already 15%
        # Buy another 600 at 100 = 60k. Total = 210k/1M = 21% > 20%
        r = engine.validate_order(
            _make_order(price=100.0, volume=600),
            _make_account(total_assets=1_000_000.0), (existing,),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed

    def test_sell_skips(self, risk_config: RiskConfig) -> None:
        pos = _make_position(volume=200, market_value=200_000.0)
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(direction=OrderDirection.SELL, volume=100),
            _make_account(total_assets=1_000_000.0), (pos,),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_zero_total_assets(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=10.0, volume=100),
            _make_account(total_assets=0.0, available_cash=10_000.0), (),
            prev_close=10.0, now=_trading_time(),
        )
        assert not r.passed


# -- Total position limit --


class TestCheckTotalPositionLimit:
    def test_below_limit(self, risk_config: RiskConfig) -> None:
        positions = tuple(
            _make_position(code=f"60051{i}", market_value=10_000.0)
            for i in range(5)
        )
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(code="000001"), _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_at_limit_existing_stock(self, risk_config: RiskConfig) -> None:
        positions = tuple(
            _make_position(code=f"60051{i}", market_value=10_000.0)
            for i in range(10)
        )
        engine = RiskEngine(risk_config)
        # Buying more of an existing stock doesn't increase count
        r = engine.validate_order(
            _make_order(code="600510"), _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_exceeds_limit_new_stock(self, risk_config: RiskConfig) -> None:
        positions = tuple(
            _make_position(code=f"60051{i}", market_value=10_000.0)
            for i in range(10)
        )
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(code="000001"), _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "total_position_limit"

    def test_sell_skips(self, risk_config: RiskConfig) -> None:
        positions = tuple(
            _make_position(code=f"60051{i}", market_value=10_000.0)
            for i in range(10)
        )
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(code="600510", direction=OrderDirection.SELL),
            _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed


# -- Trading time --


class TestCheckTradingTime:
    def test_during_trading_hours(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_outside_hours(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(), _make_account(), (),
            prev_close=100.0, now=_non_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "trading_time"

    def test_lunch_break(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(), _make_account(), (),
            prev_close=100.0, now=_lunch_time(),
        )
        assert not r.passed

    def test_afternoon_session(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        afternoon = dt.datetime(2026, 3, 23, 14, 0, tzinfo=SHANGHAI)
        r = engine.validate_order(
            _make_order(), _make_account(), (),
            prev_close=100.0, now=afternoon,
        )
        assert r.passed


# -- Full chain --


class TestValidateOrderChain:
    def test_all_pass(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=500_000.0), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_first_failure_short_circuits(
        self, risk_config: RiskConfig
    ) -> None:
        engine = RiskEngine(risk_config)
        # Invalid code — should fail at check 1
        r = engine.validate_order(
            _make_order(code="bad"), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.rule_name == "code_validity"

    def test_sell_path(self, risk_config: RiskConfig) -> None:
        pos = _make_position(volume=200, market_value=20_000.0)
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(direction=OrderDirection.SELL, volume=100),
            _make_account(), (pos,),
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_returns_frozen_result(self, risk_config: RiskConfig) -> None:
        engine = RiskEngine(risk_config)
        r = engine.validate_order(
            _make_order(), _make_account(), (),
            prev_close=100.0, now=_trading_time(),
        )
        with pytest.raises(Exception):
            r.passed = False  # type: ignore[misc]


# -- Safety check --


class TestSafetyInvariant:
    def test_no_llm_imports(self) -> None:
        """Risk engine must never import LLM-related modules."""
        import importlib
        import inspect

        mod = importlib.import_module("backend.risk.engine")
        source = inspect.getsource(mod)
        forbidden = [
            "backend.llm", "backend.agents", "backend.mirofish",
            "openai", "langchain", "langgraph",
        ]
        for term in forbidden:
            assert term not in source, (
                f"Risk engine contains forbidden import: {term}"
            )
