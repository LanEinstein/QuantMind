import numpy as np

from scripts.yeren_research.m3_520_candidate_e import CostModel
from scripts.yeren_research.m3_right_side_wave_rule import (
    WaveFeatures,
    WaveSpec,
    compute_wave_features,
    simulate_wave_trades,
    statutory_limit_pct,
)
from scripts.yeren_research.pit_limit_panel import align_limits
from scripts.yeren_research.pit_priced_panel import PricedSeries

# A miniature spec so fixtures stay hand-checkable; the frozen study runs the
# WaveSpec defaults (5/20/30, 250-bar lookback, 10-bar entry window).
SMALL = WaveSpec(
    short_window=2,
    mid_window=3,
    long_window=4,
    lookback_bars=5,
    entry_window_bars=3,
)
FREE_COSTS = CostModel(
    commission_rate=0.0,
    transfer_fee_rate=0.0,
    stamp_duty_rate=0.0,
    slippage_rate=0.0,
    min_commission=0.0,
)


def _dates(length, start=20200101):
    return np.asarray([start + day for day in range(length)], dtype=np.int32)


def _series(code, dates, opens, closes, pct_chg=None, adj=None):
    length = len(dates)
    return PricedSeries(
        code=code,
        dates=np.asarray(dates, dtype=np.int32),
        opens=np.asarray(opens, dtype=float),
        closes=np.asarray(closes, dtype=float),
        pct_chg=(
            np.asarray(pct_chg, dtype=float)
            if pct_chg is not None
            else np.zeros(length)
        ),
        adj=np.asarray(adj, dtype=float) if adj is not None else np.ones(length),
    )


def _features(length, *, activation_at=(), structure_at=(), pullback_at=(), exit_at=()):
    def mask(indices):
        out = np.zeros(length, dtype=bool)
        out[list(indices)] = True
        return out

    nan = np.full(length, np.nan)
    return WaveFeatures(
        ma_short=nan,
        ma_mid=nan,
        ma_long=nan,
        range_position=nan,
        activation=mask(activation_at),
        structure=mask(structure_at),
        pullback=mask(pullback_at),
        exit_signal=mask(exit_at),
    )


def _replay(series, features, *, st=None, up=None, down=None, spec=SMALL, **kwargs):
    length = len(series.dates)
    calendar_index = {int(day): i for i, day in enumerate(series.dates)}
    return simulate_wave_trades(
        series,
        features,
        spec=spec,
        st_mask=st if st is not None else np.zeros(length, dtype=bool),
        up_limits=up if up is not None else np.full(length, np.nan),
        down_limits=down if down is not None else np.full(length, np.nan),
        start_date=int(series.dates[0]),
        end_date=int(series.dates[-1]),
        calendar_index=calendar_index,
        costs=FREE_COSTS,
        **kwargs,
    )


# --- statutory limit regimes -------------------------------------------------


def test_statutory_limit_pct_follows_the_board_and_its_effective_date():
    dates = np.asarray([20200821, 20200824, 20200825], dtype=np.int32)

    assert np.allclose(statutory_limit_pct("688001.SH", dates), 0.20)
    assert np.allclose(statutory_limit_pct("600000.SH", dates), 0.10)
    # ChiNext moved from 10% to 20% on the registration-reform date itself.
    assert np.allclose(statutory_limit_pct("300001.SZ", dates), [0.10, 0.20, 0.20])


def test_activation_threshold_is_half_the_boards_own_limit():
    # +6% is a big candle under a 10% ceiling but only a small one under 20%,
    # so the same bar must activate a main-board name and not a ChiNext name.
    closes = [30, 28, 26, 24, 10.0, 10.6]
    opens = [30, 28, 26, 24, 10.0, 10.1]
    pct = [0, 0, 0, 0, -58.0, 6.0]
    dates = _dates(6, start=20210101)

    main = compute_wave_features(
        _series("600000.SH", dates, opens, closes, pct), SMALL
    )
    chinext = compute_wave_features(
        _series("300001.SZ", dates, opens, closes, pct), SMALL
    )

    assert bool(main.activation[5])
    assert not bool(chinext.activation[5])


def test_activation_requires_a_bullish_body_not_just_a_red_close():
    # Gapped up 6% then sold off below its own open: pct_chg qualifies, the
    # candle does not.
    closes = [30, 28, 26, 24, 10.0, 10.6]
    opens = [30, 28, 26, 24, 10.0, 11.5]
    pct = [0, 0, 0, 0, -58.0, 6.0]
    features = compute_wave_features(
        _series("600000.SH", _dates(6), opens, closes, pct), SMALL
    )

    assert not bool(features.activation[5])


def test_activation_requires_the_bottom_third_of_the_trailing_range():
    # Same +6% bullish bar, but struck near the top of its trailing range.
    closes = [10, 11, 12, 13, 14, 14.84]
    opens = [10, 11, 12, 13, 14, 14.1]
    pct = [0, 0, 0, 0, 0, 6.0]
    features = compute_wave_features(
        _series("600000.SH", _dates(6), opens, closes, pct), SMALL
    )

    assert features.range_position[5] > SMALL.range_position_max
    assert not bool(features.activation[5])


# --- entry chain -------------------------------------------------------------


def test_entry_fills_at_the_next_open_never_at_the_signal_close():
    dates = _dates(8)
    series = _series("600000.SH", dates, opens=[10] * 8, closes=[9] * 8)
    features = _features(8, activation_at=[2], structure_at=[3], pullback_at=[3])

    trades, _ = _replay(series, features)

    assert len(trades) == 1
    assert trades[0].entry_signal_date == int(dates[3])
    assert trades[0].entry_date == int(dates[4])
    assert trades[0].entry_price == 10.0


def test_activation_outside_the_entry_window_produces_no_entry():
    series = _series("600000.SH", _dates(10), opens=[10] * 10, closes=[9] * 10)
    # entry_window_bars=3 means the activation must sit in [t-3, t-1].
    features = _features(10, activation_at=[1], structure_at=[5], pullback_at=[5])

    trades, _ = _replay(series, features)

    assert trades == ()


def test_one_activation_yields_at_most_one_entry():
    dates = _dates(12)
    series = _series("600000.SH", dates, opens=[10] * 12, closes=[9] * 12)
    # Two consecutive setup bars share one activation; the position opened by
    # the first exits immediately, and the second must not reuse it.
    features = _features(
        12, activation_at=[2], structure_at=[3, 6], pullback_at=[3, 6], exit_at=[4]
    )

    trades, _ = _replay(series, features)

    assert [t.entry_signal_date for t in trades] == [int(dates[3])]


def test_an_up_limit_void_still_spends_the_activation():
    dates = _dates(12)
    length = len(dates)
    series = _series("600000.SH", dates, opens=[10] * length, closes=[9] * length)
    up = np.full(length, 100.0)
    up[4] = 10.0  # the fill bar opens exactly at its ceiling
    features = _features(
        length, activation_at=[2], structure_at=[3, 5], pullback_at=[3, 5]
    )

    trades, counts = _replay(series, features, up=up)

    assert counts["entry_void_up_limit"] == 1
    # Without E7 the same activation would chase in two bars later at a higher
    # price; card 1 forbids that, so the setup is spent.
    assert trades == ()


def test_st_on_the_signal_date_voids_the_decision_without_spending_it():
    dates = _dates(12)
    length = len(dates)
    series = _series("600000.SH", dates, opens=[10] * length, closes=[9] * length)
    st = np.zeros(length, dtype=bool)
    st[3] = True
    features = _features(
        length, activation_at=[2], structure_at=[3, 4], pullback_at=[3, 4]
    )

    trades, counts = _replay(series, features, st=st)

    assert counts["entry_signals_st_voided"] == 1
    # The security was never eligible on bar 3, so bar 4 may still use it.
    assert [t.entry_signal_date for t in trades] == [int(dates[4])]


# --- exit chain --------------------------------------------------------------


def test_exit_defers_past_a_down_limit_open_to_the_next_tradable_bar():
    dates = _dates(10)
    length = len(dates)
    opens = [10.0] * length
    opens[6] = 5.0  # the first candidate exit bar opens on its floor
    series = _series("600000.SH", dates, opens=opens, closes=[9] * length)
    down = np.full(length, 1.0)
    down[6] = 5.0
    features = _features(
        length, activation_at=[2], structure_at=[3], pullback_at=[3], exit_at=[5]
    )

    trades, _ = _replay(series, features, down=down)

    assert len(trades) == 1
    assert trades[0].exit_signal_date == int(dates[5])
    assert trades[0].exit_date == int(dates[7])
    assert trades[0].exit_delay_days == 1


def test_exit_signal_on_the_final_bar_is_a_no_fill_fact_not_a_free_exit():
    dates = _dates(7)
    series = _series("600000.SH", dates, opens=[10] * 7, closes=[9] * 7)
    features = _features(
        7, activation_at=[2], structure_at=[3], pullback_at=[3], exit_at=[6]
    )

    trades, counts = _replay(series, features)

    assert counts["exit_no_fill_fact"] == 1
    assert trades[0].status == "no_fill_fact"
    assert not np.isfinite(trades[0].net_return_pct)


def test_a_position_with_no_exit_signal_is_marked_open_at_window_end():
    dates = _dates(7)
    series = _series("600000.SH", dates, opens=[10] * 7, closes=[12] * 7)
    features = _features(7, activation_at=[2], structure_at=[3], pullback_at=[3])

    trades, _ = _replay(series, features)

    assert trades[0].status == "open_at_window_end"
    assert trades[0].exit_date == int(dates[-1])
    assert trades[0].exit_price == 12.0


def test_t_plus_one_holds_at_least_one_bar_between_buy_and_sell():
    dates = _dates(9)
    series = _series("600000.SH", dates, opens=[10] * 9, closes=[9] * 9)
    # Exit fires on the very bar the entry filled on; the sale still cannot
    # happen before the following open.
    features = _features(
        9, activation_at=[2], structure_at=[3], pullback_at=[3], exit_at=[4]
    )

    trades, _ = _replay(series, features)

    assert trades[0].entry_date == int(dates[4])
    assert trades[0].exit_date == int(dates[5])
    assert trades[0].holding_bars == 1


# --- pricing, fees, window bounds -------------------------------------------


def test_return_uses_adjusted_prices_across_a_factor_change():
    dates = _dates(9)
    adj = [1.0] * 5 + [2.0] * 4  # a 2-for-1 split priced in from bar 5
    opens = [10.0] * 5 + [6.0] * 4
    series = _series("600000.SH", dates, opens=opens, closes=[9] * 9, adj=adj)
    features = _features(
        9, activation_at=[2], structure_at=[3], pullback_at=[3], exit_at=[5]
    )

    trades, _ = _replay(series, features)

    # Raw 10 -> 6 looks like -40%; adjusted 10 -> 12 is the real +20%.
    assert np.isclose(trades[0].gross_return_pct, 20.0)


def test_fees_and_the_commission_floor_push_the_net_below_the_gross():
    dates = _dates(9)
    series = _series("600000.SH", dates, opens=[10] * 9, closes=[9] * 9)
    features = _features(
        9, activation_at=[2], structure_at=[3], pullback_at=[3], exit_at=[5]
    )
    calendar_index = {int(day): i for i, day in enumerate(dates)}

    trades, _ = simulate_wave_trades(
        series,
        features,
        spec=SMALL,
        st_mask=np.zeros(9, dtype=bool),
        up_limits=np.full(9, np.nan),
        down_limits=np.full(9, np.nan),
        start_date=int(dates[0]),
        end_date=int(dates[-1]),
        calendar_index=calendar_index,
        costs=CostModel(commission_rate=0.00015, min_commission=5.0),
    )

    assert np.isclose(trades[0].gross_return_pct, 0.0)
    assert trades[0].net_return_pct < -1.0  # ¥5 floor on a ¥1,000 lot dominates


def test_window_truncation_does_not_change_a_fully_contained_trade():
    dates = _dates(12)
    series = _series("600000.SH", dates, opens=[10] * 12, closes=[9] * 12)
    features = _features(
        12, activation_at=[2], structure_at=[3], pullback_at=[3], exit_at=[5]
    )
    calendar_index = {int(day): i for i, day in enumerate(dates)}

    def replay(end_index):
        trades, _ = simulate_wave_trades(
            series,
            features,
            spec=SMALL,
            st_mask=np.zeros(12, dtype=bool),
            up_limits=np.full(12, np.nan),
            down_limits=np.full(12, np.nan),
            start_date=int(dates[0]),
            end_date=int(dates[end_index]),
            calendar_index=calendar_index,
            costs=FREE_COSTS,
        )
        return trades

    short_window = replay(7)
    long_window = replay(11)

    assert len(short_window) == len(long_window) == 1
    assert short_window[0] == long_window[0]


# --- limit alignment ---------------------------------------------------------


def test_align_limits_leaves_bars_without_a_stored_row_as_nan():
    series = _series("600000.SH", _dates(4), opens=[10] * 4, closes=[10] * 4)
    panel = {
        "600000.SH": (
            np.asarray([20200101, 20200103], dtype=np.int32),
            np.asarray([11.0, 12.0]),
            np.asarray([9.0, 8.0]),
        )
    }

    up, down = align_limits(series, panel)

    assert np.allclose(up[[0, 2]], [11.0, 12.0])
    assert np.allclose(down[[0, 2]], [9.0, 8.0])
    assert not np.isfinite(up[1]) and not np.isfinite(up[3])


def test_align_limits_returns_all_nan_for_a_security_with_no_rows():
    series = _series("600000.SH", _dates(3), opens=[10] * 3, closes=[10] * 3)

    up, down = align_limits(series, {})

    assert not np.isfinite(up).any()
    assert not np.isfinite(down).any()
