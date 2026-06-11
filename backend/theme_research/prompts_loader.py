"""File-based versioned SOP prompt registry (Y-006).

The investigation/analysis prompt is NEVER hardcoded (P0-8-amendment-2026-06-01
§2.12 + §3): it lives in ``config/prompts/theme_research/{version}.yaml``, pinned
by SHA256 in ``config/prompts/theme_research/prompts.lock.json``, git-versioned +
restart-gated + amendment-gated. This mirrors :class:`PromptRegistry` exactly
(immutable, zero hot-reload, fail-closed boot) and adds two theme-specific
guarantees:

* **Frozen SOP skeleton.** The loaded YAML must enumerate exactly the five SOP
  steps (:data:`THEME_SOP_STEPS`) — the first-principles reverse-deduction
  methodology is frozen; only wording / exemplars / params evolve (§2.12, the
  T-001 persona-card vs T-004 exemplars split). A YAML missing/renaming a step
  fails closed.
* **LiveArtifactRegistry pin.** The active version's content SHA256 must be
  approved as an :class:`ArtifactKind.PROMPT_VERSION` hash when a registry is
  supplied with ``require_pinned=True`` — runtime only ever serves an approved
  prompt version (R-001 / P2-2). An evolved-but-unpinned prompt is refused even
  if its YAML is structurally valid.

Architectural invariants (same as the prompt + live-artifact registries):
zero runtime mutate, zero hot-reload, fail-closed boot, and no
``backend.{api,broker,risk,llm,agents,mirofish,data}`` imports.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.strategy_evolution.live_artifact_registry import (
    ArtifactKind,
    LiveArtifactRegistry,
)
from backend.theme_research.sop_schema import THEME_SOP_STEPS

VERSION_TAG_RE = re.compile(r"^v\d+(?:\.\d+)?$")
SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")

# The frozen step keys the SOP YAML must contain — the methodology skeleton.
_REQUIRED_STEP_KEYS: frozenset[str] = frozenset(s.value for s in THEME_SOP_STEPS)


def compute_prompt_sha256(content: bytes) -> str:
    """SHA256 hex of a prompt payload (one hashing scheme across the layer)."""
    return hashlib.sha256(content).hexdigest()


# ---------------------------------------------------------------------------
# Errors — all fail-close the boot path
# ---------------------------------------------------------------------------


class ThemePromptRegistryError(Exception):
    """Base class; subclasses fail-close boot (catching base in prod is the bug)."""


class ThemePromptLockFileNotFoundError(ThemePromptRegistryError):
    """``config/prompts/theme_research/prompts.lock.json`` missing on disk."""


class ThemePromptLockFileMalformedError(ThemePromptRegistryError):
    """Lock file exists but is not valid JSON or fails the strict schema."""


class ThemePromptFileNotFoundError(ThemePromptRegistryError):
    """A pinned ``{version}.yaml`` is missing on disk."""


class ThemePromptChecksumMismatchError(ThemePromptRegistryError):
    """A pinned YAML's content SHA256 does not match the lockfile (drift)."""


class ThemePromptSkeletonError(ThemePromptRegistryError):
    """The pinned YAML does not enumerate the frozen five SOP steps."""


class ThemePromptNotPinnedError(ThemePromptRegistryError):
    """The active version's hash is not approved in the LiveArtifactRegistry."""


# ---------------------------------------------------------------------------
# Schema — prompts.lock.json
# ---------------------------------------------------------------------------


class ThemePromptVersionEntry(BaseModel):
    """One pinned SOP prompt version: path + content SHA256 + pinner."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    pinned_at: datetime
    pinned_by: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _check_path_and_hash(self) -> ThemePromptVersionEntry:
        if not self.path.startswith("config/prompts/theme_research/"):
            raise ValueError(
                f"theme prompt path must live under "
                f"config/prompts/theme_research/, got {self.path!r}"
            )
        if not self.path.endswith(".yaml"):
            raise ValueError(
                f"theme prompt path must end with .yaml, got {self.path!r}"
            )
        # Path-traversal containment (mirrors PromptVersionEntry X-027): reject
        # any ``..`` component / absolute / backslash so the loader cannot read
        # or hash a file outside the prompts subtree.
        if any(p == ".." for p in PurePosixPath(self.path).parts):
            raise ValueError(
                f"theme prompt path must not contain '..' components, "
                f"got {self.path!r}"
            )
        if "\\" in self.path or self.path.startswith("/"):
            raise ValueError(
                f"theme prompt path must be a forward-slash relative path, "
                f"got {self.path!r}"
            )
        if not SHA256_HEX_RE.fullmatch(self.sha256):
            raise ValueError(
                f"sha256 must be 64-char lowercase hex, got {self.sha256!r}"
            )
        return self


class ThemePromptLockFile(BaseModel):
    """Root of ``config/prompts/theme_research/prompts.lock.json``.

    ``active_version`` is the single live SOP prompt version; it must be present
    in ``versions``. An empty ``versions`` is permitted as a bootstrap state but
    then ``active_version`` must be empty too (nothing to serve — fail-closed).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    version: Literal["1.0"]
    updated_at: datetime
    active_version: str = ""
    versions: dict[str, ThemePromptVersionEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_active(self) -> ThemePromptLockFile:
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


# ---------------------------------------------------------------------------
# Skeleton validation — the frozen methodology guard
# ---------------------------------------------------------------------------


def validate_sop_skeleton(yaml_content: str) -> None:
    """Raise :class:`ThemePromptSkeletonError` if the SOP skeleton is wrong.

    The YAML must parse to a mapping with a ``steps`` mapping whose keys are
    EXACTLY the five frozen :data:`THEME_SOP_STEPS` (no missing, no extra, no
    renamed step). This is what keeps the first-principles reverse-deduction
    methodology immutable while wording/exemplars below it stay editable.
    """
    try:
        doc = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise ThemePromptSkeletonError(f"SOP YAML does not parse: {exc}") from exc
    if not isinstance(doc, dict):
        raise ThemePromptSkeletonError("SOP YAML root must be a mapping")
    steps = doc.get("steps")
    if not isinstance(steps, dict):
        raise ThemePromptSkeletonError("SOP YAML must have a 'steps' mapping")
    keys = frozenset(steps.keys())
    if keys != _REQUIRED_STEP_KEYS:
        missing = sorted(_REQUIRED_STEP_KEYS - keys)
        extra = sorted(keys - _REQUIRED_STEP_KEYS)
        raise ThemePromptSkeletonError(
            f"SOP skeleton must enumerate exactly the five frozen steps "
            f"{sorted(_REQUIRED_STEP_KEYS)}; missing={missing} extra={extra}"
        )
    # The reverse-deduction methodology is an ORDERED pipeline (codex Y P2):
    # direction → sectors → chain → chokepoint → tickers. A YAML with the right
    # keys in the wrong order would invert the methodology — reject it.
    ordered = tuple(steps.keys())
    expected = tuple(s.value for s in THEME_SOP_STEPS)
    if ordered != expected:
        raise ThemePromptSkeletonError(
            f"SOP steps must appear in the frozen order {expected}, got {ordered}"
        )


# ---------------------------------------------------------------------------
# Registry — immutable boot-time loader
# ---------------------------------------------------------------------------


class ThemePromptRegistry:
    """Immutable boot-time view over the pinned SOP prompt (see module docstring).

    Two-step construction mirrors :class:`PromptRegistry`: ``from_lockfile`` for
    production wiring, ``__init__`` for in-memory tests. No mutate/reload surface.
    """

    __slots__ = ("_active_version", "_active_sha256", "_content")
    _active_version: str
    _active_sha256: str
    _content: str

    def __init__(
        self, active_version: str, active_sha256: str, content: str
    ) -> None:
        object.__setattr__(self, "_active_version", active_version)
        object.__setattr__(self, "_active_sha256", active_sha256)
        object.__setattr__(self, "_content", content)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"ThemePromptRegistry is immutable; cannot set {name!r}. Promotions "
            f"require amendment + repin + git + restart (P0-8-amendment §2.12)."
        )

    @property
    def active_version(self) -> str:
        return self._active_version

    @property
    def active_sha256(self) -> str:
        return self._active_sha256

    def active_prompt(self) -> str:
        """The pinned SOP prompt YAML content (UTF-8 string)."""
        return self._content

    @classmethod
    def from_lockfile(
        cls,
        lock_path: Path | str,
        *,
        repo_root: Path | str | None = None,
        registry: LiveArtifactRegistry | None = None,
        require_pinned: bool = False,
    ) -> ThemePromptRegistry:
        """Load + verify from disk; fail-closed on any structural problem.

        Steps (each fail-closes): lock file exists + parses → an
        ``active_version`` is set → its YAML exists under ``repo_root`` →
        its SHA256 matches → the SOP skeleton is the frozen five steps → (if
        ``require_pinned``) its hash is approved in ``registry`` as a
        ``PROMPT_VERSION``.
        """
        lock_path = Path(lock_path)
        if not lock_path.is_file():
            raise ThemePromptLockFileNotFoundError(
                f"theme prompt lock file not found at {lock_path}; ship "
                f"config/prompts/theme_research/prompts.lock.json before boot"
            )
        try:
            lock = ThemePromptLockFile.model_validate_json(
                lock_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ThemePromptLockFileMalformedError(
                f"{lock_path} failed schema validation: {exc}"
            ) from exc

        if not lock.active_version:
            raise ThemePromptLockFileMalformedError(
                f"{lock_path} has no active_version pinned; runtime refuses to "
                f"serve an unpinned SOP prompt (fail-closed)"
            )

        entry = lock.versions[lock.active_version]
        root = Path(repo_root) if repo_root is not None else Path.cwd()
        prompt_path = root / entry.path
        if not prompt_path.is_file():
            raise ThemePromptFileNotFoundError(
                f"pinned SOP prompt {entry.path} (version={lock.active_version}) "
                f"missing at {prompt_path}; restore from git or rebuild lockfile"
            )
        payload = prompt_path.read_bytes()
        actual = compute_prompt_sha256(payload)
        if actual != entry.sha256:
            raise ThemePromptChecksumMismatchError(
                f"SOP prompt {entry.path} sha256 mismatch: lockfile expected "
                f"{entry.sha256}, file is {actual} — restore from git or rebuild "
                f"the lockfile via an explicit amendment"
            )
        content = payload.decode("utf-8")
        validate_sop_skeleton(content)

        if require_pinned:
            if registry is None:
                raise ThemePromptNotPinnedError(
                    "require_pinned=True but no LiveArtifactRegistry supplied; "
                    "runtime cannot verify the SOP prompt is approved"
                )
            if not registry.is_approved(ArtifactKind.PROMPT_VERSION, actual):
                raise ThemePromptNotPinnedError(
                    f"SOP prompt version {lock.active_version} (sha {actual}) is "
                    f"not approved in LiveArtifactRegistry; pin it via amendment "
                    f"+ restart before runtime use (R-001 / P2-2)"
                )

        return cls(lock.active_version, actual, content)


__all__ = [
    "SHA256_HEX_RE",
    "VERSION_TAG_RE",
    "ThemePromptChecksumMismatchError",
    "ThemePromptFileNotFoundError",
    "ThemePromptLockFile",
    "ThemePromptLockFileMalformedError",
    "ThemePromptLockFileNotFoundError",
    "ThemePromptNotPinnedError",
    "ThemePromptRegistry",
    "ThemePromptRegistryError",
    "ThemePromptSkeletonError",
    "ThemePromptVersionEntry",
    "compute_prompt_sha256",
    "validate_sop_skeleton",
]
