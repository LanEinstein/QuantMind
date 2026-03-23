"""Tests for trading hours utility (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.data.trading_hours import is_trading_day, is_trading_hours

SHANGHAI = ZoneInfo("Asia/Shanghai")


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
