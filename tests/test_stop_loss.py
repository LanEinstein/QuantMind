"""Tests for StopLossMonitor."""

from __future__ import annotations

from backend.broker.models import Position
from backend.risk.stop_loss import check_stop_loss, check_trailing_stop, scan_positions


class TestCheckStopLoss:
    def test_triggered(self) -> None:
        assert check_stop_loss(100.0, 91.0, 0.08) is True

    def test_not_triggered(self) -> None:
        assert check_stop_loss(100.0, 93.0, 0.08) is False

    def test_exactly_at_threshold(self) -> None:
        # -8% exactly: 100 -> 92.0
        assert check_stop_loss(100.0, 92.0, 0.08) is True

    def test_profit_position(self) -> None:
        assert check_stop_loss(100.0, 110.0, 0.08) is False

    def test_zero_cost_price(self) -> None:
        assert check_stop_loss(0.0, 10.0, 0.08) is False


class TestCheckTrailingStop:
    def test_triggered(self) -> None:
        # Peak 120, current 107 = -10.8% drawdown
        assert check_trailing_stop(120.0, 107.0, 0.10) is True

    def test_not_triggered(self) -> None:
        assert check_trailing_stop(120.0, 110.0, 0.10) is False

    def test_exactly_at_threshold(self) -> None:
        # Peak 100, current 90 = -10% exactly
        assert check_trailing_stop(100.0, 90.0, 0.10) is True

    def test_zero_peak(self) -> None:
        assert check_trailing_stop(0.0, 10.0, 0.10) is False


def _pos(code: str, cost: float, mv: float) -> Position:
    return Position(
        code=code, volume=100, available_volume=100,
        cost_price=cost, market_value=mv,
        unrealized_pnl=mv - cost * 100,
        unrealized_pnl_pct=(mv - cost * 100) / (cost * 100) if cost > 0 else 0,
    )


class TestScanPositions:
    def test_one_triggered(self) -> None:
        positions = (
            _pos("600519", 100.0, 9100.0),  # -9% -> trigger
            _pos("000001", 10.0, 950.0),   # -5% -> no trigger
        )
        prices = {"600519": 91.0, "000001": 9.5}
        result = scan_positions(positions, prices, 0.08, {}, 0.10)
        assert "600519" in result
        assert "000001" not in result

    def test_none_triggered(self) -> None:
        positions = (_pos("600519", 100.0, 9800.0),)
        result = scan_positions(positions, {"600519": 98.0}, 0.08, {}, 0.10)
        assert result == ()

    def test_empty_positions(self) -> None:
        result = scan_positions((), {}, 0.08, {}, 0.10)
        assert result == ()

    def test_missing_price_skipped(self) -> None:
        positions = (_pos("600519", 100.0, 9000.0),)
        result = scan_positions(positions, {}, 0.08, {}, 0.10)
        assert result == ()
