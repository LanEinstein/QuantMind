import numpy as np

from scripts.yeren_research.m3_520 import RuleSpec, compute_features
from scripts.yeren_research.m3_520_adjustment_audit import (
    _exit_touches_change,
    audit_contamination,
    audit_convention_and_events,
    audit_signal_difference,
    convention_errors,
)
from scripts.yeren_research.pit_priced_panel import PricedSeries, factor_change_mask


def _series(closes, adj, pct_chg=None, dates=None):
    closes = np.asarray(closes, dtype=float)
    adj = np.asarray(adj, dtype=float)
    length = len(closes)
    return PricedSeries(
        code="000001.SZ",
        dates=np.asarray(
            dates if dates is not None else np.arange(20230101, 20230101 + length),
            dtype=np.int32,
        ),
        opens=closes.copy(),
        closes=closes,
        pct_chg=np.zeros(length) if pct_chg is None else np.asarray(pct_chg, float),
        adj=adj,
    )


def test_factor_change_mask_marks_only_the_bar_the_factor_moves():
    series = _series([10, 10, 5, 5], [1.0, 1.0, 2.0, 2.0])

    np.testing.assert_array_equal(
        factor_change_mask(series), [False, False, True, False]
    )


def test_constant_factor_series_reports_no_corporate_action():
    series = _series([10, 11, 12], [1.3, 1.3, 1.3])

    assert not factor_change_mask(series).any()


def test_multiplying_by_the_factor_reproduces_the_vendor_return_on_a_split():
    # 2-for-1 split on the third bar: the raw close halves, the holder gains 0%.
    series = _series([20, 20, 10, 10], [1.0, 1.0, 2.0, 2.0], pct_chg=[0, 0, 0, 0])

    errors = convention_errors(series)

    assert abs(errors["multiply"][2]) < 1e-12
    assert abs(errors["raw"][2] + 0.5) < 1e-12
    assert abs(errors["divide"][2] + 0.75) < 1e-12
    assert abs(errors["multiply_lagged_factor"][2] + 0.5) < 1e-12


def test_a_factor_history_that_contradicts_pct_chg_is_named_misaligned():
    # Factor jumps 1.0 -> 2.34 while the vendor reports a -6.2% day: the stored
    # history cannot describe this security's adjustments.
    broken = _series(
        [10.0, 10.0, 9.38], [1.0, 1.0, 2.3404], pct_chg=[0.0, 0.0, -6.1641]
    )
    clean = _series([20, 20, 10], [1.0, 1.0, 2.0], pct_chg=[0, 0, 0])

    report, misaligned = audit_convention_and_events((broken, clean))

    assert misaligned == frozenset({broken.code})
    assert report["misaligned_bars"] == 1
    assert report["securities_with_any_change"] == 2


def test_misaligned_securities_are_dropped_from_the_decision_metrics():
    closes = np.linspace(30, 15, 40)
    series = _series(closes, np.full(len(closes), 1.0))

    result = audit_signal_difference(
        (series,),
        spec=RuleSpec(stop_days=3),
        start_date=int(series.dates[0]),
        end_date=int(series.dates[-1]),
        excluded=frozenset({series.code}),
    )
    contamination = audit_contamination(
        (series,),
        spec=RuleSpec(stop_days=3),
        start_date=int(series.dates[0]),
        end_date=int(series.dates[-1]),
        excluded=frozenset({series.code}),
    )

    assert result["entries_on_both_price_forms"] == 0
    assert contamination["entry_signals"] == 0
    assert contamination["securities_excluded_for_misalignment"] == 1


def test_exit_touches_change_covers_the_exit_signal_bar_and_its_neighbours():
    changed = np.asarray([False, True, False, False, False])

    assert _exit_touches_change(changed, 0) is True
    assert _exit_touches_change(changed, 2) is True
    assert _exit_touches_change(changed, 3) is False


def test_entry_signals_are_unchanged_by_a_constant_price_scale():
    # The rule reads only slope signs and average orderings, so any per-security
    # constant factor - which is all that separates forward from backward
    # adjustment - cannot move a signal.
    closes = np.asarray(
        [30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15]
        + [15, 15, 16, 16, 17, 17, 18, 18, 19, 19, 20, 21, 22, 23, 24, 25],
        dtype=float,
    )
    spec = RuleSpec(stop_days=3)

    baseline = compute_features(closes, spec)
    scaled = compute_features(closes * 3.7, spec)

    np.testing.assert_array_equal(baseline.entry_signal, scaled.entry_signal)
    np.testing.assert_array_equal(
        baseline.early_exit_signal, scaled.early_exit_signal
    )
    np.testing.assert_array_equal(
        baseline.full_cross_signal, scaled.full_cross_signal
    )


def test_signal_difference_is_empty_when_the_factor_never_moves():
    closes = np.linspace(30, 15, 32)
    series = _series(closes, np.full(len(closes), 1.4))

    result = audit_signal_difference(
        (series,),
        spec=RuleSpec(stop_days=3),
        start_date=int(series.dates[0]),
        end_date=int(series.dates[-1]),
        excluded=frozenset(),
    )

    assert result["entries_only_on_raw"] == 0
    assert result["entries_only_on_adjusted"] == 0
