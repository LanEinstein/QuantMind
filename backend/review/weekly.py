"""Weekly deep-review window resolution + aggregation (AA-003).

Pure functions. The week under review is the ISO week containing the
last trading day strictly before ``now`` — this makes the Saturday
weekend run, the Sunday catch-up, and a post-week holiday catch-up all
agree on the same ``week_key``, while a mid-week holiday catch-up sees
``complete=False`` and waits (a premature weekly record would block the
real weekend review through the idempotence check).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from backend.review.models import (
    DailyReviewRecord,
    ReviewLane,
    TradeSide,
    WeeklyReviewRecord,
)
from backend.utils.trading_hours import is_trading_day

MAX_LOOKBACK_DAYS = 30
"""Longest gap between trading days the resolver tolerates (the longest
A-share closure — Spring Festival + weekends — is well under this)."""


@dataclass(frozen=True)
class ReviewWeek:
    """Resolved review window (one ISO week)."""

    week_key: str
    window_start: date
    window_end: date
    last_trading_date: date
    expected_trade_dates: tuple[str, ...]
    complete: bool

    @property
    def window_start_iso(self) -> str:
        return self.window_start.isoformat()

    @property
    def window_end_iso(self) -> str:
        return self.window_end.isoformat()


def resolve_review_week(
    now: datetime,
    *,
    is_trading_day_fn: Callable[[date], bool] = is_trading_day,
) -> ReviewWeek | None:
    """Resolve the ISO week containing the last completed session.

    ``None`` when no trading day exists in the past
    :data:`MAX_LOOKBACK_DAYS` (a corrupted calendar — the caller skips,
    fail-closed).
    """
    today = now.date()
    last_trading: date | None = None
    for offset in range(1, MAX_LOOKBACK_DAYS + 1):
        candidate = today - timedelta(days=offset)
        if is_trading_day_fn(candidate):
            last_trading = candidate
            break
    if last_trading is None:
        return None

    monday = last_trading - timedelta(days=last_trading.weekday())
    window = tuple(monday + timedelta(days=i) for i in range(7))
    expected = tuple(
        d.isoformat()
        for d in window
        if d < today and is_trading_day_fn(d)
    )
    complete = all(
        not is_trading_day_fn(d) for d in window if d >= today
    )
    iso = last_trading.isocalendar()
    return ReviewWeek(
        week_key=f"{iso.year}-W{iso.week:02d}",
        window_start=monday,
        window_end=window[-1],
        last_trading_date=last_trading,
        expected_trade_dates=expected,
        complete=complete,
    )


def build_weekly_review(
    *,
    week: ReviewWeek,
    created_at: datetime,
    lane: ReviewLane,
    daily_records: Sequence[DailyReviewRecord],
    policy_hash: str | None,
) -> WeeklyReviewRecord:
    """Aggregate the week's daily records into one frozen row (pure)."""
    in_window = [
        r
        for r in daily_records
        if week.window_start_iso <= r.trade_date <= week.window_end_iso
    ]
    reviewed = tuple(sorted({r.trade_date for r in in_window}))
    missing = tuple(
        d for d in week.expected_trade_dates if d not in set(reviewed)
    )

    facts = [f for r in in_window for f in r.trade_facts]
    sells = [f for f in facts if f.side is TradeSide.SELL]
    sells_with_return = [
        f for f in sells if f.holding_return_pct is not None
    ]
    bps_values = [
        f.execution_vs_vwap_bps
        for f in facts
        if f.execution_vs_vwap_bps is not None
    ]
    counterfactuals = [c for r in in_window for c in r.counterfactuals]

    return WeeklyReviewRecord(
        week_key=week.week_key,
        window_start=week.window_start_iso,
        window_end=week.window_end_iso,
        created_at=created_at,
        lane=lane,
        policy_hash=policy_hash,
        expected_trade_dates=week.expected_trade_dates,
        reviewed_trade_dates=reviewed,
        missing_trade_dates=missing,
        total_trades=len(facts),
        buy_count=sum(1 for f in facts if f.side is TradeSide.BUY),
        sell_count=len(sells),
        sell_with_return_count=len(sells_with_return),
        sell_win_count=sum(
            1
            for f in sells_with_return
            if (f.holding_return_pct or 0.0) > 0.0
        ),
        avg_execution_vs_vwap_bps=(
            sum(bps_values) / len(bps_values) if bps_values else None
        ),
        total_fees_cny=sum(
            f.commission + f.stamp_tax + f.transfer_fee for f in facts
        ),
        risk_rejected_total=sum(r.risk_rejected_count for r in in_window),
        builder_early_return_total=sum(
            r.builder_early_return_count for r in in_window
        ),
        counterfactual_total=len(counterfactuals),
        counterfactual_promotable_total=sum(
            1 for c in counterfactuals if c.promotable
        ),
    )


__all__ = [
    "MAX_LOOKBACK_DAYS",
    "ReviewWeek",
    "build_weekly_review",
    "resolve_review_week",
]
