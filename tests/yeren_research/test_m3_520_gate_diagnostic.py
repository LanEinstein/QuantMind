import numpy as np

from scripts.yeren_research.m3_520 import RuleFeatures, Trade
from scripts.yeren_research.m3_520_gate_diagnostic import (
    crossed_during_trade,
    held_bar_range,
    tally,
)


def _features(ma_short, ma_mid):
    length = len(ma_short)
    return RuleFeatures(
        ma_short=np.asarray(ma_short, dtype=float),
        ma_mid=np.asarray(ma_mid, dtype=float),
        ma_long=np.full(length, np.nan),
        entry_signal=np.zeros(length, dtype=bool),
        early_exit_signal=np.zeros(length, dtype=bool),
        full_cross_signal=np.zeros(length, dtype=bool),
    )


def _trade(entry_index, exit_index, status="closed"):
    return Trade(
        code="000001.SZ",
        entry_signal_date=20230103,
        entry_date=20230104,
        exit_signal_date=20230110 if status == "closed" else None,
        exit_date=20230111,
        entry_price=10.0,
        exit_price=10.0,
        return_pct=0.0,
        mae_pct=0.0,
        entry_index=entry_index,
        exit_index=exit_index,
        status=status,
    )


def test_closed_trade_drops_the_exit_day_because_it_is_sold_at_that_open():
    assert held_bar_range(_trade(2, 6)) == (2, 5)


def test_unfinished_trade_keeps_its_final_mark_bar():
    assert held_bar_range(_trade(2, 6, status="open_at_window_end")) == (2, 6)


def test_cross_only_on_the_exit_day_does_not_count_for_a_closed_trade():
    features = _features([9.0, 9.2, 9.3, 10.9], [10.0, 10.0, 10.0, 10.0])

    assert crossed_during_trade(features, _trade(0, 3)) is False
    assert crossed_during_trade(features, _trade(0, 3, "open_at_window_end")) is True


def test_single_bar_above_the_mid_average_proves_the_cross_happened():
    features = _features([9.0, 9.2, 10.5, 9.9], [10.0, 10.0, 10.0, 10.0])

    assert crossed_during_trade(features, _trade(0, 3)) is True


def test_trade_closed_before_the_upward_cross_is_counted_as_never_crossed():
    features = _features([9.0, 9.2, 9.3, 9.4], [10.0, 10.0, 10.0, 10.0])

    assert crossed_during_trade(features, _trade(0, 3)) is False


def test_missing_averages_never_count_as_a_cross():
    features = _features([np.nan, np.nan], [np.nan, np.nan])

    assert crossed_during_trade(features, _trade(0, 1)) is False


def test_unfinished_trades_stay_out_of_the_closed_share():
    crossed = _features([9.0, 10.5, 10.6], [10.0, 10.0, 10.0])
    flat = _features([9.0, 9.5, 9.6], [10.0, 10.0, 10.0])

    counts = tally(
        [
            (crossed, _trade(0, 2)),
            (flat, _trade(0, 2)),
            (flat, _trade(0, 2, status="open_at_window_end")),
        ]
    )

    assert counts.closed == 2
    assert counts.closed_crossed == 1
    assert counts.closed_never_crossed == 1
    assert counts.open_at_window_end == 1
    assert counts.open_crossed == 0
    assert counts.as_dict()["closed_never_crossed_share"] == 0.5
    assert counts.as_dict()["trades_total"] == 3
    assert counts.as_dict()["open_never_crossed"] == 1


def test_holding_statistics_cover_closed_trades_only():
    flat = _features([9.0] * 12, [10.0] * 12)

    counts = tally(
        [
            (flat, _trade(0, 2)),
            (flat, _trade(0, 4)),
            (flat, _trade(0, 11, status="open_at_window_end")),
        ]
    )

    assert counts.holding_days_median == 3.0
    assert counts.holding_days_max == 4


def test_empty_tally_reports_no_holding_statistics():
    counts = tally([])

    assert counts.closed == 0
    assert counts.holding_days_median is None
    assert counts.as_dict()["closed_never_crossed_share"] is None
