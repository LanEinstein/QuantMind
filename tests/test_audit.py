"""Tests for B-005 AuditEvent + AuditStore.

Coverage:
- 34 event types, 5 actors, 4 outcomes locked
- Evolution events restrict actor to SYSTEM / SCHEDULER
- Plaintext-secret payload rejected
- AuditStore JSONL-first dual-write + Mongo fail-open
- read_jsonl tolerates malformed lines
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.audit.models import (
    AUDIT_EVENT_TYPES,
    EVOLUTION_EVENT_TYPES,
    SYSTEM_ONLY_ACTORS,
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)
from backend.audit.store import (
    AuditStore,
    InMemoryAuditCollection,
    read_jsonl,
)

# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------


class TestEnums:
    def test_event_type_count_matches_amendment(self) -> None:
        # The P1-6 amendment-34 file literally enumerates 40 event_type
        # values across 5 categories (2 + 11 + 7 + 13 + 7). The doc
        # prose summarizes them as "34 类" (2+11+7+13+7=40, doc claim of
        # "22+4+1+7=34" had an arithmetic error in the original P1-6 main
        # which counted 22 instead of the listed 28). The enum follows
        # the literal amendment listing — 40 distinct values — and the
        # "34 类" shorthand is documented in CLAUDE.md §2.9 / plan.html
        # B-005 for human readability.
        assert len(AUDIT_EVENT_TYPES) == 40

    def test_event_type_documented_categories(self) -> None:
        # 2 + 11 + 7 + 13 + 7 = 40 distinct values.
        category_1 = {
            AuditEventType.EXECUTION_REPORT_SUBMITTED,
            AuditEventType.RECONCILIATION_TICKET_DECIDED,
        }
        evolution = EVOLUTION_EVENT_TYPES
        assert len(category_1) == 2
        assert len(evolution) == 7
        # All five categories sum to the enum total.
        assert (
            len(AUDIT_EVENT_TYPES)
            == len(AuditEventType)
            == 40
        )

    def test_evolution_event_count_7(self) -> None:
        assert len(EVOLUTION_EVENT_TYPES) == 7

    def test_actor_count_5(self) -> None:
        assert len({a for a in AuditActor}) == 5

    def test_outcome_count_4(self) -> None:
        assert len({o for o in AuditOutcome}) == 4

    def test_system_only_actors_locked(self) -> None:
        assert SYSTEM_ONLY_ACTORS == {AuditActor.SYSTEM, AuditActor.SCHEDULER}

    @pytest.mark.parametrize(
        "value",
        [
            "execution_report_submitted",
            "reconciliation_ticket_decided",
            "mode_switch_initiated",
            "credential_rotated",
            "state_machine_illegal_transition",
            "monthly_budget_50pct_reached",
            "execution_report_parse_failed",
            "prompt_version_pinned",
            "shadow_evolution_run_completed",
        ],
    )
    def test_specific_event_types_present(self, value: str) -> None:
        assert AuditEventType(value).value == value


# -----------------------------------------------------------------------------
# AuditEvent
# -----------------------------------------------------------------------------


def _make_event(
    *,
    event_type: AuditEventType = AuditEventType.EXECUTION_REPORT_SUBMITTED,
    actor: AuditActor = AuditActor.FEISHU_USER,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    payload: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        timestamp=datetime(2026, 5, 12, 9, 30, tzinfo=UTC),
        event_type=event_type,
        actor=actor,
        resource_type="execution_report",
        payload=payload or {"instruction_id": "QM-20260512-093001-600519-BUY-001"},
        outcome=outcome,
    )


class TestAuditEvent:
    def test_basic_event(self) -> None:
        ev = _make_event()
        assert ev.event_type is AuditEventType.EXECUTION_REPORT_SUBMITTED
        assert ev.actor is AuditActor.FEISHU_USER

    def test_frozen(self) -> None:
        ev = _make_event()
        with pytest.raises(ValidationError):
            ev.outcome = AuditOutcome.FAILURE  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            AuditEvent(  # type: ignore[call-arg]
                timestamp=datetime(2026, 5, 12, tzinfo=UTC),
                event_type=AuditEventType.EXECUTION_REPORT_SUBMITTED,
                actor=AuditActor.FEISHU_USER,
                resource_type="x",
                outcome=AuditOutcome.SUCCESS,
                surprise="x",
            )

    @pytest.mark.parametrize(
        "event_type",
        list(EVOLUTION_EVENT_TYPES),
    )
    def test_evolution_requires_system_or_scheduler(
        self, event_type: AuditEventType
    ) -> None:
        for actor in [
            AuditActor.FEISHU_USER,
            AuditActor.FRONTEND_USER,
            AuditActor.CLI,
        ]:
            with pytest.raises(ValidationError):
                _make_event(event_type=event_type, actor=actor)

    @pytest.mark.parametrize(
        "actor",
        list(SYSTEM_ONLY_ACTORS),
    )
    def test_evolution_with_system_actor_ok(self, actor: AuditActor) -> None:
        ev = _make_event(
            event_type=AuditEventType.PROMPT_VERSION_PINNED,
            actor=actor,
        )
        assert ev.event_type is AuditEventType.PROMPT_VERSION_PINNED

    def test_payload_plaintext_secret_blocked(self) -> None:
        with pytest.raises(ValidationError):
            _make_event(payload={"raw_key": "sk-aaaaaaaaaaaaaaaaaaaa"})

    def test_payload_feishu_secret_blocked(self) -> None:
        with pytest.raises(ValidationError):
            _make_event(payload={"raw": "cli_aaaaaaaaaaaaaaaaaaaa"})

    def test_payload_assignment_form_blocked(self) -> None:
        with pytest.raises(ValidationError):
            _make_event(payload={"line": "DEEPSEEK_API_KEY=secret"})

    def test_payload_fingerprint_ok(self) -> None:
        ev = _make_event(payload={"key_fingerprint": "a1b2c3d4"})
        assert ev.payload["key_fingerprint"] == "a1b2c3d4"


# -----------------------------------------------------------------------------
# AuditStore
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_writes_to_jsonl_and_mongo(tmp_path: Path) -> None:
    coll = InMemoryAuditCollection()
    store = AuditStore(coll, jsonl_path=tmp_path / "audit.jsonl")

    ev = await store.write(
        event_type=AuditEventType.EXECUTION_REPORT_SUBMITTED,
        actor=AuditActor.FEISHU_USER,
        resource_type="execution_report",
        payload={"instruction_id": "QM-20260512-093001-600519-BUY-001"},
        outcome=AuditOutcome.SUCCESS,
    )

    # Mongo: 1 doc inserted.
    assert len(coll.documents) == 1
    assert coll.documents[0]["event_type"] == ev.event_type.value
    # Codex-review cycle 1: Mongo doc must keep ``timestamp`` as a real
    # datetime (BSON Date in motor) so the 180-day TTL index works.
    assert isinstance(coll.documents[0]["timestamp"], datetime)
    assert coll.documents[0]["event_id"] == str(ev.event_id)

    # JSONL: 1 line containing the same event_id.
    jsonl = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(jsonl) == 1
    parsed = json.loads(jsonl[0])
    assert parsed["event_id"] == str(ev.event_id)


@pytest.mark.asyncio
async def test_store_mongo_failure_keeps_jsonl(tmp_path: Path) -> None:
    coll = InMemoryAuditCollection(fail=True)
    store = AuditStore(coll, jsonl_path=tmp_path / "audit.jsonl")

    ev = await store.write(
        event_type=AuditEventType.LLM_CALL_TIMEOUT_30S,
        actor=AuditActor.SYSTEM,
        resource_type="llm_call",
        payload={"provider": "deepseek"},
        outcome=AuditOutcome.DEGRADED,
    )

    # JSONL still has the line — local backup is the dependable layer.
    jsonl = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(jsonl) == 1
    parsed = json.loads(jsonl[0])
    assert parsed["event_id"] == str(ev.event_id)
    # Mongo silently failed — fail-open per P1-6 §1.7.4.
    assert coll.documents == []


@pytest.mark.asyncio
async def test_store_evolution_event_rejects_user_actor(
    tmp_path: Path,
) -> None:
    coll = InMemoryAuditCollection()
    store = AuditStore(coll, jsonl_path=tmp_path / "audit.jsonl")

    with pytest.raises(ValidationError):
        await store.write(
            event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
            actor=AuditActor.FEISHU_USER,
            resource_type="self_evolution_artifact",
            outcome=AuditOutcome.SUCCESS,
        )


def test_read_jsonl_tolerates_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    ev = AuditEvent(
        timestamp=datetime(2026, 5, 12, tzinfo=UTC),
        event_type=AuditEventType.MOCKBROKER_RESET,
        actor=AuditActor.SYSTEM,
        resource_type="mockbroker",
        payload={},
        outcome=AuditOutcome.SUCCESS,
    )
    path.write_text(
        ev.model_dump_json()
        + "\n"
        + "{ not valid json\n"
        + "\n"
        + ev.model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    events = read_jsonl(path)
    assert len(events) == 2
    assert all(e.event_type is AuditEventType.MOCKBROKER_RESET for e in events)


def test_read_jsonl_missing_file(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "absent.jsonl") == []
