"""DailyTradingState assembler (D-002 / P0-7 §1).

The RiskEngine is a pure function (no IO, no LLM); checks 10/12/13/14
need per-day counters + the live quote. This module reads
``broker_events`` (E-002), the in-process CircuitBreaker, the
:class:`MarketMetaProvider`, and the decision_ledger to materialise
the :class:`DailyTradingState` DTO the InstructionPlanBuilder hands
to the engine.

Why a separate service: the assembler lives under
``backend/services`` so the risk-isolation redline stays intact
(``backend/risk`` cannot import ``backend.data``). It owns the
"read everything that's needed for 14-check" responsibility and
nothing else.

LLM red line: no LLM imports — the assembler only reads from the
broker / data / ledger stores.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore
from backend.data.market_meta_provider import (
    MarketMetaProvider,
    StaleQuoteError,
)
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState

log = structlog.get_logger(component="services.daily_state_assembler")
SHANGHAI = ZoneInfo("Asia/Shanghai")


async def assemble_daily_state(
    *,
    stock_code: str,
    now: datetime,
    event_store: BrokerEventStore,
    market_meta: MarketMetaProvider,
    circuit_breaker: CircuitBreaker,
    day_open_nav: float,
    current_nav: float,
    recent_trade_pnls: Sequence[float] = (),
) -> DailyTradingState:
    """Build a :class:`DailyTradingState` for the 14-check engine call.

    The assembler queries the ``broker_events`` log for today's dispatched
    instruction count (DISPATCHED status), reads the live quote with
    StaleQuoteError → ``current_price=None`` (check 12 then fail-closes),
    and forwards the CircuitBreaker halt window flag. day_open_nav /
    current_nav arrive pre-computed because their data sources are
    different (broker_events for current_nav, broker_snapshots / EOD
    chain for day_open_nav) — keeping them as inputs makes the
    assembler trivially testable.

    Args:
        stock_code: target stock for the at-fill quote.
        now: tz-aware Asia/Shanghai timestamp.
        event_store: BrokerEventStore for today's instruction tally.
        market_meta: MarketMetaProvider for the live quote.
        circuit_breaker: in-process CircuitBreaker singleton.
        day_open_nav / current_nav: portfolio NAV at open vs current.
        recent_trade_pnls: optional override; in production the
            decision_ledger supplies the last-3 FILLED trade PnLs.
    """
    today_count = await _count_dispatched_today(event_store, now=now)

    try:
        current_price = await market_meta.get_current_price(stock_code, now=now)
    except StaleQuoteError:
        current_price = None

    halted = circuit_breaker.is_halted(now)
    halt_until = _derive_halt_until(circuit_breaker, halted)

    today_pnl_pct = 0.0
    if day_open_nav > 0:
        today_pnl_pct = (current_nav - day_open_nav) / day_open_nav

    state = DailyTradingState(
        today_new_instruction_count=today_count,
        today_portfolio_pnl_pct=today_pnl_pct,
        last_3_trade_pnls=tuple(recent_trade_pnls[-3:]),
        current_price=current_price,
        is_in_halt_cooldown=halted,
        halt_until=halt_until,
    )
    log.debug(
        "daily_state_assembled",
        stock_code=stock_code,
        today_count=today_count,
        current_price=current_price,
        is_in_halt_cooldown=halted,
    )
    return state


def _derive_halt_until(
    breaker: CircuitBreaker, halted: bool
) -> datetime | None:
    """Compute the halt-expiry wall-clock from the breaker's private state.

    CircuitBreaker does not expose ``halt_until`` directly because the
    cooldown is judged inside ``is_halted(now)``; the assembler reaches
    into the private ``_halted_at`` + ``_config.cooldown_minutes`` to
    materialise the value DailyTradingState wants. Marked as private
    access on purpose — adding a public accessor on CircuitBreaker is a
    backend/risk API change which needs its own amendment.
    """
    if not halted:
        return None
    started = getattr(breaker, "_halted_at", None)
    cooldown_min = getattr(getattr(breaker, "_config", None), "cooldown_minutes", 0)
    if started is None or cooldown_min <= 0:
        return None
    from datetime import timedelta as _td

    return started + _td(minutes=int(cooldown_min))


async def _count_dispatched_today(
    event_store: BrokerEventStore,
    *,
    now: datetime,
) -> int:
    """Count BrokerEvents from today's trading session that are dispatched.

    "Dispatched today" = ORDER_PLACED + EXECUTION_REPORT_APPLIED events
    whose occurred_at maps to the same local trade-date as ``now``. The
    P0-7 §1.4 daily cap is BUY+SELL (HOLD never routes — CLAUDE.md §2.7).

    The implementation streams events from sequence > 0 (full history)
    and filters in Python; for production scale this is O(events_today)
    which fits well within a per-instruction call budget. A future
    optimization can persist a per-day counter doc.
    """
    today_local = now.astimezone(SHANGHAI).date()
    count = 0
    async for event in event_store.stream_since(0):
        if event.event_type not in {
            BrokerEventType.ORDER_PLACED,
            BrokerEventType.EXECUTION_REPORT_APPLIED,
        }:
            continue
        occurred_local = event.occurred_at.astimezone(SHANGHAI).date()
        if occurred_local == today_local:
            count += 1
    return count


async def compute_today_portfolio_pnl_pct(
    equity_repo: Any,
    *,
    now: datetime,
) -> float:
    """Day-open-NAV-relative MTM P&L ratio for the daily-loss brake (check 13).

    ``current_nav`` = TODAY's latest MTM ``EquityPoint.total_equity``;
    ``day_open_nav`` = the prior trading day's CLOSING MTM equity (via
    ``get_latest_before_trade_date``, unbounded lookback). Both read the
    persisted ``equity_points`` collection, so the value is restart-safe and the
    brake re-binds immediately after a mid-day restart
    (``P0-7-amendment-2026-06-23``).

    Two correctness guards (codex review 2026-06-23):

    * **Today-tick guard** — ``get_latest()`` returns the newest point by
      ``snapshot_at`` regardless of date. Before today's first 30s MTM tick
      (pre-open, or a lagging/failed MTM cron), that is YESTERDAY's close, which
      would mis-state today's drawdown as yesterday's full-day P&L and could
      spuriously halt the 09:35 BUY scan. So if the latest point is not dated
      today, there is no computable intraday drawdown → ``0.0`` (fail-safe
      inactive — identical to the pre-amendment behaviour at that moment).
    * **Unbounded prior lookback** — a fixed N-day window would drop the prior
      close across a long A-share holiday; ``get_latest_before_trade_date`` has
      no window. A genuine first session (no prior point) falls back to seed
      capital. A near-zero ``current_nav`` (validated real wipeout) yields a
      large negative ratio → halt (fail-SAFE).

    Fail-OPEN to ``0.0`` when the equity store is absent / has no point yet, or
    on a read error caught by the caller (infra glitch; the always-on
    per-position stops + ≤5 caps still bind). Returns a finite ratio.
    """
    if equity_repo is None:
        return 0.0
    latest = await equity_repo.get_latest()
    today_iso = now.astimezone(SHANGHAI).date().isoformat()
    if latest is None or latest.trade_date != today_iso:
        return 0.0  # no today MTM tick → no intraday drawdown (fail-safe)
    current_nav = float(latest.total_equity)
    prior = await equity_repo.get_latest_before_trade_date(today_iso)
    day_open_nav = (
        prior.total_equity
        if prior is not None and prior.total_equity > 0
        else latest.initial_capital
    )
    if day_open_nav <= 0:
        return 0.0
    ratio = (current_nav - day_open_nav) / day_open_nav
    return ratio if math.isfinite(ratio) else 0.0


async def assemble_daily_risk_inputs(
    *,
    event_store: BrokerEventStore | None,
    equity_repo: Any,
    now: datetime,
) -> tuple[int, float]:
    """``(today_instruction_count, today_portfolio_pnl_pct)`` for the daily
    14-check state, wired from the live persisted stores by the crons
    (``P0-7-amendment-2026-06-23``).

    Fail-safe per source (CLAUDE.md §3 fail-open-for-infra): a broker-events or
    equity-store read fault yields that source's 0-default so a transient store
    glitch never blocks trading — the always-on per-position stops + the ≤5
    position/order caps remain. The order count still binds across runs/restarts
    because it reads the persisted append-only ``broker_events`` (not in-memory).
    """
    count = 0
    if event_store is not None:
        try:
            count = await _count_dispatched_today(event_store, now=now)
        except Exception:  # noqa: BLE001 — count glitch must not block trading
            log.warning("daily_instruction_count_failed", exc_info=True)
    pnl = 0.0
    try:
        pnl = await compute_today_portfolio_pnl_pct(equity_repo, now=now)
    except Exception:  # noqa: BLE001 — equity glitch → brake best-effort
        log.warning("daily_pnl_pct_failed", exc_info=True)
    return count, pnl


__all__ = [
    "assemble_daily_state",
    "compute_today_portfolio_pnl_pct",
    "assemble_daily_risk_inputs",
]
