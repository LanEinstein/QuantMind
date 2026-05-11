"""Tests for B-002 decision_ledger model + service.

Coverage:
- :class:`DecisionLedgerEntry` strict / frozen / append-only invariants
- :class:`LedgerEvent` actor allowlist (LLM never appears)
- :class:`DecisionLedgerService` correlation lookups end-to-end
- :class:`InMemoryLedgerRepository` round-trips without mutation
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.models.instruction import InstructionPlan, InstructionStatus
from backend.models.ledger import (
    DecisionLedgerEntry,
    LedgerEvent,
    LedgerEventKind,
)
from backend.services.ledger import (
    DecisionLedgerService,
    InMemoryLedgerRepository,
)
from tests.test_instruction_models import _make_plan

SH = ZoneInfo("Asia/Shanghai")


def _plan() -> InstructionPlan:
    return _make_plan()


def _event(kind: LedgerEventKind, at: datetime, actor: str = "SYSTEM") -> LedgerEvent:
    return LedgerEvent(kind=kind, at=at, actor=actor, payload={})


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------


class TestLedgerEvent:
    def test_frozen(self) -> None:
        ev = LedgerEvent(
            kind=LedgerEventKind.PLAN_DRAFTED,
            at=datetime(2026, 5, 12, 9, 30, tzinfo=SH),
            actor="SYSTEM",
        )
        with pytest.raises(ValidationError):
            ev.actor = "FEISHU_USER"  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            LedgerEvent(  # type: ignore[call-arg]
                kind=LedgerEventKind.PLAN_DRAFTED,
                at=datetime(2026, 5, 12, tzinfo=SH),
                actor="SYSTEM",
                surprise="x",
            )

    def test_payload_scalar_only(self) -> None:
        # Pydantic strict mode rejects nested dicts because the value
        # union excludes dict types.
        with pytest.raises(ValidationError):
            LedgerEvent(
                kind=LedgerEventKind.PLAN_DRAFTED,
                at=datetime(2026, 5, 12, tzinfo=SH),
                actor="SYSTEM",
                payload={"nested": {"x": 1}},  # type: ignore[dict-item]
            )


class TestDecisionLedgerEntry:
    def test_requires_at_least_one_event(self) -> None:
        with pytest.raises(ValidationError):
            DecisionLedgerEntry(
                instruction_id="QM-20260512-093001-600519-BUY-001",
                analysis_record_id="run-1",
                signal_id="sig-1",
                events=(),
                created_at=datetime(2026, 5, 12, 9, 30, tzinfo=SH),
                updated_at=datetime(2026, 5, 12, 9, 30, tzinfo=SH),
            )

    def test_events_must_be_chronological(self) -> None:
        t0 = datetime(2026, 5, 12, 9, 30, tzinfo=SH)
        t1 = t0 + timedelta(seconds=1)
        with pytest.raises(ValidationError):
            DecisionLedgerEntry(
                instruction_id="QM-20260512-093001-600519-BUY-001",
                analysis_record_id="run-1",
                signal_id="sig-1",
                events=(
                    _event(LedgerEventKind.PLAN_VALIDATED, t1),
                    _event(LedgerEventKind.PLAN_DRAFTED, t0),
                ),
                created_at=t0,
                updated_at=t1,
            )

    def test_updated_at_must_cover_last_event(self) -> None:
        t0 = datetime(2026, 5, 12, 9, 30, tzinfo=SH)
        t_late = datetime(2026, 5, 12, 10, 0, tzinfo=SH)
        with pytest.raises(ValidationError):
            DecisionLedgerEntry(
                instruction_id="QM-20260512-093001-600519-BUY-001",
                analysis_record_id="run-1",
                signal_id="sig-1",
                events=(_event(LedgerEventKind.PLAN_DRAFTED, t_late),),
                created_at=t0,
                updated_at=t0,
            )

    def test_frozen(self) -> None:
        t0 = datetime(2026, 5, 12, 9, 30, tzinfo=SH)
        entry = DecisionLedgerEntry(
            instruction_id="QM-20260512-093001-600519-BUY-001",
            analysis_record_id="run-1",
            signal_id="sig-1",
            events=(_event(LedgerEventKind.PLAN_DRAFTED, t0),),
            created_at=t0,
            updated_at=t0,
        )
        with pytest.raises(ValidationError):
            entry.broker_order_id = "ord-9"  # type: ignore[misc]


# -----------------------------------------------------------------------------
# Service
# -----------------------------------------------------------------------------


@pytest.fixture
def service() -> DecisionLedgerService:
    return DecisionLedgerService(InMemoryLedgerRepository())


@pytest.mark.asyncio
async def test_open_for_plan_creates_entry(service: DecisionLedgerService) -> None:
    plan = _plan()
    entry = await service.open_for_plan(plan)
    assert entry.instruction_id == plan.instruction_id
    assert entry.events[0].kind is LedgerEventKind.PLAN_DRAFTED
    assert entry.analysis_record_id == plan.analysis_record_id
    assert entry.signal_id == plan.signal_id


@pytest.mark.asyncio
async def test_open_for_plan_idempotent(service: DecisionLedgerService) -> None:
    plan = _plan()
    first = await service.open_for_plan(plan)
    second = await service.open_for_plan(plan)
    assert first == second
    assert len(second.events) == 1


@pytest.mark.asyncio
async def test_append_event_progresses_lifecycle(
    service: DecisionLedgerService,
) -> None:
    plan = _plan()
    await service.open_for_plan(plan)
    later = plan.created_at + timedelta(seconds=5)
    updated = await service.append_event(
        plan.instruction_id,
        kind=LedgerEventKind.PLAN_VALIDATED,
        at=later,
    )
    assert len(updated.events) == 2
    assert updated.events[-1].kind is LedgerEventKind.PLAN_VALIDATED
    assert updated.updated_at == later


@pytest.mark.asyncio
async def test_append_event_with_broker_correlation(
    service: DecisionLedgerService,
) -> None:
    plan = _plan()
    await service.open_for_plan(plan)
    fill_at = plan.created_at + timedelta(minutes=1)
    updated = await service.append_event(
        plan.instruction_id,
        kind=LedgerEventKind.BROKER_FILLED,
        at=fill_at,
        broker_order_id="ord-9",
        trade_ids=("trade-A", "trade-B"),
    )
    assert updated.broker_order_id == "ord-9"
    assert updated.trade_ids == ("trade-A", "trade-B")

    found = await service.find_by_correlation("broker_order_id", "ord-9")
    assert found is not None
    assert found.instruction_id == plan.instruction_id

    found_by_trade = await service.find_by_correlation("trade_id", "trade-B")
    assert found_by_trade is not None


@pytest.mark.asyncio
async def test_append_event_rejects_out_of_order_time(
    service: DecisionLedgerService,
) -> None:
    plan = _plan()
    await service.open_for_plan(plan)
    earlier = plan.created_at - timedelta(seconds=1)
    with pytest.raises(ValueError):
        await service.append_event(
            plan.instruction_id,
            kind=LedgerEventKind.PLAN_VALIDATED,
            at=earlier,
        )


@pytest.mark.asyncio
async def test_append_event_unknown_instruction_raises(
    service: DecisionLedgerService,
) -> None:
    with pytest.raises(LookupError):
        await service.append_event(
            "QM-20260512-093001-600519-BUY-001",
            kind=LedgerEventKind.PLAN_VALIDATED,
            at=datetime(2026, 5, 12, 9, 31, tzinfo=SH),
        )


@pytest.mark.asyncio
async def test_actor_allowlist(service: DecisionLedgerService) -> None:
    plan = _plan()
    await service.open_for_plan(plan)
    with pytest.raises(ValueError):
        await service.append_event(
            plan.instruction_id,
            kind=LedgerEventKind.PLAN_VALIDATED,
            at=plan.created_at + timedelta(seconds=5),
            actor="LLM",
        )


@pytest.mark.asyncio
async def test_mark_plan_status_emits_mapped_kind(
    service: DecisionLedgerService,
) -> None:
    plan = _plan()
    await service.open_for_plan(plan)
    at = plan.created_at + timedelta(seconds=5)
    updated = await service.mark_plan_status(
        plan.instruction_id, InstructionStatus.VALIDATED, at=at
    )
    assert updated.events[-1].kind is LedgerEventKind.PLAN_VALIDATED


@pytest.mark.asyncio
async def test_mark_plan_status_rejected_with_reason(
    service: DecisionLedgerService,
) -> None:
    plan = _plan()
    await service.open_for_plan(plan)
    updated = await service.mark_plan_status(
        plan.instruction_id,
        InstructionStatus.REJECTED,
        at=plan.created_at + timedelta(seconds=5),
        reason="position_limit failed",
    )
    last = updated.events[-1]
    assert last.kind is LedgerEventKind.PLAN_REJECTED
    assert last.payload["reason"] == "position_limit failed"


@pytest.mark.asyncio
async def test_find_by_correlation_unknown_field(
    service: DecisionLedgerService,
) -> None:
    with pytest.raises(ValueError):
        await service.find_by_correlation("not_a_field", "x")


@pytest.mark.asyncio
async def test_find_by_correlation_returns_none_when_absent(
    service: DecisionLedgerService,
) -> None:
    plan = _plan()
    await service.open_for_plan(plan)
    found = await service.find_by_correlation(
        "feishu_message_id", "never-issued"
    )
    assert found is None


def _strip_tz(value: object) -> object:
    """Simulate motor's default BSON decode (naive UTC datetimes)."""
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    if isinstance(value, dict):
        return {k: _strip_tz(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_tz(v) for v in value]
    return value


class _FakeMongoServiceWithBson:
    """Test double that mimics Mongo's tz-stripping codec.

    Real motor encodes datetime→BSON Date→naive datetime on decode unless
    ``tz_aware=True`` is configured. Our default deploy doesn't set that
    option, so we exercise the naive-datetime path here.
    """

    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    async def upsert_decision_ledger_entry(self, doc: dict) -> None:
        self.store[doc["instruction_id"]] = _strip_tz(doc)  # type: ignore[assignment]

    async def get_decision_ledger_by_instruction(
        self, instruction_id: str
    ) -> dict | None:
        return self.store.get(instruction_id)

    async def find_decision_ledger_by_correlation(
        self, field: str, value: str
    ) -> dict | None:
        for entry in self.store.values():
            if field == "trade_id" and value in entry.get("trade_ids", []):
                return entry
            if entry.get(field) == value:
                return entry
        return None


@pytest.mark.asyncio
async def test_mongo_round_trip_through_repo() -> None:
    """Codex-review cycle 1+2: write/read serialization must agree, and
    naive-UTC datetimes coming back from Mongo's default BSON decode
    must be re-coerced to UTC-aware so later append_event time
    comparisons don't raise TypeError.
    """
    from backend.services.ledger import MongoLedgerRepository

    fake_mongo = _FakeMongoServiceWithBson()
    repo = MongoLedgerRepository(fake_mongo)
    svc = DecisionLedgerService(repo)
    plan = _plan()

    await svc.open_for_plan(plan)
    await svc.append_event(
        plan.instruction_id,
        kind=LedgerEventKind.BROKER_FILLED,
        at=plan.created_at + timedelta(minutes=1),
        broker_order_id="ord-9",
        trade_ids=("trade-A",),
    )

    fetched = await svc.get_by_instruction(plan.instruction_id)
    assert fetched is not None
    assert fetched.instruction_id == plan.instruction_id
    assert fetched.trade_ids == ("trade-A",)
    # Codex-review cycle 2: datetimes must come back tz-aware so later
    # comparisons in append_event don't TypeError on naive vs aware.
    assert fetched.created_at.tzinfo is not None
    assert fetched.updated_at.tzinfo is not None

    # A subsequent append must succeed against the round-tripped entry.
    later = await svc.append_event(
        plan.instruction_id,
        kind=LedgerEventKind.FEISHU_SENT,
        at=plan.created_at + timedelta(minutes=2),
        feishu_message_id="msg-1",
    )
    assert later.feishu_message_id == "msg-1"

    by_trade = await svc.find_by_correlation("trade_id", "trade-A")
    assert by_trade is not None


@pytest.mark.asyncio
async def test_trade_ids_accumulate_without_duplication(
    service: DecisionLedgerService,
) -> None:
    plan = _plan()
    await service.open_for_plan(plan)
    first = await service.append_event(
        plan.instruction_id,
        kind=LedgerEventKind.BROKER_FILLED,
        at=plan.created_at + timedelta(seconds=5),
        trade_ids=("a", "b"),
    )
    second = await service.append_event(
        plan.instruction_id,
        kind=LedgerEventKind.BROKER_FILLED,
        at=plan.created_at + timedelta(seconds=10),
        trade_ids=("b", "c"),
    )
    assert first.trade_ids == ("a", "b")
    assert second.trade_ids == ("a", "b", "c")
