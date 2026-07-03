"""Unit tests for the D1 panel's pure helpers (beta alignment + forward return).

The heavy PIT ingest is exercised by the ``main --max-rebalances`` smoke run; here
we pin the two pure, look-ahead-free helpers that carry the leak discipline: the
market/stock date alignment feeding the rolling beta, and the single 20td forward
label.
"""

from __future__ import annotations

import pytest

from scripts.factor_research.defensive_d1_panel import (
    _forward_return_20d,
    aligned_market_window,
)


def test_aligned_market_window_aligns_by_date_most_recent_last() -> None:
    dates = ["20180102", "20180103", "20180104", "20180105"]
    adj = [10.0, 11.0, 12.0, 13.0]
    market = {"20180102": 3.0, "20180103": 3.1, "20180104": 3.2, "20180105": 3.3}
    stock, mkt = aligned_market_window(dates, adj, market, pos=3, n=10)
    assert stock == [10.0, 11.0, 12.0, 13.0]  # chronological, most-recent last
    assert mkt == [3.0, 3.1, 3.2, 3.3]


def test_aligned_market_window_skips_missing_market_days() -> None:
    dates = ["20180102", "20180103", "20180104", "20180105"]
    adj = [10.0, 11.0, 12.0, 13.0]
    market = {"20180102": 3.0, "20180104": 3.2, "20180105": 3.3}  # 03 halted
    stock, mkt = aligned_market_window(dates, adj, market, pos=3, n=10)
    # The stock's 20180103 bar is dropped because the market has no bar that day —
    # both sides skip the same date so the pairs stay contemporaneous.
    assert stock == [10.0, 12.0, 13.0]
    assert mkt == [3.0, 3.2, 3.3]


def test_aligned_market_window_respects_n_cap_and_trails_from_pos() -> None:
    dates = ["20180102", "20180103", "20180104", "20180105"]
    adj = [10.0, 11.0, 12.0, 13.0]
    market = {d: 3.0 for d in dates}
    stock, mkt = aligned_market_window(dates, adj, market, pos=2, n=2)
    # pos=2 → trailing two aligned dates ending at index 2.
    assert stock == [11.0, 12.0]
    assert mkt == [3.0, 3.0]


def test_aligned_market_window_drops_nonfinite_and_nonpositive_stock() -> None:
    dates = ["20180102", "20180103", "20180104"]
    adj = [10.0, float("nan"), -5.0]
    market = {d: 3.0 for d in dates}
    stock, mkt = aligned_market_window(dates, adj, market, pos=2, n=10)
    assert stock == [10.0]  # NaN and negative closes dropped fail-closed
    assert mkt == [3.0]


def test_forward_return_20d_uses_bar_20td_ahead() -> None:
    closes = [1.0] * 20 + [1.1]  # index 0 → index 20 is +10%
    assert _forward_return_20d(closes, 0) == pytest.approx(0.1)


def test_forward_return_20d_none_when_too_few_bars() -> None:
    closes = [1.0] * 10
    assert _forward_return_20d(closes, 0) is None


def test_forward_return_20d_none_on_nonpositive_base() -> None:
    closes = [0.0] + [1.0] * 25
    assert _forward_return_20d(closes, 0) is None
