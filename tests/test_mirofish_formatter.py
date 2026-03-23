"""Tests for MiroFish simulation result formatter."""

from __future__ import annotations

from backend.mirofish.formatter import format_simulation_context
from backend.mirofish.schemas import (
    ExtremeScenario,
    HiddenVariable,
    InflectionPoint,
    SentimentSnapshot,
    SimulationConfig,
    SimulationResult,
)


def _make_result(
    rounds: int = 5,
    with_hidden: bool = True,
    with_inflection: bool = True,
    with_extreme: bool = True,
    empty_evolution: bool = False,
) -> SimulationResult:
    evolution = ()
    if not empty_evolution:
        evolution = tuple(
            SentimentSnapshot(
                round=i,
                bullish=round(0.4 + i * 0.01, 3),
                bearish=round(0.3 - i * 0.005, 3),
                neutral=round(0.3 - i * 0.005, 3),
            )
            for i in range(1, rounds + 1)
        )
    return SimulationResult(
        event_summary="央行降准50个基点",
        simulation_config=SimulationConfig(),
        sentiment_evolution=evolution,
        hidden_variables=(
            HiddenVariable(
                variable="外资加速流入",
                probability=0.72,
                reasoning="降准叠加汇率企稳",
            ),
        )
        if with_hidden
        else (),
        key_inflection_points=(
            InflectionPoint(day=3, event="获利回吐"),
        )
        if with_inflection
        else (),
        extreme_scenarios=(
            ExtremeScenario(
                scenario="利好叠加", probability=0.15, impact="+3-5%"
            ),
        )
        if with_extreme
        else (),
        recommended_action="短期看多，分批建仓",
        cost_rmb=2.5,
        duration_seconds=30.0,
    )


class TestFormatSimulationContext:
    def test_empty_results_returns_empty(self) -> None:
        assert format_simulation_context(()) == ""

    def test_single_full_result(self) -> None:
        text = format_simulation_context((_make_result(),))
        assert "央行降准" in text
        assert "外资加速流入" in text
        assert "72%" in text
        assert "获利回吐" in text
        assert "Day 3" in text
        assert "利好叠加" in text
        assert "15%" in text
        assert "短期看多" in text
        assert "2.5" in text

    def test_long_evolution_truncated(self) -> None:
        text = format_simulation_context((_make_result(rounds=20),))
        assert "..." in text
        # Should not have all 20 round numbers
        lines = text.split("\n")
        round_lines = [ln for ln in lines if ln.strip().startswith("R")]
        assert len(round_lines) < 20

    def test_degraded_result_shows_warning(self) -> None:
        text = format_simulation_context(
            (_make_result(empty_evolution=True),)
        )
        assert "不完整" in text

    def test_multiple_results_separated(self) -> None:
        r1 = _make_result()
        r2 = SimulationResult(
            event_summary="美联储加息",
            simulation_config=SimulationConfig(),
            sentiment_evolution=(
                SentimentSnapshot(
                    round=1, bullish=0.3, bearish=0.4, neutral=0.3
                ),
            ),
            hidden_variables=(),
            key_inflection_points=(),
            extreme_scenarios=(),
            recommended_action="观望",
            cost_rmb=1.0,
            duration_seconds=20.0,
        )
        text = format_simulation_context((r1, r2))
        assert "---" in text
        assert "央行降准" in text
        assert "美联储加息" in text

    def test_skipped_result(self) -> None:
        r = SimulationResult(
            event_summary="小事件",
            simulation_config=SimulationConfig(),
            sentiment_evolution=(),
            hidden_variables=(),
            key_inflection_points=(),
            extreme_scenarios=(),
            recommended_action="事件重要性不足，未触发仿真",
            cost_rmb=0.0,
            duration_seconds=0.0,
        )
        text = format_simulation_context((r,))
        assert "不完整" in text or "未触发" in text
