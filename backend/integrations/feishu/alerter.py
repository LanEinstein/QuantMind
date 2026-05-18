"""Feishu OpenAPI alerter (F-006 / P0-2-amendment-2026-05-16).

Replaces the legacy ``backend.monitoring.alerter.Alerter`` webhook path
for system alerts. After the amendment retired the owner tenant's
custom-bot, every alert flows through the self-built app OpenAPI
``POST /open-apis/im/v1/messages`` to ``FEISHU_ALERT_CHAT_ID`` — the
**alert** chat is intentionally isolated from the decision chat so a
loud alert storm cannot pollute the order / reconciliation thread.

Red lines (CLAUDE.md §2.9 / P0-2-amendment-2026-05-16 §4):

* Whitelisted alert types only — buy/sell/recon/clarification text
  must never flow through here (P1-7 §1.7).
* Dedup window 15 minutes per ``(type, key)`` so a sustained outage
  does not spam the alert chat.
* Empty / unknown alert types log a warning instead of dispatching so
  a typo cannot leak through.
* Renders body via :class:`MessageRenderer.render_alert` — LLMs never
  compose alert text.
* Detects legacy ``FEISHU_CUSTOM_BOT_*`` env values at construction and
  raises so the legacy webhook never accidentally comes back online.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.integrations.feishu.client import FeishuClient, SendMessageResult
from backend.integrations.feishu.renderer import MessageRenderer
from backend.services.secrets_validator import (
    LEGACY_FEISHU_CUSTOM_BOT_NAMES,
)

log = logging.getLogger("backend.integrations.feishu.alerter")


# Whitelisted alert types. Adding a new type must update this set + the
# F-006 redline-check sub-check + the F-006 audit row schema.
ALERT_TYPES: frozenset[str] = frozenset(
    {
        # P1-7 cost budget breaches (forwarded by cost_guard)
        "monthly_budget_50pct_reached",
        "monthly_budget_80pct_reached",
        "monthly_budget_100pct_reached",
        "daily_cost_ceiling_20cny_breached",
        "kimi_daily_cap_4cny_breached",
        # P0-6 system interruptions
        "llm_all_providers_failed",
        "scheduler_lag",
        "circuit_breaker_open",
        "data_quality_breach",
        # Operational lifecycle
        "backup_failed",
        "health_critical",
        "feishu_longconn_disconnected",
        # P2-2 self-evolution notifications (gated)
        "evolution_amendment_drafted",
        # J-004 — P0-6 §1 acceptance-window reset triggers (5 sub-types
        # share this single alert vocabulary entry).
        "acceptance_reset_triggered",
    }
)
"""Locked alert-type vocabulary. Adding a new entry needs an amendment +
test update (see also ``backend/monitoring/alert_dispatcher.py``
ALERT_MATRIX and ``scripts/redline-check.sh``)."""


DEFAULT_DEDUP_WINDOW = timedelta(minutes=15)
"""P1-7 §1.7 — Alerter.dedup_15min prevents alert storms."""


@dataclass(frozen=True)
class AlertResult:
    """Outcome of a single :meth:`FeishuAlerter.fire` call."""

    sent: bool
    """``True`` iff the message was dispatched to Feishu."""

    suppressed: bool
    """``True`` iff dedup or unknown-type suppression triggered."""

    send_result: SendMessageResult | None
    """Underlying Feishu response (None when suppressed)."""

    reason: str
    """Short tag for audit + structured logging."""


@dataclass
class _CooldownState:
    """Tracking entry inside :class:`FeishuAlerter`."""

    last_fired_at: datetime
    last_dedup_key: str = ""


class FeishuAlerter:
    """Self-built-app OpenAPI alerter (F-006).

    Args:
        feishu: FeishuClient. ``None`` is OK in simulation_auto — every
            call short-circuits to ``sent=False, reason="no_client"``.
        renderer: MessageRenderer for the alert body. Always required.
        alert_chat_id: ``FEISHU_ALERT_CHAT_ID``. Must be a different
            chat from the decision chat (the constructor enforces it).
        decision_chat_id: ``optional`` — when supplied, the alerter
            asserts ``alert_chat_id != decision_chat_id`` so a
            misconfigured env cannot route alerts into the decision chat.
        dedup_window: cooldown between repeat fires of the same type.
        env: env mapping for legacy CUSTOM_BOT detection (defaults
            ``os.environ``).
        clock: optional UTC clock for tests.
    """

    def __init__(
        self,
        *,
        feishu: FeishuClient | None,
        renderer: MessageRenderer,
        alert_chat_id: str,
        decision_chat_id: str | None = None,
        dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
        env: Mapping[str, str] | None = None,
        clock: Any | None = None,
    ) -> None:
        if not alert_chat_id:
            raise ValueError(
                "FeishuAlerter requires alert_chat_id "
                "(FEISHU_ALERT_CHAT_ID)"
            )
        if (
            decision_chat_id is not None
            and decision_chat_id == alert_chat_id
        ):
            raise ValueError(
                "alert_chat_id must NOT equal decision_chat_id — alerts "
                "must stay isolated from the decision chat "
                "(P0-2-amendment-2026-05-16 §4 red line 7)"
            )
        if dedup_window.total_seconds() <= 0:
            raise ValueError("dedup_window must be positive")

        self._feishu = feishu
        self._renderer = renderer
        self._alert_chat_id = alert_chat_id
        self._dedup_window = dedup_window
        self._cooldowns: dict[str, _CooldownState] = {}
        self._lock = asyncio.Lock()
        self._clock = clock or _utc_now

        # Legacy detection — log if FEISHU_CUSTOM_BOT_* surfaced in
        # this process. Don't fail-start (secrets_validator already
        # tolerated them as a soft warning); just emit a warning so
        # operators notice the regression.
        scan = env if env is not None else os.environ
        for legacy in LEGACY_FEISHU_CUSTOM_BOT_NAMES:
            if scan.get(legacy, "").strip():
                log.warning(
                    "feishu_alerter_legacy_custom_bot_detected "
                    "credential=%s — see P0-2-amendment-2026-05-16",
                    legacy,
                )

    # -- Public API ----------------------------------------------------

    @property
    def alert_chat_id(self) -> str:
        """``FEISHU_ALERT_CHAT_ID`` the alerter dispatches into.

        Exposed read-only so downstream wrappers (e.g. the X-014
        ``EvolutionFeishuNotifier``) can audit the destination
        without having to thread the env var through their own
        constructor.
        """
        return self._alert_chat_id

    async def fire(
        self,
        *,
        alert_type: str,
        severity: str = "warning",
        message: str,
        dedup_key: str = "",
        fired_at: datetime | None = None,
    ) -> AlertResult:
        """Render + dispatch an alert message.

        Args:
            alert_type: one of :data:`ALERT_TYPES`. Unknown types are
                logged and suppressed.
            severity: ``info`` / ``warning`` / ``critical``. Surfaced
                in the alert header; no business logic depends on it.
            message: short human-readable body. LLMs never compose this.
            dedup_key: optional secondary key bundled with ``alert_type``
                for finer-grained dedup (e.g. include the resource id
                when one alert type covers many resources).
            fired_at: clock override; defaults to ``datetime.now(UTC)``.

        Returns:
            :class:`AlertResult` describing dispatch / suppress reason.
        """
        if alert_type not in ALERT_TYPES:
            log.warning(
                "feishu_alerter_unknown_type alert_type=%s severity=%s",
                alert_type,
                severity,
            )
            return AlertResult(
                sent=False,
                suppressed=True,
                send_result=None,
                reason="unknown_alert_type",
            )
        if not message:
            raise ValueError("alert message must not be empty")

        now = fired_at or self._clock()
        cooldown_key = f"{alert_type}|{dedup_key}"
        async with self._lock:
            state = self._cooldowns.get(cooldown_key)
            if state is not None and (now - state.last_fired_at) < self._dedup_window:
                return AlertResult(
                    sent=False,
                    suppressed=True,
                    send_result=None,
                    reason="dedup_window",
                )
            self._cooldowns[cooldown_key] = _CooldownState(
                last_fired_at=now, last_dedup_key=dedup_key
            )

        if self._feishu is None:
            log.warning(
                "feishu_alerter_no_client alert_type=%s severity=%s",
                alert_type,
                severity,
            )
            return AlertResult(
                sent=False,
                suppressed=True,
                send_result=None,
                reason="no_client",
            )

        body = self._renderer.render_alert(
            alert_type=alert_type,
            severity=severity,
            message=message,
            fired_at=now,
        )
        send_result = await self._feishu.send_message(
            self._alert_chat_id,
            body,
            uuid=f"alert-{alert_type}-{now.isoformat()}-{dedup_key}",
        )
        return AlertResult(
            sent=send_result.ok,
            suppressed=False,
            send_result=send_result,
            reason="dispatched" if send_result.ok else "send_failed",
        )

    def reset(self, alert_type: str | None = None) -> None:
        """Clear cooldown — used by tests and operational toggles."""
        if alert_type is None:
            self._cooldowns.clear()
            return
        keys_to_drop = [
            key for key in self._cooldowns if key.startswith(f"{alert_type}|")
        ]
        for key in keys_to_drop:
            del self._cooldowns[key]


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ALERT_TYPES",
    "AlertResult",
    "DEFAULT_DEDUP_WINDOW",
    "FeishuAlerter",
]
