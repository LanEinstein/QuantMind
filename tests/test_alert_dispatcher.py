"""H-004 — AlertDispatcher tests.

Coverage:
- ALERT_MATRIX → critical fires to both audit + Feishu
- ALERT_MATRIX → soft monthly milestones fire to audit ONLY
- Unknown alert types are dropped with reason
- Feishu alerter None → audit-only graceful degradation
- Audit failure does NOT block Feishu dispatch (fail-open)
- Feishu failure does NOT block audit (fail-open)
- Decision-path alert names FORBIDDEN
- Every fire_to_feishu=True alert is also in FeishuAlerter.ALERT_TYPES
- Matrix summary is read-only / shape-stable
- AlertDispatcher does NOT compose buy/sell/recon/clarification text
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.audit.models import (
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)
from backend.integrations.feishu.alerter import (
    ALERT_TYPES as FEISHU_ALERT_TYPES,
)
from backend.integrations.feishu.alerter import AlertResult
from backend.monitoring.alert_dispatcher import (
    ALERT_MATRIX,
    AlertDispatcher,
    DispatchResult,
    _assert_feishu_subset,
    assert_no_decision_path_alerts,
    matrix_summary,
)


@pytest.fixture
def fake_audit() -> MagicMock:
    audit = MagicMock()
    audit.write = AsyncMock(
        return_value=AuditEvent(
            timestamp=datetime(2026, 5, 16, tzinfo=UTC),
            event_type=AuditEventType.SYSTEM_INTERRUPTED,
            actor=AuditActor.SYSTEM,
            resource_type="x",
            outcome=AuditOutcome.DEGRADED,
        )
    )
    return audit


@pytest.fixture
def fake_feishu() -> MagicMock:
    feishu = MagicMock()
    feishu.fire = AsyncMock(
        return_value=AlertResult(
            sent=True,
            suppressed=False,
            send_result=None,
            reason="dispatched",
        )
    )
    return feishu


# ----------------------------------------------------------------------
# Matrix invariants
# ----------------------------------------------------------------------


def test_matrix_count_is_locked() -> None:
    # 5 cost types + 4 P0-6 interruptions + 3 ops lifecycle + 1 P2-2 +
    # 1 J-004 acceptance reset trigger.
    assert len(ALERT_MATRIX) == 14


def test_every_feishu_fire_is_in_feishu_alert_types() -> None:
    # Self-check helper raises on mismatch.
    _assert_feishu_subset()


def test_no_decision_path_alerts_leaked() -> None:
    assert_no_decision_path_alerts()


def test_monthly_milestones_are_audit_only() -> None:
    for pct in (50, 80, 100):
        spec = ALERT_MATRIX[f"monthly_budget_{pct}pct_reached"]
        assert spec.fire_to_feishu is False, (
            f"monthly {pct}pct must NOT fire to Feishu — audit only "
            "(P1-7 §1.7 / CLAUDE.md §2.10)"
        )


def test_daily_hard_breach_fires_to_feishu() -> None:
    spec = ALERT_MATRIX["daily_cost_ceiling_20cny_breached"]
    assert spec.fire_to_feishu is True
    assert spec.severity == "critical"


def test_kimi_cap_fires_to_feishu_but_not_critical() -> None:
    spec = ALERT_MATRIX["kimi_daily_cap_4cny_breached"]
    assert spec.fire_to_feishu is True
    # Kimi cap only stops Kimi; not a full LLM halt → warning, not critical
    assert spec.severity == "warning"


def test_matrix_summary_shape_is_stable() -> None:
    rows = matrix_summary()
    assert isinstance(rows, list)
    assert len(rows) == 14
    required_keys = {
        "alert_type",
        "audit_event_type",
        "fire_to_feishu",
        "severity",
        "reason_namespace",
        "description",
    }
    for row in rows:
        assert required_keys.issubset(row.keys())


# ----------------------------------------------------------------------
# AlertDispatcher.fire — critical path
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_critical_alert_fires_audit_and_feishu(
    fake_audit: MagicMock, fake_feishu: MagicMock
) -> None:
    dispatcher = AlertDispatcher(audit=fake_audit, feishu_alerter=fake_feishu)
    result = await dispatcher.fire(
        alert_type="daily_cost_ceiling_20cny_breached",
        message="daily budget exceeded",
        payload={"spent": 20.5},
    )
    assert result.audit_written is True
    assert result.feishu_sent is True
    assert result.feishu_suppressed is False
    fake_audit.write.assert_awaited_once()
    fake_feishu.fire.assert_awaited_once()
    # Severity propagated to Feishu
    assert (
        fake_feishu.fire.await_args.kwargs["severity"] == "critical"
    )


@pytest.mark.asyncio
async def test_soft_monthly_alert_is_audit_only(
    fake_audit: MagicMock, fake_feishu: MagicMock
) -> None:
    dispatcher = AlertDispatcher(audit=fake_audit, feishu_alerter=fake_feishu)
    result = await dispatcher.fire(
        alert_type="monthly_budget_50pct_reached",
        message="month at 50%",
    )
    assert result.audit_written is True
    assert result.feishu_sent is False
    assert result.reason == "audit_only"
    fake_feishu.fire.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_alert_type_dropped(
    fake_audit: MagicMock, fake_feishu: MagicMock
) -> None:
    dispatcher = AlertDispatcher(audit=fake_audit, feishu_alerter=fake_feishu)
    result = await dispatcher.fire(
        alert_type="instruction_dispatched",  # decision path — forbidden
        message="x",
    )
    assert result.audit_written is False
    assert result.feishu_sent is False
    assert result.reason == "unknown_alert_type"
    fake_audit.write.assert_not_awaited()
    fake_feishu.fire.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_feishu_client_degrades_to_audit_only(
    fake_audit: MagicMock,
) -> None:
    dispatcher = AlertDispatcher(audit=fake_audit, feishu_alerter=None)
    result = await dispatcher.fire(
        alert_type="daily_cost_ceiling_20cny_breached",
        message="x",
    )
    assert result.audit_written is True
    assert result.feishu_sent is False
    assert result.reason == "no_feishu_client"


@pytest.mark.asyncio
async def test_audit_failure_is_fail_open(fake_feishu: MagicMock) -> None:
    audit = MagicMock()
    audit.write = AsyncMock(side_effect=RuntimeError("mongo + jsonl both down"))
    dispatcher = AlertDispatcher(audit=audit, feishu_alerter=fake_feishu)
    result = await dispatcher.fire(
        alert_type="daily_cost_ceiling_20cny_breached",
        message="x",
    )
    # Audit failed but Feishu dispatch still goes through.
    assert result.audit_written is False
    assert result.feishu_sent is True


@pytest.mark.asyncio
async def test_feishu_failure_is_fail_open(
    fake_audit: MagicMock,
) -> None:
    feishu = MagicMock()
    feishu.fire = AsyncMock(side_effect=RuntimeError("openapi 500"))
    dispatcher = AlertDispatcher(audit=fake_audit, feishu_alerter=feishu)
    result = await dispatcher.fire(
        alert_type="daily_cost_ceiling_20cny_breached",
        message="x",
    )
    # Audit still wrote, Feishu failed.
    assert result.audit_written is True
    assert result.feishu_sent is False
    assert result.reason == "feishu_send_failed"


@pytest.mark.asyncio
async def test_feishu_suppressed_dedup_propagates(
    fake_audit: MagicMock,
) -> None:
    feishu = MagicMock()
    feishu.fire = AsyncMock(
        return_value=AlertResult(
            sent=False,
            suppressed=True,
            send_result=None,
            reason="dedup_window",
        )
    )
    dispatcher = AlertDispatcher(audit=fake_audit, feishu_alerter=feishu)
    result = await dispatcher.fire(
        alert_type="circuit_breaker_open",
        message="cooldown 60min",
        dedup_key="single_instance",
    )
    assert result.audit_written is True
    assert result.feishu_sent is False
    assert result.feishu_suppressed is True
    assert result.reason == "dedup_window"


@pytest.mark.asyncio
async def test_payload_carries_severity_and_message(
    fake_audit: MagicMock, fake_feishu: MagicMock
) -> None:
    dispatcher = AlertDispatcher(audit=fake_audit, feishu_alerter=fake_feishu)
    await dispatcher.fire(
        alert_type="kimi_daily_cap_4cny_breached",
        message="kimi cap reached",
        payload={"agent": "risk_officer"},
    )
    call_kwargs = fake_audit.write.await_args.kwargs
    payload = call_kwargs["payload"]
    assert payload["severity"] == "warning"
    assert payload["alert_type"] == "kimi_daily_cap_4cny_breached"
    assert payload["message"] == "kimi cap reached"
    assert payload["agent"] == "risk_officer"


@pytest.mark.asyncio
async def test_dispatch_result_isinstance() -> None:
    audit = MagicMock()
    audit.write = AsyncMock(
        return_value=AuditEvent(
            timestamp=datetime(2026, 5, 16, tzinfo=UTC),
            event_type=AuditEventType.SYSTEM_INTERRUPTED,
            actor=AuditActor.SYSTEM,
            resource_type="x",
            outcome=AuditOutcome.DEGRADED,
        )
    )
    dispatcher = AlertDispatcher(audit=audit, feishu_alerter=None)
    result = await dispatcher.fire(
        alert_type="monthly_budget_50pct_reached", message="x"
    )
    assert isinstance(result, DispatchResult)


# ----------------------------------------------------------------------
# Source-level guarantee: dispatcher does NOT call MessageRenderer for
# buy/sell/clarification/recon templates. The renderer is only available
# to FeishuMessenger (F-002 / F-004 / F-005). Static check.
# ----------------------------------------------------------------------


def test_dispatcher_does_not_import_messenger_or_renderer_paths() -> None:
    source = Path("backend/monitoring/alert_dispatcher.py").read_text(
        encoding="utf-8"
    )
    forbidden_phrases = [
        # Decision-path artefacts that MUST not pass through this module.
        "feishu_messenger",
        "render_buy_template",
        "render_sell_template",
        "render_reconciliation",
        "render_clarification",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in source.lower(), (
            f"alert_dispatcher.py contains forbidden symbol {phrase!r} "
            "(decision-path / buy-sell text composition)"
        )


def test_feishu_alert_types_unchanged_count() -> None:
    """If FeishuAlerter.ALERT_TYPES count changes, force a matrix review.

    J-004 added one entry (acceptance_reset_triggered) bringing the
    total to 14. CLAUDE.md §2.11 maintains the locked vocabulary.
    """
    assert len(FEISHU_ALERT_TYPES) == 14
