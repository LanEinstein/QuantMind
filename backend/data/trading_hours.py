"""Trading hours utility for A-share market."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

# Trading sessions (Beijing time)
MORNING_OPEN = dt.time(9, 30)
MORNING_CLOSE = dt.time(11, 30)
AFTERNOON_OPEN = dt.time(13, 0)
AFTERNOON_CLOSE = dt.time(15, 0)


def is_trading_hours(now: dt.datetime | None = None) -> bool:
    """Check if the given time is within A-share trading hours.

    Trading hours (Beijing time): 09:30-11:30, 13:00-15:00 Mon-Fri.
    End times are exclusive (11:30 and 15:00 are NOT trading).

    Args:
        now: Datetime to check. Defaults to current Beijing time.
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
    """Check if the given date is a trading day (weekday).

    Does NOT account for Chinese public holidays.
    TODO: Integrate holiday calendar for accurate trading day check.

    Args:
        date: Date to check. Defaults to today in Beijing timezone.
    """
    if date is None:
        date = dt.datetime.now(tz=SHANGHAI).date()
    return date.weekday() < 5  # Mon=0 .. Fri=4
