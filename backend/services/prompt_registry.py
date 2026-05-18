"""Prompt registry — file-based prompt version control (P2-2 §1.4).

Single source of truth for prompt versions across the four mandatory
LLM agents: ``fundamental_analyst``, ``technical_analyst``,
``risk_officer``, ``fund_manager`` (P0-10 §1.1).

Layout on disk:

* ``config/prompts.lock.json`` — :class:`PromptLockFile` instance
  pinning every live version's path + SHA256 + pinner.
* ``config/prompts/{agent}/{version}.yaml`` — the actual prompt
  template payload (UTF-8 YAML; opaque bytes to this module).

The registry is constructed once at boot via
:meth:`PromptRegistry.from_lockfile` and serves as an immutable view
for the lifetime of the process. Promotions go through git +
amendment + restart per P0-7 §2 red line 14 (hot-reload forbidden) +
P2-2 §1.4. The loader fail-closes on any structural problem
(missing lock file, missing prompt YAML, checksum mismatch, alias
pointing at an unknown version, mandatory agent without a production
alias once ``require_full_production_coverage=True``).

Architectural invariants:

* Zero runtime mutate — ``PromptRegistry`` exposes only read accessors;
  ``__setattr__`` is intercepted to raise.
* Zero hot-reload — there is no ``reload()`` method by design.
* Zero ``backend.{api, broker, risk, llm, agents, mirofish, data}``
  imports — Phase X module isolation enforced by the X-018 three-layer
  gate (ruff banned-imports + AST scan + ``scripts/redline-check.sh``).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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

    All subclasses fail-close the boot path so a misconfigured deploy
    cannot silently fall back to a stale prompt. Catching this base
    in production is forbidden — boot failures are the signal.
    """


class PromptLockFileNotFoundError(PromptRegistryError):
    """``config/prompts.lock.json`` is missing on disk."""


class PromptLockFileMalformedError(PromptRegistryError):
    """``prompts.lock.json`` exists but is not valid JSON or fails schema."""


class PromptFileNotFoundError(PromptRegistryError):
    """A pinned ``config/prompts/{agent}/{version}.yaml`` is missing."""


class PromptChecksumMismatchError(PromptRegistryError):
    """A pinned prompt YAML's content SHA256 does not match the lockfile.

    This is the **single most important** invariant of the registry —
    if the prompt has been edited out-of-band, the boot must fail-close
    instead of serving a silently-different prompt. The error message
    includes the expected and observed hashes so the operator can
    quickly identify whether to rebuild the lockfile (after an
    intentional amendment) or restore the prompt from git.
    """


class PromptAgentCoverageError(PromptRegistryError):
    """A mandatory agent does not have a ``production`` alias.

    Only raised when the caller passes
    ``require_full_production_coverage=True`` to
    :meth:`PromptRegistry.from_lockfile`. The X-001 bootstrap state
    (no agents pinned yet) is intentionally permitted.
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


# ---------------------------------------------------------------------------
# Registry — immutable boot-time loader
# ---------------------------------------------------------------------------


def compute_prompt_sha256(content: bytes) -> str:
    """Return the canonical SHA256 hex digest of a prompt payload.

    Centralised so the lockfile builder, the registry verifier, and
    the X-009 DSPy GEPA runner cannot disagree on the hashing scheme.
    SHA256 hex is the format pinned by P2-2 §1.4 + the
    :class:`PromptVersionEntry` regex (lowercase 64-char hex).
    """
    return hashlib.sha256(content).hexdigest()


class PromptRegistry:
    """Immutable boot-time view over ``config/prompts.lock.json``.

    Construction is two-step so the same code path supports both
    production wiring (``from_lockfile``) and tests that need to
    assemble an in-memory registry without touching the filesystem.

    Once constructed, the registry is **fully immutable** — there is
    no ``reload``, ``update``, or ``promote`` method by design. Any
    attempt to set an attribute raises :class:`AttributeError` so a
    well-meaning future contributor cannot accidentally introduce a
    hot-reload path that bypasses the amendment workflow.
    """

    __slots__ = ("_lock", "_contents", "_resolved_aliases")

    def __init__(
        self,
        lock: PromptLockFile,
        contents: Mapping[tuple[str, str], str],
    ) -> None:
        # Defensive copy + map proxy so callers cannot mutate the
        # registry through the underlying dict reference.
        object.__setattr__(self, "_lock", lock)
        object.__setattr__(self, "_contents", MappingProxyType(dict(contents)))
        resolved: dict[tuple[str, str], str] = {}
        for agent_name, agent_lock in lock.agents.items():
            for alias, version_tag in agent_lock.aliases.items():
                resolved[(agent_name, alias)] = version_tag
        object.__setattr__(
            self, "_resolved_aliases", MappingProxyType(resolved)
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"PromptRegistry is immutable; cannot set {name!r}. "
            f"Promotions require amendment + restart (P0-7 §2 red line 14 + "
            f"P2-2 §1.4)."
        )

    # ------------------------------------------------------------------
    # Read accessors
    # ------------------------------------------------------------------

    @property
    def lock(self) -> PromptLockFile:
        return self._lock  # type: ignore[return-value]

    @property
    def agents(self) -> frozenset[str]:
        return frozenset(self._lock.agents.keys())  # type: ignore[attr-defined]

    def resolve_alias(self, agent: str, alias: str) -> str:
        """Return the version tag for ``agent`` / ``alias``.

        Raises :class:`KeyError` if the agent has no such alias — the
        caller is expected to handle this (e.g. challenger alias is
        intentionally absent until X-009 GEPA pins one).
        """
        try:
            return self._resolved_aliases[(agent, alias)]  # type: ignore[index]
        except KeyError as exc:
            raise KeyError(
                f"no {alias!r} alias for agent {agent!r}; configured "
                f"aliases: {self.list_aliases(agent)}"
            ) from exc

    def list_aliases(self, agent: str) -> tuple[str, ...]:
        agent_lock = self._lock.agents.get(agent)  # type: ignore[attr-defined]
        if agent_lock is None:
            return ()
        return tuple(sorted(agent_lock.aliases.keys()))

    def list_versions(self, agent: str) -> tuple[str, ...]:
        agent_lock = self._lock.agents.get(agent)  # type: ignore[attr-defined]
        if agent_lock is None:
            return ()
        return tuple(sorted(agent_lock.versions.keys()))

    def get_prompt(self, agent: str, alias_or_version: str) -> str:
        """Return the prompt YAML content as a UTF-8 string.

        ``alias_or_version`` is first checked against the agent's
        configured aliases (``production`` / ``staging`` /
        ``challenger``); if it matches an alias the resolved version
        tag is used; otherwise the argument is treated as a literal
        version tag. This mirrors how DSPy / LiteLLM clients typically
        reference prompts ("give me production" vs. "give me v3").
        """
        agent_lock = self._lock.agents.get(agent)  # type: ignore[attr-defined]
        if agent_lock is None:
            raise KeyError(f"agent {agent!r} not present in lockfile")
        version_tag = agent_lock.aliases.get(alias_or_version, alias_or_version)
        key = (agent, version_tag)
        try:
            return self._contents[key]  # type: ignore[index]
        except KeyError as exc:
            raise KeyError(
                f"no content registered for ({agent!r}, {version_tag!r}); "
                f"available versions: {self.list_versions(agent)}"
            ) from exc

    def has_full_production_coverage(self) -> bool:
        return self._lock.has_full_production_coverage()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Boot-time loader
    # ------------------------------------------------------------------

    @classmethod
    def from_lockfile(
        cls,
        lock_path: Path | str,
        *,
        repo_root: Path | str | None = None,
        require_full_production_coverage: bool = False,
    ) -> PromptRegistry:
        """Load the registry from disk with full verification.

        Steps in order — each one fail-closes on the slightest doubt:

        1. ``lock_path`` exists and is a regular file.
        2. The file parses as ``PromptLockFile`` (Pydantic strict).
        3. Every pinned version's YAML exists under ``repo_root`` (or
           the current working directory if ``repo_root`` is None).
        4. Every pinned version's SHA256 matches the stored hash.
        5. If ``require_full_production_coverage`` is True, every
           agent in :data:`MANDATORY_AGENTS` has a ``production``
           alias — this is what runtime decision paths require; X-001
           bootstrap callers pass False.
        """
        lock_path = Path(lock_path)
        if not lock_path.is_file():
            raise PromptLockFileNotFoundError(
                f"prompts lock file not found at {lock_path}; "
                f"create it (see config/prompts.lock.json) before boot"
            )
        try:
            raw = lock_path.read_text(encoding="utf-8")
            lock = PromptLockFile.model_validate_json(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise PromptLockFileMalformedError(
                f"{lock_path} failed schema validation: {exc}"
            ) from exc

        root = Path(repo_root) if repo_root is not None else Path.cwd()
        contents: dict[tuple[str, str], str] = {}
        for agent_name, agent_lock in lock.agents.items():
            for version_tag, entry in agent_lock.versions.items():
                prompt_path = root / entry.path
                if not prompt_path.is_file():
                    raise PromptFileNotFoundError(
                        f"pinned prompt {entry.path} (agent={agent_name}, "
                        f"version={version_tag}) is missing at "
                        f"{prompt_path}; restore from git or rebuild the lockfile"
                    )
                payload_bytes = prompt_path.read_bytes()
                actual_sha = compute_prompt_sha256(payload_bytes)
                if actual_sha != entry.sha256:
                    raise PromptChecksumMismatchError(
                        f"prompt {entry.path} sha256 mismatch: "
                        f"lockfile expected {entry.sha256}, file is "
                        f"{actual_sha} — restore from git or rebuild "
                        f"the lockfile via an explicit amendment"
                    )
                contents[(agent_name, version_tag)] = payload_bytes.decode(
                    "utf-8"
                )

        if require_full_production_coverage and not lock.has_full_production_coverage():
            missing = sorted(
                agent
                for agent in MANDATORY_AGENTS
                if (lock.agents.get(agent) is None
                    or "production" not in lock.agents[agent].aliases)
            )
            raise PromptAgentCoverageError(
                f"mandatory agents missing a production alias: {missing}; "
                f"runtime paths refuse to serve until every P0-10 §1.1 "
                f"agent is pinned"
            )

        return cls(lock, contents)


__all__ = [
    "MANDATORY_AGENTS",
    "PROMPT_ALIAS_NAMES",
    "PromptAgentCoverageError",
    "PromptAgentLock",
    "PromptChecksumMismatchError",
    "PromptFileNotFoundError",
    "PromptLockFile",
    "PromptLockFileMalformedError",
    "PromptLockFileNotFoundError",
    "PromptRegistry",
    "PromptRegistryError",
    "PromptVersionEntry",
    "SHA256_HEX_RE",
    "VERSION_TAG_RE",
    "compute_prompt_sha256",
]
