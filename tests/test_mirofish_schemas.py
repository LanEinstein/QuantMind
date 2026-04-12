"""Tests for MiroFish schemas (TDD RED -> GREEN)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.mirofish.schemas import (
    EventDescription,
    ExtremeScenario,
    HiddenVariable,
    InflectionPoint,
    MomentumShift,
    SentimentSnapshot,
    SimulationConfig,
    SimulationResult,
)


class TestEventDescription:
    def test_create_valid(self) -> None:
        ev = EventDescription(
            title="央行宣布降准50个基点",
            content="中国人民银行今日宣布...",
            importance_score=9,
            sectors=("银行", "房地产"),
            stocks=("601398", "600036"),
        )
        assert ev.title == "央行宣布降准50个基点"
        assert ev.importance_score == 9
        assert len(ev.sectors) == 2

    def test_frozen(self) -> None:
        ev = EventDescription(
            title="test", content="test",
            importance_score=5, sectors=(), stocks=(),
        )
        with pytest.raises(ValidationError):
            ev.title = "changed"  # type: ignore[misc]

    def test_importance_score_range(self) -> None:
        with pytest.raises(ValidationError):
            EventDescription(
                title="t", content="c",
                importance_score=11, sectors=(), stocks=(),
            )
        with pytest.raises(ValidationError):
            EventDescription(
                title="t", content="c",
                importance_score=-1, sectors=(), stocks=(),
            )

    def test_empty_sectors_stocks(self) -> None:
        ev = EventDescription(
            title="t", content="c",
            importance_score=5, sectors=(), stocks=(),
        )
        assert ev.sectors == ()
        assert ev.stocks == ()


class TestSimulationConfig:
    def test_defaults(self) -> None:
        cfg = SimulationConfig()
        assert cfg.agent_count == 300
        assert cfg.rounds == 20
        assert cfg.model == "MiniMax-M2.5"

    def test_agent_count_range(self) -> None:
        with pytest.raises(ValidationError):
            SimulationConfig(agent_count=10)
        with pytest.raises(ValidationError):
            SimulationConfig(agent_count=2000)

    def test_rounds_range(self) -> None:
        with pytest.raises(ValidationError):
            SimulationConfig(rounds=2)


class TestSentimentSnapshot:
    def test_valid(self) -> None:
        s = SentimentSnapshot(round=1, bullish=0.45, bearish=0.30, neutral=0.25)
        assert s.round == 1
        assert s.bullish == 0.45

    def test_sum_approximately_one(self) -> None:
        # Should pass with small rounding error
        s = SentimentSnapshot(round=1, bullish=0.34, bearish=0.33, neutral=0.33)
        assert abs(s.bullish + s.bearish + s.neutral - 1.0) < 0.05

    def test_sum_far_from_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SentimentSnapshot(round=1, bullish=0.8, bearish=0.8, neutral=0.8)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SentimentSnapshot(round=1, bullish=-0.1, bearish=0.5, neutral=0.6)

    def test_frozen(self) -> None:
        s = SentimentSnapshot(round=1, bullish=0.5, bearish=0.3, neutral=0.2)
        with pytest.raises(ValidationError):
            s.bullish = 0.9  # type: ignore[misc]


class TestHiddenVariable:
    def test_valid(self) -> None:
        hv = HiddenVariable(
            variable="外资加速流入概率",
            probability=0.72,
            reasoning="降准信号叠加人民币企稳",
        )
        assert hv.probability == 0.72

    def test_probability_range(self) -> None:
        with pytest.raises(ValidationError):
            HiddenVariable(variable="x", probability=1.5, reasoning="y")


class TestInflectionPoint:
    def test_valid(self) -> None:
        ip = InflectionPoint(day=3, event="情绪高点")
        assert ip.day == 3

    def test_day_min(self) -> None:
        with pytest.raises(ValidationError):
            InflectionPoint(day=0, event="x")


class TestExtremeScenario:
    def test_valid(self) -> None:
        es = ExtremeScenario(
            scenario="超预期利好", probability=0.15, impact="+3-5%"
        )
        assert es.probability == 0.15


class TestMomentumShift:
    def test_valid(self) -> None:
        ms = MomentumShift(
            round_number=3,
            direction="bullish_to_bearish",
            magnitude=0.23,
            trigger_narrative="外资大幅流出",
        )
        assert ms.round_number == 3
        assert ms.magnitude == 0.23

    def test_round_number_minimum_two(self) -> None:
        with pytest.raises(ValidationError):
            MomentumShift(
                round_number=1,
                direction="bearish_to_bullish",
                magnitude=0.1,
            )

    def test_trigger_narrative_defaults_empty(self) -> None:
        ms = MomentumShift(
            round_number=2, direction="bearish_to_bullish", magnitude=0.5
        )
        assert ms.trigger_narrative == ""


class TestSentimentSnapshotEnriched:
    def test_accepts_narrative_and_intensity(self) -> None:
        s = SentimentSnapshot(
            round=1,
            bullish=0.5,
            bearish=0.3,
            neutral=0.2,
            dominant_narrative="降准预期升温",
            intensity=0.8,
        )
        assert s.dominant_narrative == "降准预期升温"
        assert s.intensity == 0.8

    def test_defaults_intensity_to_half(self) -> None:
        s = SentimentSnapshot(round=1, bullish=0.4, bearish=0.3, neutral=0.3)
        assert s.intensity == 0.5
        assert s.dominant_narrative == ""


class TestHiddenVariableEnriched:
    def test_consensus_ratio_defaults_zero(self) -> None:
        hv = HiddenVariable(variable="x", probability=0.5, reasoning="r")
        assert hv.agent_consensus_ratio == 0.0

    def test_is_absent_defaults_true(self) -> None:
        hv = HiddenVariable(variable="x", probability=0.5, reasoning="r")
        assert hv.is_absent_from_original is True

    def test_accepts_explicit_values(self) -> None:
        hv = HiddenVariable(
            variable="外资净流入",
            probability=0.72,
            reasoning="北向资金连续三日净买入",
            agent_consensus_ratio=0.68,
            is_absent_from_original=False,
        )
        assert hv.agent_consensus_ratio == 0.68
        assert hv.is_absent_from_original is False


class TestInflectionPointEnriched:
    def test_before_after_dicts_default_empty(self) -> None:
        ip = InflectionPoint(day=3, event="情绪高点")
        assert ip.before_sentiment == {}
        assert ip.after_sentiment == {}

    def test_confidence_defaults_half(self) -> None:
        ip = InflectionPoint(day=3, event="情绪高点")
        assert ip.confidence == 0.5

    def test_confidence_range_enforced(self) -> None:
        with pytest.raises(ValidationError):
            InflectionPoint(day=3, event="x", confidence=1.5)
        with pytest.raises(ValidationError):
            InflectionPoint(day=3, event="x", confidence=-0.1)

    def test_accepts_full_enriched_fields(self) -> None:
        ip = InflectionPoint(
            day=5,
            event="情绪逆转",
            inflection_type="sentiment_reversal",
            before_sentiment={"bullish": 0.3, "bearish": 0.5, "neutral": 0.2},
            after_sentiment={"bullish": 0.6, "bearish": 0.2, "neutral": 0.2},
            confidence=0.85,
        )
        assert ip.inflection_type == "sentiment_reversal"
        assert ip.before_sentiment["bullish"] == 0.3
        assert ip.confidence == 0.85


class TestExtremeScenarioEnriched:
    def test_direction_accepts_upside_downside(self) -> None:
        es_up = ExtremeScenario(
            scenario="超预期利好", probability=0.1, impact="+5%", direction="upside"
        )
        es_down = ExtremeScenario(
            scenario="黑天鹅", probability=0.05, impact="-10%", direction="downside"
        )
        assert es_up.direction == "upside"
        assert es_down.direction == "downside"

    def test_direction_defaults_empty(self) -> None:
        es = ExtremeScenario(scenario="s", probability=0.1, impact="+3%")
        assert es.direction == ""
        assert es.trigger_conditions == ""
        assert es.early_warning_signals == ""


class TestSimulationResultEnriched:
    def test_momentum_shifts_default_empty_tuple(self) -> None:
        result = SimulationResult(
            event_summary="test",
            simulation_config=SimulationConfig(),
            sentiment_evolution=(),
            hidden_variables=(),
            key_inflection_points=(),
            extreme_scenarios=(),
            recommended_action="看多",
            cost_rmb=0.0,
            duration_seconds=0.0,
        )
        assert result.momentum_shifts == ()

    def test_momentum_shifts_round_trip(self) -> None:
        shift = MomentumShift(
            round_number=3, direction="bullish_to_bearish", magnitude=0.23
        )
        result = SimulationResult(
            event_summary="央行降准",
            simulation_config=SimulationConfig(),
            sentiment_evolution=(),
            hidden_variables=(),
            key_inflection_points=(),
            extreme_scenarios=(),
            momentum_shifts=(shift,),
            recommended_action="看空",
            cost_rmb=1.0,
            duration_seconds=15.0,
        )
        data = result.model_dump()
        assert len(data["momentum_shifts"]) == 1
        assert data["momentum_shifts"][0]["direction"] == "bullish_to_bearish"


class TestLegacyDocumentBackwardCompat:
    def test_legacy_document_without_enriched_fields_validates(self) -> None:
        """Old MongoDB documents lacking enriched fields must deserialize cleanly."""
        legacy_doc = {
            "event_summary": "央行降准",
            "simulation_config": {
                "agent_count": 300,
                "rounds": 20,
                "model": "MiniMax-M2.5",
            },
            "sentiment_evolution": [
                {"round": 1, "bullish": 0.4, "bearish": 0.3, "neutral": 0.3}
            ],
            "hidden_variables": [
                {"variable": "x", "probability": 0.5, "reasoning": "r"}
            ],
            "key_inflection_points": [{"day": 3, "event": "拐点"}],
            "extreme_scenarios": [
                {"scenario": "s", "probability": 0.1, "impact": "+3%"}
            ],
            "recommended_action": "看多",
            "cost_rmb": 2.5,
            "duration_seconds": 30.0,
        }
        result = SimulationResult.model_validate(legacy_doc)
        # New fields must have safe defaults — not raise
        assert result.sentiment_evolution[0].intensity == 0.5
        assert result.sentiment_evolution[0].dominant_narrative == ""
        assert result.hidden_variables[0].agent_consensus_ratio == 0.0
        assert result.hidden_variables[0].is_absent_from_original is True
        assert result.key_inflection_points[0].inflection_type == ""
        assert result.key_inflection_points[0].before_sentiment == {}
        assert result.key_inflection_points[0].confidence == 0.5
        assert result.extreme_scenarios[0].direction == ""
        assert result.momentum_shifts == ()


class TestSimulationResult:
    def test_full_construction(self) -> None:
        result = SimulationResult(
            event_summary="央行降准",
            simulation_config=SimulationConfig(),
            sentiment_evolution=(
                SentimentSnapshot(round=1, bullish=0.4, bearish=0.3, neutral=0.3),
            ),
            hidden_variables=(
                HiddenVariable(variable="x", probability=0.5, reasoning="r"),
            ),
            key_inflection_points=(
                InflectionPoint(day=3, event="拐点"),
            ),
            extreme_scenarios=(
                ExtremeScenario(scenario="极端", probability=0.1, impact="+5%"),
            ),
            recommended_action="看多",
            cost_rmb=2.5,
            duration_seconds=30.0,
        )
        assert result.event_summary == "央行降准"
        assert len(result.sentiment_evolution) == 1

    def test_frozen(self) -> None:
        result = SimulationResult(
            event_summary="test",
            simulation_config=SimulationConfig(),
            sentiment_evolution=(),
            hidden_variables=(),
            key_inflection_points=(),
            extreme_scenarios=(),
            recommended_action="test",
            cost_rmb=0.0,
            duration_seconds=0.0,
        )
        with pytest.raises(ValidationError):
            result.event_summary = "changed"  # type: ignore[misc]

    def test_model_dump_shape(self) -> None:
        result = SimulationResult(
            event_summary="央行降准",
            simulation_config=SimulationConfig(
                agent_count=300, rounds=20, model="MiniMax-M2.5"
            ),
            sentiment_evolution=(
                SentimentSnapshot(round=1, bullish=0.45, bearish=0.3, neutral=0.25),
            ),
            hidden_variables=(
                HiddenVariable(variable="v", probability=0.7, reasoning="r"),
            ),
            key_inflection_points=(
                InflectionPoint(day=3, event="e"),
            ),
            extreme_scenarios=(
                ExtremeScenario(scenario="s", probability=0.15, impact="+3%"),
            ),
            recommended_action="看多",
            cost_rmb=2.5,
            duration_seconds=30.0,
        )
        data = result.model_dump()
        # Blueprint 3.3 shape checks
        assert "event_summary" in data
        assert "simulation_config" in data
        assert data["simulation_config"]["agent_count"] == 300
        assert "sentiment_evolution" in data
        assert data["sentiment_evolution"][0]["round"] == 1
        assert "hidden_variables" in data
        assert "key_inflection_points" in data
        assert "extreme_scenarios" in data
        assert "recommended_action" in data
