"""X-016 — AuditEventType Category-5 (self-evolution) enum review.

This file is the **dedicated** assertion of the 7 Category-5 enum values
locked by P1-6-amendment-2026-05-11-audit-eventtype-34 + P2-2 §2 red
line 12 (evolution actor restricted to SYSTEM / SCHEDULER).

Existing tests in ``tests/test_audit.py`` and
``tests/test_evolution_audit_writer.py`` already cover overlapping
behaviour. This file consolidates the Category-5 guard in one place so a
future reviewer can verify the seven enum values + actor red line
without grepping across two unrelated suites.

What is covered (Phase X-C task X-016 acceptance):

* The 7 enum values are present with their locked string values.
* ``EVOLUTION_EVENT_TYPES`` is a frozenset of size 7 covering exactly
  those 7 enum members.
* ``SYSTEM_ONLY_ACTORS`` is ``{SYSTEM, SCHEDULER}`` — locked.
* Constructing an ``AuditEvent`` for any Category-5 event with a
  FEISHU_USER / FRONTEND_USER / CLI actor raises ``ValidationError``.
* Constructing an ``AuditEvent`` for any Category-5 event with SYSTEM or
  SCHEDULER actor succeeds (round-tripped through the validator).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.audit.models import (
    EVOLUTION_EVENT_TYPES,
    SYSTEM_ONLY_ACTORS,
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)

# Locked: P1-6-amendment-2026-05-11-audit-eventtype-34 + P2-2 §2 red line 12.
_LOCKED_CATEGORY_5_VALUES: tuple[tuple[AuditEventType, str], ...] = (
    (AuditEventType.PROMPT_VERSION_PINNED, "prompt_version_pinned"),
    (AuditEventType.PROMPT_VERSION_ROLLED_BACK, "prompt_version_rolled_back"),
    (AuditEventType.RAG_DOCUMENT_INGESTED, "rag_document_ingested"),
    (
        AuditEventType.RAG_DOCUMENT_REJECTED_NON_WHITELIST,
        "rag_document_rejected_non_whitelist",
    ),
    (
        AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
        "shadow_evolution_run_completed",
    ),
    (AuditEventType.EVOLUTION_AMENDMENT_DRAFTED, "evolution_amendment_drafted"),
    (AuditEventType.EVOLUTION_FEISHU_NOTIFIED, "evolution_feishu_notified"),
)

_NON_SYSTEM_ACTORS: tuple[AuditActor, ...] = (
    AuditActor.FEISHU_USER,
    AuditActor.FRONTEND_USER,
    AuditActor.CLI,
)


def _make(
    *,
    event_type: AuditEventType,
    actor: AuditActor,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    resource_type: str = "evolution_prompt_version",
    payload: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        timestamp=datetime(2026, 5, 18, 22, 0, tzinfo=UTC),
        event_type=event_type,
        actor=actor,
        resource_type=resource_type,
        payload=payload or {},
        outcome=outcome,
    )


# -----------------------------------------------------------------------------
# Locked enum values
# -----------------------------------------------------------------------------


class TestCategory5EnumValues:
    """The 7 Category-5 enum values are present with their locked strings."""

    def test_category_5_size_is_seven(self) -> None:
        assert len(EVOLUTION_EVENT_TYPES) == 7

    @pytest.mark.parametrize(
        ("event_type", "expected_value"), _LOCKED_CATEGORY_5_VALUES
    )
    def test_category_5_value_locked(
        self, event_type: AuditEventType, expected_value: str
    ) -> None:
        assert event_type.value == expected_value

    def test_category_5_membership_complete(self) -> None:
        members = {event_type for event_type, _ in _LOCKED_CATEGORY_5_VALUES}
        assert members == set(EVOLUTION_EVENT_TYPES)

    def test_category_5_frozenset_is_immutable(self) -> None:
        assert isinstance(EVOLUTION_EVENT_TYPES, frozenset)
        with pytest.raises(AttributeError):
            EVOLUTION_EVENT_TYPES.add(  # type: ignore[attr-defined]
                AuditEventType.EXECUTION_REPORT_SUBMITTED
            )

    def test_category_5_disjoint_from_other_categories(self) -> None:
        # The four non-evolution categories must not leak any of the
        # seven evolution event_type names.
        non_evolution = {
            AuditEventType.EXECUTION_REPORT_SUBMITTED,
            AuditEventType.RECONCILIATION_TICKET_DECIDED,
            AuditEventType.MODE_SWITCH_INITIATED,
            AuditEventType.CREDENTIAL_ROTATED,
            AuditEventType.LLM_CALL_TIMEOUT_30S,
        }
        assert non_evolution.isdisjoint(EVOLUTION_EVENT_TYPES)


# -----------------------------------------------------------------------------
# SYSTEM_ONLY_ACTORS lock
# -----------------------------------------------------------------------------


class TestSystemOnlyActors:
    def test_size_is_two(self) -> None:
        assert len(SYSTEM_ONLY_ACTORS) == 2

    def test_members_are_system_and_scheduler(self) -> None:
        assert SYSTEM_ONLY_ACTORS == {AuditActor.SYSTEM, AuditActor.SCHEDULER}

    def test_frozenset_is_immutable(self) -> None:
        assert isinstance(SYSTEM_ONLY_ACTORS, frozenset)
        with pytest.raises(AttributeError):
            SYSTEM_ONLY_ACTORS.add(AuditActor.CLI)  # type: ignore[attr-defined]

    def test_disjoint_from_user_actors(self) -> None:
        user_actors = {
            AuditActor.FEISHU_USER,
            AuditActor.FRONTEND_USER,
            AuditActor.CLI,
        }
        assert user_actors.isdisjoint(SYSTEM_ONLY_ACTORS)


# -----------------------------------------------------------------------------
# Actor guard at construction time
# -----------------------------------------------------------------------------


class TestActorGuardOnEvolutionEvents:
    """P2-2 §2 red line 12: Category-5 actor restricted to SYSTEM/SCHEDULER."""

    @pytest.mark.parametrize(
        "event_type",
        [event_type for event_type, _ in _LOCKED_CATEGORY_5_VALUES],
    )
    @pytest.mark.parametrize("actor", _NON_SYSTEM_ACTORS)
    def test_non_system_actor_rejected(
        self, event_type: AuditEventType, actor: AuditActor
    ) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _make(event_type=event_type, actor=actor)
        # The validator's message names the offending event_type so an
        # operator searching audit logs for the rejection has a hook.
        assert event_type.value in str(exc_info.value)

    @pytest.mark.parametrize(
        "event_type",
        [event_type for event_type, _ in _LOCKED_CATEGORY_5_VALUES],
    )
    @pytest.mark.parametrize("actor", sorted(SYSTEM_ONLY_ACTORS))
    def test_system_actor_accepted(
        self, event_type: AuditEventType, actor: AuditActor
    ) -> None:
        event = _make(event_type=event_type, actor=actor)
        assert event.event_type is event_type
        assert event.actor is actor

    def test_failure_outcome_still_guarded(self) -> None:
        # Outcome=FAILURE / DEGRADED / BLOCKED must still observe the
        # actor lock (a failed shadow run from a CLI actor is still a
        # red-line violation).
        for outcome in (
            AuditOutcome.FAILURE,
            AuditOutcome.DEGRADED,
            AuditOutcome.BLOCKED,
        ):
            with pytest.raises(ValidationError):
                _make(
                    event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
                    actor=AuditActor.CLI,
                    outcome=outcome,
                )

    def test_non_evolution_event_unaffected_by_guard(self) -> None:
        # Category 1 / 4 events must still accept FEISHU_USER / CLI as
        # actor — the Category-5 guard is scoped to evolution events.
        ev = _make(
            event_type=AuditEventType.EXECUTION_REPORT_SUBMITTED,
            actor=AuditActor.FEISHU_USER,
        )
        assert ev.actor is AuditActor.FEISHU_USER

        ev2 = _make(
            event_type=AuditEventType.LLM_CALL_TIMEOUT_30S,
            actor=AuditActor.SYSTEM,
        )
        assert ev2.event_type is AuditEventType.LLM_CALL_TIMEOUT_30S
