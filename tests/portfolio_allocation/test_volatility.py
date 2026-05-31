"""Tests for backend.portfolio_allocation.volatility (Phase P P-002)."""

from __future__ import annotations

import pytest

from backend.portfolio_allocation.volatility import inverse_vol_weights


class TestInverseVolFormula:
    @pytest.mark.unit
    def test_weights_inverse_to_volatility(self) -> None:
        # σ = {A:0.01, B:0.02, C:0.04} → 1/σ = {100, 50, 25}, total 175.
        w = inverse_vol_weights({"A": 0.01, "B": 0.02, "C": 0.04})
        assert w["A"] == pytest.approx(100 / 175)
        assert w["B"] == pytest.approx(50 / 175)
        assert w["C"] == pytest.approx(25 / 175)
        # Lower vol → larger weight.
        assert w["A"] > w["B"] > w["C"]

    @pytest.mark.unit
    def test_weights_sum_to_one(self) -> None:
        w = inverse_vol_weights({"A": 0.013, "B": 0.027, "C": 0.005, "D": 0.04})
        assert sum(w.values()) == pytest.approx(1.0)

    @pytest.mark.unit
    def test_equal_vol_gives_equal_weight(self) -> None:
        w = inverse_vol_weights({"A": 0.02, "B": 0.02, "C": 0.02})
        for code in ("A", "B", "C"):
            assert w[code] == pytest.approx(1 / 3)


class TestEqualWeightFallback:
    @pytest.mark.unit
    def test_all_none_is_equal_weight(self) -> None:
        w = inverse_vol_weights({"A": None, "B": None, "C": None, "D": None})
        for code in ("A", "B", "C", "D"):
            assert w[code] == pytest.approx(0.25)
        assert sum(w.values()) == pytest.approx(1.0)

    @pytest.mark.unit
    def test_sigma_at_or_below_eps_treated_as_missing(self) -> None:
        # σ ≤ eps would blow up 1/σ → must fall back, never produce inf.
        w = inverse_vol_weights({"A": 0.0, "B": 1e-12, "C": 0.02})
        assert all(0.0 <= v < 1.0 for v in w.values())
        assert all(v == v for v in w.values())  # no NaN
        assert sum(w.values()) == pytest.approx(1.0)

    @pytest.mark.unit
    def test_partial_missing_filled_with_mean_valid_inv(self) -> None:
        # Valid: A σ=0.01 (1/σ=100), C σ=0.04 (1/σ=25). Mean valid = 62.5.
        # Missing B → 62.5. Total = 100 + 62.5 + 25 = 187.5.
        w = inverse_vol_weights({"A": 0.01, "B": None, "C": 0.04})
        assert w["A"] == pytest.approx(100 / 187.5)
        assert w["B"] == pytest.approx(62.5 / 187.5)
        assert w["C"] == pytest.approx(25 / 187.5)
        assert sum(w.values()) == pytest.approx(1.0)
        # Missing name is neutral: between the lowest and highest valid weight.
        assert w["C"] < w["B"] < w["A"]

    @pytest.mark.unit
    def test_nonfinite_sigma_treated_as_missing(self) -> None:
        w = inverse_vol_weights(
            {"A": float("nan"), "B": float("inf"), "C": 0.02, "D": -0.01}
        )
        assert all(v == v and abs(v) != float("inf") for v in w.values())
        assert sum(w.values()) == pytest.approx(1.0)


class TestEdgeCasesAndDeterminism:
    @pytest.mark.unit
    def test_empty_input_returns_empty(self) -> None:
        assert inverse_vol_weights({}) == {}

    @pytest.mark.unit
    def test_single_name_full_weight(self) -> None:
        assert inverse_vol_weights({"A": 0.02}) == {"A": pytest.approx(1.0)}

    @pytest.mark.unit
    def test_deterministic_same_input_same_output(self) -> None:
        sigma = {"A": 0.011, "B": None, "C": 0.033, "D": 0.0}
        assert inverse_vol_weights(sigma) == inverse_vol_weights(dict(sigma))

    @pytest.mark.unit
    def test_order_follows_insertion(self) -> None:
        w = inverse_vol_weights({"C": 0.02, "A": 0.01, "B": 0.04})
        assert list(w.keys()) == ["C", "A", "B"]
