"""Prompt registry — file-based prompt version control (P2-2 §1.4).

Single source of truth for prompt versions across the four mandatory
LLM agents: ``fundamental_analyst``, ``technical_analyst``,
``risk_officer``, ``fund_manager`` (P0-10 §1.1).

This module ships in two stages:

* **X-001 (this file)** — schema-only. Pydantic v2 frozen + strict +
  ``extra='forbid'`` models defining the ``prompts.lock.json`` contract.
  No filesystem IO, no checksum verification, no alias resolution.
* **X-003** — extends with ``load_pinned_version``, alias resolver
  (production / staging / challenger), startup ``fail-fast`` on
  missing files or checksum mismatch.

Architectural invariants (continued from P0-7 §2 red line 14 +
P2-2 §2 red line 17):

* Zero runtime mutate. ``prompts.lock.json`` is read once at boot;
  promotions go through git + amendment + restart.
* Zero hot-reload. The registry is constructed once via
  ``PromptRegistry.from_lockfile(...)`` and never patched in place.
* Zero ``backend.{api, broker, risk, llm, agents, mirofish, data}``
  imports — Phase X module isolation. Enforced by ruff + AST scan
  + ``scripts/redline-check.sh`` (X-018 three-layer gate).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

MANDATORY_AGENTS: frozenset[str] = frozenset(
    {
        "fundamental_analyst",
        "technical_analyst",
        "risk_officer",
        "fund_manager",
    }
)
"""Four LLM agents that are required in production — P0-10 §1.1.

The registry schema allows the ``agents`` map to be empty at the X-001
bootstrap stage (no prompts pinned yet). X-003 enforces, at load time,
that every mandatory agent has a ``production`` alias pointing at a
valid version once the registry is hydrated for runtime use.
"""

PROMPT_ALIAS_NAMES: frozenset[str] = frozenset({"production", "staging", "challenger"})
"""Three named aliases per agent. ``production`` is the live version.
``staging`` and ``challenger`` are evaluation slots used by the X-007
shadow chain and the X-013 amendment drafter."""

VERSION_TAG_RE = re.compile(r"^v\d+(?:\.\d+)?$")
"""Version tag format — ``v1``, ``v1.0``, ``v23.456``. Numeric only;
no semver pre-release suffixes (keeps git diff and ``prompts.lock.json``
trivially diff-able)."""

SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")
"""Lowercase hex SHA256 — 64 chars. Matches Python ``hashlib.sha256().hexdigest()``."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PromptRegistryError(Exception):
    """Base class for prompt registry failures.

    Concrete subclasses live in X-003. Defining the base here keeps the
    schema module importable without dragging in IO concerns.
    """


# ---------------------------------------------------------------------------
# Schema — prompts.lock.json
# ---------------------------------------------------------------------------


class PromptVersionEntry(BaseModel):
    """One pinned prompt version on disk.

    A ``version`` tag (``v1``, ``v1.0``, ...) maps to a YAML file under
    ``config/prompts/{agent}/{version}.yaml`` with a SHA256 checksum
    recorded at pin time. The checksum is verified on every load —
    bit-rot, accidental edits, or a non-fast-forward git operation
    fail-close the boot path.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    pinned_at: datetime
    pinned_by: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _check_path_and_hash(self) -> PromptVersionEntry:
        if not self.path.startswith("config/prompts/"):
            raise ValueError(
                f"prompt version path must live under config/prompts/, "
                f"got {self.path!r}"
            )
        if not self.path.endswith(".yaml"):
            raise ValueError(
                f"prompt version path must end with .yaml, got {self.path!r}"
            )
        if not SHA256_HEX_RE.fullmatch(self.sha256):
            raise ValueError(
                f"sha256 must be 64-char lowercase hex, got {self.sha256!r}"
            )
        return self


class PromptAgentLock(BaseModel):
    """All pinned versions and aliases for one agent.

    ``aliases`` resolves a logical name (``production``) to a concrete
    version tag (``v3``); ``versions`` maps the version tag to its
    on-disk artifact. Aliases are optional at the X-001 bootstrap
    stage so a brand-new repo can ship before any prompts are pinned.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    aliases: dict[Literal["production", "staging", "challenger"], str] = Field(
        default_factory=dict
    )
    versions: dict[str, PromptVersionEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_alias_targets(self) -> PromptAgentLock:
        for alias, target in self.aliases.items():
            if not VERSION_TAG_RE.fullmatch(target):
                raise ValueError(
                    f"alias {alias!r} target must match {VERSION_TAG_RE.pattern!r}, "
                    f"got {target!r}"
                )
            if target not in self.versions:
                raise ValueError(
                    f"alias {alias!r} -> {target!r} but version {target!r} is not "
                    f"present in versions; aliases may only point at known versions"
                )
        for version_tag in self.versions:
            if not VERSION_TAG_RE.fullmatch(version_tag):
                raise ValueError(
                    f"version tag must match {VERSION_TAG_RE.pattern!r}, "
                    f"got {version_tag!r}"
                )
        return self


class PromptLockFile(BaseModel):
    """Root of ``config/prompts.lock.json``.

    The schema deliberately tolerates an empty ``agents`` map so the
    repo can be initialized before any prompt has been pinned. X-003
    layers on the stricter runtime guarantee that every mandatory
    agent must have a ``production`` alias before the registry can be
    hydrated for live decisions.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    version: Literal["1.0"]
    updated_at: datetime
    agents: dict[str, PromptAgentLock] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_agent_keys(self) -> PromptLockFile:
        for agent_name in self.agents:
            if agent_name not in MANDATORY_AGENTS:
                raise ValueError(
                    f"unknown agent {agent_name!r}; only the four mandatory "
                    f"agents are permitted: {sorted(MANDATORY_AGENTS)}"
                )
        return self

    def has_full_production_coverage(self) -> bool:
        """True if every mandatory agent has a ``production`` alias pinned.

        Called by X-003 ``PromptRegistry.from_lockfile`` to decide
        whether the registry is hot enough to serve runtime requests.
        At X-001 bootstrap this is false because ``agents`` is empty.
        """
        for agent in MANDATORY_AGENTS:
            agent_lock = self.agents.get(agent)
            if agent_lock is None or "production" not in agent_lock.aliases:
                return False
        return True


__all__ = [
    "MANDATORY_AGENTS",
    "PROMPT_ALIAS_NAMES",
    "PromptAgentLock",
    "PromptLockFile",
    "PromptRegistryError",
    "PromptVersionEntry",
    "SHA256_HEX_RE",
    "VERSION_TAG_RE",
]
