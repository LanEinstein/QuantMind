"""ThemeCandidateRegistry — human-pin gate for theme candidate artifacts (Y-004).

P0-8-amendment-2026-06-01 §2.5/§3: a theme candidate artifact may influence live
selection ONLY after a human approves it and its content hash is pinned (git +
restart), exactly the P2-2 / R-001 pin discipline. R-001 froze
:class:`LiveArtifactRegistry` to five artifact kinds (changing them needs an
R-amendment, which this P0-8 amendment does not grant), so the candidate artifact
— a distinct artifact class — is gated by this sibling registry that mirrors the
SAME posture: immutable, fail-closed boot, content-addressed, no runtime add path.
(The SOP *prompt* version still pins into the real ``LiveArtifactRegistry``
PROMPT_VERSION via Y-006.)

The empty bootstrap state denies everything — without a fresh pinned artifact the
theme quota is simply empty and the pure-quant path runs unchanged (the human
gate never stalls Line-1, §2.7).

Mirrors the registry invariants: zero runtime mutate, zero hot-reload, fail-closed
boot, no ``backend.{api,broker,risk,llm,agents,mirofish,data}`` imports.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

SHA256_HEX_RE = re.compile(r"^[a-f0-9]{64}$")


class ThemeCandidateRegistryError(Exception):
    """Base class; subclasses fail-close boot."""


class ThemeCandidateLockFileNotFoundError(ThemeCandidateRegistryError):
    """``config/theme_candidates.lock.json`` is missing on disk."""


class ThemeCandidateLockFileMalformedError(ThemeCandidateRegistryError):
    """The lock file exists but is not valid JSON or fails the strict schema."""


class ThemeCandidateLockFile(BaseModel):
    """Root of ``config/theme_candidates.lock.json``.

    ``approved`` is the set of content-addressed candidate-artifact hashes a human
    pinned. Empty is the valid bootstrap (deny-all). Each entry must be a 64-char
    lowercase hex SHA256 so a typo cannot silently widen what is live.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    version: Literal["1.0"]
    updated_at: datetime
    approved: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_hex(self) -> ThemeCandidateLockFile:
        for entry in self.approved:
            if not SHA256_HEX_RE.fullmatch(entry):
                raise ValueError(
                    f"approved theme-candidate entry must be 64-char lowercase "
                    f"hex SHA256, got {entry!r}"
                )
        return self


class ThemeCandidateRegistry:
    """Immutable, content-addressed approved-hash gate for candidate artifacts."""

    __slots__ = ("_approved",)
    _approved: frozenset[str]

    def __init__(self, approved: Iterable[str]) -> None:
        object.__setattr__(self, "_approved", frozenset(approved))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"ThemeCandidateRegistry is immutable; cannot set {name!r}. There is "
            f"no runtime path to pin a candidate — approval requires human review "
            f"+ pin + git + restart (P0-8-amendment-2026-06-01 §2.5)."
        )

    def is_pinned(self, content_hash: str) -> bool:
        """True iff ``content_hash`` was human-approved + pinned. Else refused —
        including a valid-but-unpinned artifact (fail-closed)."""
        return content_hash in self._approved

    @property
    def approved(self) -> frozenset[str]:
        return self._approved

    @classmethod
    def from_lock(cls, lock: ThemeCandidateLockFile) -> ThemeCandidateRegistry:
        return cls(lock.approved)

    @classmethod
    def from_lockfile(cls, lock_path: Path | str) -> ThemeCandidateRegistry:
        """Load + verify from disk; fail-closed on any structural problem."""
        lock_path = Path(lock_path)
        if not lock_path.is_file():
            raise ThemeCandidateLockFileNotFoundError(
                f"theme-candidate lock file not found at {lock_path}; ship "
                f"config/theme_candidates.lock.json (empty is valid) before boot"
            )
        try:
            lock = ThemeCandidateLockFile.model_validate_json(
                lock_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ThemeCandidateLockFileMalformedError(
                f"{lock_path} failed schema validation: {exc}"
            ) from exc
        return cls.from_lock(lock)


__all__ = [
    "SHA256_HEX_RE",
    "ThemeCandidateLockFile",
    "ThemeCandidateLockFileMalformedError",
    "ThemeCandidateLockFileNotFoundError",
    "ThemeCandidateRegistry",
    "ThemeCandidateRegistryError",
]
