"""PromotionIntent — append-only promotion/demotion intent ledger
(AB-003 / P2-2-amendment-2026-06-12 §1.2).

The intent is the AUDITABLE bridge between a deterministic
:class:`PromotionDecision` (AB-002) and the activation mechanics
(activation.py): nothing touches ``live_artifacts.lock.json`` or
``next_boot.lock.json`` except through an intent row.

Mode boundary (amendment §2): intents may only be CREATED in pure
``simulation_auto`` mode — under ``feishu_interactive`` every promotion
keeps the original human gate (Feishu approval + human amendment + git
+ restart). A mode switch freezes all in-flight PENDING intents for the
owner to triage (AB-008 adversarial family 4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from backend.services.run_mode import feishu_interactive_enabled
from backend.strategy_evolution.experiment_registry import ExperimentKind

log = structlog.get_logger(component="strategy_evolution.promotion_intent")

_SHA256_HEX = r"^[0-9a-f]{64}$"


class IntentAction(StrEnum):
    PROMOTE = "promote"
    DEMOTE = "demote"


class IntentStatus(StrEnum):
    PENDING = "pending"
    ACTIVATED = "activated"
    ROLLED_BACK = "rolled_back"
    FROZEN = "frozen"
    CANCELLED = "cancelled"


_ST = IntentStatus
ALLOWED_INTENT_TRANSITIONS: frozenset[
    tuple[IntentStatus, IntentStatus]
] = frozenset(
    {
        (_ST.PENDING, _ST.ACTIVATED),
        (_ST.PENDING, _ST.CANCELLED),
        (_ST.PENDING, _ST.FROZEN),
        (_ST.ACTIVATED, _ST.ROLLED_BACK),
        # A frozen intent is owner-triaged: cancel only (re-proposing
        # goes through a fresh decision + intent, never a thaw).
        (_ST.FROZEN, _ST.CANCELLED),
    }
)


class PromotionModeError(RuntimeError):
    """Intent creation attempted outside the simulation_auto domain."""


class InvalidIntentTransitionError(ValueError):
    """Requested intent status change is not in the allowlist."""


class PromotionIntent(BaseModel):
    """One promotion/demotion intent (frozen; status lives in events)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    intent_id: UUID = Field(default_factory=uuid4)
    created_at: datetime
    action: IntentAction
    kind: ExperimentKind
    family: str = Field(min_length=1, max_length=128)
    artifact_hash: str = Field(pattern=_SHA256_HEX)
    experiment_id: str = Field(pattern=_SHA256_HEX)
    decision_digest: str = Field(pattern=_SHA256_HEX)
    """``PromotionDecision.inputs_digest`` — ties the intent to the
    exact deterministic judgement that authorised it (replayable)."""

    manifest_hash: str = Field(pattern=_SHA256_HEX)
    previous_manifest_hash: str | None = Field(
        default=None, pattern=_SHA256_HEX
    )


def build_promotion_intent(
    *,
    action: IntentAction,
    kind: ExperimentKind,
    family: str,
    artifact_hash: str,
    experiment_id: str,
    decision_digest: str,
    manifest_hash: str,
    previous_manifest_hash: str | None,
    created_at: datetime,
    decision_promoted: bool,
) -> PromotionIntent:
    """The single intent constructor — sim-mode + decision gated.

    Raises:
        PromotionModeError: ``feishu_interactive`` is enabled — the
            objective-promotion authority is simulation_auto ONLY
            (amendment §2); the live domain keeps the human gate.
        ValueError: a PROMOTE intent built from a non-promoted decision.
    """
    if feishu_interactive_enabled():
        raise PromotionModeError(
            "objective promotion intents are simulation_auto-only "
            "(P2-2-amendment-2026-06-12 §2); feishu_interactive keeps "
            "the human gate"
        )
    if action is IntentAction.PROMOTE and not decision_promoted:
        raise ValueError(
            "PROMOTE intent requires a promoted=True decision "
            "(fail-closed: a failed judgement cannot reach activation)"
        )
    return PromotionIntent(
        created_at=created_at,
        action=action,
        kind=kind,
        family=family,
        artifact_hash=artifact_hash,
        experiment_id=experiment_id,
        decision_digest=decision_digest,
        manifest_hash=manifest_hash,
        previous_manifest_hash=previous_manifest_hash,
    )


class IntentEvent(BaseModel):
    """One append-only row in ``promotion_intent_events``."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    intent_id: UUID
    to_status: IntentStatus
    occurred_at: datetime
    reason: str = Field(min_length=1, max_length=256)
    intent: PromotionIntent | None = None
    """The full intent rides on the opening PENDING event only."""


class _MotorDatabase(Protocol):
    def __getitem__(self, name: str) -> Any: ...


class MongoPromotionIntentLedger:
    """Append-only adapter over ``promotion_intent_events``."""

    COLLECTION = "promotion_intent_events"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def open_intent(
        self, intent: PromotionIntent, *, reason: str
    ) -> None:
        """Record the opening PENDING event (carries the full intent)."""
        await self._append(
            IntentEvent(
                intent_id=intent.intent_id,
                to_status=IntentStatus.PENDING,
                occurred_at=intent.created_at,
                reason=reason,
                intent=intent,
            )
        )

    async def record_status(
        self,
        intent_id: UUID,
        to_status: IntentStatus,
        *,
        at: datetime,
        reason: str,
    ) -> None:
        """Append a status transition (allowlist-validated)."""
        current = await self.current_status(intent_id)
        if current is None:
            raise InvalidIntentTransitionError(
                f"intent {intent_id} has no opening event"
            )
        if (current, to_status) not in ALLOWED_INTENT_TRANSITIONS:
            raise InvalidIntentTransitionError(
                f"intent {intent_id}: {current.value} → "
                f"{to_status.value} not allowed"
            )
        await self._append(
            IntentEvent(
                intent_id=intent_id,
                to_status=to_status,
                occurred_at=at,
                reason=reason,
            )
        )

    async def current_status(
        self, intent_id: UUID
    ) -> IntentStatus | None:
        events = await self._events_for(intent_id)
        return events[-1].to_status if events else None

    async def get_intent(
        self, intent_id: UUID
    ) -> PromotionIntent | None:
        events = await self._events_for(intent_id)
        for event in events:
            if event.intent is not None:
                return event.intent
        return None

    async def pending_intents(self) -> tuple[PromotionIntent, ...]:
        """Every intent whose folded status is PENDING."""
        cursor = (
            self._db[self.COLLECTION].find({}).sort("occurred_at", 1)
        )
        opened: dict[str, PromotionIntent] = {}
        status: dict[str, IntentStatus] = {}
        async for raw in cursor:
            event = self._decode(raw)
            if event is None:
                continue
            key = str(event.intent_id)
            status[key] = event.to_status
            if event.intent is not None:
                opened[key] = event.intent
        return tuple(
            opened[key]
            for key, st in status.items()
            if st is IntentStatus.PENDING and key in opened
        )

    async def freeze_all_pending(
        self, *, at: datetime, reason: str
    ) -> tuple[UUID, ...]:
        """Mode-switch hook: freeze every PENDING intent (amendment §2)."""
        frozen: list[UUID] = []
        for intent in await self.pending_intents():
            await self.record_status(
                intent.intent_id,
                IntentStatus.FROZEN,
                at=at,
                reason=reason,
            )
            frozen.append(intent.intent_id)
        if frozen:
            log.warning(
                "promotion_intents_frozen",
                count=len(frozen),
                reason=reason,
            )
        return tuple(frozen)

    async def _events_for(
        self, intent_id: UUID
    ) -> tuple[IntentEvent, ...]:
        cursor = (
            self._db[self.COLLECTION]
            .find({"intent_id": str(intent_id)})
            .sort("occurred_at", 1)
        )
        out: list[IntentEvent] = []
        async for raw in cursor:
            decoded = self._decode(raw)
            if decoded is not None:
                out.append(decoded)
        return tuple(out)

    async def _append(self, event: IntentEvent) -> None:
        doc = event.model_dump(mode="python")
        doc["event_id"] = str(event.event_id)
        doc["intent_id"] = str(event.intent_id)
        if event.intent is not None:
            doc["intent"] = event.intent.model_dump(mode="python")
            doc["intent"]["intent_id"] = str(event.intent.intent_id)
        await self._db[self.COLLECTION].insert_one(doc)

    def _decode(self, raw: dict[str, Any]) -> IntentEvent | None:
        doc = {k: v for k, v in raw.items() if k != "_id"}
        for key in ("event_id", "intent_id"):
            if isinstance(doc.get(key), str):
                doc[key] = UUID(doc[key])
        occurred = doc.get("occurred_at")
        if isinstance(occurred, datetime) and occurred.tzinfo is None:
            doc["occurred_at"] = occurred.replace(tzinfo=UTC)
        intent_raw = doc.get("intent")
        if isinstance(intent_raw, dict):
            intent_doc = dict(intent_raw)
            if isinstance(intent_doc.get("intent_id"), str):
                intent_doc["intent_id"] = UUID(intent_doc["intent_id"])
            created = intent_doc.get("created_at")
            if isinstance(created, datetime) and created.tzinfo is None:
                intent_doc["created_at"] = created.replace(tzinfo=UTC)
            doc["intent"] = intent_doc
        try:
            return IntentEvent.model_validate(doc, strict=False)
        except Exception as exc:  # noqa: BLE001 — log + drop row
            log.warning(
                "intent_event_decode_failed",
                intent_id=raw.get("intent_id"),
                error=str(exc),
            )
            return None


__all__ = [
    "ALLOWED_INTENT_TRANSITIONS",
    "IntentAction",
    "IntentEvent",
    "IntentStatus",
    "InvalidIntentTransitionError",
    "MongoPromotionIntentLedger",
    "PromotionIntent",
    "PromotionModeError",
    "build_promotion_intent",
]
