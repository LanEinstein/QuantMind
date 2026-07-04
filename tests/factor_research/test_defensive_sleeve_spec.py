"""Frozen-spec invariants for :mod:`scripts.factor_research.defensive_sleeve_spec`.

The deployable defensive sleeve is committed + hashed BEFORE the confirmatory sci-gate
backtest and the pre-registered forward validation run. These tests pin the committed
composition and the anti-p-hacking reuse (the universe filter is D1's validated filter,
not re-tuned; the container is the frontier buf40_5).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scripts.factor_research import defensive_d1_spec as d1
from scripts.factor_research import defensive_sleeve_spec as spec
from scripts.factor_research.slot_frontier import FRONTIER


def test_universe_filter_reuses_d1_validated_filter() -> None:
    # Structural asdict equality (field SET + values), NOT a hardcoded field list — so a
    # field ADDED to / REMOVED from D1's validated filter breaks this guard instead of
    # silently leaving the sleeve's hand-typed copy stale (codex).
    assert dataclasses.asdict(spec.UNIVERSE_FILTER) == dataclasses.asdict(
        d1.UNIVERSE_FILTERS
    )


def test_selection_is_simplest_deterministic_rule() -> None:
    assert spec.SELECTION_FACTOR == "dv_ratio"
    assert spec.SELECTION_TOP_N == 5
    assert spec.SELECTION_WEIGHTING == "equal_weight"


def test_analyst_tilt_off_by_default() -> None:
    assert spec.ANALYST_TILT.enabled is False
    assert spec.ANALYST_TILT.factors == ("np_rev", "rev_diff", "cover_chg")
    assert "tie_break" in spec.ANALYST_TILT.role


def test_container_is_frontier_buf40_5() -> None:
    buf = {c.label: c for c in FRONTIER}["buf40_5"]
    assert spec.CONTAINER.label == "buf40_5"
    assert spec.CONTAINER.slots == buf.slots
    assert spec.CONTAINER.cap_percent == buf.cap_percent
    assert spec.CONTAINER.slots * spec.CONTAINER.cap_percent == 40  # ≈40% gross


def test_horizon_is_monthly() -> None:
    assert spec.HORIZON == spec.REBALANCE_FREQ == 20


def test_science_gate_is_risk_property_dsr_disclosed() -> None:
    g = spec.SCIENCE_GATE
    assert g.net_pnl_positive is True
    assert g.bear_cum_nonneg is True
    assert 0.0 < g.mdd_disclose_bound < 1.0
    assert g.dsr_role == "disclosure_only"


def test_forward_kill_switch_pre_registered() -> None:
    k = spec.FORWARD_KILL_SWITCH
    assert k.mdd_kill > spec.SCIENCE_GATE.mdd_disclose_bound  # kill > disclosed bound
    assert k.bear_cum_kill < 0.0
    assert k.baseline_underperf_periods >= 1
    assert k.min_forward_periods >= 1


def test_go_live_and_amendment_pointers() -> None:
    assert "P0-6" in spec.GO_LIVE_GATE
    assert spec.AMENDMENT.startswith("qgr-certification-rearch-amendment-2026-07-04")


def test_spec_hash_deterministic_and_frozen() -> None:
    assert spec.spec_hash() == spec.spec_hash()
    assert len(spec.spec_hash()) == 64
    assert (
        spec.spec_hash()
        == "c1d058c36ac0ae0f693078187dbc6df8eaaa4a8bcb3700c9c32caaff5d2543c1"
    )


def test_governance_prose_excluded_from_hash() -> None:
    # GO_LIVE_GATE / AMENDMENT are re-wordable governance prose, not constants —
    # a reword must NOT mutate the frozen hash (D1/D2/AM convention; codex).
    dumped = str(spec._canonical_payload())
    assert "go_live" not in dumped
    assert spec.GO_LIVE_GATE not in dumped
    assert spec.AMENDMENT not in dumped


def test_all_constants_frozen() -> None:
    for obj in (
        spec.UNIVERSE_FILTER,
        spec.ANALYST_TILT,
        spec.CONTAINER,
        spec.SCIENCE_GATE,
        spec.FORWARD_KILL_SWITCH,
    ):
        assert dataclasses.is_dataclass(obj)
        assert obj.__dataclass_params__.frozen  # type: ignore[attr-defined]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, next(iter(dataclasses.fields(obj))).name, None)


def test_module_pure_no_backend_import() -> None:
    source = Path(spec.__file__).read_text(encoding="utf-8")
    for forbidden in ("import backend", "from backend"):
        assert forbidden not in source, forbidden
