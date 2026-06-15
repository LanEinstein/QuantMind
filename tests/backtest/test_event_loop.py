"""AE-004 event-loop primitives — monotonic clock + look-ahead guard."""

from __future__ import annotations

import pytest

from backend.backtest.event_loop import (
    BacktestClock,
    ClockViolationError,
    DayBar,
)
from tests.backtest._builders import make_bar


def test_daybar_limit_gate_properties() -> None:
    at_up = make_bar("600000", "20260102", open_cents=11_000, limit_up_cents=11_000)
    at_down = make_bar("600000", "20260102", open_cents=9_000, limit_down_cents=9_000)
    free = make_bar("600000", "20260102", open_cents=10_000)
    assert at_up.at_limit_up
    assert at_down.at_limit_down
    assert not free.at_limit_up
    assert not free.at_limit_down


def test_daybar_validation() -> None:
    with pytest.raises(ValueError):
        make_bar("600000", "2026-01-02", open_cents=10_000)  # bad date shape
    with pytest.raises(ValueError):
        make_bar("600000", "20260102", open_cents=0)  # non-positive price


def test_clock_advances_monotonically() -> None:
    clock = BacktestClock(("20260102", "20260105", "20260106"))
    assert clock.current_day == "20260102"
    assert clock.advance() == "20260105"
    assert clock.advance() == "20260106"
    assert clock.exhausted
    assert clock.advance() is None


def test_clock_dedups_and_sorts() -> None:
    clock = BacktestClock(("20260106", "20260102", "20260106"))
    assert clock.days == ("20260102", "20260106")


def test_clock_rejects_forward_read() -> None:
    clock = BacktestClock(("20260102", "20260105"))
    clock.assert_readable("20260102")  # current — ok
    with pytest.raises(ClockViolationError):
        clock.assert_readable("20260105")  # future — look-ahead


def test_clock_rejects_empty_and_bad_dates() -> None:
    with pytest.raises(ClockViolationError):
        BacktestClock(())
    with pytest.raises(ClockViolationError):
        BacktestClock(("2026-01-02",))


def test_daybar_is_frozen() -> None:
    bar = make_bar("600000", "20260102", open_cents=10_000)
    with pytest.raises((AttributeError, TypeError)):
        bar.open_cents = 1  # type: ignore[misc]
    assert isinstance(bar, DayBar)
