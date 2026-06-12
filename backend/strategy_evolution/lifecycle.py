"""Strategy lifecycle state machine (R-002 / P2-2-amendment-2026-05-24).

candidate → shadow → active → decaying → retired. The single source of
truth for lifecycle transitions, mirroring the reconciliation ticket
state machine discipline: direct ``model_copy(update={"state": ...})``
is a red line — all callers go through :func:`transition_lifecycle`.

Locked invariants:

* **Registry-gated activation (R-001 tie-in):** a strategy may enter
  ACTIVE only when its content hash is approved in the
  :class:`LiveArtifactRegistry` (``STRATEGY_CODE`` kind). A valid but
  un-pinned hash is rejected — promotion machinery can never outrun
  the human/objective approval gate.
* **RETIRED is terminal + no re-proposal:** there is no transition out
  of RETIRED, and the ledger rejects opening a new CANDIDATE for a
  strategy id that was ever retired (dossier §215 — retired nodes stay
  as provenance, never resurface as "new" discoveries).
* **Append-only ledger:** the Mongo store only inserts; current state
  is a deterministic fold over the event log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from backend.strategy_evolution.live_artifact_registry import (
    ArtifactKind,
    LiveArtifactRegistry,
)

log = structlog.get_logger(component="strategy_evolution.lifecycle")

_SHA256_HEX = r"^[0-9a-f]{64}$"


class StrategyLifecycleState(StrEnum):
    """The five locked lifecycle states (P2-2-amendment-2026-05-24)."""

    CANDIDATE = "candidate"
    SHADOW = "shadow"
    ACTIVE = "active"
    DECAYING = "decaying"
    RETIRED = "retired"


_S = StrategyLifecycleState
ALLOWED_LIFECYCLE_TRANSITIONS: frozenset[
    tuple[StrategyLifecycleState, StrategyLifecycleState]
] = frozenset(
    {
        (_S.CANDIDATE, _S.SHADOW),
        # A candidate that fails screening retires directly.
        (_S.CANDIDATE, _S.RETIRED),
        (_S.SHADOW, _S.ACTIVE),
        # A shadow failure retires; shadow never goes back to candidate
        # (re-running screening on the same artifact is re-proposal).
        (_S.SHADOW, _S.RETIRED),
        (_S.ACTIVE, _S.DECAYING),
        (_S.ACTIVE, _S.RETIRED),
        # Decay can recover (performance returned within band) or end.
        (_S.DECAYING, _S.ACTIVE),
        (_S.DECAYING, _S.RETIRED),
    }
)
"""No transition leaves RETIRED — terminal by construction."""


class InvalidLifecycleTransitionError(ValueError):
    """Requested transition is not in the allowlist."""


class UnapprovedStrategyError(ValueError):
    """ACTIVE requested for a hash the LiveArtifactRegistry has not
    pinned (fail-closed; R-001 / R0 §8)."""


class StrategyLifecycleRecord(BaseModel):
    """Current lifecycle state of one strategy artifact."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    strategy_hash: str = Field(pattern=_SHA256_HEX)
    """Content SHA256 of the strategy artifact — the SAME identifier
    the LiveArtifactRegistry pins (STRATEGY_CODE kind)."""

    state: StrategyLifecycleState
    entered_at: datetime
    reason: str = Field(min_length=1, max_length=256)


class LifecycleEvent(BaseModel):
    """One append-only row in ``strategy_lifecycle_events``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    strategy_hash: str = Field(pattern=_SHA256_HEX)
    from_state: StrategyLifecycleState | None = None
    """``None`` for the opening CANDIDATE event."""
    to_state: StrategyLifecycleState
    occurred_at: datetime
    reason: str = Field(min_length=1, max_length=256)


def transition_lifecycle(
    record: StrategyLifecycleRecord,
    target: StrategyLifecycleState,
    *,
    at: datetime,
    reason: str,
    registry: LiveArtifactRegistry | None = None,
) -> StrategyLifecycleRecord:
    """Move ``record`` to ``target`` if the transition is allowed.

    Args:
        record: current lifecycle record.
        target: requested next state.
        at: transition timestamp (must be >= ``record.entered_at``).
        reason: human/audit-readable transition cause.
        registry: REQUIRED when ``target`` is ACTIVE — the strategy
            hash must be pinned (fail-closed: a missing registry is
            treated like an unapproved hash, never waved through).

    Raises:
        InvalidLifecycleTransitionError: pair not in the allowlist.
        UnapprovedStrategyError: ACTIVE requested without a registry
            pin for this hash.
        ValueError: ``at`` precedes the current state's entry time.
    """
    pair = (record.state, target)
    if pair not in ALLOWED_LIFECYCLE_TRANSITIONS:
        raise InvalidLifecycleTransitionError(
            f"{record.strategy_hash[:12]}: {record.state.value} → "
            f"{target.value} not allowed"
        )
    if at < record.entered_at:
        raise ValueError("transition time must be >= entered_at")
    if target is _S.ACTIVE:
        if registry is None or not registry.is_approved(
            ArtifactKind.STRATEGY_CODE, record.strategy_hash
        ):
            raise UnapprovedStrategyError(
                f"strategy {record.strategy_hash[:12]} is not pinned in "
                f"the LiveArtifactRegistry; ACTIVE is registry-gated "
                f"(R-001, fail-closed)"
            )
    return StrategyLifecycleRecord(
        strategy_hash=record.strategy_hash,
        state=target,
        entered_at=at,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Append-only ledger
# ---------------------------------------------------------------------------


@runtime_checkable
class _MotorDatabase(Protocol):
    def __getitem__(self, name: str) -> Any: ...


class StrategyRetiredError(ValueError):
    """A retired strategy hash was re-proposed (no-re-proposal rule)."""


class StaleLifecycleRecordError(ValueError):
    """The caller-supplied record no longer matches the folded ledger
    state (a retried call or a concurrent transition won) — appending
    on stale state could rewind a terminal/active strategy (codex P1)."""


class MongoLifecycleLedger:
    """Append-only adapter over ``strategy_lifecycle_events``.

    Current state = fold over the per-strategy event log. The only
    write is ``insert_one``; corrections append a new transition.
    """

    COLLECTION = "strategy_lifecycle_events"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def open_candidate(
        self,
        strategy_hash: str,
        *,
        at: datetime,
        reason: str,
    ) -> StrategyLifecycleRecord:
        """Open a new CANDIDATE — rejected for ever-retired hashes."""
        events = await self._events_for(strategy_hash)
        if any(e.to_state is _S.RETIRED for e in events):
            raise StrategyRetiredError(
                f"strategy {strategy_hash[:12]} was retired; re-proposal "
                f"is forbidden (provenance stays, discovery must move on)"
            )
        if events:
            raise ValueError(
                f"strategy {strategy_hash[:12]} already has a lifecycle"
            )
        record = StrategyLifecycleRecord(
            strategy_hash=strategy_hash,
            state=_S.CANDIDATE,
            entered_at=at,
            reason=reason,
        )
        await self._append(
            LifecycleEvent(
                strategy_hash=strategy_hash,
                from_state=None,
                to_state=_S.CANDIDATE,
                occurred_at=at,
                reason=reason,
            )
        )
        return record

    async def record_transition(
        self,
        record: StrategyLifecycleRecord,
        target: StrategyLifecycleState,
        *,
        at: datetime,
        reason: str,
        registry: LiveArtifactRegistry | None = None,
    ) -> StrategyLifecycleRecord:
        """Re-fold, verify the caller's view, validate, then append.

        Codex R-002 P1 — validating only the caller-supplied record
        would let a retried call holding a stale CANDIDATE/SHADOW view
        append after a RETIRED event and effectively rewind the
        terminal state (``current_state`` trusts the last event). The
        folded ledger state is the authority; a mismatch raises
        :class:`StaleLifecycleRecordError` and nothing is appended.
        """
        current = await self.current_state(record.strategy_hash)
        if current is None:
            raise StaleLifecycleRecordError(
                f"strategy {record.strategy_hash[:12]} has no lifecycle; "
                f"open_candidate first"
            )
        if current.state is not record.state:
            raise StaleLifecycleRecordError(
                f"strategy {record.strategy_hash[:12]}: caller holds "
                f"{record.state.value} but the ledger is at "
                f"{current.state.value} — refusing stale transition"
            )
        moved = transition_lifecycle(
            current, target, at=at, reason=reason, registry=registry
        )
        await self._append(
            LifecycleEvent(
                strategy_hash=record.strategy_hash,
                from_state=current.state,
                to_state=target,
                occurred_at=at,
                reason=reason,
            )
        )
        return moved

    async def current_state(
        self, strategy_hash: str
    ) -> StrategyLifecycleRecord | None:
        """Fold the event log to the current record (None = unknown)."""
        events = await self._events_for(strategy_hash)
        if not events:
            return None
        last = events[-1]
        return StrategyLifecycleRecord(
            strategy_hash=strategy_hash,
            state=last.to_state,
            entered_at=last.occurred_at,
            reason=last.reason,
        )

    async def _events_for(
        self, strategy_hash: str
    ) -> tuple[LifecycleEvent, ...]:
        cursor = (
            self._db[self.COLLECTION]
            .find({"strategy_hash": strategy_hash})
            .sort("occurred_at", 1)
        )
        out: list[LifecycleEvent] = []
        async for raw in cursor:
            decoded = self._decode(raw)
            if decoded is not None:
                out.append(decoded)
        return tuple(out)

    async def _append(self, event: LifecycleEvent) -> None:
        doc = event.model_dump(mode="python")
        doc["event_id"] = str(event.event_id)
        await self._db[self.COLLECTION].insert_one(doc)

    def _decode(self, raw: dict[str, Any]) -> LifecycleEvent | None:
        doc = {k: v for k, v in raw.items() if k != "_id"}
        eid = doc.get("event_id")
        if isinstance(eid, str):
            doc["event_id"] = UUID(eid)
        occurred = doc.get("occurred_at")
        if isinstance(occurred, datetime) and occurred.tzinfo is None:
            doc["occurred_at"] = occurred.replace(tzinfo=UTC)
        try:
            return LifecycleEvent.model_validate(doc, strict=False)
        except Exception as exc:  # noqa: BLE001 — log + drop row
            log.warning(
                "lifecycle_event_decode_failed",
                strategy_hash=raw.get("strategy_hash"),
                error=str(exc),
            )
            return None


__all__ = [
    "ALLOWED_LIFECYCLE_TRANSITIONS",
    "InvalidLifecycleTransitionError",
    "LifecycleEvent",
    "MongoLifecycleLedger",
    "StaleLifecycleRecordError",
    "StrategyLifecycleRecord",
    "StrategyLifecycleState",
    "StrategyRetiredError",
    "UnapprovedStrategyError",
    "transition_lifecycle",
]
