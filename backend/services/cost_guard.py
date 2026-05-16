"""H-003 — P1-7 daily / monthly / Kimi LLM budget guard.

Single source of truth for whether the next pipeline run is allowed
under the configured LLM budgets. The guard never mutates spend data —
it reads via :mod:`backend.services.cost_probe` (Redis-only) and answers:

* ``daily.status``       — ``ok`` | ``soft_breach`` | ``hard_breach``
* ``monthly.status``     — ``ok`` | ``threshold_50`` | ``threshold_80`` |
                            ``threshold_100``
* ``kimi.status``        — ``ok`` | ``hard_breach``

P1-7 locked constants (CLAUDE.md §2.10 / docs/decisions/p1-7.md):

* Daily hard ceiling = ¥20         — ONLY full-LLM circuit breaker
* Daily soft ceiling = 0.70 × ¥20 = ¥14 — Kimi-escalation cut, NEVER full halt
* Kimi daily ceiling = ¥4          — only stops Kimi escalations, not full LLM
* Monthly soft budget = ¥440       — 50/80/100% audit/alert nodes, never stops

Module red lines (P1-7 §2 / P0-10 propagation):

* This module MUST NOT import ``backend.{llm,agents,mirofish,data}``.
  Spend data flows in via :mod:`backend.services.cost_probe` (Redis only)
  and the redline-check sub-check ``[H-003] cost_guard isolation`` walks
  the AST to enforce that boundary.
* The 4 ceiling constants are module-level frozen literals — runtime
  ``mutate`` of ``QUANTMIND_DAILY_BUDGET`` / ``QUANTMIND_MONTHLY_BUDGET``
  is read at boot only; hot-reload is forbidden (P0-7 inherited).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from backend.services.cost_probe import (
    get_daily_spent,
    get_daily_spent_for_provider,
    get_month_spent,
)

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="cost_guard")


# ---------------------------------------------------------------------------
# P1-7 locked defaults
# ---------------------------------------------------------------------------

_DEFAULT_DAILY_BUDGET_RMB = 20.0
"""P1-7 daily hard ceiling — ONLY full-LLM circuit breaker."""

_DEFAULT_SOFT_CEIL_PCT = 0.7
"""¥14 soft threshold = 0.70 × ¥20."""

_DEFAULT_MONTHLY_BUDGET_RMB = 440.0
"""P1-7 monthly soft budget = 22 trading days × ¥20."""

_DEFAULT_KIMI_DAILY_CAP_RMB = 4.0
"""P1-7 Kimi daily cap — only blocks Kimi escalation, never DeepSeek/Qwen."""

KIMI_PROVIDER_NAME = "kimi"
"""Redis usage key suffix written by ``backend.llm.fallback``."""

MONTHLY_MILESTONE_FRACTIONS: tuple[float, ...] = (0.50, 0.80, 1.00)
"""Three audit-only milestones; 100% NEVER stops LLM (P1-7 §1.7)."""


# ---------------------------------------------------------------------------
# State envelopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyBudgetState:
    """Today's LLM spend vs the daily ceilings."""

    daily_budget: float
    spent_today: float
    soft_ceiling: float
    hard_ceiling: float
    remaining: float
    status: str  # "ok" | "soft_breach" | "hard_breach"


# Kept as an alias for backwards compatibility with monitoring / scheduler
# imports that still say ``BudgetState`` (Phase 5B era).
BudgetState = DailyBudgetState


@dataclass(frozen=True)
class MonthlyBudgetState:
    """Current month's LLM spend vs the soft ¥440 milestones."""

    monthly_budget: float
    spent_month: float
    fraction: float
    threshold_reached: float | None
    status: str  # "ok" | "threshold_50" | "threshold_80" | "threshold_100"


@dataclass(frozen=True)
class KimiBudgetState:
    """Today's Kimi-only spend vs the ¥4 daily cap."""

    kimi_daily_cap: float
    spent_today: float
    remaining: float
    status: str  # "ok" | "hard_breach"


@dataclass(frozen=True)
class FullBudgetState:
    """Bundle of all three states (used by ``GET /api/cost/budget``)."""

    daily: DailyBudgetState
    monthly: MonthlyBudgetState
    kimi: KimiBudgetState


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DailyBudgetExceededError(RuntimeError):
    """Raised when the next call would exceed the daily hard ceiling."""


class KimiDailyCapExceededError(RuntimeError):
    """Raised when the next Kimi escalation would exceed the ¥4 cap."""


# ---------------------------------------------------------------------------
# Env parsing
# ---------------------------------------------------------------------------


def _read_env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Parse a float from the environment, falling back to ``default``.

    Rejects NaN and infinity (would silently disable the cap) and clamps
    sub-minimum values up to the floor. Logs a warning so the operator
    sees the misconfiguration on boot.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("cost_guard_env_parse_failed", name=name, raw=raw)
        return default
    if not math.isfinite(value):
        log.warning(
            "cost_guard_env_non_finite", name=name, raw=raw, fallback=default
        )
        return default
    if value < minimum:
        log.warning(
            "cost_guard_env_clamped", name=name, raw=value, clamped_to=minimum
        )
        return minimum
    return value


def _classify_daily(spent: float, soft: float, hard: float) -> str:
    if spent >= hard:
        return "hard_breach"
    if spent >= soft:
        return "soft_breach"
    return "ok"


def _classify_monthly(fraction: float) -> tuple[str, float | None]:
    """Return ``(status, milestone)`` for the monthly soft budget.

    The milestone is the highest fraction the spend crossed; the status
    string is the canonical name used by the audit / alerter.
    """
    if fraction >= 1.00:
        return "threshold_100", 1.00
    if fraction >= 0.80:
        return "threshold_80", 0.80
    if fraction >= 0.50:
        return "threshold_50", 0.50
    return "ok", None


def _classify_kimi(spent: float, cap: float) -> str:
    return "hard_breach" if spent >= cap else "ok"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_daily_budget_state(
    redis_client: redis.asyncio.Redis,
) -> DailyBudgetState:
    """Build the current daily-budget state from Redis."""
    daily_budget = _read_env_float(
        "QUANTMIND_DAILY_BUDGET",
        _DEFAULT_DAILY_BUDGET_RMB,
        minimum=0.0,
    )
    soft_pct = _read_env_float(
        "QUANTMIND_SOFT_CEIL_PCT",
        _DEFAULT_SOFT_CEIL_PCT,
        minimum=0.0,
    )
    if soft_pct > 1.0:
        log.warning(
            "cost_guard_soft_pct_clamped", raw=soft_pct, clamped_to=1.0
        )
        soft_pct = 1.0

    raw_spent = await get_daily_spent(redis_client)
    soft_ceiling = round(daily_budget * soft_pct, 4)
    hard_ceiling = daily_budget

    if not math.isfinite(raw_spent) or raw_spent < 0:
        log.error(
            "cost_guard_invalid_spent",
            raw_spent=raw_spent,
            action="fail_closed_as_hard_breach",
        )
        sentinel = round(max(daily_budget, 0.0) + 1.0, 4)
        return DailyBudgetState(
            daily_budget=daily_budget,
            spent_today=sentinel,
            soft_ceiling=soft_ceiling,
            hard_ceiling=hard_ceiling,
            remaining=0.0,
            status="hard_breach",
        )

    spent_today = round(raw_spent, 4)
    status = _classify_daily(spent_today, soft_ceiling, hard_ceiling)
    return DailyBudgetState(
        daily_budget=daily_budget,
        spent_today=spent_today,
        soft_ceiling=soft_ceiling,
        hard_ceiling=hard_ceiling,
        remaining=round(max(0.0, daily_budget - spent_today), 4),
        status=status,
    )


async def get_monthly_budget_state(
    redis_client: redis.asyncio.Redis,
) -> MonthlyBudgetState:
    """Build the current monthly-budget state from Redis."""
    monthly_budget = _read_env_float(
        "QUANTMIND_MONTHLY_BUDGET",
        _DEFAULT_MONTHLY_BUDGET_RMB,
        minimum=0.0,
    )
    raw_month = await get_month_spent(redis_client)
    if not math.isfinite(raw_month) or raw_month < 0:
        log.error(
            "cost_guard_invalid_monthly_spent", raw=raw_month, action="fail_closed"
        )
        raw_month = max(monthly_budget, 0.0) + 1.0

    spent_month = round(raw_month, 4)
    if monthly_budget > 0:
        fraction = spent_month / monthly_budget
    elif spent_month > 0:
        # Misconfigured budget=0 with positive spend: cap to a finite
        # sentinel so /api/cost/budget can serialize the state (Starlette
        # rejects non-finite floats and would 500). The status path still
        # surfaces threshold_100 so operators see the breach (codex
        # cycle 1 P2).
        fraction = 1.0
    else:
        fraction = 0.0
    status, milestone = _classify_monthly(fraction)
    return MonthlyBudgetState(
        monthly_budget=monthly_budget,
        spent_month=spent_month,
        fraction=round(fraction, 4),
        threshold_reached=milestone,
        status=status,
    )


async def get_kimi_budget_state(
    redis_client: redis.asyncio.Redis,
) -> KimiBudgetState:
    """Build the current Kimi-only daily-budget state from Redis."""
    cap = _read_env_float(
        "QUANTMIND_KIMI_DAILY_CAP",
        _DEFAULT_KIMI_DAILY_CAP_RMB,
        minimum=0.0,
    )
    raw_spent = await get_daily_spent_for_provider(
        redis_client, provider=KIMI_PROVIDER_NAME
    )
    if not math.isfinite(raw_spent) or raw_spent < 0:
        log.error(
            "cost_guard_invalid_kimi_spent",
            raw=raw_spent,
            action="fail_closed_as_hard_breach",
        )
        return KimiBudgetState(
            kimi_daily_cap=cap,
            spent_today=round(cap + 1.0, 4) if cap > 0 else 1.0,
            remaining=0.0,
            status="hard_breach",
        )
    spent = round(raw_spent, 4)
    return KimiBudgetState(
        kimi_daily_cap=cap,
        spent_today=spent,
        remaining=round(max(0.0, cap - spent), 4),
        status=_classify_kimi(spent, cap),
    )


async def get_full_budget_state(
    redis_client: redis.asyncio.Redis,
) -> FullBudgetState:
    """One-shot fetch of daily + monthly + Kimi state."""
    daily = await get_daily_budget_state(redis_client)
    monthly = await get_monthly_budget_state(redis_client)
    kimi = await get_kimi_budget_state(redis_client)
    return FullBudgetState(daily=daily, monthly=monthly, kimi=kimi)


# ---------------------------------------------------------------------------
# Backwards-compatible thin wrappers (kept for analysis_scheduler etc.)
# ---------------------------------------------------------------------------


async def get_budget_state(
    redis_client: redis.asyncio.Redis,
) -> DailyBudgetState:
    """Legacy alias — daily state only."""
    return await get_daily_budget_state(redis_client)


async def assert_budget_allows(
    redis_client: redis.asyncio.Redis,
    *,
    agent_name: str,
) -> DailyBudgetState:
    """Return the daily ``BudgetState`` or raise on ``hard_breach``.

    Callers should treat ``status == "soft_breach"`` as a cue to
    activate Kimi escalation block via :class:`SoftDegradeManager`.
    """
    state = await get_daily_budget_state(redis_client)
    if state.status == "hard_breach":
        log.error(
            "daily_budget_breached",
            agent=agent_name,
            spent=state.spent_today,
            budget=state.daily_budget,
        )
        raise DailyBudgetExceededError(
            f"Daily budget {state.daily_budget:.2f} CNY exceeded "
            f"(spent {state.spent_today:.2f}); skipping {agent_name}"
        )
    if state.status == "soft_breach":
        log.warning(
            "cost_soft_breach_active",
            agent=agent_name,
            spent=state.spent_today,
            soft_ceiling=state.soft_ceiling,
        )
    return state


async def assert_kimi_budget_allows(
    redis_client: redis.asyncio.Redis,
    *,
    agent_name: str,
) -> KimiBudgetState:
    """Return the Kimi state or raise on ``hard_breach``.

    Only stops Kimi escalations — the daily ¥20 hard cap stays the
    sole full-LLM circuit breaker (P1-7 §1.3 / CLAUDE.md §2.10).
    """
    state = await get_kimi_budget_state(redis_client)
    if state.status == "hard_breach":
        log.error(
            "kimi_daily_cap_breached",
            agent=agent_name,
            spent=state.spent_today,
            cap=state.kimi_daily_cap,
        )
        raise KimiDailyCapExceededError(
            f"Kimi daily cap {state.kimi_daily_cap:.2f} CNY exceeded "
            f"(spent {state.spent_today:.2f}); deferring Kimi for {agent_name}"
        )
    return state


# Internal helpers re-exported for tests + redline scanner.
_classify = _classify_daily

__all__ = [
    "BudgetState",  # alias kept for legacy callers
    "DailyBudgetExceededError",
    "DailyBudgetState",
    "FullBudgetState",
    "KIMI_PROVIDER_NAME",
    "KimiBudgetState",
    "KimiDailyCapExceededError",
    "MONTHLY_MILESTONE_FRACTIONS",
    "MonthlyBudgetState",
    "_classify",
    "_classify_daily",
    "_classify_kimi",
    "_classify_monthly",
    "_read_env_float",
    "assert_budget_allows",
    "assert_kimi_budget_allows",
    "get_budget_state",
    "get_daily_budget_state",
    "get_full_budget_state",
    "get_kimi_budget_state",
    "get_monthly_budget_state",
]
