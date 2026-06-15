"""Tests for the AE-005 honest deterministic Sobol parameter producer."""

from __future__ import annotations

import pytest

from backend.strategy_evolution.evolvable_params import (
    SELECTOR_WEIGHT_SUM_TOLERANCE,
    validate_param_set,
)
from backend.strategy_evolution.mechanism_registry import EconomicMechanism
from backend.strategy_evolution.quant_param_search import (
    SELECTOR_WEIGHTS_FAMILY,
    THEME_TIER_WEIGHTS_FAMILY,
    VALUE_SLOT_QUOTA_FAMILY,
    ParamExperimentProducer,
    ParamSearchError,
    ParamSet,
    _finalise_sum_to_one,
    assert_cumulative_n_not_reset,
    effective_dimension,
)

_MECH = {
    SELECTOR_WEIGHTS_FAMILY: EconomicMechanism.MOMENTUM_CONTINUATION,
    THEME_TIER_WEIGHTS_FAMILY: EconomicMechanism.DIVERSIFICATION,
    VALUE_SLOT_QUOTA_FAMILY: EconomicMechanism.VALUE_PREMIUM,
}


class TestDeterminism:
    def test_same_seed_bit_identical_sequence(self) -> None:
        producer = ParamExperimentProducer(family=SELECTOR_WEIGHTS_FAMILY)
        first = producer.produce(
            seed=20260615, n_candidates=16, mechanism=_MECH[SELECTOR_WEIGHTS_FAMILY]
        )
        second = producer.produce(
            seed=20260615, n_candidates=16, mechanism=_MECH[SELECTOR_WEIGHTS_FAMILY]
        )
        assert [p.param_hash for p in first] == [p.param_hash for p in second]
        assert first == second

    def test_different_seed_diverges(self) -> None:
        producer = ParamExperimentProducer(family=SELECTOR_WEIGHTS_FAMILY)
        a = producer.produce(
            seed=1, n_candidates=16, mechanism=_MECH[SELECTOR_WEIGHTS_FAMILY]
        )
        b = producer.produce(
            seed=2, n_candidates=16, mechanism=_MECH[SELECTOR_WEIGHTS_FAMILY]
        )
        assert [p.param_hash for p in a] != [p.param_hash for p in b]


class TestLegality:
    @pytest.mark.parametrize("family", list(_MECH))
    def test_every_candidate_is_legal(self, family: str) -> None:
        producer = ParamExperimentProducer(family=family)
        candidates = producer.produce(seed=7, n_candidates=64, mechanism=_MECH[family])
        for cand in candidates:
            assert validate_param_set(cand.as_dict()).passed, cand.as_dict()

    def test_selector_weights_sum_to_one_and_within_caps(self) -> None:
        producer = ParamExperimentProducer(family=SELECTOR_WEIGHTS_FAMILY)
        for cand in producer.produce(
            seed=3, n_candidates=128, mechanism=_MECH[SELECTOR_WEIGHTS_FAMILY]
        ):
            values = cand.as_dict()
            assert abs(sum(values.values()) - 1.0) <= SELECTOR_WEIGHT_SUM_TOLERANCE
            for v in values.values():
                assert 0.0 <= v <= 0.6

    def test_theme_tiers_monotone_non_increasing(self) -> None:
        producer = ParamExperimentProducer(family=THEME_TIER_WEIGHTS_FAMILY)
        for cand in producer.produce(
            seed=11, n_candidates=128, mechanism=_MECH[THEME_TIER_WEIGHTS_FAMILY]
        ):
            v = cand.as_dict()
            seq = [
                v["theme.tier1_weight"],
                v["theme.tier2_weight"],
                v["theme.tier3_weight"],
                v["theme.tier4_weight"],
            ]
            assert all(a >= b for a, b in zip(seq, seq[1:], strict=False)), seq

    def test_value_quota_is_integer_in_range(self) -> None:
        producer = ParamExperimentProducer(family=VALUE_SLOT_QUOTA_FAMILY)
        seen: set[float] = set()
        for cand in producer.produce(
            seed=5, n_candidates=64, mechanism=_MECH[VALUE_SLOT_QUOTA_FAMILY]
        ):
            value = cand.as_dict()[VALUE_SLOT_QUOTA_FAMILY]
            assert value == int(value)
            assert 0 <= value <= 2
            seen.add(value)
        assert len(seen) >= 2  # the producer actually explores the small domain


class TestEffectiveDimension:
    def test_selector_simplex_drops_one_dim(self) -> None:
        assert effective_dimension(SELECTOR_WEIGHTS_FAMILY) == 4

    def test_theme_tiers_full_dim(self) -> None:
        assert effective_dimension(THEME_TIER_WEIGHTS_FAMILY) == 4

    def test_quota_one_dim(self) -> None:
        assert effective_dimension(VALUE_SLOT_QUOTA_FAMILY) == 1


class TestGuards:
    def test_unknown_family_rejected(self) -> None:
        with pytest.raises(ParamSearchError):
            ParamExperimentProducer(family="risk.max_total_position_pct")

    def test_inadmissible_mechanism_rejected(self) -> None:
        producer = ParamExperimentProducer(family=THEME_TIER_WEIGHTS_FAMILY)
        with pytest.raises(ParamSearchError):
            producer.produce(
                seed=1, n_candidates=4, mechanism=EconomicMechanism.VALUE_PREMIUM
            )

    def test_zero_candidates_rejected(self) -> None:
        producer = ParamExperimentProducer(family=SELECTOR_WEIGHTS_FAMILY)
        with pytest.raises(ParamSearchError):
            producer.produce(
                seed=1, n_candidates=0, mechanism=_MECH[SELECTOR_WEIGHTS_FAMILY]
            )


class TestCumulativeNGuard:
    def test_reset_rejected(self) -> None:
        with pytest.raises(ParamSearchError):
            assert_cumulative_n_not_reset(
                declared_cumulative_n=40, registry_trial_count=128
            )

    def test_monotone_accepted(self) -> None:
        assert_cumulative_n_not_reset(
            declared_cumulative_n=200, registry_trial_count=128
        )
        assert_cumulative_n_not_reset(
            declared_cumulative_n=128, registry_trial_count=128
        )

    def test_negative_rejected(self) -> None:
        with pytest.raises(ParamSearchError):
            assert_cumulative_n_not_reset(
                declared_cumulative_n=-1, registry_trial_count=0
            )


class TestFinaliseSumToOne:
    def test_negative_residual_never_makes_a_weight_negative(self) -> None:
        # Projected weights that round UP to sum > 1 with a zero-valued
        # max-headroom weight: the residual is negative and must NOT be dumped
        # onto the zero weight (which would drive it below 0 and crash produce()).
        caps = [0.6] * 5
        out = _finalise_sum_to_one([0.0, 0.4, 0.3, 0.300001, 0.0], caps)
        assert all(0.0 <= w <= 0.6 for w in out), out
        assert abs(sum(out) - 1.0) <= 1e-9

    def test_positive_residual_stays_within_caps(self) -> None:
        caps = [0.6] * 5
        out = _finalise_sum_to_one([0.0000004, 0.333333, 0.333333, 0.333333, 0.0], caps)
        assert all(0.0 <= w <= 0.6 for w in out)
        assert abs(sum(out) - 1.0) <= 1e-9


class TestParamSet:
    def test_param_space_strings_fixed_precision(self) -> None:
        ps = ParamSet(
            family=VALUE_SLOT_QUOTA_FAMILY,
            values=((VALUE_SLOT_QUOTA_FAMILY, 1.0),),
            mechanism=EconomicMechanism.VALUE_PREMIUM,
        )
        assert ps.param_space_strings() == {VALUE_SLOT_QUOTA_FAMILY: "1.000000"}

    def test_param_hash_stable_and_sentinel_sensitive(self) -> None:
        base = ParamSet(
            family=VALUE_SLOT_QUOTA_FAMILY,
            values=((VALUE_SLOT_QUOTA_FAMILY, 1.0),),
            mechanism=EconomicMechanism.VALUE_PREMIUM,
        )
        sentinel = ParamSet(
            family=VALUE_SLOT_QUOTA_FAMILY,
            values=((VALUE_SLOT_QUOTA_FAMILY, 1.0),),
            mechanism=None,
            is_sentinel=True,
        )
        assert base.param_hash == base.param_hash
        assert base.param_hash != sentinel.param_hash


__all__: list[str] = []
