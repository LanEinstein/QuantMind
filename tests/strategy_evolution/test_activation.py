"""AB-003 activation mechanics tests (atomic swap / rollback / windows)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from backend.strategy_evolution.activation import (
    LIVE_LOCK_NAME,
    NEXT_BOOT_LOCK_NAME,
    PREV_LOCK_NAME,
    ActivationStatus,
    ActivationWindowError,
    apply_pending_activation,
    build_activation_manifest,
    compute_manifest_hash,
    rollback_to_previous,
    write_next_boot_lock,
)
from backend.strategy_evolution.live_artifact_registry import ArtifactKind
from backend.strategy_evolution.promotion_intent import PromotionModeError

# Saturday 20:00 Shanghai — far outside the pre-open blackout.
STAGE_TIME = dt.datetime(
    2026, 6, 13, 20, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))
)
NEW_HASH = "b" * 64


@pytest.fixture(autouse=True)
def _pure_sim_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "false")


def _seed_live_lock(lock_dir: Path) -> dict:
    lock = {
        "version": "1.0",
        "updated_at": "2026-06-11T00:00:00+08:00",
        "approved": {
            "strategy_code": [],
            "feature_def": [],
            "prompt_version": ["a" * 64],
            "anomaly_model": [],
            "rag_index": [],
        },
    }
    (lock_dir / LIVE_LOCK_NAME).write_text(json.dumps(lock, indent=2))
    return lock


def _manifest(lock: dict, **overrides):
    current = {
        kind: tuple(hashes) for kind, hashes in lock["approved"].items()
    }
    kwargs = {
        "current_approved": current,
        "add": {ArtifactKind.STRATEGY_CODE: (NEW_HASH,)},
        "intent_id": "intent-1",
        "created_at": STAGE_TIME,
    }
    kwargs.update(overrides)
    return build_activation_manifest(**kwargs)


class TestManifest:
    def test_content_addressed_over_target_state(self) -> None:
        a = compute_manifest_hash({"strategy_code": ("b" * 64,)}, {})
        b = compute_manifest_hash({"strategy_code": ("b" * 64,)}, {})
        assert a == b
        c = compute_manifest_hash(
            {"strategy_code": ("c" * 64,)}, {}
        )
        assert a != c

    def test_build_merges_add_and_remove(self, tmp_path: Path) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(
            lock,
            remove={ArtifactKind.PROMPT_VERSION: ("a" * 64,)},
        )
        assert NEW_HASH in manifest.approved["strategy_code"]
        assert manifest.approved["prompt_version"] == ()

    def test_param_whitelist_enforced_at_build(
        self, tmp_path: Path
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        with pytest.raises(ValueError, match="whitelist"):
            _manifest(lock, params={"line2.atr_stop_mult": 99.0})


class TestStaging:
    def test_stage_writes_atomic_lock(self, tmp_path: Path) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(lock)
        path = write_next_boot_lock(
            manifest, now=STAGE_TIME, lock_dir=tmp_path
        )
        assert path.name == NEXT_BOOT_LOCK_NAME
        revived = json.loads(path.read_text())
        assert revived["manifest_hash"] == manifest.manifest_hash

    def test_blackout_window_refuses_staging(
        self, tmp_path: Path
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(lock)
        # Monday 08:30 Shanghai — 60min before the 09:30 open (<2h).
        near_open = dt.datetime(
            2026, 6, 15, 8, 30,
            tzinfo=dt.timezone(dt.timedelta(hours=8)),
        )
        with pytest.raises(ActivationWindowError):
            write_next_boot_lock(
                manifest, now=near_open, lock_dir=tmp_path
            )

    def test_feishu_mode_refuses_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(lock)
        monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "true")
        with pytest.raises(PromotionModeError):
            write_next_boot_lock(
                manifest, now=STAGE_TIME, lock_dir=tmp_path
            )


class TestBootApply:
    def test_noop_without_staged_lock(self, tmp_path: Path) -> None:
        _seed_live_lock(tmp_path)
        result = apply_pending_activation(lock_dir=tmp_path)
        assert result.status is ActivationStatus.NOOP

    def test_apply_swaps_lockfile_and_backs_up(
        self, tmp_path: Path
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(lock)
        write_next_boot_lock(manifest, now=STAGE_TIME, lock_dir=tmp_path)

        result = apply_pending_activation(lock_dir=tmp_path)
        assert result.status is ActivationStatus.APPLIED
        assert result.manifest_hash == manifest.manifest_hash
        # New pin live; backup holds the previous bytes; staged lock gone.
        live = json.loads((tmp_path / LIVE_LOCK_NAME).read_text())
        assert NEW_HASH in live["approved"]["strategy_code"]
        prev = json.loads((tmp_path / PREV_LOCK_NAME).read_text())
        assert prev["approved"]["strategy_code"] == []
        assert not (tmp_path / NEXT_BOOT_LOCK_NAME).exists()

    def test_corrupt_staged_manifest_quarantined(
        self, tmp_path: Path
    ) -> None:
        _seed_live_lock(tmp_path)
        (tmp_path / NEXT_BOOT_LOCK_NAME).write_text("{not json")
        result = apply_pending_activation(lock_dir=tmp_path)
        assert (
            result.status is ActivationStatus.CORRUPT_STAGED_MANIFEST
        )
        # Live lockfile untouched; staged quarantined as .bad.
        live = json.loads((tmp_path / LIVE_LOCK_NAME).read_text())
        assert live["approved"]["prompt_version"] == ["a" * 64]
        assert not (tmp_path / NEXT_BOOT_LOCK_NAME).exists()
        assert (tmp_path / "next_boot.lock.bad").exists()

    def test_tampered_manifest_hash_quarantined(
        self, tmp_path: Path
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(lock)
        doc = json.loads(manifest.model_dump_json())
        doc["approved"]["strategy_code"] = ["e" * 64]  # tamper
        (tmp_path / NEXT_BOOT_LOCK_NAME).write_text(json.dumps(doc))
        result = apply_pending_activation(lock_dir=tmp_path)
        assert (
            result.status is ActivationStatus.CORRUPT_STAGED_MANIFEST
        )
        assert "mismatch" in result.detail

    def test_health_assert_failure_rolls_back(
        self, tmp_path: Path
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        # A manifest whose target state the registry must reject:
        # an invalid (non-sha256) pin smuggled past the model by
        # constructing the staged file directly.
        manifest = _manifest(lock)
        doc = json.loads(manifest.model_dump_json())
        doc["approved"]["strategy_code"] = ["not-a-hash"]
        from backend.strategy_evolution.activation import (
            compute_manifest_hash as cmh,
        )

        doc["manifest_hash"] = cmh(
            {k: tuple(v) for k, v in doc["approved"].items()},
            doc["params"],
        )
        (tmp_path / NEXT_BOOT_LOCK_NAME).write_text(json.dumps(doc))

        result = apply_pending_activation(lock_dir=tmp_path)
        assert result.status is ActivationStatus.ROLLED_BACK
        # Automatic rollback restored the previous pins.
        live = json.loads((tmp_path / LIVE_LOCK_NAME).read_text())
        assert live["approved"]["prompt_version"] == ["a" * 64]
        assert not (tmp_path / NEXT_BOOT_LOCK_NAME).exists()


class TestRollbackToPrevious:
    def test_demotion_rollback_restores_backup(
        self, tmp_path: Path
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(lock)
        write_next_boot_lock(manifest, now=STAGE_TIME, lock_dir=tmp_path)
        assert (
            apply_pending_activation(lock_dir=tmp_path).status
            is ActivationStatus.APPLIED
        )
        assert rollback_to_previous(lock_dir=tmp_path) is True
        live = json.loads((tmp_path / LIVE_LOCK_NAME).read_text())
        assert live["approved"]["strategy_code"] == []

    def test_rollback_without_backup_is_noop(
        self, tmp_path: Path
    ) -> None:
        _seed_live_lock(tmp_path)
        assert rollback_to_previous(lock_dir=tmp_path) is False


class TestCodexABFixes:
    """Codex Phase-AB regressions: boot mode re-check, param refusal,
    intent terminal-status mapping."""

    def test_mode_flip_at_boot_freezes_staged_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(lock)
        write_next_boot_lock(manifest, now=STAGE_TIME, lock_dir=tmp_path)
        monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "true")
        result = apply_pending_activation(lock_dir=tmp_path)
        assert result.status is ActivationStatus.FROZEN_MODE_SWITCH
        # Live lockfile untouched; staged quarantined as .frozen.
        live = json.loads((tmp_path / LIVE_LOCK_NAME).read_text())
        assert live["approved"]["strategy_code"] == []
        assert not (tmp_path / NEXT_BOOT_LOCK_NAME).exists()
        assert (tmp_path / "next_boot.lock.frozen").exists()

    def test_param_bearing_manifest_refused_at_staging(
        self, tmp_path: Path
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(
            lock, params={"line2.r_multiple": 1.2}
        )
        with pytest.raises(ValueError, match="param-bearing"):
            write_next_boot_lock(
                manifest, now=STAGE_TIME, lock_dir=tmp_path
            )

    def test_handcrafted_param_manifest_refused_at_apply(
        self, tmp_path: Path
    ) -> None:
        lock = _seed_live_lock(tmp_path)
        manifest = _manifest(lock, params={"line2.r_multiple": 1.2})
        (tmp_path / NEXT_BOOT_LOCK_NAME).write_text(
            manifest.model_dump_json()
        )
        result = apply_pending_activation(lock_dir=tmp_path)
        assert (
            result.status is ActivationStatus.CORRUPT_STAGED_MANIFEST
        )
        assert "param" in result.detail
        live = json.loads((tmp_path / LIVE_LOCK_NAME).read_text())
        assert live["approved"]["strategy_code"] == []

    def test_intent_status_mapping_respects_allowlist(self) -> None:
        from backend.strategy_evolution.activation import (
            intent_status_for_activation,
        )
        from backend.strategy_evolution.promotion_intent import (
            ALLOWED_INTENT_TRANSITIONS,
            IntentStatus,
        )

        for status in ActivationStatus:
            terminal = intent_status_for_activation(status)
            if terminal is None:
                assert status is ActivationStatus.NOOP
                continue
            # Every mapped terminal must be reachable from PENDING —
            # the boot-time intent state (codex AB P2).
            assert (
                IntentStatus.PENDING,
                terminal,
            ) in ALLOWED_INTENT_TRANSITIONS
