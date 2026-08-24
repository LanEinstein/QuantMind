"""Subscription calendars for the institutional-rent reminder (MZ-1).

Reads today's subscribable A-share IPOs (``new_share``, keyed by
``ipo_date`` = subscription day) and convertible bonds (``cb_issue``,
keyed by ``onl_date``). Pure functions over an injected Tushare
``query`` callable so tests run with canned frames and zero network.

Protocol scope (institutional-rent protocol §1/§2): all SSE/SZSE boards
are listed (STAR included, owner decides per account permission); BSE
(``.BJ``) is excluded — its allocation floor needs ~6M CNY capital.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol


class _FrameLike(Protocol):
    """The minimal pandas.DataFrame surface the calendar readers use."""

    empty: bool

    def to_dict(self, orient: str) -> list[dict[str, Any]]: ...


QueryFn = Callable[..., _FrameLike]

# ann_date → onl_date lag for CBs is ~2 trading days; a 14-calendar-day
# trailing window over ann_date always covers today's subscriptions.
_CB_ANN_LOOKBACK_DAYS = 14


@dataclass(frozen=True)
class StockSubscription:
    ts_code: str
    sub_code: str
    name: str
    board: str
    price: float | None  # None = not yet published (normal before T-1 evening)


@dataclass(frozen=True)
class CbSubscription:
    ts_code: str
    onl_code: str
    onl_name: str


def board_of(ts_code: str) -> str:
    """Human board label from a Tushare ts_code (BSE handled by exclusion)."""
    code = ts_code.split(".")[0]
    if code.startswith("688") or code.startswith("689"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith("60"):
        return "沪主板"
    return "深主板"


def normalize_date(value: object) -> str:
    """``2026-08-19`` / ``20260819`` → ``20260819`` (Tushare mixes both)."""
    return str(value).replace("-", "").strip()


def fetch_stock_subscriptions(
    query: QueryFn, date: str
) -> tuple[StockSubscription, ...]:
    """A-share IPOs subscribable on ``date`` (YYYYMMDD), BSE excluded."""
    frame = query("new_share", start_date=date, end_date=date)
    if frame.empty:
        return ()
    out: list[StockSubscription] = []
    for row in frame.to_dict("records"):
        ts_code = str(row.get("ts_code", ""))
        if not ts_code or ts_code.endswith(".BJ"):
            continue
        if normalize_date(row.get("ipo_date", "")) != date:
            continue
        raw_price = row.get("price")
        price = (
            float(raw_price)
            if isinstance(raw_price, int | float) and float(raw_price) > 0
            else None
        )
        out.append(
            StockSubscription(
                ts_code=ts_code,
                sub_code=str(row.get("sub_code", "")),
                name=str(row.get("name", "")),
                board=board_of(ts_code),
                price=price,
            )
        )
    return tuple(sorted(out, key=lambda s: s.ts_code))


def fetch_cb_subscriptions(query: QueryFn, date: str) -> tuple[CbSubscription, ...]:
    """Convertible bonds whose online subscription day (``onl_date``) is ``date``."""
    start = (
        datetime.strptime(date, "%Y%m%d") - timedelta(days=_CB_ANN_LOOKBACK_DAYS)
    ).strftime("%Y%m%d")
    frame = query("cb_issue", start_date=start, end_date=date)
    if frame.empty:
        return ()
    out: list[CbSubscription] = []
    for row in frame.to_dict("records"):
        onl_code = str(row.get("onl_code") or "").strip()
        if not onl_code:
            continue
        if normalize_date(row.get("onl_date", "")) != date:
            continue
        out.append(
            CbSubscription(
                ts_code=str(row.get("ts_code", "")),
                onl_code=onl_code,
                onl_name=str(row.get("onl_name") or "").strip(),
            )
        )
    return tuple(sorted(out, key=lambda c: c.ts_code))
