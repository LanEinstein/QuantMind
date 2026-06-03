"""``_count_trading_days`` fail-closed contract (codex P2 cycle-5).

A malformed or future buy date must return ``None`` (not the old ``0`` sentinel)
so the caller omits it and TIME_STOP stays *unevaluable* — a spurious ``0`` would
read as "intact" and could both suppress a thesis break AND wrongly grant the
long-term-hold take-profit exemption.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.main import _count_trading_days

_NOW = datetime(2026, 6, 3, 10, 0, tzinfo=UTC)


def test_malformed_date_returns_none() -> None:
    assert _count_trading_days("not-a-date", _NOW) is None
    assert _count_trading_days("", _NOW) is None


def test_future_date_returns_none() -> None:
    assert _count_trading_days("2099-01-01", _NOW) is None


def test_today_returns_zero() -> None:
    # A position bought today legitimately has 0 holding days (not corrupt).
    assert _count_trading_days("2026-06-03", _NOW) == 0


def test_past_date_counts_trading_days() -> None:
    out = _count_trading_days("2026-05-27", _NOW)
    assert isinstance(out, int)
    assert out >= 1
