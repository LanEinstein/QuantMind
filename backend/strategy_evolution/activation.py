"""Activation mechanics — manifest, next_boot.lock, boot apply/rollback
(AB-003 / P2-2-amendment-2026-06-12 §1.2; codex P0-4).

git is NOT a runtime control plane: activation is an append-only
:class:`PromotionIntent` + a content-addressed
:class:`ActivationManifest` + an atomic ``next_boot.lock.json`` swap +
a controlled 08:30 restart (external supervisor — see
``deploy/promote_restart.sh``) + boot-time hash/health assertions with
AUTOMATIC rollback to the previous lockfile. git only mirrors the
record after the fact (daily batch, by the human/ops lane); this module
contains ZERO git/subprocess calls — the AB-008 adversarial scan pins
that.

"config runtime-immutable + restart to take effect" is preserved — the
only thing the amendment changed is WHO approves (deterministic
judgement instead of a human), not HOW activation happens.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field

from backend.review.ops_gate import ACTIVATION_BLACKOUT, next_market_open
from backend.services.run_mode import feishu_interactive_enabled
from backend.strategy_evolution.evolvable_params import validate_param_set
from backend.strategy_evolution.live_artifact_registry import (
    ArtifactKind,
    LiveArtifactRegistry,
)
from backend.strategy_evolution.promotion_intent import (
    IntentStatus,
    PromotionModeError,
)

log = structlog.get_logger(component="strategy_evolution.activation")

LIVE_LOCK_NAME = "live_artifacts.lock.json"
PREV_LOCK_NAME = "live_artifacts.lock.prev.json"
NEXT_BOOT_LOCK_NAME = "next_boot.lock.json"


class ActivationWindowError(RuntimeError):
    """Activation attempted inside the 2h pre-open blackout (§1.4)."""


class ActivationManifest(BaseModel):
    """Content-addressed full target state of the live artifact pins.

    ``manifest_hash`` is derived from the TARGET STATE only (approved
    map + evolvable params) — identical target states share a hash
    regardless of when/why they were proposed, which makes the
    rollback chain (``previous_manifest_hash`` on the intent) and the
    AA-004 policy segmentation deterministic.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    intent_id: str = Field(min_length=1, max_length=64)
    approved: dict[str, tuple[str, ...]]
    params: dict[str, float] = Field(default_factory=dict)


def compute_manifest_hash(
    approved: dict[str, tuple[str, ...]],
    params: dict[str, float],
) -> str:
    payload = json.dumps(
        {
            "approved": {
                kind: sorted(hashes)
                for kind, hashes in sorted(approved.items())
            },
            "params": {
                name: round(value, 8)
                for name, value in sorted(params.items())
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_activation_manifest(
    *,
    current_approved: dict[str, tuple[str, ...]],
    add: dict[ArtifactKind, tuple[str, ...]] | None = None,
    remove: dict[ArtifactKind, tuple[str, ...]] | None = None,
    params: dict[str, float] | None = None,
    current_params: dict[str, float] | None = None,
    intent_id: str,
    created_at: datetime,
) -> ActivationManifest:
    """Build the target-state manifest (pure; whitelist-validated).

    Raises:
        FrozenParamViolationError / ValueError: a param change touches the
            frozen set or fails clamp/group validation (AB-005).
    """
    target: dict[str, tuple[str, ...]] = {
        kind.value: tuple(sorted(current_approved.get(kind.value, ())))
        for kind in ArtifactKind
    }
    for kind, hashes in (add or {}).items():
        merged = set(target[kind.value]) | set(hashes)
        target[kind.value] = tuple(sorted(merged))
    for kind, hashes in (remove or {}).items():
        remaining = set(target[kind.value]) - set(hashes)
        target[kind.value] = tuple(sorted(remaining))

    target_params = dict(params or {})
    if target_params:
        validation = validate_param_set(
            target_params, current=current_params
        )
        if not validation.passed:
            raise ValueError(
                f"manifest params failed whitelist validation: "
                f"{'; '.join(validation.violations)}"
            )

    return ActivationManifest(
        manifest_hash=compute_manifest_hash(target, target_params),
        created_at=created_at,
        intent_id=intent_id,
        approved=target,
        params=target_params,
    )


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def write_next_boot_lock(
    manifest: ActivationManifest,
    *,
    now: datetime,
    lock_dir: Path | str = "config",
) -> Path:
    """Atomically stage the manifest for the next controlled restart.

    Guards (both fail-closed):
    * mode — staging is simulation_auto-only (amendment §2);
    * activation blackout — no activation action within 2h of the next
      market open (§1.4; the 08:30 supervisor window sits 65min before
      the 09:35 runs but MUST itself satisfy this bound vs 09:30 open
      → staging the lock happens the evening before / early enough).
    """
    if feishu_interactive_enabled():
        raise PromotionModeError(
            "next_boot.lock staging is simulation_auto-only "
            "(P2-2-amendment-2026-06-12 §2)"
        )
    if manifest.params:
        # Codex AB P2 — there is no runtime consumption path for
        # whitelisted param values yet (the AB experiment harness gap):
        # staging one would report APPLIED while changing nothing.
        # Reject loudly until the param-application schema lands.
        raise ValueError(
            "param-bearing manifests cannot be staged yet: the runtime "
            "param-application path lands with the AB experiment "
            "harness — a silent no-op promotion is worse than a refusal"
        )
    next_open = next_market_open(now)
    if next_open is not None and next_open - now < ACTIVATION_BLACKOUT:
        raise ActivationWindowError(
            f"within the {ACTIVATION_BLACKOUT} pre-open blackout "
            f"(next open {next_open.isoformat()}); refuse to stage"
        )
    path = Path(lock_dir) / NEXT_BOOT_LOCK_NAME
    _atomic_write(path, manifest.model_dump_json(indent=2))
    log.info(
        "next_boot_lock_staged",
        manifest_hash=manifest.manifest_hash[:12],
        intent_id=manifest.intent_id,
    )
    return path


class ActivationStatus(StrEnum):
    NOOP = "noop"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    CORRUPT_STAGED_MANIFEST = "corrupt_staged_manifest"
    FROZEN_MODE_SWITCH = "frozen_mode_switch"
    """The mode flipped to feishu_interactive between staging and the
    restart — the staged manifest is quarantined untouched (codex AB
    P1): a sim-domain promotion must never mutate the live lockfile
    under the human-gate domain."""


class ActivationResult(BaseModel):
    """Outcome of one boot-time activation attempt."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    status: ActivationStatus
    manifest_hash: str | None = None
    intent_id: str | None = None
    detail: str = Field(default="", max_length=512)


def apply_pending_activation(
    *, lock_dir: Path | str = "config"
) -> ActivationResult:
    """Boot-time consume-once activation with automatic rollback.

    Steps (each fail-closed):
    1. no ``next_boot.lock.json`` → NOOP (the overwhelmingly common boot);
    2. parse + validate the staged manifest; corruption quarantines the
       file (renamed ``.bad``) and leaves the live lockfile UNTOUCHED;
    3. back up the live lockfile to ``live_artifacts.lock.prev.json``;
    4. atomically write the manifest's approved map as the new lockfile;
    5. health assertion — :class:`LiveArtifactRegistry` must load the
       new lockfile; a failure restores the backup bytes (automatic
       rollback) and reports ROLLED_BACK;
    6. the staged lock is removed in every non-NOOP path (consume-once
       — a crash loop must not re-apply forever).
    """
    directory = Path(lock_dir)
    staged_path = directory / NEXT_BOOT_LOCK_NAME
    if not staged_path.is_file():
        return ActivationResult(status=ActivationStatus.NOOP)

    # Codex AB P1 — re-check the mode AT APPLY TIME: a lock staged in
    # simulation mode must not activate if the owner flipped
    # FEISHU_INTERACTIVE_ENABLED before the controlled restart. The
    # staged manifest is quarantined (.frozen) for owner triage; the
    # live lockfile stays untouched.
    if feishu_interactive_enabled():
        frozen_path = staged_path.with_suffix(".frozen")
        os.replace(staged_path, frozen_path)
        log.warning(
            "next_boot_lock_frozen_mode_switch",
            quarantined=str(frozen_path),
        )
        return ActivationResult(
            status=ActivationStatus.FROZEN_MODE_SWITCH,
            detail=(
                "feishu_interactive enabled at boot; staged sim "
                "promotion quarantined for owner triage"
            ),
        )

    live_path = directory / LIVE_LOCK_NAME
    prev_path = directory / PREV_LOCK_NAME

    try:
        manifest = ActivationManifest.model_validate_json(
            staged_path.read_text(encoding="utf-8")
        )
        recomputed = compute_manifest_hash(
            manifest.approved, manifest.params
        )
        if recomputed != manifest.manifest_hash:
            raise ValueError(
                f"manifest hash mismatch: stored "
                f"{manifest.manifest_hash[:12]}, recomputed "
                f"{recomputed[:12]}"
            )
    except Exception as exc:  # noqa: BLE001 — quarantine, never apply
        bad_path = staged_path.with_suffix(".bad")
        os.replace(staged_path, bad_path)
        log.error(
            "next_boot_lock_corrupt",
            error=str(exc),
            quarantined=str(bad_path),
        )
        return ActivationResult(
            status=ActivationStatus.CORRUPT_STAGED_MANIFEST,
            detail=str(exc)[:512],
        )

    if manifest.params:
        # Defence in depth behind the staging refusal (codex AB P2):
        # a hand-crafted param-bearing staged lock must not report
        # APPLIED while silently dropping the params.
        bad_path = staged_path.with_suffix(".bad")
        os.replace(staged_path, bad_path)
        log.error(
            "next_boot_lock_params_unsupported",
            quarantined=str(bad_path),
        )
        return ActivationResult(
            status=ActivationStatus.CORRUPT_STAGED_MANIFEST,
            detail=(
                "param-bearing manifest: no runtime param-application "
                "path exists yet — refused (would be a silent no-op)"
            ),
        )

    previous_bytes = (
        live_path.read_bytes() if live_path.is_file() else None
    )
    if previous_bytes is not None:
        _atomic_write(
            prev_path, previous_bytes.decode("utf-8")
        )

    new_lock = {
        "version": "1.0",
        "updated_at": manifest.created_at.isoformat(),
        "approved": {
            kind: list(hashes)
            for kind, hashes in sorted(manifest.approved.items())
        },
    }
    _atomic_write(live_path, json.dumps(new_lock, indent=2))

    try:
        LiveArtifactRegistry.from_lockfile(live_path)
    except Exception as exc:  # noqa: BLE001 — automatic rollback
        if previous_bytes is not None:
            _atomic_write(live_path, previous_bytes.decode("utf-8"))
        staged_path.unlink(missing_ok=True)
        log.error(
            "activation_health_assert_failed_rolled_back",
            manifest_hash=manifest.manifest_hash[:12],
            error=str(exc),
        )
        return ActivationResult(
            status=ActivationStatus.ROLLED_BACK,
            manifest_hash=manifest.manifest_hash,
            intent_id=manifest.intent_id,
            detail=f"registry health assert failed: {exc}"[:512],
        )

    staged_path.unlink(missing_ok=True)
    log.info(
        "activation_applied",
        manifest_hash=manifest.manifest_hash[:12],
        intent_id=manifest.intent_id,
    )
    return ActivationResult(
        status=ActivationStatus.APPLIED,
        manifest_hash=manifest.manifest_hash,
        intent_id=manifest.intent_id,
    )


def intent_status_for_activation(
    status: ActivationStatus,
) -> IntentStatus | None:
    """Map a boot activation outcome to the intent's terminal status.

    Codex AB P2 — the intent is still PENDING at boot, so the only
    legal transitions are PENDING→{ACTIVATED, CANCELLED, FROZEN}:
    APPLIED → ACTIVATED; ROLLED_BACK / CORRUPT → CANCELLED (the staged
    activation is consumed and dead — a fresh decision must mint a new
    intent); FROZEN_MODE_SWITCH → FROZEN (owner triage). NOOP → None.
    """
    from backend.strategy_evolution.promotion_intent import IntentStatus

    return {
        ActivationStatus.APPLIED: IntentStatus.ACTIVATED,
        ActivationStatus.ROLLED_BACK: IntentStatus.CANCELLED,
        ActivationStatus.CORRUPT_STAGED_MANIFEST: IntentStatus.CANCELLED,
        ActivationStatus.FROZEN_MODE_SWITCH: IntentStatus.FROZEN,
        ActivationStatus.NOOP: None,
    }[status]


def rollback_to_previous(
    *, lock_dir: Path | str = "config"
) -> bool:
    """Restore the previous lockfile bytes (AB-004 demotion path).

    Returns False (no-op) when no backup exists or the backup itself
    fails the registry health assertion — a demotion must never swap
    in a corrupt lockfile.
    """
    directory = Path(lock_dir)
    prev_path = directory / PREV_LOCK_NAME
    live_path = directory / LIVE_LOCK_NAME
    if not prev_path.is_file():
        return False
    backup_bytes = prev_path.read_bytes()
    current_bytes = (
        live_path.read_bytes() if live_path.is_file() else None
    )
    _atomic_write(live_path, backup_bytes.decode("utf-8"))
    try:
        LiveArtifactRegistry.from_lockfile(live_path)
    except Exception as exc:  # noqa: BLE001 — restore what was there
        if current_bytes is not None:
            _atomic_write(live_path, current_bytes.decode("utf-8"))
        log.error("rollback_backup_corrupt", error=str(exc))
        return False
    log.warning("lockfile_rolled_back_to_previous")
    return True


__all__ = [
    "LIVE_LOCK_NAME",
    "NEXT_BOOT_LOCK_NAME",
    "PREV_LOCK_NAME",
    "ActivationManifest",
    "ActivationResult",
    "ActivationStatus",
    "ActivationWindowError",
    "apply_pending_activation",
    "build_activation_manifest",
    "intent_status_for_activation",
    "compute_manifest_hash",
    "rollback_to_previous",
    "write_next_boot_lock",
]
