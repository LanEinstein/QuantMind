"""X-014 — EvolutionFeishuNotifier unit tests.

Verifies render → alerter → audit chain, dedup propagation, and the
strict "no prompt text in alert body" red line (P2-2 §2).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.integrations.feishu.alerter import (
    ALERT_TYPES,
    DEFAULT_DEDUP_WINDOW,
    FeishuAlerter,
)
from backend.integrations.feishu.client import SendMessageResult
from backend.integrations.feishu.renderer import MessageRenderer
from backend.services.evolution_audit_writer import EvolutionAuditWriter
from backend.services.evolution_feishu_notifier import (
    EVOLUTION_ALERT_TYPE,
    EvolutionFeishuNotifier,
)


class FakeFeishuClient:
    """Captures ``send_message`` calls without touching the network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.next_message_id = "msg-001"

    async def send_message(
        self, chat_id: str, body: str, *, uuid: str
    ) -> SendMessageResult:
        self.calls.append((chat_id, body, uuid))
        return SendMessageResult(
            ok=True,
            code=0,
            msg="ok",
            message_id=self.next_message_id,
            log_id="log-001",
        )


@pytest.fixture
def fake_feishu() -> FakeFeishuClient:
    return FakeFeishuClient()


@pytest.fixture
def alerter(fake_feishu: FakeFeishuClient) -> FeishuAlerter:
    return FeishuAlerter(
        feishu=fake_feishu,  # type: ignore[arg-type]
        renderer=MessageRenderer(),
        alert_chat_id="oc_alert_chat",
        decision_chat_id="oc_decision_chat",
        dedup_window=timedelta(minutes=15),
    )


@pytest.fixture
def audit_writer(tmp_path: Path) -> EvolutionAuditWriter:
    store = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl")
    return EvolutionAuditWriter(store=store)


@pytest.fixture
def notifier(
    alerter: FeishuAlerter, audit_writer: EvolutionAuditWriter
) -> EvolutionFeishuNotifier:
    return EvolutionFeishuNotifier(
        alerter=alerter,
        renderer=MessageRenderer(),
        audit=audit_writer,
    )


def _baseline_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "amendment_id": "RPP-20260518-220000-000000-001",
        "artifact_type": "risk_parameter_proposal",
        "artifact_id": "RPP-20260518-220000-000000-001",
        "amendment_path": "docs/decisions/pending/RPP-20260518-220000-000000-001.md",
    }
    base.update(overrides)
    return base


class TestAlertTypeVocabulary:
    def test_alert_type_constant_locked(self) -> None:
        assert EVOLUTION_ALERT_TYPE == "evolution_amendment_drafted"

    def test_alert_type_in_alerter_vocabulary(self) -> None:
        # Without this membership the alerter would suppress every
        # evolution page as 'unknown_alert_type'.
        assert EVOLUTION_ALERT_TYPE in ALERT_TYPES


@pytest.mark.asyncio
class TestDispatch:
    async def test_happy_path_sends_and_audits(
        self,
        notifier: EvolutionFeishuNotifier,
        alerter: FeishuAlerter,
        fake_feishu: FakeFeishuClient,
        tmp_path: Path,
    ) -> None:
        result = await notifier.fire_pending(**_baseline_kwargs())
        assert result.sent is True
        assert result.suppressed is False
        assert result.reason == "dispatched"
        assert len(fake_feishu.calls) == 1
        chat_id, body, _ = fake_feishu.calls[0]
        assert chat_id == alerter.alert_chat_id
        assert "evolution_amendment_drafted" in body
        # Body must NEVER include words like 'prompt' that hint at full
        # LLM text — only identifiers + amendment path.
        assert "fund_manager_prompt_full" not in body

    async def test_audit_row_emitted(
        self,
        notifier: EvolutionFeishuNotifier,
        tmp_path: Path,
    ) -> None:
        await notifier.fire_pending(**_baseline_kwargs())
        jsonl = (tmp_path / "audit.jsonl").read_text()
        assert "evolution_feishu_notified" in jsonl
        assert "RPP-20260518-220000-000000-001" in jsonl

    async def test_dedup_suppresses_second_call(
        self,
        notifier: EvolutionFeishuNotifier,
        fake_feishu: FakeFeishuClient,
    ) -> None:
        first = await notifier.fire_pending(**_baseline_kwargs())
        second = await notifier.fire_pending(**_baseline_kwargs())
        assert first.sent is True
        assert second.sent is False
        assert second.suppressed is True
        assert second.reason == "dedup_window"
        # Audit row written for BOTH calls (the suppression is itself
        # auditable as outcome=BLOCKED).
        assert len(fake_feishu.calls) == 1

    async def test_audit_records_suppression(
        self,
        notifier: EvolutionFeishuNotifier,
        tmp_path: Path,
    ) -> None:
        await notifier.fire_pending(**_baseline_kwargs())
        await notifier.fire_pending(**_baseline_kwargs())
        lines = (tmp_path / "audit.jsonl").read_text().splitlines()
        suppress_line = [
            line for line in lines if '"suppressed":true' in line
        ]
        assert len(suppress_line) == 1
        assert '"suppression_reason":"dedup_window"' in suppress_line[0]

    async def test_artifact_type_invalid_rejected(
        self,
        notifier: EvolutionFeishuNotifier,
    ) -> None:
        with pytest.raises(ValueError):
            await notifier.fire_pending(
                **_baseline_kwargs(artifact_type="unknown")
            )

    async def test_amendment_path_outside_pending_rejected(
        self,
        notifier: EvolutionFeishuNotifier,
    ) -> None:
        with pytest.raises(ValueError):
            await notifier.fire_pending(
                **_baseline_kwargs(
                    amendment_path="docs/decisions/p0-7.md"
                )
            )

    async def test_correlation_id_propagated_to_audit(
        self,
        notifier: EvolutionFeishuNotifier,
        tmp_path: Path,
    ) -> None:
        await notifier.fire_pending(
            **_baseline_kwargs(),
            correlation_id="run-2026-05-18-22:00",
        )
        line = (tmp_path / "audit.jsonl").read_text()
        assert "run-2026-05-18-22:00" in line


@pytest.mark.asyncio
async def test_no_client_short_circuits_gracefully(
    audit_writer: EvolutionAuditWriter,
) -> None:
    alerter = FeishuAlerter(
        feishu=None,
        renderer=MessageRenderer(),
        alert_chat_id="oc_alert_chat",
    )
    notifier = EvolutionFeishuNotifier(
        alerter=alerter,
        renderer=MessageRenderer(),
        audit=audit_writer,
    )
    result = await notifier.fire_pending(**_baseline_kwargs())
    assert result.sent is False
    assert result.suppressed is True
    assert result.reason == "no_client"


def test_evolution_event_type_present() -> None:
    """Schema-level safety net — the audit enum must still expose the
    EVOLUTION_FEISHU_NOTIFIED member that EvolutionAuditWriter relies
    on. Regression alarm if the P1-6 amendment ever drops it."""
    assert AuditEventType.EVOLUTION_FEISHU_NOTIFIED.value == (
        "evolution_feishu_notified"
    )


@pytest.mark.asyncio
async def test_explicit_fired_at(
    notifier: EvolutionFeishuNotifier,
    fake_feishu: FakeFeishuClient,
) -> None:
    # 14:00 UTC == 22:00 Asia/Shanghai (the cron-scheduled wall time).
    fixed = datetime(2026, 5, 18, 14, 0, 1, tzinfo=UTC)
    await notifier.fire_pending(
        **_baseline_kwargs(),
        fired_at=fixed,
    )
    _, body, _ = fake_feishu.calls[0]
    # body header carries the fixed timestamp via render_alert in Asia/Shanghai
    assert "22:00:01" in body


@pytest.mark.asyncio
async def test_jsonl_audit_outcome_success(
    notifier: EvolutionFeishuNotifier,
    tmp_path: Path,
) -> None:
    await notifier.fire_pending(**_baseline_kwargs())
    line = (tmp_path / "audit.jsonl").read_text()
    assert '"outcome":"success"' in line
    assert '"actor":"system"' in line


@pytest.mark.asyncio
async def test_concurrent_dedup_safety(
    notifier: EvolutionFeishuNotifier,
    fake_feishu: FakeFeishuClient,
) -> None:
    # The alerter has an asyncio.Lock around cooldown writes; two
    # concurrent fires must result in exactly one dispatch.
    await asyncio.gather(
        notifier.fire_pending(**_baseline_kwargs()),
        notifier.fire_pending(**_baseline_kwargs()),
    )
    assert len(fake_feishu.calls) == 1


def test_default_dedup_window_15min() -> None:
    assert DEFAULT_DEDUP_WINDOW == timedelta(minutes=15)


@pytest.mark.asyncio
async def test_alert_outcome_is_success_audit_actor(
    notifier: EvolutionFeishuNotifier,
    tmp_path: Path,
) -> None:
    await notifier.fire_pending(**_baseline_kwargs())
    # SCHEDULER reserved for shadow_run; this notifier defaults to SYSTEM.
    line = (tmp_path / "audit.jsonl").read_text()
    assert AuditActor.SYSTEM.value in line
    assert AuditOutcome.SUCCESS.value in line
