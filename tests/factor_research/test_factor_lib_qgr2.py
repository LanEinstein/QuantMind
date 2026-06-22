"""Tests for QGR-3 ⑦ tranche-2 factors: 1-day momentum + limit-board structure.

1-day intraday momentum / overnight gap (Gao et al. 2023; §3.1.2) and the
limit-board structure tags (limit streak / broke-board, §3.3, strictly `<d` to
avoid look-ahead). Pure functions — no store / network. Cover the value math,
fail-closed (``None``) paths, the data-availability None-vs-0 distinction
(pre-2020 limit_list_d absent → None; present-but-not-on-board → 0), and the
QGR2 registry wiring (mechanisms reuse EXISTING enum values).
"""

from __future__ import annotations

import math

import pytest

from scripts.factor_research.factor_lib import (
    ALL_FACTORS_BY_NAME,
    QGR2_FACTOR_NAMES,
    QGR2_FACTORS,
    QGR2_FACTORS_BY_NAME,
    broke_board_prev,
    compute_qgr2_factors,
    intraday_return,
    limit_streak_prev,
    overnight_gap,
)


class TestIntradayReturn:
    def test_basic(self) -> None:
        assert intraday_return(10.0, 11.0) == pytest.approx(0.10)
        assert intraday_return(10.0, 9.0) == pytest.approx(-0.10)

    def test_fail_closed(self) -> None:
        assert intraday_return(0.0, 11.0) is None
        assert intraday_return(-1.0, 11.0) is None
        assert intraday_return(math.nan, 11.0) is None
        assert intraday_return(10.0, math.nan) is None


class TestOvernightGap:
    def test_basic(self) -> None:
        assert overnight_gap(10.0, 10.5) == pytest.approx(0.05)
        assert overnight_gap(10.0, 9.5) == pytest.approx(-0.05)

    def test_fail_closed(self) -> None:
        assert overnight_gap(0.0, 10.5) is None
        assert overnight_gap(math.nan, 10.5) is None
        assert overnight_gap(10.0, math.nan) is None


class TestLimitStreakPrev:
    def test_unavailable_is_none(self) -> None:
        # Pre-2020: no limit_list_d snapshot → cannot tell → None (fail-closed).
        assert limit_streak_prev("U", 3.0, available=False) is None

    def test_not_on_board_is_zero(self) -> None:
        # Snapshot present, stock not limit-up the prior day → streak 0 (known).
        assert limit_streak_prev(None, None, available=True) == 0.0
        assert limit_streak_prev("D", 1.0, available=True) == 0.0
        assert limit_streak_prev("Z", 1.0, available=True) == 0.0

    def test_limit_up_returns_streak(self) -> None:
        assert limit_streak_prev("U", 3.0, available=True) == pytest.approx(3.0)

    def test_limit_up_with_bad_times_is_none(self) -> None:
        assert limit_streak_prev("U", math.nan, available=True) is None


class TestBrokeBoardPrev:
    def test_unavailable_is_none(self) -> None:
        assert broke_board_prev("U", 2.0, available=False) is None

    def test_not_on_board_is_zero(self) -> None:
        assert broke_board_prev(None, None, available=True) == 0.0

    def test_broke_flag(self) -> None:
        # limit-up prior day with open_times>0 = sealed then broke = fade tag.
        assert broke_board_prev("U", 2.0, available=True) == 1.0
        assert broke_board_prev("U", 0.0, available=True) == 0.0

    def test_limit_up_with_bad_open_times_is_none(self) -> None:
        # Symmetric with limit_streak_prev: unknown open_times on a 'U' day → None.
        assert broke_board_prev("U", math.nan, available=True) is None


class TestComputeQgr2:
    def test_full_vector(self) -> None:
        vec = compute_qgr2_factors(
            open_price=10.0,
            close=10.5,
            pre_close=10.2,
            prev_limit="U",
            prev_limit_times=2.0,
            prev_open_times=1.0,
            limit_data_available=True,
        )
        assert set(vec) == set(QGR2_FACTOR_NAMES)
        assert vec["intraday_ret_1d"] == pytest.approx(0.05)
        assert vec["overnight_gap_1d"] == pytest.approx(10.0 / 10.2 - 1.0)
        assert vec["limit_streak_prev"] == pytest.approx(2.0)
        assert vec["broke_board_prev"] == 1.0

    def test_pre2020_limit_factors_none_price_factors_ok(self) -> None:
        vec = compute_qgr2_factors(
            open_price=10.0,
            close=10.5,
            pre_close=10.2,
            prev_limit=None,
            prev_limit_times=None,
            prev_open_times=None,
            limit_data_available=False,
        )
        assert vec["intraday_ret_1d"] is not None
        assert vec["overnight_gap_1d"] is not None
        assert vec["limit_streak_prev"] is None
        assert vec["broke_board_prev"] is None


class TestRegistryWiring:
    def test_mechanisms_registered(self) -> None:
        from backend.strategy_evolution.mechanism_registry import EconomicMechanism

        registered = {m.value for m in EconomicMechanism}
        for fdef in QGR2_FACTORS:
            assert fdef.mechanism in registered

    def test_names_unique_and_in_all_registry(self) -> None:
        assert len(QGR2_FACTOR_NAMES) == len(set(QGR2_FACTOR_NAMES))
        for name in QGR2_FACTOR_NAMES:
            assert name in ALL_FACTORS_BY_NAME
            assert QGR2_FACTORS_BY_NAME[name].name == name

    def test_disjoint_from_qgr1_and_round1(self) -> None:
        from scripts.factor_research.factor_lib import FACTOR_NAMES, QGR_FACTOR_NAMES

        assert set(QGR2_FACTOR_NAMES).isdisjoint(QGR_FACTOR_NAMES)
        assert set(QGR2_FACTOR_NAMES).isdisjoint(FACTOR_NAMES)
