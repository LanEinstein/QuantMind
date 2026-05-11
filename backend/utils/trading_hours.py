"""Trading hours utility for A-share market.

Pure stdlib (datetime + zoneinfo); no IO, no LLM, no data-layer
dependency. Lives under ``backend.utils`` so ``backend.risk`` can
import it without violating the P0-10 redline that forbids
``backend.risk`` from depending on ``backend.{llm,agents,mirofish,data}``.

C-007 (Phase C) will swap ``is_trading_day`` to read the static
``config/holidays.yaml`` calendar; until then it is a Mon–Fri check.
"""

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
    """Check if the given date is a trading day.

    Currently a Mon–Fri check. C-007 will swap this to read the static
    ``config/holidays.yaml`` calendar (P0-6 acceptance window scaffold).

    Args:
        date: Date to check. Defaults to today in Beijing timezone.
    """
    if date is None:
        date = dt.datetime.now(tz=SHANGHAI).date()
    return date.weekday() < 5  # Mon=0 .. Fri=4
