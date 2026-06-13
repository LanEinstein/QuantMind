"""T-001 — trader persona-card registry: frozen, pinned, fail-closed.

Covers the immutable identity skeleton, SHA256 pin verification, the
LiveArtifactRegistry PROMPT_VERSION gate, the ≥2-trader coverage rule, and the
invariant that the persona cards are ADDITIVE — they never reduce the four
mandatory agents (P0-10-amendment-2026-05-24 §2.3 red line 3).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from backend.agents_team.persona_registry import (
    MANDATORY_PERSONA_DISJOINT_ERROR,
    MAX_PERSONA_EXEMPLARS,
    MIN_TRADER_PERSONAS,
    TraderPersona,
    TraderPersonaChecksumMismatchError,
    TraderPersonaCoverageError,
    TraderPersonaFileNotFoundError,
    TraderPersonaLockFileMalformedError,
    TraderPersonaLockFileNotFoundError,
    TraderPersonaNotPinnedError,
    TraderPersonaRegistry,
    TraderPersonaSkeletonError,
    compute_persona_sha256,
    validate_persona_skeleton,
)
from backend.agents_team.state import MANDATORY_AGENTS
from backend.strategy_evolution.live_artifact_registry import (
    ArtifactKind,
    LiveArtifactLockFile,
    LiveArtifactRegistry,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCK = _REPO_ROOT / "config" / "prompts" / "traders.lock.json"
_LIVE_LOCK = _REPO_ROOT / "config" / "live_artifacts.lock.json"

_GOOD_CARD = """\
version: v1
persona_id: trader_x
identity: |
  你是一个测试交易员。
mandate: |
  给 fund_manager 建议文本。
output_contract: |
  输出中文自由文本; 严禁 side/volume/price。
exemplars: []
"""


def _write_card(tmp: Path, persona_id: str, body: str) -> tuple[Path, str]:
    """Write card under tmp/config/prompts/{persona_id}/v1.yaml; return (root, sha)."""
    rel = Path("config") / "prompts" / persona_id / "v1.yaml"
    path = tmp / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return tmp, compute_persona_sha256(body.encode("utf-8"))


def _lockfile(tmp: Path, personas: dict[str, tuple[str, str]]) -> Path:
    """personas: persona_id -> (rel_path, sha). Write a traders.lock.json."""
    lock = {
        "version": "1.0",
        "updated_at": "2026-06-13T00:00:00+08:00",
        "personas": {
            pid: {
                "active_version": "v1",
                "versions": {
                    "v1": {
                        "path": rel,
                        "sha256": sha,
                        "pinned_at": "2026-06-13T00:00:00+08:00",
                        "pinned_by": "test",
                    }
                },
            }
            for pid, (rel, sha) in personas.items()
        },
    }
    p = tmp / "lock.json"
    p.write_text(json.dumps(lock), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Real shipped cards
# ---------------------------------------------------------------------------


class TestShippedCards:
    @pytest.mark.unit
    def test_loads_both_trader_personas(self) -> None:
        reg = TraderPersonaRegistry.from_lockfile(_LOCK, repo_root=_REPO_ROOT)
        assert reg.persona_ids() == ("trader_mean_reversion", "trader_momentum")
        for p in reg.personas():
            assert isinstance(p, TraderPersona)
            assert p.identity.strip()
            assert p.mandate.strip()
            assert p.output_contract.strip()
            # The frozen skeleton bakes the safety boundary into the card.
            assert "fund_manager" in p.mandate
            assert "volume" in p.output_contract

    @pytest.mark.unit
    def test_shipped_cards_are_pinned_in_live_registry(self) -> None:
        """require_pinned=True against the real lock → both cards approved."""
        registry = LiveArtifactRegistry.from_lockfile(_LIVE_LOCK)
        reg = TraderPersonaRegistry.from_lockfile(
            _LOCK,
            repo_root=_REPO_ROOT,
            registry=registry,
            require_pinned=True,
            require_full_coverage=True,
        )
        assert len(reg.personas()) >= MIN_TRADER_PERSONAS

    @pytest.mark.unit
    def test_personas_are_additive_not_mandatory(self) -> None:
        """The 4 mandatory agents are untouched; traders are a disjoint set."""
        reg = TraderPersonaRegistry.from_lockfile(_LOCK, repo_root=_REPO_ROOT)
        assert set(MANDATORY_AGENTS) == {
            "fundamental_analyst",
            "technical_analyst",
            "risk_officer",
            "fund_manager",
        }
        assert not (set(reg.persona_ids()) & set(MANDATORY_AGENTS)), (
            MANDATORY_PERSONA_DISJOINT_ERROR
        )


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutable:
    @pytest.mark.unit
    def test_registry_setattr_raises(self) -> None:
        reg = TraderPersonaRegistry.from_lockfile(_LOCK, repo_root=_REPO_ROOT)
        with pytest.raises(AttributeError):
            reg._personas = {}  # type: ignore[attr-defined]

    @pytest.mark.unit
    def test_persona_is_frozen(self) -> None:
        reg = TraderPersonaRegistry.from_lockfile(_LOCK, repo_root=_REPO_ROOT)
        p = reg.personas()[0]
        with pytest.raises((TypeError, ValueError)):
            p.identity = "mutated"  # type: ignore[misc]

    @pytest.mark.unit
    def test_internal_map_is_read_only(self) -> None:
        """codex T-001 P2: a leaked ``_personas`` reference cannot be mutated."""
        reg = TraderPersonaRegistry.from_lockfile(_LOCK, repo_root=_REPO_ROOT)
        with pytest.raises((AttributeError, TypeError)):
            reg._personas.clear()  # type: ignore[attr-defined]
        with pytest.raises((AttributeError, TypeError)):
            reg._personas["x"] = None  # type: ignore[index,attr-defined]


# ---------------------------------------------------------------------------
# Fail-closed boot
# ---------------------------------------------------------------------------


class TestFailClosed:
    @pytest.mark.unit
    def test_missing_lockfile(self, tmp_path: Path) -> None:
        with pytest.raises(TraderPersonaLockFileNotFoundError):
            TraderPersonaRegistry.from_lockfile(tmp_path / "nope.json")

    @pytest.mark.unit
    def test_malformed_lockfile(self, tmp_path: Path) -> None:
        p = tmp_path / "lock.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(TraderPersonaLockFileMalformedError):
            TraderPersonaRegistry.from_lockfile(p)

    @pytest.mark.unit
    def test_checksum_mismatch(self, tmp_path: Path) -> None:
        root, _sha = _write_card(tmp_path, "trader_x", _GOOD_CARD)
        bad = "0" * 64
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", bad)}
        )
        with pytest.raises(TraderPersonaChecksumMismatchError):
            TraderPersonaRegistry.from_lockfile(lock, repo_root=root)

    @pytest.mark.unit
    def test_missing_card_file(self, tmp_path: Path) -> None:
        sha = compute_persona_sha256(_GOOD_CARD.encode("utf-8"))
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        with pytest.raises(TraderPersonaFileNotFoundError):
            TraderPersonaRegistry.from_lockfile(lock, repo_root=tmp_path)

    @pytest.mark.unit
    def test_skeleton_missing_key(self, tmp_path: Path) -> None:
        body = "version: v1\npersona_id: trader_x\nidentity: hi\nmandate: hi\n"
        root, sha = _write_card(tmp_path, "trader_x", body)
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        with pytest.raises(TraderPersonaSkeletonError):
            TraderPersonaRegistry.from_lockfile(lock, repo_root=root)

    @pytest.mark.unit
    def test_persona_id_mismatch(self, tmp_path: Path) -> None:
        body = _GOOD_CARD.replace("persona_id: trader_x", "persona_id: trader_other")
        root, sha = _write_card(tmp_path, "trader_x", body)
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        with pytest.raises(TraderPersonaSkeletonError):
            TraderPersonaRegistry.from_lockfile(lock, repo_root=root)

    @pytest.mark.unit
    def test_too_many_exemplars(self, tmp_path: Path) -> None:
        ex = "\n".join(f"  - 案例{i}" for i in range(MAX_PERSONA_EXEMPLARS + 1))
        body = _GOOD_CARD.replace("exemplars: []", f"exemplars:\n{ex}")
        root, sha = _write_card(tmp_path, "trader_x", body)
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        with pytest.raises(TraderPersonaSkeletonError):
            TraderPersonaRegistry.from_lockfile(lock, repo_root=root)

    @pytest.mark.unit
    def test_mandatory_agent_id_rejected(self, tmp_path: Path) -> None:
        """codex T-001 P2: a persona id colliding with a mandatory agent fails."""
        sha = compute_persona_sha256(_GOOD_CARD.encode("utf-8"))
        lock = _lockfile(
            tmp_path,
            {"fund_manager": ("config/prompts/fund_manager/v1.yaml", sha)},
        )
        with pytest.raises(TraderPersonaLockFileMalformedError):
            TraderPersonaRegistry.from_lockfile(lock, repo_root=tmp_path)

    @pytest.mark.unit
    def test_path_directory_mismatch_fails_closed(self, tmp_path: Path) -> None:
        """codex T-001 P2: a lock path not matching {persona}/{version} fails."""
        root, sha = _write_card(tmp_path, "trader_x", _GOOD_CARD)
        # Pin active v1 but point the path at a v2.yaml directory entry.
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v2.yaml", sha)}
        )
        with pytest.raises(TraderPersonaLockFileMalformedError):
            TraderPersonaRegistry.from_lockfile(lock, repo_root=root)

    @pytest.mark.unit
    def test_card_version_mismatch_fails_closed(self, tmp_path: Path) -> None:
        """codex T-001 P2: card body version must equal the pinned active version."""
        body = _GOOD_CARD.replace("version: v1", "version: v2")
        root, sha = _write_card(tmp_path, "trader_x", body)
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        with pytest.raises(TraderPersonaSkeletonError):
            TraderPersonaRegistry.from_lockfile(lock, repo_root=root)


# ---------------------------------------------------------------------------
# LiveArtifactRegistry pin
# ---------------------------------------------------------------------------


def _empty_registry() -> LiveArtifactRegistry:
    return LiveArtifactRegistry.from_lock(
        LiveArtifactLockFile(version="1.0", updated_at=datetime(2026, 6, 13))
    )


class TestPin:
    @pytest.mark.unit
    def test_require_pinned_without_registry(self, tmp_path: Path) -> None:
        root, sha = _write_card(tmp_path, "trader_x", _GOOD_CARD)
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        with pytest.raises(TraderPersonaNotPinnedError):
            TraderPersonaRegistry.from_lockfile(
                lock, repo_root=root, require_pinned=True
            )

    @pytest.mark.unit
    def test_require_pinned_unapproved_hash(self, tmp_path: Path) -> None:
        root, sha = _write_card(tmp_path, "trader_x", _GOOD_CARD)
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        with pytest.raises(TraderPersonaNotPinnedError):
            TraderPersonaRegistry.from_lockfile(
                lock,
                repo_root=root,
                registry=_empty_registry(),
                require_pinned=True,
            )

    @pytest.mark.unit
    def test_require_pinned_approved_hash(self, tmp_path: Path) -> None:
        root, sha = _write_card(tmp_path, "trader_x", _GOOD_CARD)
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        registry = LiveArtifactRegistry.from_lock(
            LiveArtifactLockFile.model_validate_json(
                json.dumps(
                    {
                        "version": "1.0",
                        "updated_at": "2026-06-13T00:00:00+08:00",
                        "approved": {"prompt_version": [sha]},
                    }
                )
            )
        )
        reg = TraderPersonaRegistry.from_lockfile(
            lock, repo_root=root, registry=registry, require_pinned=True
        )
        assert reg.persona_ids() == ("trader_x",)
        assert registry.is_approved(ArtifactKind.PROMPT_VERSION, sha)


# ---------------------------------------------------------------------------
# Coverage rule (≥2 traders)
# ---------------------------------------------------------------------------


class TestCoverage:
    @pytest.mark.unit
    def test_require_full_coverage_one_persona(self, tmp_path: Path) -> None:
        root, sha = _write_card(tmp_path, "trader_x", _GOOD_CARD)
        lock = _lockfile(
            tmp_path, {"trader_x": ("config/prompts/trader_x/v1.yaml", sha)}
        )
        with pytest.raises(TraderPersonaCoverageError):
            TraderPersonaRegistry.from_lockfile(
                lock, repo_root=root, require_full_coverage=True
            )

    @pytest.mark.unit
    def test_empty_bootstrap_is_permitted(self, tmp_path: Path) -> None:
        lock = _lockfile(tmp_path, {})
        reg = TraderPersonaRegistry.from_lockfile(lock, repo_root=tmp_path)
        assert reg.persona_ids() == ()


class TestSkeletonHelper:
    @pytest.mark.unit
    def test_validate_returns_parsed_doc(self) -> None:
        doc = validate_persona_skeleton(_GOOD_CARD, expected_persona_id="trader_x")
        assert doc["persona_id"] == "trader_x"

    @pytest.mark.unit
    def test_non_mapping_root(self) -> None:
        with pytest.raises(TraderPersonaSkeletonError):
            validate_persona_skeleton("- a\n- b", expected_persona_id="trader_x")
