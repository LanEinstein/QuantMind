"""Trading hours + day predicates for A-share market.

Pure stdlib + ``backend.utils.holiday_loader`` (which itself reads
``config/holidays.yaml`` once per process). No LLM, no DB, no
``backend.{llm,agents,mirofish,data}`` import — that isolation is
required so ``backend/risk/engine.py`` can keep importing
``is_trading_hours`` from here without violating the P0-10 redline.

C-007 added the static-calendar lookup. Pre-C-007 ``is_trading_day``
was a Mon–Fri only check; the calendar now overlays holidays + makeup
workdays per ``config/holidays.yaml`` (lookup precedence: makeup wins
over weekend, then holiday closes, else weekday rule).
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from zoneinfo import ZoneInfo

from backend.utils.holiday_loader import get_holiday_table

SHANGHAI = ZoneInfo("Asia/Shanghai")

# Continuous-auction sessions (Beijing time). End times are exclusive.
MORNING_OPEN = dt.time(9, 30)
MORNING_CLOSE = dt.time(11, 30)
AFTERNOON_OPEN = dt.time(13, 0)
AFTERNOON_CLOSE = dt.time(15, 0)

# Call-auction windows (Beijing time), additive in U-E1 — see the P0-7
# amendment 2026-05-27 (call-auction-predicates-and-matching-boundary).
# Order-accepting windows; the single match fires at the window end (09:25 →
# open price; 15:00 → close price). End times are exclusive (the match instant
# itself is no longer an order window).
OPENING_AUCTION_OPEN = dt.time(9, 15)
OPENING_AUCTION_CLOSE = dt.time(9, 25)
CLOSING_AUCTION_OPEN = dt.time(14, 57)
CLOSING_AUCTION_CLOSE = dt.time(15, 0)


def t_minus_1_eod_utc(as_of: dt.date) -> dt.datetime:
    """The T-1 EOD logical fetch time — ``as_of`` 15:00 CST close — as UTC.

    Single source for anchoring a Line-1/Line-2 frame's provenance
    ``fetch_time_utc`` to the moment the T-1 EOD data actually pertains to
    (the ``as_of`` afternoon close), rather than the wall clock of whenever
    the frame happens to be assembled.

    WHY both the dry-run and the production 09:35 path need this (U-D4b +
    U-D6c): the frame is T-1 EOD data but the ``InstructionPlan.created_at``
    is the run-day ~09:35. The :class:`Line1FrameAssembler` default stamps
    ``fetch_time_utc = datetime.now(UTC)``; if that is the run-day morning it
    is only minutes before ``created_at`` (a fragile race against the
    ``snapshot_at must be strictly before created_at`` invariant in
    ``backend/models/instruction.py``), and for a same-day re-assembly stamped
    after ``created_at`` it violates it outright and crashes every plan.
    Anchoring to the T-1 15:00 close puts ``snapshot_at`` hours before any
    run-day ``created_at`` — deterministic, race-free, and honest provenance.

    SAFE for PIT replay (R0 §3 red line A): ``fetch_time_utc`` is pure
    provenance — NOT part of the snapshot checksum (computed over raw bytes
    only) nor the replay feature digest — so bit-exact ``replay`` is
    unaffected.
    """
    return dt.datetime.combine(
        as_of, AFTERNOON_CLOSE, tzinfo=SHANGHAI
    ).astimezone(dt.UTC)


class MarketPhase(StrEnum):
    """Fine-grained A-share session phase (U-E1, additive).

    Distinct from :func:`is_trading_hours`, which stays coarse (it treats the
    whole 09:30-11:30 / 13:00-15:00 continuous span as "trading" and is the
    ONLY predicate RiskEngine check #07 consults). ``market_phase`` is a finer
    audit/diagnostic view that also names the call-auction + lunch phases.
    """

    CLOSED = "closed"
    """Non-trading day, or a trading day before 09:15."""
    PRE_OPEN_AUCTION = "pre_open_auction"
    """09:15-09:30 — opening call-auction order window (match at 09:25) plus
    the 09:25-09:30 quiet gap before continuous trading opens."""
    CONTINUOUS_AM = "continuous_am"
    """09:30-11:30 — morning continuous auction."""
    LUNCH_BREAK = "lunch_break"
    """11:30-13:00 — midday recess."""
    CONTINUOUS_PM = "continuous_pm"
    """13:00-14:57 — afternoon continuous auction."""
    CLOSING_AUCTION = "closing_auction"
    """14:57-15:00 — closing call auction (match at 15:00)."""
    POST_CLOSE = "post_close"
    """A trading day at/after 15:00."""


def is_trading_hours(now: dt.datetime | None = None) -> bool:
    """Check if the given time is within A-share trading hours.

    Trading hours (Beijing time): 09:30-11:30, 13:00-15:00 on a
    trading day. End times are exclusive (11:30 and 15:00 are NOT
    trading). Calendar lookup goes through :func:`is_trading_day`,
    which now consults ``config/holidays.yaml``.
    """
    if now is None:
        now = dt.datetime.now(tz=SHANGHAI)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI)
    else:
        now = now.astimezone(SHANGHAI)

    if not is_trading_day(now.date()):
        return False

    t = now.time()
    morning = MORNING_OPEN <= t < MORNING_CLOSE
    afternoon = AFTERNOON_OPEN <= t < AFTERNOON_CLOSE
    return morning or afternoon


def is_trading_day(date: dt.date | None = None) -> bool:
    """Check if the given date is a trading day for the A-share market.

    Three-tier rule:

    1. If ``date ∈ makeup_workdays`` → trading day (overrides weekend).
    2. Else if ``date ∈ holidays`` → non-trading.
    3. Else weekday → trading; weekend → non-trading.

    Note: ``makeup_workdays`` reflects the **A-share exchange** schedule,
    NOT the State Council 调休补班 (office) schedule. Since 2018+ the
    SSE/SZSE has consistently published "为周末休市，不补休" — so the
    default ``config/holidays.yaml`` keeps ``makeup_workdays_YYYY``
    empty. The branch is preserved so ops can add an entry on the rare
    year an exchange explicitly opens a Sat/Sun.

    Args:
        date: Date to check. Defaults to today in Beijing timezone.
    """
    if date is None:
        date = dt.datetime.now(tz=SHANGHAI).date()
    table = get_holiday_table()
    if date in table.makeup_workdays:
        return True
    if date in table.holidays:
        return False
    return date.weekday() < 5  # Mon=0 .. Fri=4


def _to_shanghai(now: dt.datetime | None) -> dt.datetime:
    """Normalise ``now`` to an Asia/Shanghai-aware datetime.

    Mirrors the inline normalisation :func:`is_trading_hours` has always done
    (None → now; naive → assume Shanghai; aware → convert), so the call-auction
    predicates accept the same input shapes.
    """
    if now is None:
        return dt.datetime.now(tz=SHANGHAI)
    if now.tzinfo is None:
        return now.replace(tzinfo=SHANGHAI)
    return now.astimezone(SHANGHAI)


def is_opening_call_auction(now: dt.datetime | None = None) -> bool:
    """True iff ``now`` is in the opening call-auction order window.

    09:15-09:25 (Beijing) on a trading day; end exclusive (09:25 is the match
    instant, not an order window). Additive in U-E1 — does NOT affect
    :func:`is_trading_hours` / RiskEngine check #07.
    """
    now = _to_shanghai(now)
    if not is_trading_day(now.date()):
        return False
    return OPENING_AUCTION_OPEN <= now.time() < OPENING_AUCTION_CLOSE


def is_closing_call_auction(now: dt.datetime | None = None) -> bool:
    """True iff ``now`` is in the closing call-auction window.

    14:57-15:00 (Beijing) on a trading day; end exclusive (15:00 is the close
    match instant). Additive in U-E1.
    """
    now = _to_shanghai(now)
    if not is_trading_day(now.date()):
        return False
    return CLOSING_AUCTION_OPEN <= now.time() < CLOSING_AUCTION_CLOSE


def is_call_auction(now: dt.datetime | None = None) -> bool:
    """True iff ``now`` is in either the opening or closing call auction."""
    return is_opening_call_auction(now) or is_closing_call_auction(now)


def market_phase(now: dt.datetime | None = None) -> MarketPhase:
    """Classify ``now`` into a fine-grained :class:`MarketPhase` (U-E1).

    A diagnostic/audit view; RiskEngine check #07 still uses only
    :func:`is_trading_hours`. Boundaries match the session constants (open
    inclusive, close exclusive). The 09:25-09:30 quiet gap is folded into
    ``PRE_OPEN_AUCTION`` (the pre-open period as a whole).
    """
    now = _to_shanghai(now)
    if not is_trading_day(now.date()):
        return MarketPhase.CLOSED
    t = now.time()
    if t < OPENING_AUCTION_OPEN:
        return MarketPhase.CLOSED
    if t < MORNING_OPEN:  # 09:15-09:30 (auction window + quiet gap)
        return MarketPhase.PRE_OPEN_AUCTION
    if t < MORNING_CLOSE:  # 09:30-11:30
        return MarketPhase.CONTINUOUS_AM
    if t < AFTERNOON_OPEN:  # 11:30-13:00
        return MarketPhase.LUNCH_BREAK
    if t < CLOSING_AUCTION_OPEN:  # 13:00-14:57
        return MarketPhase.CONTINUOUS_PM
    if t < CLOSING_AUCTION_CLOSE:  # 14:57-15:00
        return MarketPhase.CLOSING_AUCTION
    return MarketPhase.POST_CLOSE  # >= 15:00
