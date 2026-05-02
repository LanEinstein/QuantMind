"""LLM provider configuration models and client factory."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
        raise ValueError(
            f"Environment variable {var_name} is not set or empty"
        )
    return resolved


# -- Frozen Pydantic config models --


class FallbackConfig(BaseModel):
    """Fallback provider specification for an agent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)


class RoutingConfig(BaseModel):
    """Tiered triage→escalation routing for an agent.

    Triage runs the cheap provider first; if escalation_condition fires
    (confidence below threshold, contradiction with another agent, …)
    the router re-runs against the expensive provider. The actual
    escalation decision lives in LLMRouter._should_escalate (P5B-T03);
    P5B-T01 only lands the schema. ``escalation_condition`` is loosely
    typed today and will be tightened to a dedicated model in T03.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    triage_provider: str = Field(min_length=1)
    triage_model: str = Field(min_length=1)
    escalation_provider: str | None = Field(default=None, min_length=1)
    escalation_model: str | None = Field(default=None, min_length=1)
    escalation_condition: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_escalation_pair(self) -> RoutingConfig:
        has_provider = self.escalation_provider is not None
        has_model = self.escalation_model is not None
        if has_provider != has_model:
            raise ValueError(
                "escalation_provider and escalation_model must be set "
                "together (or both omitted)"
            )
        if self.escalation_condition and not has_provider:
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
                    "thinking.type='disabled' requires max_tokens=0 and "
                    "keep='none'"
                )
        elif self.max_tokens == 0:
            raise ValueError(
                "thinking.type='enabled' requires max_tokens > 0"
            )
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
    the client.
    """
    api_key = resolve_env_var(provider_config.api_key)
    return AsyncOpenAI(
        base_url=provider_config.base_url,
        api_key=api_key,
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
