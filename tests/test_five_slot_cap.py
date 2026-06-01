"""V-001 — ≤5 concurrent-position hard cap (check#6 max_total_positions 10→5).

P0-7-amendment-2026-06-01-five-slot-rotation §1.1: owner-locked hard constraint
of ≤5 concurrent distinct positions (ETF counts). check#6
(``_check_total_position_limit``) already has the right semantics — SELL skips,
adding to an existing holding does not increase the count, every distinct held
code (incl. ETF) counts — so the change is config-only (10→5) + restart. No 15th
check (``risk_summary`` stays min=max=14). These tests pin:

* the production ``config/risk.yaml`` value is exactly 5 (the amendment lock);
* check#6 rejects the 6th distinct new-position BUY;
* check#6 admits exactly the 5th distinct new position (boundary);
* SELL of a held code is skipped (an exit must never be trapped by the cap);
* a BUY adding to an already-held code passes (count unchanged);
* an ETF position counts toward the 5 (no board exemption).
"""

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

# Five-slot config — identical to the P0-7 lock except max_total_positions: 5.
RISK_YAML_FIVE_SLOT = """\
position_limits:
  max_single_stock_pct: 0.15
  max_sector_pct: 0.40
  max_total_positions: 5
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
def five_slot_config(tmp_path: Path) -> RiskConfig:
    path = tmp_path / "risk_five_slot.yaml"
    path.write_text(RISK_YAML_FIVE_SLOT, encoding="utf-8")
    return load_risk_config(path)


def _trading_time() -> dt.datetime:
    """Monday 10:00 Beijing time — a valid trading hour."""
    return dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)


def _make_order(
    code: str = "600519",
    price: float = 100.0,
    volume: int = 100,
    direction: OrderDirection = OrderDirection.BUY,
) -> Order:
    now = _trading_time()
    return Order(
        order_id="test", code=code, price=price, volume=volume,
        direction=direction, order_type=OrderType.LIMIT,
        created_at=now, updated_at=now,
    )


def _make_account(
    total_assets: float = 1_000_000.0, available_cash: float = 500_000.0,
) -> AccountInfo:
    return AccountInfo(
        total_assets=total_assets, available_cash=available_cash,
        frozen_cash=0.0, market_value=0.0,
        total_pnl=0.0, total_pnl_pct=0.0, initial_capital=1_000_000.0,
    )


def _make_position(code: str, market_value: float = 10_000.0) -> Position:
    return Position(
        code=code, volume=100, available_volume=100,
        cost_price=100.0, market_value=market_value,
        unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
    )


# ---------------------------------------------------------------------------
# Production config lock — the amendment value
# ---------------------------------------------------------------------------


def test_production_config_max_total_positions_is_five() -> None:
    """config/risk.yaml pins max_total_positions=5 (amendment §1.1 lock)."""
    config = load_risk_config(Path("config/risk.yaml"))
    assert config.position_limits.max_total_positions == 5


# ---------------------------------------------------------------------------
# check#6 ≤5 boundary
# ---------------------------------------------------------------------------


class TestFiveSlotCap:
    def test_fifth_new_position_admitted(self, five_slot_config: RiskConfig) -> None:
        """4 held + the 5th distinct new BUY → passes (exactly fills the cap)."""
        positions = tuple(_make_position(f"60051{i}") for i in range(4))
        engine = RiskEngine(five_slot_config)
        r = engine.validate_order(
            _make_order(code="000001"), _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_sixth_new_position_rejected(self, five_slot_config: RiskConfig) -> None:
        """5 held + a 6th distinct new BUY → rejected by check#6."""
        positions = tuple(_make_position(f"60051{i}") for i in range(5))
        engine = RiskEngine(five_slot_config)
        r = engine.validate_order(
            _make_order(code="000001"), _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "total_position_limit"

    def test_sell_of_held_code_skips_cap(self, five_slot_config: RiskConfig) -> None:
        """A SELL is never trapped by the cap even at 5 held positions."""
        positions = tuple(_make_position(f"60051{i}") for i in range(5))
        engine = RiskEngine(five_slot_config)
        r = engine.validate_order(
            _make_order(code="600510", direction=OrderDirection.SELL),
            _make_account(), positions, prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_add_to_existing_position_passes(
        self, five_slot_config: RiskConfig
    ) -> None:
        """Buying more of an already-held code does not increase the count."""
        positions = tuple(_make_position(f"60051{i}") for i in range(5))
        engine = RiskEngine(five_slot_config)
        r = engine.validate_order(
            _make_order(code="600510"), _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed

    def test_etf_counts_toward_cap(self, five_slot_config: RiskConfig) -> None:
        """An ETF holding occupies a slot — no board exemption (ETF 也算)."""
        # 4 ordinary stocks + 1 broad ETF (510300) = 5 distinct held codes.
        positions = (
            *(_make_position(f"60051{i}") for i in range(4)),
            _make_position("510300"),
        )
        engine = RiskEngine(five_slot_config)
        r = engine.validate_order(
            _make_order(code="000001"), _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert not r.passed
        assert r.rule_name == "total_position_limit"

    def test_zero_volume_position_not_counted(
        self, five_slot_config: RiskConfig
    ) -> None:
        """A fully-exited (volume 0) holding frees its slot for a new BUY."""
        positions = (
            *(_make_position(f"60051{i}") for i in range(4)),
            Position(
                code="600599", volume=0, available_volume=0,
                cost_price=100.0, market_value=0.0,
                unrealized_pnl=0.0, unrealized_pnl_pct=0.0,
            ),
        )
        engine = RiskEngine(five_slot_config)
        r = engine.validate_order(
            _make_order(code="000001"), _make_account(), positions,
            prev_close=100.0, now=_trading_time(),
        )
        assert r.passed
