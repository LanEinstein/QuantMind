"""X-015 — EvolutionAuditWriter unit tests.

Covers the 7 Category-5 emissions, actor=SYSTEM/SCHEDULER guard,
non-evolution event-type rejection, and payload shape.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.audit.models import (
    EVOLUTION_EVENT_TYPES,
    AuditActor,
    AuditEventType,
    AuditOutcome,
)
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.services.evolution_audit_writer import (
    _SCHEDULER_EVENTS,
    REASON_NAMESPACE,
    EvolutionAuditWriter,
    _default_actor_for,
)


@pytest.fixture
def writer(tmp_path: Path) -> EvolutionAuditWriter:
    mongo = InMemoryAuditCollection()
    store = AuditStore(mongo, jsonl_path=tmp_path / "audit.jsonl")
    return EvolutionAuditWriter(store=store)


class TestSchedulerEventClassification:
    def test_seven_evolution_event_types_covered(self) -> None:
        # the wrapper must be able to emit every Category-5 event
        assert len(EVOLUTION_EVENT_TYPES) == 7

    def test_only_shadow_run_is_scheduler_actor(self) -> None:
        assert _SCHEDULER_EVENTS == {
            AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED
        }
        assert (
            _default_actor_for(AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED)
            == AuditActor.SCHEDULER
        )
        for et in EVOLUTION_EVENT_TYPES - _SCHEDULER_EVENTS:
            assert _default_actor_for(et) == AuditActor.SYSTEM


@pytest.mark.asyncio
class TestSevenEmissions:
    async def test_prompt_version_pinned(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.prompt_version_pinned(
            agent="fundamental_analyst",
            version_tag="v3",
            sha256="a" * 64,
            pinned_by="owner",
        )
        assert event.event_type == AuditEventType.PROMPT_VERSION_PINNED
        assert event.actor == AuditActor.SYSTEM
        assert event.resource_type == "prompt_version"
        assert event.resource_id == "fundamental_analyst:v3"
        assert event.payload["sha256"] == "a" * 64
        assert event.reason_namespace == REASON_NAMESPACE

    async def test_prompt_version_rolled_back(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.prompt_version_rolled_back(
            agent="fund_manager",
            from_version="v4",
            to_version="v3",
            reason="shadow regression",
        )
        assert event.event_type == AuditEventType.PROMPT_VERSION_ROLLED_BACK
        assert "v4->v3" in event.resource_id

    async def test_rag_document_ingested(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.rag_document_ingested(
            doc_id="ARXIV-2509.13196",
            source="arxiv",
            content_sha256="b" * 64,
            whitelist_rule_version="v1.0",
        )
        assert event.event_type == AuditEventType.RAG_DOCUMENT_INGESTED
        assert event.resource_id == "ARXIV-2509.13196"
        assert event.outcome == AuditOutcome.SUCCESS

    async def test_rag_document_rejected_non_whitelist(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.rag_document_rejected_non_whitelist(
            attempted_source="medium.com",
            url="https://medium.com/some-post",
            reason="source not in whitelist",
        )
        assert event.outcome == AuditOutcome.BLOCKED
        assert (
            event.event_type
            == AuditEventType.RAG_DOCUMENT_REJECTED_NON_WHITELIST
        )

    async def test_shadow_evolution_run_completed_passed(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.shadow_evolution_run_completed(
            challenger_artifact_id="PROMPT-fund_manager-v4",
            champion_baseline_id="PROMPT-fund_manager-v3",
            passed=True,
            metrics_summary={"pnl_cny": 12345.6},
        )
        assert event.event_type == AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED
        assert event.actor == AuditActor.SCHEDULER
        assert event.outcome == AuditOutcome.SUCCESS

    async def test_shadow_evolution_run_completed_failed(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.shadow_evolution_run_completed(
            challenger_artifact_id="X",
            champion_baseline_id="Y",
            passed=False,
            metrics_summary={},
        )
        assert event.outcome == AuditOutcome.FAILURE

    async def test_evolution_amendment_drafted(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.evolution_amendment_drafted(
            amendment_id="RPP-20260518-220000-000000-001",
            artifact_type="risk_parameter_proposal",
            artifact_id="RPP-20260518-220000-000000-001",
            amendment_path="docs/decisions/pending/RPP-20260518-220000-000000-001.md",
        )
        assert event.event_type == AuditEventType.EVOLUTION_AMENDMENT_DRAFTED
        assert (
            event.payload["amendment_path"]
            == "docs/decisions/pending/RPP-20260518-220000-000000-001.md"
        )

    async def test_evolution_feishu_notified_success(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.evolution_feishu_notified(
            amendment_id="A1",
            chat_id="oc_chat",
            message_uuid="alert-evolution-A1-2026",
        )
        assert event.outcome == AuditOutcome.SUCCESS

    async def test_evolution_feishu_notified_suppressed(
        self, writer: EvolutionAuditWriter
    ) -> None:
        event = await writer.evolution_feishu_notified(
            amendment_id="A1",
            chat_id="oc_chat",
            message_uuid="alert-evolution-A1-2026",
            suppressed=True,
            suppression_reason="dedup_window",
        )
        assert event.outcome == AuditOutcome.BLOCKED
        assert event.payload["suppression_reason"] == "dedup_window"


@pytest.mark.asyncio
class TestActorGuard:
    async def test_non_evolution_event_rejected(
        self, writer: EvolutionAuditWriter
    ) -> None:
        with pytest.raises(ValueError, match="Category-5"):
            await writer._emit(
                event_type=AuditEventType.MODE_SWITCH_INITIATED,
                resource_type="run_mode",
                resource_id="x",
                payload={},
            )

    async def test_llm_actor_rejected(
        self, writer: EvolutionAuditWriter
    ) -> None:
        with pytest.raises(ValueError, match="actor"):
            await writer._emit(
                event_type=AuditEventType.PROMPT_VERSION_PINNED,
                resource_type="prompt_version",
                resource_id="x",
                payload={},
                actor=AuditActor.FRONTEND_USER,
            )

    async def test_feishu_actor_rejected(
        self, writer: EvolutionAuditWriter
    ) -> None:
        with pytest.raises(ValueError, match="actor"):
            await writer._emit(
                event_type=AuditEventType.PROMPT_VERSION_PINNED,
                resource_type="prompt_version",
                resource_id="x",
                payload={},
                actor=AuditActor.FEISHU_USER,
            )


@pytest.mark.asyncio
async def test_jsonl_double_write(
    tmp_path: Path,
) -> None:
    mongo = InMemoryAuditCollection()
    store = AuditStore(mongo, jsonl_path=tmp_path / "audit.jsonl")
    writer = EvolutionAuditWriter(store=store)

    await writer.prompt_version_pinned(
        agent="fund_manager",
        version_tag="v1",
        sha256="c" * 64,
        pinned_by="owner",
    )
    # JSONL + Mongo both received the row.
    jsonl_content = (tmp_path / "audit.jsonl").read_text()
    assert "prompt_version_pinned" in jsonl_content
    assert len(mongo.documents) == 1


@pytest.mark.asyncio
async def test_correlation_id_propagated(
    writer: EvolutionAuditWriter,
) -> None:
    event = await writer.prompt_version_pinned(
        agent="risk_officer",
        version_tag="v2",
        sha256="d" * 64,
        pinned_by="owner",
        correlation_id="run-2026-05-18",
    )
    assert event.correlation_id == "run-2026-05-18"


@pytest.mark.asyncio
async def test_explicit_timestamp_honoured(
    writer: EvolutionAuditWriter,
) -> None:
    fixed = datetime(2026, 5, 18, 22, 0, tzinfo=UTC)
    event = await writer.shadow_evolution_run_completed(
        challenger_artifact_id="C",
        champion_baseline_id="B",
        passed=True,
        metrics_summary={},
        correlation_id=None,
    )
    # default-now timestamp
    assert event.timestamp.tzinfo is not None
    fixed_event = await writer._emit(
        event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
        resource_type="shadow_evolution_run",
        resource_id="C",
        payload={},
        timestamp=fixed,
    )
    assert fixed_event.timestamp == fixed
