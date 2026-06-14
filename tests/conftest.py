"""Shared test fixtures for QuantMind tests."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Owner production-runtime env vars that must never leak into the hermetic
# test suite. The owner's shell carries the live-trading / I-002 long-run
# staging env (interactive overlay on, pilot tier, decision chat + owner
# allowlist, prod authorization). A unit/integration test that silently
# resolves to feishu_interactive / pilot / prod because of an ambient export
# is not hermetic — that is exactly the test-isolation drift that turned 3
# orchestration tests red once the owner staged U-E5 / I-002.
_OWNER_PROD_RUNTIME_ENV = (
    "FEISHU_INTERACTIVE_ENABLED",
    "FEISHU_DECISION_CHAT_ID",
    "FEISHU_OWNER_OPEN_ID",
    "QUANTMIND_FEISHU_TIER",
    "QUANTMIND_PROD_RUN",
    "QUANTMIND_OWNER_PROD_AUTHORIZATION",
)


@pytest.fixture(autouse=True)
def _scrub_owner_prod_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the default-off baseline for owner production-runtime env vars.

    Runs before every test (``autouse``) and clears the vars in
    :data:`_OWNER_PROD_RUNTIME_ENV` so the suite behaves identically whether
    or not the owner's shell exports them. Tests that exercise the
    interactive / pilot / prod paths set the relevant var themselves; their
    ``monkeypatch.setenv`` wins because it is applied after this fixture and
    unwound in LIFO order at teardown.
    """
    for name in _OWNER_PROD_RUNTIME_ENV:
        monkeypatch.delenv(name, raising=False)

SAMPLE_YAML = """\
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
    name: "News Crawler"
    provider: deepseek
    model: deepseek-v4-pro
    fallback: { provider: qwen, model: qwen3.6-plus }
    frequency: "every_5min"
    task: "Crawl financial news"
  analyst:
    name: "Analyst"
    provider: qwen
    model: qwen3.6-plus
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
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-dashscope")
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-moonshot")


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
