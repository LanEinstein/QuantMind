"""Tests for cross-sectional industry + size neutralization (R2-2 / S4)."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.factor_research.neutralize import (
    neutralize_cross_section,
    neutralize_panel,
)


def test_residuals_zero_when_factor_is_exactly_industry_plus_size() -> None:
    # value = industry_effect + 3*log_size exactly → residuals ~ 0.
    industry = ["A", "A", "B", "B", "A", "B"]
    log_size = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    eff = {"A": 10.0, "B": -5.0}
    values = [eff[ind] + 3.0 * s for ind, s in zip(industry, log_size, strict=True)]
    resid = neutralize_cross_section(industry, log_size, values, min_obs=4)
    assert all(r is not None and abs(r) < 1e-9 for r in resid)


def test_residuals_orthogonal_to_size_and_demeaned() -> None:
    industry = ["A"] * 6
    log_size = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    values = [0.5, 0.1, 0.9, 0.2, 0.7, 0.3]  # arbitrary
    resid = neutralize_cross_section(industry, log_size, values, min_obs=4)
    rs = [r for r in resid if r is not None]
    assert len(rs) == 6
    assert sum(rs) == pytest.approx(0.0, abs=1e-9)  # intercept absorbs the mean
    # orthogonal to log_size (regressor) → covariance ≈ 0
    mean_s = sum(log_size) / len(log_size)
    cov = sum((s - mean_s) * r for s, r in zip(log_size, rs, strict=True))
    assert cov == pytest.approx(0.0, abs=1e-9)


def test_missing_rows_are_none_others_computed() -> None:
    industry = ["A", "A", None, "B", "B", "A"]
    log_size = [1.0, 2.0, 3.0, float("nan"), 5.0, 6.0]
    values = [0.5, 0.1, 0.9, 0.2, None, 0.3]
    resid = neutralize_cross_section(industry, log_size, values, min_obs=3)
    # index 2 (no industry), 3 (nan size), 4 (no value) → None
    assert resid[2] is None and resid[3] is None and resid[4] is None
    assert all(resid[i] is not None for i in (0, 1, 5))


def test_pandas_na_industry_is_missing_not_a_bucket() -> None:
    # pd.NA (nullable-dtype missing) must fail closed to None, not become a
    # literal "<NA>" industry dummy (codex P3).
    industry = ["A", "A", pd.NA, "B", "B", "A"]
    log_size = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    values = [0.5, 0.1, 0.9, 0.2, 0.7, 0.3]
    resid = neutralize_cross_section(industry, log_size, values, min_obs=3)
    assert resid[2] is None  # pd.NA industry → dropped, not residualised
    assert all(resid[i] is not None for i in (0, 1, 3, 4, 5))


def test_too_few_observations_all_none() -> None:
    industry = ["A", "A"]
    log_size = [1.0, 2.0]
    values = [0.5, 0.1]
    resid = neutralize_cross_section(industry, log_size, values, min_obs=20)
    assert all(r is None for r in resid)


def test_single_industry_is_size_only_neutralization() -> None:
    industry = ["A"] * 5
    log_size = [1.0, 2.0, 3.0, 4.0, 5.0]
    values = [2.0 * s + 1.0 for s in log_size]  # pure size + intercept
    resid = neutralize_cross_section(industry, log_size, values, min_obs=4)
    assert all(r is not None and abs(r) < 1e-9 for r in resid)


def test_neutralize_panel_adds_neut_columns_per_date() -> None:
    rows = []
    for date in ("20240105", "20240112"):
        for i, (ind, s) in enumerate(
            [("A", 1.0), ("A", 2.0), ("B", 3.0), ("B", 4.0), ("A", 5.0)]
        ):
            rows.append(
                {
                    "date": date,
                    "code": f"c{i}",
                    "industry_l1": ind,
                    "log_circ_mv": s,
                    "f1": 0.1 * i + (0.0 if date == "20240105" else 1.0),
                }
            )
    panel = pd.DataFrame(rows)
    out = neutralize_panel(panel, ["f1"], min_obs=3)
    assert "f1_neut" in out.columns
    # residuals are per-date (each date's cross-section demeaned independently)
    for date in ("20240105", "20240112"):
        d = out[out["date"] == date]["f1_neut"].dropna()
        assert d.sum() == pytest.approx(0.0, abs=1e-9)


def test_neutralize_panel_winsor_clips_outliers() -> None:
    # One extreme value; with winsor it should not dominate the fit.
    rows = [
        {
            "date": "20240105",
            "code": f"c{i}",
            "industry_l1": "A",
            "log_circ_mv": float(i),
            "f1": v,
        }
        for i, v in enumerate([1.0, 2.0, 3.0, 4.0, 1000.0])
    ]
    panel = pd.DataFrame(rows)
    plain = neutralize_panel(panel, ["f1"], min_obs=3)
    wins = neutralize_panel(panel, ["f1"], min_obs=3, winsor_quantile=0.2)
    # winsorizing changes the residuals (the outlier is clipped before the fit)
    assert not plain["f1_neut"].equals(wins["f1_neut"])
    assert wins["f1_neut"].notna().all()
