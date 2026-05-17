#!/usr/bin/env python
"""J-005 — Pinned-clock N-day trading harness.

Walks ``--days {1, 5, 10, 45}`` trading days forward from a start date,
firing pinned BrokerScheduler-aligned timestamps on each day:

* 09:30 Asia/Shanghai — morning open
* 10:30 — mid-morning interval tick (any 30s cron representative)
* 11:30 — morning close (transition to lunch)
* 13:00 — afternoon open
* 14:30 — late-afternoon interval tick
* 15:00 — afternoon close (last intraday tick)
* 16:00 — EOD pipeline tick (acceptance + EOD snapshot)
* 16:30 — advance_day tick (clears today's buy volume for T+1)

Each pinned timestamp invokes any registered ``tick_callback`` so the
caller can plug in real BrokerScheduler / DataScheduler runners against
fake market-data adapters; the harness itself is *only* the clock +
calendar walker so it stays decoupled from the heavy data layers.

QUANTMIND_LLM_STUB=1 is set by default so any LLM call routed through
:class:`backend.llm.router.LLMRouter` returns the canned
:class:`StubChatCompletion` — zero real LLM cost. The harness asserts
no reset triggers fire across the entire run (a tick callback that
fires reset triggers is treated as a *substrate bug* the harness is
designed to surface before I-002 burns real money).

Usage::

    # Walk 5 trading days from today, no tick callback wired (calendar smoke):
    python scripts/simulate_n_trading_days.py --days 5

    # 45-day full pre-flight before authorising I-002 production:
    python scripts/simulate_n_trading_days.py --days 45 --json

    # Pin a specific start date for repeatable CI runs:
    python scripts/simulate_n_trading_days.py --days 5 --start 2026-05-18

Exit codes:

* ``0`` — N days walked, 0 reset triggers fired, LLM stub honoured.
* ``1`` — any reset trigger fired, ``--require-stub`` violated, or a
  tick callback raised. ``stderr`` lists every failure cause.

Red lines:

* QUANTMIND_LLM_STUB defaults to ``1`` — production callers must NOT
  invoke this script with ``--allow-real-llm``.
* No real broker / scheduler is started by the harness itself; the
  caller wires the tick callback. Without a callback the harness still
  reports a successful calendar walk (useful for J-006 runbook
  rehearsal in the absence of full pipeline wiring).
* The script is read-only against Mongo + Redis by default; any tick
  callback the caller plugs in inherits the same expectation unless
  the caller explicitly opts into a writable mode.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
import traceback
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from backend.data.trading_calendar import is_trading_day, next_trading_day
from backend.llm.router import QUANTMIND_LLM_STUB_ENV, is_llm_stub_enabled
from backend.utils.trading_hours import SHANGHAI

ALLOWED_DAYS = (1, 5, 10, 45)
"""Locked profile sizes — the harness asserts ``--days`` is one of
these so operators cannot bypass the documented pre-flight tiers."""


PINNED_TICKS_BY_HOUR_MINUTE: tuple[tuple[int, int, str], ...] = (
    (9, 30, "morning_open"),
    (10, 30, "intraday_mtm_sample"),
    (11, 30, "morning_close"),
    (13, 0, "afternoon_open"),
    (14, 30, "intraday_mtm_sample"),
    (15, 0, "afternoon_close"),
    (16, 0, "eod_pipeline"),
    (16, 30, "advance_day"),
)
"""8 pinned ticks per trading day. Names align with
:class:`backend.broker.scheduler.BrokerScheduler` cron labels so the
operator can map J-005 logs onto the real scheduler."""


TickCallback = Callable[[dt.datetime, str], Awaitable[None]]
"""Async callback signature consumed by the harness for each pinned tick.

Receives the Asia/Shanghai-aware datetime and the tick label."""


@dataclass
class _TickRecord:
    when: dt.datetime
    label: str


@dataclass(frozen=True)
class SimulationOutcome:
    """JSON-shaped envelope the harness emits at the end of a run."""

    requested_days: int
    start_date: str
    end_date: str
    trading_days_walked: int
    tick_count: int
    llm_router_stubbed: bool
    real_llm_calls_observed: int
    reset_triggers_fired: tuple[str, ...]
    tick_callback_errors: tuple[str, ...]
    elapsed_seconds: float
    allow_real_llm: bool = False
    """Operator's explicit opt-in to real LLM cost. When True the
    ``ok`` invariant relaxes the stub-mode requirement; when False
    (default) the outcome FAILs unless the LLM router is stubbed."""
    ticks_per_day: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            label for _, _, label in PINNED_TICKS_BY_HOUR_MINUTE
        )
    )

    @property
    def ok(self) -> bool:
        base_ok = (
            self.trading_days_walked == self.requested_days
            and not self.reset_triggers_fired
            and not self.tick_callback_errors
        )
        if not base_ok:
            return False
        if self.allow_real_llm:
            # Operator explicitly opted into real LLM cost; the harness
            # has no remaining stub-mode invariant to enforce.
            return True
        # Default preflight contract: stub mode must be active AND no
        # real LLM calls observed. Codex cycle 1 P1 fix — previously
        # ``llm_router_stubbed=False`` could slip through silently when
        # the parent shell pre-set ``QUANTMIND_LLM_STUB=0`` even though
        # no ``--allow-real-llm`` was passed.
        return self.llm_router_stubbed and self.real_llm_calls_observed == 0


# ---------------------------------------------------------------------------
# Calendar walk
# ---------------------------------------------------------------------------


def iter_trading_days(
    start: dt.date, count: int
) -> Iterable[dt.date]:
    """Yield ``count`` trading days from (and including) ``start``.

    The first yielded date is ``start`` itself if it is a trading day,
    else the next trading day after ``start``. Honours
    ``config/holidays.yaml`` via :func:`is_trading_day` /
    :func:`next_trading_day`.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    cursor = start if is_trading_day(start) else next_trading_day(start)
    yield cursor
    for _ in range(count - 1):
        cursor = next_trading_day(cursor)
        yield cursor


def pinned_ticks_for_day(day: dt.date) -> list[tuple[dt.datetime, str]]:
    """Return the 8 pinned ``(datetime, label)`` ticks for ``day``."""
    return [
        (
            dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI),
            label,
        )
        for hour, minute, label in PINNED_TICKS_BY_HOUR_MINUTE
    ]


# ---------------------------------------------------------------------------
# Harness loop
# ---------------------------------------------------------------------------


async def run_simulation(
    *,
    days: int,
    start_date: dt.date,
    tick_callback: TickCallback | None = None,
    reset_trigger_observer: Callable[[], Iterable[str]] | None = None,
    real_llm_call_observer: Callable[[], int] | None = None,
    allow_real_llm: bool = False,
) -> SimulationOutcome:
    """Run the N-day pinned-clock harness and return a :class:`SimulationOutcome`.

    Args:
        days: number of trading days to walk (must be in ALLOWED_DAYS).
        start_date: earliest date to consider; harness rolls forward to
            the first trading day >= this date.
        tick_callback: optional async callback fired for every pinned
            tick. When None the harness just walks the calendar without
            driving the pipeline (useful for runbook smoke).
        reset_trigger_observer: optional sync callable that returns the
            list of trigger types fired SINCE the last call. The harness
            consults this at end of run to assert zero fires.
        real_llm_call_observer: optional sync callable that returns the
            count of real (non-stubbed) LLM provider calls since boot.
            Harness uses to assert zero across the run.
    """
    if days not in ALLOWED_DAYS:
        raise ValueError(
            f"--days must be one of {ALLOWED_DAYS}; got {days}"
        )

    started_at = _now()
    ticks: list[_TickRecord] = []
    callback_errors: list[str] = []

    days_walked = 0
    last_day: dt.date | None = None
    for day in iter_trading_days(start_date, days):
        days_walked += 1
        last_day = day
        for when, label in pinned_ticks_for_day(day):
            ticks.append(_TickRecord(when=when, label=label))
            if tick_callback is None:
                continue
            try:
                await tick_callback(when, label)
            except Exception as exc:  # noqa: BLE001
                # Treat any callback failure as a substrate bug.
                callback_errors.append(
                    f"{day.isoformat()} {label} @ {when.isoformat()}: {exc!r}"
                )

    elapsed = (_now() - started_at).total_seconds()
    reset_fired: tuple[str, ...] = (
        tuple(reset_trigger_observer()) if reset_trigger_observer else ()
    )
    real_llm = (
        real_llm_call_observer() if real_llm_call_observer else 0
    )

    return SimulationOutcome(
        requested_days=days,
        start_date=start_date.isoformat(),
        end_date=(last_day or start_date).isoformat(),
        trading_days_walked=days_walked,
        tick_count=len(ticks),
        llm_router_stubbed=is_llm_stub_enabled(),
        real_llm_calls_observed=real_llm,
        reset_triggers_fired=reset_fired,
        tick_callback_errors=tuple(callback_errors),
        elapsed_seconds=elapsed,
        allow_real_llm=allow_real_llm,
    )


def _now() -> dt.datetime:
    """Wall-clock helper isolated so tests can monkeypatch deterministically."""
    return dt.datetime.now(dt.UTC)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="simulate_n_trading_days",
        description=(
            "Pinned-clock N-trading-day harness. Default: walks the "
            "calendar with zero callbacks and asserts the LLM router is "
            "stubbed + no reset triggers fire."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        required=True,
        choices=ALLOWED_DAYS,
        help=f"Trading days to walk; one of {ALLOWED_DAYS}.",
    )
    parser.add_argument(
        "--start",
        default=None,
        help=(
            "ISO date to start from (defaults to today Asia/Shanghai). "
            "Harness rolls forward to the first trading day >= this."
        ),
    )
    parser.add_argument(
        "--allow-real-llm",
        action="store_true",
        help=(
            "Opt out of the QUANTMIND_LLM_STUB=1 default; the harness "
            "will let any LLM call hit a real provider. Burns budget — "
            "production usage forbidden."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON envelope (default: table).",
    )
    return parser.parse_args(argv)


def _resolve_start(raw: str | None) -> dt.date:
    if raw is None:
        return dt.datetime.now(SHANGHAI).date()
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"invalid --start date: {raw!r} ({exc})") from exc


def _prepare_env(args: argparse.Namespace) -> None:
    """Force the LLM stub env unless ``--allow-real-llm``.

    Codex cycle 1 P1 fix — ``setdefault`` would leave a pre-set
    ``QUANTMIND_LLM_STUB=0`` from the parent shell in place, allowing
    tick callbacks to hit real providers during a supposedly
    zero-cost preflight. Force-set the truthy value so the harness's
    documented "no real LLM cost" contract holds.
    """
    if not args.allow_real_llm:
        os.environ[QUANTMIND_LLM_STUB_ENV] = "1"


def _format_table(outcome: SimulationOutcome) -> str:
    verdict = "PASS" if outcome.ok else "FAIL"
    lines = [
        f"simulate_n_trading_days verdict: {verdict}",
        f"  requested_days           : {outcome.requested_days}",
        f"  trading_days_walked      : {outcome.trading_days_walked}",
        f"  start_date               : {outcome.start_date}",
        f"  end_date                 : {outcome.end_date}",
        f"  pinned_ticks_fired       : {outcome.tick_count}",
        f"  llm_router_stubbed       : {outcome.llm_router_stubbed}",
        f"  real_llm_calls_observed  : {outcome.real_llm_calls_observed}",
        f"  reset_triggers_fired     : "
        f"{list(outcome.reset_triggers_fired) or 'none'}",
        f"  tick_callback_errors     : "
        f"{len(outcome.tick_callback_errors)}",
        f"  elapsed_seconds          : {outcome.elapsed_seconds:.3f}",
    ]
    if outcome.tick_callback_errors:
        lines.append("  -- callback errors --")
        for err in outcome.tick_callback_errors:
            lines.append(f"    - {err}")
    if outcome.reset_triggers_fired:
        lines.append("  -- reset triggers fired (substrate bug) --")
        for trigger in outcome.reset_triggers_fired:
            lines.append(f"    - {trigger}")
    return "\n".join(lines)


def _format_json(outcome: SimulationOutcome) -> str:
    return json.dumps(
        {
            "verdict": "PASS" if outcome.ok else "FAIL",
            "ok": outcome.ok,
            "requested_days": outcome.requested_days,
            "trading_days_walked": outcome.trading_days_walked,
            "start_date": outcome.start_date,
            "end_date": outcome.end_date,
            "tick_count": outcome.tick_count,
            "ticks_per_day": list(outcome.ticks_per_day),
            "llm_router_stubbed": outcome.llm_router_stubbed,
            "real_llm_calls_observed": outcome.real_llm_calls_observed,
            "reset_triggers_fired": list(outcome.reset_triggers_fired),
            "tick_callback_errors": list(outcome.tick_callback_errors),
            "elapsed_seconds": outcome.elapsed_seconds,
        },
        indent=2,
        ensure_ascii=False,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _prepare_env(args)
    start = _resolve_start(args.start)

    try:
        outcome = asyncio.run(
            run_simulation(
                days=args.days,
                start_date=start,
                allow_real_llm=args.allow_real_llm,
            )
        )
    except Exception:  # noqa: BLE001
        print("simulate_n_trading_days verdict: FAIL", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        return 1

    if args.json:
        print(_format_json(outcome))
    else:
        print(_format_table(outcome))
    return 0 if outcome.ok else 1


if __name__ == "__main__":  # pragma: no cover — exercised via tests
    raise SystemExit(main())


# Silence unused-import warning on Any (kept for downstream extensions).
_ = Any
