"""Tests for the AE-005 first-class immutable candidate batch + sentinels + mandate."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from backend.strategy_evolution.candidate_batch import (
    CandidateBatch,
    assemble_batch,
)
from backend.strategy_evolution.forward_shadow_mandate import (
    MIN_FORWARD_SHADOW_CALENDAR_DAYS,
    PREDECLARED_FORWARD_SHADOW_METRICS,
    ForwardShadowMandate,
    HonestDashboard,
)
from backend.strategy_evolution.mechanism_registry import EconomicMechanism
from backend.strategy_evolution.quant_param_search import (
    SELECTOR_WEIGHTS_FAMILY,
    ParamExperimentProducer,
    ParamSearchError,
)
from backend.strategy_evolution.sentinel import make_sentinels

_MECH = EconomicMechanism.MOMENTUM_CONTINUATION


def _build(n_real: int = 8, n_sentinel: int = 2, seed: int = 42) -> CandidateBatch:
    producer = ParamExperimentProducer(family=SELECTOR_WEIGHTS_FAMILY)
    real = producer.produce(seed=seed, n_candidates=n_real, mechanism=_MECH)
    sentinels = make_sentinels(
        family=SELECTOR_WEIGHTS_FAMILY, count=n_sentinel, seed=seed
    )
    return assemble_batch(
        family=SELECTOR_WEIGHTS_FAMILY,
        seed=seed,
        declared_n=n_real,
        window_start="2015-01-05",
        window_end="2026-06-01",
        cumulative_n_at_creation=n_real,
        mechanism=_MECH,
        real_candidates=real,
        sentinels=sentinels,
    )


class TestImmutability:
    def test_batch_is_frozen(self) -> None:
        batch = _build()
        with pytest.raises(dataclasses.FrozenInstanceError):
            batch.family = "nope"  # type: ignore[misc]

    def test_candidates_tuple_not_a_mutation_surface(self) -> None:
        batch = _build()
        assert isinstance(batch.candidates, tuple)
        assert isinstance(batch.sentinel_hashes, frozenset)

    def test_batch_id_stable(self) -> None:
        assert _build(seed=42).batch_id == _build(seed=42).batch_id

    def test_batch_id_changes_with_candidates(self) -> None:
        assert _build(seed=1).batch_id != _build(seed=2).batch_id


class TestPartitions:
    def test_real_vs_sentinel_split(self) -> None:
        batch = _build(n_real=8, n_sentinel=3)
        assert len(batch.real_candidates) == 8
        assert len(batch.sentinels) == 3
        assert len(batch.sentinel_hashes) == 3
        assert all(c.mechanism is None for c in batch.sentinels)
        assert all(c.is_sentinel for c in batch.sentinels)
        assert all(not c.is_sentinel for c in batch.real_candidates)


class TestAssembleGuards:
    def test_rejects_real_candidate_flagged_sentinel(self) -> None:
        producer = ParamExperimentProducer(family=SELECTOR_WEIGHTS_FAMILY)
        real = producer.produce(seed=1, n_candidates=2, mechanism=_MECH)
        tampered = dataclasses.replace(real[0], is_sentinel=True)
        with pytest.raises(ParamSearchError):
            assemble_batch(
                family=SELECTOR_WEIGHTS_FAMILY,
                seed=1,
                declared_n=2,
                window_start="2015-01-05",
                window_end="2026-06-01",
                cumulative_n_at_creation=2,
                mechanism=_MECH,
                real_candidates=[tampered, real[1]],
                sentinels=[],
            )

    def test_rejects_duplicate_candidate(self) -> None:
        producer = ParamExperimentProducer(family=SELECTOR_WEIGHTS_FAMILY)
        real = producer.produce(seed=1, n_candidates=1, mechanism=_MECH)
        with pytest.raises(ParamSearchError):
            assemble_batch(
                family=SELECTOR_WEIGHTS_FAMILY,
                seed=1,
                declared_n=2,
                window_start="2015-01-05",
                window_end="2026-06-01",
                cumulative_n_at_creation=2,
                mechanism=_MECH,
                real_candidates=[real[0], real[0]],
                sentinels=[],
            )

    def test_rejects_cumulative_n_below_batch_size(self) -> None:
        producer = ParamExperimentProducer(family=SELECTOR_WEIGHTS_FAMILY)
        real = producer.produce(seed=1, n_candidates=4, mechanism=_MECH)
        with pytest.raises(ParamSearchError):
            assemble_batch(
                family=SELECTOR_WEIGHTS_FAMILY,
                seed=1,
                declared_n=4,
                window_start="2015-01-05",
                window_end="2026-06-01",
                cumulative_n_at_creation=2,
                mechanism=_MECH,
                real_candidates=real,
                sentinels=[],
            )


class TestSentinels:
    def test_deterministic(self) -> None:
        a = make_sentinels(family=SELECTOR_WEIGHTS_FAMILY, count=3, seed=9)
        b = make_sentinels(family=SELECTOR_WEIGHTS_FAMILY, count=3, seed=9)
        assert [s.param_hash for s in a] == [s.param_hash for s in b]

    def test_sentinels_have_no_mechanism(self) -> None:
        sentinels = make_sentinels(family=SELECTOR_WEIGHTS_FAMILY, count=4, seed=1)
        assert all(s.mechanism is None for s in sentinels)
        assert all(s.is_sentinel for s in sentinels)

    def test_zero_count(self) -> None:
        assert make_sentinels(family=SELECTOR_WEIGHTS_FAMILY, count=0, seed=1) == ()


class TestForwardShadowMandate:
    def _mandate(self) -> ForwardShadowMandate:
        return ForwardShadowMandate(
            batch_id="b" * 64,
            family=SELECTOR_WEIGHTS_FAMILY,
            mechanism=_MECH,
            candidate_param_hash="a" * 64,
            frozen_param_values=(("selector.weight_momentum", 0.4),),
            predeclared_metrics=PREDECLARED_FORWARD_SHADOW_METRICS,
            prefilter_excess_sharpe=0.12,
            calendar_start_date="2026-06-15",
            min_calendar_days=MIN_FORWARD_SHADOW_CALENDAR_DAYS,
            created_at=dt.datetime(2026, 6, 15, 22, 0, tzinfo=dt.UTC),
        )

    def test_fresh_mandate_is_not_complete(self) -> None:
        mandate = self._mandate()
        assert mandate.is_shadow_window_complete(as_of=dt.date(2026, 6, 15)) is False

    def test_incomplete_before_45_calendar_days(self) -> None:
        mandate = self._mandate()
        assert mandate.is_shadow_window_complete(as_of=dt.date(2026, 7, 20)) is False

    def test_complete_after_45_calendar_days(self) -> None:
        mandate = self._mandate()
        assert mandate.is_shadow_window_complete(as_of=dt.date(2026, 7, 30)) is True

    def test_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            self._mandate().prefilter_excess_sharpe = 0.0  # type: ignore[misc]


class TestHonestDashboard:
    def _dash(self, sentinels_passed: int = 0, survivors: int = 1) -> HonestDashboard:
        return HonestDashboard(
            batch_id="c" * 64,
            family=SELECTOR_WEIGHTS_FAMILY,
            cumulative_n=128,
            real_candidate_count=16,
            sentinel_count=2,
            sentinels_passed=sentinels_passed,
            survivors=survivors,
            pbo=0.3,
            spa_p_value=0.04,
            n_observations=2000,
            min_observations_required=500,
            batch_admitted=True,
            days_since_last_promotion=None,
        )

    def test_integrity_ok_when_no_sentinel_passes(self) -> None:
        assert self._dash(sentinels_passed=0).sentinel_integrity_ok is True

    def test_integrity_broken_when_a_sentinel_passes(self) -> None:
        assert self._dash(sentinels_passed=1).sentinel_integrity_ok is False

    def test_no_survivors_means_no_alpha(self) -> None:
        assert self._dash(survivors=0).space_has_alpha_signal is False

    def test_summary_is_a_string(self) -> None:
        assert isinstance(self._dash().summary(), str)


__all__: list[str] = []
