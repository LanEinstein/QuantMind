"""SOP prompt registry invariants (Y-006) — pin, checksum, frozen skeleton."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.strategy_evolution.live_artifact_registry import (
    ArtifactKind,
    LiveArtifactRegistry,
)
from backend.theme_research.prompts_loader import (
    ThemePromptChecksumMismatchError,
    ThemePromptFileNotFoundError,
    ThemePromptLockFile,
    ThemePromptLockFileMalformedError,
    ThemePromptLockFileNotFoundError,
    ThemePromptNotPinnedError,
    ThemePromptRegistry,
    ThemePromptSkeletonError,
    ThemePromptVersionEntry,
    validate_sop_skeleton,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK = "config/prompts/theme_research/prompts.lock.json"
_YAML_PATH = REPO_ROOT / "config/prompts/theme_research/v1.yaml"


def _v1_sha() -> str:
    return hashlib.sha256(_YAML_PATH.read_bytes()).hexdigest()


# -- the shipped registry loads + validates ---------------------------------


def test_real_lockfile_loads_and_serves() -> None:
    reg = ThemePromptRegistry.from_lockfile(
        REPO_ROOT / LOCK, repo_root=REPO_ROOT
    )
    assert reg.active_version == "v1"
    assert reg.active_sha256 == _v1_sha()
    assert "first_principles_reverse_deduction" in reg.active_prompt()


def test_real_yaml_skeleton_is_the_frozen_five() -> None:
    validate_sop_skeleton(_YAML_PATH.read_text(encoding="utf-8"))


# -- skeleton guard ----------------------------------------------------------


def test_skeleton_missing_step_rejected() -> None:
    bad = "steps:\n  direction: {}\n  sectors: {}\n  chain: {}\n  chokepoint: {}\n"
    with pytest.raises(ThemePromptSkeletonError, match="missing"):
        validate_sop_skeleton(bad)


def test_skeleton_extra_step_rejected() -> None:
    bad = (
        "steps:\n  direction: {}\n  sectors: {}\n  chain: {}\n"
        "  chokepoint: {}\n  tickers: {}\n  rogue: {}\n"
    )
    with pytest.raises(ThemePromptSkeletonError, match="extra"):
        validate_sop_skeleton(bad)


def test_skeleton_non_mapping_rejected() -> None:
    with pytest.raises(ThemePromptSkeletonError):
        validate_sop_skeleton("- just\n- a list\n")


def test_skeleton_wrong_order_rejected() -> None:
    """Right keys, wrong order — the reverse-deduction pipeline is inverted."""
    bad = (
        "steps:\n  sectors: {}\n  direction: {}\n  chain: {}\n"
        "  chokepoint: {}\n  tickers: {}\n"
    )
    with pytest.raises(ThemePromptSkeletonError, match="frozen order"):
        validate_sop_skeleton(bad)


# -- fail-closed loaders -----------------------------------------------------


def _write_lock(tmp_path: Path, yaml_body: str, *, sha: str | None = None) -> Path:
    yaml_dir = tmp_path / "config/prompts/theme_research"
    yaml_dir.mkdir(parents=True)
    (yaml_dir / "v1.yaml").write_text(yaml_body, encoding="utf-8")
    real_sha = hashlib.sha256(yaml_body.encode("utf-8")).hexdigest()
    lock = {
        "version": "1.0",
        "updated_at": "2026-06-11T00:00:00+08:00",
        "active_version": "v1",
        "versions": {
            "v1": {
                "path": "config/prompts/theme_research/v1.yaml",
                "sha256": sha or real_sha,
                "pinned_at": "2026-06-11T00:00:00+08:00",
                "pinned_by": "test",
            }
        },
    }
    lock_path = tmp_path / LOCK
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return lock_path


_GOOD_YAML = (
    "version: v1\nsteps:\n  direction: {}\n  sectors: {}\n  chain: {}\n"
    "  chokepoint: {}\n  tickers: {}\n"
)


def test_missing_lockfile_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ThemePromptLockFileNotFoundError):
        ThemePromptRegistry.from_lockfile(tmp_path / LOCK, repo_root=tmp_path)


def test_malformed_lockfile_fails_closed(tmp_path: Path) -> None:
    lock_path = tmp_path / LOCK
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ThemePromptLockFileMalformedError):
        ThemePromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)


def test_checksum_mismatch_fails_closed(tmp_path: Path) -> None:
    lock_path = _write_lock(tmp_path, _GOOD_YAML, sha="0" * 64)
    with pytest.raises(ThemePromptChecksumMismatchError):
        ThemePromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)


def test_missing_yaml_fails_closed(tmp_path: Path) -> None:
    lock_path = _write_lock(tmp_path, _GOOD_YAML)
    (tmp_path / "config/prompts/theme_research/v1.yaml").unlink()
    with pytest.raises(ThemePromptFileNotFoundError):
        ThemePromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)


def test_skeleton_violation_at_load_fails_closed(tmp_path: Path) -> None:
    bad_yaml = "version: v1\nsteps:\n  direction: {}\n"
    lock_path = _write_lock(tmp_path, bad_yaml)
    with pytest.raises(ThemePromptSkeletonError):
        ThemePromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)


def test_no_active_version_fails_closed(tmp_path: Path) -> None:
    yaml_dir = tmp_path / "config/prompts/theme_research"
    yaml_dir.mkdir(parents=True)
    (yaml_dir / "v1.yaml").write_text(_GOOD_YAML, encoding="utf-8")
    lock = {
        "version": "1.0",
        "updated_at": "2026-06-11T00:00:00+08:00",
        "active_version": "",
        "versions": {},
    }
    lock_path = tmp_path / LOCK
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(ThemePromptLockFileMalformedError):
        ThemePromptRegistry.from_lockfile(lock_path, repo_root=tmp_path)


# -- LiveArtifactRegistry pin -----------------------------------------------


def test_require_pinned_without_registry_fails_closed() -> None:
    with pytest.raises(ThemePromptNotPinnedError, match="no LiveArtifactRegistry"):
        ThemePromptRegistry.from_lockfile(
            REPO_ROOT / LOCK, repo_root=REPO_ROOT, require_pinned=True
        )


def test_require_pinned_unapproved_fails_closed() -> None:
    empty = LiveArtifactRegistry({ArtifactKind.PROMPT_VERSION: frozenset()})
    with pytest.raises(ThemePromptNotPinnedError, match="not approved"):
        ThemePromptRegistry.from_lockfile(
            REPO_ROOT / LOCK,
            repo_root=REPO_ROOT,
            registry=empty,
            require_pinned=True,
        )


def test_require_pinned_approved_ok() -> None:
    reg = LiveArtifactRegistry(
        {ArtifactKind.PROMPT_VERSION: frozenset({_v1_sha()})}
    )
    loaded = ThemePromptRegistry.from_lockfile(
        REPO_ROOT / LOCK,
        repo_root=REPO_ROOT,
        registry=reg,
        require_pinned=True,
    )
    assert loaded.active_sha256 == _v1_sha()


def test_shipped_locks_are_internally_consistent() -> None:
    """End-to-end: the shipped live_artifacts.lock.json pins the same v1 SOP hash
    that prompts.lock.json points at, so require_pinned=True loads (codex Y P2)."""
    reg = LiveArtifactRegistry.from_lockfile(
        str(REPO_ROOT / "config/live_artifacts.lock.json")
    )
    loaded = ThemePromptRegistry.from_lockfile(
        REPO_ROOT / LOCK,
        repo_root=REPO_ROOT,
        registry=reg,
        require_pinned=True,
    )
    assert loaded.active_sha256 == _v1_sha()


def test_wrong_kind_does_not_approve_prompt() -> None:
    """Kind-typed: a STRATEGY_CODE hash never admits the prompt (cross-kind)."""
    reg = LiveArtifactRegistry(
        {ArtifactKind.STRATEGY_CODE: frozenset({_v1_sha()})}
    )
    with pytest.raises(ThemePromptNotPinnedError):
        ThemePromptRegistry.from_lockfile(
            REPO_ROOT / LOCK,
            repo_root=REPO_ROOT,
            registry=reg,
            require_pinned=True,
        )


# -- immutability ------------------------------------------------------------


def test_registry_is_immutable() -> None:
    reg = ThemePromptRegistry.from_lockfile(REPO_ROOT / LOCK, repo_root=REPO_ROOT)
    with pytest.raises(AttributeError, match="immutable"):
        reg._content = "tampered"  # type: ignore[attr-defined]


# -- path-traversal containment (prompt-injection surface) ------------------


_PIN_AT = datetime(2026, 6, 11, tzinfo=UTC)


def _entry(path: str) -> ThemePromptVersionEntry:
    return ThemePromptVersionEntry(
        path=path,
        sha256="a" * 64,
        pinned_at=_PIN_AT,
        pinned_by="test",
    )


def test_path_outside_subtree_rejected() -> None:
    with pytest.raises(ValidationError, match="config/prompts/theme_research/"):
        _entry("config/prompts/other/v1.yaml")


def test_path_traversal_dotdot_rejected() -> None:
    with pytest.raises(ValidationError, match=r"\.\."):
        _entry("config/prompts/theme_research/../../../etc/passwd.yaml")


def test_path_absolute_rejected() -> None:
    # An absolute path fails the subtree check first (it does not start with
    # the relative prefix) — still rejected, fail-closed.
    with pytest.raises(ValidationError, match="config/prompts/theme_research/"):
        _entry("/config/prompts/theme_research/v1.yaml")


def test_path_backslash_rejected() -> None:
    with pytest.raises(ValidationError, match="relative"):
        _entry("config/prompts/theme_research/sub\\v1.yaml")


def test_path_non_yaml_rejected() -> None:
    with pytest.raises(ValidationError, match=".yaml"):
        _entry("config/prompts/theme_research/v1.txt")


def test_bad_sha_in_entry_rejected() -> None:
    with pytest.raises(ValidationError, match="64-char"):
        ThemePromptVersionEntry(
            path="config/prompts/theme_research/v1.yaml",
            sha256="z" * 64,  # right length, not hex
            pinned_at=_PIN_AT,
            pinned_by="test",
        )


def test_active_version_not_in_versions_rejected() -> None:
    with pytest.raises(ValidationError, match="not present"):
        ThemePromptLockFile(
            version="1.0",
            updated_at=_PIN_AT,
            active_version="v9",
            versions={"v1": _entry("config/prompts/theme_research/v1.yaml")},
        )


def test_versions_without_active_rejected() -> None:
    with pytest.raises(ValidationError, match="active_version is empty"):
        ThemePromptLockFile(
            version="1.0",
            updated_at=_PIN_AT,
            active_version="",
            versions={"v1": _entry("config/prompts/theme_research/v1.yaml")},
        )
