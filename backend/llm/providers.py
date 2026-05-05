"""LLM provider configuration models and client factory."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Concurrent agent fan-out (5 analysts + 2 researchers) shares the
# AsyncOpenAI client per provider. Without explicit bounds the openai
# SDK uses 600s default timeout, so a single stuck TCP handshake can
# stall the whole 900s pipeline. Cap connect/read/write/pool so a
# flaky connection fails fast and the router's fallback chain takes
# over before the analysis-scheduler timeout fires.
_CONNECT_TIMEOUT_SEC = 10.0
_READ_TIMEOUT_SEC = 120.0
_WRITE_TIMEOUT_SEC = 30.0
_POOL_TIMEOUT_SEC = 10.0
# Default openai SDK retries=2 stack with read_timeout to burn 360s+
# on a single hanging upstream. With router-level fallback already in
# place, one SDK retry is plenty: failed primary call hits fallback
# within ~240s instead of ~480s, leaving 600s+ for the rest of the
# 900s pipeline budget.
_MAX_RETRIES = 1

_ENV_PATTERN = re.compile(r"^\$\{(\w+)\}$")


def resolve_env_var(value: str) -> str:
    """Resolve '${ENV_VAR}' syntax to actual environment variable value.

    Plain strings are returned unchanged. Raises ValueError if the
    referenced environment variable is not set or is empty.
    """
    match = _ENV_PATTERN.match(value)
    if not match:
        return value
    var_name = match.group(1)
    resolved = os.environ.get(var_name)
    if not resolved:
        raise ValueError(f"Environment variable {var_name} is not set or empty")
    return resolved


# -- Frozen Pydantic config models --


class FallbackConfig(BaseModel):
    """Fallback provider specification for an agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class EscalationCondition(BaseModel):
    """Triggers that promote a triage answer to the escalation provider.

    Today only ``confidence_lt`` (numeric threshold against the parsed
    JSON ``confidence`` field) is implemented; the model is the typed
    schema deferred from P5B-T01. ``extra='forbid'`` keeps a typo from
    silently disabling escalation, and the post-init validator forces at
    least one rule to be set so an empty mapping never reaches the
    router with the appearance of "configured but inert".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    confidence_lt: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _at_least_one_rule(self) -> EscalationCondition:
        if self.confidence_lt is None:
            raise ValueError(
                "escalation_condition must define at least one rule "
                "(currently supported: confidence_lt)"
            )
        return self


class RoutingConfig(BaseModel):
    """Tiered triage→escalation routing for an agent.

    Triage runs the cheap provider first; if escalation_condition fires
    (confidence below threshold, parse failure, …) the router re-runs
    against the expensive provider. The actual escalation decision lives
    in :meth:`LLMRouter._should_escalate` (P5B-T03).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    triage_provider: str = Field(min_length=1)
    triage_model: str = Field(min_length=1)
    escalation_provider: str | None = Field(default=None, min_length=1)
    escalation_model: str | None = Field(default=None, min_length=1)
    escalation_condition: EscalationCondition | None = Field(default=None)

    @model_validator(mode="after")
    def _check_escalation_pair(self) -> RoutingConfig:
        has_provider = self.escalation_provider is not None
        has_model = self.escalation_model is not None
        if has_provider != has_model:
            raise ValueError(
                "escalation_provider and escalation_model must be set "
                "together (or both omitted)"
            )
        if self.escalation_condition is not None and not has_provider:
            raise ValueError(
                "escalation_condition requires both escalation_provider "
                "and escalation_model"
            )
        return self


class ThinkingConfig(BaseModel):
    """Per-agent Kimi K2.6 thinking-mode configuration.

    keep="all" keeps every round's reasoning_content in context (needed
    for multi-round bull/bear debate); "last_round" only keeps the most
    recent for terminal judgement; "none" pairs with type=disabled to
    drop reasoning entirely for cheap summary agents. Bounds match the
    Moonshot K2.6 reasoning cap (32k upper, 0 lower).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["enabled", "disabled"] = "enabled"
    max_tokens: int = Field(default=8000, ge=0, le=32_000)
    keep: Literal["all", "last_round", "none"] = "all"

    @model_validator(mode="after")
    def _check_disabled_invariant(self) -> ThinkingConfig:
        if self.type == "disabled":
            if self.max_tokens != 0 or self.keep != "none":
                raise ValueError(
                    "thinking.type='disabled' requires max_tokens=0 and keep='none'"
                )
        elif self.max_tokens == 0:
            raise ValueError("thinking.type='enabled' requires max_tokens > 0")
        return self


class AgentConfig(BaseModel):
    """Per-agent LLM routing configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    fallback: FallbackConfig | None = None
    routing: RoutingConfig | None = None
    thinking: ThinkingConfig = Field(default_factory=ThinkingConfig)
    frequency: str = ""
    task: str = ""


class ProviderConfig(BaseModel):
    """LLM provider connection configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    default_model: str = Field(min_length=1)


class DefaultsConfig(BaseModel):
    """Default parameters for LLM calls."""

    model_config = ConfigDict(frozen=True)

    temperature: float = 0.3
    max_tokens: int = 4096


class RouterConfig(BaseModel):
    """Complete YAML configuration schema for the LLM router."""

    model_config = ConfigDict(frozen=True)

    providers: dict[str, ProviderConfig]
    agents: dict[str, AgentConfig]
    defaults: DefaultsConfig = DefaultsConfig()

    @model_validator(mode="after")
    def _check_provider_references(self) -> RouterConfig:
        """Fail fast on agent.provider / fallback / routing typos.

        Without this, a bad provider name only surfaces at runtime as
        ``Unknown provider`` from inside the request hot path, with no
        agent context. Catching it here gives the operator a single
        line pointing at the offending YAML key.
        """
        known = set(self.providers)
        for agent_name, agent in self.agents.items():
            if agent.provider not in known:
                raise ValueError(
                    f"agents.{agent_name}.provider='{agent.provider}' "
                    f"not in providers={sorted(known)}"
                )
            if agent.fallback is not None and agent.fallback.provider not in known:
                raise ValueError(
                    f"agents.{agent_name}.fallback.provider="
                    f"'{agent.fallback.provider}' not in providers="
                    f"{sorted(known)}"
                )
            if agent.routing is not None:
                if agent.routing.triage_provider not in known:
                    raise ValueError(
                        f"agents.{agent_name}.routing.triage_provider="
                        f"'{agent.routing.triage_provider}' not in "
                        f"providers={sorted(known)}"
                    )
                esc = agent.routing.escalation_provider
                if esc is not None and esc not in known:
                    raise ValueError(
                        f"agents.{agent_name}.routing.escalation_provider="
                        f"'{esc}' not in providers={sorted(known)}"
                    )
        return self


# -- Client factory --


def create_openai_client(provider_config: ProviderConfig) -> AsyncOpenAI:
    """Create an AsyncOpenAI client from a provider configuration.

    Resolves ${ENV_VAR} syntax in the api_key field before creating
    the client. Applies bounded connect/read/write/pool timeouts and
    enables SDK-level retries so flaky upstream connections fail fast
    and trigger router-level fallback before the pipeline timeout.
    """
    api_key = resolve_env_var(provider_config.api_key)
    # Force IPv4-only egress: hosts like dashscope.aliyuncs.com publish
    # AAAA records but operators in IPv4-only networks see Happy Eyeballs
    # races stall on dead IPv6 paths until the connect timeout fires for
    # every parallel agent call. local_address="0.0.0.0" pins httpx to
    # IPv4 sockets so AAAA addresses are skipped at connect time.
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=_CONNECT_TIMEOUT_SEC,
            read=_READ_TIMEOUT_SEC,
            write=_WRITE_TIMEOUT_SEC,
            pool=_POOL_TIMEOUT_SEC,
        ),
        limits=httpx.Limits(
            max_connections=64,
            max_keepalive_connections=16,
            keepalive_expiry=30.0,
        ),
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
    )
    return AsyncOpenAI(
        base_url=provider_config.base_url,
        api_key=api_key,
        timeout=_READ_TIMEOUT_SEC,
        max_retries=_MAX_RETRIES,
        http_client=http_client,
    )


# -- YAML loading --


def load_router_config(yaml_path: str | Path) -> RouterConfig:
    """Load and validate router configuration from a YAML file.

    Returns an immutable RouterConfig instance.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        pydantic.ValidationError: If the schema is invalid.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return RouterConfig.model_validate(raw)
