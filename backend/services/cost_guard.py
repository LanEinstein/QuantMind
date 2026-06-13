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

import datetime
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

_DEFAULT_DAILY_BUDGET_RMB = 100.0
"""P1-7 daily hard ceiling — ONLY full-LLM circuit breaker. P1-7-amendment
2026-05-26 raised it ¥20 → ¥100 so Line-1 can debate multiple shortlist
candidates per day (multi-candidate fall-through). Still a hard ceiling: the
crossing call never happens (真·预留). Override via ``QUANTMIND_DAILY_BUDGET``."""

_DEFAULT_SOFT_CEIL_PCT = 0.7
"""Soft threshold ratio = 0.70 × daily hard (auto-scales: ¥70 at the ¥100
hard). Unchanged by the 2026-05-26 amendment (it is a fraction, not ¥14)."""

_DEFAULT_MONTHLY_BUDGET_RMB = 440.0
"""P1-7 monthly soft budget = 22 trading days × ¥20."""

_DEFAULT_KIMI_DAILY_CAP_RMB = 4.0
"""P1-7 Kimi daily cap — only blocks Kimi escalation, never DeepSeek/Qwen."""

KIMI_PROVIDER_NAME = "kimi"
"""Redis usage key suffix written by ``backend.llm.fallback``."""

MONTHLY_MILESTONE_FRACTIONS: tuple[float, ...] = (0.50, 0.80, 1.00)
"""Three audit-only milestones; 100% NEVER stops LLM (P1-7 §1.7)."""

# P1-7-amendment-2026-05-24 introduced fan-out caps; P1-7-amendment-2026-05-26
# REVOKED its "one debate per daily shortlist, not per candidate" rule. Line-1
# now debates shortlist candidates one-by-one (REJECTED → fall through to the
# next) until a routable BUY basket is built. The multiplicative fan-out is now
# bounded by TWO independent fail-closed guards: this per-day debate cap + the
# daily ¥100 pre-reservation. One 4-agent debate costs ~¥0.08–0.4, so 8 debates
# ≈ ¥3.2 ≪ ¥100 — the debate cap binds first.
_DEFAULT_MAX_DEBATES_PER_DAY = 8
"""Max multi-agent debate runs per UTC day. Each shortlist candidate Line-1
debates claims ONE slot (basket mode debates several per day, P1-7-amendment
2026-05-26 §2.3). RiskEngine check-10 (≤5 orders/day) + the 15%/70% caps bound
the routable basket to ≈5, so the default 8 leaves headroom. Raise via
``QUANTMIND_MAX_DEBATES_PER_DAY`` (env override is amendment-blessed)."""

_DEFAULT_MAX_ANOMALY_LLM_PER_DAY = 10
"""Max Line-2 anomaly-triggered LLM calls per UTC day (N-004). Line-2 is a
pure-quant poll (zero LLM); the LLM fires only on a deduped trigger up to this
cap, writing the same ``llm:usage:{utc_date}`` counter (P1-7-amendment §2.2)."""

# Pre-call reservation key + TTL. Reservations are transient (released by
# ``settle_budget`` after the call); the TTL guarantees a crashed caller's
# stale reservation cannot wedge the daily counter forever.
_RESERVED_KEY_PREFIX = "llm:usage"
_RESERVATION_TTL_SECONDS = 3600

# Per-UTC-day debate-count key (fan-out cap). TTL spans the trading day plus
# slack so the counter resets next day without a scheduler sweep.
_DEBATE_COUNT_KEY_PREFIX = "llm:debates"
_DEBATE_COUNT_TTL_SECONDS = 36 * 3600

# Line-2 anomaly-triggered LLM gate (N-004). The count key bounds the daily
# trigger budget (max_anomaly_llm_per_day); the dedup SET stops the same
# (code, kind) trigger from firing twice in one UTC day. Both live in the
# ``llm:anomaly`` namespace, and the actual spend still reserves on the unified
# ``llm:usage`` counter via reserve_budget so Line-2 cannot bypass the ¥20 cap.
_ANOMALY_COUNT_KEY_PREFIX = "llm:anomaly"
_ANOMALY_DEDUP_KEY_PREFIX = "llm:anomaly:dedup"
_ANOMALY_TTL_SECONDS = 36 * 3600

# Line-2 thesis-review LLM gate (W-002 / P0-10-amendment-line2-2026-06-01 §1.2).
# The post-close advisory review fires one LLM call per open PositionThesis; the
# count key bounds the daily budget, the dedup SET stops the same (code, date)
# review from firing twice (a same-day cron re-run), and the actual spend still
# reserves on the unified ``llm:usage`` counter via reserve_budget so the
# advisory cannot bypass the ¥100 daily hard cap. Mirrors the N-004 anomaly gate.
_DEFAULT_MAX_THESIS_REVIEW_LLM_PER_DAY = 10
"""Max Line-2 thesis-review LLM calls per UTC day. Naturally bounded by the
≤5-slot held cap; the explicit cap is a fail-closed backstop. Override via
``QUANTMIND_MAX_THESIS_REVIEW_LLM_PER_DAY`` (env override is amendment-blessed)."""
_THESIS_REVIEW_COUNT_KEY_PREFIX = "llm:thesis_review"
_THESIS_REVIEW_DEDUP_KEY_PREFIX = "llm:thesis_review:dedup"
_THESIS_REVIEW_TTL_SECONDS = 36 * 3600

# MiroFish sector-forecast LLM gate (O-002 / P0-8-amendment-2026-05-24).
# The 17:00 EOD pipeline fires ONE forecast call per TRADE DATE; the dedup
# marker is keyed by trade_date (NOT the rerun's UTC day) so a holiday /
# vendor-lag fallback to a prior trading day cannot re-pay for the same
# ``MIROFISH-FORECAST-{trade_date}`` on a later calendar day (codex O-002
# P2 — Mongo's unique-id rejection only fires AFTER the paid call). The
# count key is a per-UTC-day fail-closed backstop, and the spend reserves
# on the unified ``llm:usage`` counter so the forecast cannot bypass the
# ¥100/day hard cap. Mirrors the W-002 thesis-review gate.
_DEFAULT_MAX_SECTOR_FORECAST_LLM_PER_DAY = 2
"""Max MiroFish sector-forecast LLM calls per UTC day (1 cron + 1 manual
re-run headroom). Override via ``QUANTMIND_MAX_SECTOR_FORECAST_LLM_PER_DAY``."""
_SECTOR_FORECAST_COUNT_KEY_PREFIX = "llm:sector_forecast"
_SECTOR_FORECAST_DEDUP_KEY_PREFIX = "llm:sector_forecast:dedup"
_SECTOR_FORECAST_COUNT_TTL_SECONDS = 36 * 3600
# Dedup marker lives long enough to span the longest A-share holiday
# cluster (golden week / spring festival ≈ 9 calendar days of fallback
# reruns to the same prior trade date) + slack.
_SECTOR_FORECAST_DEDUP_TTL_SECONDS = 14 * 24 * 3600


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
    *,
    today: datetime.date | None = None,
) -> DailyBudgetState:
    """Build the current daily-budget state from Redis.

    ``today`` pins the UTC day for the in-flight **reservation** read (defaults
    to the real UTC day in production, where it is never pinned). Threading it
    lets a test read the reservation counter for the same day it reserved on,
    instead of the real-today key — the prior implicit real-today read made the
    M-005 reservation fold date-brittle (it broke on any day != the test's
    pinned date). Actual spend (``get_daily_spent``) is read for the real day in
    production and is mocked deterministically under test, so it is intentionally
    not re-pinned here (that would force every spend mock to grow a ``today``
    kwarg for no production benefit).
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

    # Fold in-flight pre-call reservations into the effective spend so the
    # ¥20 hard cap holds for EVERY caller — including legacy ones that still
    # use ``assert_budget_allows`` (analysis_scheduler / shadow / GEPA). Without
    # this, ¥19 actual + a ¥1 in-flight debate reservation would read < ¥20 on
    # the legacy path and let another paid call start, defeating the cap
    # (codex M-005 P1). ``reserve_budget`` itself keeps using ``get_daily_spent``
    # + its own counter read, so there is no double-count.
    reserved = await get_daily_reserved(redis_client, today=today)
    spent_today = round(raw_spent + reserved, 4)
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
    today: datetime.date | None = None,
) -> DailyBudgetState:
    """Return the daily ``BudgetState`` or raise on ``hard_breach``.

    Callers should treat ``status == "soft_breach"`` as a cue to
    activate Kimi escalation block via :class:`SoftDegradeManager`.
    ``today`` pins the UTC day (defaults to the real day) so the gate sees the
    same day's spend + in-flight reservations deterministically.
    """
    state = await get_daily_budget_state(redis_client, today=today)
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


# ---------------------------------------------------------------------------
# P1-7-amendment-2026-05-24 — pre-call reservation (真·预留) + fan-out caps
# ---------------------------------------------------------------------------


def get_max_debates_per_day() -> int:
    """Max multi-agent debate runs per UTC day (runtime-immutable at boot)."""
    return int(
        _read_env_float(
            "QUANTMIND_MAX_DEBATES_PER_DAY",
            float(_DEFAULT_MAX_DEBATES_PER_DAY),
            minimum=1.0,
        )
    )


def get_max_anomaly_llm_per_day() -> int:
    """Max Line-2 anomaly-triggered LLM calls per UTC day (N-004)."""
    return int(
        _read_env_float(
            "QUANTMIND_MAX_ANOMALY_LLM_PER_DAY",
            float(_DEFAULT_MAX_ANOMALY_LLM_PER_DAY),
            minimum=0.0,
        )
    )


def get_max_thesis_review_llm_per_day() -> int:
    """Max Line-2 thesis-review LLM calls per UTC day (W-002)."""
    return int(
        _read_env_float(
            "QUANTMIND_MAX_THESIS_REVIEW_LLM_PER_DAY",
            float(_DEFAULT_MAX_THESIS_REVIEW_LLM_PER_DAY),
            minimum=0.0,
        )
    )


def get_max_sector_forecast_llm_per_day() -> int:
    """Max MiroFish sector-forecast LLM calls per UTC day (O-002)."""
    return int(
        _read_env_float(
            "QUANTMIND_MAX_SECTOR_FORECAST_LLM_PER_DAY",
            float(_DEFAULT_MAX_SECTOR_FORECAST_LLM_PER_DAY),
            minimum=0.0,
        )
    )


def _utc_date_str(today: datetime.date | None = None) -> str:
    base = today or datetime.datetime.now(tz=datetime.UTC).date()
    return base.isoformat()


def _reserved_key(date_str: str) -> str:
    """The per-UTC-day reservation counter (same ``llm:usage`` namespace as
    spend, so it is auditable alongside actual cost; ``cost_probe`` skips it
    because it is a plain string, not a per-agent hash)."""
    return f"{_RESERVED_KEY_PREFIX}:{date_str}:reserved"


async def get_daily_reserved(
    redis_client: redis.asyncio.Redis,
    *,
    today: datetime.date | None = None,
) -> float:
    """Sum of in-flight pre-call reservations for the UTC day (0 if none).

    Read by :func:`get_daily_budget_state` so the legacy budget path accounts
    for reservations too (codex M-005 P1). Fail-open: an unreadable / invalid
    counter returns 0.0 — the dependable enforcement is ``reserve_budget``'s own
    atomic check; this only folds the in-flight amount into the shared state.
    """
    key = _reserved_key(_utc_date_str(today))
    try:
        raw = await redis_client.get(key)
    except Exception as exc:  # noqa: BLE001 — fail-open, reserve_budget is primary
        log.warning("reserved_read_failed", key=key, error=str(exc))
        return 0.0
    if raw is None:
        return 0.0
    if isinstance(raw, bytes | bytearray):
        raw = raw.decode("utf-8", "ignore")
    # Only accept real scalar return types from Redis. Anything else
    # (e.g. a test double whose __float__ defaults to 1.0) reads as 0.0 so
    # it cannot silently inflate the effective spend.
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        return 0.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(val) or val < 0:
        return 0.0
    return round(val, 4)


@dataclass(frozen=True)
class BudgetReservation:
    """Handle for an in-flight pre-call LLM budget reservation.

    Returned by :func:`reserve_budget`; passed to :func:`settle_budget` to
    release the reservation after the call completes. ``amount_rmb`` is the
    estimated cost that was reserved (released verbatim on settle — the
    *actual* spend is recorded separately by the LLM router's track_usage).
    """

    key: str
    amount_rmb: float
    agent_name: str
    date: str


async def reserve_budget(
    redis_client: redis.asyncio.Redis,
    *,
    agent_name: str,
    estimated_rmb: float,
    today: datetime.date | None = None,
) -> BudgetReservation:
    """Reserve ``estimated_rmb`` against the daily ¥20 hard cap BEFORE the call.

    P1-7-amendment-2026-05-24: this replaces the old post-hoc trailing-stop
    (``assert_budget_allows`` only raised once spend had *already* crossed
    ¥20, letting the crossing call complete). Here the estimate is reserved
    atomically first; if ``spent + reserved`` would exceed the hard ceiling
    the reservation is rolled back and :class:`DailyBudgetExceededError` is
    raised — **the crossing call never happens**.

    Every LLM-spending path (debate / MiroFish / Line-2 anomaly / Phase R)
    MUST reserve here so the unified ``llm:usage:{utc_date}`` counter governs
    the whole system (amendment §2.4). Fail-closed: a non-finite/negative
    observed spend is treated as already over budget.
    """
    if not math.isfinite(estimated_rmb) or estimated_rmb < 0:
        raise ValueError(f"estimated_rmb must be finite and >= 0, got {estimated_rmb}")
    daily_budget = _read_env_float(
        "QUANTMIND_DAILY_BUDGET", _DEFAULT_DAILY_BUDGET_RMB, minimum=0.0
    )
    date_str = _utc_date_str(today)
    key = _reserved_key(date_str)

    new_reserved = await redis_client.incrbyfloat(key, estimated_rmb)
    # Best-effort TTL so a crashed caller's reservation cannot wedge the
    # counter past the trading day.
    try:
        await redis_client.expire(key, _RESERVATION_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 — TTL is hygiene, not correctness
        log.warning("reservation_expire_failed", key=key, error=str(exc))

    try:
        reserved_val = float(new_reserved)
    except (TypeError, ValueError):
        reserved_val = estimated_rmb

    spent = await get_daily_spent(redis_client, today=today)
    if not math.isfinite(spent) or spent < 0:
        log.error("reserve_invalid_spent", raw=spent, action="fail_closed")
        spent = daily_budget + 1.0

    projected = round(spent + reserved_val, 4)
    if projected > daily_budget:
        # Roll back this reservation — the crossing call must not run.
        await redis_client.incrbyfloat(key, -estimated_rmb)
        log.error(
            "daily_budget_reservation_refused",
            agent=agent_name,
            estimated=estimated_rmb,
            spent=round(spent, 4),
            already_reserved=round(reserved_val - estimated_rmb, 4),
            budget=daily_budget,
        )
        raise DailyBudgetExceededError(
            f"Reservation of {estimated_rmb:.4f} CNY for {agent_name} would "
            f"cross the daily budget {daily_budget:.2f} CNY "
            f"(spent {spent:.4f} + reserved {reserved_val:.4f}); call refused"
        )
    return BudgetReservation(
        key=key, amount_rmb=estimated_rmb, agent_name=agent_name, date=date_str
    )


def _debate_count_key(date_str: str) -> str:
    return f"{_DEBATE_COUNT_KEY_PREFIX}:{date_str}"


async def reserve_debate_slot(
    redis_client: redis.asyncio.Redis,
    *,
    today: datetime.date | None = None,
) -> int:
    """Claim one of the day's debate slots — the multiplicative fan-out cap.

    P1-7-amendment-2026-05-26 §2.3 (revoking the 2026-05-24 "one debate per
    daily shortlist" rule): Line-1 debates shortlist candidates one-by-one, so
    each ``run_shortlist`` invocation (one candidate) claims ONE slot. This
    per-day cap is now one of the two guards (with the ¥100 reservation) that
    bound the multi-candidate fan-out. Atomically increments
    ``llm:debates:{utc_date}`` and raises :class:`DailyBudgetExceededError`
    (rolling the counter back) when it would exceed ``max_debates_per_day`` —
    the crossing debate does not run, so the basket loop stops fail-closed.
    """
    cap = get_max_debates_per_day()
    date_str = _utc_date_str(today)
    key = _debate_count_key(date_str)
    new_count = await redis_client.incr(key)
    try:
        await redis_client.expire(key, _DEBATE_COUNT_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 — TTL is hygiene, not correctness
        log.warning("debate_count_expire_failed", key=key, error=str(exc))
    try:
        count = int(new_count)
    except (TypeError, ValueError):
        count = cap + 1  # fail closed
    if count > cap:
        await redis_client.decr(key)
        log.error("max_debates_per_day_reached", cap=cap, attempted=count)
        raise DailyBudgetExceededError(
            f"max_debates_per_day {cap} reached for {date_str}; debate refused"
        )
    return count


async def reset_daily_gate_counters(
    redis_client: redis.asyncio.Redis,
    *,
    today: datetime.date | None = None,
) -> None:
    """Clear the day's TRANSIENT fan-out / reservation gate counters.

    Deletes the debate-slot counter, the in-flight reservation counter, and the
    Line-2 anomaly count + dedup keys for the UTC day — the gates that normally
    reset at the BrokerScheduler 00:00 cron. It does NOT touch the audited LLM
    *spend* (the ``llm:usage:{date}`` per-agent hashes), so budget history is
    preserved.

    Used by the render-only dry-run harness so each invocation simulates a
    FRESH trading day (otherwise a second same-day dry-run inherits the first
    run's debate-slot count and fails ``max_debates_per_day`` spuriously).
    Fail-open: a Redis hiccup is logged, never raised — the dry-run still runs.
    """
    date_str = _utc_date_str(today)
    keys = (
        _debate_count_key(date_str),
        _reserved_key(date_str),
        _anomaly_count_key(date_str),
        _anomaly_dedup_key(date_str),
        # W-002 (codex P3): the thesis-review count/dedup are per-day gates too —
        # clear them with the rest so a fresh-day dry-run / same-day rerun is not
        # spuriously deduped out of reviewing.
        _thesis_review_count_key(date_str),
        _thesis_review_dedup_key(date_str),
        # O-002: only the per-UTC-day forecast cap counter is a transient
        # daily gate. The dedup marker is keyed by trade_date (durable
        # idempotency across UTC days), so it is NOT cleared here — a
        # fresh trading day is a NEW trade_date with its own marker.
        _sector_forecast_count_key(date_str),
    )
    try:
        await redis_client.delete(*keys)
    except Exception as exc:  # noqa: BLE001 — best-effort fresh-day reset
        log.warning("reset_daily_gate_counters_failed", error=str(exc))


def _anomaly_count_key(date_str: str) -> str:
    return f"{_ANOMALY_COUNT_KEY_PREFIX}:{date_str}"


def _anomaly_dedup_key(date_str: str) -> str:
    return f"{_ANOMALY_DEDUP_KEY_PREFIX}:{date_str}"


def _thesis_review_count_key(date_str: str) -> str:
    return f"{_THESIS_REVIEW_COUNT_KEY_PREFIX}:{date_str}"


def _thesis_review_dedup_key(date_str: str) -> str:
    return f"{_THESIS_REVIEW_DEDUP_KEY_PREFIX}:{date_str}"


async def reserve_thesis_review_slot(
    redis_client: redis.asyncio.Redis,
    *,
    trigger_key: str,
    estimated_rmb: float,
    today: datetime.date | None = None,
) -> BudgetReservation | None:
    """Gate an OPTIONAL Line-2 thesis-review advisory LLM call (W-002).

    The 17:30 post-close review fires one LLM call per open ``PositionThesis``.
    The call is **non-decision advisory** (it only writes ``evidence_collection``
    + a display-only digest), so any limit simply **skips** it — this function
    never raises (the caller treats ``None`` as "do not call the LLM"). The spend
    reserves on the SAME ``llm:usage:{utc_date}`` counter as every other LLM path
    so the ¥100/day hard cap cannot be bypassed (P1-7-amendment §2.4).

    Returns a :class:`BudgetReservation` when permitted, or ``None`` when it must
    be skipped — already reviewed today for this ``trigger_key`` (dedup, e.g. a
    same-day cron re-run), the daily thesis-review cap is exhausted, or the ¥100
    reservation refuses. Mirrors :func:`reserve_anomaly_llm_slot`.
    """
    date_str = _utc_date_str(today)
    dedup_key = _thesis_review_dedup_key(date_str)
    count_key = _thesis_review_count_key(date_str)

    try:
        added = int(await redis_client.sadd(dedup_key, trigger_key))
    except Exception as exc:  # noqa: BLE001 — fail-closed: skip optional LLM
        log.warning("thesis_review_dedup_failed", trigger=trigger_key, error=str(exc))
        return None
    await _safe_expire(redis_client, dedup_key, _THESIS_REVIEW_TTL_SECONDS)
    if added == 0:
        # Already reviewed today for this (code, date) — dedup skip.
        return None

    cap = get_max_thesis_review_llm_per_day()
    try:
        new_count = int(await redis_client.incr(count_key))
    except Exception as exc:  # noqa: BLE001 — fail-closed
        log.warning("thesis_review_count_failed", trigger=trigger_key, error=str(exc))
        await _safe_srem(redis_client, dedup_key, trigger_key)
        return None
    await _safe_expire(redis_client, count_key, _THESIS_REVIEW_TTL_SECONDS)
    if new_count > cap:
        await _safe_decr(redis_client, count_key)
        log.info("thesis_review_cap_reached", cap=cap, attempted=new_count)
        return None

    try:
        return await reserve_budget(
            redis_client,
            agent_name=f"line2:thesis_review:{trigger_key}"[:64],
            estimated_rmb=estimated_rmb,
            today=today,
        )
    except DailyBudgetExceededError:
        await _safe_decr(redis_client, count_key)
        log.info("thesis_review_budget_skip", trigger=trigger_key)
        return None
    except Exception as exc:  # noqa: BLE001 — fail-closed: never raise on this path
        log.warning("thesis_review_reserve_failed", trigger=trigger_key, error=str(exc))
        await _safe_decr(redis_client, count_key)
        await _safe_srem(redis_client, dedup_key, trigger_key)
        return None


async def reserve_sector_forecast_slot(
    redis_client: redis.asyncio.Redis,
    *,
    trigger_key: str,
    estimated_rmb: float,
    today: datetime.date | None = None,
) -> BudgetReservation | None:
    """Gate the OPTIONAL daily MiroFish sector-forecast LLM call (O-002).

    The 17:00 EOD pipeline fires at most one forecast per trade date. The call
    is non-decision advisory (it only writes ``evidence_collection`` with the
    ``MIROFISH-`` prefix), so any limit simply **skips** it — this function
    never raises (the caller treats ``None`` as "do not call the LLM"). The
    spend reserves on the SAME ``llm:usage:{utc_date}`` counter as every other
    LLM path so the ¥100/day hard cap cannot be bypassed (P1-7-amendment §2.4).

    Returns a :class:`BudgetReservation` when permitted, or ``None`` when it
    must be skipped — already forecast today for this ``trigger_key`` (dedup,
    e.g. a same-day cron re-run), the daily forecast cap is exhausted, or the
    ¥100 reservation refuses. Mirrors :func:`reserve_thesis_review_slot`.
    """
    date_str = _utc_date_str(today)
    # Dedup keyed by the TRADE DATE (trigger_key), not the rerun's UTC day,
    # so a holiday/vendor-lag fallback cannot re-pay for the same forecast.
    dedup_key = _sector_forecast_dedup_key(trigger_key)
    count_key = _sector_forecast_count_key(date_str)

    try:
        added = int(await redis_client.sadd(dedup_key, trigger_key))
    except Exception as exc:  # noqa: BLE001 — fail-closed: skip optional LLM
        log.warning(
            "sector_forecast_dedup_failed", trigger=trigger_key, error=str(exc)
        )
        return None
    await _safe_expire(
        redis_client, dedup_key, _SECTOR_FORECAST_DEDUP_TTL_SECONDS
    )
    if added == 0:
        # Already forecast for this trade date (possibly on an earlier UTC
        # day via fallback) — dedup skip.
        return None

    cap = get_max_sector_forecast_llm_per_day()
    try:
        new_count = int(await redis_client.incr(count_key))
    except Exception as exc:  # noqa: BLE001 — fail-closed
        log.warning(
            "sector_forecast_count_failed", trigger=trigger_key, error=str(exc)
        )
        await _safe_srem(redis_client, dedup_key, trigger_key)
        return None
    await _safe_expire(
        redis_client, count_key, _SECTOR_FORECAST_COUNT_TTL_SECONDS
    )
    if new_count > cap:
        await _safe_decr(redis_client, count_key)
        # No paid call happened — release the durable trade-date marker so a
        # later retry (next UTC day / after budget frees) is not blocked for
        # the 14-day dedup TTL (codex O-002 verify). The marker must mean
        # "a forecast was actually paid for", not "we tried once".
        await _safe_srem(redis_client, dedup_key, trigger_key)
        log.info("sector_forecast_cap_reached", cap=cap, attempted=new_count)
        return None

    try:
        return await reserve_budget(
            redis_client,
            agent_name=f"mirofish:sector_forecast:{trigger_key}"[:64],
            estimated_rmb=estimated_rmb,
            today=today,
        )
    except DailyBudgetExceededError:
        await _safe_decr(redis_client, count_key)
        # Non-paid skip → release the durable marker so the same trade_date
        # can be re-attempted once the daily budget frees up.
        await _safe_srem(redis_client, dedup_key, trigger_key)
        log.info("sector_forecast_budget_skip", trigger=trigger_key)
        return None
    except Exception as exc:  # noqa: BLE001 — fail-closed: never raise here
        log.warning(
            "sector_forecast_reserve_failed", trigger=trigger_key, error=str(exc)
        )
        await _safe_decr(redis_client, count_key)
        await _safe_srem(redis_client, dedup_key, trigger_key)
        return None


def _sector_forecast_count_key(date_str: str) -> str:
    """Per-UTC-day forecast-call cap counter (fail-closed backstop)."""
    return f"{_SECTOR_FORECAST_COUNT_KEY_PREFIX}:{date_str}"


def _sector_forecast_dedup_key(trade_date: str) -> str:
    """Per-TRADE-DATE dedup marker (durable across UTC days / reruns)."""
    return f"{_SECTOR_FORECAST_DEDUP_KEY_PREFIX}:{trade_date}"


async def reserve_anomaly_llm_slot(
    redis_client: redis.asyncio.Redis,
    *,
    trigger_key: str,
    estimated_rmb: float,
    today: datetime.date | None = None,
) -> BudgetReservation | None:
    """Gate an OPTIONAL Line-2 anomaly-triggered LLM enrichment call (N-004).

    Line-2 is a pure-quant poll (zero LLM); an LLM fires only on a deduplicated
    trigger, bounded by ``max_anomaly_llm_per_day``, and the spend reserves on
    the SAME ``llm:usage:{utc_date}`` counter as every other LLM path so the
    ¥20/day hard cap cannot be bypassed (P1-7-amendment §2.4 / N-004).

    Returns a :class:`BudgetReservation` when the call is permitted, or ``None``
    when it must be **skipped** — already fired today for this ``trigger_key``
    (dedup), the daily anomaly cap is exhausted, or the ¥20 reservation refuses.
    The LLM here is non-decision enrichment, so any limit simply skips it; this
    function never raises (the caller treats ``None`` as "do not call the LLM").
    """
    date_str = _utc_date_str(today)
    dedup_key = _anomaly_dedup_key(date_str)
    count_key = _anomaly_count_key(date_str)

    try:
        added = int(await redis_client.sadd(dedup_key, trigger_key))
    except Exception as exc:  # noqa: BLE001 — fail-closed: skip optional LLM
        log.warning("anomaly_dedup_failed", trigger=trigger_key, error=str(exc))
        return None
    await _safe_expire(redis_client, dedup_key, _ANOMALY_TTL_SECONDS)
    if added == 0:
        # Already fired today for this (code, kind) trigger — dedup skip.
        return None

    cap = get_max_anomaly_llm_per_day()
    try:
        new_count = int(await redis_client.incr(count_key))
    except Exception as exc:  # noqa: BLE001 — fail-closed
        log.warning("anomaly_count_failed", trigger=trigger_key, error=str(exc))
        await _safe_srem(redis_client, dedup_key, trigger_key)
        return None
    await _safe_expire(redis_client, count_key, _ANOMALY_TTL_SECONDS)
    if new_count > cap:
        # Daily anomaly-LLM budget exhausted — roll back the count; leave the
        # dedup member so a re-eval of the same trigger is a no-op (deduped).
        await _safe_decr(redis_client, count_key)
        log.info("anomaly_llm_cap_reached", cap=cap, attempted=new_count)
        return None

    try:
        return await reserve_budget(
            redis_client,
            agent_name=f"line2:anomaly:{trigger_key}"[:64],
            estimated_rmb=estimated_rmb,
            today=today,
        )
    except DailyBudgetExceededError:
        # ¥20 hard cap would be crossed — skip the optional enrichment and roll
        # back the anomaly count so it reflects fired calls only. Leave the
        # dedup member: the day's budget is exhausted, a same-day retry is moot.
        await _safe_decr(redis_client, count_key)
        log.info("anomaly_llm_budget_skip", trigger=trigger_key)
        return None
    except Exception as exc:  # noqa: BLE001 — fail-closed: never raise on this
        # A non-budget reservation failure (e.g. a raw Redis error inside
        # reserve_budget) must NOT propagate from this optional Line-2 path —
        # the whole gate is fail-closed (codex N-004 P2). Roll back BOTH the
        # count and the dedup member so a later poll can retry the (transient)
        # failure rather than being silently deduped out.
        log.warning("anomaly_reserve_failed", trigger=trigger_key, error=str(exc))
        await _safe_decr(redis_client, count_key)
        await _safe_srem(redis_client, dedup_key, trigger_key)
        return None


async def _safe_expire(
    redis_client: redis.asyncio.Redis, key: str, ttl: int
) -> None:
    try:
        await redis_client.expire(key, ttl)
    except Exception as exc:  # noqa: BLE001 — TTL is hygiene, not correctness
        log.warning("expire_failed", key=key, error=str(exc))


async def _safe_decr(redis_client: redis.asyncio.Redis, key: str) -> None:
    try:
        await redis_client.decr(key)
    except Exception as exc:  # noqa: BLE001 — best-effort rollback
        log.warning("decr_failed", key=key, error=str(exc))


async def _safe_srem(
    redis_client: redis.asyncio.Redis, key: str, member: str
) -> None:
    try:
        await redis_client.srem(key, member)
    except Exception as exc:  # noqa: BLE001 — best-effort rollback
        log.warning("srem_failed", key=key, error=str(exc))


async def settle_budget(
    redis_client: redis.asyncio.Redis,
    reservation: BudgetReservation,
) -> None:
    """Release an in-flight reservation after the call completes.

    The *actual* spend is recorded by the LLM router's ``track_usage`` into
    the per-agent ``llm:usage:{date}:{agent}:{provider}`` hashes; ``settle``
    only frees the transient reservation so ``reserved`` reflects pending
    calls only. Idempotent-ish: never raises (a transient Redis error must
    not crash the post-call path — the TTL backstops a missed release).
    """
    try:
        await redis_client.incrbyfloat(reservation.key, -reservation.amount_rmb)
    except Exception as exc:  # noqa: BLE001 — TTL backstops a missed release
        log.warning(
            "reservation_settle_failed",
            key=reservation.key,
            agent=reservation.agent_name,
            error=str(exc),
        )


# Internal helpers re-exported for tests + redline scanner.
_classify = _classify_daily



# ---------------------------------------------------------------------------
# Evolution lane sub-budget (AB-007 / P2-2-amendment-2026-06-12; absorbs
# the R-004 "independent sub-budget" mandate)
# ---------------------------------------------------------------------------

_DEFAULT_EVOLUTION_DAILY_SUBBUDGET_RMB = 10.0
"""Daily ceiling for the 22:00 evolution lane. A SUB-budget: every
reservation ALSO goes through :func:`reserve_budget` against the
unified ``llm:usage:{utc_date}`` counter, so evolution spend can never
bypass the ¥100 daily hard cap (amendment §2.4 discipline)."""


def _evolution_key(date_str: str) -> str:
    return f"llm:evolution:{date_str}"


async def get_evolution_spent(
    redis_client: redis.asyncio.Redis,
    *,
    today: datetime.date | None = None,
) -> float:
    """Evolution-lane reserved spend for the UTC day (0.0 on cold key)."""
    raw = await redis_client.get(_evolution_key(_utc_date_str(today)))
    if raw is None:
        return 0.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if math.isfinite(value) and value >= 0 else 0.0


async def reserve_evolution_run(
    redis_client: redis.asyncio.Redis,
    *,
    estimated_rmb: float,
    today: datetime.date | None = None,
) -> BudgetReservation | None:
    """Reserve one evolution run against BOTH budget layers.

    Returns the unified-counter reservation (settle it after the run)
    or ``None`` when either layer refuses — the 22:00 lane then skips
    tonight's run (degrade, never freeze; X-005 decoupling). Order:
    the evolution sub-counter is checked first (cheap), then the
    unified ¥100 reservation; the sub-counter increments only after
    the unified reservation succeeded so the two layers cannot drift.
    """
    if not math.isfinite(estimated_rmb) or estimated_rmb < 0:
        raise ValueError(
            f"estimated_rmb must be finite and >= 0, got {estimated_rmb}"
        )
    subbudget = _read_env_float(
        "QUANTMIND_EVOLUTION_DAILY_SUBBUDGET",
        _DEFAULT_EVOLUTION_DAILY_SUBBUDGET_RMB,
        minimum=0.0,
    )
    spent = await get_evolution_spent(redis_client, today=today)
    if spent + estimated_rmb > subbudget:
        log.info(
            "evolution_subbudget_exhausted",
            spent=round(spent, 4),
            estimated=estimated_rmb,
            subbudget=subbudget,
        )
        return None
    try:
        reservation = await reserve_budget(
            redis_client,
            agent_name="evolution_shadow_run",
            estimated_rmb=estimated_rmb,
            today=today,
        )
    except DailyBudgetExceededError as exc:
        log.warning(
            "evolution_run_blocked_by_daily_hard_cap", error=str(exc)
        )
        return None
    key = _evolution_key(_utc_date_str(today))
    await redis_client.incrbyfloat(key, estimated_rmb)
    await redis_client.expire(key, 60 * 60 * 48)
    return reservation


__all__ = [
    "BudgetReservation",
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
    "get_daily_reserved",
    "get_full_budget_state",
    "get_kimi_budget_state",
    "get_max_anomaly_llm_per_day",
    "get_max_debates_per_day",
    "get_max_thesis_review_llm_per_day",
    "get_monthly_budget_state",
    "reserve_anomaly_llm_slot",
    "reserve_budget",
    "reserve_debate_slot",
    "reserve_evolution_run",
    "reserve_thesis_review_slot",
    "reset_daily_gate_counters",
    "get_evolution_spent",
    "settle_budget",
]
