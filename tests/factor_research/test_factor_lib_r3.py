"""Tests for the round-3 factor computations (R3-2): SUE / accruals / asset-growth.

Pure functions — no store / network. Cover the YTD→single-quarter differencing,
the seasonal-difference SUE standardisation, the annual-pair accruals /
asset-growth, the fail-closed (None) paths, and the R3 registry wiring.
"""

from __future__ import annotations

import math

import pytest

from scripts.factor_research.factor_lib import (
    ALL_FACTORS_BY_NAME,
    R3_FACTOR_NAMES,
    R3_FACTORS_BY_NAME,
    _annual_pair,
    _single_quarter_series,
    accruals_sloan,
    asset_growth,
    compute_statement_factors,
    earnings_surprise_sue,
)


def _ytd_from_quarters(quarters: dict[int, list[float]]) -> dict[str, float | None]:
    """Build a YTD profit_dedt map from ``{year: [q1,q2,q3,q4]}`` single quarters."""
    out: dict[str, float | None] = {}
    for year, qs in quarters.items():
        cum = 0.0
        for i, mmdd in enumerate(("0331", "0630", "0930", "1231")):
            cum += qs[i]
            out[f"{year}{mmdd}"] = cum
    return out


class TestSingleQuarter:
    def test_q1_is_ytd_rest_differenced(self) -> None:
        ytd = _ytd_from_quarters({2020: [10.0, 12.0, 8.0, 20.0]})
        sq = _single_quarter_series(ytd)
        assert sq["20200331"] == pytest.approx(10.0)
        assert sq["20200630"] == pytest.approx(12.0)
        assert sq["20200930"] == pytest.approx(8.0)
        assert sq["20201231"] == pytest.approx(20.0)

    def test_missing_prior_quarter_omits_single_quarter(self) -> None:
        # Q3 present but Q2 missing → cannot difference Q3 → omitted.
        ytd: dict[str, float | None] = {"20200331": 10.0, "20200930": 30.0}
        sq = _single_quarter_series(ytd)
        assert "20200331" in sq
        assert "20200930" not in sq


class TestSUE:
    def test_positive_surprise_gives_positive_sue(self) -> None:
        # 2018-2020 flat single-quarter earnings; 2021 jumps up → positive SUE.
        quarters = {
            2018: [10.0, 10.0, 10.0, 10.0],
            2019: [10.0, 10.0, 10.0, 10.0],
            2020: [10.0, 10.0, 10.0, 10.0],
            2021: [14.0, 15.0, 16.0, 20.0],
        }
        sue = earnings_surprise_sue(_ytd_from_quarters(quarters))
        assert sue is not None and sue > 0

    def test_insufficient_diffs_returns_none(self) -> None:
        # Only 2 years → 4 seasonal diffs < SUE_MIN_DIFFS (6) → None.
        quarters = {
            2020: [10.0, 11.0, 12.0, 13.0],
            2021: [11.0, 12.0, 13.0, 14.0],
        }
        assert earnings_surprise_sue(_ytd_from_quarters(quarters)) is None

    def test_zero_dispersion_returns_none(self) -> None:
        # Perfectly seasonal (every year identical) → all diffs 0 → σ=0 → None.
        base = [10.0, 12.0, 8.0, 20.0]
        quarters = {y: list(base) for y in range(2016, 2022)}
        assert earnings_surprise_sue(_ytd_from_quarters(quarters)) is None

    def test_empty_series_returns_none(self) -> None:
        assert earnings_surprise_sue({}) is None


class TestAccruals:
    def test_basic_value(self) -> None:
        # (NI 100 − CFO 60) / avg(1000, 800) = 40 / 900.
        v = accruals_sloan(100.0, 60.0, 1000.0, 800.0)
        assert v == pytest.approx(40.0 / 900.0)

    def test_high_accruals_positive_low_quality(self) -> None:
        # NI >> CFO → positive accruals (low earnings quality).
        assert accruals_sloan(100.0, 10.0, 1000.0, 1000.0) > 0

    @pytest.mark.parametrize(
        "args",
        [
            (None, 60.0, 1000.0, 800.0),
            (100.0, None, 1000.0, 800.0),
            (100.0, 60.0, None, 800.0),
            (100.0, 60.0, float("nan"), 800.0),
        ],
    )
    def test_missing_input_returns_none(self, args: tuple) -> None:
        assert accruals_sloan(*args) is None

    def test_nonpositive_avg_assets_returns_none(self) -> None:
        assert accruals_sloan(100.0, 60.0, -1000.0, -800.0) is None


class TestAssetGrowth:
    def test_basic_value(self) -> None:
        assert asset_growth(1200.0, 1000.0) == pytest.approx(0.2)

    def test_nonpositive_prior_returns_none(self) -> None:
        assert asset_growth(1200.0, 0.0) is None
        assert asset_growth(1200.0, -50.0) is None

    def test_missing_returns_none(self) -> None:
        assert asset_growth(None, 1000.0) is None
        assert asset_growth(1200.0, None) is None


class TestAnnualPair:
    def test_consecutive_annuals(self) -> None:
        ta: dict[str, float | None] = {
            "20221231": 800.0,
            "20231231": 900.0,
            "20230630": 850.0,  # interim ignored
        }
        assert _annual_pair(ta) == ("20231231", "20221231")

    def test_non_consecutive_annual_returns_none(self) -> None:
        # 2021 annual missing → latest 2023 has no 2022 prior in this map.
        ta: dict[str, float | None] = {"20211231": 700.0, "20231231": 900.0}
        assert _annual_pair(ta) is None

    def test_no_annual_returns_none(self) -> None:
        assert _annual_pair({"20230630": 850.0}) is None


class TestComputeStatementFactors:
    def test_full_integration(self) -> None:
        profit = _ytd_from_quarters(
            {
                2018: [10.0, 10.0, 10.0, 10.0],
                2019: [10.0, 10.0, 10.0, 10.0],
                2020: [10.0, 10.0, 10.0, 10.0],
                2021: [14.0, 15.0, 16.0, 20.0],
            }
        )
        ni = {"20201231": 100.0, "20211231": 120.0}
        cfo = {"20201231": 60.0, "20211231": 50.0}
        ta = {"20201231": 1000.0, "20211231": 1200.0}
        out = compute_statement_factors(
            profit_dedt_ytd=profit,
            n_income_ytd=ni,
            cfo_ytd=cfo,
            total_assets=ta,
        )
        assert out["sue"] is not None and out["sue"] > 0
        # accruals on 2021 annual: (120 − 50)/avg(1200,1000) = 70/1100
        assert out["accr"] == pytest.approx(70.0 / 1100.0)
        # asset growth: 1200/1000 − 1
        assert out["asset_growth"] == pytest.approx(0.2)

    def test_no_annual_pair_yields_none_balance_factors(self) -> None:
        out = compute_statement_factors(
            profit_dedt_ytd={},
            n_income_ytd={"20231231": 100.0},
            cfo_ytd={"20231231": 60.0},
            total_assets={"20231231": 1000.0},  # only one annual → no pair
        )
        assert out["accr"] is None
        assert out["asset_growth"] is None
        assert out["sue"] is None


class TestRegistry:
    def test_r3_names(self) -> None:
        assert R3_FACTOR_NAMES == ("sue", "accr", "asset_growth")

    def test_directions_and_mechanisms(self) -> None:
        assert R3_FACTORS_BY_NAME["sue"].attractive_high is True
        assert R3_FACTORS_BY_NAME["accr"].attractive_high is False
        assert R3_FACTORS_BY_NAME["asset_growth"].attractive_high is False
        assert R3_FACTORS_BY_NAME["accr"].mechanism == "quality_premium"
        # honestly-unregistered mechanisms (fail-closed until amendment)
        assert R3_FACTORS_BY_NAME["sue"].mechanism == "post_earnings_drift"
        assert R3_FACTORS_BY_NAME["asset_growth"].mechanism == "asset_growth_anomaly"

    def test_merged_into_all_factors(self) -> None:
        for name in R3_FACTOR_NAMES:
            assert name in ALL_FACTORS_BY_NAME
        # round-1 / round-2 still present (additive merge).
        assert "ret_5d" in ALL_FACTORS_BY_NAME
        assert "roe" in ALL_FACTORS_BY_NAME

    def test_unregistered_mechanism_not_in_economic_enum(self) -> None:
        # Governance enum is untouched: sue/asset_growth mechanisms are NOT
        # registered EconomicMechanism values (promotion stays fail-closed).
        from backend.strategy_evolution.mechanism_registry import EconomicMechanism

        registered = {m.value for m in EconomicMechanism}
        assert "quality_premium" in registered  # accr maps to a real one
        assert "post_earnings_drift" not in registered
        assert "asset_growth_anomaly" not in registered


def test_all_finite_helpers_pure() -> None:
    # Sanity: a NaN anywhere never leaks a NaN factor.
    assert accruals_sloan(float("nan"), 1.0, 1.0, 1.0) is None
    assert asset_growth(float("nan"), 1.0) is None
    assert not math.isnan(asset_growth(2.0, 1.0))  # type: ignore[arg-type]
