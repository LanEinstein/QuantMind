"""Tests for backend/monitoring/alerter.py."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from backend.monitoring.alerter import ALERT_TYPES, Alerter


@pytest.mark.asyncio
async def test_webhook_called_when_url_set() -> None:
    sender = AsyncMock()
    alerter = Alerter(
        webhook_url="https://hook.example.com/alert", sender=sender
    )
    delivered = await alerter.fire(
        "cost_budget_exceeded", "Daily spend over ¥20"
    )
    assert delivered is True
    sender.assert_awaited_once()
    url, payload = sender.call_args[0]
    assert url == "https://hook.example.com/alert"
    assert payload["type"] == "cost_budget_exceeded"
    assert payload["message"] == "Daily spend over ¥20"


@pytest.mark.asyncio
async def test_no_webhook_log_only_does_not_crash() -> None:
    alerter = Alerter(webhook_url=None)
    delivered = await alerter.fire("scheduler_lag", "Watchdog tripped")
    assert delivered is True  # log-only still counts as "handled"


@pytest.mark.asyncio
async def test_cooldown_suppresses_repeat() -> None:
    sender = AsyncMock()
    alerter = Alerter(
        webhook_url="https://hook.example.com/alert",
        cooldown=timedelta(minutes=5),
        sender=sender,
    )

    first = await alerter.fire("analysis_job_failed", "Failure A")
    second = await alerter.fire("analysis_job_failed", "Failure B")

    assert first is True
    assert second is False
    assert sender.await_count == 1


@pytest.mark.asyncio
async def test_cooldown_independent_per_type() -> None:
    sender = AsyncMock()
    alerter = Alerter(
        webhook_url="https://hook.example.com/alert",
        cooldown=timedelta(minutes=5),
        sender=sender,
    )

    assert await alerter.fire("cost_budget_exceeded", "Cost") is True
    assert await alerter.fire("analysis_job_failed", "Job") is True
    # Same as cost_budget_exceeded — should be suppressed
    assert await alerter.fire("cost_budget_exceeded", "Cost 2") is False
    assert sender.await_count == 2


@pytest.mark.asyncio
async def test_reset_clears_cooldown() -> None:
    sender = AsyncMock()
    alerter = Alerter(
        webhook_url="https://hook.example.com/alert",
        cooldown=timedelta(minutes=5),
        sender=sender,
    )
    await alerter.fire("analysis_job_failed", "1")
    alerter.reset("analysis_job_failed")
    await alerter.fire("analysis_job_failed", "2")
    assert sender.await_count == 2


@pytest.mark.asyncio
async def test_delivery_exception_does_not_propagate() -> None:
    async def explode(url: str, payload: dict) -> None:
        raise RuntimeError("network down")

    alerter = Alerter(
        webhook_url="https://hook.example.com/alert", sender=explode
    )
    delivered = await alerter.fire("health_critical", "All down")
    assert delivered is False  # delivery failed, but no exception raised


def test_known_alert_types() -> None:
    assert {
        "cost_budget_exceeded",
        "scheduler_lag",
        "llm_all_providers_failed",
        "analysis_job_failed",
        "circuit_breaker_open",
    } <= ALERT_TYPES
