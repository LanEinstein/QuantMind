"""Tests for MiroFish report parser and simulator."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.mirofish.report_parser import (
    extract_deep_json,
    parse_evolution_response,
    parse_extraction_response,
    parse_persona_response,
)
from backend.mirofish.schemas import (
    EventDescription,
    SimulationResult,
)
from backend.mirofish.simulator import MiroFishSimulator

# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestExtractDeepJson:
    def test_bare_json(self) -> None:
        result = extract_deep_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_markdown_fenced(self) -> None:
        text = '分析如下：\n```json\n{"key": "value"}\n```\n完毕。'
        result = extract_deep_json(text)
        assert result == {"key": "value"}

    def test_json_with_preamble(self) -> None:
        text = '以下是JSON结果：\n{"action": "买入", "price": 50.0}'
        result = extract_deep_json(text)
        assert result is not None
        assert result["action"] == "买入"

    def test_nested_objects(self) -> None:
        text = json.dumps({
            "sentiment_evolution": [
                {"round": 1, "bullish": 0.4, "bearish": 0.3, "neutral": 0.3}
            ]
        })
        result = extract_deep_json(text)
        assert result is not None
        assert len(result["sentiment_evolution"]) == 1

    def test_invalid_json(self) -> None:
        assert extract_deep_json("not json at all") is None

    def test_empty_string(self) -> None:
        assert extract_deep_json("") is None

    def test_array_not_dict(self) -> None:
        assert extract_deep_json("[1, 2, 3]") is None


class TestParsePersonaResponse:
    def test_valid(self) -> None:
        raw = json.dumps({
            "event_summary": "央行降准",
            "initial_sentiment": {
                "bullish": 0.5, "bearish": 0.2, "neutral": 0.3
            },
            "participant_breakdown": "散户看多",
        })
        result = parse_persona_response(raw)
        assert result is not None
        summary, sentiment = result
        assert summary == "央行降准"
        assert sentiment["bullish"] == 0.5

    def test_missing_fields(self) -> None:
        assert parse_persona_response('{"other": 1}') is None

    def test_garbage(self) -> None:
        assert parse_persona_response("这不是JSON") is None


class TestParseEvolutionResponse:
    def test_valid(self) -> None:
        data = {
            "sentiment_evolution": [
                {"round": i, "bullish": 0.4, "bearish": 0.3, "neutral": 0.3}
                for i in range(1, 6)
            ]
        }
        result = parse_evolution_response(json.dumps(data))
        assert result is not None
        assert len(result) == 5
        assert result[0].round == 1

    def test_empty_array(self) -> None:
        assert parse_evolution_response(
            '{"sentiment_evolution": []}'
        ) is None

    def test_garbage(self) -> None:
        assert parse_evolution_response("garbage") is None

    def test_invalid_items_skipped(self) -> None:
        data = {
            "sentiment_evolution": [
                {"round": 1, "bullish": 0.4, "bearish": 0.3, "neutral": 0.3},
                {"round": 2, "bullish": 5.0, "bearish": 5.0, "neutral": 5.0},
            ]
        }
        result = parse_evolution_response(json.dumps(data))
        assert result is not None
        assert len(result) == 1  # second item rejected by validator


class TestParseExtractionResponse:
    def test_valid_full(self) -> None:
        data = {
            "hidden_variables": [
                {"variable": "v", "probability": 0.7, "reasoning": "r"}
            ],
            "key_inflection_points": [{"day": 3, "event": "e"}],
            "extreme_scenarios": [
                {"scenario": "s", "probability": 0.15, "impact": "+3%"}
            ],
            "recommended_action": "看多",
        }
        result = parse_extraction_response(json.dumps(data))
        assert result is not None
        assert len(result["hidden_variables"]) == 1
        assert len(result["key_inflection_points"]) == 1
        assert result["recommended_action"] == "看多"

    def test_partial(self) -> None:
        data = {
            "hidden_variables": [
                {"variable": "v", "probability": 0.5, "reasoning": "r"}
            ],
        }
        result = parse_extraction_response(json.dumps(data))
        assert result is not None
        assert len(result["hidden_variables"]) == 1
        assert len(result["extreme_scenarios"]) == 0

    def test_garbage(self) -> None:
        assert parse_extraction_response("not json") is None


# ---------------------------------------------------------------------------
# Simulator tests
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


def _sample_event(score: int = 8) -> EventDescription:
    return EventDescription(
        title="央行宣布全面降准50个基点",
        content="中国人民银行今日宣布降准50个基点，释放资金约1万亿。",
        importance_score=score,
        sectors=("银行", "房地产"),
        stocks=("601398", "600036"),
    )


def _valid_persona_json() -> str:
    return json.dumps({
        "event_summary": "央行降准50个基点",
        "initial_sentiment": {
            "bullish": 0.50, "bearish": 0.20, "neutral": 0.30
        },
        "participant_breakdown": "散户偏乐观",
    })


def _valid_evolution_json(rounds: int = 5) -> str:
    return json.dumps({
        "sentiment_evolution": [
            {
                "round": i,
                "bullish": round(0.5 + i * 0.01, 3),
                "bearish": round(0.2 - i * 0.005, 3),
                "neutral": round(0.3 - i * 0.005, 3),
            }
            for i in range(1, rounds + 1)
        ]
    })


def _valid_extraction_json() -> str:
    return json.dumps({
        "hidden_variables": [
            {
                "variable": "外资加速流入",
                "probability": 0.72,
                "reasoning": "降准信号叠加",
            }
        ],
        "key_inflection_points": [
            {"day": 3, "event": "获利回吐"}
        ],
        "extreme_scenarios": [
            {"scenario": "利好叠加", "probability": 0.15, "impact": "+5%"}
        ],
        "recommended_action": "短期看多，分批建仓",
    })


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    cfg = {
        "simulation": {
            "enabled": True,
            "agent_count": 100,
            "rounds": 5,
            "model": "MiniMax-M2.5",
            "trigger_threshold": 7,
        },
        "cost_estimate": {
            "input_price_per_1k": 0.0021,
            "output_price_per_1k": 0.0084,
            "chars_per_token": 1.5,
        },
    }
    path = tmp_path / "mirofish.yaml"
    import yaml

    path.write_text(yaml.dump(cfg), encoding="utf-8")
    return path


class TestMiroFishSimulator:
    @pytest.mark.asyncio
    async def test_full_success(self, config_path: Path) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                _make_completion(_valid_persona_json()),
                _make_completion(_valid_evolution_json(5)),
                _make_completion(_valid_extraction_json()),
            ]
        )
        sim = MiroFishSimulator(router, config_path)
        result = await sim.run_simulation(_sample_event())

        assert isinstance(result, SimulationResult)
        assert result.event_summary == "央行降准50个基点"
        assert len(result.sentiment_evolution) == 5
        assert len(result.hidden_variables) >= 1
        assert len(result.key_inflection_points) >= 1
        assert len(result.extreme_scenarios) >= 1
        assert result.recommended_action == "短期看多，分批建仓"
        assert result.cost_rmb > 0
        assert result.duration_seconds >= 0
        assert router.complete.call_count == 3

    @pytest.mark.asyncio
    async def test_below_threshold_skips(self, config_path: Path) -> None:
        router = AsyncMock()
        sim = MiroFishSimulator(router, config_path)
        result = await sim.run_simulation(_sample_event(score=3))

        assert "未触发" in result.recommended_action
        assert result.cost_rmb == 0.0
        assert result.sentiment_evolution == ()
        router.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_persona_failure_uses_fallback(
        self, config_path: Path
    ) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                _make_completion("garbage response"),
                _make_completion(_valid_evolution_json(5)),
                _make_completion(_valid_extraction_json()),
            ]
        )
        sim = MiroFishSimulator(router, config_path)
        result = await sim.run_simulation(_sample_event())

        assert isinstance(result, SimulationResult)
        # Falls back to event.title
        assert result.event_summary == "央行宣布全面降准50个基点"
        assert len(result.sentiment_evolution) == 5

    @pytest.mark.asyncio
    async def test_evolution_failure_uses_synthetic(
        self, config_path: Path
    ) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                _make_completion(_valid_persona_json()),
                _make_completion("garbage"),
                _make_completion(_valid_extraction_json()),
            ]
        )
        sim = MiroFishSimulator(router, config_path)
        result = await sim.run_simulation(_sample_event())

        assert isinstance(result, SimulationResult)
        assert len(result.sentiment_evolution) == 5  # synthetic fallback
        # Verify synthetic evolution is valid
        for s in result.sentiment_evolution:
            assert abs(s.bullish + s.bearish + s.neutral - 1.0) < 0.05

    @pytest.mark.asyncio
    async def test_extraction_failure_returns_partial(
        self, config_path: Path
    ) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                _make_completion(_valid_persona_json()),
                _make_completion(_valid_evolution_json(5)),
                _make_completion("garbage"),
            ]
        )
        sim = MiroFishSimulator(router, config_path)
        result = await sim.run_simulation(_sample_event())

        assert isinstance(result, SimulationResult)
        assert result.hidden_variables == ()
        assert "解析失败" in result.recommended_action

    @pytest.mark.asyncio
    async def test_all_calls_fail(self, config_path: Path) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                _make_completion("bad1"),
                _make_completion("bad2"),
                _make_completion("bad3"),
            ]
        )
        sim = MiroFishSimulator(router, config_path)
        result = await sim.run_simulation(_sample_event())

        # Still returns valid result (fully degraded)
        assert isinstance(result, SimulationResult)
        assert result.cost_rmb >= 0
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_result_is_frozen(self, config_path: Path) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            side_effect=[
                _make_completion(_valid_persona_json()),
                _make_completion(_valid_evolution_json(5)),
                _make_completion(_valid_extraction_json()),
            ]
        )
        sim = MiroFishSimulator(router, config_path)
        result = await sim.run_simulation(_sample_event())
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            result.event_summary = "changed"  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_config_from_yaml(self, config_path: Path) -> None:
        router = AsyncMock()
        sim = MiroFishSimulator(router, config_path)
        assert sim._config.agent_count == 100
        assert sim._config.rounds == 5
