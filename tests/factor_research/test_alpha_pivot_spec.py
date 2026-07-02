"""Frozen-spec invariants for :mod:`scripts.factor_research.alpha_pivot_spec`.

The alpha-pivot fixed prior spec is committed and hashed *before* any evaluation
touches returns; these tests pin every committed value so an accidental edit
after the ledger stamp (AP0-002) is caught immediately. They also assert the
byte-exact anchor to ``slot_frontier.FRONTIER`` and the purity of the module.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scripts.factor_research import alpha_pivot_spec as spec
from scripts.factor_research.bottom_confirmation import CORE_CONDITION_NAMES
from scripts.factor_research.slot_frontier import FRONTIER

# The single source of truth for the committed factor set (implementation plan §3).
_EXPECTED_FACTORS: tuple[tuple[str, int, str, str], ...] = (
    ("rev_1d", -1, "reversal", "verified"),
    ("max_5d", -1, "reversal", "verified"),
    ("turn_spike", -1, "reversal", "verified"),
    ("np_rev", +1, "analyst", "cautious"),
    ("rev_diff", +1, "analyst", "cautious"),
    ("cover_chg", +1, "analyst", "cautious"),
    ("roe", +1, "quality", "cautious"),
    ("gpm", +1, "quality", "cautious"),
    ("ep_ttm", +1, "quality", "cautious"),
    ("accr", -1, "quality", "cautious"),
)


def test_ranker_factors_match_spec_exactly() -> None:
    got = tuple((f.name, f.sign, f.block, f.grade) for f in spec.RANKER_FACTORS)
    assert got == _EXPECTED_FACTORS
    assert len(spec.RANKER_FACTORS) == 10


def test_block_composition() -> None:
    blocks = {
        b: [f.name for f in spec.RANKER_FACTORS if f.block == b]
        for b in spec.BLOCK_NAMES
    }
    assert blocks["reversal"] == ["rev_1d", "max_5d", "turn_spike"]
    assert blocks["analyst"] == ["np_rev", "rev_diff", "cover_chg"]
    assert blocks["quality"] == ["roe", "gpm", "ep_ttm", "accr"]


def test_block_weights_committed_and_sum_to_one() -> None:
    assert spec.BLOCK_WEIGHTS == (
        ("reversal", 0.5),
        ("analyst", 0.25),
        ("quality", 0.25),
    )
    assert sum(w for _, w in spec.BLOCK_WEIGHTS) == pytest.approx(1.0)
    assert spec.block_weight("reversal") == 0.5
    assert spec.block_weight("analyst") == 0.25
    assert spec.block_weight("quality") == 0.25
    with pytest.raises(KeyError):
        spec.block_weight("nope")


def test_every_block_has_weight_and_factors() -> None:
    weighted = {name for name, _ in spec.BLOCK_WEIGHTS}
    assert weighted == set(spec.BLOCK_NAMES)
    for block in spec.BLOCK_NAMES:
        assert any(f.block == block for f in spec.RANKER_FACTORS)


def test_universe_filters_mirror_bottom_confirmation_verbatim() -> None:
    # core-4 must equal the bottom_confirmation source of truth, order-preserving.
    assert spec.UNIVERSE_FILTERS.bottom_confirmation_core == CORE_CONDITION_NAMES
    # cyq cost band pre-declared dropped; the hard exclusions / at-limit removal kept.
    assert spec.UNIVERSE_FILTERS.cyq_cost_band_included is False
    assert spec.UNIVERSE_FILTERS.exclusion_four_piece_applied is True
    assert spec.UNIVERSE_FILTERS.at_limit_unfillable_removed is True


def test_containers_field_identical_to_slot_frontier() -> None:
    frontier_by_label = {c.label: c for c in FRONTIER}
    for container in spec.CONTAINERS:
        anchor = frontier_by_label[container.label]
        assert container.slots == anchor.slots, container.label
        assert container.cap_percent == anchor.cap_percent, container.label
    assert tuple(c.label for c in spec.CONTAINERS) == ("eq_5", "buf40_5")
    # buf40_5 = 5 slots × 8% cap ≈ 40% gross / 60% cash buffer (P-E ≥40% floor).
    buf = frontier_by_label["buf40_5"]
    assert buf.slots * buf.cap_percent == 40


def test_gate_calibration_not_relaxed() -> None:
    g = spec.GATE_CALIBRATION
    assert g.dsr_threshold == 0.95
    assert g.pbo_threshold == 0.5
    assert g.spa_method == "hansen"
    assert g.rw_method == "romano_wolf"
    assert g.cpcv_purge_embargo == 4  # = horizon - 1
    assert g.deflation_n == 2417


def test_power_inputs_pre_declared() -> None:
    p = spec.POWER_INPUTS
    assert (p.skew, p.kurtosis) == (0.0, 3.0)  # normal moments
    assert p.hac_lag == 4 == p.horizon - 1
    assert p.rebalance_freq == 5
    assert p.t_onc_effective == 497
    assert p.deflation_n == spec.GATE_CALIBRATION.deflation_n == 2417
    assert p.k_power == 2.0  # owner decision #2
    # SR_ref is disclosed/derived at AP-0.5 — its SOURCE is committed, not a number.
    assert "zero new peek" in p.sr_ref_source


def test_spec_hash_deterministic() -> None:
    assert spec.spec_hash() == spec.spec_hash()
    assert len(spec.spec_hash()) == 64
    int(spec.spec_hash(), 16)  # valid hex


def test_all_constants_are_frozen() -> None:
    frozen_instances = [
        spec.RANKER_FACTORS[0],
        spec.UNIVERSE_FILTERS,
        spec.CONTAINERS[0],
        spec.GATE_CALIBRATION,
        spec.POWER_INPUTS,
    ]
    for obj in frozen_instances:
        assert dataclasses.is_dataclass(obj)
        assert obj.__dataclass_params__.frozen  # type: ignore[attr-defined]
        with pytest.raises(dataclasses.FrozenInstanceError):
            object.__setattr__  # sanity: FrozenInstanceError raised on setattr
            setattr(obj, next(iter(dataclasses.fields(obj))).name, None)


def test_factor_spec_validates_inputs() -> None:
    with pytest.raises(ValueError):
        spec.FactorSpec("x", 0, "reversal", "verified", "")  # bad sign
    with pytest.raises(ValueError):
        spec.FactorSpec("x", 1, "nope", "verified", "")  # bad block
    with pytest.raises(ValueError):
        spec.FactorSpec("x", 1, "reversal", "nope", "")  # bad grade


def test_module_has_no_forbidden_backend_imports() -> None:
    # Research-domain isolation redline: no backend.{llm,agents,mirofish,risk}.
    src = Path(spec.__file__).read_text(encoding="utf-8")
    forbidden = ("backend.llm", "backend.agents", "backend.mirofish", "backend.risk")
    for token in forbidden:
        assert token not in src
