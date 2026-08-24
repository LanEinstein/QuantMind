"""Frozen-spec invariants for :mod:`scripts.factor_research.defensive_sleeve2_spec`.

SLV-2 is committed + hashed BEFORE its confirmatory science gate runs
(preregistration ``docs/research/defensive-sleeve2-preregistration-2026-08-23.md``).
These tests pin the committed composition, the verbatim reuse of the D1-validated
universe filter, and the preregistered four-criteria gate constants.
"""

from __future__ import annotations

import dataclasses

from scripts.factor_research import defensive_d1_spec as d1
from scripts.factor_research import defensive_sleeve2_spec as spec
from scripts.factor_research import defensive_sleeve_spec as slv1
from scripts.factor_research.slot_frontier import FRONTIER


def test_universe_filter_reuses_d1_validated_filter() -> None:
    assert dataclasses.asdict(spec.UNIVERSE_FILTER) == dataclasses.asdict(
        d1.UNIVERSE_FILTERS
    )


def test_selection_is_single_raw_gpm_rule() -> None:
    assert spec.SELECTION_FACTOR == "gpm"
    assert spec.SELECTION_TOP_N == 5
    assert spec.SELECTION_WEIGHTING == "equal_weight"


def test_slv1_exclusion_matches_slv1_committed_rule() -> None:
    assert spec.SLV1_EXCLUSION.factor == slv1.SELECTION_FACTOR == "dv_ratio"
    assert spec.SLV1_EXCLUSION.top_n == slv1.SELECTION_TOP_N == 5


def test_container_is_frontier_buf40_5() -> None:
    buf = {c.label: c for c in FRONTIER}["buf40_5"]
    assert spec.CONTAINER.label == "buf40_5"
    assert spec.CONTAINER.slots == buf.slots
    assert spec.CONTAINER.cap_percent == buf.cap_percent


def test_horizon_is_monthly() -> None:
    assert spec.HORIZON == spec.REBALANCE_FREQ == 20


def test_science_gate_constants_match_preregistration() -> None:
    g = spec.SCIENCE_GATE
    assert g.net_pnl_positive is True
    assert g.bear_cum_nonneg is True
    assert g.mdd_hard_bound == 0.20
    assert g.placebo_t_min == 2.0
    assert spec.PLACEBO_SEED == 20260823


def test_spec_hash_deterministic_and_distinct_from_slv1() -> None:
    assert spec.spec_hash() == spec.spec_hash()
    assert len(spec.spec_hash()) == 64
    assert spec.spec_hash() != slv1.spec_hash()
