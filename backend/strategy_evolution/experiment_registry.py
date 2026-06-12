"""ExperimentRegistry — append-only, all experiments, content-addressed
(AB-001 / P2-2-amendment-2026-06-12 §1.1).

Every evolution experiment — INCLUDING failures — is registered before
any promotion judgement. Without the failures the promotion engine
degenerates into a multiple-testing machine (codex P2-1): after N
random tries something always "wins". The registry therefore exposes
the cumulative trial count per parameter family so AB-002 can deflate
its significance bar (deflated Sharpe + Bonferroni-style alpha).

``experiment_id`` is content-addressed over the experiment DESIGN
(kind + family + hypothesis + artifact + param space + window) — not
its outcome — so the same design registered twice is an idempotent
skip, and an outcome can never be laundered by re-registering.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger(component="strategy_evolution.experiment_registry")

_SHA256_HEX = r"^[0-9a-f]{64}$"


class ExperimentKind(StrEnum):
    """The three evolvable artifact classes with tiered windows."""

    THRESHOLD_PARAM = "threshold_param"
    PROMPT = "prompt"
    STRATEGY_CODE = "strategy_code"


class ExperimentRecord(BaseModel):
    """One append-only row in ``evolution_experiments``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    experiment_id: str = Field(pattern=_SHA256_HEX)
    kind: ExperimentKind
    family: str = Field(min_length=1, max_length=128)
    """Parameter family for trial counting + the AB-004 cooldown (e.g.
    ``line2.drawdown_stop`` / ``prompt.fund_manager`` / a strategy
    namespace). The multiple-testing correction is per family."""

    hypothesis: str = Field(min_length=1, max_length=512)
    artifact_hash: str = Field(pattern=_SHA256_HEX)
    param_space: dict[str, str] = Field(default_factory=dict)
    """Canonical parameter assignment (values stringified by the
    caller with fixed precision so the content address is stable)."""

    window_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    trading_days: int = Field(ge=0)
    sample_count: int = Field(ge=0)

    metrics: dict[str, float] = Field(default_factory=dict)
    ci_low: float | None = None
    ci_high: float | None = None
    success: bool
    registered_at: datetime


def compute_experiment_id(
    *,
    kind: ExperimentKind,
    family: str,
    hypothesis: str,
    artifact_hash: str,
    param_space: dict[str, str],
    window_start: str,
    window_end: str,
) -> str:
    """Content address of the experiment DESIGN (outcome excluded)."""
    payload = json.dumps(
        {
            "kind": kind.value,
            "family": family,
            "hypothesis": hypothesis,
            "artifact_hash": artifact_hash,
            "param_space": dict(sorted(param_space.items())),
            "window_start": window_start,
            "window_end": window_end,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def bonferroni_alpha(base_alpha: float, n_trials: int) -> float:
    """Bonferroni-corrected per-trial alpha — monotone tightening.

    ``n_trials`` counts EVERY registered experiment in the family
    (failures included); n=0/1 keeps the base alpha.
    """
    if not 0.0 < base_alpha < 1.0:
        raise ValueError("base_alpha must be in (0, 1)")
    if n_trials < 0:
        raise ValueError("n_trials must be >= 0")
    return base_alpha / max(1, n_trials)


class _MotorDatabase(Protocol):
    def __getitem__(self, name: str) -> Any: ...


class MongoExperimentRegistry:
    """Append-only adapter over ``evolution_experiments``.

    The only write is ``insert_one``; a duplicate ``experiment_id`` is
    an idempotent skip (the design was already registered — its
    outcome row stands, no rewrite path exists).
    """

    COLLECTION = "evolution_experiments"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def register(self, record: ExperimentRecord) -> bool:
        """Insert ``record``; returns False on an idempotent skip."""
        existing = await self._db[self.COLLECTION].find_one(
            {"experiment_id": record.experiment_id}
        )
        if existing is not None:
            log.info(
                "experiment_already_registered",
                experiment_id=record.experiment_id[:12],
            )
            return False
        doc = record.model_dump(mode="python")
        await self._db[self.COLLECTION].insert_one(doc)
        log.info(
            "experiment_registered",
            experiment_id=record.experiment_id[:12],
            family=record.family,
            success=record.success,
        )
        return True

    async def get(self, experiment_id: str) -> ExperimentRecord | None:
        raw = await self._db[self.COLLECTION].find_one(
            {"experiment_id": experiment_id}
        )
        if raw is None:
            return None
        return self._decode(raw)

    async def count_trials(self, family: str | None = None) -> int:
        """Cumulative trial count (ALL outcomes — failures included).

        This is the ``n_trials`` input to the AB-002 deflated-Sharpe /
        Bonferroni correction; ``None`` counts across every family.
        """
        query: dict[str, Any] = {} if family is None else {"family": family}
        return int(await self._db[self.COLLECTION].count_documents(query))

    async def last_registered_at(
        self, family: str
    ) -> datetime | None:
        """Most recent registration time for ``family`` (AB-004 cooldown)."""
        cursor = (
            self._db[self.COLLECTION]
            .find({"family": family})
            .sort("registered_at", -1)
            .limit(1)
        )
        async for raw in cursor:
            decoded = self._decode(raw)
            return None if decoded is None else decoded.registered_at
        return None

    def _decode(self, raw: dict[str, Any]) -> ExperimentRecord | None:
        doc = {k: v for k, v in raw.items() if k != "_id"}
        registered = doc.get("registered_at")
        if isinstance(registered, datetime) and registered.tzinfo is None:
            doc["registered_at"] = registered.replace(tzinfo=UTC)
        try:
            return ExperimentRecord.model_validate(doc, strict=False)
        except Exception as exc:  # noqa: BLE001 — log + drop row
            log.warning(
                "experiment_decode_failed",
                experiment_id=raw.get("experiment_id"),
                error=str(exc),
            )
            return None


__all__ = [
    "ExperimentKind",
    "ExperimentRecord",
    "MongoExperimentRegistry",
    "bonferroni_alpha",
    "compute_experiment_id",
]
