"""Tests for trading hours utility (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.utils.trading_hours import (
    MarketPhase,
    is_call_auction,
    is_closing_call_auction,
    is_opening_call_auction,
    is_trading_day,
    is_trading_hours,
    market_phase,
    t_minus_1_eod_utc,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


class TestTMinus1EodUtc:
    """t_minus_1_eod_utc anchors a frame's fetch_time to the as_of 15:00 close."""

    def test_anchors_to_1500_cst_as_utc(self) -> None:
        import datetime as dt

        got = t_minus_1_eod_utc(dt.date(2026, 5, 29))
        # 2026-05-29 15:00 Asia/Shanghai (UTC+8) == 07:00 UTC same day.
        assert got == dt.datetime(2026, 5, 29, 7, 0, tzinfo=dt.UTC)
        assert got.tzinfo is dt.UTC

    def test_strictly_before_next_session_open(self) -> None:
        import datetime as dt

        # The anchor (Fri 15:00) must be strictly before the next run-day
        # ~09:35 created_at — the invariant the production frame relies on.
        anchor = t_minus_1_eod_utc(dt.date(2026, 5, 29))
        monday_0935 = dt.datetime(
            2026, 6, 1, 9, 35, tzinfo=SHANGHAI
        ).astimezone(dt.UTC)
        assert anchor < monday_0935


class TestIsTradingHours:
    """Tests for is_trading_hours."""

    def test_morning_session(self) -> None:
        # Monday 10:00 Beijing time — should be trading
        now = datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is True

    def test_lunch_break(self) -> None:
        # Monday 12:00 Beijing time — lunch break, not trading
        now = datetime(2026, 3, 23, 12, 0, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is False

    def test_afternoon_session(self) -> None:
        # Monday 14:00 Beijing time — afternoon trading
        now = datetime(2026, 3, 23, 14, 0, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is True

    def test_before_open(self) -> None:
        # Monday 09:00 Beijing time — before market open
        now = datetime(2026, 3, 23, 9, 0, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is False

    def test_after_close(self) -> None:
        # Monday 15:30 Beijing time — after market close
        now = datetime(2026, 3, 23, 15, 30, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is False

    def test_weekend(self) -> None:
        # Saturday 10:00 Beijing time — weekend
        now = datetime(2026, 3, 21, 10, 0, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is False

    def test_exact_open(self) -> None:
        # Monday 09:30 Beijing time — exact open
        now = datetime(2026, 3, 23, 9, 30, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is True

    def test_exact_close(self) -> None:
        # Monday 15:00 Beijing time — exact close time is NOT trading
        now = datetime(2026, 3, 23, 15, 0, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is False

    def test_morning_end(self) -> None:
        # Monday 11:30 Beijing time — exact end of morning session
        now = datetime(2026, 3, 23, 11, 30, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is False

    def test_afternoon_start(self) -> None:
        # Monday 13:00 Beijing time — exact start of afternoon
        now = datetime(2026, 3, 23, 13, 0, tzinfo=SHANGHAI)
        assert is_trading_hours(now) is True


class TestIsTradingDay:
    """Tests for is_trading_day."""

    def test_weekday(self) -> None:
        # Monday
        now = datetime(2026, 3, 23, tzinfo=SHANGHAI)
        assert is_trading_day(now.date()) is True

    def test_saturday(self) -> None:
        now = datetime(2026, 3, 21, tzinfo=SHANGHAI)
        assert is_trading_day(now.date()) is False

    def test_sunday(self) -> None:
        now = datetime(2026, 3, 22, tzinfo=SHANGHAI)
        assert is_trading_day(now.date()) is False


# 2026-03-23 is a Monday (trading day); 2026-03-21 is a Saturday.
_MON = lambda h, m, s=0: datetime(2026, 3, 23, h, m, s, tzinfo=SHANGHAI)  # noqa: E731
_SAT = lambda h, m, s=0: datetime(2026, 3, 21, h, m, s, tzinfo=SHANGHAI)  # noqa: E731


class TestOpeningCallAuction:
    """is_opening_call_auction — 09:15 ≤ t < 09:25 on a trading day (U-E1)."""

    def test_start_inclusive(self) -> None:
        assert is_opening_call_auction(_MON(9, 15)) is True

    def test_mid(self) -> None:
        assert is_opening_call_auction(_MON(9, 20)) is True

    def test_just_before_match(self) -> None:
        assert is_opening_call_auction(_MON(9, 24, 59)) is True

    def test_match_excluded(self) -> None:
        # 09:25 is the single auction match — the order window has closed.
        assert is_opening_call_auction(_MON(9, 25)) is False

    def test_before_window(self) -> None:
        assert is_opening_call_auction(_MON(9, 14, 59)) is False

    def test_continuous_open_not_auction(self) -> None:
        assert is_opening_call_auction(_MON(9, 30)) is False

    def test_weekend(self) -> None:
        assert is_opening_call_auction(_SAT(9, 20)) is False


class TestClosingCallAuction:
    """is_closing_call_auction — 14:57 ≤ t < 15:00 on a trading day (U-E1)."""

    def test_start_inclusive(self) -> None:
        assert is_closing_call_auction(_MON(14, 57)) is True

    def test_mid(self) -> None:
        assert is_closing_call_auction(_MON(14, 59, 59)) is True

    def test_close_excluded(self) -> None:
        assert is_closing_call_auction(_MON(15, 0)) is False

    def test_before_window(self) -> None:
        assert is_closing_call_auction(_MON(14, 56, 59)) is False

    def test_weekend(self) -> None:
        assert is_closing_call_auction(_SAT(14, 58)) is False


class TestIsCallAuction:
    """is_call_auction — opening ∪ closing (U-E1)."""

    def test_opening(self) -> None:
        assert is_call_auction(_MON(9, 18)) is True

    def test_closing(self) -> None:
        assert is_call_auction(_MON(14, 58)) is True

    def test_continuous(self) -> None:
        assert is_call_auction(_MON(10, 0)) is False

    def test_closed(self) -> None:
        assert is_call_auction(_MON(8, 0)) is False


class TestMarketPhase:
    """market_phase — fine-grained phase enum (U-E1)."""

    def test_closed_premarket(self) -> None:
        assert market_phase(_MON(8, 0)) == MarketPhase.CLOSED

    def test_pre_open_auction(self) -> None:
        assert market_phase(_MON(9, 15)) == MarketPhase.PRE_OPEN_AUCTION
        # The 09:25-09:30 quiet gap is still the pre-open period.
        assert market_phase(_MON(9, 27)) == MarketPhase.PRE_OPEN_AUCTION

    def test_continuous_am(self) -> None:
        assert market_phase(_MON(9, 30)) == MarketPhase.CONTINUOUS_AM
        assert market_phase(_MON(11, 29, 59)) == MarketPhase.CONTINUOUS_AM

    def test_lunch_break(self) -> None:
        assert market_phase(_MON(11, 30)) == MarketPhase.LUNCH_BREAK
        assert market_phase(_MON(12, 30)) == MarketPhase.LUNCH_BREAK

    def test_continuous_pm(self) -> None:
        assert market_phase(_MON(13, 0)) == MarketPhase.CONTINUOUS_PM
        assert market_phase(_MON(14, 56, 59)) == MarketPhase.CONTINUOUS_PM

    def test_closing_auction(self) -> None:
        assert market_phase(_MON(14, 57)) == MarketPhase.CLOSING_AUCTION
        assert market_phase(_MON(14, 59, 59)) == MarketPhase.CLOSING_AUCTION

    def test_post_close(self) -> None:
        assert market_phase(_MON(15, 0)) == MarketPhase.POST_CLOSE
        assert market_phase(_MON(16, 0)) == MarketPhase.POST_CLOSE

    def test_weekend_closed(self) -> None:
        assert market_phase(_SAT(10, 0)) == MarketPhase.CLOSED


class TestCallAuctionTzHandling:
    """Naive + cross-tz inputs normalise like is_trading_hours (U-E1)."""

    def test_naive_assumed_shanghai(self) -> None:
        naive = datetime(2026, 3, 23, 9, 20)  # no tzinfo
        assert is_opening_call_auction(naive) is True

    def test_utc_converted(self) -> None:
        # 01:20 UTC == 09:20 Shanghai (opening auction).
        utc = datetime(2026, 3, 23, 1, 20, tzinfo=ZoneInfo("UTC"))
        assert is_opening_call_auction(utc) is True


class TestIsTradingHoursUnchanged:
    """U-E1 amendment: is_trading_hours semantics MUST stay unchanged."""

    def test_closing_auction_window_still_trading(self) -> None:
        # is_trading_hours stays coarse: 13:00-15:00 incl 14:57-15:00.
        assert is_trading_hours(_MON(14, 58)) is True

    def test_pre_open_auction_not_trading(self) -> None:
        assert is_trading_hours(_MON(9, 20)) is False
