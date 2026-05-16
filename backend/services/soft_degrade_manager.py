"""H-003 — SoftDegradeManager: P1-7 §1.5 Kimi-escalation block + monthly milestones.

When the daily soft ceiling (¥14) is breached the manager turns OFF
Kimi escalation only — the 4 mandatory agents stay running and
DeepSeek + Qwen keep serving them. When a monthly milestone (50 / 80 /
100%) is reached the manager publishes an idempotent ``SETNX`` flag so
the alerter fires exactly once per milestone per month.

P1-7 red lines (CLAUDE.md §2.10 / inherited):

* Module MUST NOT import ``backend.{llm,agents,mirofish,data}``.
  Spend data flows via ``backend.services.cost_probe`` (Redis only).
* The 4 budget constants live in :mod:`backend.services.cost_guard` and
  are imported here (not re-defined) so a future tightening only needs
  one edit.
* The manager NEVER:
    - cuts back the 4 mandatory agents (P0-10 §2 redline 5)
    - lowers the fast/slow cron frequency (P0-9 §1.4)
    - falls back to DeepSeek-only (P1-7 §1.4 — wipes Qwen-ChinaA strength)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from backend.services.cost_guard import (
    MONTHLY_MILESTONE_FRACTIONS,
    DailyBudgetState,
    KimiBudgetState,
    MonthlyBudgetState,
)

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="soft_degrade_manager")

_KIMI_BLOCK_KEY = "soft_degrade:kimi_escalation_blocked"
_MONTHLY_MILESTONE_KEY_FMT = "cost_alert:monthly:{ym}:{pct}"


@dataclass(frozen=True)
class DegradeFlags:
    """Snapshot of all soft-degrade flags."""

    kimi_escalation_blocked: bool
    daily_status: str
    kimi_status: str
    monthly_status: str


@dataclass(frozen=True)
class MilestoneTransition:
    """Result of evaluating the monthly soft budget."""

    fraction: float
    """The highest fraction the current spend has crossed (0.5 / 0.8 / 1.0)."""

    fired: bool
    """``True`` iff the ``SETNX`` succeeded (first observation this month)."""

    alert_type: str
    """Canonical alert type for the matching milestone."""


def _ym_key(now: datetime) -> str:
    """Calendar-month key used in the milestone Redis lookup."""
    return f"{now.year:04d}{now.month:02d}"


def _seconds_until_next_midnight_utc(now: datetime) -> int:
    """TTL helper — Redis flag expires at the next UTC midnight."""
    tomorrow = (now + timedelta(days=1)).date()
    expiry = datetime(
        tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC
    )
    return max(1, int((expiry - now).total_seconds()))


def _seconds_until_next_month_utc(now: datetime) -> int:
    """TTL helper — milestone flags expire at the start of the next month."""
    if now.month == 12:
        first_of_next = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        first_of_next = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return max(1, int((first_of_next - now).total_seconds()))


def _milestone_alert_type(pct: float) -> str:
    if pct >= 1.00:
        return "monthly_budget_100pct_reached"
    if pct >= 0.80:
        return "monthly_budget_80pct_reached"
    if pct >= 0.50:
        return "monthly_budget_50pct_reached"
    return "ok"


class SoftDegradeManager:
    """Read/write the soft-degrade Redis flags."""

    def __init__(self, redis_client: redis.asyncio.Redis) -> None:
        self._redis = redis_client

    # ---- Kimi escalation block ------------------------------------------

    async def is_kimi_escalation_blocked(self) -> bool:
        """``True`` when today's soft breach blocked Kimi escalation.

        Tightens the truthiness check so a non-str/bytes value (e.g. an
        ``AsyncMock`` returned by a unit-test redis double) is treated
        as absent. Real ``redis.asyncio`` returns ``str`` when wired
        with ``decode_responses=True`` and ``bytes`` otherwise.
        """
        try:
            value = await self._redis.get(_KIMI_BLOCK_KEY)
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning("soft_degrade_get_failed", error=str(exc))
            return False
        if isinstance(value, str | bytes):
            return bool(value)
        return False

    async def activate_kimi_escalation_block(
        self,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> bool:
        """Set the flag if not already set; returns ``True`` on first set.

        TTL = seconds until next UTC midnight so the block clears naturally
        when the daily LLM bucket rolls over. The BrokerScheduler 1st cron
        also resets the flag at 00:00 Asia/Shanghai (belt-and-braces; the
        TTL alone is the dependable layer).
        """
        moment = now or datetime.now(UTC)
        ttl = _seconds_until_next_midnight_utc(moment)
        try:
            ok = await self._redis.set(
                _KIMI_BLOCK_KEY,
                reason,
                ex=ttl,
                nx=True,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning("soft_degrade_set_failed", error=str(exc))
            return False
        return bool(ok)

    async def reset_daily(self) -> None:
        """Clear the Kimi-escalation flag (BrokerScheduler 00:00 cron)."""
        try:
            await self._redis.delete(_KIMI_BLOCK_KEY)
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning("soft_degrade_delete_failed", error=str(exc))

    # ---- Monthly milestone notifications --------------------------------

    async def maybe_fire_monthly_milestone(
        self,
        monthly: MonthlyBudgetState,
        *,
        now: datetime | None = None,
    ) -> MilestoneTransition | None:
        """Set the ``SETNX`` flag for the highest milestone crossed.

        Returns the transition envelope when ``fired=True`` (first time
        this month) OR ``fired=False`` when the milestone was already
        recorded earlier in the month — callers decide whether to
        surface an alert. Returns ``None`` when no milestone reached.
        """
        if monthly.threshold_reached is None:
            return None
        pct = monthly.threshold_reached
        moment = now or datetime.now(UTC)
        key = _MONTHLY_MILESTONE_KEY_FMT.format(
            ym=_ym_key(moment), pct=int(pct * 100)
        )
        ttl = _seconds_until_next_month_utc(moment)
        alert_type = _milestone_alert_type(pct)
        try:
            ok = await self._redis.set(key, "1", ex=ttl, nx=True)
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning(
                "monthly_milestone_set_failed",
                pct=pct,
                error=str(exc),
            )
            return MilestoneTransition(
                fraction=pct, fired=False, alert_type=alert_type
            )
        return MilestoneTransition(
            fraction=pct, fired=bool(ok), alert_type=alert_type
        )

    # ---- Aggregate snapshot ---------------------------------------------

    async def snapshot(
        self,
        *,
        daily: DailyBudgetState,
        monthly: MonthlyBudgetState,
        kimi: KimiBudgetState,
    ) -> DegradeFlags:
        return DegradeFlags(
            kimi_escalation_blocked=await self.is_kimi_escalation_blocked(),
            daily_status=daily.status,
            kimi_status=kimi.status,
            monthly_status=monthly.status,
        )


# Allowed monthly fractions (mirror cost_guard.MONTHLY_MILESTONE_FRACTIONS).
__all__ = [
    "MONTHLY_MILESTONE_FRACTIONS",
    "DegradeFlags",
    "MilestoneTransition",
    "SoftDegradeManager",
]
