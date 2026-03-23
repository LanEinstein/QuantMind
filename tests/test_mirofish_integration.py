"""Integration tests for MiroFish embedded in Intelligence Officer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agents.intelligence_officer import intelligence_officer_node
from backend.agents.models import AnalysisServices, AnalysisState, PipelineConfig
from backend.mirofish.schemas import (
    ExtremeScenario,
    HiddenVariable,
    InflectionPoint,
    SentimentSnapshot,
    SimulationConfig,
    SimulationResult,
)


def _make_completion(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


def _sample_state() -> AnalysisState:
    return {
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "trade_date": "2026-03-22",
        "news_report": "央行宣布降准50个基点，预计释放万亿流动性。",
        "sentiment_report": "市场情绪偏乐观",
        "fundamental_report": "基本面强劲",
        "technical_report": "技术面看多",
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


def _mock_simulation_result() -> SimulationResult:
    return SimulationResult(
        event_summary="央行降准50个基点",
        simulation_config=SimulationConfig(),
        sentiment_evolution=(
            SentimentSnapshot(
                round=1, bullish=0.5, bearish=0.2, neutral=0.3
            ),
        ),
        hidden_variables=(
            HiddenVariable(
                variable="外资加速流入",
                probability=0.72,
                reasoning="降准叠加汇率企稳",
            ),
        ),
        key_inflection_points=(
            InflectionPoint(day=3, event="获利回吐"),
        ),
        extreme_scenarios=(
            ExtremeScenario(
                scenario="利好叠加", probability=0.15, impact="+3%"
            ),
        ),
        recommended_action="短期看多",
        cost_rmb=2.5,
        duration_seconds=30.0,
    )


def _events_json() -> str:
    return json.dumps({
        "events": [
            {
                "title": "央行降准50个基点",
                "content": "释放万亿流动性",
                "importance_score": 9,
                "sectors": ["银行"],
                "stocks": ["601398"],
            }
        ]
    })


def _make_services(
    with_simulator: bool = True,
) -> AnalysisServices:
    router = AsyncMock()
    # Default: intelligence_officer LLM call returns report text
    # news_crawler LLM call returns events JSON
    def _route(*args, **kwargs):
        agent_name = args[0]
        if agent_name == "news_crawler":
            return _make_completion(_events_json())
        return _make_completion("情报研判报告")

    router.complete = AsyncMock(side_effect=_route)

    market_data = AsyncMock()
    index_mock = MagicMock()
    index_mock.name = "上证指数"
    index_mock.price = 3150.5
    index_mock.change_pct = 0.85
    market_data.get_index_realtime = AsyncMock(return_value=[index_mock])
    market_data.get_capital_flow = AsyncMock(
        return_value=MagicMock(north_net_inflow=3.2e9)
    )

    simulator = None
    if with_simulator:
        simulator = AsyncMock()
        simulator.run_simulation = AsyncMock(
            return_value=_mock_simulation_result()
        )

    return AnalysisServices(
        llm_router=router,
        market_data=market_data,
        history_data=AsyncMock(),
        news_crawler=AsyncMock(),
        mirofish_simulator=simulator,
        pipeline_config=PipelineConfig(),
    )


class TestIntelligenceOfficerWithMiroFish:
    @pytest.mark.asyncio
    async def test_high_importance_triggers_simulation(self) -> None:
        services = _make_services(with_simulator=True)
        result = await intelligence_officer_node(
            _sample_state(), services
        )
        assert "intelligence_report" in result
        # Simulator should have been called
        services.mirofish_simulator.run_simulation.assert_called_once()
        # The final LLM call should include MiroFish context
        calls = services.llm_router.complete.call_args_list
        # Last call is intelligence_officer
        last_call = calls[-1]
        messages = last_call[0][1]
        user_msg = messages[1]["content"]
        assert "MiroFish" in user_msg

    @pytest.mark.asyncio
    async def test_no_events_skips_simulation(self) -> None:
        services = _make_services(with_simulator=True)
        # Override news_crawler response to return no events
        services.llm_router.complete = AsyncMock(
            return_value=_make_completion(
                '{"events": []}'
            )
        )
        # But intelligence_officer call needs to return a report
        call_count = 0

        def _route(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            agent_name = args[0]
            if agent_name == "news_crawler":
                return _make_completion('{"events": []}')
            return _make_completion("情报报告")

        services.llm_router.complete = AsyncMock(side_effect=_route)

        result = await intelligence_officer_node(
            _sample_state(), services
        )
        assert "intelligence_report" in result
        services.mirofish_simulator.run_simulation.assert_not_called()

    @pytest.mark.asyncio
    async def test_simulation_failure_degrades(self) -> None:
        services = _make_services(with_simulator=True)
        services.mirofish_simulator.run_simulation = AsyncMock(
            side_effect=RuntimeError("simulation exploded")
        )
        result = await intelligence_officer_node(
            _sample_state(), services
        )
        # Should still produce a report
        assert "intelligence_report" in result
        assert result["intelligence_report"] != ""

    @pytest.mark.asyncio
    async def test_event_extraction_failure_degrades(self) -> None:
        services = _make_services(with_simulator=True)
        # Make the event extraction LLM call fail
        services.llm_router.complete = AsyncMock(
            side_effect=[
                Exception("DeepSeek down"),  # event extraction fails
                _make_completion("情报报告"),  # intelligence_officer succeeds
            ]
        )
        result = await intelligence_officer_node(
            _sample_state(), services
        )
        assert "intelligence_report" in result

    @pytest.mark.asyncio
    async def test_no_simulator_skips_entirely(self) -> None:
        services = _make_services(with_simulator=False)
        assert services.mirofish_simulator is None
        result = await intelligence_officer_node(
            _sample_state(), services
        )
        assert "intelligence_report" in result
        # No MiroFish context in the LLM call
        calls = services.llm_router.complete.call_args_list
        last_call = calls[-1]
        messages = last_call[0][1]
        user_msg = messages[1]["content"]
        assert "MiroFish" not in user_msg

    @pytest.mark.asyncio
    async def test_multiple_events_all_simulated(self) -> None:
        services = _make_services(with_simulator=True)
        multi_events = json.dumps({
            "events": [
                {
                    "title": "事件A",
                    "content": "内容A",
                    "importance_score": 8,
                    "sectors": [],
                    "stocks": [],
                },
                {
                    "title": "事件B",
                    "content": "内容B",
                    "importance_score": 9,
                    "sectors": [],
                    "stocks": [],
                },
            ]
        })

        def _route(*args, **kwargs):
            agent_name = args[0]
            if agent_name == "news_crawler":
                return _make_completion(multi_events)
            return _make_completion("情报报告")

        services.llm_router.complete = AsyncMock(side_effect=_route)

        result = await intelligence_officer_node(
            _sample_state(), services
        )
        assert "intelligence_report" in result
        assert services.mirofish_simulator.run_simulation.call_count == 2
