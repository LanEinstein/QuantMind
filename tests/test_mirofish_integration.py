"""Integration tests for MiroFish embedded in Intelligence Officer."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agents.intelligence_officer import intelligence_officer_node
from backend.agents.models import AnalysisServices, AnalysisState, PipelineConfig
from backend.mirofish.extractors import HiddenVariableExtractionPipeline
from backend.mirofish.extractors.schemas import (
    EnrichedExtremeScenario,
    EnrichedHiddenVariable,
    EnrichedInflectionPoint,
    ExtractionResult,
    SentimentRound,
)
from backend.mirofish.schemas import (
    ExtremeScenario,
    HiddenVariable,
    InflectionPoint,
    MomentumShift,
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
    async def test_event_driven_evidence_writer_called(self) -> None:
        """C-006: HIGH severity events route through MiroFishEvidenceWriter."""
        services = _make_services(with_simulator=True)
        writer = AsyncMock()
        writer.write = AsyncMock(return_value=True)
        services = services.model_copy(update={"mirofish_writer": writer})

        result = await intelligence_officer_node(
            _sample_state(), services
        )
        assert "intelligence_report" in result
        writer.write.assert_called_once()
        evidence_arg = writer.write.call_args.args[0]
        assert evidence_arg.path == "event_driven"
        assert evidence_arg.evidence_id.startswith("MIROFISH-EVENT-")
        assert evidence_arg.severity >= 8  # HIGH threshold
        assert evidence_arg.trade_date == "2026-03-22"

    @pytest.mark.asyncio
    async def test_event_driven_cap_rejection_does_not_break_pipeline(
        self,
    ) -> None:
        """If the writer rejects (cap), intelligence_officer still produces a report."""
        from backend.mirofish.output_writer import MiroFishEvidenceError

        services = _make_services(with_simulator=True)
        writer = AsyncMock()
        writer.write = AsyncMock(
            side_effect=MiroFishEvidenceError(
                "cap", reason="daily_cap_reached"
            )
        )
        services = services.model_copy(update={"mirofish_writer": writer})
        result = await intelligence_officer_node(
            _sample_state(), services
        )
        assert "intelligence_report" in result

    @pytest.mark.asyncio
    async def test_high_event_paired_after_earlier_failure(self) -> None:
        """codex cycle 1 P2: a failed earlier simulation must not shift
        the (event, result) pairing so that a HIGH event is dropped or
        paired with the wrong simulation result."""
        services = _make_services(with_simulator=True)
        writer = AsyncMock()
        writer.write = AsyncMock(return_value=True)
        services = services.model_copy(update={"mirofish_writer": writer})

        # Two events: a LOW-severity one first (which fails simulation),
        # then a HIGH-severity one (which succeeds). The HIGH event
        # MUST still be the argument fed to writer.write — the previous
        # zip(events, results) implementation would have shifted indices.
        multi_events = json.dumps({
            "events": [
                {
                    "title": "低重要度事件",
                    "content": "x",
                    "importance_score": 4,
                    "sectors": [],
                    "stocks": [],
                },
                {
                    "title": "重要事件",
                    "content": "y",
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
        services.mirofish_simulator.run_simulation = AsyncMock(
            side_effect=[
                RuntimeError("low-sev simulation explode"),
                _mock_simulation_result(),
            ]
        )

        await intelligence_officer_node(_sample_state(), services)

        writer.write.assert_called_once()
        evidence_arg = writer.write.call_args.args[0]
        assert evidence_arg.severity == 9
        assert evidence_arg.event_title == "重要事件"

    @pytest.mark.asyncio
    async def test_low_severity_event_skips_evidence_write(self) -> None:
        """Only events with importance_score>=8 trigger the writer."""
        services = _make_services(with_simulator=True)
        writer = AsyncMock()
        writer.write = AsyncMock(return_value=True)
        services = services.model_copy(update={"mirofish_writer": writer})

        low_sev_json = json.dumps({
            "events": [
                {
                    "title": "低重要度",
                    "content": "x",
                    "importance_score": 5,
                    "sectors": [],
                    "stocks": [],
                }
            ]
        })

        def _route(*args, **kwargs):
            agent_name = args[0]
            if agent_name == "news_crawler":
                return _make_completion(low_sev_json)
            return _make_completion("情报报告")

        services.llm_router.complete = AsyncMock(side_effect=_route)

        await intelligence_officer_node(_sample_state(), services)
        writer.write.assert_not_called()

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


# ---------------------------------------------------------------------------
# Direct to_simulation_result() round-trip tests
# ---------------------------------------------------------------------------

def _make_pipeline() -> HiddenVariableExtractionPipeline:
    """Create a pipeline with a dummy router (not called in sync tests)."""
    return HiddenVariableExtractionPipeline(router=MagicMock())


def _make_extraction(
    *,
    sentiment_rounds: tuple[SentimentRound, ...] = (),
    momentum_shifts: tuple[MomentumShift, ...] = (),
    hidden_variables: tuple[EnrichedHiddenVariable, ...] = (),
    inflection_points: tuple[EnrichedInflectionPoint, ...] = (),
    extreme_scenarios: tuple[EnrichedExtremeScenario, ...] = (),
    recommended_action: str = "看多",
) -> ExtractionResult:
    return ExtractionResult(
        event_summary="央行降准",
        sentiment_rounds=sentiment_rounds,
        momentum_shifts=momentum_shifts,
        hidden_variables=hidden_variables,
        inflection_points=inflection_points,
        extreme_scenarios=extreme_scenarios,
        recommended_action=recommended_action,
    )


class TestToSimulationResult:
    def test_preserves_sentiment_intensity(self) -> None:
        extraction = _make_extraction(
            sentiment_rounds=(
                SentimentRound(
                    round=1, bullish=0.5, bearish=0.3, neutral=0.2,
                    dominant_narrative="降准预期", intensity=0.85,
                ),
            ),
        )
        result = _make_pipeline().to_simulation_result(
            extraction, SimulationConfig(), 1.0, 10.0
        )
        snap = result.sentiment_evolution[0]
        assert snap.dominant_narrative == "降准预期"
        assert snap.intensity == 0.85

    def test_preserves_hidden_variable_consensus(self) -> None:
        extraction = _make_extraction(
            hidden_variables=(
                EnrichedHiddenVariable(
                    variable="外资净流入",
                    probability=0.72,
                    reasoning="北向资金连续净买入",
                    agent_consensus_ratio=0.68,
                    is_absent_from_original=True,
                ),
            ),
        )
        result = _make_pipeline().to_simulation_result(
            extraction, SimulationConfig(), 1.0, 10.0
        )
        hv = result.hidden_variables[0]
        assert hv.agent_consensus_ratio == 0.68
        assert hv.is_absent_from_original is True
        # reasoning must NOT be contaminated with consensus/disclaimer strings
        assert "[consensus=" not in hv.reasoning
        assert "disclaimer" not in hv.reasoning.lower()

    def test_preserves_inflection_type_and_confidence(self) -> None:
        extraction = _make_extraction(
            inflection_points=(
                EnrichedInflectionPoint(
                    day=5,
                    event="情绪逆转",
                    inflection_type="sentiment_reversal",
                    before_sentiment={"bullish": 0.3, "bearish": 0.5, "neutral": 0.2},
                    after_sentiment={"bullish": 0.6, "bearish": 0.2, "neutral": 0.2},
                    confidence=0.9,
                ),
            ),
        )
        result = _make_pipeline().to_simulation_result(
            extraction, SimulationConfig(), 1.0, 10.0
        )
        ip = result.key_inflection_points[0]
        assert ip.inflection_type == "sentiment_reversal"
        assert ip.before_sentiment["bullish"] == 0.3
        assert ip.after_sentiment["bullish"] == 0.6
        assert ip.confidence == 0.9
        # event must NOT have stringified prefix like [sentiment_reversal]
        assert ip.event == "情绪逆转"

    def test_preserves_extreme_scenario_direction(self) -> None:
        extraction = _make_extraction(
            extreme_scenarios=(
                EnrichedExtremeScenario(
                    scenario="超预期利好",
                    probability=0.1,
                    impact="+5%",
                    direction="upside",
                    trigger_conditions="美联储降息超预期",
                    early_warning_signals="外资持续流入",
                ),
            ),
        )
        result = _make_pipeline().to_simulation_result(
            extraction, SimulationConfig(), 1.0, 10.0
        )
        es = result.extreme_scenarios[0]
        assert es.direction == "upside"
        assert es.trigger_conditions == "美联储降息超预期"
        assert es.early_warning_signals == "外资持续流入"
        # scenario must NOT be prefixed with [upside]
        assert es.scenario == "超预期利好"

    def test_emits_momentum_shifts(self) -> None:
        shift = MomentumShift(
            round_number=3, direction="bullish_to_bearish", magnitude=0.23,
            trigger_narrative="获利回吐"
        )
        extraction = _make_extraction(momentum_shifts=(shift,))
        result = _make_pipeline().to_simulation_result(
            extraction, SimulationConfig(), 1.0, 10.0
        )
        assert len(result.momentum_shifts) == 1
        assert result.momentum_shifts[0].direction == "bullish_to_bearish"
        assert result.momentum_shifts[0].magnitude == 0.23

    def test_pydantic_round_trip(self) -> None:
        shift = MomentumShift(
            round_number=2, direction="bearish_to_bullish", magnitude=0.15
        )
        extraction = _make_extraction(
            sentiment_rounds=(
                SentimentRound(
                    round=1, bullish=0.4, bearish=0.3, neutral=0.3,
                    dominant_narrative="政策预期", intensity=0.7,
                ),
            ),
            momentum_shifts=(shift,),
        )
        result = _make_pipeline().to_simulation_result(
            extraction, SimulationConfig(), 2.5, 30.0
        )
        # Serialize → deserialize must be lossless
        data = result.model_dump(mode="json")
        restored = SimulationResult.model_validate(data)
        assert restored.sentiment_evolution[0].intensity == 0.7
        assert restored.momentum_shifts[0].magnitude == 0.15

    def test_no_longer_mutates_recommendation_text(self) -> None:
        """Verify the old [动量转换: ...] suffix is gone."""
        shift = MomentumShift(
            round_number=3, direction="bullish_to_bearish", magnitude=0.23
        )
        extraction = _make_extraction(
            momentum_shifts=(shift,),
            recommended_action="短期看多",
        )
        result = _make_pipeline().to_simulation_result(
            extraction, SimulationConfig(), 1.0, 10.0
        )
        assert result.recommended_action == "短期看多"
        assert "动量转换" not in result.recommended_action
