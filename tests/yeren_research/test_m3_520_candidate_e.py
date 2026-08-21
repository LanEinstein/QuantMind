import numpy as np

from scripts.yeren_research.m3_520 import RuleFeatures
from scripts.yeren_research.m3_520_candidate_e import (
    CostModel,
    _fill_delay,
    _gate_established,
    build_st_mask,
    build_universe,
    simulate_trades_e,
)
from scripts.yeren_research.pit_priced_panel import PricedSeries


def _series(code, dates, opens, closes, adj=None):
    length = len(dates)
    return PricedSeries(
        code=code,
        dates=np.asarray(dates, dtype=np.int32),
        opens=np.asarray(opens, dtype=float),
        closes=np.asarray(closes, dtype=float),
        pct_chg=np.zeros(length),
        adj=np.asarray(adj, dtype=float) if adj is not None else np.ones(length),
    )


def _features(
    length, *, entry_at, ma_short, ma_mid, early_exit_at=(), full_cross_at=()
):
    entry_signal = np.zeros(length, dtype=bool)
    entry_signal[list(entry_at)] = True
    early_exit_signal = np.zeros(length, dtype=bool)
    early_exit_signal[list(early_exit_at)] = True
    full_cross_signal = np.zeros(length, dtype=bool)
    full_cross_signal[list(full_cross_at)] = True
    return RuleFeatures(
        ma_short=np.asarray(ma_short, dtype=float),
        ma_mid=np.asarray(ma_mid, dtype=float),
        ma_long=np.full(length, np.nan),
        entry_signal=entry_signal,
        early_exit_signal=early_exit_signal,
        full_cross_signal=full_cross_signal,
    )


def test_cost_model_charges_stamp_duty_only_on_the_sell_side():
    costs = CostModel(
        commission_rate=0.00025,
        transfer_fee_rate=0.00001,
        stamp_duty_rate=0.001,
        slippage_rate=0.0,
        min_commission=0.0,  # isolate the percentage rates from the floor
    )

    flat = costs.net_return_pct(10.0, 10.0)

    assert flat < 0
    assert np.isclose(flat, (0.99874 / 1.00026 - 1.0) * 100.0)


def test_cost_model_applies_the_minimum_commission_floor_on_a_small_order():
    # One lot (100 shares) at 10.0 is a ¥1,000 notional; 0.025% of that is
    # ¥0.25, well under the ¥5 floor, so the floor -- not the rate -- must
    # set the actual commission charged on each leg.
    costs = CostModel(
        commission_rate=0.00025,
        transfer_fee_rate=0.0,
        stamp_duty_rate=0.0,
        slippage_rate=0.0,
        min_commission=5.0,
    )
    floored = costs.net_return_pct(10.0, 10.0)

    unfloored = CostModel(
        commission_rate=0.00025,
        transfer_fee_rate=0.0,
        stamp_duty_rate=0.0,
        slippage_rate=0.0,
        min_commission=0.0,
    ).net_return_pct(10.0, 10.0)

    assert floored < unfloored
    # paid = 1000 + 5 (floor); received = 1000 - 5 (floor).
    assert np.isclose(floored, (995.0 / 1005.0 - 1.0) * 100.0)


def test_cost_model_floor_does_not_bind_on_a_large_notional():
    # 100 shares at 1000.0 is a ¥100,000 notional; 0.025% of that is ¥25,
    # above the ¥5 floor, so the rate -- not the floor -- should govern.
    costs = CostModel(
        commission_rate=0.00025,
        transfer_fee_rate=0.0,
        stamp_duty_rate=0.0,
        slippage_rate=0.0,
        min_commission=5.0,
    )

    flat = costs.net_return_pct(1000.0, 1000.0)

    assert np.isclose(flat, (99975.0 / 100025.0 - 1.0) * 100.0)


def test_gate_established_requires_a_bar_strictly_above_mid_ma():
    features = _features(6, entry_at=(), ma_short=[9, 9, 9, 11, 9, 9], ma_mid=[10] * 6)

    assert _gate_established(features, entry_index=0, held_last_index=5) is True
    assert _gate_established(features, entry_index=0, held_last_index=2) is False


def test_fill_delay_is_zero_for_an_ordinary_next_open():
    calendar_index = {20230103: 0, 20230104: 1, 20230105: 2}

    assert _fill_delay(calendar_index, 20230103, 20230104) == 0
    assert _fill_delay(calendar_index, 20230103, 20230105) == 1


def test_build_st_mask_reflects_the_pit_interval_the_date_falls_in():
    timeline = {
        "000001.SZ": (
            np.asarray([20200101, 20220101, 20220601], dtype=np.int64),
            np.asarray([False, True, False]),
        )
    }
    dates = np.asarray([20200601, 20220301, 20221231], dtype=np.int32)

    mask = build_st_mask(dates, "000001.SZ", timeline)

    np.testing.assert_array_equal(mask, [False, True, False])


def test_build_st_mask_defaults_to_not_st_for_an_unknown_code():
    mask = build_st_mask(np.asarray([20200101], dtype=np.int32), "999999.SZ", {})

    assert mask.tolist() == [False]


def test_build_universe_excludes_bj_but_keeps_aligned_sh_sz():
    series = (
        _series("000001.SZ", [20230103], [10.0], [10.0]),
        _series("920000.BJ", [20230103], [10.0], [10.0]),
    )

    working, report = build_universe(series)

    assert [item.code for item in working] == ["000001.SZ"]
    assert report["securities_excluded_for_bj_exchange"] == 1
    assert report["securities_excluded_for_misalignment"] == 0


def test_ordinary_trade_closes_at_adjusted_open_and_gate_established_when_crossed():
    dates = list(range(1, 9))
    series = _series(
        "000001.SZ",
        dates,
        opens=[10, 10, 11, 11, 11, 11, 12, 12],
        closes=[10, 10, 10, 10, 10, 9, 9, 9],
        adj=[2, 2, 2, 2, 2, 2, 2, 2],
    )
    # entry signal at index 1 -> entry at index 2 (adjusted open 22).
    # 5MA > 20MA at index 4 establishes the gate; early-exit fires at index 5.
    features = _features(
        8,
        entry_at=(1,),
        ma_short=[np.nan, 9, 9, 9, 11, 9, 9, 9],
        ma_mid=[np.nan, 10, 10, 10, 10, 10, 10, 10],
        early_exit_at=(5,),
    )
    calendar_index = {date: date for date in dates}
    # Generous limits at the entry/exit bars so a missing-row flag doesn't
    # override the cohort this test means to check.
    limits = {
        ("000001.SZ", 3): {"up_limit": 99999.999, "down_limit": 0.01},
        ("000001.SZ", 7): {"up_limit": 99999.999, "down_limit": 0.01},
    }

    trades, counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=8,
        limits=limits,
        calendar_index=calendar_index,
        costs=CostModel(
            slippage_rate=0.0,
            commission_rate=0.0,
            stamp_duty_rate=0.0,
            transfer_fee_rate=0.0,
        ),
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.status == "closed"
    assert trade.entry_price == 22.0  # adjusted open = 11 * 2
    assert trade.exit_price == 24.0  # adjusted open = 12 * 2
    assert trade.cohort == "primary"
    assert trade.gross_return_pct == (24.0 / 22.0 - 1.0) * 100.0
    assert counts["entry_void_up_limit"] == 0


def test_gate_never_established_is_tagged_disclosure_only():
    dates = list(range(1, 6))
    series = _series("000001.SZ", dates, opens=[10] * 5, closes=[10] * 5)
    # 5MA stays below 20MA for the whole holding period: gate never fires.
    features = _features(
        5,
        entry_at=(0,),
        ma_short=[9, 8, 7, 6, 5],
        ma_mid=[10, 10, 10, 10, 10],
        early_exit_at=(2,),
    )
    calendar_index = {date: date for date in dates}
    limits = {
        ("000001.SZ", 2): {"up_limit": 99999.999, "down_limit": 0.01},
        ("000001.SZ", 4): {"up_limit": 99999.999, "down_limit": 0.01},
    }

    trades, _counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=5,
        limits=limits,
        calendar_index=calendar_index,
        costs=CostModel(),
    )

    assert len(trades) == 1
    assert trades[0].status == "closed"
    assert trades[0].cohort == "disclosure_only"


def test_entry_at_up_limit_open_is_voided_not_delayed():
    dates = list(range(1, 5))
    series = _series("000001.SZ", dates, opens=[10, 11, 11, 11], closes=[10] * 4)
    features = _features(4, entry_at=(0,), ma_short=[np.nan] * 4, ma_mid=[np.nan] * 4)
    calendar_index = {date: date for date in dates}
    limits = {("000001.SZ", 2): {"up_limit": 11.0, "down_limit": 9.0}}

    trades, counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=4,
        limits=limits,
        calendar_index=calendar_index,
        costs=CostModel(),
    )

    assert trades == ()
    assert counts["entry_void_up_limit"] == 1


def test_entry_signal_with_no_next_bar_is_counted_and_produces_no_trade():
    dates = [1]
    series = _series("000001.SZ", dates, opens=[10], closes=[10])
    features = _features(1, entry_at=(0,), ma_short=[np.nan], ma_mid=[np.nan])

    trades, counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=1,
        limits={},
        calendar_index={1: 0},
        costs=CostModel(),
    )

    assert trades == ()
    assert counts["entry_signals_without_any_next_bar"] == 1


def test_exit_blocked_by_down_limit_retries_until_a_fillable_open():
    # dates 1..7 (index i -> date i+1). Entry signal on date1, entry fills at
    # date2. Exit signal on date2; first exit candidate (date3) is blocked at
    # its own down_limit; the retry lands on date4, which is fillable.
    dates = list(range(1, 8))
    series = _series(
        "000001.SZ", dates, opens=[10, 10, 10, 9.5, 9, 9, 10], closes=[10] * 7
    )
    features = _features(
        7,
        entry_at=(0,),
        ma_short=[np.nan] * 7,
        ma_mid=[np.nan] * 7,
        early_exit_at=(1,),
    )
    calendar_index = {date: date for date in dates}
    limits = {
        ("000001.SZ", 3): {"up_limit": 11.0, "down_limit": 10.0},
        ("000001.SZ", 4): {"up_limit": 11.0, "down_limit": 9.0},
    }

    trades, counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=7,
        limits=limits,
        calendar_index=calendar_index,
        costs=CostModel(),
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade.status == "closed"
    assert trade.exit_index == 3  # skipped index 2 (blocked at 10.0), filled on date4
    assert trade.exit_delay_days == 1
    assert counts["exit_no_fill_fact"] == 0


def test_exit_blocked_all_the_way_to_window_edge_marks_open_at_window_end():
    dates = list(range(1, 6))
    series = _series("000001.SZ", dates, opens=[10, 10, 9, 9, 9], closes=[10] * 5)
    features = _features(
        5, entry_at=(0,), ma_short=[np.nan] * 5, ma_mid=[np.nan] * 5, early_exit_at=(1,)
    )
    calendar_index = {date: date for date in dates}
    limits = {
        ("000001.SZ", 3): {"up_limit": 11.0, "down_limit": 9.0},
        ("000001.SZ", 4): {"up_limit": 11.0, "down_limit": 9.0},
        ("000001.SZ", 5): {"up_limit": 11.0, "down_limit": 9.0},
    }

    trades, counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=5,
        limits=limits,
        calendar_index=calendar_index,
        costs=CostModel(),
    )

    assert len(trades) == 1
    assert trades[0].status == "open_at_window_end"
    assert trades[0].exit_index == 4
    assert counts["exit_no_fill_fact"] == 0


def test_exit_signal_on_the_final_stored_bar_has_no_fill_fact():
    dates = list(range(1, 4))
    series = _series("000001.SZ", dates, opens=[10, 10, 8], closes=[10, 10, 8])
    features = _features(
        3, entry_at=(0,), ma_short=[np.nan] * 3, ma_mid=[np.nan] * 3, early_exit_at=(2,)
    )
    calendar_index = {date: date for date in dates}

    trades, counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=3,
        limits={},
        calendar_index=calendar_index,
        costs=CostModel(),
    )

    assert len(trades) == 1
    assert trades[0].status == "no_fill_fact"
    assert np.isnan(trades[0].net_return_pct)
    assert counts["exit_no_fill_fact"] == 1


def test_missing_limit_row_is_flagged_and_excluded_from_primary_or_disclosure():
    dates = list(range(1, 4))
    series = _series("000001.SZ", dates, opens=[10, 10, 11], closes=[10, 10, 11])
    features = _features(
        3,
        entry_at=(0,),
        ma_short=[9, 9, 11],
        ma_mid=[10, 10, 10],
        early_exit_at=(1,),
    )
    calendar_index = {date: date for date in dates}
    # No limits dict entry at all for the exit candidate bar (date 3).

    trades, _counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=3,
        limits={},
        calendar_index=calendar_index,
        costs=CostModel(),
    )

    assert len(trades) == 1
    assert trades[0].status == "closed"
    assert trades[0].exit_limit_row_missing is True
    assert trades[0].cohort == "flagged_unverified_fill"


def test_full_cross_alone_still_triggers_the_exit_when_early_exit_signal_is_false():
    # Regression for the P1 codex found: a day can be a down-cross
    # (full_cross_signal) without also being a slope turn-down
    # (early_exit_signal). The exit trigger must be the union, not
    # early_exit_signal alone, or this position never closes.
    dates = list(range(1, 6))
    series = _series("000001.SZ", dates, opens=[10, 10, 10, 8, 8], closes=[10] * 5)
    features = _features(
        5,
        entry_at=(0,),
        ma_short=[np.nan] * 5,
        ma_mid=[np.nan] * 5,
        early_exit_at=(),  # deliberately empty
        full_cross_at=(2,),
    )
    calendar_index = {date: date for date in dates}
    limits = {("000001.SZ", 4): {"up_limit": 99999.999, "down_limit": 0.01}}

    trades, counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=5,
        limits=limits,
        calendar_index=calendar_index,
        costs=CostModel(),
    )

    assert len(trades) == 1
    assert trades[0].status == "closed"
    assert trades[0].exit_index == 3  # date4, the bar after the full_cross signal
    assert counts["exit_no_fill_fact"] == 0


def test_touched_dates_with_buffer_is_empty_when_the_only_entry_is_st_flagged():
    from scripts.yeren_research.m3_520 import RuleSpec
    from scripts.yeren_research.m3_520_candidate_e import _touched_dates_with_buffer

    spec = RuleSpec(short_window=2, mid_window=3, long_window=4, stop_days=1)
    closes = [40, 34, 28, 23, 19, 17, 20, 14, 11, 8, 6]
    dates = list(range(20200101, 20200101 + len(closes)))
    series = _series("000001.SZ", dates, opens=closes, closes=closes)
    # entry_signal[6] is True for this fixture (verified against compute_features).

    st_timeline_none = {}
    open_touched = _touched_dates_with_buffer(
        (series,),
        spec=spec,
        start_date=dates[0],
        end_date=dates[-1],
        st_timeline=st_timeline_none,
    )
    assert open_touched  # sanity: this fixture does produce a touched entry

    st_timeline_blocking = {
        "000001.SZ": (
            np.asarray([dates[0]], dtype=np.int64),
            np.asarray([True]),
        )
    }
    st_blocked_touched = _touched_dates_with_buffer(
        (series,),
        spec=spec,
        start_date=dates[0],
        end_date=dates[-1],
        st_timeline=st_timeline_blocking,
    )
    assert st_blocked_touched == set()


def test_closed_trade_mae_excludes_the_exit_bars_close():
    # The exit bar's close happens after the position was sold at that bar's
    # open, so a crash in that close must not count toward MAE. Regression
    # for the codex finding: the old code included it.
    dates = list(range(1, 5))
    series = _series("000001.SZ", dates, opens=[10, 10, 10, 10], closes=[10, 10, 10, 1])
    features = _features(
        4, entry_at=(0,), ma_short=[np.nan] * 4, ma_mid=[np.nan] * 4, early_exit_at=(2,)
    )
    calendar_index = {date: date for date in dates}
    limits = {("000001.SZ", 4): {"up_limit": 99999.999, "down_limit": 0.01}}

    trades, _counts = simulate_trades_e(
        series,
        features,
        entry_signal=features.entry_signal,
        start_date=1,
        end_date=4,
        limits=limits,
        calendar_index=calendar_index,
        costs=CostModel(),
    )

    assert len(trades) == 1
    assert trades[0].status == "closed"
    assert trades[0].exit_index == 3  # date4, the crashed-close bar
    assert trades[0].mae_pct == 0.0  # held closes were [10, 10]; the crash excluded


def test_touched_dates_drops_an_up_limit_voided_entry_once_limits_are_known():
    # Pass 1 (no limit knowledge yet) treats the entry as unconstrained and
    # touches its date. Once that date's up_limit is known to block it
    # (passed back in as `limits`, mirroring evaluate_window's refinement
    # loop), the entry must no longer be touched -- this is the mechanism
    # the fixed-point loop in evaluate_window relies on to converge instead
    # of looping on a phantom entry forever.
    from scripts.yeren_research.m3_520 import RuleSpec
    from scripts.yeren_research.m3_520_candidate_e import _touched_dates_with_buffer

    spec = RuleSpec(short_window=2, mid_window=3, long_window=4, stop_days=1)
    closes = [40, 34, 28, 23, 19, 17, 20, 14, 11, 8, 6]
    dates = list(range(20200101, 20200101 + len(closes)))
    series = _series("000001.SZ", dates, opens=closes, closes=closes)
    st_timeline = {}

    pass1 = _touched_dates_with_buffer(
        (series,),
        spec=spec,
        start_date=dates[0],
        end_date=dates[-1],
        st_timeline=st_timeline,
    )
    entry_date = dates[7]  # entry_signal fires at index 6 -> entry_index 7
    assert entry_date in {d for _c, d in pass1}

    blocking_limits = {("000001.SZ", entry_date): {"up_limit": 1.0, "down_limit": 0.01}}
    pass2 = _touched_dates_with_buffer(
        (series,),
        spec=spec,
        start_date=dates[0],
        end_date=dates[-1],
        st_timeline=st_timeline,
        limits=blocking_limits,
    )
    # This fixture's only entry is now voided and there is no other entry
    # signal to reveal, so the correctly-refined touched set is empty.
    assert pass2 == set()
