"""Daily LLM cost ceiling enforcement.

Single source of truth for whether the next pipeline run is allowed
under the configured daily budget. The guard never mutates spend data —
it reads the live aggregate from Redis (via cost_tracker) and answers
ok / soft_breach / hard_breach so the scheduler can short-circuit
before paying for another LLM call.

Environment knobs:
- ``QUANTMIND_DAILY_BUDGET`` — absolute hard ceiling in CNY (default ¥20)
- ``QUANTMIND_SOFT_CEIL_PCT`` — fraction of the hard ceiling that
  triggers a soft warning (default 0.7 → ¥14 with the default budget)

Distinct from ``ALERT_COST_DAILY_CNY`` consumed by the dashboard
``/api/monitoring/dashboard`` endpoint: that knob is a cosmetic alert
threshold; this module enforces a hard skip before LLM calls happen.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from backend.llm.cost_tracker import aggregate_costs

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="cost_guard")

# Defaults applied when the env vars are unset; deliberately permissive
# so production stays the source of truth for tightening these.
_DEFAULT_DAILY_BUDGET_RMB = 20.0
_DEFAULT_SOFT_CEIL_PCT = 0.7


@dataclass(frozen=True)
class BudgetState:
    """Snapshot of today's LLM spend vs the configured ceilings."""

    daily_budget: float
    spent_today: float
    soft_ceiling: float
    hard_ceiling: float
    remaining: float
    status: str  # "ok" | "soft_breach" | "hard_breach"


class DailyBudgetExceededError(RuntimeError):
    """Raised when the next call would exceed the daily hard ceiling."""


def _read_env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """Parse a float from the environment, falling back to ``default``.

    Tolerates malformed values (logs + uses default) so a typo in one
    knob does not crash the scheduler boot. Rejects NaN and infinity:
    those would silently soft-disable the cap (e.g. ``budget=inf`` makes
    every spend look ok), which is the worst possible failure mode for
    a guard rail. Non-finite values fall back to ``default``.
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
            "cost_guard_env_non_finite",
            name=name,
            raw=raw,
            fallback=default,
        )
        return default
    if value < minimum:
        log.warning(
            "cost_guard_env_clamped",
            name=name,
            raw=value,
            clamped_to=minimum,
        )
        return minimum
    return value


def _classify(spent: float, soft: float, hard: float) -> str:
    if spent >= hard:
        return "hard_breach"
    if spent >= soft:
        return "soft_breach"
    return "ok"


async def get_budget_state(
    redis_client: redis.asyncio.Redis,
) -> BudgetState:
    """Build the current ``BudgetState`` from Redis aggregations.

    Reads only — no mutation of cost or budget data. Today's total comes
    from ``aggregate_costs(days=1)`` which scans
    ``llm:usage:{date}:*`` keys for the current Asia/Shanghai date.
    """
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
    # A misconfigured >1.0 soft pct would defeat the warning, so cap it.
    if soft_pct > 1.0:
        log.warning(
            "cost_guard_soft_pct_clamped",
            raw=soft_pct,
            clamped_to=1.0,
        )
        soft_pct = 1.0

    summary = await aggregate_costs(redis_client, days=1)
    raw_spent = (
        next(iter(summary.daily_totals.values()), 0.0)
        if summary.daily_totals
        else 0.0
    )

    soft_ceiling = round(daily_budget * soft_pct, 4)
    hard_ceiling = daily_budget

    # Fail-closed on corrupt aggregate data: a Redis HSET that wrote
    # ``cost_rmb=nan`` or ``-inf`` would otherwise propagate here and
    # make ``_classify()`` see "ok" forever. Treat invalid spend as a
    # hard breach so the scheduler short-circuits until operators fix
    # the data, instead of silently disabling the cap.
    if not math.isfinite(raw_spent) or raw_spent < 0:
        log.error(
            "cost_guard_invalid_spent",
            raw_spent=raw_spent,
            action="fail_closed_as_hard_breach",
        )
        sentinel_spent = round(max(daily_budget, 0.0) + 1.0, 4)
        return BudgetState(
            daily_budget=daily_budget,
            spent_today=sentinel_spent,
            soft_ceiling=soft_ceiling,
            hard_ceiling=hard_ceiling,
            remaining=0.0,
            status="hard_breach",
        )

    spent_today = raw_spent
    status = _classify(spent_today, soft_ceiling, hard_ceiling)
    return BudgetState(
        daily_budget=daily_budget,
        spent_today=round(spent_today, 4),
        soft_ceiling=soft_ceiling,
        hard_ceiling=hard_ceiling,
        remaining=round(max(0.0, daily_budget - spent_today), 4),
        status=status,
    )


async def assert_budget_allows(
    redis_client: redis.asyncio.Redis,
    *,
    agent_name: str,
) -> BudgetState:
    """Return the live ``BudgetState`` or raise on ``hard_breach``.

    Callers should treat the returned ``status == "soft_breach"`` as a
    cue to degrade (for example serialize catch-up runs and force
    Kimi thinking off, see Phase 5B).
    """
    state = await get_budget_state(redis_client)
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
