"""RiskEngine check #02 price-cage subcheck (U-E2 / 缺口4).

The continuous-auction price cage (价格笼子) is an ADDITIONAL constraint folded
into check #02 (``price_reasonability``) — it does NOT add a 15th check. It only
fires for a BUY limit order when the Line-1 provider threads a live
:class:`backend.risk.price_cage.CageQuote` (best_ask + provenance). A limit above
``max(best_ask×1.02, best_ask+10×tick)`` (沪深主板/ETF) is a 废单 the exchange
would bounce, so the engine rejects it independently of the prev_close band.
Fail-closed on a missing best_ask / board.
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
    RiskConfig,
    load_risk_config,
)
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.price_cage import CageQuote
from backend.risk.stock_meta import Board, StockMetadata

SHANGHAI = ZoneInfo("Asia/Shanghai")

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
def cfg(tmp_path: Path) -> RiskConfig:
    path = tmp_path / "risk.yaml"
    path.write_text(RISK_YAML_P0_7, encoding="utf-8")
    return load_risk_config(path)


def _trading_time() -> dt.datetime:
    return dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)


def _order(
    *,
    code: str = "600519",
    price: float = 100.0,
    direction: OrderDirection = OrderDirection.BUY,
    order_type: OrderType = OrderType.LIMIT,
) -> Order:
    now = _trading_time()
    return Order(
        order_id="t", code=code, price=price, volume=100,
        direction=direction, order_type=order_type,
        created_at=now, updated_at=now,
    )


def _account() -> AccountInfo:
    return AccountInfo(
        total_assets=1_000_000.0, available_cash=1_000_000.0, frozen_cash=0.0,
        market_value=0.0, total_pnl=0.0, total_pnl_pct=0.0,
        initial_capital=1_000_000.0,
    )


def _meta(code: str = "600519", board: Board = Board.SH_MAIN) -> StockMetadata:
    return StockMetadata(
        code=code, name=code, board=board, is_st=False,
        instrument_type="etf" if board is Board.ETF else "stock",
    )


def _state(current_price: float = 100.0) -> DailyTradingState:
    return DailyTradingState(
        today_new_instruction_count=0, today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(), current_price=current_price,
        is_in_halt_cooldown=False, halt_until=None,
    )


_DEFAULT_META = object()  # sentinel: build _meta(order.code); None means no meta


def _validate(
    cfg: RiskConfig,
    order: Order,
    *,
    prev_close: float | None = 100.0,
    meta: object = _DEFAULT_META,
    live_quote: CageQuote | None = None,
):
    engine = RiskEngine(cfg)
    resolved = _meta(code=order.code) if meta is _DEFAULT_META else meta
    return engine.validate_order(
        order,
        _account(),
        (),
        prev_close=prev_close,
        now=_trading_time(),
        # Tie the live MTM price to the order so check #12 (limit-up block)
        # sees an in-band current price — these tests isolate the cage subcheck.
        daily_state=_state(current_price=order.price),
        stock_meta=resolved,  # type: ignore[arg-type]
        live_quote=live_quote,
    )


class TestCagePasses:
    def test_buy_within_cage_passes(self, cfg: RiskConfig) -> None:
        # best_ask 100.0 → cage ceiling max(102.0, 100.10) = 102.0; limit 101 ok.
        r = _validate(
            cfg, _order(price=101.0),
            live_quote=CageQuote(best_ask=100.0, source="adata"),
        )
        assert r.passed

    def test_buy_at_exact_cage_ceiling_passes(self, cfg: RiskConfig) -> None:
        # best_ask 100 → ceiling exactly 102.0; limit 102.0 is ≤ (boundary).
        r = _validate(
            cfg, _order(price=102.0),
            live_quote=CageQuote(best_ask=100.0, source="adata"),
        )
        assert r.passed


class TestCageRejects:
    def test_buy_above_cage_rejected(self, cfg: RiskConfig) -> None:
        # best_ask 100 → ceiling 102.0; limit 102.01 is a 废单.
        r = _validate(
            cfg, _order(price=102.01),
            live_quote=CageQuote(best_ask=100.0, source="adata"),
        )
        assert not r.passed
        assert r.rule_name == "price_reasonability"  # folded into check #02
        assert "price_cage_violation" in (r.message or "")
        assert "adata" in (r.message or "")  # provenance surfaced

    def test_missing_best_ask_fails_closed(self, cfg: RiskConfig) -> None:
        r = _validate(
            cfg, _order(price=101.0),
            live_quote=CageQuote(best_ask=None, source="akshare"),
        )
        assert not r.passed
        assert r.rule_name == "price_reasonability"
        assert "price_cage" in (r.message or "")

    def test_missing_board_fails_closed(self, cfg: RiskConfig) -> None:
        # live_quote present but NO stock_meta → cannot resolve the board, so
        # the cage subcheck (running first in check #02) fails closed.
        r = _validate(
            cfg, _order(price=101.0),
            meta=None,
            live_quote=CageQuote(best_ask=100.0, source="adata"),
        )
        assert not r.passed
        assert r.rule_name == "price_reasonability"
        assert "price_cage_violation: missing board" in (r.message or "")

    def test_cage_runs_even_when_prev_close_none(self, cfg: RiskConfig) -> None:
        # prev_close None makes the band check early-return passed; the cage
        # subcheck must STILL reject an over-cage BUY (not bypassed).
        r = _validate(
            cfg, _order(price=130.0), prev_close=None,
            live_quote=CageQuote(best_ask=100.0, source="adata"),
        )
        assert not r.passed
        assert "price_cage_violation" in (r.message or "")


class TestCageNotApplied:
    def test_no_live_quote_skips_cage(self, cfg: RiskConfig) -> None:
        # Backward-compat: a caller that does not thread a live quote keeps the
        # pre-U-E2 behaviour (band check only).
        r = _validate(cfg, _order(price=101.0), live_quote=None)
        assert r.passed

    def test_sell_not_caged(self, cfg: RiskConfig) -> None:
        # The cage is a BUY-side 废单 guard; a SELL limit at the same price with
        # a live quote present is NOT cage-checked.
        r = _validate(
            cfg,
            _order(price=130.0, direction=OrderDirection.SELL),
            live_quote=CageQuote(best_ask=100.0, source="adata"),
        )
        # SELL is not blocked by the cage; the over-band price would trip check
        # #02's band instead — assert it is NOT a cage violation.
        assert "price_cage_violation" not in (r.message or "")

    def test_market_buy_skips_cage(self, cfg: RiskConfig) -> None:
        # best_ask 50 → cage ceiling ~51; a LIMIT at 100 would be a 废单, but a
        # MARKET order early-returns before the cage subcheck, so it passes.
        r = _validate(
            cfg,
            _order(price=100.0, order_type=OrderType.MARKET),
            live_quote=CageQuote(best_ask=50.0, source="adata"),
        )
        assert r.passed  # MARKET orders early-return before the cage subcheck


class TestCageEtfTick:
    def test_etf_low_price_cage(self, cfg: RiskConfig) -> None:
        # ETF best_ask 4.00 → pct ceiling 4.08; tick alt 4.00+10×0.001=4.01 →
        # 孰高 4.08. Limit 4.05 passes; 4.09 rejected.
        ok = _validate(
            cfg, _order(code="510300", price=4.05), prev_close=4.00,
            meta=_meta(code="510300", board=Board.ETF),
            live_quote=CageQuote(best_ask=4.00, source="adata"),
        )
        assert ok.passed
        bad = _validate(
            cfg, _order(code="510300", price=4.09), prev_close=4.00,
            meta=_meta(code="510300", board=Board.ETF),
            live_quote=CageQuote(best_ask=4.00, source="adata"),
        )
        assert not bad.passed
        assert "price_cage_violation" in (bad.message or "")
