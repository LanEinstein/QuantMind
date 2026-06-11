"""Model-aware LLM cost pricing (P0-10-amendment-2026-06-11).

Every model actually routed in config/agent_models.yaml must carry its
own list-price tier in ``MODEL_COST_RATES`` — the 2026-06-11 audit found
the previous table under-counted spend 3-30x against the ¥100/day hard
cap, and under-counting a budget guard is the dangerous direction
(silent over-spend). Rates are pinned at official LIST prices (owner
pick: limited-time discounts lapse without notice); the provider-family
fallback in ``COST_RATES`` is pinned at each family's priciest member so
an unmapped model can never be billed below any family member.

The Redis key segment stays keyed by provider family so the cost_guard
daily aggregation is unchanged — only ``cost_rmb`` is model-accurate.
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

# Official list prices (¥/M tokens, cache-miss input), verified 2026-06-11 —
# EXCEPT kimi-k2.6, which is a deliberate over-estimate (USD $0.95/$4.00 ×
# FX 7.5, above the prevailing band) pending owner console verification
# (P0-10-amendment-2026-06-11 §6). When the owner confirms the real RMB
# list, correct MODEL_COST_RATES (and the drift-guarded constants it
# anchors) and THEN this table — not the other way around.
_EXPECTED_MODEL_RATES: dict[str, tuple[float, float]] = {
    "deepseek-v4-pro": (3.0, 6.0),
    "deepseek-v4-flash": (1.0, 2.0),
    "qwen3.6-plus": (2.0, 12.0),
    "qwen3.7-max": (12.0, 36.0),
    "kimi-k2.6": (7.5, 30.0),
}


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
    @pytest.mark.parametrize(
        ("model", "expected"), sorted(_EXPECTED_MODEL_RATES.items())
    )
    def test_every_routed_model_has_its_list_price(
        self, model: str, expected: tuple[float, float]
    ) -> None:
        assert model in MODEL_COST_RATES
        rate = MODEL_COST_RATES[model]
        assert rate.input_rmb_per_million == pytest.approx(expected[0])
        assert rate.output_rmb_per_million == pytest.approx(expected[1])

    def test_family_fallback_is_priciest_family_member(self) -> None:
        # An unmapped model billed at the family rate must never be
        # billed below ANY known family member — that would reopen the
        # silent-under-count hole the per-model table exists to close.
        families = {
            "deepseek": ("deepseek-v4-pro", "deepseek-v4-flash"),
            "qwen": ("qwen3.6-plus", "qwen3.7-max"),
            "kimi": ("kimi-k2.6",),
        }
        for family, members in families.items():
            family_rate = COST_RATES[family]
            for member in members:
                member_rate = MODEL_COST_RATES[member]
                assert (
                    family_rate.input_rmb_per_million
                    >= member_rate.input_rmb_per_million
                ), f"{family} family input rate undercuts {member}"
                assert (
                    family_rate.output_rmb_per_million
                    >= member_rate.output_rmb_per_million
                ), f"{family} family output rate undercuts {member}"


class TestTrackUsageModelAware:
    async def test_qwen37_max_uses_premium_rate(self, mock_redis: AsyncMock) -> None:
        # 1M prompt + 1M completion at 12/36 → 48 RMB.
        await track_usage(
            mock_redis,
            "fund_manager",
            "qwen",
            1_000_000,
            1_000_000,
            model="qwen3.7-max",
        )
        assert _cost_rmb_written(mock_redis) == pytest.approx(48.0)

    async def test_qwen36_plus_uses_its_own_rate(self, mock_redis: AsyncMock) -> None:
        # 1M + 1M at 2/12 → 14 RMB (no longer the flat family ¥1/¥1).
        await track_usage(
            mock_redis,
            "fundamental_analyst",
            "qwen",
            1_000_000,
            1_000_000,
            model="qwen3.6-plus",
        )
        assert _cost_rmb_written(mock_redis) == pytest.approx(14.0)

    async def test_kimi_k26_uses_its_own_rate(self, mock_redis: AsyncMock) -> None:
        # 1M + 1M at 7.5/30 → 37.5 RMB (thinking tokens bill as output).
        await track_usage(
            mock_redis,
            "thesis_reviewer",
            "kimi",
            1_000_000,
            1_000_000,
            model="kimi-k2.6",
        )
        assert _cost_rmb_written(mock_redis) == pytest.approx(37.5)

    async def test_unknown_model_string_falls_back_to_family_rate(
        self, mock_redis: AsyncMock
    ) -> None:
        # A model STRING not in MODEL_COST_RATES (e.g. a newly routed
        # model missing its tier) must bill at the family fallback —
        # NOT at ¥0. qwen family = priciest member = 12/36 → 48 RMB.
        await track_usage(
            mock_redis,
            "fund_manager",
            "qwen",
            1_000_000,
            1_000_000,
            model="qwen-not-yet-priced",
        )
        assert _cost_rmb_written(mock_redis) == pytest.approx(48.0)

    async def test_model_omitted_uses_family_rate(self, mock_redis: AsyncMock) -> None:
        # Family fallback = priciest member (qwen → 12/36).
        await track_usage(mock_redis, "technical_analyst", "qwen", 1_000_000, 0)
        assert _cost_rmb_written(mock_redis) == pytest.approx(12.0)

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
        assert cost == pytest.approx(48.0)

    def test_deepseek_v4_flash_cheap_tier(self) -> None:
        cost = calculate_cost(
            "deepseek", 1_000_000, 1_000_000, model="deepseek-v4-flash"
        )
        assert cost == pytest.approx(3.0)

    def test_family_fallback_without_model(self) -> None:
        cost = calculate_cost("qwen", 1_000_000, 1_000_000)
        assert cost == pytest.approx(48.0)

    def test_unknown_provider_and_model_is_zero(self) -> None:
        assert calculate_cost("nonsense", 1_000, 1_000) == 0.0
        assert calculate_cost("nonsense", 1_000, 1_000, model="also-nonsense") == 0.0
