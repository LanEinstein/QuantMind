"""AB-005 frozen set + evolvable whitelist tests (adversarial-first)."""

from __future__ import annotations

import pytest

from backend.strategy_evolution.evolvable_params import (
    EVOLVABLE_WHITELIST,
    FROZEN_NON_EVOLVABLE,
    FrozenParamViolationError,
    validate_param_change,
    validate_param_set,
)


class TestFrozenSet:
    def test_frozen_param_raises_regardless_of_value(self) -> None:
        """Adversarial: a 'high-Sharpe' variant of a frozen safety
        parameter must be rejected — no score can justify it."""
        with pytest.raises(FrozenParamViolationError):
            validate_param_change("risk.max_single_stock_pct", 0.30)
        with pytest.raises(FrozenParamViolationError):
            validate_param_change("budget.daily_hard_cny", 500.0)
        with pytest.raises(FrozenParamViolationError):
            validate_param_change("mode.feishu_interactive_enabled", 1.0)

    def test_frozen_and_whitelist_are_disjoint(self) -> None:
        assert FROZEN_NON_EVOLVABLE.isdisjoint(EVOLVABLE_WHITELIST)

    def test_safety_floor_members_present(self) -> None:
        for name in (
            "safety.real_order_placement",
            "safety.risk_engine_pure_function",
            "risk.max_total_positions",
            "risk.circuit_cooldown_minutes",
            "universe.exclude_st",
            "reconciliation.cash_tolerance_cny",
            "risk.fourteen_check_set",
        ):
            assert name in FROZEN_NON_EVOLVABLE

    def test_frozen_set_is_immutable_type(self) -> None:
        assert isinstance(FROZEN_NON_EVOLVABLE, frozenset)
        with pytest.raises(AttributeError):
            FROZEN_NON_EVOLVABLE.add("x")  # type: ignore[attr-defined]


class TestClamps:
    def test_inside_clamp_passes(self) -> None:
        assert validate_param_change("line2.atr_stop_mult", 2.5) == ()

    def test_outside_clamp_rejected(self) -> None:
        violations = validate_param_change("line2.atr_stop_mult", 9.0)
        assert violations and "clamp" in violations[0]

    def test_clamp_boundaries_inclusive(self) -> None:
        spec = EVOLVABLE_WHITELIST["line2.atr_stop_mult"]
        assert validate_param_change(spec.name, spec.clamp_min) == ()
        assert validate_param_change(spec.name, spec.clamp_max) == ()

    def test_unknown_param_rejected(self) -> None:
        violations = validate_param_change("line2.brand_new_knob", 1.0)
        assert violations and "whitelist" in violations[0]

    def test_int_domain_enforced(self) -> None:
        violations = validate_param_change(
            "allocation.value_slot_quota", 1.5
        )
        assert any("integer" in v for v in violations)

    def test_clamp_specs_are_runtime_immutable(self) -> None:
        with pytest.raises(TypeError):
            EVOLVABLE_WHITELIST["line2.atr_stop_mult"] = (  # type: ignore[index]
                EVOLVABLE_WHITELIST["line2.r_multiple"]
            )
        spec = EVOLVABLE_WHITELIST["line2.atr_stop_mult"]
        with pytest.raises(AttributeError):
            spec.clamp_max = 99.0  # type: ignore[misc]


class TestSafetyAdjacentMonotone:
    def test_loosening_a_stop_rejected(self) -> None:
        """atr_stop_mult UP delays the protective SELL — forbidden."""
        violations = validate_param_change(
            "line2.atr_stop_mult", 3.0, current=2.5
        )
        assert any("only tighten" in v for v in violations)

    def test_tightening_a_stop_allowed(self) -> None:
        assert (
            validate_param_change(
                "line2.atr_stop_mult", 2.0, current=2.5
            )
            == ()
        )

    def test_non_safety_param_moves_freely_inside_clamp(self) -> None:
        assert (
            validate_param_change("line2.r_multiple", 2.0, current=1.0)
            == ()
        )
        assert (
            validate_param_change("line2.r_multiple", 0.8, current=1.0)
            == ()
        )

    def test_no_current_value_skips_monotone_check(self) -> None:
        # Bootstrap case: no incumbent value recorded yet.
        assert validate_param_change("line2.atr_stop_mult", 3.5) == ()


class TestGroupConstraints:
    def test_selector_weights_must_sum_to_one(self) -> None:
        weights = {
            "selector.weight_momentum": 0.3,
            "selector.weight_volatility": 0.2,
            "selector.weight_liquidity": 0.2,
            "selector.weight_value": 0.2,
            "selector.weight_quality": 0.2,  # sum = 1.1
        }
        result = validate_param_set(weights)
        assert not result.passed
        assert any("sum" in v for v in result.violations)

    def test_normalised_selector_weights_pass(self) -> None:
        weights = {
            "selector.weight_momentum": 0.3,
            "selector.weight_volatility": 0.2,
            "selector.weight_liquidity": 0.2,
            "selector.weight_value": 0.15,
            "selector.weight_quality": 0.15,
        }
        assert validate_param_set(weights).passed

    def test_partial_selector_group_rejected(self) -> None:
        result = validate_param_set({"selector.weight_momentum": 0.3})
        assert not result.passed
        assert any("whole" in v for v in result.violations)

    def test_tier_order_constraint(self) -> None:
        ok = validate_param_set(
            {
                "theme.tier1_weight": 1.0,
                "theme.tier2_weight": 0.75,
                "theme.tier3_weight": 0.5,
                "theme.tier4_weight": 0.25,
            }
        )
        assert ok.passed
        bad = validate_param_set(
            {
                "theme.tier1_weight": 0.6,
                "theme.tier2_weight": 0.9,
                "theme.tier3_weight": 0.5,
                "theme.tier4_weight": 0.25,
            }
        )
        assert not bad.passed
        assert any("order" in v for v in bad.violations)

    def test_frozen_member_in_set_raises(self) -> None:
        with pytest.raises(FrozenParamViolationError):
            validate_param_set(
                {
                    "line2.r_multiple": 1.2,
                    "risk.max_total_position_pct": 0.95,
                }
            )
