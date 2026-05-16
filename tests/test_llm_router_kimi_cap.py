"""H-003 cycle 3 — Kimi ¥4 daily-cap enforcement in LLMRouter.

Cycle 3 P2: assert_kimi_budget_allows existed but no production code
called it. The router must now check the Kimi cap before any
``esc_provider == 'kimi'`` escalation and skip / return the triage
response when the cap is breached.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.llm.router import LLMRouter
from backend.services.cost_guard import KimiBudgetState


def _routing_yaml(tmp_path: Path) -> Path:
    """Same tiered-agent config shape as the broader escalation suite."""
    path = tmp_path / "agent_models.yaml"
    path.write_text(
        """\
providers:
  qwen:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    default_model: "qwen3.6-plus"
  kimi:
    base_url: "https://api.moonshot.cn/v1"
    api_key: "${MOONSHOT_API_KEY}"
    default_model: "kimi-k2.6"

defaults:
  temperature: 0.3
  max_tokens: 4096

agents:
  tiered:
    name: "Tiered"
    provider: kimi
    model: kimi-k2.6
    routing:
      triage_provider: qwen
      triage_model: qwen3.6-plus
      escalation_provider: kimi
      escalation_model: kimi-k2.6
      escalation_condition:
        confidence_lt: 0.6
    thinking:
      type: enabled
      max_tokens: 4000
      keep: last_round
    frequency: "daily"
    task: "tiered example"
""",
        encoding="utf-8",
    )
    return path


def _make_completion(content: str) -> object:
    """Minimal ChatCompletion-shaped object."""
    from openai.types.chat.chat_completion import (
        ChatCompletion,
        Choice,
    )
    from openai.types.chat.chat_completion_message import (
        ChatCompletionMessage,
    )
    from openai.types.completion_usage import CompletionUsage

    return ChatCompletion(
        id="chatcmpl-test",
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(role="assistant", content=content),
            )
        ],
        created=0,
        model="qwen3.6-plus",
        object="chat.completion",
        usage=CompletionUsage(
            prompt_tokens=10, completion_tokens=20, total_tokens=30
        ),
    )


@pytest.fixture
def mock_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-dashscope-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-moonshot-key")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_kimi_cap_breach_skips_escalation(
    tmp_path: Path,
    mock_env_vars: None,
) -> None:
    """Kimi ¥4 hard breach must veto the escalation, return triage result."""
    routing = _routing_yaml(tmp_path)
    # AsyncMock Redis returns AsyncMock from .get(), so the soft-degrade
    # str/bytes guard returns False; the Kimi cap path is what we test.
    redis_mock = AsyncMock()

    router = LLMRouter(config_path=routing)
    await router.initialize(redis_client=redis_mock)

    triage_resp = _make_completion('{"confidence": 0.3, "action": "wait"}')
    esc_resp = _make_completion("kimi escalated answer")
    qwen = AsyncMock()
    qwen.chat.completions.create = AsyncMock(return_value=triage_resp)
    kimi = AsyncMock()
    kimi.chat.completions.create = AsyncMock(return_value=esc_resp)

    def get_client(name: str) -> AsyncMock:
        return qwen if name == "qwen" else kimi

    kimi_state = KimiBudgetState(
        kimi_daily_cap=4.0,
        spent_today=4.5,
        remaining=0.0,
        status="hard_breach",
    )

    with patch.object(router, "_get_client", side_effect=get_client), patch(
        "backend.services.cost_guard.get_kimi_budget_state",
        new_callable=AsyncMock,
        return_value=kimi_state,
    ):
        result = await router.complete(
            "tiered", [{"role": "user", "content": "hi"}]
        )

    # Triage ran, escalation did NOT.
    qwen.chat.completions.create.assert_awaited_once()
    kimi.chat.completions.create.assert_not_awaited()
    # Result is the triage response.
    assert result is triage_resp

    await router.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_kimi_cap_ok_allows_escalation(
    tmp_path: Path,
    mock_env_vars: None,
) -> None:
    """Kimi cap OK → escalation proceeds as usual."""
    routing = _routing_yaml(tmp_path)
    redis_mock = AsyncMock()

    router = LLMRouter(config_path=routing)
    await router.initialize(redis_client=redis_mock)

    triage_resp = _make_completion('{"confidence": 0.3, "action": "wait"}')
    esc_resp = _make_completion("kimi escalated answer")
    qwen = AsyncMock()
    qwen.chat.completions.create = AsyncMock(return_value=triage_resp)
    kimi = AsyncMock()
    kimi.chat.completions.create = AsyncMock(return_value=esc_resp)

    def get_client(name: str) -> AsyncMock:
        return qwen if name == "qwen" else kimi

    ok_state = KimiBudgetState(
        kimi_daily_cap=4.0,
        spent_today=1.0,
        remaining=3.0,
        status="ok",
    )
    with patch.object(router, "_get_client", side_effect=get_client), patch(
        "backend.llm.router.track_escalation",
        new_callable=AsyncMock,
    ), patch(
        "backend.services.cost_guard.get_kimi_budget_state",
        new_callable=AsyncMock,
        return_value=ok_state,
    ):
        result = await router.complete(
            "tiered", [{"role": "user", "content": "hi"}]
        )

    qwen.chat.completions.create.assert_awaited_once()
    kimi.chat.completions.create.assert_awaited_once()
    assert result is esc_resp

    await router.close()


@pytest.mark.asyncio
async def test_kimi_cap_probe_fail_open() -> None:
    """A probe exception must fail-open (escalation continues)."""
    router = LLMRouter.__new__(LLMRouter)
    router._redis = AsyncMock()
    router._log = AsyncMock()
    with patch(
        "backend.services.cost_guard.get_kimi_budget_state",
        new_callable=AsyncMock,
        side_effect=RuntimeError("redis down"),
    ):
        blocked = await router._kimi_daily_cap_breached()
    assert blocked is False


@pytest.mark.asyncio
async def test_kimi_cap_probe_no_redis_returns_false() -> None:
    router = LLMRouter.__new__(LLMRouter)
    router._redis = None
    router._log = AsyncMock()
    assert await router._kimi_daily_cap_breached() is False
