"""Mongo-backed Repository implementations wired by Phase I-001.

Six repositories satisfy the Protocols declared in the API + integration
layers so :mod:`backend.main` can attach concrete persistence to
``app.state`` and the GET endpoints stop reporting
``repository_status="unavailable"``:

* :class:`MongoInstructionPlanRepository` — backs
  :class:`backend.api.instruction_plans.InstructionPlanReadRepository`
  (list + detail + builder_early_returns + broker_at_fill).
* :class:`MongoEquityPointRepository` — backs
  :class:`backend.api.equity_points.EquityPointReadRepository`
  (latest 30s MTM point).
* :class:`MongoTicketRepository` — backs
  :class:`backend.integrations.feishu.reconciliation.TicketRepository`
  (open ticket triage + decide write surface).
* :class:`MongoDailyReconciliationStore` — backs
  :class:`backend.integrations.feishu.reconciliation.DailyReconciliationStore`
  (user-reported mirror persistence).
* :class:`MongoSnapshotLookup` — backs
  :class:`backend.integrations.feishu.reconciliation.SnapshotLookup`
  (re-run :func:`detect_deviations` on a MISMATCH reply).
* :class:`MongoAcceptanceRepository` — backs
  :class:`backend.services.acceptance_report.AcceptanceRepository`
  (16:00:30 daily acceptance window persistence; ``can_switch_to_feishu_on``
  reads the latest row here).

All implementations follow the same shape: thin adapter classes that
take a Motor ``AsyncIOMotorDatabase`` handle, accept already-validated
Pydantic models, and never re-implement schema constraints. The
schemas themselves stay owned by the domain modules
(:mod:`backend.models.instruction`, :mod:`backend.models.reconciliation`,
:mod:`backend.models.equity`, :mod:`backend.services.acceptance_report`).

LLM red line: no imports from ``backend.{llm,agents,mirofish}``. The
repositories are pure persistence boundaries — validation lives in the
DTOs they round-trip.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog

from backend.broker.persistence.events import BrokerEventType
from backend.models.equity import EquityPoint
from backend.models.instruction import InstructionPlan
from backend.models.reconciliation import (
    DailyReconciliation,
    MockBrokerSnapshot,
    ReconciliationTicket,
    ReconciliationTicketStatus,
    ReportedPosition,
)
from backend.services.acceptance_report import AcceptanceReport

log = structlog.get_logger(component="services.mongo_repositories")


# ---------------------------------------------------------------------------
# Motor protocols (duck-typed so tests can swap an in-memory fake without
# importing motor — keeps the Phase E import-isolation lint clean).
# ---------------------------------------------------------------------------


@runtime_checkable
class _MotorCollection(Protocol):
    def find(self, *args: Any, **kwargs: Any) -> Any: ...
    async def find_one(
        self, *args: Any, **kwargs: Any
    ) -> dict[str, Any] | None: ...
    async def update_one(
        self, *args: Any, **kwargs: Any
    ) -> Any: ...
    async def insert_one(self, *args: Any, **kwargs: Any) -> Any: ...
    async def count_documents(
        self, *args: Any, **kwargs: Any
    ) -> int: ...


@runtime_checkable
class _MotorDatabase(Protocol):
    def __getitem__(self, name: str) -> _MotorCollection: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_utc(value: Any) -> Any:
    """Recursively coerce naive ``datetime`` values to UTC-aware.

    Motor decodes BSON Dates as naive UTC by default; the Pydantic
    models expect aware datetimes. Mirrors
    :func:`backend.services.ledger._attach_utc`.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    if isinstance(value, dict):
        return {k: _ensure_utc(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ensure_utc(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_ensure_utc(v) for v in value)
    return value


def _strip_id(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop the Mongo ``_id`` key so Pydantic validation does not trip."""
    return {k: v for k, v in raw.items() if k != "_id"}


# ===========================================================================
# 1. InstructionPlan
# ===========================================================================


class MongoInstructionPlanRepository:
    """Adapter over ``instruction_plans`` collection.

    Schema notes:
    * One document per ``instruction_id`` (unique index keyed on the id).
    * The InstructionPlan model is dumped in ``mode="python"`` so
      datetimes round-trip as BSON Date and tuples land as Mongo arrays.
    * ``builder_early_returns`` rows live in a sibling
      ``instruction_plan_builder_early_returns`` collection keyed by
      ``instruction_id`` (set by the orchestrator when an early return
      blocks routing for a plan that did get persisted — D-003
      candidates that never produce a plan are out of scope for the
      drawer because there is no instruction_id to query against).
    * ``broker_at_fill`` reads the most recent ``ORDER_FILLED`` /
      ``EXECUTION_REPORT_APPLIED`` row from ``broker_events`` filtered
      by ``correlation_id=instruction_id``.
    """

    PLAN_COLLECTION = "instruction_plans"
    BUILDER_COLLECTION = "instruction_plan_builder_early_returns"
    BROKER_EVENT_COLLECTION = "broker_events"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    # -- writers (owned by the SignalToPlan orchestrator / Phase I-001) --

    async def upsert(self, plan: InstructionPlan) -> None:
        doc = plan.model_dump(mode="python")
        doc["instruction_id"] = plan.instruction_id  # keep top-level key
        await self._db[self.PLAN_COLLECTION].update_one(
            {"instruction_id": plan.instruction_id},
            {"$set": doc},
            upsert=True,
        )

    async def append_builder_early_return(
        self,
        *,
        instruction_id: str,
        reason_namespace: str,
        payload: dict[str, Any],
        at: datetime,
    ) -> None:
        """Persist a builder early-return row tied to an existing plan."""
        await self._db[self.BUILDER_COLLECTION].insert_one(
            {
                "instruction_id": instruction_id,
                "reason_namespace": reason_namespace,
                "payload": dict(payload),
                "at": at,
            }
        )

    # -- read surface ---------------------------------------------------

    async def list_recent(
        self,
        *,
        limit: int,
        status: str | None,
        trade_date: str | None,
    ) -> list[InstructionPlan]:
        query: dict[str, Any] = {}
        if status is not None:
            query["status"] = status
        if trade_date is not None:
            query["trade_date"] = trade_date
        cursor = (
            self._db[self.PLAN_COLLECTION]
            .find(query)
            .sort("created_at", -1)
            .limit(limit)
        )
        plans: list[InstructionPlan] = []
        async for raw in cursor:
            try:
                plans.append(
                    InstructionPlan.model_validate(
                        _ensure_utc(_strip_id(raw)), strict=False
                    )
                )
            except Exception as exc:  # noqa: BLE001 — log + drop row
                log.warning(
                    "instruction_plan_decode_failed",
                    instruction_id=raw.get("instruction_id"),
                    error=str(exc),
                )
        return plans

    async def get_by_id(
        self, instruction_id: str
    ) -> InstructionPlan | None:
        raw = await self._db[self.PLAN_COLLECTION].find_one(
            {"instruction_id": instruction_id}
        )
        if raw is None:
            return None
        return InstructionPlan.model_validate(
            _ensure_utc(_strip_id(raw)), strict=False
        )

    async def get(
        self, instruction_id: str
    ) -> InstructionPlan | None:
        """Alias satisfying :class:`InstructionPlanLookup` protocol.

        :class:`backend.api.instruction_plans.InstructionPlanReadRepository`
        uses ``get_by_id`` while
        :class:`backend.integrations.feishu.parser.InstructionPlanLookup`
        uses ``get``. The two protocols are intentionally separate
        (one is read-list-oriented, the other is single-fetch), but
        wiring the same Mongo adapter into both saves an indirection
        adapter in main.py. Codex Cycle 3 P1 fix — without this method
        the live ExecutionReportOrchestrator path raised
        ``AttributeError`` the first time a real report arrived.
        """
        return await self.get_by_id(instruction_id)

    async def builder_early_returns(
        self, instruction_id: str
    ) -> list[dict[str, Any]]:
        cursor = (
            self._db[self.BUILDER_COLLECTION]
            .find({"instruction_id": instruction_id})
            .sort("at", 1)
        )
        rows: list[dict[str, Any]] = []
        async for raw in cursor:
            cleaned = _ensure_utc(_strip_id(raw))
            at = cleaned.get("at")
            rows.append(
                {
                    "reason_namespace": cleaned.get("reason_namespace"),
                    "payload": cleaned.get("payload", {}),
                    "at": (
                        at.astimezone(UTC).isoformat()
                        if isinstance(at, datetime)
                        else at
                    ),
                }
            )
        return rows

    AUDIT_EVENT_COLLECTION = "audit_events"

    async def broker_at_fill(
        self, instruction_id: str
    ) -> dict[str, Any] | None:
        """Project the last ORDER_FILLED / REJECTED row for the plan.

        Returns ``None`` when the plan has never been routed (e.g. HOLD
        plans which never reach the broker, or BUY/SELL plans whose
        Builder early-returned before MockBroker).

        Codex Cycle 4 P2 fix: the rejection path also reads
        ``audit_events`` because :class:`SimulationExecutor` writes a
        ``RISK_ENGINE_CHECK_REJECTED`` audit row instead of an
        ``ORDER_REJECTED`` broker event when the broker turns down an
        order (price-limit at-fill recheck, etc.). Without that
        fallback the operator reason-drawer would lose the locked
        broker rejection reason like ``price_limit_violation_at_fill``.
        """
        cursor = (
            self._db[self.BROKER_EVENT_COLLECTION]
            .find(
                {
                    "correlation_id": instruction_id,
                    "event_type": {
                        "$in": [
                            BrokerEventType.ORDER_FILLED.value,
                            BrokerEventType.EXECUTION_REPORT_APPLIED.value,
                        ]
                    },
                }
            )
            .sort("sequence", -1)
            .limit(1)
        )
        async for raw in cursor:
            cleaned = _ensure_utc(_strip_id(raw))
            payload = cleaned.get("payload") or {}
            return {
                "outcome": "FILLED",
                "reason": None,
                "fill_price": payload.get("fill_price"),
                "fill_volume": payload.get("volume"),
                "broker_event_sequence": cleaned.get("sequence"),
            }
        # No success row in broker_events — also scan ORDER_REJECTED
        # rows for callers (e.g. ExecutionReportApplier) that DO write
        # rejection events directly.
        cursor = (
            self._db[self.BROKER_EVENT_COLLECTION]
            .find(
                {
                    "correlation_id": instruction_id,
                    "event_type": BrokerEventType.ORDER_REJECTED.value,
                }
            )
            .sort("sequence", -1)
            .limit(1)
        )
        async for raw in cursor:
            cleaned = _ensure_utc(_strip_id(raw))
            payload = cleaned.get("payload") or {}
            return {
                "outcome": "REJECTED",
                "reason": payload.get("reason"),
                "fill_price": None,
                "fill_volume": None,
                "broker_event_sequence": cleaned.get("sequence"),
            }
        # Final fallback — SimulationExecutor's _reject path writes
        # RISK_ENGINE_CHECK_REJECTED to audit_events with the rejection
        # reason in payload.reason. Surface the same locked reason
        # string to the broker_at_fill drawer tab.
        cursor = (
            self._db[self.AUDIT_EVENT_COLLECTION]
            .find(
                {
                    "correlation_id": instruction_id,
                    "event_type": "risk_engine_check_rejected",
                }
            )
            .sort("timestamp", -1)
            .limit(1)
        )
        async for raw in cursor:
            cleaned = _ensure_utc(_strip_id(raw))
            payload = cleaned.get("payload") or {}
            return {
                "outcome": "REJECTED",
                "reason": payload.get("reason"),
                "fill_price": None,
                "fill_volume": None,
                "broker_event_sequence": None,
            }
        return None


# ===========================================================================
# 2. EquityPoint
# ===========================================================================


class MongoEquityPointRepository:
    """Adapter over ``equity_points`` collection (E-006 / P1-2.B §1.1)."""

    COLLECTION = "equity_points"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def upsert(self, point: EquityPoint) -> None:
        doc = point.model_dump(mode="python")
        await self._db[self.COLLECTION].update_one(
            {"snapshot_at": point.snapshot_at},
            {"$set": doc},
            upsert=True,
        )

    async def get_latest(self) -> EquityPoint | None:
        cursor = (
            self._db[self.COLLECTION]
            .find({})
            .sort("snapshot_at", -1)
            .limit(1)
        )
        async for raw in cursor:
            return EquityPoint.model_validate(
                _ensure_utc(_strip_id(raw)), strict=False
            )
        return None

    async def list_eod_series(
        self, start_date: str, end_date: str
    ) -> list[EquityPoint]:
        """AD-001 — one EquityPoint per trade_date in [start, end] inclusive.

        EquityPoint is the source-of-truth for the KPI header (replacing the
        trade-net-amount-derived curve). The collection holds 30s intraday
        ticks; this reduces to the LAST tick of each trade date (the
        closing-mark equity) so the daily series is deterministic. Trade
        dates are ``YYYY-MM-DD`` strings, so lexical order == chronological.
        Returned ascending by trade_date.
        """
        cursor = (
            self._db[self.COLLECTION]
            .find({"trade_date": {"$gte": start_date, "$lte": end_date}})
            .sort("snapshot_at", 1)
        )
        by_date: dict[str, EquityPoint] = {}
        async for raw in cursor:
            point = EquityPoint.model_validate(
                _ensure_utc(_strip_id(raw)), strict=False
            )
            by_date[point.trade_date] = point  # last tick of the day wins
        return [by_date[d] for d in sorted(by_date)]

    async def get_latest_before_trade_date(
        self, trade_date: str
    ) -> EquityPoint | None:
        """Latest MTM point STRICTLY before ``trade_date`` (``YYYY-MM-DD``).

        The prior trading day's closing equity = the day-open reference for the
        daily-loss brake (P0-7-amendment-2026-06-23). UNBOUNDED lookback (not a
        fixed window) so a long A-share holiday gap (Spring Festival / National
        Day, 7-12 calendar days) never drops the reference. Sorting by
        ``snapshot_at`` desc returns the LAST tick of the most recent earlier
        trade_date (its closing mark). ``None`` only on a genuine first session.
        """
        cursor = (
            self._db[self.COLLECTION]
            .find({"trade_date": {"$lt": trade_date}})
            .sort("snapshot_at", -1)
            .limit(1)
        )
        async for raw in cursor:
            return EquityPoint.model_validate(
                _ensure_utc(_strip_id(raw)), strict=False
            )
        return None


# ===========================================================================
# 3. ReconciliationTicket
# ===========================================================================


class MongoTicketRepository:
    """Adapter over ``reconciliation_tickets`` collection."""

    COLLECTION = "reconciliation_tickets"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def get(self, ticket_id: str) -> ReconciliationTicket | None:
        raw = await self._db[self.COLLECTION].find_one(
            {"ticket_id": ticket_id}
        )
        if raw is None:
            return None
        return ReconciliationTicket.model_validate(
            _ensure_utc(_strip_id(raw)), strict=False
        )

    async def save(self, ticket: ReconciliationTicket) -> None:
        doc = ticket.model_dump(mode="python")
        await self._db[self.COLLECTION].update_one(
            {"ticket_id": ticket.ticket_id},
            {"$set": doc},
            upsert=True,
        )

    async def list_open_for_date(
        self, trade_date: str
    ) -> tuple[ReconciliationTicket, ...]:
        return await self._list_open(trade_date=trade_date)

    async def list_all_open(self) -> tuple[ReconciliationTicket, ...]:
        """List every OPEN / EXPIRED ticket regardless of trade_date.

        Codex Cycle 9 P2 fix — ``_acceptance_callback`` uses this to
        decide whether to set ``reconciliation_paused=True``. Filtering
        only the current EOD trade_date would let a prior day's
        unresolved ticket pass through ``paused=False`` and persist
        PASS/FAIL while the buy/sell freeze for that older ticket is
        still live.
        """
        return await self._list_open(trade_date=None)

    async def allocate_next_id(self, trade_date_compact: str) -> str:
        """Return ``RECON-<yyyymmdd>-NNN`` one past today's max seq.

        Mirrors the allocation in ``scripts/reconcile_now.py`` (fail-safe
        001 when no ticket exists for the date). AA-001's 16:10 sim
        auto-reconciliation is the only in-process caller; the cron runs
        one-at-a-time so a read-then-write race cannot occur in practice,
        and a collision would surface as an upsert on the same id rather
        than silent data loss.
        """
        prefix = f"RECON-{trade_date_compact}-"
        cursor = self._db[self.COLLECTION].find(
            {"ticket_id": {"$regex": f"^{prefix}"}},
            projection={"ticket_id": 1},
        )
        max_seq = 0
        async for doc in cursor:
            tid = str(doc.get("ticket_id", ""))
            try:
                max_seq = max(max_seq, int(tid.rsplit("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        return f"{prefix}{max_seq + 1:03d}"

    async def _list_open(
        self, *, trade_date: str | None
    ) -> tuple[ReconciliationTicket, ...]:
        open_or_expired = [
            ReconciliationTicketStatus.OPEN.value,
            ReconciliationTicketStatus.EXPIRED.value,
        ]
        query: dict[str, Any] = {"status": {"$in": open_or_expired}}
        if trade_date is not None:
            query["trade_date"] = trade_date
        cursor = self._db[self.COLLECTION].find(query).sort("created_at", -1)
        out: list[ReconciliationTicket] = []
        async for raw in cursor:
            try:
                out.append(
                    ReconciliationTicket.model_validate(
                        _ensure_utc(_strip_id(raw)), strict=False
                    )
                )
            except Exception as exc:  # noqa: BLE001 — log + drop row
                log.warning(
                    "reconciliation_ticket_decode_failed",
                    ticket_id=raw.get("ticket_id"),
                    error=str(exc),
                )
        return tuple(out)


# ===========================================================================
# 4. DailyReconciliation
# ===========================================================================


class MongoDailyReconciliationStore:
    """Adapter over ``daily_reconciliations`` collection."""

    COLLECTION = "daily_reconciliations"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def save(self, daily: DailyReconciliation) -> None:
        doc = daily.model_dump(mode="python")
        # Compound key (ticket_id) so a retried Feishu delivery for the
        # same reconciliation reply collapses to one row instead of
        # piling up duplicates.
        await self._db[self.COLLECTION].update_one(
            {"ticket_id": daily.ticket_id},
            {"$set": doc},
            upsert=True,
        )

    async def get(self, key: str) -> DailyReconciliation | None:
        """Look up by ticket_id (preferred) or trade_date (legacy).

        Codex Cycle 7 P2 fix — the applier + orchestrator now warm
        the cache by ``ticket.ticket_id`` so multi-ticket days don't
        collapse to a single overwrite. ``ticket_id`` is the unique
        Mongo key; ``trade_date`` is kept as a fallback so existing
        callers (or tests that wired the old API) keep working.
        """
        raw = await self._db[self.COLLECTION].find_one(
            {"ticket_id": key}
        )
        if raw is None:
            raw = await self._db[self.COLLECTION].find_one(
                {"trade_date": key}
            )
        if raw is None:
            return None
        return DailyReconciliation.model_validate(
            _ensure_utc(_strip_id(raw)), strict=False
        )


# ===========================================================================
# 5. SnapshotLookup (broker_snapshots → MockBrokerSnapshot for MISMATCH)
# ===========================================================================


class MongoSnapshotLookup:
    """Resolve ``expected_snapshot_id`` → :class:`MockBrokerSnapshot`.

    Reads from the existing ``broker_snapshots`` collection (E-002) and
    projects the durable :class:`backend.broker.persistence.snapshots.BrokerSnapshot`
    rows into the orchestrator-facing
    :class:`backend.models.reconciliation.MockBrokerSnapshot` shape. The
    durable row carries the EOD cash + per-stock positions + checksum;
    the orchestrator only needs the typed positions for re-running
    :func:`detect_deviations`.
    """

    COLLECTION = "broker_snapshots"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def get(
        self, expected_snapshot_id: str
    ) -> MockBrokerSnapshot | None:
        raw = await self._db[self.COLLECTION].find_one(
            {"snapshot_id": expected_snapshot_id}
        )
        if raw is None:
            return None
        cleaned = _ensure_utc(_strip_id(raw))
        positions: list[ReportedPosition] = []
        # Codex Cycle 8 P2 fix — fail-closed on ANY position decode
        # failure. Returning a partial snapshot would make the MISMATCH
        # reconciliation path re-run detect_deviations against an
        # incomplete expected position set, producing a false deviation
        # report (e.g. flagging every dropped position as "unexpected"
        # or accepting a user-reported snapshot that's actually wrong).
        # Better to return None and let the orchestrator surface
        # ``expected_snapshot_missing`` than silently corrupt the
        # decision path.
        for pos in cleaned.get("positions") or ():
            try:
                positions.append(
                    ReportedPosition(
                        code=str(pos.get("code")),
                        volume=int(pos.get("volume", 0)),
                        cost_price=float(pos.get("cost_price", 0.0)),
                    )
                )
            except Exception as exc:  # noqa: BLE001 — fail-closed
                log.warning(
                    "snapshot_position_decode_failed",
                    snapshot_id=expected_snapshot_id,
                    error=str(exc),
                )
                return None
        snapshot_at = cleaned.get("created_at")
        if not isinstance(snapshot_at, datetime):
            return None
        return MockBrokerSnapshot(
            cash=float(cleaned.get("cash", 0.0)),
            positions=tuple(positions),
            snapshot_at=snapshot_at,
        )


# ===========================================================================
# 6. AcceptanceReport
# ===========================================================================


class MongoAcceptanceRepository:
    """Adapter over ``acceptance_reports`` collection (P0-6)."""

    COLLECTION = "acceptance_reports"

    def __init__(self, db: _MotorDatabase) -> None:
        self._db = db

    async def upsert(self, report: AcceptanceReport) -> None:
        doc = report.model_dump(mode="python")
        await self._db[self.COLLECTION].update_one(
            {"trade_date": report.trade_date},
            {"$set": doc},
            upsert=True,
        )

    async def latest(self) -> AcceptanceReport | None:
        cursor = (
            self._db[self.COLLECTION]
            .find({})
            .sort("trade_date", -1)
            .limit(1)
        )
        async for raw in cursor:
            return AcceptanceReport.model_validate(
                _ensure_utc(_strip_id(raw)), strict=False
            )
        return None

    async def list_recent(
        self, *, limit: int = 60
    ) -> list[AcceptanceReport]:
        """List recent reports for the Acceptance UI page (read-only)."""
        cursor = (
            self._db[self.COLLECTION]
            .find({})
            .sort("trade_date", -1)
            .limit(limit)
        )
        out: list[AcceptanceReport] = []
        async for raw in cursor:
            try:
                out.append(
                    AcceptanceReport.model_validate(
                        _ensure_utc(_strip_id(raw)), strict=False
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "acceptance_report_decode_failed",
                    trade_date=raw.get("trade_date"),
                    error=str(exc),
                )
        return out


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


async def _async_iter(cursor: Any) -> AsyncIterator[Any]:  # pragma: no cover
    """Defensive helper for non-Motor stubs used by tests."""
    async for row in cursor:
        yield row


__all__ = [
    "MongoAcceptanceRepository",
    "MongoDailyReconciliationStore",
    "MongoEquityPointRepository",
    "MongoInstructionPlanRepository",
    "MongoSnapshotLookup",
    "MongoTicketRepository",
]
