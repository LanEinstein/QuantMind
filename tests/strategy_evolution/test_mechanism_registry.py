"""Tests for the AE-005 economic-mechanism hypothesis gate."""

from __future__ import annotations

from backend.strategy_evolution.mechanism_registry import (
    FAMILY_MECHANISMS,
    EconomicMechanism,
    admissible_mechanisms,
    has_valid_mechanism,
    known_family,
)


class TestMechanismGate:
    def test_valid_mechanism_passes(self) -> None:
        assert (
            has_valid_mechanism(
                "selector_weights", EconomicMechanism.MOMENTUM_CONTINUATION
            )
            is True
        )

    def test_none_mechanism_always_rejected(self) -> None:
        # A sentinel / pure-data winner names no mechanism → default-overfit.
        assert has_valid_mechanism("selector_weights", None) is False

    def test_mechanism_outside_family_rejected(self) -> None:
        # Value premium is not an admissible rationale for theme tier weights.
        assert (
            has_valid_mechanism("theme_tier_weights", EconomicMechanism.VALUE_PREMIUM)
            is False
        )

    def test_unknown_family_rejected(self) -> None:
        assert (
            has_valid_mechanism(
                "risk.max_total_position_pct",
                EconomicMechanism.MOMENTUM_CONTINUATION,
            )
            is False
        )
        assert known_family("risk.max_total_position_pct") is False

    def test_known_families(self) -> None:
        assert known_family("selector_weights")
        assert known_family("allocation.value_slot_quota")
        assert known_family("theme_tier_weights")

    def test_admissible_mechanisms_for_unknown_family_is_empty(self) -> None:
        assert admissible_mechanisms("nope") == frozenset()


class TestImmutability:
    def test_family_map_is_read_only(self) -> None:
        import pytest

        with pytest.raises(TypeError):
            FAMILY_MECHANISMS["new"] = frozenset()  # type: ignore[index]

    def test_admissible_sets_are_frozen(self) -> None:
        mechs = admissible_mechanisms("selector_weights")
        assert isinstance(mechs, frozenset)


__all__: list[str] = []
