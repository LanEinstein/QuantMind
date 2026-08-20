import numpy as np

from scripts.yeren_research.m3_520 import (
    RuleFeatures,
    RuleSpec,
    SecuritySeries,
    Trade,
    compute_features,
    matched_horizon_placebo,
    simulate_trades,
)


def test_features_for_prefix_do_not_depend_on_future_prices():
    closes = np.asarray(
        [20, 19, 18, 18, 17, 17, 18, 18, 19, 20, 19, 18, 18, 19, 20, 21],
        dtype=float,
    )
    prefix = compute_features(closes, RuleSpec(stop_days=3))
    extended = compute_features(
        np.concatenate((closes, np.asarray([1_000, 1_001, 1_002], dtype=float))),
        RuleSpec(stop_days=3),
    )

    np.testing.assert_array_equal(
        prefix.entry_signal, extended.entry_signal[: len(closes)]
    )
    np.testing.assert_array_equal(
        prefix.full_cross_signal, extended.full_cross_signal[: len(closes)]
    )


def test_full_cross_exit_is_not_triggered_by_entry_below_mid_ma():
    series = SecuritySeries(
        code="000001.SZ",
        dates=np.arange(1, 9, dtype=np.int32),
        opens=np.asarray([10, 10, 11, 11, 11, 11, 12, 12], dtype=float),
        closes=np.asarray([10, 10, 10, 10, 10, 9, 9, 9], dtype=float),
    )
    features = RuleFeatures(
        ma_short=np.full(8, np.nan),
        ma_mid=np.full(8, np.nan),
        ma_long=np.full(8, np.nan),
        entry_signal=np.asarray(
            [False, False, True, False, False, False, False, False]
        ),
        early_exit_signal=np.zeros(8, dtype=bool),
        full_cross_signal=np.asarray(
            [False, False, False, False, False, True, False, False]
        ),
    )

    trades = simulate_trades(
        series,
        features,
        start_date=1,
        end_date=8,
        exit_kind="full_cross",
    )

    assert len(trades) == 1
    assert trades[0].entry_date == 4
    assert trades[0].exit_signal_date == 6
    assert trades[0].exit_date == 7
    assert trades[0].return_pct == (12 / 11 - 1) * 100


def test_placebo_is_reproducible_for_fixed_seed():
    series = SecuritySeries(
        code="000001.SZ",
        dates=np.arange(1, 20, dtype=np.int32),
        opens=np.linspace(10, 20, 19),
        closes=np.linspace(10, 20, 19),
    )
    trade = Trade(
        code=series.code,
        entry_signal_date=5,
        entry_date=6,
        exit_signal_date=10,
        exit_date=11,
        entry_price=float(series.opens[5]),
        exit_price=float(series.opens[10]),
        return_pct=50.0,
        mae_pct=0.0,
        entry_index=5,
        exit_index=10,
        status="closed",
    )

    first = matched_horizon_placebo(
        (trade,),
        {series.code: series},
        start_date=1,
        end_date=19,
        reps=8,
        seed=520,
        warmup_bars=0,
    )
    second = matched_horizon_placebo(
        (trade,),
        {series.code: series},
        start_date=1,
        end_date=19,
        reps=8,
        seed=520,
        warmup_bars=0,
    )

    assert first == second
