"""Tests for SignalEvaluator (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pandas as pd
import pytest

from backend.services.signal_evaluator import SignalEvaluator


def _make_signal(
    code: str = "600519",
    action: str = "买入",
    trade_date: str = "2026-04-01",
) -> dict:
    return {
        "stock_code": code,
        "stock_name": "贵州茅台",
        "action": action,
        "confidence": 0.8,
        "risk_score": 0.3,
        "trade_date": trade_date,
    }


def _make_kline(prices: list[float]) -> pd.DataFrame:
    """Create a minimal K-line DataFrame with close prices."""
    dates = [f"2026-04-{i + 1:02d}" for i in range(len(prices))]
    return pd.DataFrame({"date": dates, "close": prices})


@pytest.fixture()
def mock_mongodb() -> AsyncMock:
    mongodb = AsyncMock()
    mongodb.query_signals = AsyncMock(return_value=[])
    return mongodb


@pytest.fixture()
def mock_history() -> AsyncMock:
    history = AsyncMock()
    history.get_kline = AsyncMock(return_value=pd.DataFrame())
    return history


@pytest.fixture()
def evaluator(mock_mongodb: AsyncMock, mock_history: AsyncMock) -> SignalEvaluator:
    return SignalEvaluator(mongodb=mock_mongodb, history_data=mock_history)


class TestEvaluateSignalAccuracy:
    """Tests for SignalEvaluator.evaluate."""

    @pytest.mark.asyncio
    async def test_buy_correct_when_price_rose(
        self,
        evaluator: SignalEvaluator,
        mock_mongodb: AsyncMock,
        mock_history: AsyncMock,
    ) -> None:
        mock_mongodb.query_signals.return_value = [
            _make_signal(action="买入", trade_date="2026-04-01"),
        ]
        # Price rose from 100 → 105 (correct for buy)
        mock_history.get_kline.return_value = _make_kline(
            [100.0, 101.0, 103.0, 104.0, 105.0]
        )

        result = await evaluator.evaluate(lookback_days=30, horizon_days=5)

        assert result["total_evaluated"] == 1
        assert result["correct"] == 1
        assert result["hit_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_sell_correct_when_price_fell(
        self,
        evaluator: SignalEvaluator,
        mock_mongodb: AsyncMock,
        mock_history: AsyncMock,
    ) -> None:
        mock_mongodb.query_signals.return_value = [
            _make_signal(action="卖出", trade_date="2026-04-01"),
        ]
        # Price fell from 100 → 95 (correct for sell)
        mock_history.get_kline.return_value = _make_kline(
            [100.0, 98.0, 97.0, 96.0, 95.0]
        )

        result = await evaluator.evaluate(lookback_days=30, horizon_days=5)

        assert result["total_evaluated"] == 1
        assert result["correct"] == 1
        assert result["hit_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_hold_excluded_from_accuracy(
        self,
        evaluator: SignalEvaluator,
        mock_mongodb: AsyncMock,
        mock_history: AsyncMock,
    ) -> None:
        mock_mongodb.query_signals.return_value = [
            _make_signal(action="持有", trade_date="2026-04-01"),
        ]

        result = await evaluator.evaluate(lookback_days=30, horizon_days=5)

        assert result["total_evaluated"] == 0
        assert result["hit_rate"] == 0.0
        # History should not even be called for hold signals
        mock_history.get_kline.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_report_structure(
        self,
        evaluator: SignalEvaluator,
        mock_mongodb: AsyncMock,
        mock_history: AsyncMock,
    ) -> None:
        mock_mongodb.query_signals.return_value = [
            _make_signal(action="买入", trade_date="2026-04-01"),
        ]
        mock_history.get_kline.return_value = _make_kline([100.0, 105.0])

        result = await evaluator.evaluate()

        assert "hit_rate" in result
        assert "total_evaluated" in result
        assert "correct" in result
        assert "by_action" in result
        assert isinstance(result["by_action"], dict)

    @pytest.mark.asyncio
    async def test_empty_signals(
        self, evaluator: SignalEvaluator, mock_mongodb: AsyncMock
    ) -> None:
        mock_mongodb.query_signals.return_value = []

        result = await evaluator.evaluate()

        assert result["total_evaluated"] == 0
        assert result["correct"] == 0
        assert result["hit_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_skips_signals_without_price_data(
        self,
        evaluator: SignalEvaluator,
        mock_mongodb: AsyncMock,
        mock_history: AsyncMock,
    ) -> None:
        mock_mongodb.query_signals.return_value = [
            _make_signal(action="买入", trade_date="2026-04-01"),
        ]
        # Empty K-line → signal skipped
        mock_history.get_kline.return_value = pd.DataFrame()

        result = await evaluator.evaluate()

        assert result["total_evaluated"] == 0
