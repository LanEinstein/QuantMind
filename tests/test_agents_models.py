"""Tests for agent pipeline models (TDD RED -> GREEN)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.models import (
    AnalysisState,
    DebateState,
    PipelineConfig,
    TradingSignal,
)


class TestTradingSignal:
    """Tests for TradingSignal frozen model."""

    def test_create_valid_buy(self) -> None:
        sig = TradingSignal(
            action="买入",
            target_price=1800.0,
            confidence=0.75,
            risk_score=0.4,
            reasoning="基本面强劲",
            stock_code="600519",
            stock_name="贵州茅台",
            trade_date="2026-03-22",
        )
        assert sig.action == "买入"
        assert sig.confidence == 0.75

    def test_create_valid_hold(self) -> None:
        sig = TradingSignal(
            action="持有",
            confidence=0.5,
            risk_score=0.5,
            reasoning="观望",
            stock_code="600519",
            stock_name="贵州茅台",
            trade_date="2026-03-22",
        )
        assert sig.target_price is None

    def test_frozen(self) -> None:
        sig = TradingSignal(
            action="卖出",
            confidence=0.8,
            risk_score=0.7,
            reasoning="风险过高",
            stock_code="600519",
            stock_name="贵州茅台",
            trade_date="2026-03-22",
        )
        with pytest.raises(ValidationError):
            sig.action = "买入"  # type: ignore[misc]

    def test_invalid_action(self) -> None:
        with pytest.raises(ValidationError):
            TradingSignal(
                action="invalid",
                confidence=0.5,
                risk_score=0.5,
                reasoning="test",
                stock_code="600519",
                stock_name="test",
                trade_date="2026-03-22",
            )

    def test_confidence_range(self) -> None:
        with pytest.raises(ValidationError):
            TradingSignal(
                action="持有",
                confidence=1.5,
                risk_score=0.5,
                reasoning="test",
                stock_code="600519",
                stock_name="test",
                trade_date="2026-03-22",
            )

    def test_risk_score_range(self) -> None:
        with pytest.raises(ValidationError):
            TradingSignal(
                action="持有",
                confidence=0.5,
                risk_score=-0.1,
                reasoning="test",
                stock_code="600519",
                stock_name="test",
                trade_date="2026-03-22",
            )

    def test_model_dump(self) -> None:
        sig = TradingSignal(
            action="买入",
            target_price=50.0,
            confidence=0.8,
            risk_score=0.3,
            reasoning="看好",
            stock_code="000858",
            stock_name="五粮液",
            trade_date="2026-03-22",
        )
        data = sig.model_dump()
        assert data["action"] == "买入"
        assert data["target_price"] == 50.0


class TestPipelineConfig:
    """Tests for PipelineConfig."""

    def test_defaults(self) -> None:
        config = PipelineConfig()
        assert config.max_debate_rounds == 2
        assert config.analysis_timeout_seconds == 300

    def test_frozen(self) -> None:
        config = PipelineConfig()
        with pytest.raises(ValidationError):
            config.max_debate_rounds = 5  # type: ignore[misc]


class TestDebateState:
    """Tests for DebateState TypedDict."""

    def test_create(self) -> None:
        state: DebateState = {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        }
        assert state["count"] == 0

    def test_update_immutable_pattern(self) -> None:
        state: DebateState = {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        }
        new_state: DebateState = {
            **state,
            "history": "Bull: 看多理由",
            "bull_history": "看多理由",
            "count": 1,
        }
        assert state["count"] == 0
        assert new_state["count"] == 1


class TestAnalysisState:
    """Tests for AnalysisState TypedDict."""

    def test_create_minimal(self) -> None:
        state: AnalysisState = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "trade_date": "2026-03-22",
            "news_report": "",
            "sentiment_report": "",
            "fundamental_report": "",
            "technical_report": "",
            "intelligence_report": "",
            "debate_state": {
                "history": "",
                "bull_history": "",
                "bear_history": "",
                "current_response": "",
                "count": 0,
            },
            "risk_assessment": "",
            "trading_signal": {},
        }
        assert state["stock_code"] == "600519"
