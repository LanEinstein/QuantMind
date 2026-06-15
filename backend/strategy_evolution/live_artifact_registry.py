"""LiveArtifactRegistry — the self-evolution approval gate (R-001).

Governance: P2-2-amendment-2026-05-24 §2.3 + R0 §8 (the 7 leakage paths) +
``backend/strategy_evolution/CLAUDE.md`` red line 1. Self-evolution (Phase R)
lets agents DISCOVER and shadow-validate strategies/factors/prompts/anomaly
models/RAG indexes, but NOTHING goes live except by an explicit human act:
draft amendment -> pin its content hash in immutable config -> git commit ->
restart. This module is the runtime enforcement of that rule.

Contract:

* At boot, :meth:`LiveArtifactRegistry.from_lockfile` loads an approved-hash
  set from ``config/live_artifacts.lock.json`` across the five locked artifact
  kinds (:class:`ArtifactKind`). Every entry is a SHA256 hex digest — the
  same content-addressing R0 §3 uses everywhere, so "approved" means "this
  exact bytes-pinned artifact".
* :meth:`is_approved` is a kind-typed set-membership check. Anything not in
  the set is refused — INCLUDING a perfectly valid hash that simply was never
  pinned (a discovery agent's unapproved high-Sharpe strategy). Kind-typing
  stops a strategy hash from ever admitting a prompt.
* There is **no runtime path to add a hash**: the registry is fully immutable
  (``__setattr__`` raises; no ``approve``/``add``/``reload``/``promote``).
  The empty bootstrap state denies everything — fail-closed by default.

Architectural invariants (mirror :mod:`backend.services.prompt_registry`):
zero runtime mutate, zero hot-reload, fail-closed boot, and zero
``backend.{api,broker,risk,llm,agents,mirofish,data}`` imports (P2-2 module
isolation — the registry must never reach back into the trading stack).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")
"""Lowercase 64-char hex SHA256 — matches ``hashlib.sha256().hexdigest()`` and
the prompt registry's pin format, so every pinned artifact is content-addressed."""


class ArtifactKind(StrEnum):
    """The five artifact kinds the registry gates (R0 §8 / P2-2 §2.3).

    Each closes a leakage path: a discovered STRATEGY_CODE referenced by live
    config, a FEATURE_DEF consumed by the live selector, a cached
    PROMPT_VERSION past its approval boundary, an ANOMALY_MODEL retrained
    without approval, a RAG_INDEX injecting unapproved rules.
    """

    STRATEGY_CODE = "strategy_code"
    FEATURE_DEF = "feature_def"
    PROMPT_VERSION = "prompt_version"
    ANOMALY_MODEL = "anomaly_model"
    RAG_INDEX = "rag_index"


# ---------------------------------------------------------------------------
# Errors — all fail-close the boot path (catching the base in prod is the bug)
# ---------------------------------------------------------------------------


class LiveArtifactRegistryError(Exception):
    """Base class for registry failures; subclasses fail-close boot."""


class LiveArtifactLockFileNotFoundError(LiveArtifactRegistryError):
    """``config/live_artifacts.lock.json`` is missing on disk."""


class LiveArtifactLockFileMalformedError(LiveArtifactRegistryError):
    """The lock file exists but is not valid JSON or fails the strict schema
    (bad SHA256 entry, unknown artifact kind, wrong version, ...)."""


# ---------------------------------------------------------------------------
# Schema — config/live_artifacts.lock.json
# ---------------------------------------------------------------------------


class ApprovedHashes(BaseModel):
    """The pinned hash set per artifact kind (field names == kind values).

    Every entry must be a 64-char lowercase hex SHA256; an unknown kind key is
    refused (``extra="forbid"``) so a typo cannot silently widen what is live.
    Empty lists are the valid bootstrap state (nothing pinned yet).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    strategy_code: tuple[str, ...] = ()
    feature_def: tuple[str, ...] = ()
    prompt_version: tuple[str, ...] = ()
    anomaly_model: tuple[str, ...] = ()
    rag_index: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_hex(self) -> ApprovedHashes:
        for kind in ArtifactKind:
            for entry in getattr(self, kind.value):
                if not SHA256_HEX_RE.fullmatch(entry):
                    raise ValueError(
                        f"approved {kind.value} entry must be a 64-char "
                        f"lowercase hex SHA256, got {entry!r}"
                    )
        return self


class LiveArtifactLockFile(BaseModel):
    """Root of ``config/live_artifacts.lock.json``.

    Tolerates an all-empty ``approved`` so the repo ships before any artifact
    is pinned; that bootstrap state denies everything (fail-closed).

    ``params`` is the AE-006 (AB-003-amendment-2026-06-14) schema v2 block:
    an optional ``{name: value}`` map of evolved quantitative parameters
    pinned by the human-gate. The registry itself ignores it — it only
    gates approved HASHES — but the field MUST be modelled so a v2 lockfile
    parses past ``extra="forbid"``. The :class:`RuntimeParamStore` reads and
    re-validates ``params`` (whitelist + clamp + group + frozen baseline) at
    boot. A v1 lockfile omits the key entirely → ``params == {}`` → the
    runtime is byte-identical to the pre-AE-006 system (the §4 red line).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    version: Literal["1.0", "2.0"]
    updated_at: datetime
    approved: ApprovedHashes = ApprovedHashes()
    params: dict[str, float] = Field(default_factory=dict)


def load_lockfile(lock_path: Path | str) -> LiveArtifactLockFile:
    """Locate + parse + fail-closed-validate the live-artifact lockfile.

    Single source of truth for loading ``config/live_artifacts.lock.json``,
    shared by :class:`LiveArtifactRegistry` (the approved-hash gate) and the
    :class:`~backend.strategy_evolution.runtime_param_store.RuntimeParamStore`
    (the schema v2 ``params`` block, AE-006) so the two never drift on file
    location, parse, or error taxonomy. A missing file fails closed (a deploy
    must ship the lock file — even the empty bootstrap one); malformed JSON or
    a schema violation fails closed too.
    """
    lock_path = Path(lock_path)
    if not lock_path.is_file():
        raise LiveArtifactLockFileNotFoundError(
            f"live-artifact lock file not found at {lock_path}; ship "
            f"config/live_artifacts.lock.json (empty is valid) before boot"
        )
    try:
        return LiveArtifactLockFile.model_validate_json(
            lock_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LiveArtifactLockFileMalformedError(
            f"{lock_path} failed schema validation: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Registry — immutable boot-time gate
# ---------------------------------------------------------------------------


class LiveArtifactRegistry:
    """Immutable, kind-typed approved-hash gate (see module docstring).

    Two-step construction mirrors :class:`PromptRegistry`: ``from_lockfile``
    for production wiring, ``from_lock`` for in-memory tests. Once built there
    is no mutate/reload/approve surface — promotions require amendment +
    restart (P2-2 §2.2).
    """

    __slots__ = ("_approved",)
    _approved: Mapping[ArtifactKind, frozenset[str]]

    def __init__(self, approved: Mapping[ArtifactKind, frozenset[str]]) -> None:
        # Materialise every kind (even empty) so ``approved``/``is_approved``
        # never depend on a missing key. The map is wrapped in a
        # MappingProxyType and its values are frozensets, so NEITHER the map
        # NOR a kind's set can be mutated through a leaked reference — there is
        # no ``registry._approved[kind] = {...}`` runtime hash-add path (codex
        # P1; the gate's whole point is amendment+restart-only promotion).
        object.__setattr__(
            self,
            "_approved",
            MappingProxyType(
                {
                    kind: frozenset(approved.get(kind, frozenset()))
                    for kind in ArtifactKind
                }
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"LiveArtifactRegistry is immutable; cannot set {name!r}. There is "
            f"no runtime path to add an approved hash — promotions require "
            f"amendment + restart (P2-2 §2.2)."
        )

    # -- read accessors -----------------------------------------------------

    def is_approved(self, kind: ArtifactKind, identifier: str) -> bool:
        """True iff ``identifier`` is pinned for ``kind``.

        Anything else is refused — an unknown kind, a malformed string, or a
        valid-but-unpinned hash. This is the gate the live selector consults.
        """
        return identifier in self._approved.get(kind, frozenset())

    def approved(self, kind: ArtifactKind) -> frozenset[str]:
        """The pinned hash set for ``kind`` (frozen — callers cannot mutate it)."""
        return self._approved.get(kind, frozenset())

    # -- boot-time loaders --------------------------------------------------

    @classmethod
    def from_lock(cls, lock: LiveArtifactLockFile) -> LiveArtifactRegistry:
        """Build from an already-validated lock model (in-memory / tests)."""
        return cls(
            {
                kind: frozenset(getattr(lock.approved, kind.value))
                for kind in ArtifactKind
            }
        )

    @classmethod
    def from_lockfile(cls, lock_path: Path | str) -> LiveArtifactRegistry:
        """Load + verify from disk; fail-closed on any structural problem.

        A missing file fails closed (a deploy must ship the lock file — even
        the empty bootstrap one) so a misconfigured deploy cannot silently
        admit nothing-OR-everything depending on a default.
        """
        return cls.from_lock(load_lockfile(lock_path))


__all__ = [
    "SHA256_HEX_RE",
    "ApprovedHashes",
    "ArtifactKind",
    "LiveArtifactLockFile",
    "LiveArtifactLockFileMalformedError",
    "LiveArtifactLockFileNotFoundError",
    "LiveArtifactRegistry",
    "LiveArtifactRegistryError",
    "load_lockfile",
]
