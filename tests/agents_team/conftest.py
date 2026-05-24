"""Shared fixtures for agents_team graph/node tests (Phase M-002 / M-003)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
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


# ---------------------------------------------------------------------------
# Deterministic LLMCompleter fake (M-003)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeMessage:
    content: str | None


@dataclass(frozen=True)
class _FakeChoice:
    message: _FakeMessage


@dataclass(frozen=True)
class _FakeCompletion:
    choices: list[_FakeChoice]


@dataclass
class FakeRouter:
    """Deterministic :class:`LLMCompleter` fake for agents_team tests.

    Returns a canned non-empty report for each analyst and a JSON envelope
    for ``fund_manager``. ``action`` controls the proposed direction
    (买入/卖出/持有); ``fail_agents`` makes named agents raise (fail-closed
    path); ``empty_agents`` makes named agents return empty content;
    ``bad_fund_manager_json`` makes fund_manager emit unparseable text.
    Records every ``agent_name`` it was asked to complete in ``calls``.
    """

    action: str = "买入"
    confidence: float = 0.9
    fail_agents: frozenset[str] = field(default_factory=frozenset)
    empty_agents: frozenset[str] = field(default_factory=frozenset)
    no_choices_agents: frozenset[str] = field(default_factory=frozenset)
    none_content_agents: frozenset[str] = field(default_factory=frozenset)
    whitespace_agents: frozenset[str] = field(default_factory=frozenset)
    bad_fund_manager_json: bool = False
    calls: list[str] = field(default_factory=list)

    async def complete(
        self, agent_name: str, messages: list[dict[str, str]], **kwargs: Any
    ) -> _FakeCompletion:
        self.calls.append(agent_name)
        if agent_name in self.fail_agents:
            raise RuntimeError(f"boom {agent_name}")
        if agent_name in self.empty_agents:
            return _FakeCompletion([_FakeChoice(_FakeMessage(""))])
        if agent_name in self.no_choices_agents:
            return _FakeCompletion([])  # choices[0] → IndexError (fail-closed)
        if agent_name in self.none_content_agents:
            return _FakeCompletion([_FakeChoice(_FakeMessage(None))])
        if agent_name in self.whitespace_agents:
            return _FakeCompletion([_FakeChoice(_FakeMessage("   \n\t  "))])
        if agent_name == "fund_manager":
            if self.bad_fund_manager_json:
                content = "抱歉,我无法用 JSON 回答这个问题。"
            else:
                content = json.dumps(
                    {
                        "action": self.action,
                        "target_price": 4.6,
                        "confidence": self.confidence,
                        "risk_score": 0.2,
                        "reasoning": f"综合判断给出 {self.action} 建议",
                    },
                    ensure_ascii=False,
                )
            return _FakeCompletion([_FakeChoice(_FakeMessage(content))])
        return _FakeCompletion(
            [_FakeChoice(_FakeMessage(f"[{agent_name}] 分析报告正文"))]
        )


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
    """A context whose fake fund_manager proposes BUY and whose risk inputs
    let a 200-lot 510300 buy at 4.5 pass the 14-check."""
    return TeamContext(
        risk_engine=risk_engine,
        account=account,
        positions=(),
        prev_close=4.5,
        daily_state=daily_state,
        stock_meta=stock_meta,
        now=_NOW,
        llm_router=FakeRouter(action="买入"),
    )


@pytest.fixture
def candidate() -> CandidateBrief:
    return CandidateBrief(
        code=_CODE, name=_NAME, proposed_volume=200, proposed_limit_price=4.5
    )


def empty_positions() -> tuple[Position, ...]:
    return ()
