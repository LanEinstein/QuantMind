"""Tests for backend.portfolio_allocation.allocator (Phase P P-002)."""

from __future__ import annotations

import pytest

from backend.portfolio_allocation.allocator import (
    cash_to_lots,
    compute_target_cash,
    deployable_cash,
)
from backend.portfolio_allocation.volatility import inverse_vol_weights

# Standard policy knobs used across the allocator tests.
PER_NAME = 0.10
SINGLE_STOCK = 0.15
SINGLE_INSTR = 50000.0


def _target(
    weights: dict[str, float],
    deployable: float,
    total_assets: float,
    existing: dict[str, float] | None = None,
    *,
    per_name: float = PER_NAME,
    single_stock: float = SINGLE_STOCK,
    single_instr: float = SINGLE_INSTR,
) -> dict[str, float]:
    return compute_target_cash(
        weights,
        deployable,
        total_assets,
        existing or {},
        per_name_target_pct=per_name,
        single_stock_cap_pct=single_stock,
        single_instruction_cap=single_instr,
    )


class TestDeployableCash:
    @pytest.mark.unit
    def test_fraction_binds(self) -> None:
        d = deployable_cash(
            30000.0, 100000.0, deploy_fraction=0.33, cash_buffer_pct=0.05
        )
        assert d == pytest.approx(9900.0)  # 30000 * 0.33

    @pytest.mark.unit
    def test_buffer_binds(self) -> None:
        # by_fraction = 1980 but buffer (5000) leaves only 1000.
        d = deployable_cash(
            6000.0, 100000.0, deploy_fraction=0.33, cash_buffer_pct=0.05
        )
        assert d == pytest.approx(1000.0)

    @pytest.mark.unit
    def test_buffer_exceeds_cash_clamps_to_zero(self) -> None:
        d = deployable_cash(
            4000.0, 100000.0, deploy_fraction=0.33, cash_buffer_pct=0.05
        )
        assert d == 0.0

    @pytest.mark.unit
    def test_nonfinite_fails_closed(self) -> None:
        assert deployable_cash(
            float("nan"), 100000.0, deploy_fraction=0.33, cash_buffer_pct=0.05
        ) == 0.0
        assert deployable_cash(
            30000.0, float("inf"), deploy_fraction=0.33, cash_buffer_pct=0.05
        ) == 0.0


class TestComputeTargetCash:
    @pytest.mark.unit
    def test_basic_split_under_caps(self) -> None:
        alloc = _target({"A": 0.5, "B": 0.5}, 10000.0, 100000.0)
        assert alloc == {"A": pytest.approx(5000.0), "B": pytest.approx(5000.0)}

    @pytest.mark.unit
    def test_per_name_cap_clamps(self) -> None:
        # A weighted 0.9 of 10000 = 9000 but per-name cap = 10% * 50000 = 5000.
        alloc = _target({"A": 0.9, "B": 0.1}, 10000.0, 50000.0)
        assert alloc["A"] == pytest.approx(5000.0)  # clamped to per-name cap
        # Residual (4000) redistributes onto uncapped B (1000 -> 5000 cap).
        assert alloc["B"] == pytest.approx(5000.0)
        assert sum(alloc.values()) == pytest.approx(10000.0)

    @pytest.mark.unit
    def test_residual_redistribution_one_pass(self) -> None:
        # A capped low (existing eats headroom); residual flows to B only.
        alloc = _target(
            {"A": 0.7, "B": 0.3}, 10000.0, 100000.0, {"A": 13000.0}
        )
        # A headroom = 15% * 100000 - 13000 = 2000 (binds below per-name 10000).
        assert alloc["A"] == pytest.approx(2000.0)
        assert alloc["B"] == pytest.approx(8000.0)  # 3000 + 5000 residual
        assert sum(alloc.values()) == pytest.approx(10000.0)

    @pytest.mark.unit
    def test_incremental_existing_holding_reduces_headroom(self) -> None:
        alloc = _target({"A": 1.0}, 10000.0, 100000.0, {"A": 13000.0})
        assert alloc["A"] == pytest.approx(2000.0)  # 15% headroom binds

    @pytest.mark.unit
    def test_existing_over_cap_yields_zero_no_negative(self) -> None:
        alloc = _target({"A": 1.0}, 10000.0, 100000.0, {"A": 20000.0})
        assert alloc["A"] == 0.0  # never negative

    @pytest.mark.unit
    def test_single_instruction_cap_binds(self) -> None:
        alloc = _target({"A": 1.0}, 100000.0, 1_000_000.0)
        # per-name 10% = 100000, single-stock 15% = 150000, ¥50k cap binds.
        assert alloc["A"] == pytest.approx(50000.0)

    @pytest.mark.unit
    def test_zero_deployable_all_zero(self) -> None:
        alloc = _target({"A": 0.5, "B": 0.5}, 0.0, 100000.0)
        assert alloc == {"A": 0.0, "B": 0.0}

    @pytest.mark.unit
    def test_empty_weights(self) -> None:
        assert _target({}, 10000.0, 100000.0) == {}

    @pytest.mark.unit
    def test_corrupt_existing_value_fails_closed_to_zero_headroom(self) -> None:
        alloc = _target({"A": 1.0}, 10000.0, 100000.0, {"A": float("nan")})
        assert alloc["A"] == 0.0


class TestAdversarialCaps:
    """For *any* σ vector + account, every target ≤ its cap and Σ ≤ deployable."""

    SIGMA_SCENARIOS = [
        {"A": 0.01, "B": 0.02, "C": 0.04},
        {"A": None, "B": 0.02, "C": 0.0},
        {"A": float("nan"), "B": float("inf"), "C": 1e-15},
        {"A": 0.5, "B": 0.5, "C": 0.5, "D": 0.5, "E": 0.5},
        {"A": 1e-6, "B": 100.0},  # extreme spread
        {"A": None, "B": None},  # all missing → equal weight
    ]
    ACCOUNTS = [
        (10000.0, 100000.0, {}),
        (50000.0, 200000.0, {"A": 9000.0}),
        (1000.0, 5000.0, {"B": 700.0}),
        (33000.0, 100000.0, {"A": 14000.0, "C": 5000.0}),
    ]

    # Out-of-contract weight vectors inverse_vol_weights never emits, but the
    # public compute_target_cash must still hold the bounds (fail-closed):
    # weights > 1, negative, inf, NaN, and sum != 1.
    RAW_WEIGHT_SCENARIOS = [
        {"A": 1.5, "B": -0.5},          # negative sibling pushes A > 1
        {"A": float("inf"), "B": 0.5},  # inf must not consume the full cap
        {"A": float("nan"), "B": 1.0},  # NaN dropped to 0
        {"A": 0.9, "B": 0.9, "C": 0.9},  # sum 2.7 > 1 → renormalized
        {"A": -1.0, "B": -2.0},         # all negative → all zero
    ]

    def _assert_bounds(
        self,
        alloc: dict[str, float],
        deployable: float,
        total: float,
        existing: dict[str, float],
    ) -> None:
        eps = 1e-6
        for code, value in alloc.items():
            assert value >= 0.0
            per_name_cap = PER_NAME * total
            headroom = SINGLE_STOCK * total - existing.get(code, 0.0)
            cap = max(0.0, min(per_name_cap, headroom, SINGLE_INSTR))
            assert value <= cap + eps, f"{code}: {value} > cap {cap}"
        assert sum(alloc.values()) <= deployable + eps

    @pytest.mark.unit
    @pytest.mark.parametrize("sigma", SIGMA_SCENARIOS)
    @pytest.mark.parametrize(("deployable", "total", "existing"), ACCOUNTS)
    def test_targets_never_exceed_caps_or_deployable(
        self,
        sigma: dict[str, float | None],
        deployable: float,
        total: float,
        existing: dict[str, float],
    ) -> None:
        weights = inverse_vol_weights(sigma)
        alloc = _target(weights, deployable, total, existing)
        self._assert_bounds(alloc, deployable, total, existing)

    @pytest.mark.unit
    @pytest.mark.parametrize("weights", RAW_WEIGHT_SCENARIOS)
    @pytest.mark.parametrize(("deployable", "total", "existing"), ACCOUNTS)
    def test_raw_out_of_contract_weights_still_bounded(
        self,
        weights: dict[str, float],
        deployable: float,
        total: float,
        existing: dict[str, float],
    ) -> None:
        # Even with weights > 1 / negative / inf / NaN / sum != 1, no target
        # exceeds its cap and Σ never exceeds deployable.
        alloc = _target(weights, deployable, total, existing)
        self._assert_bounds(alloc, deployable, total, existing)


class TestCashToLots:
    @pytest.mark.unit
    def test_floor_to_whole_lots(self) -> None:
        # 10000 / (50 * 100) = 2.0 lots -> 200 shares.
        assert cash_to_lots(10000.0, 50.0) == 200

    @pytest.mark.unit
    def test_floors_partial_lot(self) -> None:
        # 7400 / 5000 = 1.48 -> 1 lot -> 100 shares.
        assert cash_to_lots(7400.0, 50.0) == 100

    @pytest.mark.unit
    def test_cannot_afford_one_lot_returns_zero(self) -> None:
        # 4999 / 5000 = 0.99 -> 0 lots -> 0 (skip today, never coerced to 1).
        assert cash_to_lots(4999.0, 50.0) == 0

    @pytest.mark.unit
    def test_exact_one_lot(self) -> None:
        assert cash_to_lots(5000.0, 50.0) == 100

    @pytest.mark.unit
    @pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
    def test_bad_price_returns_zero(self, price: float) -> None:
        assert cash_to_lots(10000.0, price) == 0

    @pytest.mark.unit
    @pytest.mark.parametrize("cash", [0.0, -5.0, float("nan"), float("inf")])
    def test_bad_target_returns_zero(self, cash: float) -> None:
        assert cash_to_lots(cash, 50.0) == 0

    @pytest.mark.unit
    def test_nonpositive_lot_returns_zero(self) -> None:
        assert cash_to_lots(10000.0, 50.0, lot=0) == 0

    @pytest.mark.unit
    def test_custom_lot_size(self) -> None:
        assert cash_to_lots(10000.0, 50.0, lot=200) == 200  # 10000/10000 = 1*200


class TestReplayDeterminism:
    @pytest.mark.unit
    def test_target_cash_bit_exact_replay(self) -> None:
        weights = inverse_vol_weights({"A": 0.011, "B": None, "C": 0.033})
        a = _target(weights, 12345.0, 98765.0, {"A": 1234.0})
        b = _target(weights, 12345.0, 98765.0, {"A": 1234.0})
        assert a == b

    @pytest.mark.unit
    def test_cash_to_lots_replay(self) -> None:
        assert cash_to_lots(9876.0, 31.4) == cash_to_lots(9876.0, 31.4)
