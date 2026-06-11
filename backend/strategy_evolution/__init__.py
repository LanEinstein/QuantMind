"""Self-evolution (Phase R) — discovery + shadow-validation behind a human gate.

WHY this module exists: P2-2-amendment-2026-05-24 promotes the deferred
self-evolution track to active discovery. Agents may PROPOSE strategies /
factors / prompts / anomaly models / RAG indexes and validate them in a 45-day
shadow, but NOTHING goes live except by an explicit human act (amendment + pin
+ git + restart). :class:`LiveArtifactRegistry` (R-001) is the runtime
enforcement of that rule — the single approval gate every discovered artifact
must pass before the live path can read or execute it.

Red lines (module CLAUDE.md / P2-2): import-isolated (never imports
``backend.{api,broker,risk,llm,agents,mirofish,data}``), human-gated (no
agent auto-promotion), append-only/fail-closed.
"""

from backend.strategy_evolution.live_artifact_registry import (
    SHA256_HEX_RE,
    ApprovedHashes,
    ArtifactKind,
    LiveArtifactLockFile,
    LiveArtifactLockFileMalformedError,
    LiveArtifactLockFileNotFoundError,
    LiveArtifactRegistry,
    LiveArtifactRegistryError,
)

__all__ = [
    "SHA256_HEX_RE",
    "ApprovedHashes",
    "ArtifactKind",
    "LiveArtifactLockFile",
    "LiveArtifactLockFileMalformedError",
    "LiveArtifactLockFileNotFoundError",
    "LiveArtifactRegistry",
    "LiveArtifactRegistryError",
]
