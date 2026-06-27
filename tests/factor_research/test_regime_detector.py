"""Unit tests for the PIT-clean crash/risk regime detector (batch-B1).

Covers the deterministic core: trailing drawdown / realised-vol statistics use
ONLY present-and-prior closes (causal — no look-ahead), the headline drawdown
threshold fires correctly, and ``high_risk_dates`` filters candidate rebalance
dates with the pre-committed gate. The market-store reader is exercised by the
ablation smoke run.
"""

from __future__ import annotations

import pytest

from scripts.factor_research import regime_detector as rd


def test_drawdown_is_causal_no_lookahead() -> None:
    """Reclassifying a truncated prefix must match the full run on shared days.

    A later close can never change an earlier day's regime read (causal by
    construction) — the defining PIT property.
    """
    closes = {f"202001{d:02d}": 100.0 - d for d in range(1, 21)}  # monotone fall
    full = rd.classify_regimes(closes)
    prefix_days = sorted(closes)[:10]
    prefix = rd.classify_regimes({d: closes[d] for d in prefix_days})
    for d in prefix_days:
        assert full[d].drawdown_from_peak == prefix[d].drawdown_from_peak
        assert full[d].high_risk == prefix[d].high_risk


def test_high_risk_fires_on_deep_drawdown() -> None:
    """A −15% drawdown from the trailing peak trips the −10% headline gate."""
    closes = {"20200101": 100.0, "20200102": 95.0, "20200103": 85.0}
    states = rd.classify_regimes(closes)
    assert states["20200103"].drawdown_from_peak == pytest.approx(-0.15)
    assert states["20200103"].high_risk is True
    assert states["20200101"].high_risk is False  # at the peak, no drawdown


def test_drawdown_uses_trailing_peak_window_only() -> None:
    """A peak older than ``PEAK_LOOKBACK`` no longer anchors the drawdown."""
    closes = {f"2020{i:04d}"[:8].ljust(8, "0"): 50.0 for i in range(1, 200)}
    # Day 1 is a tall peak; with a 60-day lookback, day 150's drawdown forgets it.
    keys = sorted(closes)
    closes[keys[0]] = 1000.0
    states = rd.classify_regimes(closes, peak_lookback=60)
    late = states[keys[150]]
    assert late.drawdown_from_peak == 0.0  # flat trailing window, peak == close


def test_high_risk_dates_filters_candidates() -> None:
    closes = {"20200101": 100.0, "20200102": 88.0, "20200103": 102.0}
    states = rd.classify_regimes(closes)
    hi = rd.high_risk_dates(states, ["20200101", "20200102", "20200103"])
    assert hi == ("20200102",)  # only the −12% day


def test_high_risk_dates_missing_regime_is_not_high_risk() -> None:
    """A candidate date with no market read fails open to 'do not de-risk'."""
    states = rd.classify_regimes({"20200101": 100.0, "20200102": 80.0})
    hi = rd.high_risk_dates(states, ["20200102", "20991231"])
    assert "20991231" not in hi
    assert hi == ("20200102",)


def test_vol_variant_is_superset_of_headline() -> None:
    """The disclosure vol variant flags at least every headline high-risk date."""
    closes = {
        f"202001{d:02d}": 100.0 * (1.0 + 0.05 * ((-1) ** d)) for d in range(1, 30)
    }
    states = rd.classify_regimes(closes)
    cand = sorted(closes)
    head = set(rd.high_risk_dates(states, cand, use_vol_variant=False))
    var = set(rd.high_risk_dates(states, cand, use_vol_variant=True))
    assert head.issubset(var)


def test_realized_vol_zero_on_flat_series() -> None:
    closes = {f"202001{d:02d}": 100.0 for d in range(1, 25)}
    states = rd.classify_regimes(closes)
    assert states[sorted(closes)[-1]].realized_vol_annualized == 0.0
