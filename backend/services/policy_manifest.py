"""Policy manifest hash + segment ledger (AA-004).

P2-2-amendment-2026-06-12 §1.6 (codex P0-1): performance / acceptance /
equity metrics must be segmented by the ACTIVE POLICY so an (AB-era)
promotion never produces an unreproducible "half-old-half-new" readiness
curve. ``policy_hash`` is a deterministic SHA256 over everything that
defines trading behaviour at boot:

* the policy-bearing config files (risk / universe / agent routing /
  broker economics / slot rotation / allocation / live-artifact pins);
* the version constants of the deterministic code stacks (screener
  features + Line-2 sell-stack triggers);
* the activation env flags that gate optional trigger behaviour.

Until Phase AB lands the activation-manifest pipeline, this boot-time
derivation IS the manifest source; AB replaces the input set with the
content-addressed activation manifest without changing consumers.

Segments are append-only rows in ``policy_segments``: one row per
distinct consecutive hash, written at boot when the hash differs from
the latest recorded one — the transition-point ledger the frontend +
acceptance read (§1.6 切换点留痕). Rows are never rewritten.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger(component="services.policy_manifest")

POLICY_CONFIG_FILES: tuple[str, ...] = (
    "config/agent_models.yaml",
    "config/allocation_policy.yaml",
    "config/broker.yaml",
    "config/live_artifacts.lock.json",
    "config/risk.yaml",
    "config/slot_rotation_policy.yaml",
    "config/universe_policy.yaml",
)
"""Policy-bearing config files folded into the hash (sorted)."""

POLICY_ENV_FLAGS: tuple[str, ...] = (
    "QUANTMIND_LINE2_ADAPTIVE_DRAWDOWN_ENABLED",
    "QUANTMIND_LINE2_ENTRY_ANCHORED_STOP_ENABLED",
    "QUANTMIND_LINE2_REENTRY_ENABLED",
    "QUANTMIND_LINE2_REGIME_DRAWDOWN_ENABLED",
    "QUANTMIND_LINE2_REGIME_TAKEPROFIT_ENABLED",
    "QUANTMIND_LINE2_SELL_INTO_STRENGTH_ENABLED",
    "QUANTMIND_LINE2_STALE_EXIT_ENABLED",
    "QUANTMIND_LINE2_THESIS_TAKEPROFIT_EXEMPT_ENABLED",
    "QUANTMIND_LINE2_TIERED_TAKEPROFIT_ENABLED",
    "QUANTMIND_THESIS_QUANT_BREAK_ENABLED",
)
"""Activation flags that change trigger behaviour without a config
diff. Normalised to the canonical truthy set before hashing."""

_TRUTHY = frozenset({"true", "1", "yes", "on"})


class PolicySegmentRecord(BaseModel):
    """One append-only row in the ``policy_segments`` collection."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    segment_id: UUID = Field(default_factory=uuid4)
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    components: dict[str, str] = Field(default_factory=dict)
    """Per-component digests/versions for diff-ability — NOT part of
    the hash input contract (the hash is recomputed from sources)."""


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        # A missing policy file IS a policy difference — encode the
        # absence deterministically instead of crashing the boot.
        return "absent"


def _code_versions() -> dict[str, str]:
    """Version constants of the deterministic code stacks (lazy import
    so this module never holds hard references to the trading stack)."""
    versions: dict[str, str] = {}
    try:
        from backend.monitoring.intraday_triggers import (
            FEATURE_CODE_VERSION as TRIGGERS_VERSION,
        )

        versions["sell_stack"] = str(TRIGGERS_VERSION)
    except Exception:  # noqa: BLE001 — absence is itself encoded
        versions["sell_stack"] = "absent"
    try:
        from backend.screening.screener import (
            FEATURE_CODE_VERSION as SCREENER_VERSION,
        )

        versions["screener"] = str(SCREENER_VERSION)
    except Exception:  # noqa: BLE001
        versions["screener"] = "absent"
    return versions


def build_policy_components(
    *, repo_root: Path | str = "."
) -> dict[str, str]:
    """Deterministic component map: file digests + versions + flags."""
    root = Path(repo_root)
    components: dict[str, str] = {
        f"file:{rel}": _file_sha256(root / rel)
        for rel in POLICY_CONFIG_FILES
    }
    for name, version in _code_versions().items():
        components[f"code:{name}"] = version
    for flag in POLICY_ENV_FLAGS:
        raw = os.environ.get(flag, "").strip().lower()
        components[f"env:{flag}"] = "on" if raw in _TRUTHY else "off"
    return components


def compute_policy_hash(*, repo_root: Path | str = ".") -> str:
    """SHA256 hex over the canonical JSON of the component map."""
    payload = json.dumps(
        build_policy_components(repo_root=repo_root),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def current_sell_stack_version() -> str | None:
    """The Line-2 trigger stack version for the position nameplate."""
    versions = _code_versions()
    value = versions.get("sell_stack")
    return None if value in (None, "absent") else value


# ---------------------------------------------------------------------------
# Segment ledger
# ---------------------------------------------------------------------------


class _MotorDatabase(Protocol):
    def __getitem__(self, name: str) -> Any: ...


class MongoPolicySegmentStore:
    """Append-only adapter over the ``policy_segments`` collection."""

    COLLECTION = "policy_segments"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def latest(self) -> PolicySegmentRecord | None:
        cursor = (
            self._db[self.COLLECTION]
            .find({})
            .sort("started_at", -1)
            .limit(1)
        )
        async for raw in cursor:
            return self._decode(raw)
        return None

    async def list_all(self) -> tuple[PolicySegmentRecord, ...]:
        cursor = self._db[self.COLLECTION].find({}).sort("started_at", 1)
        out: list[PolicySegmentRecord] = []
        async for raw in cursor:
            decoded = self._decode(raw)
            if decoded is not None:
                out.append(decoded)
        return tuple(out)

    async def append(self, record: PolicySegmentRecord) -> None:
        doc = record.model_dump(mode="python")
        doc["segment_id"] = str(record.segment_id)
        await self._db[self.COLLECTION].insert_one(doc)

    def _decode(self, raw: dict[str, Any]) -> PolicySegmentRecord | None:
        doc = {k: v for k, v in raw.items() if k != "_id"}
        sid = doc.get("segment_id")
        if isinstance(sid, str):
            doc["segment_id"] = UUID(sid)
        started = doc.get("started_at")
        if isinstance(started, datetime) and started.tzinfo is None:
            doc["started_at"] = started.replace(tzinfo=UTC)
        try:
            return PolicySegmentRecord.model_validate(doc, strict=False)
        except Exception as exc:  # noqa: BLE001 — log + drop row
            log.warning(
                "policy_segment_decode_failed",
                policy_hash=raw.get("policy_hash"),
                error=str(exc),
            )
            return None


async def ensure_policy_segment(
    store: MongoPolicySegmentStore,
    *,
    now: datetime,
    trade_date: str,
    repo_root: Path | str = ".",
) -> PolicySegmentRecord:
    """Boot-time transition ledger: append a segment iff the hash moved.

    Returns the ACTIVE segment record (the freshly appended one or the
    unchanged latest). The append-only row is the transition-point
    provenance the frontend + acceptance segmentation read (§1.6).
    """
    components = build_policy_components(repo_root=repo_root)
    policy_hash = hashlib.sha256(
        json.dumps(
            components,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()

    latest = await store.latest()
    if latest is not None and latest.policy_hash == policy_hash:
        return latest

    record = PolicySegmentRecord(
        policy_hash=policy_hash,
        started_at=now,
        trade_date=trade_date,
        components=components,
    )
    await store.append(record)
    log.info(
        "policy_segment_opened",
        policy_hash=policy_hash,
        previous=(None if latest is None else latest.policy_hash),
        trade_date=trade_date,
    )
    return record


__all__ = [
    "POLICY_CONFIG_FILES",
    "POLICY_ENV_FLAGS",
    "MongoPolicySegmentStore",
    "PolicySegmentRecord",
    "build_policy_components",
    "compute_policy_hash",
    "current_sell_stack_version",
    "ensure_policy_segment",
]
