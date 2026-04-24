"""Tests for hidden variable extraction pipeline (P3-T02).

Covers:
- Unit tests for each extractor with mocked simulation data
- Probability calculation logic
- Full pipeline with sample simulation output
- Integration test: event -> MiroFish sim -> extraction -> structured result
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from backend.mirofish.extractors import (
    ExtremeScenarioAnalyzer,
    HiddenVariableExtractionPipeline,
    HiddenVariableExtractor,
    InflectionPointDetector,
    SentimentEvolutionTracker,
)
from backend.mirofish.extractors.schemas import (
    AgentAction,
    EnrichedExtremeScenario,
    EnrichedHiddenVariable,
    EnrichedInflectionPoint,
    ExtractionResult,
    MomentumShift,
    RawSimulationOutput,
    SentimentRound,
    SentimentSnapshotRaw,
)
from backend.mirofish.schemas import (
    EventDescription,
    SimulationConfig,
    SimulationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completion(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 200
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _sample_raw_simulation(rounds: int = 5) -> RawSimulationOutput:
    evolution = tuple(
        SentimentSnapshotRaw(
            round=i,
            bullish=round(0.5 + i * 0.02, 3),
            bearish=round(0.2 - i * 0.01, 3),
            neutral=round(0.3 - i * 0.01, 3),
        )
        for i in range(1, rounds + 1)
    )
    return RawSimulationOutput(
        event_title="央行宣布全面降准50个基点",
        event_content="中国人民银行今日宣布降准50个基点，释放资金约1万亿。",
        event_sectors=("银行", "房地产"),
        event_stocks=("601398", "600036"),
        event_summary="央行降准50个基点",
        initial_sentiment={"bullish": 0.50, "bearish": 0.20, "neutral": 0.30},
        sentiment_evolution=evolution,
        agent_count=100,
        rounds=rounds,
    )


def _sample_sentiment_rounds(rounds: int = 5) -> tuple[SentimentRound, ...]:
    return tuple(
        SentimentRound(
            round=i,
            bullish=round(0.5 + i * 0.02, 3),
            bearish=round(0.2 - i * 0.01, 3),
            neutral=round(0.3 - i * 0.01, 3),
            dominant_narrative=f"叙事R{i}",
            intensity=round(0.5 + i * 0.05, 3),
        )
        for i in range(1, rounds + 1)
    )


def _valid_sentiment_classification_json(rounds: int = 5) -> str:
    return json.dumps({
        "rounds": [
            {
                "round": i,
                "dominant_narrative": f"央行宽松预期第{i}轮",
                "intensity": round(0.5 + i * 0.05, 2),
            }
            for i in range(1, rounds + 1)
        ]
    })


def _valid_hidden_variable_json() -> str:
    return json.dumps({
        "hidden_variables": [
            {
                "variable": "外资加速流入概率",
                "probability": 0.72,
                "reasoning": "降准信号叠加利率差扩大",
                "agent_consensus_ratio": 0.65,
                "is_absent_from_original": True,
            },
            {
                "variable": "房地产政策进一步放松",
                "probability": 0.45,
                "reasoning": "多位机构投资者联想到历史降准后的地产政策",
                "agent_consensus_ratio": 0.40,
                "is_absent_from_original": True,
            },
        ]
    })


def _valid_inflection_point_json() -> str:
    return json.dumps({
        "inflection_points": [
            {
                "day": 6,
                "event": "获利回吐压力显现",
                "inflection_type": "sentiment_reversal",
                "before_sentiment": {
                    "bullish": 0.60,
                    "bearish": 0.15,
                    "neutral": 0.25,
                },
                "after_sentiment": {
                    "bullish": 0.40,
                    "bearish": 0.35,
                    "neutral": 0.25,
                },
                "confidence": 0.75,
            }
        ]
    })


def _valid_extreme_scenario_json() -> str:
    return json.dumps({
        "extreme_scenarios": [
            {
                "scenario": "超预期利好叠加引发强势反弹",
                "probability": 0.12,
                "impact": "+5-8%",
                "direction": "upside",
                "trigger_conditions": "多重利好同时兑现",
                "early_warning_signals": "北向资金连续3日净流入超50亿",
            },
            {
                "scenario": "利好出尽获利盘涌出",
                "probability": 0.08,
                "impact": "-3-5%",
                "direction": "downside",
                "trigger_conditions": "降准利好已完全price-in",
                "early_warning_signals": "龙虎榜机构大幅卖出",
            },
        ]
    })


def _valid_recommendation_json() -> str:
    return json.dumps({
        "recommended_action": "短期看多，分批建仓，关注北向资金流向"
    })


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestExtractionSchemas:
    def test_raw_simulation_output_frozen(self) -> None:
        raw = _sample_raw_simulation()
        with pytest.raises(Exception):
            raw.event_title = "changed"  # type: ignore[misc]

    def test_sentiment_round_sum_validation(self) -> None:
        with pytest.raises(ValueError, match="sum to ~1.0"):
            SentimentRound(
                round=1, bullish=0.8, bearish=0.8, neutral=0.1
            )

    def test_sentiment_round_valid(self) -> None:
        r = SentimentRound(
            round=1,
            bullish=0.5,
            bearish=0.3,
            neutral=0.2,
            dominant_narrative="央行宽松",
            intensity=0.7,
        )
        assert r.dominant_narrative == "央行宽松"
        assert r.intensity == 0.7

    def test_momentum_shift_frozen(self) -> None:
        ms = MomentumShift(
            round_number=3,
            direction="bullish_to_bearish",
            magnitude=0.2,
            trigger_narrative="获利回吐",
        )
        assert ms.round_number == 3
        with pytest.raises(Exception):
            ms.direction = "changed"  # type: ignore[misc]

    def test_enriched_hidden_variable_has_disclaimer(self) -> None:
        hv = EnrichedHiddenVariable(
            variable="test",
            probability=0.5,
            reasoning="test reason",
        )
        assert "simulated crowd wisdom" in hv.disclaimer

    def test_enriched_inflection_point(self) -> None:
        ip = EnrichedInflectionPoint(
            day=5,
            event="情绪反转",
            inflection_type="sentiment_reversal",
            confidence=0.8,
        )
        assert ip.inflection_type == "sentiment_reversal"

    def test_enriched_extreme_scenario(self) -> None:
        es = EnrichedExtremeScenario(
            scenario="超预期利好",
            probability=0.1,
            impact="+5%",
            direction="upside",
            trigger_conditions="多重利好",
            early_warning_signals="北向资金流入",
        )
        assert es.direction == "upside"

    def test_extraction_result_frozen(self) -> None:
        er = ExtractionResult(event_summary="test")
        with pytest.raises(Exception):
            er.event_summary = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SentimentEvolutionTracker tests
# ---------------------------------------------------------------------------


class TestSentimentEvolutionTracker:
    @pytest.mark.asyncio
    async def test_extract_success(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion(
                _valid_sentiment_classification_json()
            )
        )
        tracker = SentimentEvolutionTracker(router)
        raw = _sample_raw_simulation()
        result = await tracker.extract(raw)

        assert len(result) == 5
        assert all(isinstance(r, SentimentRound) for r in result)
        assert result[0].dominant_narrative == "央行宽松预期第1轮"
        assert result[0].intensity > 0

    @pytest.mark.asyncio
    async def test_extract_empty_evolution(self) -> None:
        router = AsyncMock()
        tracker = SentimentEvolutionTracker(router)
        raw = RawSimulationOutput(
            event_title="test",
            event_content="test",
            event_summary="test",
            initial_sentiment={"bullish": 0.33, "bearish": 0.33, "neutral": 0.34},
            sentiment_evolution=(),
        )
        result = await tracker.extract(raw)
        assert result == ()
        router.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_llm_failure_uses_fallback_intensity(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion("garbage")
        )
        tracker = SentimentEvolutionTracker(router)
        raw = _sample_raw_simulation()
        result = await tracker.extract(raw)

        assert len(result) == 5
        # Fallback: narratives are empty, intensity is heuristic
        assert all(r.dominant_narrative == "" for r in result)
        assert all(r.intensity >= 0 for r in result)

    @pytest.mark.asyncio
    async def test_detect_momentum_shift(self) -> None:
        router = AsyncMock()
        tracker = SentimentEvolutionTracker(router)

        # Create evolution with a big shift at round 3
        evolution = (
            SentimentRound(
                round=1, bullish=0.5, bearish=0.3, neutral=0.2,
                dominant_narrative="乐观", intensity=0.6,
            ),
            SentimentRound(
                round=2, bullish=0.52, bearish=0.28, neutral=0.20,
                dominant_narrative="持续乐观", intensity=0.6,
            ),
            SentimentRound(
                round=3, bullish=0.30, bearish=0.45, neutral=0.25,
                dominant_narrative="恐慌蔓延", intensity=0.8,
            ),
        )
        shifts = await tracker.detect_momentum_shift(evolution)

        assert len(shifts) == 1
        assert shifts[0].round_number == 3
        assert shifts[0].direction == "bullish_to_bearish"
        assert shifts[0].magnitude > 0.15

    @pytest.mark.asyncio
    async def test_no_momentum_shift(self) -> None:
        router = AsyncMock()
        tracker = SentimentEvolutionTracker(router)
        evolution = _sample_sentiment_rounds(3)
        shifts = await tracker.detect_momentum_shift(evolution)
        # Small increments don't trigger shift
        assert len(shifts) == 0

    @pytest.mark.asyncio
    async def test_same_direction_surge_not_reversal(self) -> None:
        """Large bullish change without dominance flip is not a reversal."""
        router = AsyncMock()
        tracker = SentimentEvolutionTracker(router)
        evolution = (
            SentimentRound(
                round=1, bullish=0.55, bearish=0.25, neutral=0.20,
                dominant_narrative="乐观", intensity=0.6,
            ),
            SentimentRound(
                round=2, bullish=0.75, bearish=0.10, neutral=0.15,
                dominant_narrative="更乐观", intensity=0.8,
            ),
        )
        shifts = await tracker.detect_momentum_shift(evolution)
        # Both rounds are bullish-dominant, no dominance flip
        assert len(shifts) == 0

    @pytest.mark.asyncio
    async def test_momentum_shift_too_few_rounds(self) -> None:
        router = AsyncMock()
        tracker = SentimentEvolutionTracker(router)
        shifts = await tracker.detect_momentum_shift(())
        assert shifts == ()
        shifts = await tracker.detect_momentum_shift(
            (_sample_sentiment_rounds(1)[0],)
        )
        assert shifts == ()


# ---------------------------------------------------------------------------
# HiddenVariableExtractor tests
# ---------------------------------------------------------------------------


class TestHiddenVariableExtractor:
    @pytest.mark.asyncio
    async def test_extract_success(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion(_valid_hidden_variable_json())
        )
        extractor = HiddenVariableExtractor(router)
        raw = _sample_raw_simulation()
        result = await extractor.extract(raw)

        assert len(result) >= 2
        assert all(isinstance(hv, EnrichedHiddenVariable) for hv in result)
        # Sorted by probability descending
        assert result[0].probability >= result[1].probability
        assert result[0].is_absent_from_original is True
        assert "simulated crowd wisdom" in result[0].disclaimer

    @pytest.mark.asyncio
    async def test_filters_non_hidden_variables(self) -> None:
        """Variables marked is_absent_from_original=false are filtered."""
        router = AsyncMock()
        mixed_json = json.dumps({
            "hidden_variables": [
                {
                    "variable": "已知事件 - 降准",
                    "probability": 0.9,
                    "reasoning": "原始事件已提及",
                    "agent_consensus_ratio": 0.8,
                    "is_absent_from_original": False,
                },
                {
                    "variable": "隐性: 外资加速",
                    "probability": 0.6,
                    "reasoning": "群体涌现",
                    "agent_consensus_ratio": 0.5,
                    "is_absent_from_original": True,
                },
            ]
        })
        router.complete = AsyncMock(
            return_value=_make_completion(mixed_json)
        )
        extractor = HiddenVariableExtractor(router)
        raw = _sample_raw_simulation()
        result = await extractor.extract(raw)

        # Only the truly hidden variable should remain
        assert len(result) == 1
        assert result[0].variable == "隐性: 外资加速"

    @pytest.mark.asyncio
    async def test_extract_llm_failure(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion("garbage")
        )
        extractor = HiddenVariableExtractor(router)
        raw = _sample_raw_simulation()
        result = await extractor.extract(raw)
        assert result == ()

    def test_assess_probability_weighted(self) -> None:
        router = AsyncMock()
        extractor = HiddenVariableExtractor(router)

        agents = (
            AgentAction(
                agent_type="institutional", action="买入", confidence=0.9
            ),
            AgentAction(
                agent_type="retail", action="买入", confidence=0.5
            ),
        )
        prob = extractor.assess_probability("外资流入", agents, 100)
        assert 0.0 < prob < 1.0

    def test_assess_probability_empty_agents(self) -> None:
        router = AsyncMock()
        extractor = HiddenVariableExtractor(router)
        prob = extractor.assess_probability("test", (), 100)
        assert prob == 0.0

    def test_assess_probability_zero_total(self) -> None:
        router = AsyncMock()
        extractor = HiddenVariableExtractor(router)
        agents = (
            AgentAction(
                agent_type="retail", action="买入", confidence=0.5
            ),
        )
        prob = extractor.assess_probability("test", agents, 0)
        assert prob == 0.0

    def test_assess_probability_institutional_weight(self) -> None:
        router = AsyncMock()
        extractor = HiddenVariableExtractor(router)

        # Same confidence but institutional weighs more
        inst = (
            AgentAction(
                agent_type="institutional", action="买入", confidence=0.8
            ),
        )
        retail = (
            AgentAction(
                agent_type="retail", action="买入", confidence=0.8
            ),
        )
        p_inst = extractor.assess_probability("test", inst, 10)
        p_retail = extractor.assess_probability("test", retail, 10)
        assert p_inst > p_retail


# ---------------------------------------------------------------------------
# InflectionPointDetector tests
# ---------------------------------------------------------------------------


class TestInflectionPointDetector:
    @pytest.mark.asyncio
    async def test_extract_success(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion(
                _valid_inflection_point_json()
            )
        )
        detector = InflectionPointDetector(router)
        evolution = _sample_sentiment_rounds()
        hidden_vars = (
            EnrichedHiddenVariable(
                variable="test",
                probability=0.5,
                reasoning="test",
            ),
        )
        result = await detector.extract(evolution, hidden_vars)

        assert len(result) >= 1
        assert all(isinstance(ip, EnrichedInflectionPoint) for ip in result)
        assert result[0].inflection_type == "sentiment_reversal"
        assert result[0].confidence == 0.75

    @pytest.mark.asyncio
    async def test_extract_empty_evolution(self) -> None:
        router = AsyncMock()
        detector = InflectionPointDetector(router)
        result = await detector.extract((), ())
        assert result == ()

    @pytest.mark.asyncio
    async def test_extract_llm_failure_uses_heuristic(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion("garbage")
        )
        detector = InflectionPointDetector(router)

        # Evolution with a bullish crossing below 50%
        evolution = (
            SentimentRound(
                round=1, bullish=0.55, bearish=0.25, neutral=0.20,
                dominant_narrative="牛市", intensity=0.7,
            ),
            SentimentRound(
                round=2, bullish=0.45, bearish=0.35, neutral=0.20,
                dominant_narrative="回调", intensity=0.6,
            ),
        )
        result = await detector.extract(evolution, ())

        # Heuristic should detect the crossing
        assert len(result) >= 1
        assert result[0].inflection_type == "sentiment_reversal"

    @pytest.mark.asyncio
    async def test_heuristic_exhaustion_detection(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion("garbage")
        )
        detector = InflectionPointDetector(router)

        evolution = (
            SentimentRound(
                round=1, bullish=0.50, bearish=0.30, neutral=0.20,
                dominant_narrative="活跃", intensity=0.8,
            ),
            SentimentRound(
                round=2, bullish=0.48, bearish=0.32, neutral=0.20,
                dominant_narrative="疲软", intensity=0.5,
            ),
        )
        result = await detector.extract(evolution, ())

        exhaustion_points = [
            p for p in result if p.inflection_type == "exhaustion"
        ]
        assert len(exhaustion_points) >= 1


# ---------------------------------------------------------------------------
# ExtremeScenarioAnalyzer tests
# ---------------------------------------------------------------------------


class TestExtremeScenarioAnalyzer:
    @pytest.mark.asyncio
    async def test_extract_success(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion(
                _valid_extreme_scenario_json()
            )
        )
        analyzer = ExtremeScenarioAnalyzer(router)
        raw = _sample_raw_simulation()
        evolution = _sample_sentiment_rounds()
        result = await analyzer.extract(raw, evolution)

        assert len(result) >= 2
        directions = {es.direction for es in result}
        assert "upside" in directions
        assert "downside" in directions
        assert all(
            isinstance(es, EnrichedExtremeScenario) for es in result
        )

    @pytest.mark.asyncio
    async def test_extract_llm_failure_adds_fallbacks(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion("garbage")
        )
        analyzer = ExtremeScenarioAnalyzer(router)
        raw = _sample_raw_simulation()
        evolution = _sample_sentiment_rounds()
        result = await analyzer.extract(raw, evolution)

        # Should still have at least 1 upside + 1 downside (fallbacks)
        assert len(result) >= 2
        directions = {es.direction for es in result}
        assert "upside" in directions
        assert "downside" in directions

    @pytest.mark.asyncio
    async def test_missing_upside_adds_fallback(self) -> None:
        router = AsyncMock()
        only_downside = json.dumps({
            "extreme_scenarios": [
                {
                    "scenario": "大跌",
                    "probability": 0.1,
                    "impact": "-5%",
                    "direction": "downside",
                    "trigger_conditions": "利空",
                    "early_warning_signals": "放量下跌",
                }
            ]
        })
        router.complete = AsyncMock(
            return_value=_make_completion(only_downside)
        )
        analyzer = ExtremeScenarioAnalyzer(router)
        raw = _sample_raw_simulation()
        result = await analyzer.extract(raw, _sample_sentiment_rounds())

        directions = {es.direction for es in result}
        assert "upside" in directions
        assert "downside" in directions
        assert len(result) >= 2


# ---------------------------------------------------------------------------
# HiddenVariableExtractionPipeline tests
# ---------------------------------------------------------------------------


class TestExtractionPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_success(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                # 1. Sentiment classification
                _make_completion(
                    _valid_sentiment_classification_json()
                ),
                # 2. Hidden variable extraction
                _make_completion(_valid_hidden_variable_json()),
                # 3. Inflection point detection
                _make_completion(_valid_inflection_point_json()),
                # 4. Extreme scenario analysis
                _make_completion(_valid_extreme_scenario_json()),
                # 5. Recommended action
                _make_completion(_valid_recommendation_json()),
            ]
        )

        pipeline = HiddenVariableExtractionPipeline(router)
        raw = _sample_raw_simulation()
        result = await pipeline.extract_all(raw)

        assert isinstance(result, ExtractionResult)
        assert result.event_summary == "央行降准50个基点"
        assert len(result.sentiment_rounds) == 5
        assert len(result.hidden_variables) >= 2
        assert len(result.inflection_points) >= 1
        assert len(result.extreme_scenarios) >= 2
        assert "短期看多" in result.recommended_action

    @pytest.mark.asyncio
    async def test_pipeline_all_extractors_fail(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion("garbage")
        )

        pipeline = HiddenVariableExtractionPipeline(router)
        raw = _sample_raw_simulation()
        result = await pipeline.extract_all(raw)

        # Pipeline should still return valid result (degraded)
        assert isinstance(result, ExtractionResult)
        assert result.event_summary == "央行降准50个基点"
        # Sentiment rounds should have fallback intensity
        assert len(result.sentiment_rounds) == 5
        # Hidden vars may be empty on parse failure
        # Extreme scenarios should have fallbacks
        assert len(result.extreme_scenarios) >= 2

    @pytest.mark.asyncio
    async def test_to_simulation_result(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                _make_completion(
                    _valid_sentiment_classification_json()
                ),
                _make_completion(_valid_hidden_variable_json()),
                _make_completion(_valid_inflection_point_json()),
                _make_completion(_valid_extreme_scenario_json()),
                _make_completion(_valid_recommendation_json()),
            ]
        )

        pipeline = HiddenVariableExtractionPipeline(router)
        raw = _sample_raw_simulation()
        extraction = await pipeline.extract_all(raw)
        config = SimulationConfig(agent_count=100, rounds=5)

        result = pipeline.to_simulation_result(
            extraction, config, cost_rmb=0.5, duration_seconds=10.0
        )

        assert isinstance(result, SimulationResult)
        assert result.event_summary == "央行降准50个基点"
        assert len(result.sentiment_evolution) == 5
        assert len(result.hidden_variables) >= 2
        assert len(result.key_inflection_points) >= 1
        assert len(result.extreme_scenarios) >= 2
        assert result.cost_rmb == 0.5
        assert result.duration_seconds == 10.0
        # Hidden variable reasoning is preserved verbatim (no disclaimer
        # string contamination — P4-T03 design: disclaimer lives on the
        # EnrichedHiddenVariable structured field, not mutated into reasoning).
        assert all(hv.reasoning for hv in result.hidden_variables)

    @pytest.mark.asyncio
    async def test_to_simulation_result_includes_momentum_shifts(
        self,
    ) -> None:
        router = AsyncMock()

        # Sentiment classification with a big shift at round 3
        sentiment_json = json.dumps({
            "rounds": [
                {"round": 1, "dominant_narrative": "乐观", "intensity": 0.6},
                {"round": 2, "dominant_narrative": "更乐观", "intensity": 0.7},
                {"round": 3, "dominant_narrative": "恐慌", "intensity": 0.9},
                {"round": 4, "dominant_narrative": "恢复", "intensity": 0.6},
                {"round": 5, "dominant_narrative": "稳定", "intensity": 0.5},
            ]
        })

        # Build a raw sim with big bullish swing
        evolution = (
            SentimentSnapshotRaw(
                round=1, bullish=0.60, bearish=0.20, neutral=0.20
            ),
            SentimentSnapshotRaw(
                round=2, bullish=0.62, bearish=0.18, neutral=0.20
            ),
            SentimentSnapshotRaw(
                round=3, bullish=0.35, bearish=0.45, neutral=0.20
            ),
            SentimentSnapshotRaw(
                round=4, bullish=0.38, bearish=0.42, neutral=0.20
            ),
            SentimentSnapshotRaw(
                round=5, bullish=0.40, bearish=0.40, neutral=0.20
            ),
        )
        raw = RawSimulationOutput(
            event_title="test",
            event_content="test content",
            event_summary="test summary",
            initial_sentiment={
                "bullish": 0.6, "bearish": 0.2, "neutral": 0.2
            },
            sentiment_evolution=evolution,
            agent_count=100,
            rounds=5,
        )

        router.complete = AsyncMock(
            side_effect=[
                _make_completion(sentiment_json),
                _make_completion(_valid_hidden_variable_json()),
                _make_completion(_valid_inflection_point_json()),
                _make_completion(_valid_extreme_scenario_json()),
                _make_completion(_valid_recommendation_json()),
            ]
        )

        pipeline = HiddenVariableExtractionPipeline(router)
        extraction = await pipeline.extract_all(raw)
        config = SimulationConfig(agent_count=100, rounds=5)
        sim_result = pipeline.to_simulation_result(
            extraction, config, cost_rmb=0.3, duration_seconds=5.0
        )

        # Momentum shifts surface as a structured field (P4-T03 design:
        # no string mutation of recommended_action).
        assert len(sim_result.momentum_shifts) >= 1
        assert sim_result.momentum_shifts[0].magnitude > 0

    @pytest.mark.asyncio
    async def test_pipeline_recommendation_fallback(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                _make_completion(
                    _valid_sentiment_classification_json()
                ),
                _make_completion(_valid_hidden_variable_json()),
                _make_completion(_valid_inflection_point_json()),
                _make_completion(_valid_extreme_scenario_json()),
                _make_completion("garbage"),  # recommendation fails
            ]
        )
        pipeline = HiddenVariableExtractionPipeline(router)
        raw = _sample_raw_simulation()
        result = await pipeline.extract_all(raw)

        assert "综合判断" in result.recommended_action


# ---------------------------------------------------------------------------
# Integration test: event -> simulator -> extraction -> result
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.fixture()
    def config_path(self, tmp_path: Path) -> Path:
        cfg = {
            "simulation": {
                "enabled": True,
                "agent_count": 100,
                "rounds": 5,
                "model": "kimi-k2.6",
                "trigger_threshold": 7,
            },
            "cost_estimate": {
                "input_price_per_1k": 0.0021,
                "output_price_per_1k": 0.0084,
                "chars_per_token": 1.5,
            },
        }
        path = tmp_path / "mirofish.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        return path

    @pytest.mark.asyncio
    async def test_end_to_end(self, config_path: Path) -> None:
        from backend.mirofish.simulator import MiroFishSimulator

        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                # Call 1: Persona generation
                _make_completion(
                    json.dumps({
                        "event_summary": "央行降准50个基点",
                        "initial_sentiment": {
                            "bullish": 0.50,
                            "bearish": 0.20,
                            "neutral": 0.30,
                        },
                        "participant_breakdown": "散户偏乐观",
                    })
                ),
                # Call 2: Evolution simulation
                _make_completion(
                    json.dumps({
                        "sentiment_evolution": [
                            {
                                "round": i,
                                "bullish": round(0.5 + i * 0.01, 3),
                                "bearish": round(0.2 - i * 0.005, 3),
                                "neutral": round(0.3 - i * 0.005, 3),
                            }
                            for i in range(1, 6)
                        ]
                    })
                ),
                # Extraction pipeline calls (5 calls)
                _make_completion(
                    _valid_sentiment_classification_json()
                ),
                _make_completion(_valid_hidden_variable_json()),
                _make_completion(_valid_inflection_point_json()),
                _make_completion(_valid_extreme_scenario_json()),
                _make_completion(_valid_recommendation_json()),
            ]
        )

        sim = MiroFishSimulator(router, config_path)
        event = EventDescription(
            title="央行宣布全面降准50个基点",
            content="中国人民银行今日宣布降准50个基点，释放资金约1万亿。",
            importance_score=8,
            sectors=("银行", "房地产"),
            stocks=("601398", "600036"),
        )
        result = await sim.run_simulation(event)

        assert isinstance(result, SimulationResult)
        assert result.event_summary == "央行降准50个基点"
        assert len(result.sentiment_evolution) == 5
        assert len(result.hidden_variables) >= 2
        assert len(result.key_inflection_points) >= 1
        assert len(result.extreme_scenarios) >= 2
        assert result.cost_rmb > 0
        assert result.duration_seconds >= 0

        # Hidden variables preserve their reasoning verbatim (disclaimer is
        # surfaced separately via the extraction layer, not mutated in).
        assert all(hv.reasoning for hv in result.hidden_variables)

        # Verify extreme scenarios preserve their direction classification
        directions = {es.direction for es in result.extreme_scenarios}
        assert "upside" in directions or "downside" in directions

        # Total calls: 2 (persona + evolution) + 5 (extraction pipeline)
        assert router.complete.call_count == 7

    @pytest.mark.asyncio
    async def test_extraction_pipeline_failure_degrades(
        self, config_path: Path
    ) -> None:
        from backend.mirofish.simulator import MiroFishSimulator

        router = AsyncMock()

        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_completion(
                    json.dumps({
                        "event_summary": "test event",
                        "initial_sentiment": {
                            "bullish": 0.4,
                            "bearish": 0.3,
                            "neutral": 0.3,
                        },
                        "participant_breakdown": "test",
                    })
                )
            if call_count == 2:
                return _make_completion(
                    json.dumps({
                        "sentiment_evolution": [
                            {
                                "round": 1,
                                "bullish": 0.4,
                                "bearish": 0.3,
                                "neutral": 0.3,
                            }
                        ]
                    })
                )
            # All extraction calls fail
            return _make_completion("garbage")

        router.complete = AsyncMock(side_effect=_side_effect)

        sim = MiroFishSimulator(router, config_path)
        event = EventDescription(
            title="test",
            content="test content",
            importance_score=8,
            sectors=("银行",),
        )
        result = await sim.run_simulation(event)

        # Degraded but valid
        assert isinstance(result, SimulationResult)
        assert result.duration_seconds >= 0
