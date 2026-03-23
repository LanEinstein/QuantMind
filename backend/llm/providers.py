"""LLM provider configuration models and client factory."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

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

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str


class AgentConfig(BaseModel):
    """Per-agent LLM routing configuration."""

    model_config = ConfigDict(frozen=True)

    name: str
    provider: str
    model: str
    fallback: FallbackConfig | None = None
    frequency: str = ""
    task: str = ""


class ProviderConfig(BaseModel):
    """LLM provider connection configuration."""

    model_config = ConfigDict(frozen=True)

    base_url: str
    api_key: str
    default_model: str


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
