"""Tests for Phase 5B per-agent thinking config translation."""

from __future__ import annotations

import itertools
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from pydantic import ValidationError

from backend.llm.providers import (
    AgentConfig,
    RouterConfig,
    RoutingConfig,
    ThinkingConfig,
    load_router_config,
)
from backend.llm.router import LLMRouter
from tests.conftest import make_chat_completion

# ============================================================
# Group 1: ThinkingConfig schema (unit)
# ============================================================


@pytest.mark.unit
class TestThinkingConfigSchema:
    def test_default_is_enabled_8000_all(self) -> None:
        cfg = ThinkingConfig()
        assert cfg.type == "enabled"
        assert cfg.max_tokens == 8000
        assert cfg.keep == "all"

    def test_disabled_zero_none_is_valid(self) -> None:
        cfg = ThinkingConfig(type="disabled", max_tokens=0, keep="none")
        assert cfg.type == "disabled"

    def test_disabled_with_nonzero_max_tokens_rejected(self) -> None:
        """type=disabled must imply max_tokens=0 / keep=none — Moonshot rule."""
        with pytest.raises(ValidationError):
            ThinkingConfig(type="disabled", max_tokens=100, keep="none")

    def test_disabled_with_keep_all_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ThinkingConfig(type="disabled", max_tokens=0, keep="all")

    def test_enabled_with_zero_max_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ThinkingConfig(type="enabled", max_tokens=0, keep="all")

    def test_negative_max_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ThinkingConfig(type="enabled", max_tokens=-1, keep="all")

    def test_max_tokens_above_cap_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ThinkingConfig(type="enabled", max_tokens=32_001, keep="all")

    def test_invalid_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ThinkingConfig(type="maybe")  # type: ignore[arg-type]

    def test_invalid_keep_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ThinkingConfig(keep="some")  # type: ignore[arg-type]

    def test_unknown_field_rejected(self) -> None:
        """extra='forbid' so a typo can't silently fall back to defaults."""
        with pytest.raises(ValidationError):
            ThinkingConfig(type="enabled", max_token=8000)  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        cfg = ThinkingConfig()
        with pytest.raises(ValidationError):
            cfg.max_tokens = 9999  # type: ignore[misc]


@pytest.mark.unit
class TestRoutingConfigSchema:
    def test_minimal_triage_only(self) -> None:
        cfg = RoutingConfig(triage_provider="qwen", triage_model="qwen3.6-plus")
        assert cfg.escalation_provider is None
        assert cfg.escalation_condition == {}

    def test_full_triage_to_escalation(self) -> None:
        cfg = RoutingConfig(
            triage_provider="qwen",
            triage_model="qwen3.6-plus",
            escalation_provider="kimi",
            escalation_model="kimi-k2.6",
            escalation_condition={"confidence_lt": 0.6},
        )
        assert cfg.escalation_condition == {"confidence_lt": 0.6}

    def test_partial_escalation_pair_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingConfig(
                triage_provider="qwen",
                triage_model="qwen3.6-plus",
                escalation_provider="kimi",
                # escalation_model missing
            )

    def test_condition_without_target_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingConfig(
                triage_provider="qwen",
                triage_model="qwen3.6-plus",
                escalation_condition={"confidence_lt": 0.6},
            )

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            RoutingConfig(
                triage_provider="qwen",
                triage_model="qwen3.6-plus",
                triage_modle="oops",  # type: ignore[arg-type]
            )

    def test_frozen(self) -> None:
        cfg = RoutingConfig(triage_provider="qwen", triage_model="qwen3.6-plus")
        with pytest.raises(ValidationError):
            cfg.triage_provider = "kimi"  # type: ignore[misc]


# ============================================================
# Group 2: Contract — Literal matrix + invalid-string rejection
# ============================================================
#
# SSoT §2.3 calls for hypothesis property tests. Adopting it requires
# a manifest change pending separate user approval. Until then we use
# the full Literal matrix plus targeted invalid-input lists, which
# gives equivalent coverage of the validation rules.


_VALID_KEEPS = ("all", "last_round", "none")
_ENABLED_TOKEN_SAMPLES = (1, 100, 1000, 4000, 8000, 10_000, 32_000)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("max_tokens", "keep"),
    list(itertools.product(_ENABLED_TOKEN_SAMPLES, _VALID_KEEPS)),
)
def test_enabled_thinking_accepts_valid_combos(max_tokens: int, keep: str) -> None:
    cfg = ThinkingConfig(type="enabled", max_tokens=max_tokens, keep=keep)  # type: ignore[arg-type]
    assert cfg.type == "enabled"
    assert cfg.max_tokens == max_tokens
    assert cfg.keep == keep


@pytest.mark.unit
def test_disabled_thinking_only_accepts_canonical_form() -> None:
    cfg = ThinkingConfig(type="disabled", max_tokens=0, keep="none")
    assert cfg.type == "disabled"
    assert cfg.max_tokens == 0
    assert cfg.keep == "none"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_keep",
    ["", " ", "All", "ALL", "some", "every", "last", "round", "any", "0"],
)
def test_thinking_config_rejects_invalid_keep(bad_keep: str) -> None:
    with pytest.raises(ValidationError):
        ThinkingConfig(type="enabled", max_tokens=8000, keep=bad_keep)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_type",
    ["", " ", "Enabled", "ENABLED", "on", "off", "true", "false", "yes"],
)
def test_thinking_config_rejects_invalid_type(bad_type: str) -> None:
    with pytest.raises(ValidationError):
        ThinkingConfig(type=bad_type)  # type: ignore[arg-type]


# ============================================================
# Group 3: _normalize_provider_kwargs translation (unit)
# ============================================================


@pytest.mark.unit
class TestNormalizeKwargsKimiEnabled:
    def test_emits_thinking_in_extra_body(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="kimi-k2.6",
            base_kwargs={"max_tokens": 4096, "temperature": 0.3},
            thinking=ThinkingConfig(type="enabled", max_tokens=8000, keep="all"),
        )
        # Moonshot K2.x exposes thinking via extra_body, not as a
        # top-level Chat Completions kwarg.
        assert "thinking" not in kw
        assert kw["extra_body"]["thinking"] == {
            "type": "enabled",
            "max_tokens": 8000,
        }

    def test_grows_max_tokens_by_reasoning_budget(self) -> None:
        """reasoning + completion share request-level max_tokens."""
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="kimi-k2.6",
            base_kwargs={"max_tokens": 4096},
            thinking=ThinkingConfig(type="enabled", max_tokens=8000, keep="all"),
        )
        assert kw["max_tokens"] == 4096 + 8000

    def test_forces_temperature_one_when_thinking_on(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="kimi-k2.6",
            base_kwargs={"temperature": 0.2},
            thinking=ThinkingConfig(type="enabled", max_tokens=4000, keep="all"),
        )
        assert kw["temperature"] == 1

    def test_merges_with_caller_extra_body(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="kimi-k2.6",
            base_kwargs={"extra_body": {"custom": "value"}},
            thinking=ThinkingConfig(type="enabled", max_tokens=4000, keep="all"),
        )
        assert kw["extra_body"]["custom"] == "value"
        assert kw["extra_body"]["thinking"]["type"] == "enabled"

    def test_kimi_k27_also_translated(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="kimi-k2.7-preview",
            base_kwargs={},
            thinking=ThinkingConfig(
                type="enabled", max_tokens=6000, keep="last_round"
            ),
        )
        assert kw["extra_body"]["thinking"]["max_tokens"] == 6000


@pytest.mark.unit
class TestNormalizeKwargsKimiDisabled:
    def test_emits_disabled_in_extra_body(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="kimi-k2.6",
            base_kwargs={"max_tokens": 4096, "temperature": 0.5},
            thinking=ThinkingConfig(type="disabled", max_tokens=0, keep="none"),
        )
        assert kw["extra_body"]["thinking"] == {"type": "disabled"}
        assert "thinking" not in kw

    def test_forces_temperature_06_when_thinking_off(self) -> None:
        """Kimi K2.6 non-thinking mode requires the documented constant."""
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="kimi-k2.6",
            base_kwargs={"temperature": 0.5},
            thinking=ThinkingConfig(type="disabled", max_tokens=0, keep="none"),
        )
        assert kw["temperature"] == 0.6

    def test_does_not_grow_max_tokens(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="kimi-k2.6",
            base_kwargs={"max_tokens": 4096},
            thinking=ThinkingConfig(type="disabled", max_tokens=0, keep="none"),
        )
        assert kw["max_tokens"] == 4096


@pytest.mark.unit
class TestNormalizeKwargsNonKimi:
    def test_thinking_dropped_for_qwen(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="qwen",
            model="qwen3.6-plus",
            base_kwargs={"temperature": 0.3, "max_tokens": 4096},
            thinking=ThinkingConfig(type="enabled", max_tokens=8000, keep="all"),
        )
        assert "thinking" not in kw
        assert "extra_body" not in kw
        assert kw["temperature"] == 0.3

    def test_thinking_dropped_for_deepseek(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="deepseek",
            model="deepseek-v4-pro",
            base_kwargs={"temperature": 0.3},
            thinking=ThinkingConfig(type="enabled", max_tokens=8000, keep="all"),
        )
        assert "thinking" not in kw
        assert "extra_body" not in kw

    def test_thinking_dropped_for_non_k2_kimi_model(self) -> None:
        kw = LLMRouter._normalize_provider_kwargs(
            provider_name="kimi",
            model="moonshot-v1-8k",
            base_kwargs={"temperature": 0.3},
            thinking=ThinkingConfig(type="enabled", max_tokens=8000, keep="all"),
        )
        assert "thinking" not in kw
        assert "extra_body" not in kw


# ============================================================
# Group 4: Real config round-trip (integration)
# ============================================================


_PROD_THINKING_TABLE: dict[str, tuple[str, int, str]] = {
    "news_crawler": ("disabled", 0, "none"),
    "sentiment_analyst": ("disabled", 0, "none"),
    "data_cleaner": ("disabled", 0, "none"),
    "fundamental_analyst": ("disabled", 0, "none"),
    "technical_analyst": ("disabled", 0, "none"),
    "intelligence_officer": ("enabled", 10_000, "last_round"),
    "bull_researcher": ("enabled", 8_000, "all"),
    "bear_researcher": ("enabled", 8_000, "all"),
    "risk_officer": ("enabled", 6_000, "last_round"),
    "fund_manager": ("enabled", 8_000, "last_round"),
}


@pytest.fixture(scope="module")
def production_router_config() -> RouterConfig:
    return load_router_config(
        Path(__file__).resolve().parents[1] / "config" / "agent_models.yaml"
    )


@pytest.mark.integration
class TestProductionConfigRoundTrip:
    def test_all_ten_agents_present(
        self, production_router_config: RouterConfig
    ) -> None:
        assert set(production_router_config.agents.keys()) == set(
            _PROD_THINKING_TABLE.keys()
        )

    @pytest.mark.parametrize(
        ("agent_name", "expected"),
        list(_PROD_THINKING_TABLE.items()),
        ids=list(_PROD_THINKING_TABLE.keys()),
    )
    def test_each_agent_thinking_matches_ssot_table(
        self,
        production_router_config: RouterConfig,
        agent_name: str,
        expected: tuple[str, int, str],
    ) -> None:
        """Locks down SSoT §704-727 — silent default-fallback regressions fail here."""
        cfg = production_router_config.agents[agent_name].thinking
        assert (cfg.type, cfg.max_tokens, cfg.keep) == expected

    def test_legacy_agent_config_without_thinking_still_loads(
        self, tmp_path: Path
    ) -> None:
        """Backwards compat: agents without explicit thinking get the default."""
        legacy = tmp_path / "legacy.yaml"
        legacy.write_text(
            """\
providers:
  qwen:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "${DASHSCOPE_API_KEY}"
    default_model: "qwen3.6-plus"

defaults:
  temperature: 0.3
  max_tokens: 4096

agents:
  legacy_agent:
    name: "Legacy"
    provider: qwen
    model: qwen3.6-plus
    frequency: "daily"
    task: "test"
""",
            encoding="utf-8",
        )
        cfg = load_router_config(legacy)
        agent = cfg.agents["legacy_agent"]
        assert isinstance(agent.thinking, ThinkingConfig)
        assert agent.thinking.type == "enabled"
        assert agent.thinking.max_tokens == 8000
        assert agent.thinking.keep == "all"
        assert agent.routing is None

    @pytest.mark.parametrize(
        ("bad_field", "match"),
        [
            (
                "    provider: qwn\n    model: qwen3.6-plus\n",
                r"agents\.typo\.provider",
            ),
            (
                "    provider: qwen\n    model: qwen3.6-plus\n"
                "    fallback: { provider: qwn, model: qwen3.6-plus }\n",
                r"agents\.typo\.fallback\.provider",
            ),
            (
                "    provider: qwen\n    model: qwen3.6-plus\n"
                "    routing:\n"
                "      triage_provider: qwn\n"
                "      triage_model: qwen3.6-plus\n",
                r"agents\.typo\.routing\.triage_provider",
            ),
            (
                "    provider: qwen\n    model: qwen3.6-plus\n"
                "    routing:\n"
                "      triage_provider: qwen\n"
                "      triage_model: qwen3.6-plus\n"
                "      escalation_provider: kimii\n"
                "      escalation_model: kimi-k2.6\n",
                r"agents\.typo\.routing\.escalation_provider",
            ),
        ],
        ids=["agent.provider", "fallback", "routing.triage", "routing.escalation"],
    )
    def test_router_config_rejects_unknown_provider_reference(
        self, tmp_path: Path, bad_field: str, match: str
    ) -> None:
        """Each cross-field validator branch fires on its own typo."""
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            f"""\
providers:
  qwen:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "${{DASHSCOPE_API_KEY}}"
    default_model: "qwen3.6-plus"
  kimi:
    base_url: "https://api.moonshot.cn/v1"
    api_key: "${{MOONSHOT_API_KEY}}"
    default_model: "kimi-k2.6"

agents:
  typo:
    name: "Typo"
{bad_field}    frequency: "daily"
    task: "x"
""",
            encoding="utf-8",
        )
        with pytest.raises(ValidationError, match=match):
            load_router_config(bad)


# ============================================================
# Group 5: complete() integration with thinking (integration)
# ============================================================


@pytest.fixture()
def kimi_thinking_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "agent_models.yaml"
    path.write_text(
        """\
providers:
  kimi:
    base_url: "https://api.moonshot.cn/v1"
    api_key: "${MOONSHOT_API_KEY}"
    default_model: "kimi-k2.6"

defaults:
  temperature: 0.3
  max_tokens: 4096

agents:
  bull:
    name: "Bull"
    provider: kimi
    model: kimi-k2.6
    thinking:
      type: enabled
      max_tokens: 8000
      keep: all
    frequency: "daily"
    task: "build bull thesis"
  cheap_summary:
    name: "Summary"
    provider: kimi
    model: kimi-k2.6
    thinking:
      type: disabled
      max_tokens: 0
      keep: none
    frequency: "daily"
    task: "summarize"
""",
        encoding="utf-8",
    )
    return path


@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_passes_thinking_via_extra_body_when_enabled(
    kimi_thinking_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    router = LLMRouter(config_path=kimi_thinking_yaml)
    await router.initialize(redis_client=mock_redis)

    mock_response = make_chat_completion()
    with patch.object(router, "_get_client") as mock_get:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_client

        await router.complete("bull", [{"role": "user", "content": "hi"}])

        mock_client.chat.completions.create.assert_awaited_once()
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"]["thinking"] == {
            "type": "enabled",
            "max_tokens": 8000,
        }
        assert kwargs["temperature"] == 1
        assert kwargs["max_tokens"] == 4096 + 8000

    await router.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_passes_thinking_disabled_via_extra_body(
    kimi_thinking_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    router = LLMRouter(config_path=kimi_thinking_yaml)
    await router.initialize(redis_client=mock_redis)

    mock_response = make_chat_completion()
    with patch.object(router, "_get_client") as mock_get:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_get.return_value = mock_client

        await router.complete(
            "cheap_summary",
            [{"role": "user", "content": "hi"}],
            temperature=0.2,
        )

        mock_client.chat.completions.create.assert_awaited_once()
        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
        # Kimi non-thinking mode pins temperature to the documented constant
        assert kwargs["temperature"] == 0.6

    await router.close()


# ============================================================
# Group 6: Fallback path with thinking propagation (integration)
# ============================================================


@pytest.fixture()
def fallback_yaml(tmp_path: Path) -> Path:
    """Two agents covering both directions of fallback."""
    path = tmp_path / "fallback.yaml"
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
  qwen_first:
    name: "QwenFirst"
    provider: qwen
    model: qwen3.6-plus
    fallback: { provider: kimi, model: kimi-k2.6 }
    thinking:
      type: enabled
      max_tokens: 4000
      keep: all
    frequency: "daily"
    task: "primary qwen, fallback kimi"
  kimi_first:
    name: "KimiFirst"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: qwen, model: qwen3.6-plus }
    thinking:
      type: enabled
      max_tokens: 4000
      keep: all
    frequency: "daily"
    task: "primary kimi, fallback qwen"
""",
        encoding="utf-8",
    )
    return path


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fallback_to_kimi_propagates_thinking(
    fallback_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    """qwen primary → kimi fallback: thinking must reach the fallback call."""
    router = LLMRouter(config_path=fallback_yaml)
    await router.initialize(redis_client=mock_redis)

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(
        side_effect=openai.APIConnectionError(request=MagicMock())
    )
    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(return_value=make_chat_completion())

    def get_client(name: str) -> AsyncMock:
        return qwen_client if name == "qwen" else kimi_client

    with patch.object(router, "_get_client", side_effect=get_client):
        await router.complete("qwen_first", [{"role": "user", "content": "hi"}])

    qwen_client.chat.completions.create.assert_awaited_once()
    kimi_client.chat.completions.create.assert_awaited_once()
    fallback_kwargs = kimi_client.chat.completions.create.call_args.kwargs
    assert fallback_kwargs["extra_body"]["thinking"]["type"] == "enabled"
    assert fallback_kwargs["extra_body"]["thinking"]["max_tokens"] == 4000
    assert fallback_kwargs["temperature"] == 1
    await router.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fallback_to_qwen_drops_thinking(
    fallback_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    """kimi primary → qwen fallback: thinking must NOT leak to qwen."""
    router = LLMRouter(config_path=fallback_yaml)
    await router.initialize(redis_client=mock_redis)

    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(
        side_effect=openai.APIConnectionError(request=MagicMock())
    )
    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=make_chat_completion())

    def get_client(name: str) -> AsyncMock:
        return kimi_client if name == "kimi" else qwen_client

    with patch.object(router, "_get_client", side_effect=get_client):
        await router.complete("kimi_first", [{"role": "user", "content": "hi"}])

    qwen_client.chat.completions.create.assert_awaited_once()
    fallback_kwargs = qwen_client.chat.completions.create.call_args.kwargs
    assert "thinking" not in fallback_kwargs
    assert "extra_body" not in fallback_kwargs
    await router.close()


# ============================================================
# Group 7: routing/escalation hook (integration)
# ============================================================


@pytest.fixture()
def routing_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "routing.yaml"
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_routing_uses_triage_provider_first(
    routing_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    router = LLMRouter(config_path=routing_yaml)
    await router.initialize(redis_client=mock_redis)

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=make_chat_completion())
    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(return_value=make_chat_completion())

    def get_client(name: str) -> AsyncMock:
        return qwen_client if name == "qwen" else kimi_client

    with patch.object(router, "_get_client", side_effect=get_client):
        await router.complete("tiered", [{"role": "user", "content": "hi"}])

    qwen_client.chat.completions.create.assert_awaited_once()
    # Default _should_escalate returns False — escalation must NOT fire
    kimi_client.chat.completions.create.assert_not_awaited()
    await router.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_routing_escalates_with_full_kwargs(
    routing_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    """When _should_escalate fires, escalation provider gets thinking config."""
    router = LLMRouter(config_path=routing_yaml)
    await router.initialize(redis_client=mock_redis)

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=make_chat_completion())
    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(return_value=make_chat_completion())

    def get_client(name: str) -> AsyncMock:
        return qwen_client if name == "qwen" else kimi_client

    with (
        patch.object(router, "_get_client", side_effect=get_client),
        patch.object(router, "_should_escalate", return_value=True),
    ):
        await router.complete("tiered", [{"role": "user", "content": "hi"}])

    qwen_client.chat.completions.create.assert_awaited_once()
    kimi_client.chat.completions.create.assert_awaited_once()

    triage_call = qwen_client.chat.completions.create.call_args
    assert triage_call.kwargs["model"] == "qwen3.6-plus"
    assert "thinking" not in triage_call.kwargs
    assert "extra_body" not in triage_call.kwargs

    esc_call = kimi_client.chat.completions.create.call_args
    assert esc_call.kwargs["model"] == "kimi-k2.6"
    assert esc_call.kwargs["extra_body"]["thinking"] == {
        "type": "enabled",
        "max_tokens": 4000,
    }
    assert esc_call.kwargs["temperature"] == 1
    await router.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_escalation_error_propagates(
    routing_yaml: Path, mock_env_vars: None, mock_redis: AsyncMock
) -> None:
    """Escalation API error must propagate (no agent has fallback here)."""
    router = LLMRouter(config_path=routing_yaml)
    await router.initialize(redis_client=mock_redis)

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=make_chat_completion())
    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(
        side_effect=openai.APIConnectionError(request=MagicMock())
    )

    def get_client(name: str) -> AsyncMock:
        return qwen_client if name == "qwen" else kimi_client

    with (
        patch.object(router, "_get_client", side_effect=get_client),
        patch.object(router, "_should_escalate", return_value=True),
    ):
        with pytest.raises(openai.APIConnectionError):
            await router.complete("tiered", [{"role": "user", "content": "hi"}])
    await router.close()


@pytest.fixture()
def routing_with_fallback_yaml(tmp_path: Path) -> Path:
    """Tiered agent that ALSO has a fallback — locks the contract that
    escalation failure must not be silently absorbed by the primary
    fallback path.
    """
    path = tmp_path / "routing_fb.yaml"
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
  deepseek:
    base_url: "https://api.deepseek.com/v1"
    api_key: "${DEEPSEEK_API_KEY}"
    default_model: "deepseek-v4-pro"

defaults:
  temperature: 0.3
  max_tokens: 4096

agents:
  tiered_fb:
    name: "TieredFallback"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: deepseek, model: deepseek-v4-pro }
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
    task: "tiered with fallback"
""",
        encoding="utf-8",
    )
    return path


@pytest.mark.integration
@pytest.mark.asyncio
async def test_escalation_error_not_caught_by_primary_fallback(
    routing_with_fallback_yaml: Path,
    mock_env_vars: None,
    mock_redis: AsyncMock,
) -> None:
    """Even when the agent has a fallback, escalation failure propagates.

    Fallback is wired only for the *primary/triage* call. Once triage
    succeeds and we elect to escalate, an escalation error must NOT
    silently route into the fallback provider — that would mask
    cost/latency anomalies the operator needs to see.
    """
    router = LLMRouter(config_path=routing_with_fallback_yaml)
    await router.initialize(redis_client=mock_redis)

    qwen_client = AsyncMock()
    qwen_client.chat.completions.create = AsyncMock(return_value=make_chat_completion())
    kimi_client = AsyncMock()
    kimi_client.chat.completions.create = AsyncMock(
        side_effect=openai.APIConnectionError(request=MagicMock())
    )
    deepseek_client = AsyncMock()
    deepseek_client.chat.completions.create = AsyncMock(
        return_value=make_chat_completion()
    )

    def get_client(name: str) -> AsyncMock:
        return {
            "qwen": qwen_client,
            "kimi": kimi_client,
            "deepseek": deepseek_client,
        }[name]

    with (
        patch.object(router, "_get_client", side_effect=get_client),
        patch.object(router, "_should_escalate", return_value=True),
    ):
        with pytest.raises(openai.APIConnectionError):
            await router.complete(
                "tiered_fb", [{"role": "user", "content": "hi"}]
            )

    # Triage ran, escalation ran and failed; fallback must NOT have been
    # invoked — that's the whole point of the contract.
    qwen_client.chat.completions.create.assert_awaited_once()
    kimi_client.chat.completions.create.assert_awaited_once()
    deepseek_client.chat.completions.create.assert_not_awaited()
    await router.close()


@pytest.mark.unit
def test_should_escalate_default_returns_false() -> None:
    """Until P5B-T03 plugs in real logic, escalation must never fire."""
    router = LLMRouter(config_path=Path("/dev/null"))
    agent = AgentConfig(
        name="x",
        provider="kimi",
        model="kimi-k2.6",
        routing=RoutingConfig(
            triage_provider="qwen",
            triage_model="qwen3.6-plus",
            escalation_provider="kimi",
            escalation_model="kimi-k2.6",
            escalation_condition={"confidence_lt": 0.6},
        ),
    )
    response = make_chat_completion()
    assert router._should_escalate(agent, response) is False
