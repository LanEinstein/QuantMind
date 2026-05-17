"""J-004 — Unit + integration tests for the reset trigger detector.

Coverage:

* 5 trigger types each detected at threshold and skipped below.
* Each fire calls :meth:`AcceptanceService.record_reset` and
  :meth:`AlertDispatcher.fire` with the locked alert_type.
* Audit event lands as SYSTEM_INTERRUPTED + reason_namespace
  ``acceptance_reset_trigger`` carrying the trigger sub-type.
* Reconciliation freeze is a no-op (does NOT zero counter).
* :meth:`AcceptanceService.compute` clamps window_start when reset
  fired, producing INSUFFICIENT_DATA even after 45+ real trading
  days.
* :data:`ALERT_MATRIX` and :data:`FEISHU_ALERT_TYPES` both include
  ``acceptance_reset_triggered``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

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
from backend.monitoring.alert_dispatcher import (
    ALERT_MATRIX,
    AlertDispatcher,
)
from backend.services.acceptance_report import (
    AcceptanceComputeInput,
    AcceptanceOutcome,
    AcceptanceService,
    InMemoryAcceptanceRepository,
    StabilityCounters,
    StrategyCounters,
    WindowResetState,
)
from backend.services.reset_trigger_detector import (
    LLM_FULL_STOP_THRESHOLD,
    LONG_CONN_OUTAGE_THRESHOLD,
    MARKET_DATA_OUTAGE_THRESHOLD,
    RESET_ALERT_TYPE,
    ResetTriggerDetector,
    ResetTriggerType,
)

# ---------------------------------------------------------------------------
# Recording stubs
# ---------------------------------------------------------------------------


class _RecordingAuditStore:
    """Captures every ``AuditStore.write`` call without touching IO."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def write(
        self,
        *,
        event_type: AuditEventType,
        actor: AuditActor,
        actor_detail: str | None = None,
        resource_type: str,
        resource_id: str | None = None,
        payload: dict[str, Any],
        outcome: AuditOutcome,
        correlation_id: str | None = None,
        reason_namespace: str | None = None,
        timestamp: dt.datetime | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            timestamp=timestamp or dt.datetime.now(dt.UTC),
            event_type=event_type,
            actor=actor,
            actor_detail=actor_detail,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload,
            outcome=outcome,
            correlation_id=correlation_id,
            reason_namespace=reason_namespace,
        )
        self.events.append(event)
        return event

    # Read-only convenience accessors used by tests.
    def by_reason(self, namespace: str) -> list[AuditEvent]:
        return [e for e in self.events if e.reason_namespace == namespace]


class _RecordingFeishuAlerter:
    """Captures every ``FeishuAlerter.fire`` call."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fire(
        self,
        *,
        alert_type: str,
        severity: str,
        message: str,
        dedup_key: str = "",
        fired_at: dt.datetime | None = None,
    ) -> Any:
        record = {
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
            "dedup_key": dedup_key,
            "fired_at": fired_at,
        }
        self.calls.append(record)
        # Mimic AlertResult shape used by AlertDispatcher.
        return _StubAlertResult(sent=True, suppressed=False, reason="ok")


class _StubAlertResult:
    def __init__(self, *, sent: bool, suppressed: bool, reason: str) -> None:
        self.sent = sent
        self.suppressed = suppressed
        self.reason = reason


def _make_dispatcher() -> tuple[
    AlertDispatcher, _RecordingAuditStore, _RecordingFeishuAlerter
]:
    audit = _RecordingAuditStore()
    feishu = _RecordingFeishuAlerter()
    dispatcher = AlertDispatcher(audit=audit, feishu_alerter=feishu)
    return dispatcher, audit, feishu


def _make_detector(
    *,
    repository: InMemoryAcceptanceRepository | None = None,
) -> tuple[
    ResetTriggerDetector,
    AcceptanceService,
    _RecordingAuditStore,
    _RecordingFeishuAlerter,
]:
    dispatcher, audit, feishu = _make_dispatcher()
    service = AcceptanceService(
        repository=repository or InMemoryAcceptanceRepository()
    )
    detector = ResetTriggerDetector(
        alert_dispatcher=dispatcher,
        acceptance_service=service,
    )
    return detector, service, audit, feishu


# ---------------------------------------------------------------------------
# Locked vocabulary
# ---------------------------------------------------------------------------


def test_alert_type_present_in_dispatcher_matrix() -> None:
    spec = ALERT_MATRIX[RESET_ALERT_TYPE]
    assert spec.fire_to_feishu is True
    assert spec.audit_event_type is AuditEventType.SYSTEM_INTERRUPTED
    assert spec.reason_namespace == "acceptance_reset_trigger"


def test_alert_type_present_in_feishu_alerter_whitelist() -> None:
    assert RESET_ALERT_TYPE in FEISHU_ALERT_TYPES


def test_reset_trigger_type_enum_count_locked() -> None:
    assert len({t for t in ResetTriggerType}) == 5


# ---------------------------------------------------------------------------
# 5 trigger detection paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_market_data_outage_below_threshold_no_fire() -> None:
    detector, _, audit, feishu = _make_detector()
    started = dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC)
    observed = started + (MARKET_DATA_OUTAGE_THRESHOLD - dt.timedelta(seconds=1))
    event = await detector.notify_market_data_outage(
        started_at=started, observed_at=observed
    )
    assert event is None
    assert audit.events == []
    assert feishu.calls == []


@pytest.mark.asyncio
async def test_market_data_outage_at_threshold_fires() -> None:
    detector, service, audit, feishu = _make_detector()
    started = dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC)
    observed = started + MARKET_DATA_OUTAGE_THRESHOLD
    event = await detector.notify_market_data_outage(
        started_at=started, observed_at=observed
    )
    assert event is not None
    assert event.trigger_type is ResetTriggerType.MARKET_DATA_OUTAGE_30MIN
    assert event.payload["trigger_type"] == "MARKET_DATA_OUTAGE_30MIN"
    assert len(audit.by_reason("acceptance_reset_trigger")) == 1
    assert len(feishu.calls) == 1
    assert feishu.calls[0]["alert_type"] == RESET_ALERT_TYPE
    # AcceptanceService.record_reset was called.
    state = service.reset_state()
    assert state.last_reset_at == observed
    assert state.last_reset_reason == "MARKET_DATA_OUTAGE_30MIN"


@pytest.mark.asyncio
async def test_llm_full_stop_below_threshold_no_fire() -> None:
    detector, _, audit, feishu = _make_detector()
    started = dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC)
    observed = started + (LLM_FULL_STOP_THRESHOLD - dt.timedelta(seconds=1))
    event = await detector.notify_llm_full_stop(
        started_at=started, observed_at=observed
    )
    assert event is None


@pytest.mark.asyncio
async def test_llm_full_stop_at_threshold_fires() -> None:
    detector, service, audit, feishu = _make_detector()
    started = dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC)
    observed = started + LLM_FULL_STOP_THRESHOLD
    event = await detector.notify_llm_full_stop(
        started_at=started, observed_at=observed
    )
    assert event is not None
    assert event.trigger_type is ResetTriggerType.LLM_FULL_STOP_1H
    assert service.reset_state().last_reset_reason == "LLM_FULL_STOP_1H"


@pytest.mark.asyncio
async def test_mock_broker_corruption_fires_immediately() -> None:
    detector, service, audit, feishu = _make_detector()
    when = dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC)
    event = await detector.notify_mock_broker_corruption(
        observed_at=when, detail="checksum mismatch on broker snapshot"
    )
    assert event.trigger_type is ResetTriggerType.MOCK_BROKER_CORRUPTION
    assert event.payload["detail"] == "checksum mismatch on broker snapshot"
    assert len(audit.events) == 1
    audit_event = audit.events[0]
    assert audit_event.event_type is AuditEventType.SYSTEM_INTERRUPTED
    assert audit_event.reason_namespace == "acceptance_reset_trigger"
    assert audit_event.payload["trigger_type"] == "MOCK_BROKER_CORRUPTION"


@pytest.mark.asyncio
async def test_state_machine_illegal_transition_fires_immediately() -> None:
    detector, service, audit, feishu = _make_detector()
    when = dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC)
    event = await detector.notify_state_machine_illegal_transition(
        observed_at=when,
        instruction_id="QM-20260517-100000-600519-BUY-001",
        from_state="DISPATCHED",
        to_state="VALIDATED",
    )
    assert event.trigger_type is ResetTriggerType.STATE_MACHINE_ILLEGAL_TRANSITION
    assert event.payload["from_state"] == "DISPATCHED"
    assert event.payload["to_state"] == "VALIDATED"
    assert event.payload["instruction_id"] == "QM-20260517-100000-600519-BUY-001"
    assert service.reset_state().last_reset_reason == (
        "STATE_MACHINE_ILLEGAL_TRANSITION"
    )


@pytest.mark.asyncio
async def test_long_conn_outage_below_threshold_no_fire() -> None:
    detector, _, audit, feishu = _make_detector()
    started = dt.datetime(2026, 5, 17, 6, 0, tzinfo=dt.UTC)
    observed = started + (LONG_CONN_OUTAGE_THRESHOLD - dt.timedelta(seconds=1))
    event = await detector.notify_long_conn_outage(
        started_at=started, observed_at=observed
    )
    assert event is None


@pytest.mark.asyncio
async def test_long_conn_outage_at_threshold_fires() -> None:
    detector, service, audit, feishu = _make_detector()
    started = dt.datetime(2026, 5, 17, 6, 0, tzinfo=dt.UTC)
    observed = started + LONG_CONN_OUTAGE_THRESHOLD
    event = await detector.notify_long_conn_outage(
        started_at=started, observed_at=observed
    )
    assert event is not None
    assert event.trigger_type is ResetTriggerType.LONG_CONN_OUTAGE_4H
    assert service.reset_state().last_reset_reason == "LONG_CONN_OUTAGE_4H"


# ---------------------------------------------------------------------------
# Reconciliation freeze — explicit no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_freeze_is_no_op() -> None:
    detector, service, audit, feishu = _make_detector()
    await detector.notify_reconciliation_freeze(
        observed_at=dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC),
        ticket_id="RECON-20260517-001",
    )
    # No audit, no Feishu, no reset record.
    assert audit.events == []
    assert feishu.calls == []
    assert service.reset_state() == WindowResetState()


# ---------------------------------------------------------------------------
# AcceptanceService.compute clamping behaviour
# ---------------------------------------------------------------------------


def _passing_stability() -> StabilityCounters:
    return StabilityCounters(
        completed_instructions=99,
        total_instructions=100,
        accurate_reports=199,
        total_reports=200,
        data_missing_ticks=1,
        total_data_ticks=10_000,
        llm_timeout_calls=1,
        total_llm_calls=10_000,
        generated_signal_days=44,
        expected_signal_days=45,
    )


def _passing_strategy() -> StrategyCounters:
    return StrategyCounters(
        max_drawdown_pct=0.05,
        pnl_cny=20_000.0,
        csi300_excess_pct=0.02,
    )


def test_compute_without_reset_emits_pass_when_metrics_clean() -> None:
    service = AcceptanceService()
    report = service.compute(
        AcceptanceComputeInput(
            trade_date=dt.date(2026, 6, 12),
            now=dt.datetime(2026, 6, 12, 16, 0, 30, tzinfo=dt.UTC),
            stability=_passing_stability(),
            strategy=_passing_strategy(),
        )
    )
    assert report.outcome is AcceptanceOutcome.PASS
    assert report.reset_state == WindowResetState()


def test_compute_after_record_reset_clamps_to_insufficient_data() -> None:
    """A reset 3 days before trade_date forces INSUFFICIENT_DATA."""
    service = AcceptanceService()
    service.record_reset(
        when=dt.datetime(2026, 6, 9, 11, 0, tzinfo=dt.UTC),
        reason="LLM_FULL_STOP_1H",
    )
    report = service.compute(
        AcceptanceComputeInput(
            trade_date=dt.date(2026, 6, 12),
            now=dt.datetime(2026, 6, 12, 16, 0, 30, tzinfo=dt.UTC),
            stability=_passing_stability(),
            strategy=_passing_strategy(),
        )
    )
    assert report.outcome is AcceptanceOutcome.INSUFFICIENT_DATA
    assert report.trading_days_in_window < 45
    assert report.reset_state.last_reset_reason == "LLM_FULL_STOP_1H"
    assert report.window_start == "2026-06-09"


def test_reconciliation_freeze_pauses_does_not_zero_counter() -> None:
    """reconciliation_paused=True PAUSES the window; counter preserved.

    Counter preservation means: after a freeze, the report is PAUSED
    (not RESET) and ``reset_state`` remains empty unless the operator
    separately invokes ``record_reset``. This guards the
    "freeze != reset" invariant in P0-6 §1.
    """
    service = AcceptanceService()
    report = service.compute(
        AcceptanceComputeInput(
            trade_date=dt.date(2026, 6, 12),
            now=dt.datetime(2026, 6, 12, 16, 0, 30, tzinfo=dt.UTC),
            stability=_passing_stability(),
            strategy=_passing_strategy(),
            reconciliation_paused=True,
        )
    )
    assert report.outcome is AcceptanceOutcome.PAUSED
    assert report.reset_state == WindowResetState()


def test_record_reset_rejects_naive_datetime() -> None:
    service = AcceptanceService()
    with pytest.raises(ValueError, match="aware datetime"):
        service.record_reset(
            when=dt.datetime(2026, 6, 9, 11, 0),  # naive
            reason="X",
        )


def test_record_reset_rejects_empty_or_overlong_reason() -> None:
    service = AcceptanceService()
    with pytest.raises(ValueError):
        service.record_reset(
            when=dt.datetime(2026, 6, 9, 11, 0, tzinfo=dt.UTC),
            reason="",
        )
    with pytest.raises(ValueError):
        service.record_reset(
            when=dt.datetime(2026, 6, 9, 11, 0, tzinfo=dt.UTC),
            reason="x" * 65,
        )


# ---------------------------------------------------------------------------
# Audit + dispatch payload schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_event_payload_has_required_keys() -> None:
    detector, _, audit, _ = _make_detector()
    when = dt.datetime(2026, 5, 17, 10, 30, tzinfo=dt.UTC)
    started = when - dt.timedelta(hours=1)
    await detector.notify_llm_full_stop(started_at=started, observed_at=when)
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.event_type is AuditEventType.SYSTEM_INTERRUPTED
    assert event.reason_namespace == "acceptance_reset_trigger"
    assert event.resource_type == "acceptance_window"
    assert event.resource_id == "LLM_FULL_STOP_1H"
    payload = event.payload
    assert payload["trigger_type"] == "LLM_FULL_STOP_1H"
    assert payload["alert_type"] == RESET_ALERT_TYPE
    assert payload["severity"] == "critical"
    assert "elapsed_seconds" in payload
    assert "threshold_seconds" in payload


@pytest.mark.asyncio
async def test_dedup_key_is_trigger_type_only() -> None:
    """Codex cycle 1 P2 fix — same trigger fired again within the 15-min
    FeishuAlerter dedup window must collapse into a single message."""
    detector, _, _, feishu = _make_detector()
    when_a = dt.datetime(2026, 5, 17, 10, 30, tzinfo=dt.UTC)
    when_b = dt.datetime(2026, 5, 17, 10, 31, tzinfo=dt.UTC)
    started = when_a - dt.timedelta(hours=1)
    await detector.notify_llm_full_stop(started_at=started, observed_at=when_a)
    await detector.notify_llm_full_stop(started_at=started, observed_at=when_b)
    assert feishu.calls[0]["dedup_key"] == "LLM_FULL_STOP_1H"
    assert feishu.calls[1]["dedup_key"] == "LLM_FULL_STOP_1H"
    # Distinct triggers get distinct dedup_keys.
    await detector.notify_mock_broker_corruption(
        observed_at=when_a, detail="x"
    )
    assert feishu.calls[2]["dedup_key"] == "MOCK_BROKER_CORRUPTION"


# ---------------------------------------------------------------------------
# Silence the AsyncIterator unused-import warning
# ---------------------------------------------------------------------------


_ = AsyncIterator


# ---------------------------------------------------------------------------
# Codex cycle 4 regressions — naive datetime + audit_written=False paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_rejects_naive_datetime_before_dispatching() -> None:
    """Codex cycle 4 P2 — naive ``observed_at`` must raise BEFORE any
    audit / Feishu / record_reset side effects happen."""
    detector, service, audit, feishu = _make_detector()
    naive_now = dt.datetime(2026, 5, 17, 10, 0)  # naive
    # Use mock_broker_corruption since it fires immediately (no
    # threshold short-circuit) and goes straight through _fire().
    with pytest.raises(ValueError, match="naive datetime"):
        await detector.notify_mock_broker_corruption(
            observed_at=naive_now, detail="x"
        )
    assert audit.events == []
    assert feishu.calls == []
    assert service.reset_state() == WindowResetState()


@pytest.mark.asyncio
async def test_fire_raises_when_audit_write_fails_and_skips_record_reset() -> None:
    """Codex cycle 3 P1 regression — when AlertDispatcher returns
    audit_written=False, _fire must raise + must NOT touch
    record_reset (otherwise the in-process clamp diverges from the
    durable audit trail)."""

    # Build a dispatcher whose audit store always fails.
    class _FailingAuditStore:
        async def write(self, **kwargs: Any) -> AuditEvent:
            raise RuntimeError("simulated audit failure")

    audit = _FailingAuditStore()
    feishu = _RecordingFeishuAlerter()
    dispatcher = AlertDispatcher(audit=audit, feishu_alerter=feishu)
    service = AcceptanceService(repository=InMemoryAcceptanceRepository())
    detector = ResetTriggerDetector(
        alert_dispatcher=dispatcher,
        acceptance_service=service,
    )
    when = dt.datetime(2026, 5, 17, 10, 0, tzinfo=dt.UTC)
    with pytest.raises(RuntimeError, match="audit write failed"):
        await detector.notify_mock_broker_corruption(
            observed_at=when, detail="x"
        )
    # Reset state stayed empty — no in-process clamp without durable audit.
    assert service.reset_state() == WindowResetState()
