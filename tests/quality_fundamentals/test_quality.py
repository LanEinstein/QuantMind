"""AF-003 — quality-fundamentals composite (PIT selection, signs, cross-section)."""

from __future__ import annotations

from backend.quality_fundamentals.quality import (
    QualityMetric,
    fundamentals_scores,
    quality_pit_values,
)

R = QualityMetric.ROE
G = QualityMetric.GPM
E = QualityMetric.EP_TTM
A = QualityMetric.ACCRUALS


def test_pit_selection_ignores_future_announcements() -> None:
    recs = {
        R: [("2024-01-15", 0.10), ("2024-12-01", 0.99)],  # 0.99 not yet announced
    }
    vals = quality_pit_values(recs, "2024-06-01")
    assert vals[R] == 0.10  # latest announced on/before 2024-06-01
    # before any announcement → None
    assert quality_pit_values(recs, "2024-01-01")[R] is None


def test_high_quality_outranks_low_quality() -> None:
    cross = {
        "HI": {
            R: [("2024-01-01", 0.20)],
            G: [("2024-01-01", 0.40)],
            A: [("2024-01-01", 0.05)],
        },  # low accruals = good
        "MID": {
            R: [("2024-01-01", 0.10)],
            G: [("2024-01-01", 0.30)],
            A: [("2024-01-01", 0.15)],
        },
        "LO": {
            R: [("2024-01-01", 0.05)],
            G: [("2024-01-01", 0.20)],
            A: [("2024-01-01", 0.25)],
        },  # high accruals = bad
    }
    s = fundamentals_scores(cross, "2024-06-01")
    assert s["HI"] > s["MID"] > s["LO"]
    assert 0.0 <= s["LO"] <= s["HI"] <= 1.0  # type: ignore[operator]


def test_accruals_sign_is_inverted() -> None:
    # two codes identical except accruals: the lower-accrual name scores higher
    cross = {
        "CLEAN": {R: [("2024-01-01", 0.10)], A: [("2024-01-01", 0.02)]},
        "GAMED": {R: [("2024-01-01", 0.10)], A: [("2024-01-01", 0.30)]},
    }
    s = fundamentals_scores(cross, "2024-06-01")
    assert s["CLEAN"] > s["GAMED"]


def test_no_pit_metric_yields_none_not_zero() -> None:
    cross = {
        "KNOWN": {R: [("2024-01-01", 0.10)]},
        "FUTURE": {R: [("2024-12-01", 0.50)]},  # announced after as_of → no PIT value
        "EMPTY": {},
    }
    s = fundamentals_scores(cross, "2024-06-01")
    assert s["KNOWN"] is not None
    assert s["FUTURE"] is None  # dropped, not a fabricated 0.0
    assert s["EMPTY"] is None


def test_partial_metrics_average_only_present_ranks() -> None:
    cross = {
        "FULL": {R: [("2024-01-01", 0.20)], G: [("2024-01-01", 0.40)]},
        "ROE_ONLY": {R: [("2024-01-01", 0.05)]},
    }
    s = fundamentals_scores(cross, "2024-06-01")
    # ROE_ONLY ranks only on roe (lowest) → low; FULL ranks high on both
    assert s["FULL"] is not None and s["ROE_ONLY"] is not None
    assert s["FULL"] > s["ROE_ONLY"]


def test_deterministic() -> None:
    cross = {
        "X": {R: [("2024-01-01", 0.12)], G: [("2024-01-01", 0.33)]},
        "Y": {R: [("2024-01-01", 0.08)], G: [("2024-01-01", 0.22)]},
    }
    runs = [fundamentals_scores(cross, "2024-06-01") for _ in range(5)]
    assert all(r == runs[0] for r in runs)


def test_non_finite_value_dropped() -> None:
    cross = {
        "A": {R: [("2024-01-01", float("nan"))]},  # dirty → no PIT value
        "B": {R: [("2024-01-01", 0.10)]},
    }
    s = fundamentals_scores(cross, "2024-06-01")
    assert s["A"] is None
    assert s["B"] is not None
