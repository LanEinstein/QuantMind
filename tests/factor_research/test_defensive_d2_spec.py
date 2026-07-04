"""Frozen-spec invariants for :mod:`scripts.factor_research.defensive_d2_spec`.

The D2 spec is committed and hashed *before* any evaluation touches returns; these tests
pin every committed value so an accidental edit after the ledger stamp is caught. They
also assert the drift guard to ``exit_veto_panel`` (the ranker is REUSED, not copied)
and
the field-exact anchor to ``slot_frontier.FRONTIER``, and the purity of the module.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scripts.factor_research import defensive_d2_spec as spec
from scripts.factor_research import exit_veto_panel as xv
from scripts.factor_research.slot_frontier import FRONTIER


def test_ranker_reuses_exit_veto_panel_no_drift() -> None:
    # The ranker is byte-identical to A0 by REUSE; the spec only mirrors the tuple.
    assert spec.RANKER_FACTORS == xv.RANKER_FACTORS
    assert spec.RANKER_FACTORS == ("rev_1d", "max_5d", "turn_spike")
    assert spec.RANKER_IMPLEMENTATION == "exit_veto_panel.build_ranker_table"
    assert spec.CROWD_FACTOR == xv.CROWD_FACTOR == "ideal_amplitude_20d"


def test_universe_filter_committed_thresholds() -> None:
    f = spec.D2_UNIVERSE_FILTER
    assert f.vol_keep_max_quantile == 0.60
    assert f.max20d_lottery_exclude_quantile == 0.90
    assert f.dividend_min_percentile == 0.50
    assert f.roe_floor == 0.0
    assert f.gpm_floor_quantile == 0.10


def test_horizon_and_cadence_are_a0_parity() -> None:
    from scripts.factor_research.slot_frontier import HORIZON, REBALANCE_FREQ

    assert spec.HORIZON == HORIZON == 5
    assert spec.REBALANCE_FREQ == REBALANCE_FREQ == 5


def test_containers_field_identical_to_slot_frontier() -> None:
    frontier_by_label = {c.label: c for c in FRONTIER}
    for container in spec.CONTAINERS:
        anchor = frontier_by_label[container.label]
        assert container.slots == anchor.slots, container.label
        assert container.cap_percent == anchor.cap_percent, container.label
    assert tuple(c.label for c in spec.CONTAINERS) == ("eq_5", "buf40_5")
    buf = frontier_by_label["buf40_5"]
    assert buf.slots * buf.cap_percent == 40  # ≈40% gross / 60% cash buffer


def test_neutralization_recipe_committed() -> None:
    assert spec.NEUTRALIZATION == (
        "industry_sw_l1",
        "log_circ_mv",
        "winsor_0.01",
        "min_obs_20",
    )


def test_placebo_config_committed() -> None:
    assert spec.PLACEBO_SEED == 20260704
    assert spec.PLACEBO_TOP_N == 5
    assert spec.BEATS_PLACEBO_T == 2.0


def test_gate_calibration_not_relaxed_and_dsr_disclosure_only() -> None:
    g = spec.GATE_CALIBRATION
    assert g.dsr_threshold == 0.95
    assert g.pbo_threshold == 0.5
    assert g.spa_method == "hansen"
    assert g.rw_method == "romano_wolf"
    assert g.cpcv_purge_embargo == spec.HORIZON - 1 == 4
    assert g.deflation_n == 2418  # non-zeroing ledger floor pre-D2 (D1 appended → 2418)
    assert spec.DSR_ROLE == "disclosure_only"


def test_promotion_gates_are_the_selection_gate() -> None:
    assert spec.PROMOTION_GATES == (
        "beats_own_random_placebo_joint_t2",
        "bear_cum_nonneg",
        "crash_slices_nonneg",
        "net_pnl_positive",
    )


def test_decision_branches_pre_registered() -> None:
    keys = tuple(b[0] for b in spec.DECISION_BRANCHES)
    assert keys == ("a", "b", "c")
    assert "beats_own_random_placebo_joint_t2" in "".join(spec.PROMOTION_GATES)
    assert spec.AMENDMENT.startswith("qgr-certification-rearch-amendment-2026-07-04")


def test_spec_hash_deterministic() -> None:
    assert spec.spec_hash() == spec.spec_hash()
    assert len(spec.spec_hash()) == 64
    int(spec.spec_hash(), 16)  # valid hex


def test_spec_hash_frozen_value() -> None:
    # The value stamped into the ledger + result doc; a change here means the committed
    # spec drifted (an evaluation-time edit) — must fail loudly.
    assert (
        spec.spec_hash()
        == "a548273b9e46cbb2d5eb37c2599cc19ac87547a1f7f4139fc2bbf924f612bedf"
    )


def test_branch_prose_excluded_from_hash() -> None:
    # Only the branch KEYS enter the hash; the descriptions are documentation, so a
    # reword must NOT perturb the frozen scientific digest (codex robustness finding).
    payload = spec._canonical_payload()
    assert payload["decision_branches"] == ["a", "b", "c"]
    dumped = str(payload)
    # Tokens unique to the branch DESCRIPTIONS must be absent from the hashed payload.
    assert "forward queue" not in dumped
    assert "sleeve-only" not in dumped
    assert "death sentence" not in dumped
    assert spec.AMENDMENT not in dumped  # amendment pointer also excluded


def test_all_constants_are_frozen() -> None:
    for obj in (spec.D2_UNIVERSE_FILTER, spec.CONTAINERS[0], spec.GATE_CALIBRATION):
        assert dataclasses.is_dataclass(obj)
        assert obj.__dataclass_params__.frozen  # type: ignore[attr-defined]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, next(iter(dataclasses.fields(obj))).name, None)


def test_module_is_pure_no_backend_import() -> None:
    source = Path(spec.__file__).read_text(encoding="utf-8")
    for forbidden in ("import backend", "from backend"):
        assert forbidden not in source, forbidden
