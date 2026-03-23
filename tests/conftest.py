"""Shared test fixtures for QuantMind tests."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


SAMPLE_YAML = """\
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
    name: "News Crawler"
    provider: deepseek
    model: deepseek-chat
    fallback: { provider: qwen, model: qwen-turbo }
    frequency: "every_5min"
    task: "Crawl financial news"
  analyst:
    name: "Analyst"
    provider: qwen
    model: qwen-plus
    frequency: "daily"
    task: "Analyze fundamentals"
"""


@pytest.fixture()
def sample_yaml_path(tmp_path: Path) -> Path:
    """Create a temporary agent_models.yaml with valid config."""
    path = tmp_path / "agent_models.yaml"
    path.write_text(SAMPLE_YAML, encoding="utf-8")
    return path


@pytest.fixture()
def mock_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required API key environment variables."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setenv("QWEN_API_KEY", "sk-test-qwen")
    monkeypatch.setenv("MINIMAX_API_KEY", "test-minimax")


@pytest.fixture()
def mock_redis() -> MagicMock:
    """Create a mock Redis client with pipeline support.

    Note: redis.pipeline() is sync, so use MagicMock for the pipeline
    factory. Pipeline methods (hincrby, etc.) are sync too — only
    execute() is async.
    """
    redis_mock = AsyncMock()
    pipe_mock = MagicMock()
    pipe_mock.execute = AsyncMock(return_value=[])
    # pipeline() is sync on redis.asyncio.Redis — override the AsyncMock
    # child with a plain MagicMock to prevent it returning a coroutine
    redis_mock.pipeline = MagicMock(return_value=pipe_mock)
    return redis_mock


def make_chat_completion(
    content: str = "test response",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
) -> MagicMock:
    """Create a mock ChatCompletion response."""
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    choice = MagicMock()
    choice.message.content = content

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response
