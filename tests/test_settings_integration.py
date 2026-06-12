"""Integration tests for settings services."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.llm.cost_tracker import (
    aggregate_costs,
    calculate_cost,
)
from backend.services.config_service import ConfigService

SAMPLE_LLM_YAML = """\
# LLM Router core configuration
# Blueprint V3 section 2.2

providers:
  deepseek:
    base_url: "https://api.deepseek.com/v1"
    api_key: "${DEEPSEEK_API_KEY}"
    default_model: "deepseek-v4-pro"
  qwen:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    default_model: "qwen3.6-plus"

defaults:
  temperature: 0.3
  max_tokens: 4096

agents:
  news_crawler:
    name: "新闻爬取员"
    provider: deepseek
    model: deepseek-v4-pro
    fallback: { provider: qwen, model: qwen3.6-plus }
"""

SAMPLE_MIROFISH_YAML = """\
# MiroFish simulation adapter configuration
# Blueprint V3 section 3

simulation:
  enabled: true
  agent_count: 300
  rounds: 20
  model: "kimi-k2.6"
  trigger_threshold: 7
"""


@pytest.fixture()
def llm_config_path(tmp_path: Path) -> Path:
    """Create a temporary LLM config YAML file."""
    path = tmp_path / "agent_models.yaml"
    path.write_text(SAMPLE_LLM_YAML, encoding="utf-8")
    return path


@pytest.fixture()
def mirofish_config_path(tmp_path: Path) -> Path:
    """Create a temporary MiroFish config YAML file."""
    path = tmp_path / "mirofish.yaml"
    path.write_text(SAMPLE_MIROFISH_YAML, encoding="utf-8")
    return path


@pytest.fixture()
def config_service() -> ConfigService:
    """Create a ConfigService with no Redis (file-only mode)."""
    return ConfigService(redis_client=None)


# -- ConfigService read-only behavior (A-007) --


class TestConfigServiceReadOnly:
    """ConfigService is read-only after Phase A: hot-reload disabled
    and YAML writes destructively removed (P0-7 / P0-10 / P1-2.C / P1-7)."""

    async def test_read_yaml_loads_data(
        self, config_service: ConfigService, llm_config_path: Path
    ) -> None:
        data = await config_service.read_yaml(llm_config_path)
        assert data["providers"]["deepseek"]["default_model"] == "deepseek-v4-pro"
        assert data["defaults"]["temperature"] == 0.3

    async def test_llm_config_masks_keys(
        self, config_service: ConfigService, llm_config_path: Path
    ) -> None:
        data = await config_service.read_llm_config(llm_config_path)
        for provider in data["providers"].values():
            assert provider["api_key"] == "***masked***"

    def test_write_methods_removed(
        self, config_service: ConfigService
    ) -> None:
        """The destructive A-007 cleanup removed all write paths."""
        assert not hasattr(config_service, "write_yaml")
        assert not hasattr(config_service, "write_llm_config")
        assert not hasattr(config_service, "_notify_config_change")


# -- Cost aggregation --


class TestCostAggregation:
    """Test cost tracker aggregation with mock Redis data."""

    async def test_aggregation_with_seeded_data(self) -> None:
        """aggregate_costs correctly sums up seeded Redis entries."""
        redis_mock = AsyncMock()

        # Simulate SCAN returning keys for one date
        redis_mock.scan = AsyncMock(
            return_value=(
                0,
                [
                    "llm:usage:2026-03-25:news_crawler:deepseek",
                    "llm:usage:2026-03-25:analyst:qwen",
                ],
            )
        )

        # HGETALL returns for each key
        async def hgetall_side_effect(key: str) -> dict[str, str]:
            if "news_crawler" in key:
                return {
                    "prompt_tokens": "10000",
                    "completion_tokens": "5000",
                    "requests": "50",
                    "cost_rmb": "0.003",
                }
            return {
                "prompt_tokens": "20000",
                "completion_tokens": "10000",
                "requests": "30",
                "cost_rmb": "0.03",
            }

        redis_mock.hgetall = AsyncMock(side_effect=hgetall_side_effect)

        summary = await aggregate_costs(redis_mock, days=1, period="daily")

        assert summary.total_requests == 80
        assert summary.total_prompt_tokens == 30000
        assert summary.total_completion_tokens == 15000
        assert "news_crawler" in summary.by_agent
        assert "qwen" in summary.by_provider

    async def test_cost_calculation_deepseek(self) -> None:
        """Verify DeepSeek cost calculation accuracy."""
        # DeepSeek family fallback = priciest member, deepseek-v4-pro:
        # 3 input + 6 output RMB / million tokens (P0-10-amendment-2026-06-11).
        cost = calculate_cost("deepseek", 1_000_000, 1_000_000)
        assert cost == pytest.approx(9.0, abs=1e-6)

    async def test_cost_calculation_kimi(self) -> None:
        """Verify Kimi cost calculation accuracy."""
        # Kimi (kimi-k2.6 official RMB list): 6.5 input + 27 output per M
        cost = calculate_cost("kimi", 1_000_000, 1_000_000)
        assert cost == pytest.approx(33.5, abs=1e-6)
