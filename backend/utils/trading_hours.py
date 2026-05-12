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
from zoneinfo import ZoneInfo

from backend.utils.holiday_loader import get_holiday_table

SHANGHAI = ZoneInfo("Asia/Shanghai")

# Trading sessions (Beijing time)
MORNING_OPEN = dt.time(9, 30)
MORNING_CLOSE = dt.time(11, 30)
AFTERNOON_OPEN = dt.time(13, 0)
AFTERNOON_CLOSE = dt.time(15, 0)


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
