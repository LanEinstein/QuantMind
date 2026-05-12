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
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board, StockMetadata

SHANGHAI = ZoneInfo("Asia/Shanghai")

# Legacy fixture — pre-P0-7 thresholds. Used by check 1-7 tests to keep
# their boundary cases unchanged after the 14-check expansion (P0-7
# tightens single-stock from 0.20 → 0.15 etc; the legacy yaml lets us
# isolate semantic regressions from threshold-driven failures).
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

# P0-7 locked thresholds — used by 14-check tests.
RISK_YAML_P0_7 = """\
position_limits:
  max_single_stock_pct: 0.15
  max_sector_pct: 0.40
  max_total_positions: 10
  price_deviation_limit: 0.05
  volume_lot_size: 100
  max_total_position_pct: 0.70
  max_single_instruction_amount: 50000
  max_daily_new_instructions: 5
stop_loss:
  single_stock_pct: 0.08
  portfolio_daily_pct: 0.05
  trailing_stop_pct: 0.10
circuit_breaker:
  daily_loss_limit_pct: 0.05
  consecutive_loss_count: 3
  cooldown_minutes: 60
  halt_priority_order: ["daily_loss", "consecutive_loss"]
  apply_to_sell_orders: false
universe:
  allowed_boards: ["sh_main", "sz_main", "chuangye", "etf"]
  forbidden_st: true
  forbid_buy_at_limit_up: true
  forbid_sell_at_limit_down: true
  price_limit_pct_by_board:
    sh_main: 0.10
    sz_main: 0.10
    chuangye: 0.20
    etf: 0.10
"""


@pytest.fixture()
def risk_config(tmp_path: Path) -> RiskConfig:
    path = tmp_path / "risk.yaml"
    path.write_text(RISK_YAML, encoding="utf-8")
    return load_risk_config(path)


@pytest.fixture()
def risk_config_p0_7(tmp_path: Path) -> RiskConfig:
    path = tmp_path / "risk_p0_7.yaml"
    path.write_text(RISK_YAML_P0_7, encoding="utf-8")
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

    def test_no_data_layer_imports(self) -> None:
        """P0-7 §2 redline 9 — backend/risk must not import backend.data."""
        import importlib
        import inspect

        mod = importlib.import_module("backend.risk.engine")
        source = inspect.getsource(mod)
        assert "backend.data" not in source


# ===========================================================================
# P0-7 14-check expansion (checks 8-14)
# ===========================================================================


def _stock_meta(
    code: str = "600519",
    name: str = "贵州茅台",
    board: Board = Board.SH_MAIN,
    is_st: bool = False,
    instrument_type: str = "stock",
) -> StockMetadata:
    return StockMetadata(
        code=code, name=name, board=board, is_st=is_st,
        instrument_type=instrument_type,
    )


def _daily_state(
    *,
    today_new_instruction_count: int = 0,
    today_portfolio_pnl_pct: float = 0.0,
    last_3_trade_pnls: tuple[float, ...] = (),
    current_price: float | None = 100.0,
    is_in_halt_cooldown: bool = False,
    halt_until: dt.datetime | None = None,
) -> DailyTradingState:
    return DailyTradingState(
        today_new_instruction_count=today_new_instruction_count,
        today_portfolio_pnl_pct=today_portfolio_pnl_pct,
        last_3_trade_pnls=last_3_trade_pnls,
        current_price=current_price,
        is_in_halt_cooldown=is_in_halt_cooldown,
        halt_until=halt_until,
    )


# -- Check 8: total_position_pct --


class TestCheckTotalPositionPct:
    def test_within_limit(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # New order 100 * 100 = 10k on 1M account = 1% → well under 70%
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(total_assets=1_000_000.0, available_cash=500_000.0),
            (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_exceeds_limit(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # Existing 600k other_value + 150k new = 75% > 70%.
        # First buy a stock that already counts as 14% (within single-stock
        # 15%) so check 5 passes; then total breaches 70%.
        existing = Position(
            code="000001", volume=10_000, available_volume=10_000,
            cost_price=60.0, market_value=600_000.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        r = engine.validate_order(
            _make_order(code="600519", price=100.0, volume=1_500),
            _make_account(
                total_assets=1_000_000.0, available_cash=200_000.0,
            ),
            (existing,),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "total_position_pct"

    def test_sell_skips(self, risk_config_p0_7: RiskConfig) -> None:
        pos = _make_position(volume=200, market_value=20_000.0)
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(direction=OrderDirection.SELL, volume=100),
            _make_account(total_assets=1_000_000.0), (pos,),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_existing_other_positions_aggregated(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """Existing positions (other codes) contribute their market_value
        to the post-trade total — verify the aggregation is exhaustive
        rather than only revaluing the order's own code."""
        engine = RiskEngine(risk_config_p0_7)
        # 5 holdings × 130k each = 650k other_value; new order 100*100 = 10k.
        # Total after = 660k / 1M = 66% < 70% (PASS).
        others = tuple(
            Position(
                code=f"00000{i + 1}", volume=1_000, available_volume=1_000,
                cost_price=130.0, market_value=130_000.0,
                unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
            )
            for i in range(5)
        )
        r = engine.validate_order(
            _make_order(code="600519", price=100.0, volume=100),
            _make_account(
                total_assets=1_000_000.0, available_cash=200_000.0,
            ),
            others,
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(code="600519"),
        )
        assert r.passed

    def test_zero_total_assets_caught_by_check5(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """Check 5 (position_limit) intercepts zero-asset BUY first; the
        defensive guard inside check 8 is kept (mirrors the decision doc
        verbatim) but is unreachable in practice. Lock the short-circuit
        behavior so a re-ordering does not silently hide a real bug."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=10.0, volume=100),
            _make_account(total_assets=0.0, available_cash=10_000.0), (),
            prev_close=10.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "position_limit"


# -- Check 9: single_instruction_amount --


class TestCheckSingleInstructionAmount:
    def test_within_limit(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # 100 * 100 = 10k <= 50k limit
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_at_boundary(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # 500 * 100 = 50,000 exactly at boundary → PASS
        r = engine.validate_order(
            _make_order(price=500.0, volume=100),
            _make_account(
                total_assets=1_000_000.0, available_cash=200_000.0,
            ),
            (), prev_close=500.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_exceeds_limit(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # 1000 * 100 = 100,000 > 50,000 limit, but check 9 only fires if
        # earlier checks pass — keep single-stock under 15% to isolate.
        # 100 * 100 = 10k = 1% of 1M (passes check 5/8); but amount needs
        # to actually breach so use 600 * 100 = 60,000 with 1M account
        # gives 6% < 15% (check 5 passes), but 60k > 50k limit.
        r = engine.validate_order(
            _make_order(price=600.0, volume=100),
            _make_account(
                total_assets=1_000_000.0, available_cash=200_000.0,
            ),
            (), prev_close=600.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "single_instruction_amount"

    def test_applies_to_sell(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # SELL 100 shares at price=600 → 60k amount > 50k limit.
        pos = Position(
            code="600519", volume=200, available_volume=200,
            cost_price=600.0, market_value=120_000.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        r = engine.validate_order(
            _make_order(
                direction=OrderDirection.SELL, price=600.0, volume=100,
            ),
            _make_account(total_assets=1_000_000.0), (pos,),
            prev_close=600.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "single_instruction_amount"


# -- Check 10: daily_new_instruction_count --


class TestCheckDailyNewInstructionCount:
    def test_below_limit(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(today_new_instruction_count=3),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_at_limit_rejects(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(today_new_instruction_count=5),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "daily_new_instruction_count"

    def test_above_limit_rejects(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(today_new_instruction_count=10),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "daily_new_instruction_count"


# -- Check 11: universe_whitelist --


class TestCheckUniverseWhitelist:
    def test_sh_main_allowed(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="600519", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(code="600519", board=Board.SH_MAIN),
        )
        assert r.passed

    def test_etf_allowed(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="510300", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(
                code="510300", board=Board.ETF, instrument_type="etf",
            ),
        )
        assert r.passed

    def test_kchuang_blocked(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="688981", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(code="688981", board=Board.KCHUANG),
        )
        assert not r.passed
        assert r.rule_name == "universe_whitelist"

    def test_beijiao_blocked(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="830799", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(code="830799", board=Board.BEIJIAO),
        )
        assert not r.passed
        assert r.rule_name == "universe_whitelist"

    def test_convertible_bond_blocked(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="113042", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(
                code="113042", board=Board.CONVERTIBLE_BOND,
                instrument_type="bond",
            ),
        )
        assert not r.passed
        assert r.rule_name == "universe_whitelist"

    def test_st_blocked(self, risk_config_p0_7: RiskConfig) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="600119", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(
                code="600119", name="*ST 西水", board=Board.SH_MAIN,
                is_st=True,
            ),
        )
        assert not r.passed
        assert r.rule_name == "universe_whitelist"

    def test_unknown_board_blocked(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="999999", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(code="999999", board=Board.UNKNOWN),
        )
        assert not r.passed
        assert r.rule_name == "universe_whitelist"

    def test_stock_meta_none_rejects_in_builder_mode(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """P0-7 §2 redline 13 fail-closed when stock_meta absent."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="600519", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=None,
        )
        assert not r.passed
        assert r.rule_name == "universe_whitelist"


# -- Check 12: limit_up_down_block --


class TestCheckLimitUpDownBlock:
    def test_buy_below_limit_passes(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # prev_close=100, sh_main 10% → upper=110. current 105 < 110 → PASS.
        r = engine.validate_order(
            _make_order(price=105.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=105.0),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert r.passed

    def test_buy_at_limit_up_blocked(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=110.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=110.0),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"

    def test_chuangye_20pct_limit(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # ChiNext 20%: upper=120. current 115 → PASS.
        r = engine.validate_order(
            _make_order(code="300750", price=115.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=115.0),
            stock_meta=_stock_meta(
                code="300750", board=Board.CHUANGYE,
            ),
        )
        assert r.passed

    def test_chuangye_20pct_at_limit_blocked(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # ChiNext 20%: upper=120. current 120 → blocked.
        r = engine.validate_order(
            _make_order(code="300750", price=120.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=120.0),
            stock_meta=_stock_meta(
                code="300750", board=Board.CHUANGYE,
            ),
        )
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"

    def test_sell_at_limit_down_blocked(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # lower = 90. SELL at current 90 → blocked.
        pos = Position(
            code="600519", volume=200, available_volume=200,
            cost_price=100.0, market_value=18_000.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        r = engine.validate_order(
            _make_order(
                direction=OrderDirection.SELL, price=90.0, volume=100,
            ),
            _make_account(total_assets=1_000_000.0), (pos,),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=90.0),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"

    def test_current_price_none_fail_closed(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=None),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"

    def test_prev_close_none_fail_closed(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=None, now=_trading_time(),
            daily_state=_daily_state(current_price=100.0),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"

    def test_current_price_nan_fail_closed(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """Pandas/akshare can surface missing quotes as NaN rather than
        None; the engine must treat both as fail-closed (codex cycle 2
        P1) — otherwise NaN comparisons would silently pass through."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=float("nan")),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"

    def test_prev_close_nan_fail_closed(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=float("nan"), now=_trading_time(),
            daily_state=_daily_state(current_price=100.0),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        # check 2 sees NaN first and short-circuits PASS (cannot
        # evaluate deviation); check 12 then rejects fail-closed.
        assert r.rule_name == "limit_up_down_block"

    def test_stock_meta_none_fail_closed(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        # When stock_meta is None, check 11 actually fires first and
        # short-circuits — that's fine, the chain ends at the first
        # fail-closed rule, both fall under P0-7 redline 13.
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=100.0),
            stock_meta=None,
        )
        assert not r.passed
        assert r.rule_name == "universe_whitelist"


# -- Check 13: daily_loss_halt --


class TestCheckDailyLossHalt:
    def test_within_threshold(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(today_portfolio_pnl_pct=-0.03),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_breached_threshold_rejects_buy(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(today_portfolio_pnl_pct=-0.06),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "daily_loss_halt"

    def test_sell_bypasses_halt(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        # apply_to_sell_orders=false (P0-7 §4.6) — SELL exits a halt
        # rather than being locked in.
        pos = _make_position(volume=200, market_value=20_000.0)
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(direction=OrderDirection.SELL, volume=100),
            _make_account(total_assets=1_000_000.0), (pos,),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(today_portfolio_pnl_pct=-0.10),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_in_cooldown_rejects_buy(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        until = dt.datetime(2026, 3, 23, 11, 0, tzinfo=SHANGHAI)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                today_portfolio_pnl_pct=-0.02,
                is_in_halt_cooldown=True,
                halt_until=until,
            ),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "daily_loss_halt"
        assert "11:00:00" in r.message

    def test_nan_pnl_fail_closed(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """A missing or zero day-open NAV could leave the assembler
        passing ``NaN`` for ``today_portfolio_pnl_pct``. Strict ``<=``
        with NaN silently returns False and lets BUYs through — fail
        closed instead. Codex cycle 4 P2."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                today_portfolio_pnl_pct=float("nan"),
            ),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "daily_loss_halt"

    def test_inf_pnl_fail_closed(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                today_portfolio_pnl_pct=float("inf"),
            ),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "daily_loss_halt"

    def test_in_cooldown_no_until(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                is_in_halt_cooldown=True, halt_until=None,
            ),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "daily_loss_halt"
        assert "unknown" in r.message


# -- Check 14: consecutive_loss_halt --


class TestCheckConsecutiveLossHalt:
    def test_insufficient_history_passes(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # Only 2 of N=3 PnLs known → cannot evaluate → PASS.
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(last_3_trade_pnls=(-1.0, -2.0)),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_three_losses_rejects(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                last_3_trade_pnls=(-1.0, -2.0, -3.0),
            ),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "consecutive_loss_halt"

    def test_three_losses_rejects_sell_too(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """Check 14 is direction-agnostic, unlike check 13."""
        pos = _make_position(volume=200, market_value=20_000.0)
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(direction=OrderDirection.SELL, volume=100),
            _make_account(total_assets=1_000_000.0), (pos,),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                last_3_trade_pnls=(-1.0, -2.0, -3.0), current_price=100.0,
            ),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "consecutive_loss_halt"

    def test_mixed_pnls_passes(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                last_3_trade_pnls=(-1.0, 2.0, -3.0),
            ),
            stock_meta=_stock_meta(),
        )
        assert r.passed

    def test_only_last_n_considered(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        # First two losses then one win → last 3 are (-2, -3, 1) → PASS.
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                last_3_trade_pnls=(-5.0, -4.0, -3.0, -2.0, 1.0),
            ),
            stock_meta=_stock_meta(),
        )
        assert r.passed


# -- Full 14-check chain --


class TestValidateOrder14Chain:
    def test_legacy_mode_runs_only_7(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """daily_state=None + stock_meta=None → legacy 7-check (PASS)."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            # No daily_state / stock_meta — legacy mode.
        )
        assert r.passed

    def test_builder_mode_full_pass(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """All 14 checks pass with a clean order under all P0-7 limits."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(
                total_assets=1_000_000.0, available_cash=500_000.0,
            ),
            (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(
                today_new_instruction_count=2,
                today_portfolio_pnl_pct=-0.01,
                last_3_trade_pnls=(1.0, -2.0, 3.0),
                current_price=100.0,
            ),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert r.passed

    def test_first_failure_short_circuits_in_builder_mode(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        # Invalid code → check 1 fails before any 8-14 runs.
        r = engine.validate_order(
            _make_order(code="bad"),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=_stock_meta(code="bad"),
        )
        assert not r.passed
        assert r.rule_name == "code_validity"

    def test_price_reasonability_uses_board_table(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """ChiNext order at +15% prev_close passes (board limit 20%)."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="300750", price=115.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=115.0),
            stock_meta=_stock_meta(
                code="300750", board=Board.CHUANGYE,
            ),
        )
        assert r.passed

    def test_price_reasonability_global_fallback_without_meta(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """Legacy mode (no stock_meta) falls back to global 5%."""
        engine = RiskEngine(risk_config_p0_7)
        # +6% deviation; legacy 5% global limit → REJECT.
        r = engine.validate_order(
            _make_order(price=106.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "price_reasonability"

    def test_price_reasonability_at_exchange_rounded_limit(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """prev_close=1.65, sh_main 10% → upper exchange limit = 1.82
        (raw 10.3%). Check 2 must accept this published-limit price
        even though the raw deviation exceeds 10%. Codex cycle 4 P2."""
        engine = RiskEngine(risk_config_p0_7)
        # SELL at the upper-limit price; pos needed so check 4 passes.
        pos = Position(
            code="600519", volume=200, available_volume=200,
            cost_price=1.65, market_value=330.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        r = engine.validate_order(
            _make_order(
                direction=OrderDirection.SELL, price=1.82, volume=100,
            ),
            _make_account(total_assets=1_000_000.0), (pos,),
            prev_close=1.65, now=_trading_time(),
            daily_state=_daily_state(current_price=1.81),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        # Check 2 must pass — exchange-rounded limit (1.82) accepts
        # 1.82. Check 12's at-limit rejection won't fire because
        # current_price=1.81 is below the upper limit.
        assert r.passed

    def test_price_reasonability_above_exchange_limit_rejects(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """Price one cent above the exchange-rounded upper limit must
        still REJECT — the check is "outside [lower, upper]"."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=1.83, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=1.65, now=_trading_time(),
            daily_state=_daily_state(current_price=1.83),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert not r.passed
        assert r.rule_name == "price_reasonability"


# -- Mixed builder mode (one of daily_state / stock_meta None) --


class TestMixedBuilderMode:
    """When exactly one of daily_state / stock_meta is provided, the
    engine still runs the full 14-check chain (legacy mode requires BOTH
    None). Each new check's own None handling takes effect — checks
    10/13/14 PASS when daily_state is missing; check 11 fail-closes when
    stock_meta is missing."""

    def test_stock_meta_only_check10_passes_via_none(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=None,
            stock_meta=_stock_meta(),
        )
        # Checks 10/13/14 backward-compat PASS when daily_state is None.
        # Check 12 fail-closes because current_price is unavailable.
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"

    def test_daily_state_only_check11_fail_closed(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(),
            stock_meta=None,
        )
        # stock_meta=None hits check 11 (universe_whitelist) fail-closed.
        assert not r.passed
        assert r.rule_name == "universe_whitelist"


# -- Universe config edge: both forbid_* flags off --


class TestLimitUpDownBlockBothFlagsOff:
    def test_both_flags_off_short_circuits(self) -> None:
        """If both forbid_buy_at_limit_up and forbid_sell_at_limit_down
        are False (universe relaxed via amendment), check 12 short-
        circuits to PASS without requiring price context."""
        from backend.broker.models import (
            CircuitBreakerConfig,
            PositionLimitsConfig,
            RiskConfig,
            StopLossConfig,
            UniverseConfig,
        )

        relaxed_universe = UniverseConfig(
            allowed_boards=("sh_main", "sz_main", "chuangye", "etf"),
            forbidden_st=True,
            forbid_buy_at_limit_up=False,
            forbid_sell_at_limit_down=False,
            price_limit_pct_by_board={
                "sh_main": 0.10, "sz_main": 0.10,
                "chuangye": 0.20, "etf": 0.10,
            },
        )
        cfg = RiskConfig(
            position_limits=PositionLimitsConfig(),
            stop_loss=StopLossConfig(),
            circuit_breaker=CircuitBreakerConfig(),
            universe=relaxed_universe,
        )
        engine = RiskEngine(cfg)
        # Even at limit-up price, check 12 short-circuits → PASS.
        r = engine.validate_order(
            _make_order(price=110.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=110.0),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert r.passed

    def test_buy_at_rounded_limit_blocked(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """prev_close 2.23 → raw upper = 2.453, exchange rounds to 2.45.
        BUY at 2.45 must be blocked (codex cycle 1 P1)."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="600519", price=2.45, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=2.23, now=_trading_time(),
            daily_state=_daily_state(current_price=2.45),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"

    def test_sell_at_rounded_limit_blocked(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """prev_close 1.65 → raw lower = 1.485, exchange rounds to 1.49.
        SELL at 1.49 must be blocked."""
        pos = Position(
            code="600519", volume=200, available_volume=200,
            cost_price=1.65, market_value=300.0,
            unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
        )
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(
                direction=OrderDirection.SELL, price=1.49, volume=100,
            ),
            _make_account(total_assets=1_000_000.0), (pos,),
            prev_close=1.65, now=_trading_time(),
            daily_state=_daily_state(current_price=1.49),
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert not r.passed
        assert r.rule_name == "limit_up_down_block"


# -- Inclusive halt-threshold boundary (codex cycle 1 P2) --


class TestDailyLossHaltInclusiveBoundary:
    def test_exact_threshold_rejects(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """today_portfolio_pnl_pct == -0.05 must match the breaker's
        ``<=`` boundary and reject BUY orders."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(today_portfolio_pnl_pct=-0.05),
            stock_meta=_stock_meta(),
        )
        assert not r.passed
        assert r.rule_name == "daily_loss_halt"


# -- Stock-meta code mismatch (codex cycle 1 P1) --


class TestStockMetaCodeMismatch:
    def test_mismatch_rejects_before_any_check(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        """If builder hands metadata for a different stock, reject up-
        front so check 2 / check 11 / check 12 cannot consult the wrong
        board's price-limit / universe entry."""
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="688001", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=100.0),
            stock_meta=_stock_meta(
                code="600519", board=Board.SH_MAIN,
            ),
        )
        assert not r.passed
        assert r.rule_name == "stock_meta_mismatch"

    def test_match_proceeds(
        self, risk_config_p0_7: RiskConfig,
    ) -> None:
        engine = RiskEngine(risk_config_p0_7)
        r = engine.validate_order(
            _make_order(code="600519", price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=_daily_state(current_price=100.0),
            stock_meta=_stock_meta(code="600519", board=Board.SH_MAIN),
        )
        assert r.passed


    def test_both_flags_off_with_daily_state_none(self) -> None:
        """Path through check 12 (short-circuit) → checks 13/14 with
        ``daily_state=None`` exercises the backward-compat PASS arms of
        13 and 14 even when stock_meta is supplied."""
        from backend.broker.models import (
            CircuitBreakerConfig,
            PositionLimitsConfig,
            RiskConfig,
            StopLossConfig,
            UniverseConfig,
        )

        relaxed_universe = UniverseConfig(
            allowed_boards=("sh_main", "sz_main", "chuangye", "etf"),
            forbidden_st=True,
            forbid_buy_at_limit_up=False,
            forbid_sell_at_limit_down=False,
            price_limit_pct_by_board={
                "sh_main": 0.10, "sz_main": 0.10,
                "chuangye": 0.20, "etf": 0.10,
            },
        )
        cfg = RiskConfig(
            position_limits=PositionLimitsConfig(),
            stop_loss=StopLossConfig(),
            circuit_breaker=CircuitBreakerConfig(),
            universe=relaxed_universe,
        )
        engine = RiskEngine(cfg)
        r = engine.validate_order(
            _make_order(price=100.0, volume=100),
            _make_account(available_cash=200_000.0), (),
            prev_close=100.0, now=_trading_time(),
            daily_state=None,
            stock_meta=_stock_meta(board=Board.SH_MAIN),
        )
        assert r.passed
