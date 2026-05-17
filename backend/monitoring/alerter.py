"""Webhook alerter for evaluation-period events.

Emits structured alert payloads to a configurable webhook URL. Designed
to be safe when the webhook is unset (log-only mode) and to throttle
repeated alerts within a cooldown window so a sustained failure does
not spam the channel.

Wired into the monitoring dashboard, LLM preflight path (Session D.1),
circuit-breaker events, and scheduler watchdog. Each alert *type* gets
its own cooldown timer; distinct types fire independently.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

log = structlog.get_logger(component="alerter")

ALERT_TYPES = frozenset(
    {
        "cost_budget_exceeded",
        "scheduler_lag",
        "llm_all_providers_failed",
        "analysis_job_failed",
        "circuit_breaker_open",
        "backup_failed",
        "health_critical",
    }
)

AlertSender = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class AlertEvent:
    """Structured alert record for webhook + log output."""

    type: str
    message: str
    severity: str = "warning"
    context: dict[str, Any] = field(default_factory=dict)
    fired_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def as_payload(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "message": self.message,
            "severity": self.severity,
            "context": self.context,
            "fired_at": self.fired_at.isoformat(),
            "source": "quantmind",
        }


class Alerter:
    """Rate-limited webhook alerter.

    Args:
        webhook_url: POST target. When empty or None, alerts are only
            logged (safe default for dev/evaluation kickoff).
        cooldown: Minimum interval between repeat fires of the same type.
        sender: Override for HTTP delivery (used by tests). Signature
            ``async (url, payload) -> None``.
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        *,
        cooldown: timedelta = timedelta(minutes=15),
        sender: AlertSender | None = None,
    ) -> None:
        self._webhook_url = webhook_url or os.environ.get(
            "ALERT_WEBHOOK_URL", ""
        ) or None
        self._cooldown = cooldown
        self._sender = sender or _default_sender
        self._last_fired: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    @property
    def webhook_url(self) -> str | None:
        return self._webhook_url

    async def fire(
        self,
        alert_type: str,
        message: str,
        *,
        severity: str = "warning",
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Send an alert if cooldown elapsed. Returns True when delivered."""
        if alert_type not in ALERT_TYPES:
            log.warning("alerter_unknown_type", alert_type=alert_type)

        now = datetime.now(tz=UTC)
        async with self._lock:
            last = self._last_fired.get(alert_type)
            if last is not None and (now - last) < self._cooldown:
                log.debug(
                    "alerter_suppressed",
                    alert_type=alert_type,
                    remaining_seconds=(
                        self._cooldown - (now - last)
                    ).total_seconds(),
                )
                return False
            self._last_fired[alert_type] = now

        event = AlertEvent(
            type=alert_type,
            message=message,
            severity=severity,
            context=context or {},
            fired_at=now,
        )
        payload = event.as_payload()

        if self._webhook_url is None:
            log.warning("alert_fired_log_only", **payload)
            return True

        try:
            await self._sender(self._webhook_url, payload)
            log.info("alert_fired", **payload)
            return True
        except Exception as exc:
            # httpx exception messages embed the full URL, which often
            # contains a webhook secret token (Slack/Lark/Feishu/etc.).
            # Log only the exception class + HTTP status when present
            # so the secret never lands in disk logs.
            err_class = exc.__class__.__name__
            err_status = getattr(
                getattr(exc, "response", None), "status_code", None
            )
            log.warning(
                "alert_webhook_delivery_failed",
                alert_type=alert_type,
                error_class=err_class,
                http_status=err_status,
            )
            return False

    def reset(self, alert_type: str | None = None) -> None:
        """Clear cooldown — typically used by tests."""
        if alert_type is None:
            self._last_fired.clear()
        else:
            self._last_fired.pop(alert_type, None)


async def _default_sender(url: str, payload: dict[str, Any]) -> None:
    """Deliver via httpx. Imported lazily so unit tests can stub it.

    Binds local_address="0.0.0.0" so the IPv4-only egress invariant
    (memory: feedback_ipv4_only_egress) holds — without this, AAAA-only
    hosts silently stall on a box without an IPv6 default route.
    """
    import httpx

    async with httpx.AsyncClient(
        timeout=10.0,
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
    ) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


__all__ = ["ALERT_TYPES", "Alerter", "AlertEvent"]
