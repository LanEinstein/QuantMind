"""Model-aware LLM cost pricing (U-D4 / P0-10-amendment-2026-05-25).

The 4 mandatory agents run on qwen; ``fund_manager`` uses the premium
``qwen3.7-max`` deep-reasoning model (config/agent_models.yaml). Until
U-D4 its tokens were billed at the cheap ``qwen`` family rate (¥1/M),
under-counting spend against the ¥20/day hard cap — and under-counting
a budget guard is the dangerous direction (silent over-spend).

These tests pin the model-aware rate path: when a model with a specific
rate is supplied, the cost is computed from that rate; otherwise the
provider-family rate is used (back-compat). The Redis key segment stays
keyed by provider family so the cost_guard daily aggregation is
unchanged — only the ``cost_rmb`` value becomes model-accurate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.llm.cost_tracker import calculate_cost
from backend.llm.fallback import (
    COST_RATES,
    MODEL_COST_RATES,
    track_usage,
)


@pytest.fixture()
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    pipe = MagicMock()
    pipe.hincrby = MagicMock()
    pipe.hincrbyfloat = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock()
    redis.pipeline = MagicMock(return_value=pipe)
    return redis


def _cost_rmb_written(mock_redis: AsyncMock) -> float:
    pipe = mock_redis.pipeline.return_value
    cost_calls = [
        c for c in pipe.hincrbyfloat.call_args_list if c.args[1] == "cost_rmb"
    ]
    assert len(cost_calls) == 1
    return float(cost_calls[0].args[2])


class TestModelCostRates:
    def test_qwen37_max_tier_present(self) -> None:
        assert "qwen3.7-max" in MODEL_COST_RATES
        rate = MODEL_COST_RATES["qwen3.7-max"]
        # Alibaba Cloud Model Studio (DashScope) qwen3-max ≤32K tier:
        # input ¥2.5/M, output ¥10/M (May 2026). Debates run a few
        # thousand tokens, far under the 32K tier boundary.
        assert rate.input_rmb_per_million == pytest.approx(2.5)
        assert rate.output_rmb_per_million == pytest.approx(10.0)

    def test_premium_tier_is_dearer_than_qwen_family(self) -> None:
        # The whole point: qwen3.7-max must not be cheaper than the
        # generic qwen family default, otherwise the guard under-counts.
        family = COST_RATES["qwen"]
        premium = MODEL_COST_RATES["qwen3.7-max"]
        assert premium.input_rmb_per_million >= family.input_rmb_per_million
        assert premium.output_rmb_per_million >= family.output_rmb_per_million


class TestTrackUsageModelAware:
    async def test_qwen37_max_uses_premium_rate(
        self, mock_redis: AsyncMock
    ) -> None:
        # 1M prompt + 1M completion at 2.5/10 → 12.5 RMB.
        await track_usage(
            mock_redis,
            "fund_manager",
            "qwen",
            1_000_000,
            1_000_000,
            model="qwen3.7-max",
        )
        assert _cost_rmb_written(mock_redis) == pytest.approx(12.5)

    async def test_unknown_model_falls_back_to_family_rate(
        self, mock_redis: AsyncMock
    ) -> None:
        # qwen family = 1.0/1.0 → 1M + 1M = 2.0 RMB.
        await track_usage(
            mock_redis,
            "fundamental_analyst",
            "qwen",
            1_000_000,
            1_000_000,
            model="qwen3.6-plus",
        )
        assert _cost_rmb_written(mock_redis) == pytest.approx(2.0)

    async def test_model_omitted_uses_family_rate(
        self, mock_redis: AsyncMock
    ) -> None:
        await track_usage(
            mock_redis, "technical_analyst", "qwen", 1_000_000, 0
        )
        assert _cost_rmb_written(mock_redis) == pytest.approx(1.0)

    async def test_redis_key_stays_keyed_by_provider_family(
        self, mock_redis: AsyncMock
    ) -> None:
        # The premium model must NOT fragment the Redis key namespace —
        # cost_guard's daily aggregation scans by provider, so the key
        # segment must remain the family, only the cost value changes.
        await track_usage(
            mock_redis,
            "fund_manager",
            "qwen",
            100,
            100,
            model="qwen3.7-max",
        )
        pipe = mock_redis.pipeline.return_value
        key = pipe.hincrby.call_args_list[0].args[0]
        assert "qwen3.7-max" not in key
        assert key.endswith(":fund_manager:qwen")


class TestCalculateCostModelAware:
    def test_qwen37_max_premium_rate(self) -> None:
        cost = calculate_cost("qwen", 1_000_000, 1_000_000, model="qwen3.7-max")
        assert cost == pytest.approx(12.5)

    def test_family_fallback_without_model(self) -> None:
        cost = calculate_cost("qwen", 1_000_000, 1_000_000)
        assert cost == pytest.approx(2.0)

    def test_unknown_provider_and_model_is_zero(self) -> None:
        assert calculate_cost("nonsense", 1_000, 1_000) == 0.0
        assert (
            calculate_cost("nonsense", 1_000, 1_000, model="also-nonsense")
            == 0.0
        )
