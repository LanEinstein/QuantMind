"""Pure-function tests for :mod:`backend.data.suspension` (C-004 / P0-8)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from backend.data.suspension import is_suspended
from backend.models.market import WatchlistMarketSnapshot


def _snapshot(**overrides: object) -> WatchlistMarketSnapshot:
    """Helper returning a vanilla "trading" snapshot with selective overrides."""
    base: dict[str, object] = {
        "code": "600519",
        "name": "贵州茅台",
        "price": 1500.0,
        "open": 1495.0,
        "high": 1510.0,
        "low": 1490.0,
        "prev_close": 1498.0,
        "change_pct": 0.13,
        "volume": 1_000_000.0,
        "amount": 1_500_000_000.0,
        "turnover_rate": 0.5,
        "source": "adata",
        "snapshot_at": datetime(2026, 5, 12, 9, 30, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return WatchlistMarketSnapshot(**base)  # type: ignore[arg-type]


class TestIsSuspended:
    """Three locked heuristics from P0-8 §1.6.1."""

    def test_normal_trading_snapshot_is_not_suspended(self) -> None:
        assert is_suspended(_snapshot()) is False

    def test_zero_price_is_suspended(self) -> None:
        assert is_suspended(_snapshot(price=0.0)) is True

    def test_negative_price_is_suspended(self) -> None:
        """Vendor bug — never report False on a negative price."""
        assert is_suspended(_snapshot(price=-1.0)) is True

    def test_zero_prev_close_is_suspended(self) -> None:
        """First-day listings with no prev_close should fail-closed."""
        assert is_suspended(_snapshot(prev_close=0.0)) is True

    def test_zero_volume_alone_is_not_suspended(self) -> None:
        """A new listing during the auction can show zero volume but
        positive amount; only zero-both is the halt signature."""
        assert is_suspended(_snapshot(volume=0.0, amount=1_000.0)) is False

    def test_zero_volume_and_zero_amount_is_suspended(self) -> None:
        assert is_suspended(_snapshot(volume=0.0, amount=0.0)) is True

    def test_nan_change_pct_is_suspended(self) -> None:
        assert is_suspended(_snapshot(change_pct=math.nan)) is True

    def test_negative_change_pct_alone_is_not_suspended(self) -> None:
        """Down moves are legitimate; only NaN flips the halt flag."""
        assert is_suspended(_snapshot(change_pct=-9.99)) is False

    @pytest.mark.parametrize(
        "field,value",
        [
            ("price", 0.0),
            ("price", -10.0),
            ("prev_close", 0.0),
        ],
    )
    def test_param_price_fields_table(self, field: str, value: float) -> None:
        assert is_suspended(_snapshot(**{field: value})) is True

    def test_amount_zero_alone_is_not_suspended(self) -> None:
        """Some vendors return amount=0 with non-zero volume on micro-cap
        ticks; the halt heuristic needs *both* fields zero."""
        assert is_suspended(_snapshot(volume=100.0, amount=0.0)) is False

    def test_nan_price_is_suspended(self) -> None:
        """Regression: vendors propagate halts as NaN price; ``NaN <= 0``
        is False so the bare comparison misses this case."""
        assert is_suspended(_snapshot(price=math.nan)) is True

    def test_nan_prev_close_is_suspended(self) -> None:
        assert is_suspended(_snapshot(prev_close=math.nan)) is True

    def test_nan_volume_and_amount_is_suspended(self) -> None:
        """NaN volume + NaN amount also matches the second heuristic."""
        assert is_suspended(
            _snapshot(volume=math.nan, amount=math.nan)
        ) is True

    def test_nan_volume_with_positive_amount_is_not_suspended(self) -> None:
        """Single-NaN volume should not trigger by itself (mirror the
        explicit zero-volume-with-amount rule)."""
        assert is_suspended(_snapshot(volume=math.nan, amount=1_000.0)) is False
