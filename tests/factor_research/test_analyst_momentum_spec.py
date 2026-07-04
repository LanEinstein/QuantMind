"""Frozen-spec invariants for :mod:`scripts.factor_research.analyst_momentum_spec`.

The analyst-momentum spec is committed and hashed *before* any evaluation touches P&L.
These tests pin every committed value and — critically — the anti-p-hacking guards:
the factor subset + signs are REUSED verbatim from round-4's frozen analyst block, never
re-selected; the analyst PIT windows match the module defaults; the signs are consistent
with the factor_lib registry orientation.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scripts.factor_research import alpha_pivot_spec as ap
from scripts.factor_research import analyst_momentum_spec as spec
from scripts.factor_research import analyst_revision_pit as arp
from scripts.factor_research.factor_lib import R4_FACTORS_BY_NAME
from scripts.factor_research.slot_frontier import FRONTIER


def test_ranker_reuses_round4_analyst_block_no_drift() -> None:
    mine = {f.name: f.sign for f in spec.RANKER_FACTORS}
    ap_block = {f.name: f.sign for f in ap.RANKER_FACTORS if f.block == "analyst"}
    assert mine == ap_block  # subset + signs reused verbatim (not re-selected)
    assert mine == {"np_rev": 1, "rev_diff": 1, "cover_chg": 1}


def test_factor_names_in_analyst_pit_registry() -> None:
    for f in spec.RANKER_FACTORS:
        assert f.name in arp.ANALYST_FACTOR_NAMES


def test_signs_consistent_with_factor_lib_orientation() -> None:
    # +1 (long the high leg) must match attractive_high=True in the R4 registry.
    for f in spec.RANKER_FACTORS:
        attractive_high = R4_FACTORS_BY_NAME[f.name].attractive_high
        assert (f.sign == 1) == attractive_high, f.name


def test_analyst_windows_match_module_defaults() -> None:
    w = spec.ANALYST_WINDOWS
    assert (w.staleness_days, w.lookback_days, w.level_window_days) == (
        arp.STALENESS_DAYS,
        arp.LOOKBACK_DAYS,
        arp.LEVEL_WINDOW_DAYS,
    )
    assert (w.staleness_days, w.lookback_days, w.level_window_days) == (90, 90, 180)


def test_factor_sign_lookup() -> None:
    assert spec.factor_sign("np_rev") == 1
    assert spec.factor_sign("cover_chg") == 1
    with pytest.raises(KeyError):
        spec.factor_sign("nope")


def test_horizon_is_monthly() -> None:
    assert spec.HORIZON == spec.REBALANCE_FREQ == 20


def test_containers_field_identical_to_slot_frontier() -> None:
    frontier_by_label = {c.label: c for c in FRONTIER}
    for container in spec.CONTAINERS:
        anchor = frontier_by_label[container.label]
        assert container.slots == anchor.slots, container.label
        assert container.cap_percent == anchor.cap_percent, container.label
    assert tuple(c.label for c in spec.CONTAINERS) == ("eq_5", "buf40_5")


def test_gate_calibration_not_relaxed_and_dsr_disclosure_only() -> None:
    g = spec.GATE_CALIBRATION
    assert g.dsr_threshold == 0.95
    assert g.pbo_threshold == 0.5
    assert g.spa_method == "hansen"
    assert g.rw_method == "romano_wolf"
    assert g.cpcv_purge_embargo == spec.HORIZON - 1 == 19
    assert g.deflation_n == 2419  # non-zeroing ledger floor post ds.d2
    assert spec.DSR_ROLE == "disclosure_only"


def test_promotion_gates_are_the_selection_gate() -> None:
    assert spec.PROMOTION_GATES == (
        "beats_own_random_placebo_joint_t2",
        "bear_cum_nonneg",
        "crash_slices_nonneg",
        "net_pnl_positive",
    )


def test_placebo_and_neutralization_committed() -> None:
    assert spec.PLACEBO_SEED == 20260704
    assert spec.PLACEBO_TOP_N == 5
    assert spec.BEATS_PLACEBO_T == 2.0
    assert spec.NEUTRALIZATION == (
        "industry_sw_l1",
        "log_circ_mv",
        "winsor_0.01",
        "min_obs_20",
    )
    assert spec.FACTOR_WEIGHT_RULE == "equal_weight_signed_zscore_mean"


def test_spec_hash_deterministic() -> None:
    assert spec.spec_hash() == spec.spec_hash()
    assert len(spec.spec_hash()) == 64
    int(spec.spec_hash(), 16)


def test_spec_hash_frozen_value() -> None:
    assert (
        spec.spec_hash()
        == "a84dd243ebb5a7048d8e9c9f8e37f081f976e1d6ccc64bc26f7395345a6b5c73"
    )


def test_source_prose_excluded_from_hash() -> None:
    dumped = str(spec._canonical_payload())
    assert "round-4" not in dumped
    assert "analyst_revision_pit" not in dumped


def test_all_constants_are_frozen() -> None:
    for obj in (
        spec.RANKER_FACTORS[0],
        spec.ANALYST_WINDOWS,
        spec.CONTAINERS[0],
        spec.GATE_CALIBRATION,
    ):
        assert dataclasses.is_dataclass(obj)
        assert obj.__dataclass_params__.frozen  # type: ignore[attr-defined]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, next(iter(dataclasses.fields(obj))).name, None)


def test_module_is_pure_no_backend_import() -> None:
    source = Path(spec.__file__).read_text(encoding="utf-8")
    for forbidden in ("import backend", "from backend"):
        assert forbidden not in source, forbidden
