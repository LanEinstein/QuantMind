"""DecisionLedgerService — single owner of the decision_ledger lifecycle.

Responsibilities:

* Create an entry the moment an InstructionPlan is drafted, capturing
  the analysis_record_id / signal_id / instruction_id triad that ties
  the multi-agent debate to the executable plan.
* Append lifecycle events (broker fill, Feishu send, execution report,
  reconciliation, acceptance) without ever mutating prior events.
* Resolve any one correlation handle (instruction_id, broker_order_id,
  feishu_message_id, reconciliation_ticket_id, etc.) back to the full
  entry — supporting the front-end three-tab Reason drawer (Builder /
  Engine / Broker; P1-5 red line 7).

The persistence layer is abstracted behind :class:`LedgerRepository` so
unit tests can swap in :class:`InMemoryLedgerRepository`; the real Mongo
implementation lives on :class:`backend.data.database.MongoDBService`.

LLM red line: this module never imports `backend.llm` / `backend.agents`
/ `backend.mirofish`. The service only accepts validated DTOs and emits
audit-friendly events.
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.models.instruction import InstructionPlan, InstructionStatus
from backend.models.ledger import (
    DecisionLedgerEntry,
    LedgerEvent,
    LedgerEventKind,
)

_ALLOWED_ACTORS: frozenset[str] = frozenset(
    {"SYSTEM", "SCHEDULER", "FEISHU_USER", "FRONTEND_USER"}
)
"""Actor allowlist; LLM/agent layers are explicitly excluded (P0-10).
The redline-check / unit tests verify the set stays closed."""


class LedgerRepository(Protocol):
    """Persistence contract for the decision_ledger collection."""

    async def upsert(self, entry: DecisionLedgerEntry) -> None: ...
    async def get_by_instruction(
        self, instruction_id: str
    ) -> DecisionLedgerEntry | None: ...
    async def find_by_correlation(
        self, field: str, value: str
    ) -> DecisionLedgerEntry | None: ...


class InMemoryLedgerRepository:
    """In-memory ledger repository, used by tests and as a reference impl.

    Stores entries by ``instruction_id``; ``find_by_correlation`` walks
    the (small) test corpus. Not safe for concurrent writers — production
    code uses the Mongo implementation on :class:`MongoDBService`.
    """

    def __init__(self) -> None:
        self._store: dict[str, DecisionLedgerEntry] = {}

    async def upsert(self, entry: DecisionLedgerEntry) -> None:
        # Deepcopy keeps the stored entry independent of the caller's
        # reference; ``DecisionLedgerEntry`` is frozen but its tuple
        # equality identity matters for tests.
        self._store[entry.instruction_id] = deepcopy(entry)

    async def get_by_instruction(
        self, instruction_id: str
    ) -> DecisionLedgerEntry | None:
        return self._store.get(instruction_id)

    async def find_by_correlation(
        self, field: str, value: str
    ) -> DecisionLedgerEntry | None:
        if field == "instruction_id":
            return self._store.get(value)
        if field == "trade_id":
            for entry in self._store.values():
                if value in entry.trade_ids:
                    return entry
            return None
        if field not in _CORRELATION_FIELDS:
            raise ValueError(f"Unknown correlation field {field!r}")
        for entry in self._store.values():
            if getattr(entry, field) == value:
                return entry
        return None

    @property
    def entries(self) -> Iterable[DecisionLedgerEntry]:
        """Diagnostics handle for tests; not part of the Protocol."""
        return self._store.values()


_CORRELATION_FIELDS: frozenset[str] = frozenset(
    {
        "instruction_id",
        "analysis_record_id",
        "signal_id",
        "risk_validation_id",
        "broker_order_id",
        "feishu_message_id",
        "execution_report_id",
        "reconciliation_ticket_id",
        "acceptance_report_id",
        "trade_id",  # virtual — searches the trade_ids tuple
    }
)


def _ensure_actor(actor: str) -> str:
    if actor not in _ALLOWED_ACTORS:
        raise ValueError(
            f"ledger actor {actor!r} not in allowed set {sorted(_ALLOWED_ACTORS)}"
        )
    return actor


class MongoLedgerRepository:
    """Adapter binding :class:`LedgerRepository` to MongoDBService.

    Kept thin on purpose — all schema validation lives in
    :class:`DecisionLedgerEntry`; this layer only handles serialization
    and the read/write calls. Tests use :class:`InMemoryLedgerRepository`
    so we never have to spin up Mongo to validate service behavior.
    """

    def __init__(self, mongo_service: object) -> None:
        # Typed as ``object`` to avoid importing MongoDBService here —
        # that would couple services/ to data/, breaking the import-
        # isolation lint. The duck-typed methods exercised below are
        # the only contract.
        self._mongo = mongo_service

    async def upsert(self, entry: DecisionLedgerEntry) -> None:
        # Python-mode dump preserves datetimes (motor → BSON Date) and
        # tuples-as-lists for Mongo arrays; the matching read path below
        # rebuilds the entry from the same shape. Using mode="json"
        # would emit ISO strings that strict-mode validation rejects on
        # read — caught by codex-review cycle 1.
        await self._mongo.upsert_decision_ledger_entry(  # type: ignore[attr-defined]
            entry.model_dump()
        )

    async def get_by_instruction(
        self, instruction_id: str
    ) -> DecisionLedgerEntry | None:
        raw = await self._mongo.get_decision_ledger_by_instruction(  # type: ignore[attr-defined]
            instruction_id
        )
        return None if raw is None else _from_mongo(raw)

    async def find_by_correlation(
        self, field: str, value: str
    ) -> DecisionLedgerEntry | None:
        raw = await self._mongo.find_decision_ledger_by_correlation(  # type: ignore[attr-defined]
            field, value
        )
        return None if raw is None else _from_mongo(raw)


def _from_mongo(raw: dict[str, object]) -> DecisionLedgerEntry:
    """Strip the Mongo ``_id`` field and rebuild a typed entry.

    Validation runs in non-strict mode because Mongo coerces our tuple
    fields to lists; the schema's frozenness and ``extra='forbid'`` are
    still enforced — only the strict type-coercion is relaxed.

    BSON Date round-trips through motor as a *naive* UTC datetime by
    default (no ``tz_aware=True`` codec option). The model expects aware
    datetimes — naive values would later TypeError when compared with
    aware timestamps in ``append_event``. Coerce naive datetimes to UTC
    on the way in so the rest of the system sees consistent tz-aware
    objects. Caught by codex-review cycle 2.
    """
    payload = {k: v for k, v in raw.items() if k != "_id"}
    return DecisionLedgerEntry.model_validate(
        _attach_utc(payload), strict=False
    )


def _attach_utc(value: Any) -> Any:
    """Recursively coerce naive datetime values to UTC-aware datetimes.

    Pure transformation — never mutates ``value`` in place.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    if isinstance(value, dict):
        return {k: _attach_utc(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        coerced = [_attach_utc(v) for v in value]
        return coerced if isinstance(value, list) else tuple(coerced)
    return value


class DecisionLedgerService:
    """Append-only service for the decision_ledger collection."""

    def __init__(self, repository: LedgerRepository) -> None:
        self._repo = repository

    async def open_for_plan(
        self,
        plan: InstructionPlan,
        *,
        actor: str = "SYSTEM",
        at: datetime | None = None,
    ) -> DecisionLedgerEntry:
        """Create a fresh ledger entry from a newly drafted plan.

        Idempotent: re-calling for the same instruction_id returns the
        existing entry without appending a duplicate PLAN_DRAFTED.
        """
        _ensure_actor(actor)
        existing = await self._repo.get_by_instruction(plan.instruction_id)
        if existing is not None:
            return existing

        timestamp = at or plan.created_at
        entry = DecisionLedgerEntry(
            instruction_id=plan.instruction_id,
            analysis_record_id=plan.analysis_record_id,
            signal_id=plan.signal_id,
            risk_validation_id=plan.risk_validation_id,
            events=(
                LedgerEvent(
                    kind=LedgerEventKind.PLAN_DRAFTED,
                    at=timestamp,
                    actor=actor,
                    payload={
                        "side": plan.side.value,
                        "stock_code": plan.stock_code,
                        "status": plan.status.value,
                        "debate_round_count": plan.debate_round_count,
                    },
                ),
            ),
            created_at=timestamp,
            updated_at=timestamp,
        )
        await self._repo.upsert(entry)
        return entry

    async def append_event(
        self,
        instruction_id: str,
        *,
        kind: LedgerEventKind,
        at: datetime,
        actor: str = "SYSTEM",
        payload: dict[str, str | int | float | bool | None] | None = None,
        broker_order_id: str | None = None,
        trade_ids: tuple[str, ...] | None = None,
        feishu_message_id: str | None = None,
        execution_report_id: str | None = None,
        reconciliation_ticket_id: str | None = None,
        acceptance_report_id: str | None = None,
    ) -> DecisionLedgerEntry:
        """Append a lifecycle event and optionally set correlation handles.

        Returns the updated entry. Raises ``LookupError`` if the entry
        does not exist yet (the plan must call :meth:`open_for_plan`
        first).
        """
        _ensure_actor(actor)
        entry = await self._repo.get_by_instruction(instruction_id)
        if entry is None:
            raise LookupError(
                f"decision_ledger has no entry for {instruction_id!r}; "
                f"call open_for_plan first"
            )
        if at < entry.updated_at:
            raise ValueError(
                "event time must be >= existing updated_at "
                f"(at={at.isoformat()}, updated_at={entry.updated_at.isoformat()})"
            )

        event = LedgerEvent(
            kind=kind,
            at=at,
            actor=actor,
            payload=payload or {},
        )

        updates: dict[str, object] = {
            "events": entry.events + (event,),
            "updated_at": at,
        }
        if broker_order_id is not None:
            updates["broker_order_id"] = broker_order_id
        if trade_ids is not None:
            # Append-only — preserve prior trade ids when extending.
            merged = entry.trade_ids + tuple(
                t for t in trade_ids if t not in entry.trade_ids
            )
            updates["trade_ids"] = merged
        if feishu_message_id is not None:
            updates["feishu_message_id"] = feishu_message_id
        if execution_report_id is not None:
            updates["execution_report_id"] = execution_report_id
        if reconciliation_ticket_id is not None:
            updates["reconciliation_ticket_id"] = reconciliation_ticket_id
        if acceptance_report_id is not None:
            updates["acceptance_report_id"] = acceptance_report_id

        updated = entry.model_copy(update=updates)
        await self._repo.upsert(updated)
        return updated

    async def mark_plan_status(
        self,
        instruction_id: str,
        new_status: InstructionStatus,
        *,
        at: datetime,
        actor: str = "SYSTEM",
        reason: str | None = None,
    ) -> DecisionLedgerEntry:
        """Append the lifecycle event that mirrors an InstructionStatus jump.

        Maps the new status to the corresponding event kind; the
        InstructionStatus state machine itself is owned by Phase B-003.
        """
        kind = _STATUS_TO_KIND.get(new_status)
        if kind is None:
            raise ValueError(
                f"no ledger event mapping for status {new_status.value!r}"
            )
        payload: dict[str, str | int | float | bool | None] = {
            "status": new_status.value,
        }
        if reason:
            payload["reason"] = reason
        return await self.append_event(
            instruction_id,
            kind=kind,
            at=at,
            actor=actor,
            payload=payload,
        )

    async def get_by_instruction(
        self, instruction_id: str
    ) -> DecisionLedgerEntry | None:
        return await self._repo.get_by_instruction(instruction_id)

    async def find_by_correlation(
        self, field: str, value: str
    ) -> DecisionLedgerEntry | None:
        return await self._repo.find_by_correlation(field, value)


_STATUS_TO_KIND: dict[InstructionStatus, LedgerEventKind] = {
    InstructionStatus.VALIDATED: LedgerEventKind.PLAN_VALIDATED,
    InstructionStatus.REJECTED: LedgerEventKind.PLAN_REJECTED,
    InstructionStatus.DISPATCHED: LedgerEventKind.PLAN_DISPATCHED,
    InstructionStatus.FILLED: LedgerEventKind.BROKER_FILLED,
    InstructionStatus.EXPIRED: LedgerEventKind.BROKER_EXPIRED,
    InstructionStatus.AMBIGUOUS: LedgerEventKind.EXECUTION_REPORT_AMBIGUOUS,
}


__all__ = [
    "DecisionLedgerService",
    "InMemoryLedgerRepository",
    "LedgerRepository",
    "MongoLedgerRepository",
]
