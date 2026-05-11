"""AuditStore — JSONL primary write + Mongo async write (P1-6 §1.7.2 / B-005).

Order of writes is reversed from the original P1-6 sketch: JSONL goes
first because the local file is the cheapest backup, then Mongo is
attempted. Mongo failures are logged as a warning and do **not** raise
(fail-open for infra glitches per P1-6 §1.7.4 ranking).

LLM red line: this module never imports ``backend.llm`` / ``backend.agents``
/ ``backend.mirofish``. The :func:`write` API only accepts validated
DTO parameters; any caller assembling an ``AuditEvent`` directly bypasses
the LLM-safety guarantees of this layer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from backend.audit.models import (
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)

log = logging.getLogger("backend.audit.store")


class _MongoCollection(Protocol):
    """Minimal duck-typed handle for the audit_events Mongo collection.

    Lets tests pass an :class:`InMemoryAuditCollection` without importing
    motor. The real implementation in :mod:`backend.data.database`
    satisfies this same shape.
    """

    async def insert_one(self, document: dict[str, Any]) -> object: ...


class InMemoryAuditCollection:
    """In-memory stand-in used by tests."""

    def __init__(self, *, fail: bool = False) -> None:
        self.documents: list[dict[str, Any]] = []
        self._fail = fail

    async def insert_one(self, document: dict[str, Any]) -> object:
        if self._fail:
            raise RuntimeError("simulated Mongo outage")
        self.documents.append(document)
        return None


class AuditStore:
    """Append-only audit writer (Mongo primary + JSONL backup).

    The store is **not** safe for concurrent writers within a single
    process (Python's append-mode open is line-atomic but multi-step
    sequences are not). Production deploys use one writer per uvicorn
    worker; concurrent processes are not supported in Phase B.
    """

    def __init__(
        self,
        mongo_collection: _MongoCollection,
        *,
        jsonl_path: Path = Path("logs/audit.jsonl"),
    ) -> None:
        self._mongo = mongo_collection
        self._jsonl_path = jsonl_path
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    async def write(
        self,
        *,
        event_type: AuditEventType,
        actor: AuditActor,
        resource_type: str,
        payload: dict[str, Any] | None = None,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        actor_detail: str | None = None,
        resource_id: str | None = None,
        correlation_id: str | None = None,
        reason_namespace: str | None = None,
        timestamp: datetime | None = None,
    ) -> AuditEvent:
        """Build, persist, and return the :class:`AuditEvent`.

        JSONL is written first (local file rarely fails); Mongo is then
        attempted. Mongo errors log a ``audit_persistence_failed``
        warning and do not raise, so the main request path keeps going
        even if MongoDB is temporarily unreachable.
        """
        event = AuditEvent(
            timestamp=timestamp or datetime.now(UTC),
            event_type=event_type,
            actor=actor,
            actor_detail=actor_detail,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload or {},
            outcome=outcome,
            correlation_id=correlation_id,
            reason_namespace=reason_namespace,
        )

        # JSONL first — local file is the dependable layer.
        with self._jsonl_path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")

        # Mongo second — fail-open per P1-6 §1.7.4.
        # Use Python-mode dump so ``timestamp`` flows as a real datetime
        # (motor encodes it as BSON Date), letting the 180-day TTL index
        # on ``timestamp`` actually expire documents. ``model_dump(mode="json")``
        # would have serialised the value to ISO string and silently
        # broken the TTL — caught by codex-review cycle 1.
        doc = event.model_dump()
        doc["event_id"] = str(event.event_id)
        try:
            await self._mongo.insert_one(doc)
        except Exception as exc:  # noqa: BLE001 — broad-by-design fail-open
            log.warning(
                "audit_persistence_failed event_id=%s event_type=%s error=%s",
                event.event_id,
                event.event_type.value,
                exc,
            )

        return event


def read_jsonl(path: Path) -> list[AuditEvent]:
    """Replay a JSONL audit file into :class:`AuditEvent` objects.

    Used by ``scripts/query_audit.py`` and the recovery flow when Mongo
    is unavailable. Lines that fail validation are reported via
    ``log.warning`` but never raise; one corrupt line should not stop
    the operator from reading the rest of the trail.
    """
    out: list[AuditEvent] = []
    if not path.exists():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            # JSON-mode validation handles the str→datetime/UUID coercion
            # that strict-mode Python validation refuses to do.
            out.append(AuditEvent.model_validate_json(line))
        except Exception as exc:  # noqa: BLE001 — operator visibility
            log.warning(
                "audit_jsonl_invalid_line path=%s error=%s", path, exc
            )
    return out


__all__ = [
    "AuditStore",
    "InMemoryAuditCollection",
    "read_jsonl",
]
