"""R-001 — LiveArtifactRegistry: adversarial-tests-first (RED before GREEN).

Governance: P2-2-amendment-2026-05-24 §2.3 + R0 §8 (7 leakage paths) +
backend/strategy_evolution/CLAUDE.md red line 1. The registry is the central
approval GATE for self-evolution: at boot it loads an immutable approved-hash
set across 5 artifact kinds; the live path admits ONLY pinned hashes and there
is NO runtime path to add one. A valid-but-unpinned artifact (e.g. an
unapproved high-Sharpe strategy a discovery agent surfaced) must be refused.

These tests assert the LEAKAGE-IS-IMPOSSIBLE posture, not the happy path
(Codex round-2 §4): planted unapproved artifacts can be neither read nor
executed by a live consumer.
"""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.strategy_evolution.live_artifact_registry import (
    ApprovedHashes,
    ArtifactKind,
    LiveArtifactLockFile,
    LiveArtifactLockFileMalformedError,
    LiveArtifactLockFileNotFoundError,
    LiveArtifactRegistry,
    LiveArtifactRegistryError,
)


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


_APPROVED_STRATEGY = _sha("approved-momentum-v1")
_UNAPPROVED_STRATEGY = _sha("unapproved-high-sharpe-overfit")  # the attacker
_APPROVED_PROMPT = _sha("approved-fund-manager-prompt-v3")


def _registry(**kinds: tuple[str, ...]) -> LiveArtifactRegistry:
    """Build an in-memory registry (no filesystem) for unit tests."""
    return LiveArtifactRegistry.from_lock(
        LiveArtifactLockFile(
            version="1.0",
            updated_at=datetime(2026, 6, 11, tzinfo=UTC),
            approved=ApprovedHashes(**kinds),
        )
    )


# -- the registry IS the gate: kind enum + signatures ---------------------------


def test_artifact_kinds_are_the_locked_five() -> None:
    assert {k.value for k in ArtifactKind} == {
        "strategy_code", "feature_def", "prompt_version",
        "anomaly_model", "rag_index",
    }


# -- ADVERSARIAL: a planted unapproved artifact is refused ----------------------


def _live_select(registry: LiveArtifactRegistry, strategy_hash: str) -> str | None:
    """Stand-in for the live selector gate (the real wiring is Y-004): a
    candidate strategy may be read/executed ONLY if its code hash is pinned."""
    if not registry.is_approved(ArtifactKind.STRATEGY_CODE, strategy_hash):
        return None  # gated out — unreadable / unexecutable
    return strategy_hash


def test_unapproved_high_sharpe_strategy_is_gated_out() -> None:
    reg = _registry(strategy_code=(_APPROVED_STRATEGY,))
    # The discovery agent surfaced a great-looking strategy, but it was never
    # pinned via amendment+restart -> the live selector cannot touch it.
    assert _live_select(reg, _UNAPPROVED_STRATEGY) is None
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, _UNAPPROVED_STRATEGY) is False
    # The pinned one passes.
    assert _live_select(reg, _APPROVED_STRATEGY) == _APPROVED_STRATEGY
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, _APPROVED_STRATEGY) is True


def test_valid_but_unpinned_hash_is_refused() -> None:
    """Validity != approval: a well-formed SHA256 simply not in the set is denied."""
    reg = _registry(strategy_code=(_APPROVED_STRATEGY,))
    unpinned = _sha("perfectly-valid-but-never-approved")
    assert len(unpinned) == 64
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, unpinned) is False


def test_cross_kind_isolation() -> None:
    """A hash pinned as a STRATEGY must not be admitted as a PROMPT (or any
    other kind) — kind-typing stops a strategy hash from approving a prompt."""
    reg = _registry(strategy_code=(_APPROVED_STRATEGY,))
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, _APPROVED_STRATEGY) is True
    for other in (ArtifactKind.PROMPT_VERSION, ArtifactKind.FEATURE_DEF,
                  ArtifactKind.ANOMALY_MODEL, ArtifactKind.RAG_INDEX):
        assert reg.is_approved(other, _APPROVED_STRATEGY) is False


def test_each_kind_independent() -> None:
    reg = _registry(
        strategy_code=(_APPROVED_STRATEGY,),
        prompt_version=(_APPROVED_PROMPT,),
    )
    assert reg.is_approved(ArtifactKind.PROMPT_VERSION, _APPROVED_PROMPT) is True
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, _APPROVED_PROMPT) is False
    assert reg.approved(ArtifactKind.STRATEGY_CODE) == frozenset({_APPROVED_STRATEGY})
    assert reg.approved(ArtifactKind.ANOMALY_MODEL) == frozenset()


# -- NO runtime path to add a hash (the core red line) --------------------------


def test_registry_is_immutable_no_runtime_add() -> None:
    reg = _registry(strategy_code=(_APPROVED_STRATEGY,))
    # No add/approve/reload/promote surface exists.
    for forbidden in ("approve", "add", "reload", "promote", "pin", "update"):
        assert not hasattr(reg, forbidden), forbidden
    # Attribute assignment is refused (cannot swap the approved set at runtime).
    with pytest.raises(AttributeError):
        reg.is_approved = lambda *a: True  # type: ignore[assignment]
    with pytest.raises(AttributeError):
        reg._approved = {}  # type: ignore[attr-defined]
    # ...and the internal map itself is read-only: no item-assignment hash-add.
    with pytest.raises(TypeError):
        reg._approved[ArtifactKind.STRATEGY_CODE] = frozenset({_UNAPPROVED_STRATEGY})  # type: ignore[index]


def test_returned_set_cannot_mutate_registry() -> None:
    reg = _registry(strategy_code=(_APPROVED_STRATEGY,))
    got = reg.approved(ArtifactKind.STRATEGY_CODE)
    assert isinstance(got, frozenset)  # cannot .add() to it
    # Even if a caller tries, the registry's own view is unchanged.
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, _UNAPPROVED_STRATEGY) is False


# -- fail-closed boot loading ---------------------------------------------------


def test_empty_bootstrap_approves_nothing() -> None:
    """A registry with no pinned hashes (Phase R bootstrap) denies everything —
    nothing is live until explicitly pinned via amendment+restart."""
    reg = _registry()
    for kind in ArtifactKind:
        assert reg.approved(kind) == frozenset()
        assert reg.is_approved(kind, _APPROVED_STRATEGY) is False


def test_missing_lockfile_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(LiveArtifactLockFileNotFoundError):
        LiveArtifactRegistry.from_lockfile(tmp_path / "nope.json")


def test_malformed_json_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "live_artifacts.lock.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(LiveArtifactLockFileMalformedError):
        LiveArtifactRegistry.from_lockfile(bad)


def test_malformed_hash_fails_closed(tmp_path: Path) -> None:
    """A non-SHA256 entry in the approved set is a corrupt config -> fail-closed."""
    bad = tmp_path / "live_artifacts.lock.json"
    bad.write_text(
        json.dumps({
            "version": "1.0",
            "updated_at": "2026-06-11T00:00:00+08:00",
            "approved": {"strategy_code": ["not-a-sha256"]},
        }),
        encoding="utf-8",
    )
    with pytest.raises(LiveArtifactLockFileMalformedError):
        LiveArtifactRegistry.from_lockfile(bad)


def test_unknown_kind_key_fails_closed(tmp_path: Path) -> None:
    bad = tmp_path / "live_artifacts.lock.json"
    bad.write_text(
        json.dumps({
            "version": "1.0",
            "updated_at": "2026-06-11T00:00:00+08:00",
            "approved": {"strategy_code": [], "rogue_kind": [_APPROVED_STRATEGY]},
        }),
        encoding="utf-8",
    )
    with pytest.raises(LiveArtifactLockFileMalformedError):
        LiveArtifactRegistry.from_lockfile(bad)


def test_from_lockfile_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "live_artifacts.lock.json"
    path.write_text(
        json.dumps({
            "version": "1.0",
            "updated_at": "2026-06-11T00:00:00+08:00",
            "approved": {
                "strategy_code": [_APPROVED_STRATEGY],
                "prompt_version": [_APPROVED_PROMPT],
            },
        }),
        encoding="utf-8",
    )
    reg = LiveArtifactRegistry.from_lockfile(path)
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, _APPROVED_STRATEGY) is True
    assert reg.is_approved(ArtifactKind.PROMPT_VERSION, _APPROVED_PROMPT) is True
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, _UNAPPROVED_STRATEGY) is False


def test_shipped_lockfile_loads_and_denies_unapproved() -> None:
    """The in-repo config/live_artifacts.lock.json must load and deny anything
    not explicitly pinned. Y-006 pins exactly one artifact — the theme-research
    SOP prompt version (PROMPT_VERSION) authorized by P0-8-amendment-2026-06-01;
    every other kind stays empty (deny-all) and an unapproved hash is refused."""
    reg = LiveArtifactRegistry.from_lockfile("config/live_artifacts.lock.json")
    # The attacker's hash + an unrelated strategy hash are denied across all kinds.
    for kind in ArtifactKind:
        assert reg.is_approved(kind, _APPROVED_STRATEGY) is False
        assert reg.is_approved(kind, _UNAPPROVED_STRATEGY) is False
    # Only the theme SOP prompt is pinned, and only under PROMPT_VERSION.
    theme_sop_sha = hashlib.sha256(
        Path("config/prompts/theme_research/v1.yaml").read_bytes()
    ).hexdigest()
    assert reg.is_approved(ArtifactKind.PROMPT_VERSION, theme_sop_sha) is True
    assert reg.is_approved(ArtifactKind.STRATEGY_CODE, theme_sop_sha) is False
    assert reg.approved(ArtifactKind.PROMPT_VERSION) == frozenset({theme_sop_sha})
    assert reg.approved(ArtifactKind.STRATEGY_CODE) == frozenset()


# -- module import isolation (R0 §8 / P2-2; no reverse calls into the stack) ----


def test_module_import_isolation() -> None:
    forbidden = {"api", "broker", "risk", "llm", "agents", "mirofish", "data"}
    root = pathlib.Path("backend/strategy_evolution")
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "backend."
            ):
                parts = (node.module or "").split(".")
                if len(parts) >= 2 and parts[1] in forbidden:
                    violations.append(f"{path}: from {node.module}")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    p = a.name.split(".")
                    if len(p) >= 2 and p[0] == "backend" and p[1] in forbidden:
                        violations.append(f"{path}: import {a.name}")
    assert violations == [], violations


def test_base_error_catches_subclasses() -> None:
    assert issubclass(LiveArtifactLockFileNotFoundError, LiveArtifactRegistryError)
    assert issubclass(LiveArtifactLockFileMalformedError, LiveArtifactRegistryError)
