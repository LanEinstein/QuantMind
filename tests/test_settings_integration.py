"""Integration tests for settings services."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.llm.cost_tracker import (
    CostSummary,
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
    default_model: "deepseek-chat"
  qwen:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "${QWEN_API_KEY}"
    default_model: "qwen-plus"

defaults:
  temperature: 0.3
  max_tokens: 4096

agents:
  news_crawler:
    name: "新闻爬取员"
    provider: deepseek
    model: deepseek-chat
    fallback: { provider: qwen, model: qwen-turbo }
"""

SAMPLE_MIROFISH_YAML = """\
# MiroFish simulation adapter configuration
# Blueprint V3 section 3

simulation:
  enabled: true
  agent_count: 300
  rounds: 20
  model: "MiniMax-M2.5"
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


# -- ConfigService roundtrip --


class TestConfigServiceRoundtrip:
    """Test YAML read/write preserves comments and data."""

    async def test_roundtrip_preserves_comments(
        self, config_service: ConfigService, llm_config_path: Path
    ) -> None:
        """Write to YAML and verify comments are preserved."""
        # Read original
        original = await config_service.read_yaml(llm_config_path)
        assert original["providers"]["deepseek"]["default_model"] == "deepseek-chat"

        # Update a value
        await config_service.write_yaml(
            llm_config_path,
            {"defaults": {"temperature": 0.5}},
        )

        # Verify update applied
        updated = await config_service.read_yaml(llm_config_path)
        assert updated["defaults"]["temperature"] == 0.5
        # Original values preserved
        assert updated["defaults"]["max_tokens"] == 4096
        assert updated["providers"]["deepseek"]["default_model"] == "deepseek-chat"

        # Verify comments preserved in raw file
        raw_text = llm_config_path.read_text(encoding="utf-8")
        assert "# LLM Router core configuration" in raw_text
        assert "# Blueprint V3 section 2.2" in raw_text

    async def test_llm_config_masks_keys(
        self, config_service: ConfigService, llm_config_path: Path
    ) -> None:
        """read_llm_config returns masked API keys."""
        data = await config_service.read_llm_config(llm_config_path)
        for provider in data["providers"].values():
            assert provider["api_key"] == "***masked***"

    async def test_write_llm_config_skips_masked(
        self, config_service: ConfigService, llm_config_path: Path
    ) -> None:
        """write_llm_config does not overwrite keys with mask value."""
        await config_service.write_llm_config(
            llm_config_path,
            {
                "providers": {
                    "deepseek": {
                        "api_key": "***masked***",
                        "default_model": "deepseek-v2",
                    },
                },
            },
        )

        # Read raw (unmasked) to verify original key preserved
        raw = await config_service.read_yaml(llm_config_path)
        assert raw["providers"]["deepseek"]["api_key"] == "${DEEPSEEK_API_KEY}"
        assert raw["providers"]["deepseek"]["default_model"] == "deepseek-v2"


# -- File locking --


class TestFileLocking:
    """Test concurrent write safety."""

    async def test_concurrent_writes_no_corruption(
        self, config_service: ConfigService, mirofish_config_path: Path
    ) -> None:
        """Multiple concurrent writes don't corrupt the file."""

        async def write_value(val: int) -> None:
            await config_service.write_yaml(
                mirofish_config_path,
                {"simulation": {"agent_count": val}},
            )

        # Run 5 concurrent writes
        await asyncio.gather(
            write_value(100),
            write_value(200),
            write_value(300),
            write_value(400),
            write_value(500),
        )

        # File should be valid YAML with one of the values
        result = await config_service.read_yaml(mirofish_config_path)
        assert result["simulation"]["agent_count"] in {100, 200, 300, 400, 500}
        # Other fields preserved
        assert result["simulation"]["rounds"] == 20


# -- MiroFish config roundtrip --


class TestMiroFishConfigRoundtrip:
    """Test MiroFish config read/write cycle."""

    async def test_update_and_read_back(
        self, config_service: ConfigService, mirofish_config_path: Path
    ) -> None:
        """Write new simulation params and read them back."""
        await config_service.write_yaml(
            mirofish_config_path,
            {"simulation": {"agent_count": 500, "rounds": 30}},
        )
        result = await config_service.read_yaml(mirofish_config_path)
        assert result["simulation"]["agent_count"] == 500
        assert result["simulation"]["rounds"] == 30
        # Unchanged fields preserved
        assert result["simulation"]["trigger_threshold"] == 7
        assert result["simulation"]["model"] == "MiniMax-M2.5"


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
        # DeepSeek: 0.2 RMB / million tokens (input and output)
        cost = calculate_cost("deepseek", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.4, abs=1e-6)

    async def test_cost_calculation_minimax(self) -> None:
        """Verify MiniMax cost calculation accuracy."""
        # MiniMax: 2.1 input + 8.4 output per million tokens
        cost = calculate_cost("minimax", 1_000_000, 1_000_000)
        assert cost == pytest.approx(10.5, abs=1e-6)
