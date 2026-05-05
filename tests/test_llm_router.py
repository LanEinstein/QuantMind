"""Tests for the LLM router module."""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from backend.llm.fallback import COST_RATES, RETRYABLE_EXCEPTIONS, track_usage
from backend.llm.providers import (
    RouterConfig,
    create_openai_client,
    load_router_config,
    resolve_env_var,
)
from backend.llm.router import LLMRouter
from tests.conftest import SAMPLE_YAML, make_chat_completion

# ============================================================
# Group 1: providers.py — env var resolution, YAML loading
# ============================================================


class TestResolveEnvVar:
    def test_resolves_existing_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "secret123")
        assert resolve_env_var("${MY_KEY}") == "secret123"

    def test_missing_var_raises(self) -> None:
        with pytest.raises(ValueError, match="MY_MISSING"):
            resolve_env_var("${MY_MISSING}")

    def test_plain_string_passes_through(self) -> None:
        assert resolve_env_var("plain-key") == "plain-key"

    def test_empty_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EMPTY_KEY", "")
        with pytest.raises(ValueError, match="EMPTY_KEY"):
            resolve_env_var("${EMPTY_KEY}")


class TestLoadRouterConfig:
    def test_valid_yaml(self, sample_yaml_path: Path) -> None:
        config = load_router_config(sample_yaml_path)
        assert isinstance(config, RouterConfig)
        assert "deepseek" in config.providers
        assert "qwen" in config.providers
        assert "news_crawler" in config.agents
        assert config.agents["news_crawler"].provider == "deepseek"
        assert config.defaults.temperature == 0.3
        assert config.defaults.max_tokens == 4096

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_router_config(tmp_path / "nonexistent.yaml")

    def test_invalid_schema_raises(self, tmp_path: Path) -> None:
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("agents:\n  foo:\n    name: test\n")
        with pytest.raises(Exception):  # ValidationError
            load_router_config(bad_yaml)

    def test_config_is_frozen(self, sample_yaml_path: Path) -> None:
        config = load_router_config(sample_yaml_path)
        with pytest.raises(Exception):  # ValidationError on frozen model
            config.defaults.temperature = 0.9  # type: ignore[misc]

    def test_fallback_parsed(self, sample_yaml_path: Path) -> None:
        config = load_router_config(sample_yaml_path)
        fb = config.agents["news_crawler"].fallback
        assert fb is not None
        assert fb.provider == "qwen"
        assert fb.model == "qwen3.6-plus"

    def test_no_fallback_is_none(self, sample_yaml_path: Path) -> None:
        config = load_router_config(sample_yaml_path)
        assert config.agents["analyst"].fallback is None


class TestCreateOpenaiClient:
    def test_creates_client_with_correct_params(self, mock_env_vars: None) -> None:
        from backend.llm.providers import ProviderConfig

        cfg = ProviderConfig(
            base_url="https://api.deepseek.com/v1",
            api_key="${DEEPSEEK_API_KEY}",
            default_model="deepseek-chat",
        )
        client = create_openai_client(cfg)
        assert str(client.base_url).rstrip("/").endswith("/v1")
        assert client.api_key == "sk-test-deepseek"

    def test_client_binds_ipv4_only(self, mock_env_vars: None) -> None:
        """Locks in IPv4-only egress.

        Hosts like dashscope.aliyuncs.com publish AAAA records; in
        IPv4-only networks Happy Eyeballs stalls every parallel agent
        call until the connect timeout fires. Regression-prevention.
        """
        from backend.llm.providers import ProviderConfig

        cfg = ProviderConfig(
            base_url="https://api.deepseek.com/v1",
            api_key="${DEEPSEEK_API_KEY}",
            default_model="deepseek-chat",
        )
        client = create_openai_client(cfg)
        transport = client._client._transport  # internal but stable
        pool = transport._pool
        assert pool._local_address == "0.0.0.0"

    def test_client_has_bounded_timeout_and_retries(self, mock_env_vars: None) -> None:
        """Locks in connect/read timeouts + sdk-level retry budget.

        Defaults (600s read, 2 retries) let one stuck handshake stall
        the 900s pipeline. Bounded values keep fallback responsive.
        """
        from backend.llm.providers import ProviderConfig

        cfg = ProviderConfig(
            base_url="https://api.deepseek.com/v1",
            api_key="${DEEPSEEK_API_KEY}",
            default_model="deepseek-chat",
        )
        client = create_openai_client(cfg)
        assert client.max_retries == 1
        timeout_obj = client._client.timeout
        assert timeout_obj.connect == 10.0
        assert timeout_obj.read == 120.0


# ============================================================
# Group 2: router.py — routing to correct provider
# ============================================================


class TestRouterRouting:
    @pytest.fixture()
    async def router(
        self, sample_yaml_path: Path, mock_env_vars: None, mock_redis: AsyncMock
    ) -> LLMRouter:
        r = LLMRouter(config_path=sample_yaml_path)
        await r.initialize(redis_client=mock_redis)
        yield r  # type: ignore[misc]
        await r.close()

    async def test_routes_to_correct_provider(self, router: LLMRouter) -> None:
        mock_response = make_chat_completion()
        with patch.object(router, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_client

            await router.complete("news_crawler", [{"role": "user", "content": "hi"}])
            mock_get.assert_called_with("deepseek")

    async def test_passes_default_params(self, router: LLMRouter) -> None:
        mock_response = make_chat_completion()
        with patch.object(router, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_client

            await router.complete("news_crawler", [{"role": "user", "content": "hi"}])
            call_kwargs = mock_client.chat.completions.create.call_args
            assert call_kwargs.kwargs["temperature"] == 0.3
            assert call_kwargs.kwargs["max_tokens"] == 4096

    async def test_kwargs_override_defaults(self, router: LLMRouter) -> None:
        mock_response = make_chat_completion()
        with patch.object(router, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_client

            await router.complete(
                "news_crawler",
                [{"role": "user", "content": "hi"}],
                temperature=0.8,
            )
            call_kwargs = mock_client.chat.completions.create.call_args
            assert call_kwargs.kwargs["temperature"] == 0.8

    async def test_kimi_k26_thinking_mode_translation(
        self,
        tmp_path: Path,
        mock_env_vars: None,
        mock_redis: AsyncMock,
    ) -> None:
        """Kimi K2.6 with thinking=enabled emits Moonshot 'thinking' kwarg.

        Replaces the legacy P5A test that asserted a hardcoded
        max_tokens=16000 floor — that floor was removed in P5B-T01 so
        per-agent budgets are honored. See tests/test_llm_router_thinking
        for the full thinking-config matrix.
        """
        config_path = tmp_path / "agent_models.yaml"
        config_path.write_text(
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
  risk_officer:
    name: "Risk Officer"
    provider: kimi
    model: kimi-k2.6
    thinking:
      type: enabled
      max_tokens: 6000
      keep: last_round
    frequency: "daily"
    task: "Assess risk"
""",
            encoding="utf-8",
        )
        router = LLMRouter(config_path=config_path)
        await router.initialize(redis_client=mock_redis)

        mock_response = make_chat_completion()
        with patch.object(router, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_client

            await router.complete(
                "risk_officer",
                [{"role": "user", "content": "hi"}],
                temperature=0.2,
                extra_body={"custom": "value"},
            )

        call_kwargs = mock_client.chat.completions.create.call_args
        # thinking lives in extra_body for Moonshot SDK; temp=1 while on
        assert call_kwargs.kwargs["extra_body"]["thinking"] == {
            "type": "enabled",
            "max_tokens": 6000,
        }
        assert call_kwargs.kwargs["extra_body"]["custom"] == "value"
        assert call_kwargs.kwargs["temperature"] == 1
        # reasoning + completion share the request budget, so max_tokens
        # is grown by the reasoning cap to keep room for the actual output
        assert call_kwargs.kwargs["max_tokens"] == 4096 + 6000
        await router.close()

    async def test_unknown_agent_raises(self, router: LLMRouter) -> None:
        with pytest.raises(KeyError, match="unknown_agent"):
            await router.complete("unknown_agent", [{"role": "user", "content": "hi"}])


# ============================================================
# Group 3: fallback logic
# ============================================================


class TestRouterFallback:
    @pytest.fixture()
    async def router(
        self, sample_yaml_path: Path, mock_env_vars: None, mock_redis: AsyncMock
    ) -> LLMRouter:
        r = LLMRouter(config_path=sample_yaml_path)
        await r.initialize(redis_client=mock_redis)
        yield r  # type: ignore[misc]
        await r.close()

    @pytest.mark.parametrize(
        "exc_class",
        [
            openai.APIError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.APIConnectionError,
        ],
    )
    async def test_fallback_on_retryable_error(
        self, router: LLMRouter, exc_class: type[Exception]
    ) -> None:
        ok_response = make_chat_completion("fallback response")

        primary_client = AsyncMock()
        if exc_class == openai.APIError:
            primary_client.chat.completions.create = AsyncMock(
                side_effect=openai.APIError(
                    message="fail", request=MagicMock(), body=None
                )
            )
        elif exc_class == openai.APITimeoutError:
            primary_client.chat.completions.create = AsyncMock(
                side_effect=openai.APITimeoutError(request=MagicMock())
            )
        elif exc_class == openai.RateLimitError:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.headers = {}
            primary_client.chat.completions.create = AsyncMock(
                side_effect=openai.RateLimitError(
                    message="rate limited",
                    response=mock_resp,
                    body=None,
                )
            )
        elif exc_class == openai.APIConnectionError:
            primary_client.chat.completions.create = AsyncMock(
                side_effect=openai.APIConnectionError(request=MagicMock())
            )

        fallback_client = AsyncMock()
        fallback_client.chat.completions.create = AsyncMock(return_value=ok_response)

        def get_client(name: str) -> AsyncMock:
            return primary_client if name == "deepseek" else fallback_client

        with patch.object(router, "_get_client", side_effect=get_client):
            result = await router.complete(
                "news_crawler", [{"role": "user", "content": "hi"}]
            )
            assert result.choices[0].message.content == "fallback response"

    async def test_both_providers_fail_raises(self, router: LLMRouter) -> None:
        failing_client = AsyncMock()
        failing_client.chat.completions.create = AsyncMock(
            side_effect=openai.APIConnectionError(request=MagicMock())
        )

        with patch.object(router, "_get_client", return_value=failing_client):
            with pytest.raises(openai.APIConnectionError):
                await router.complete(
                    "news_crawler", [{"role": "user", "content": "hi"}]
                )

    async def test_no_fallback_raises_immediately(self, router: LLMRouter) -> None:
        failing_client = AsyncMock()
        failing_client.chat.completions.create = AsyncMock(
            side_effect=openai.APIConnectionError(request=MagicMock())
        )

        with patch.object(router, "_get_client", return_value=failing_client):
            with pytest.raises(openai.APIConnectionError):
                await router.complete("analyst", [{"role": "user", "content": "hi"}])


# ============================================================
# Group 4: Redis usage tracking
# ============================================================


class TestTrackUsage:
    async def test_writes_correct_redis_keys(self, mock_redis: AsyncMock) -> None:
        await track_usage(mock_redis, "news_crawler", "deepseek", 100, 200)
        pipe = mock_redis.pipeline.return_value
        # Verify pipeline methods were called
        # prompt_tokens, completion_tokens, requests
        assert pipe.hincrby.call_count >= 3
        assert pipe.hincrbyfloat.call_count >= 1  # cost_rmb
        assert pipe.expire.call_count >= 1
        # Verify key contains agent name and provider
        key = pipe.hincrby.call_args_list[0].args[0]
        assert "news_crawler" in key
        assert "deepseek" in key

    async def test_deepseek_cost_calculation(self, mock_redis: AsyncMock) -> None:
        await track_usage(mock_redis, "test_agent", "deepseek", 1_000_000, 0)
        pipe = mock_redis.pipeline.return_value
        # DeepSeek: 0.2 RMB per million input tokens
        cost_calls = [
            c for c in pipe.hincrbyfloat.call_args_list if c.args[1] == "cost_rmb"
        ]
        assert len(cost_calls) == 1
        assert abs(cost_calls[0].args[2] - 0.2) < 0.001

    async def test_kimi_split_cost(self, mock_redis: AsyncMock) -> None:
        await track_usage(mock_redis, "test_agent", "kimi", 1_000_000, 1_000_000)
        pipe = mock_redis.pipeline.return_value
        # Kimi: 2.1 input + 8.4 output = 10.5 RMB
        cost_calls = [
            c for c in pipe.hincrbyfloat.call_args_list if c.args[1] == "cost_rmb"
        ]
        assert len(cost_calls) == 1
        assert abs(cost_calls[0].args[2] - 10.5) < 0.001

    async def test_redis_none_does_not_crash(self) -> None:
        await track_usage(None, "test", "deepseek", 100, 200)

    async def test_redis_failure_does_not_crash(self) -> None:
        bad_redis = AsyncMock()
        # ``pipeline()`` is sync on real ``redis.asyncio.Redis``; using
        # an AsyncMock leaves a coroutine the test never awaits and
        # produces a RuntimeWarning.
        bad_redis.pipeline = MagicMock(side_effect=ConnectionError("redis down"))
        await track_usage(bad_redis, "test", "deepseek", 100, 200)


# ============================================================
# Group 5: YAML hot-reload
# ============================================================


class TestHotReload:
    async def test_detects_mtime_change(
        self, sample_yaml_path: Path, mock_env_vars: None, mock_redis: AsyncMock
    ) -> None:
        router = LLMRouter(config_path=sample_yaml_path)
        await router.initialize(redis_client=mock_redis)

        assert router.config.defaults.temperature == 0.3

        # Modify the YAML
        time.sleep(0.05)  # ensure mtime differs
        new_yaml = SAMPLE_YAML.replace("temperature: 0.3", "temperature: 0.7")
        sample_yaml_path.write_text(new_yaml, encoding="utf-8")

        # Trigger reload via complete
        mock_response = make_chat_completion()
        with patch.object(router, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_client
            await router.complete("news_crawler", [{"role": "user", "content": "hi"}])

        assert router.config.defaults.temperature == 0.7
        await router.close()

    async def test_no_change_skips_reload(
        self, sample_yaml_path: Path, mock_env_vars: None, mock_redis: AsyncMock
    ) -> None:
        router = LLMRouter(config_path=sample_yaml_path)
        await router.initialize(redis_client=mock_redis)

        config_before = router.config
        await router._maybe_reload_config()
        # Same object reference since no reload happened
        assert router.config is config_before
        await router.close()


# ============================================================
# Group 6: Lifecycle
# ============================================================


class TestLifecycle:
    async def test_initialize_and_close(
        self, sample_yaml_path: Path, mock_env_vars: None
    ) -> None:
        router = LLMRouter(config_path=sample_yaml_path)
        await router.initialize()
        assert router.config is not None
        assert "news_crawler" in router.config.agents
        await router.close()

    def test_config_before_init_raises(self, sample_yaml_path: Path) -> None:
        router = LLMRouter(config_path=sample_yaml_path)
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = router.config


class TestRetryableExceptions:
    def test_all_expected_exceptions_listed(self) -> None:
        assert openai.APIError in RETRYABLE_EXCEPTIONS
        assert openai.APITimeoutError in RETRYABLE_EXCEPTIONS
        assert openai.RateLimitError in RETRYABLE_EXCEPTIONS
        assert openai.APIConnectionError in RETRYABLE_EXCEPTIONS

    def test_cost_rates_defined_for_all_providers(self) -> None:
        assert "deepseek" in COST_RATES
        assert "qwen" in COST_RATES
        assert "kimi" in COST_RATES
