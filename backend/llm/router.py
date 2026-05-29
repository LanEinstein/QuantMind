"""Core LLM router for multi-provider request routing."""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from openai import APITimeoutError, AsyncOpenAI

from backend.llm.fallback import (
    RETRYABLE_EXCEPTIONS,
    track_escalation,
    track_fallback,
    track_llm_call,
    track_llm_timeout,
    track_usage,
)
from backend.llm.providers import (
    AgentConfig,
    RouterConfig,
    RoutingConfig,
    ThinkingConfig,
    create_openai_client,
    load_router_config,
)

if TYPE_CHECKING:
    import redis.asyncio
    from openai.types import CompletionUsage
    from openai.types.chat import ChatCompletion


# ---------------------------------------------------------------------------
# J-002 — QUANTMIND_LLM_STUB hook
# ---------------------------------------------------------------------------


QUANTMIND_LLM_STUB_ENV = "QUANTMIND_LLM_STUB"
_STUB_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def is_llm_stub_enabled() -> bool:
    """``True`` iff ``QUANTMIND_LLM_STUB`` is set to a truthy token.

    Enables the J-002 cold-start smoke test + J-005 N-day simulator
    harness to drive the system without burning real LLM budget. Every
    :meth:`LLMRouter.complete` invocation short-circuits to a canned
    :class:`StubChatCompletion` with zero token usage and no provider
    call. Production must leave the env var unset (the smoke-check
    helper exposes the flag so operators can verify in cold-start
    output).
    """
    return os.environ.get(QUANTMIND_LLM_STUB_ENV, "").strip().lower() in _STUB_TRUTHY


@dataclass(frozen=True)
class _StubMessage:
    role: str = "assistant"
    content: str = ""


@dataclass(frozen=True)
class _StubChoice:
    index: int = 0
    finish_reason: str = "stop"
    message: _StubMessage = field(default_factory=_StubMessage)


@dataclass(frozen=True)
class _StubUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class StubChatCompletion:
    """Canned chat completion returned when ``QUANTMIND_LLM_STUB=1``.

    Mirrors the :class:`openai.types.chat.ChatCompletion` surface that
    QuantMind agents actually touch (``choices[0].message.content`` +
    ``usage.total_tokens``) without importing the SDK type, so the
    stub is hermetic across SDK upgrades.

    The ``quantmind_stub`` marker is the canonical way the J-002 smoke
    test + J-005 simulator verify that no real provider was called.
    """

    id: str = "stub-completion-id"
    object: str = "chat.completion"
    created: int = 0
    model: str = "quantmind-stub"
    choices: tuple[_StubChoice, ...] = field(
        default_factory=lambda: (_StubChoice(),)
    )
    usage: _StubUsage = field(default_factory=_StubUsage)
    quantmind_stub: bool = True


# Maximum UTF-8-encoded byte length of a triage response to attempt
# JSON-parsing. Anything larger is treated as a malformed contract and
# conservatively escalates. Bounded so adversarial / runaway LLM output
# cannot DoS the parser (R5 MEDIUM, R6 LOW: the original name suggested
# bytes but compared char count — multibyte content could exceed budget).
_MAX_TRIAGE_JSON_BYTES: int = 65_536


def _extract_reasoning_tokens(usage: CompletionUsage) -> int:
    """Best-effort lift of Kimi reasoning_tokens from usage details.

    The Moonshot SDK exposes reasoning consumption via
    ``completion_tokens_details.reasoning_tokens``. When the field is
    absent (non-Kimi provider, thinking disabled, or older response
    schema) this returns 0 — used purely for observability so it must
    never raise.
    """
    details = getattr(usage, "completion_tokens_details", None)
    if details is None:
        return 0
    reasoning = getattr(details, "reasoning_tokens", None)
    if isinstance(reasoning, int) and reasoning >= 0:
        return reasoning
    return 0


class LLMRouter:
    """Routes agent LLM requests to the appropriate provider.

    Manages AsyncOpenAI client instances (config loaded once at boot;
    hot-reload disabled per P0-7/P0-10/P1-7 — restart to pick up changes),
    and automatic fallback on provider failure.

    Usage::

        router = LLMRouter(config_path="config/agent_models.yaml")
        await router.initialize(redis_client=redis_pool)
        response = await router.complete("news_crawler", messages=[...])
        await router.close()
    """

    def __init__(self, config_path: str | Path) -> None:
        """Initialize the router with the path to agent_models.yaml.

        Does NOT load config or create clients — call initialize() first.
        """
        self._config_path = Path(config_path)
        self._config: RouterConfig | None = None
        self._config_mtime: float = 0.0
        self._clients: dict[str, AsyncOpenAI] = {}
        self._redis: redis.asyncio.Redis | None = None
        self._lock = asyncio.Lock()
        self._log = structlog.get_logger(component="llm_router")

    async def initialize(
        self,
        redis_client: redis.asyncio.Redis | None = None,
    ) -> None:
        """Load config, create clients, store Redis reference.

        Must be called before complete(). Typically called in
        FastAPI lifespan.
        """
        self._redis = redis_client
        await self._reload_config()

    async def close(self) -> None:
        """Close all AsyncOpenAI clients. Call in FastAPI shutdown."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    @property
    def config(self) -> RouterConfig:
        """Return the current (immutable) router configuration."""
        if self._config is None:
            raise RuntimeError(
                "Router not initialized. Call initialize() first."
            )
        return self._config

    def preflight(self) -> dict[str, bool]:
        """Snapshot which providers currently hold a resolvable API key.

        Inspects the config's ``api_key`` entry for each provider. A
        literal key is always present; a ``${ENV}`` reference is present
        only when the environment variable is non-empty at call time.

        Returns a mapping ``{provider_name: True/False}``. Does not make
        any network calls — callers use this for a fast 503 cascade
        decision before booting the pipeline.
        """
        import re

        env_pattern = re.compile(r"^\$\{(\w+)\}$")
        if self._config is None:
            return {}
        status: dict[str, bool] = {}
        for name, provider_cfg in self._config.providers.items():
            raw = provider_cfg.api_key
            m = env_pattern.match(raw)
            if m is None:
                # Literal key in config — assume valid.
                status[name] = bool(raw)
            else:
                status[name] = bool(os.environ.get(m.group(1)))
        return status

    async def complete(
        self,
        agent_name: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> ChatCompletion:
        """Route a chat completion request for the given agent.

        1. Resolve agent -> provider -> client
        2. Call chat.completions.create
        3. On retryable failure, try fallback provider
        4. Track token usage in Redis

        Config is loaded once during ``initialize()``; hot-reload is
        disabled per P0-7 / P0-10 / P1-7. Restart the process to pick
        up ``config/agent_models.yaml`` changes.

        Args:
            agent_name: Key from agents section of YAML.
            messages: OpenAI-format message list.
            **kwargs: Override temperature, max_tokens, etc.

        Returns:
            ChatCompletion response from the provider.

        Raises:
            KeyError: If agent_name is not in config.
            openai.APIError: If both primary and fallback fail.
        """
        # J-002 — QUANTMIND_LLM_STUB=1 short-circuits every call so the
        # cold-start smoke test + N-day simulator harness can drive the
        # backend without burning real LLM budget. The stub returns a
        # canned :class:`StubChatCompletion` with zero token usage and
        # never invokes ``track_usage`` so cost_guard counters stay at 0.
        if is_llm_stub_enabled():
            self._log.info(
                "llm_stub_returned",
                agent_name=agent_name,
                env_var=QUANTMIND_LLM_STUB_ENV,
            )
            return StubChatCompletion()  # type: ignore[return-value]

        config = self.config

        if agent_name not in config.agents:
            raise KeyError(
                f"Unknown agent '{agent_name}'. "
                f"Available: {sorted(config.agents.keys())}"
            )

        agent_cfg = config.agents[agent_name]

        call_kwargs: dict[str, Any] = {
            "temperature": kwargs.pop("temperature", config.defaults.temperature),
            "max_tokens": kwargs.pop("max_tokens", config.defaults.max_tokens),
            **kwargs,
        }

        # Try primary (or routing.triage) provider
        primary_provider, primary_model = self._select_primary(agent_cfg)
        is_tiered = agent_cfg.routing is not None
        primary_stage = "triage" if is_tiered else "primary"
        # Suffix the cost-tracking name for tiered agents so daily reports
        # can split triage vs escalation spend per agent (P5B-T03 trace
        # requirement). Non-tiered agents keep a flat name unchanged.
        primary_track_name = (
            f"{agent_name}/triage" if is_tiered else agent_name
        )
        try:
            response = await self._call_provider(
                provider_name=primary_provider,
                model=primary_model,
                messages=messages,
                agent_name=primary_track_name,
                thinking=agent_cfg.thinking,
                route_stage=primary_stage,
                **call_kwargs,
            )
        except RETRYABLE_EXCEPTIONS as exc:
            self._log.warning(
                "primary_provider_failed",
                agent_name=agent_name,
                provider=primary_provider,
                model=primary_model,
                error=str(exc),
            )

            if agent_cfg.fallback is None:
                raise

            await track_fallback(
                self._redis,
                agent_name,
                primary_provider,
                agent_cfg.fallback.provider,
            )

            self._log.info(
                "trying_fallback_provider",
                agent_name=agent_name,
                fallback_provider=agent_cfg.fallback.provider,
                fallback_model=agent_cfg.fallback.model,
            )

            return await self._call_provider(
                provider_name=agent_cfg.fallback.provider,
                model=agent_cfg.fallback.model,
                messages=messages,
                agent_name=agent_name,
                thinking=agent_cfg.thinking,
                route_stage="fallback",
                **call_kwargs,
            )

        if is_tiered:
            should_esc, reason = self._should_escalate(
                agent_cfg.routing, response
            )
            esc_provider = agent_cfg.routing.escalation_provider  # type: ignore[union-attr]
            esc_model = agent_cfg.routing.escalation_model  # type: ignore[union-attr]
            # H-003 — when the daily soft ceiling has been breached the
            # SoftDegradeManager raises a Redis flag that vetoes Kimi
            # escalation specifically (DeepSeek + Qwen primary still
            # serve every request). Skip the escalation branch when the
            # flag is up so the daily ¥20 hard cap stays the *only*
            # full-LLM circuit breaker (CLAUDE.md §2.10).
            if (
                should_esc
                and esc_provider is not None
                and esc_model is not None
                and esc_provider == "kimi"
                and await self._is_kimi_escalation_blocked()
            ):
                self._log.warning(
                    "kimi_escalation_blocked_by_soft_degrade",
                    agent_name=agent_name,
                )
                return response
            # H-003 — enforce the Kimi ¥4 daily hard cap before
            # escalation. Only stops Kimi escalations; DeepSeek + Qwen
            # primary calls stay alive (P1-7 §1.4). Fail-open: a Redis
            # hiccup must not crash the escalation flow.
            if (
                should_esc
                and esc_provider == "kimi"
                and esc_model is not None
                and await self._kimi_daily_cap_breached()
            ):
                self._log.warning(
                    "kimi_escalation_blocked_by_daily_cap",
                    agent_name=agent_name,
                )
                return response
            if should_esc and esc_provider is not None and esc_model is not None:
                await track_escalation(
                    self._redis,
                    agent_name,
                    primary_provider,
                    esc_provider,
                    reason,
                )
                threshold = (
                    agent_cfg.routing.escalation_condition.confidence_lt  # type: ignore[union-attr]
                    if agent_cfg.routing.escalation_condition  # type: ignore[union-attr]
                    else None
                )
                self._log.info(
                    "escalating_to_expensive_provider",
                    agent_name=agent_name,
                    triage_provider=primary_provider,
                    triage_model=primary_model,
                    escalation_provider=esc_provider,
                    escalation_model=esc_model,
                    reason=reason,
                    confidence_threshold=threshold,
                )
                return await self._call_provider(
                    provider_name=esc_provider,
                    model=esc_model,
                    messages=messages,
                    agent_name=f"{agent_name}/escalation",
                    thinking=agent_cfg.thinking,
                    route_stage="escalation",
                    **call_kwargs,
                )

        return response

    async def _is_kimi_escalation_blocked(self) -> bool:
        """H-003 — peek the SoftDegradeManager Kimi block flag.

        Fail-open: a Redis hiccup must not stop the primary→escalation
        flow. The block is the secondary defense; the daily ¥20 hard
        cap (cost_guard.assert_budget_allows) is the dependable layer.
        Import is lazy so a future tightening of the LLM-layer import
        graph (CLAUDE.md §2.10 forbids cost_guard importing backend.llm
        — the reverse is fine) stays trivial to reason about.
        """
        if self._redis is None:
            return False
        try:
            from backend.services.soft_degrade_manager import SoftDegradeManager

            mgr = SoftDegradeManager(self._redis)
            return await mgr.is_kimi_escalation_blocked()
        except Exception as exc:  # noqa: BLE001 — operator visibility
            self._log.warning(
                "kimi_escalation_block_probe_failed", error=str(exc)
            )
            return False

    async def _kimi_daily_cap_breached(self) -> bool:
        """H-003 — peek the Kimi ¥4 daily cap (P1-7 §1.4).

        Fail-open: a Redis hiccup returns False so the escalation flow
        keeps running on the standard provider path. The daily ¥20
        hard cap (assert_budget_allows) is the dependable LLM-wide
        circuit breaker; this only stops the Kimi escalation rung.
        """
        if self._redis is None:
            return False
        try:
            from backend.services.cost_guard import get_kimi_budget_state

            state = await get_kimi_budget_state(self._redis)
            return state.status == "hard_breach"
        except Exception as exc:  # noqa: BLE001 — operator visibility
            self._log.warning(
                "kimi_daily_cap_probe_failed", error=str(exc)
            )
            return False

    async def _call_provider(
        self,
        provider_name: str,
        model: str,
        messages: list[dict[str, str]],
        agent_name: str,
        thinking: ThinkingConfig,
        route_stage: str = "primary",
        **kwargs: Any,
    ) -> ChatCompletion:
        """Execute a chat completion call against a specific provider."""
        client = self._get_client(provider_name)

        self._log.debug(
            "llm_call_start",
            agent_name=agent_name,
            provider=provider_name,
            model=model,
            route_stage=route_stage,
            thinking_type=thinking.type,
        )

        call_kwargs = self._normalize_provider_kwargs(
            provider_name=provider_name,
            model=model,
            base_kwargs=kwargs,
            thinking=thinking,
        )

        # cond10a live timeout-rate telemetry (P0-6-amendment-2026-05-29):
        # count every provider call attempt; count + re-raise on timeout.
        # Best-effort — counting never alters the 30s / 0-retry contract.
        await track_llm_call(self._redis)
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                **call_kwargs,
            )
        except APITimeoutError:
            await track_llm_timeout(self._redis)
            raise

        if response.usage:
            await track_usage(
                self._redis,
                agent_name,
                provider_name,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
                model=model,
            )
            self._log.info(
                "llm_call_complete",
                agent_name=agent_name,
                provider=provider_name,
                model=model,
                route_stage=route_stage,
                thinking_type=thinking.type,
                thinking_max_tokens=thinking.max_tokens,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                reasoning_tokens=_extract_reasoning_tokens(response.usage),
            )

        return response

    @staticmethod
    def _normalize_provider_kwargs(
        provider_name: str,
        model: str,
        base_kwargs: dict[str, Any],
        thinking: ThinkingConfig,
    ) -> dict[str, Any]:
        """Apply Kimi K2.x thinking-mode + temperature constraints.

        Kimi exposes thinking via the OpenAI-compatible ``extra_body``
        envelope (it is not part of the upstream Chat Completions
        schema). Reasoning tokens count against the request's total
        ``max_tokens`` budget, so when thinking is enabled the request
        budget is grown by the configured reasoning cap to keep room
        for the actual completion. Temperature is pinned per Moonshot
        spec: 1.0 in thinking mode, 0.6 in non-thinking mode.

        Non-Kimi providers receive the kwargs unchanged — thinking is
        silently dropped.
        """
        normalized = dict(base_kwargs)

        if not (provider_name == "kimi" and model.startswith("kimi-k2")):
            return normalized

        existing_extra = normalized.get("extra_body")
        extra_body: dict[str, Any] = (
            dict(existing_extra) if isinstance(existing_extra, dict) else {}
        )

        if thinking.type == "enabled":
            extra_body["thinking"] = {
                "type": "enabled",
                "max_tokens": thinking.max_tokens,
            }
            normalized["temperature"] = 1
            caller_max = normalized.get("max_tokens")
            if isinstance(caller_max, int):
                normalized["max_tokens"] = caller_max + thinking.max_tokens
        else:
            extra_body["thinking"] = {"type": "disabled"}
            # Kimi rejects arbitrary values when thinking is off; pin to
            # the documented non-thinking constant.
            normalized["temperature"] = 0.6

        normalized["extra_body"] = extra_body
        return normalized

    @staticmethod
    def _select_primary(agent_cfg: AgentConfig) -> tuple[str, str]:
        """Resolve the first-call (provider, model) for an agent.

        With routing.triage_* set, the cheap triage path is the primary;
        otherwise fall back to the agent's own provider/model. P5B-T01
        wires the plumbing — full escalation lives in
        :meth:`_should_escalate` (P5B-T03).
        """
        if agent_cfg.routing is not None:
            return (
                agent_cfg.routing.triage_provider,
                agent_cfg.routing.triage_model,
            )
        return (agent_cfg.provider, agent_cfg.model)

    @staticmethod
    def _should_escalate(
        routing: RoutingConfig | None,
        response: ChatCompletion,
    ) -> tuple[bool, str]:
        """Decide whether the cheap triage answer must be escalated.

        Returns ``(escalate, reason)``. Reason is one of:

        - ``no_routing``      tiered routing not configured for the agent
        - ``no_condition``    routing has no escalation_condition rule
        - ``parse_failed``    triage response was not parseable JSON,
                              had a missing/non-finite/out-of-range
                              ``confidence`` field, or was structurally
                              broken (no choices, no message, etc.).
                              Conservatively escalates so the request
                              never silently degrades to junk output
                              (spec §P5B-T03 fail-open).
        - ``low_confidence``  parsed ``confidence`` field below threshold
        - ``ok``              triage answer is trustworthy, return as-is

        Out-of-range confidence (``< 0`` or ``> 1``), ``NaN``, ``Infinity``
        and ``bool`` are all treated as ``parse_failed``. Python's
        ``json.loads`` accepts NaN/Infinity by default, so we cannot rely
        on parse rejection alone — we explicitly check finiteness and
        bounds.
        """
        if routing is None:
            return False, "no_routing"
        cond = routing.escalation_condition
        if cond is None:
            return False, "no_condition"

        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError):
            return True, "parse_failed"
        if not isinstance(content, str) or not content:
            return True, "parse_failed"
        # Cap parser cost — adversarial / runaway LLM output should not
        # be allowed to spend unbounded CPU/memory on json.loads. The
        # contract is a small JSON envelope; 65 KB is generous for the
        # `confidence`/`action`/`reasoning` shape we expect. We measure
        # the UTF-8-encoded byte length so multibyte (e.g. Chinese)
        # content cannot smuggle past a char-count budget.
        if len(content.encode("utf-8")) > _MAX_TRIAGE_JSON_BYTES:
            return True, "parse_failed"
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, ValueError, RecursionError):
            return True, "parse_failed"
        if not isinstance(parsed, dict):
            return True, "parse_failed"

        if cond.confidence_lt is not None:
            conf = parsed.get("confidence")
            # Python ``bool`` is a subclass of ``int``; reject explicitly
            # so ``True`` / ``False`` don't bypass the numeric gate.
            if isinstance(conf, bool):
                return True, "parse_failed"
            if not isinstance(conf, (int, float)):
                return True, "parse_failed"
            conf_f = float(conf)
            if not math.isfinite(conf_f):
                return True, "parse_failed"
            if conf_f < 0.0 or conf_f > 1.0:
                return True, "parse_failed"
            if conf_f < cond.confidence_lt:
                return True, "low_confidence"
        return False, "ok"

    def _get_client(self, provider_name: str) -> AsyncOpenAI:
        """Get a pre-initialized client for the given provider.

        Clients are eagerly created during config reload, so this is
        a pure read with no race condition.
        """
        if provider_name not in self._clients:
            raise KeyError(
                f"Unknown provider '{provider_name}'. "
                f"Available: {sorted(self._clients.keys())}"
            )
        return self._clients[provider_name]

    async def _reload_config(self) -> None:
        """Load configuration from YAML file (one-shot at initialize).

        Hot-reload is disabled per P0-7 / P0-10 / P1-7: this method only
        runs once, during ``initialize()``. Restart the process to pick
        up ``config/agent_models.yaml`` changes — there is no runtime
        path that re-invokes it.

        Uses ``asyncio.to_thread`` for blocking file I/O to avoid
        stalling the event loop. Eagerly creates clients for all
        providers so the read path (``_get_client``) is pure lookup.
        """
        new_config = await asyncio.to_thread(
            load_router_config, self._config_path
        )

        for name, provider_cfg in new_config.providers.items():
            if name not in self._clients:
                self._clients[name] = create_openai_client(provider_cfg)

        self._config = new_config
        self._config_mtime = self._config_path.stat().st_mtime

        self._log.info(
            "config_loaded",
            providers=sorted(new_config.providers.keys()),
            agents=sorted(new_config.agents.keys()),
        )
