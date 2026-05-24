"""Shared fixtures for agents_team graph/node tests (Phase M-002)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.agents_team.state import CandidateBrief, TeamContext
from backend.broker.models import (
    AccountInfo,
    CircuitBreakerConfig,
    Position,
    PositionLimitsConfig,
    RiskConfig,
    StopLossConfig,
    UniverseConfig,
)
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata

_SH = ZoneInfo("Asia/Shanghai")
_NOW = datetime(2026, 5, 15, 10, 30, tzinfo=_SH)  # Fri, mid-morning session
_CODE = "510300"
_NAME = "沪深300 ETF"


@pytest.fixture
def risk_engine() -> RiskEngine:
    return RiskEngine(
        RiskConfig(
            position_limits=PositionLimitsConfig(),
            stop_loss=StopLossConfig(),
            circuit_breaker=CircuitBreakerConfig(),
            universe=UniverseConfig(),
        )
    )


@pytest.fixture
def account() -> AccountInfo:
    return AccountInfo(
        total_assets=1_000_000.0,
        available_cash=900_000.0,
        frozen_cash=0.0,
        market_value=100_000.0,
        total_pnl=0.0,
        total_pnl_pct=0.0,
        initial_capital=1_000_000.0,
    )


@pytest.fixture
def stock_meta() -> RiskStockMetadata:
    return RiskStockMetadata(
        code=_CODE, name=_NAME, board=RiskBoard.ETF, is_st=False,
        instrument_type="etf",
    )


@pytest.fixture
def daily_state() -> DailyTradingState:
    return DailyTradingState(
        today_new_instruction_count=0,
        today_portfolio_pnl_pct=0.0,
        last_3_trade_pnls=(),
        current_price=4.5,
        is_in_halt_cooldown=False,
        halt_until=None,
    )


@pytest.fixture
def buy_context(
    risk_engine: RiskEngine,
    account: AccountInfo,
    stock_meta: RiskStockMetadata,
    daily_state: DailyTradingState,
) -> TeamContext:
    """A context whose stub fund_manager proposes BUY and whose risk inputs
    let a 200-lot 510300 buy at 4.5 pass the 14-check."""
    return TeamContext(
        risk_engine=risk_engine,
        account=account,
        positions=(),
        prev_close=4.5,
        daily_state=daily_state,
        stock_meta=stock_meta,
        now=_NOW,
        stub_direction="BUY",
    )


@pytest.fixture
def candidate() -> CandidateBrief:
    return CandidateBrief(
        code=_CODE, name=_NAME, proposed_volume=200, proposed_limit_price=4.5
    )


def empty_positions() -> tuple[Position, ...]:
    return ()
