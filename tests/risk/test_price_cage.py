"""Tests for the pure A-share price-cage helper (U-E2, backend/risk/price_cage)."""

from __future__ import annotations

import pytest

from backend.risk.price_cage import (
    cage_bounded_buy_limit,
    cage_ceiling,
    is_within_cage,
    tick_size,
)
from backend.risk.stock_meta import Board


class TestTickSize:
    def test_etf_is_milli(self) -> None:
        assert float(tick_size(Board.ETF)) == 0.001

    @pytest.mark.parametrize(
        "board", [Board.SH_MAIN, Board.SZ_MAIN, Board.CHUANGYE, Board.KCHUANG]
    )
    def test_stock_boards_are_cent(self, board: Board) -> None:
        assert float(tick_size(board)) == 0.01

    def test_etf_matched_by_value_not_identity(self) -> None:
        # A board compared by VALUE: a different StrEnum class instance (data
        # layer re-export / test reload) or the plain "etf" value must still
        # resolve to the ETF tick, never the stock tick (codex U-E2 P2).
        assert float(tick_size("etf")) == 0.001  # type: ignore[arg-type]
        # Codex's exact scenario: low-priced ETF cage must be 1.02, not 1.10.
        assert cage_ceiling(1.00, "etf") == 1.02  # type: ignore[arg-type]


class TestCageCeiling:
    def test_main_board_low_price_tick_wins(self) -> None:
        # best_ask 3.00: 2% = 0.06 < 0.10 (10 ticks) → 3.00 + 0.10 = 3.10.
        assert cage_ceiling(3.00, Board.SH_MAIN) == 3.10

    def test_main_board_high_price_pct_wins(self) -> None:
        # best_ask 100.00: 2% = 2.00 > 0.10 → 100 × 1.02 = 102.00.
        assert cage_ceiling(100.00, Board.SZ_MAIN) == 102.00

    def test_etf_uses_milli_tick(self) -> None:
        # ETF best_ask 4.000: 2% = 0.08 > 10×0.001 = 0.01 → 4.000 × 1.02 = 4.08.
        assert cage_ceiling(4.000, Board.ETF) == 4.08

    def test_etf_low_price_pct_still_wins_over_milli_tick(self) -> None:
        # ETF best_ask 1.000: 2% = 0.02 > 10×0.001 = 0.01 → 1.02 (NOT 1.10).
        assert cage_ceiling(1.000, Board.ETF) == 1.02

    def test_chuangye_pct_only_no_tick_floor(self) -> None:
        # ChiNext has NO 10-tick alternative: low price uses plain 2%.
        assert cage_ceiling(3.00, Board.CHUANGYE) == 3.06

    def test_kchuang_pct_only(self) -> None:
        assert cage_ceiling(3.00, Board.KCHUANG) == 3.06

    def test_half_cent_floors_down_not_up(self) -> None:
        # 10.25 × 1.02 = 10.455 — must floor DOWN to 10.45, NOT up to 10.46
        # (10.46 would exceed the legal ≤102% cage → 废单). Codex U-E2 P1.
        assert cage_ceiling(10.25, Board.SH_MAIN) == 10.45

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf"), None])
    def test_bad_best_ask_raises(self, bad: float | None) -> None:
        with pytest.raises(ValueError):
            cage_ceiling(bad, Board.SH_MAIN)  # type: ignore[arg-type]

    def test_non_numeric_best_ask_raises_valueerror(self) -> None:
        # A non-numeric value (Decimal('abc') → InvalidOperation) must surface
        # as ValueError, not crash inside validation (codex U-E2 P2).
        with pytest.raises(ValueError):
            cage_ceiling("not-a-price", Board.SH_MAIN)  # type: ignore[arg-type]


class TestCageBoundedBuyLimit:
    def test_tolerance_bound_wins(self) -> None:
        # last 3.00 ×1.01 = 3.03 < ceiling 3.10 → 3.03.
        out = cage_bounded_buy_limit(
            last_price=3.00, best_ask=3.00, board=Board.SH_MAIN, tolerance_pct=0.01
        )
        assert out == 3.03

    def test_cage_bound_wins(self) -> None:
        # last 3.00 ×1.10 = 3.30 > ceiling 3.10 → capped to 3.10.
        out = cage_bounded_buy_limit(
            last_price=3.00, best_ask=3.00, board=Board.SH_MAIN, tolerance_pct=0.10
        )
        assert out == 3.10

    def test_floored_down_to_cent(self) -> None:
        # tol bound 3.337 floors DOWN to 3.33 (never rounds up past a bound).
        out = cage_bounded_buy_limit(
            last_price=3.337, best_ask=99.0, board=Board.SH_MAIN, tolerance_pct=0.0
        )
        assert out == 3.33

    def test_result_never_exceeds_cage(self) -> None:
        out = cage_bounded_buy_limit(
            last_price=10.00, best_ask=9.99, board=Board.SH_MAIN, tolerance_pct=0.5
        )
        assert is_within_cage(limit_price=out, best_ask=9.99, board=Board.SH_MAIN)

    def test_zero_tolerance(self) -> None:
        out = cage_bounded_buy_limit(
            last_price=5.00, best_ask=5.00, board=Board.SH_MAIN, tolerance_pct=0.0
        )
        assert out == 5.00

    @pytest.mark.parametrize("bad", [-0.01, float("inf"), float("nan"), None, "abc"])
    def test_bad_tolerance_fails_closed(self, bad: object) -> None:
        # inf would silently bypass the last×(1+tol) cap; nan/non-numeric would
        # raise InvalidOperation — all must surface as ValueError (codex U-E2 P2).
        with pytest.raises(ValueError):
            cage_bounded_buy_limit(
                last_price=5.0,
                best_ask=5.0,
                board=Board.SH_MAIN,
                tolerance_pct=bad,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), None])
    def test_bad_last_price_raises(self, bad: float | None) -> None:
        with pytest.raises(ValueError):
            cage_bounded_buy_limit(
                last_price=bad,  # type: ignore[arg-type]
                best_ask=5.0,
                board=Board.SH_MAIN,
                tolerance_pct=0.01,
            )

    def test_half_cent_limit_respects_floored_ceiling(self) -> None:
        # ceiling 10.45 (10.25×1.02=10.455 floored); a wide tol caps to 10.45.
        out = cage_bounded_buy_limit(
            last_price=10.25, best_ask=10.25, board=Board.SH_MAIN, tolerance_pct=1.0
        )
        assert out == 10.45
        assert is_within_cage(limit_price=out, best_ask=10.25, board=Board.SH_MAIN)
        assert not is_within_cage(
            limit_price=10.46, best_ask=10.25, board=Board.SH_MAIN
        )

    def test_bad_best_ask_propagates(self) -> None:
        with pytest.raises(ValueError):
            cage_bounded_buy_limit(
                last_price=5.0, best_ask=0.0, board=Board.SH_MAIN, tolerance_pct=0.01
            )


class TestIsWithinCage:
    def test_within(self) -> None:
        assert is_within_cage(limit_price=3.10, best_ask=3.00, board=Board.SH_MAIN)

    def test_at_ceiling_inclusive(self) -> None:
        ceiling = cage_ceiling(3.00, Board.SH_MAIN)
        assert is_within_cage(limit_price=ceiling, best_ask=3.00, board=Board.SH_MAIN)

    def test_above_ceiling_rejected(self) -> None:
        assert not is_within_cage(limit_price=3.11, best_ask=3.00, board=Board.SH_MAIN)

    def test_etf_subcent_limit_not_falsely_rejected(self) -> None:
        # ETF best_ask 1.005 → exact cap max(1.0251, 1.015) = 1.0251. A valid
        # sub-cent ETF limit 1.025 must pass (codex U-E2 P2); 1.03 must fail.
        assert is_within_cage(limit_price=1.025, best_ask=1.005, board=Board.ETF)
        assert not is_within_cage(limit_price=1.03, best_ask=1.005, board=Board.ETF)

    def test_display_ceiling_floors_below_exact_cap(self) -> None:
        # Display 限价上限 floors to a valid 0.01 price ≤ the exact cap.
        assert cage_ceiling(1.005, Board.ETF) == 1.02

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), None])
    def test_bad_best_ask_fails_closed(self, bad: float | None) -> None:
        # No provable cage (incl. absent None ask) → reject without crashing.
        assert not is_within_cage(
            limit_price=3.00,
            best_ask=bad,  # type: ignore[arg-type]
            board=Board.SH_MAIN,
        )
