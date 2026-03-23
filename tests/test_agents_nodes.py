"""Tests for all agent nodes with mocked LLM and data services."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from backend.agents.bear_researcher import bear_researcher_node
from backend.agents.bull_researcher import bull_researcher_node
from backend.agents.fund_manager import fund_manager_node
from backend.agents.fundamental_analyst import fundamental_analyst_node
from backend.agents.intelligence_officer import intelligence_officer_node
from backend.agents.models import (
    AnalysisServices,
    AnalysisState,
    DebateState,
    PipelineConfig,
    TradingSignal,
)
from backend.agents.news_crawler import news_crawler_node
from backend.agents.risk_officer import risk_officer_node
from backend.agents.sentiment_analyst import sentiment_analyst_node
from backend.agents.technical_analyst import technical_analyst_node


def _make_completion(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 20
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _empty_debate() -> DebateState:
    return {
        "history": "",
        "bull_history": "",
        "bear_history": "",
        "current_response": "",
        "count": 0,
    }


def _sample_state() -> AnalysisState:
    return {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "trade_date": "2026-03-22",
        "news_report": "新闻报告内容",
        "sentiment_report": "情绪报告内容",
        "fundamental_report": "基本面报告内容",
        "technical_report": "技术面报告内容",
        "intelligence_report": "情报报告内容",
        "debate_state": _empty_debate(),
        "risk_assessment": "风控评估内容",
        "trading_signal": {},
    }


@pytest.fixture()
def mock_services() -> AnalysisServices:
    router = AsyncMock()
    router.complete = AsyncMock(
        return_value=_make_completion("模拟分析报告")
    )

    market_data = AsyncMock()
    market_data.get_stock_realtime = AsyncMock(
        return_value=MagicMock(
            price=1800.0, change_pct=0.28, volume=5_000_000, amount=9e9
        )
    )
    market_data.get_index_realtime = AsyncMock(
        return_value=[
            MagicMock(name="上证指数", price=3150.5, change_pct=0.85)
        ]
    )
    market_data.get_capital_flow = AsyncMock(
        return_value=MagicMock(north_net_inflow=3.2e9)
    )

    history_data = AsyncMock()
    history_data.get_financial_data = AsyncMock(
        return_value=MagicMock(
            pe_ratio=32.5, pb_ratio=10.2, roe=30.5, eps=45.8,
            revenue_growth=15.3, report_date="2025-12-31"
        )
    )
    history_data.get_kline = AsyncMock(
        return_value=pd.DataFrame([
            {"date": "2026-03-20", "open": 1790, "high": 1810,
             "low": 1785, "close": 1800, "volume": 5000000, "amount": 9e9},
        ])
    )

    news_crawler = AsyncMock()
    news_article = MagicMock(
        title="测试新闻", content="测试内容", source="eastmoney"
    )
    news_crawler.fetch_stock_news = AsyncMock(return_value=[news_article])
    news_crawler.fetch_latest_news = AsyncMock(return_value=[news_article])

    return AnalysisServices(
        llm_router=router,
        market_data=market_data,
        history_data=history_data,
        news_crawler=news_crawler,
        pipeline_config=PipelineConfig(),
    )


# -- Stage 1 Agents --


class TestNewsCrawlerNode:
    @pytest.mark.asyncio
    async def test_returns_report(self, mock_services: AnalysisServices) -> None:
        result = await news_crawler_node(_sample_state(), mock_services)
        assert "news_report" in result
        assert result["news_report"] == "模拟分析报告"
        mock_services.llm_router.complete.assert_called_once()


class TestSentimentAnalystNode:
    @pytest.mark.asyncio
    async def test_returns_report(self, mock_services: AnalysisServices) -> None:
        result = await sentiment_analyst_node(_sample_state(), mock_services)
        assert "sentiment_report" in result
        assert result["sentiment_report"] == "模拟分析报告"


class TestFundamentalAnalystNode:
    @pytest.mark.asyncio
    async def test_returns_report(self, mock_services: AnalysisServices) -> None:
        result = await fundamental_analyst_node(_sample_state(), mock_services)
        assert "fundamental_report" in result
        assert result["fundamental_report"] == "模拟分析报告"


class TestTechnicalAnalystNode:
    @pytest.mark.asyncio
    async def test_returns_report(self, mock_services: AnalysisServices) -> None:
        result = await technical_analyst_node(_sample_state(), mock_services)
        assert "technical_report" in result
        assert result["technical_report"] == "模拟分析报告"


class TestIntelligenceOfficerNode:
    @pytest.mark.asyncio
    async def test_returns_report(self, mock_services: AnalysisServices) -> None:
        result = await intelligence_officer_node(_sample_state(), mock_services)
        assert "intelligence_report" in result
        assert result["intelligence_report"] == "模拟分析报告"


# -- Stage 2 Debate Agents --


class TestBullResearcherNode:
    @pytest.mark.asyncio
    async def test_increments_count(self, mock_services: AnalysisServices) -> None:
        state = _sample_state()
        result = await bull_researcher_node(state, mock_services)
        assert "debate_state" in result
        assert result["debate_state"]["count"] == 1
        assert "看多研究员" in result["debate_state"]["history"]
        assert result["debate_state"]["current_response"].startswith("Bull:")

    @pytest.mark.asyncio
    async def test_original_state_unchanged(
        self, mock_services: AnalysisServices
    ) -> None:
        state = _sample_state()
        await bull_researcher_node(state, mock_services)
        assert state["debate_state"]["count"] == 0


class TestBearResearcherNode:
    @pytest.mark.asyncio
    async def test_increments_count(self, mock_services: AnalysisServices) -> None:
        state = _sample_state()
        state["debate_state"]["count"] = 1
        state["debate_state"]["bull_history"] = "看多论点"
        result = await bear_researcher_node(state, mock_services)
        assert result["debate_state"]["count"] == 2
        assert "看空研究员" in result["debate_state"]["history"]
        assert result["debate_state"]["current_response"].startswith("Bear:")


# -- Stage 3 Decision Agents --


class TestRiskOfficerNode:
    @pytest.mark.asyncio
    async def test_returns_assessment(
        self, mock_services: AnalysisServices
    ) -> None:
        result = await risk_officer_node(_sample_state(), mock_services)
        assert "risk_assessment" in result
        assert result["risk_assessment"] == "模拟分析报告"


class TestFundManagerNode:
    @pytest.mark.asyncio
    async def test_valid_json_response(
        self, mock_services: AnalysisServices
    ) -> None:
        mock_services.llm_router.complete = AsyncMock(
            return_value=_make_completion(
                '{"action": "买入", "target_price": 1900.0, '
                '"confidence": 0.8, "risk_score": 0.3, '
                '"reasoning": "基本面强劲"}'
            )
        )
        result = await fund_manager_node(_sample_state(), mock_services)
        assert "trading_signal" in result
        signal = TradingSignal(**result["trading_signal"])
        assert signal.action == "买入"
        assert signal.target_price == 1900.0

    @pytest.mark.asyncio
    async def test_invalid_json_fallback(
        self, mock_services: AnalysisServices
    ) -> None:
        mock_services.llm_router.complete = AsyncMock(
            return_value=_make_completion("无法生成JSON格式的回复")
        )
        result = await fund_manager_node(_sample_state(), mock_services)
        signal = TradingSignal(**result["trading_signal"])
        assert signal.action == "持有"
        assert signal.confidence == 0.5
