"""Frozen, git-versioned trader-persona card registry (T-001).

Governance: P0-10-amendment-2026-05-24 §2.1/§2.2 (固定 4 必经 agent → 可进化多
agent 团队 + ≥2 交易员) + R0 §4 (single construction point) + P2-2 (LiveArtifactRegistry
pin). The ≥2 trader agents each carry one **persona card** — an immutable
identity / mandate / output-contract — kept under
``config/prompts/{trader}/{version}.yaml`` and pinned by SHA256 in
``config/prompts/traders.lock.json``. git-versioned + restart-gated +
amendment-gated, mirroring :class:`backend.theme_research.prompts_loader
.ThemePromptRegistry` exactly (immutable, zero hot-reload, fail-closed boot).

Two card-specific guarantees on top of the SHA256 pin:

* **Frozen persona skeleton.** The loaded YAML must enumerate exactly the frozen
  identity keys (:data:`_REQUIRED_PERSONA_KEYS` = ``version`` / ``persona_id`` /
  ``identity`` / ``mandate`` / ``output_contract``); ``persona_id`` must equal
  the card's directory name. This is the T-001 "人格卡定义『agent 是谁』" half —
  it is immutable. The behavioural half ("好输出示范") lives in the OPTIONAL
  ``exemplars`` list (``≤3``, T-004) and is what evolves — never the skeleton
  (P0-10-amendment-2026-05-24 §2.2, the persona-card vs exemplars split).
* **LiveArtifactRegistry pin.** Each active card's content SHA256 must be
  approved as an :class:`ArtifactKind.PROMPT_VERSION` hash when a registry is
  supplied with ``require_pinned=True`` — runtime only ever serves an approved
  persona card (R-001 / P2-2). An evolved-but-unpinned card is refused even if
  its YAML is structurally valid.

The persona cards are ADDITIVE: they never reduce the four mandatory agents
(P0-10 §2.3 red line 3). The traders only emit advisory free text; the
``fund_manager`` remains the sole BUY/SELL/HOLD proposer and the builder still
derives ``volume`` / ``limit_price`` deterministically (R0 §4 — never from a
trader's text). That decision-path invariant is enforced by the graph topology
(T-002), not by this loader; this module only governs the immutable cards.

Architectural invariants (same as the prompt / theme / live-artifact
registries): zero runtime mutate, zero hot-reload, fail-closed boot. This
module imports only ``backend.strategy_evolution.live_artifact_registry`` (a
pure config gate) from the backend tree — never an LLM/decision module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.strategy_evolution.live_artifact_registry import (
    ArtifactKind,
    LiveArtifactRegistry,
)

VERSION_TAG_RE = re.compile(r"^v\d+(?:\.\d+)?$")
SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")
PERSONA_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

# The four mandatory agents (P0-10 §2.3) — a persona id may NEVER collide with
# one of them, or a trader card could be served under a mandatory-agent name and
# silently displace/conflict with the required agent set (the additive invariant,
# codex T-001 P2). Mirrors ``backend.agents_team.state.MANDATORY_AGENTS``; kept
# as a local literal so this config gate has no dependency on the LLM state
# module. The set is a frozen red-line constant.
_MANDATORY_AGENT_NAMES: frozenset[str] = frozenset(
    {"fundamental_analyst", "technical_analyst", "risk_officer", "fund_manager"}
)

# The frozen top-level keys a persona card YAML must contain — the immutable
# identity skeleton (P0-10-amendment-2026-05-24 §2.2). Only these are frozen;
# ``exemplars`` / ``constraints`` below them stay editable (T-004).
_REQUIRED_PERSONA_KEYS: frozenset[str] = frozenset(
    {"version", "persona_id", "identity", "mandate", "output_contract"}
)

# The minimum number of trader personas the runtime requires (≥2 traders,
# P0-10-amendment-2026-05-24 §2.1). Adding/removing a persona is an
# amendment + restart, never a hot change.
MIN_TRADER_PERSONAS: int = 2

# Behavioural exemplars are capped at three per card (FinMem style, T-004 /
# P0-10-amendment-2026-05-24 §2.2). A card shipping more fails closed so the
# cap cannot be widened by editing a YAML out of band.
MAX_PERSONA_EXEMPLARS: int = 3

# The trader personas are ADDITIVE — they never share an id with (and so never
# displace) the four mandatory agents (P0-10-amendment-2026-05-24 §2.3 red line
# 3). Asserted by the T-001 contract test.
MANDATORY_PERSONA_DISJOINT_ERROR: str = (
    "trader personas must be disjoint from the four mandatory agents; a persona "
    "may never reduce or replace fundamental_analyst / technical_analyst / "
    "risk_officer / fund_manager"
)


def compute_persona_sha256(content: bytes) -> str:
    """SHA256 hex of a persona-card payload (one hashing scheme across the layer)."""
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Errors — all fail-close the boot path (catching the base in prod is the bug)
# ---------------------------------------------------------------------------


class TraderPersonaRegistryError(Exception):
    """Base class; subclasses fail-close boot (catching base in prod is the bug)."""


class TraderPersonaLockFileNotFoundError(TraderPersonaRegistryError):
    """``config/prompts/traders.lock.json`` is missing on disk."""


class TraderPersonaLockFileMalformedError(TraderPersonaRegistryError):
    """The lock file exists but is not valid JSON or fails the strict schema."""


class TraderPersonaFileNotFoundError(TraderPersonaRegistryError):
    """A pinned ``{trader}/{version}.yaml`` is missing on disk."""


class TraderPersonaChecksumMismatchError(TraderPersonaRegistryError):
    """A pinned card's content SHA256 does not match the lockfile (drift)."""


class TraderPersonaSkeletonError(TraderPersonaRegistryError):
    """The pinned YAML does not enumerate the frozen persona skeleton."""


class TraderPersonaNotPinnedError(TraderPersonaRegistryError):
    """The active version's hash is not approved in the LiveArtifactRegistry."""


class TraderPersonaCoverageError(TraderPersonaRegistryError):
    """Fewer than :data:`MIN_TRADER_PERSONAS` personas are pinned for runtime."""


# ---------------------------------------------------------------------------
# Schema — traders.lock.json
# ---------------------------------------------------------------------------


class TraderPersonaVersionEntry(BaseModel):
    """One pinned persona-card version: path + content SHA256 + pinner."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    pinned_at: datetime
    pinned_by: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _check_path_and_hash(self) -> TraderPersonaVersionEntry:
        if not self.path.startswith("config/prompts/"):
            raise ValueError(
                f"persona card path must live under config/prompts/, "
                f"got {self.path!r}"
            )
        if not self.path.endswith(".yaml"):
            raise ValueError(
                f"persona card path must end with .yaml, got {self.path!r}"
            )
        # Path-traversal containment (mirrors PromptVersionEntry X-027): reject
        # any ``..`` component / absolute / backslash so the loader cannot read
        # or hash a file outside the prompts subtree.
        if any(p == ".." for p in PurePosixPath(self.path).parts):
            raise ValueError(
                f"persona card path must not contain '..' components, "
                f"got {self.path!r}"
            )
        if "\\" in self.path or self.path.startswith("/"):
            raise ValueError(
                f"persona card path must be a forward-slash relative path, "
                f"got {self.path!r}"
            )
        if not SHA256_HEX_RE.fullmatch(self.sha256):
            raise ValueError(
                f"sha256 must be 64-char lowercase hex, got {self.sha256!r}"
            )
        return self


class TraderPersonaLock(BaseModel):
    """All pinned versions for one trader persona + its single active version."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    active_version: str = ""
    versions: dict[str, TraderPersonaVersionEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_active(self) -> TraderPersonaLock:
        for tag in self.versions:
            if not VERSION_TAG_RE.fullmatch(tag):
                raise ValueError(
                    f"version tag must match {VERSION_TAG_RE.pattern!r}, got {tag!r}"
                )
        if self.active_version:
            if not VERSION_TAG_RE.fullmatch(self.active_version):
                raise ValueError(
                    f"active_version must match {VERSION_TAG_RE.pattern!r}, "
                    f"got {self.active_version!r}"
                )
            if self.active_version not in self.versions:
                raise ValueError(
                    f"active_version {self.active_version!r} not present in "
                    f"versions {sorted(self.versions)}"
                )
        elif self.versions:
            raise ValueError(
                "versions are pinned but active_version is empty; set "
                "active_version to one of them (fail-closed: nothing served)"
            )
        return self


class TraderPersonaLockFile(BaseModel):
    """Root of ``config/prompts/traders.lock.json``.

    Tolerates an empty ``personas`` map as a bootstrap state (nothing served —
    fail-closed). Persona ids are validated against :data:`PERSONA_ID_RE`.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    version: Literal["1.0"]
    updated_at: datetime
    personas: dict[str, TraderPersonaLock] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_persona_ids(self) -> TraderPersonaLockFile:
        for persona_id in self.personas:
            if not PERSONA_ID_RE.fullmatch(persona_id):
                raise ValueError(
                    f"persona id must match {PERSONA_ID_RE.pattern!r}, "
                    f"got {persona_id!r}"
                )
            # Fail closed on the additive invariant: a trader persona may never
            # reuse a mandatory-agent name (codex T-001 P2).
            if persona_id in _MANDATORY_AGENT_NAMES:
                raise ValueError(
                    f"persona id {persona_id!r} collides with a mandatory agent; "
                    f"trader personas are ADDITIVE and must never reuse "
                    f"{sorted(_MANDATORY_AGENT_NAMES)} "
                    f"(P0-10-amendment-2026-05-24 §2.3)"
                )
        return self


# ---------------------------------------------------------------------------
# Loaded persona card (immutable value)
# ---------------------------------------------------------------------------


class TraderPersona(BaseModel):
    """One loaded, verified, immutable trader persona card.

    ``identity`` / ``mandate`` / ``output_contract`` are the frozen skeleton
    (who the agent is + what it may emit); ``exemplars`` is the editable ``≤3``
    behavioural-demonstration list (T-004). ``content`` is the raw verified YAML
    used to render the system prompt for the trader's LLM call (T-002).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    persona_id: str
    version: str
    sha256: str
    identity: str = Field(min_length=1)
    mandate: str = Field(min_length=1)
    output_contract: str = Field(min_length=1)
    exemplars: tuple[str, ...] = ()
    content: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Skeleton validation — the frozen identity guard
# ---------------------------------------------------------------------------


def validate_persona_skeleton(
    yaml_content: str, *, expected_persona_id: str
) -> dict[str, Any]:
    """Parse + validate the frozen persona skeleton; return the parsed mapping.

    Raises :class:`TraderPersonaSkeletonError` unless the YAML parses to a
    mapping that (a) contains every frozen key in :data:`_REQUIRED_PERSONA_KEYS`,
    (b) has ``persona_id`` equal to ``expected_persona_id`` (the directory name),
    and (c) has at most :data:`MAX_PERSONA_EXEMPLARS` string exemplars. The
    frozen keys keep "who the agent is" immutable; only the optional
    ``exemplars`` / ``constraints`` below evolve (T-004).
    """
    try:
        doc = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise TraderPersonaSkeletonError(
            f"persona card YAML does not parse: {exc}"
        ) from exc
    if not isinstance(doc, dict):
        raise TraderPersonaSkeletonError("persona card YAML root must be a mapping")
    missing = sorted(_REQUIRED_PERSONA_KEYS - frozenset(doc.keys()))
    if missing:
        raise TraderPersonaSkeletonError(
            f"persona card {expected_persona_id!r} missing frozen skeleton keys "
            f"{missing}; required {sorted(_REQUIRED_PERSONA_KEYS)}"
        )
    declared = doc.get("persona_id")
    if declared != expected_persona_id:
        raise TraderPersonaSkeletonError(
            f"persona card declares persona_id {declared!r} but lives under "
            f"{expected_persona_id!r}; the id must match its directory"
        )
    for key in ("identity", "mandate", "output_contract"):
        value = doc.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TraderPersonaSkeletonError(
                f"persona card {expected_persona_id!r} key {key!r} must be a "
                f"non-empty string"
            )
    exemplars = doc.get("exemplars", [])
    if exemplars is None:
        exemplars = []
    if not isinstance(exemplars, list):
        raise TraderPersonaSkeletonError(
            f"persona card {expected_persona_id!r} 'exemplars' must be a list"
        )
    if len(exemplars) > MAX_PERSONA_EXEMPLARS:
        raise TraderPersonaSkeletonError(
            f"persona card {expected_persona_id!r} has {len(exemplars)} exemplars; "
            f"the FinMem cap is {MAX_PERSONA_EXEMPLARS} (T-004)"
        )
    for item in exemplars:
        if not isinstance(item, str):
            raise TraderPersonaSkeletonError(
                f"persona card {expected_persona_id!r} exemplars must be strings"
            )
    return doc


# ---------------------------------------------------------------------------
# Registry — immutable boot-time loader
# ---------------------------------------------------------------------------


class TraderPersonaRegistry:
    """Immutable boot-time view over the pinned trader persona cards.

    Two-step construction mirrors :class:`ThemePromptRegistry`: ``from_lockfile``
    for production wiring, ``__init__`` for in-memory tests. No mutate/reload
    surface — promotions require amendment + repin + git + restart.
    """

    __slots__ = ("_personas",)
    _personas: Mapping[str, TraderPersona]

    def __init__(self, personas: Mapping[str, TraderPersona]) -> None:
        # Defensive copy wrapped in a read-only proxy: the values are already
        # frozen Pydantic models and the map itself cannot be mutated through a
        # leaked ``reg._personas`` reference (no ``.clear()`` / item assignment),
        # mirroring the prompt/live-artifact registries (codex T-001 P2).
        object.__setattr__(
            self, "_personas", MappingProxyType(dict(personas))
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"TraderPersonaRegistry is immutable; cannot set {name!r}. Promotions "
            f"require amendment + repin + git + restart "
            f"(P0-10-amendment-2026-05-24 §2.4)."
        )

    # -- read accessors -----------------------------------------------------

    def persona_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._personas))

    def get(self, persona_id: str) -> TraderPersona:
        try:
            return self._personas[persona_id]
        except KeyError as exc:
            raise KeyError(
                f"no persona {persona_id!r}; configured: {self.persona_ids()}"
            ) from exc

    def personas(self) -> tuple[TraderPersona, ...]:
        """All loaded personas, ordered by id (deterministic graph wiring)."""
        return tuple(self._personas[pid] for pid in self.persona_ids())

    # -- boot-time loader ---------------------------------------------------

    @classmethod
    def from_lockfile(
        cls,
        lock_path: Path | str,
        *,
        repo_root: Path | str | None = None,
        registry: LiveArtifactRegistry | None = None,
        require_pinned: bool = False,
        require_full_coverage: bool = False,
    ) -> TraderPersonaRegistry:
        """Load + verify from disk; fail-closed on any structural problem.

        Per persona, in order (each fail-closes): the lock parses → an
        ``active_version`` is set → its YAML exists under ``repo_root`` → its
        SHA256 matches → the frozen skeleton is present + ``persona_id`` matches →
        (if ``require_pinned``) its hash is approved in ``registry`` as a
        ``PROMPT_VERSION``. With ``require_full_coverage`` the runtime additionally
        requires ``≥`` :data:`MIN_TRADER_PERSONAS` personas (≥2 traders).
        """
        lock_path = Path(lock_path)
        if not lock_path.is_file():
            raise TraderPersonaLockFileNotFoundError(
                f"trader persona lock file not found at {lock_path}; ship "
                f"config/prompts/traders.lock.json before boot"
            )
        try:
            lock = TraderPersonaLockFile.model_validate_json(
                lock_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise TraderPersonaLockFileMalformedError(
                f"{lock_path} failed schema validation: {exc}"
            ) from exc

        root = Path(repo_root) if repo_root is not None else Path.cwd()
        personas: dict[str, TraderPersona] = {}
        for persona_id, persona_lock in lock.personas.items():
            if not persona_lock.active_version:
                raise TraderPersonaLockFileMalformedError(
                    f"persona {persona_id!r} has no active_version pinned; runtime "
                    f"refuses to serve an unpinned persona card (fail-closed)"
                )
            entry = persona_lock.versions[persona_lock.active_version]
            # Fail closed on a lock entry whose path does not match the
            # `config/prompts/{persona_id}/{active_version}.yaml` convention —
            # otherwise a malformed lockfile could serve a card from the wrong
            # persona/version directory (codex T-001 P2).
            expected_path = (
                f"config/prompts/{persona_id}/{persona_lock.active_version}.yaml"
            )
            if entry.path != expected_path:
                raise TraderPersonaLockFileMalformedError(
                    f"persona {persona_id!r} active version "
                    f"{persona_lock.active_version!r} must pin {expected_path!r}, "
                    f"got {entry.path!r}; the card path must match its "
                    f"persona/version (fail-closed)"
                )
            card_path = root / entry.path
            if not card_path.is_file():
                raise TraderPersonaFileNotFoundError(
                    f"pinned persona card {entry.path} (persona={persona_id}, "
                    f"version={persona_lock.active_version}) missing at {card_path}; "
                    f"restore from git or rebuild lockfile"
                )
            payload = card_path.read_bytes()
            actual = compute_persona_sha256(payload)
            if actual != entry.sha256:
                raise TraderPersonaChecksumMismatchError(
                    f"persona card {entry.path} sha256 mismatch: lockfile expected "
                    f"{entry.sha256}, file is {actual} — restore from git or rebuild "
                    f"the lockfile via an explicit amendment"
                )
            content = payload.decode("utf-8")
            doc = validate_persona_skeleton(content, expected_persona_id=persona_id)
            # The card's own ``version:`` field must equal the pinned active
            # version — a card whose body says v2 served under a v1 pin is a
            # version mismatch and fails closed (codex T-001 P2).
            if str(doc.get("version")) != persona_lock.active_version:
                raise TraderPersonaSkeletonError(
                    f"persona card {persona_id!r} declares version "
                    f"{doc.get('version')!r} but is pinned as "
                    f"{persona_lock.active_version!r}; the card version must match "
                    f"the active version (fail-closed)"
                )

            if require_pinned:
                if registry is None:
                    raise TraderPersonaNotPinnedError(
                        f"require_pinned=True but no LiveArtifactRegistry supplied; "
                        f"runtime cannot verify persona {persona_id!r} is approved"
                    )
                if not registry.is_approved(ArtifactKind.PROMPT_VERSION, actual):
                    raise TraderPersonaNotPinnedError(
                        f"persona card {persona_id!r} version "
                        f"{persona_lock.active_version} (sha {actual}) is not "
                        f"approved in LiveArtifactRegistry; pin it via amendment + "
                        f"restart before runtime use (R-001 / P2-2)"
                    )

            exemplars = tuple(str(e) for e in (doc.get("exemplars") or []))
            personas[persona_id] = TraderPersona(
                persona_id=persona_id,
                version=persona_lock.active_version,
                sha256=actual,
                identity=str(doc["identity"]),
                mandate=str(doc["mandate"]),
                output_contract=str(doc["output_contract"]),
                exemplars=exemplars,
                content=content,
            )

        if require_full_coverage and len(personas) < MIN_TRADER_PERSONAS:
            raise TraderPersonaCoverageError(
                f"runtime requires ≥{MIN_TRADER_PERSONAS} trader personas "
                f"(P0-10-amendment-2026-05-24 §2.1); only {len(personas)} pinned: "
                f"{sorted(personas)}"
            )

        return cls(personas)


__all__ = [
    "MANDATORY_PERSONA_DISJOINT_ERROR",
    "MAX_PERSONA_EXEMPLARS",
    "MIN_TRADER_PERSONAS",
    "PERSONA_ID_RE",
    "SHA256_HEX_RE",
    "VERSION_TAG_RE",
    "TraderPersona",
    "TraderPersonaChecksumMismatchError",
    "TraderPersonaCoverageError",
    "TraderPersonaFileNotFoundError",
    "TraderPersonaLock",
    "TraderPersonaLockFile",
    "TraderPersonaLockFileMalformedError",
    "TraderPersonaLockFileNotFoundError",
    "TraderPersonaNotPinnedError",
    "TraderPersonaRegistry",
    "TraderPersonaSkeletonError",
    "TraderPersonaVersionEntry",
    "compute_persona_sha256",
    "validate_persona_skeleton",
]
