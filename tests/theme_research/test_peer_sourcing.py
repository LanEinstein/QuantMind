"""Human-pin gate + peer-sourcing invariants (Y-004)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.theme_research.candidate_artifact import ThemeCandidateArtifact
from backend.theme_research.candidate_registry import (
    ThemeCandidateLockFile,
    ThemeCandidateLockFileMalformedError,
    ThemeCandidateLockFileNotFoundError,
    ThemeCandidateRegistry,
)
from backend.theme_research.peer_sourcing import verify_pinned_candidates
from backend.theme_research.sop_schema import (
    ChokePointFinding,
    SourceCitation,
    ThemeCandidate,
    ThemeResearchOutput,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_T = datetime(2026, 6, 11, 1, 0, tzinfo=UTC)
_HASH = "a" * 64


def _cite() -> SourceCitation:
    return SourceCitation(source_domain="www.gov.cn", snippet_sha256="b" * 64)


def _artifact(
    *, promotable: bool = True, code: str = "600519"
) -> ThemeCandidateArtifact:
    out = ThemeResearchOutput(
        trend_direction="t",
        beneficiary_sectors=("半导体",),
        chain_links=("光刻机",),
        chokepoints=(
            ChokePointFinding(
                chain_link="光刻机",
                rationale="难",
                confidence=0.8,
                citations=(_cite(),),
            ),
        ),
        candidates=(
            ThemeCandidate(
                code=code,
                sector="半导体",
                chain_link="光刻机",
                rationale="r",
                confidence=0.7,
                citations=(_cite(),),
            ),
        ),
        overall_confidence=0.6,
        trend_citations=(_cite(),),
    )
    return ThemeCandidateArtifact.from_output(
        run_id="run-1", prompt_version_hash=_HASH, output=out,
        source_promotable=promotable, created_at=_T,
    )


# -- registry (mirrors LiveArtifactRegistry pin discipline) -----------------


def test_shipped_lock_is_empty_bootstrap_deny_all() -> None:
    reg = ThemeCandidateRegistry.from_lockfile(
        REPO_ROOT / "config/theme_candidates.lock.json"
    )
    assert reg.approved == frozenset()
    assert reg.is_pinned("a" * 64) is False


def test_missing_lock_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ThemeCandidateLockFileNotFoundError):
        ThemeCandidateRegistry.from_lockfile(tmp_path / "missing.json")


def test_malformed_lock_fails_closed(tmp_path: Path) -> None:
    p = tmp_path / "lock.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ThemeCandidateLockFileMalformedError):
        ThemeCandidateRegistry.from_lockfile(p)


def test_non_hex_entry_rejected() -> None:
    with pytest.raises(ValidationError, match="hex SHA256"):
        ThemeCandidateLockFile(
            version="1.0", updated_at=_T, approved=("not-a-hash",)
        )


def test_registry_is_immutable() -> None:
    reg = ThemeCandidateRegistry({"a" * 64})
    with pytest.raises(AttributeError, match="immutable"):
        reg._approved = frozenset()  # type: ignore[attr-defined]


def test_pinned_hash_is_approved(tmp_path: Path) -> None:
    art = _artifact()
    lock = {
        "version": "1.0",
        "updated_at": "2026-06-11T00:00:00+08:00",
        "approved": [art.content_hash()],
    }
    p = tmp_path / "lock.json"
    p.write_text(json.dumps(lock), encoding="utf-8")
    reg = ThemeCandidateRegistry.from_lockfile(p)
    assert reg.is_pinned(art.content_hash()) is True


# -- peer-sourcing fail-closed ----------------------------------------------


def test_unpinned_artifact_yields_empty() -> None:
    art = _artifact()
    empty_reg = ThemeCandidateRegistry(())  # deny-all
    assert verify_pinned_candidates(art, empty_reg) == ()


def test_non_promotable_artifact_yields_empty() -> None:
    art = _artifact(promotable=False)
    reg = ThemeCandidateRegistry({art.content_hash()})  # even if pinned
    assert verify_pinned_candidates(art, reg) == ()


def test_promotable_and_pinned_yields_candidates() -> None:
    art = _artifact()
    reg = ThemeCandidateRegistry({art.content_hash()})
    out = verify_pinned_candidates(art, reg)
    assert len(out) == 1
    assert out[0].code == "600519"
