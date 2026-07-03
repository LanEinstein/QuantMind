"""Frozen-spec invariants for :mod:`scripts.factor_research.defensive_D1_spec`.

The D1 fixed prior spec is committed and hashed *before* any evaluation touches
returns; these tests pin every committed value so an accidental edit after the
ledger stamp is caught immediately. They also assert the field-exact anchor to
``slot_frontier.FRONTIER`` and the purity of the module (no backend import).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scripts.factor_research import beta_factor as bf
from scripts.factor_research import defensive_d1_spec as spec
from scripts.factor_research.slot_frontier import FRONTIER

# The single source of truth for the committed D1 factor set (candidate doc §2).
_EXPECTED_FACTORS: tuple[tuple[str, int, str, str], ...] = (
    ("vol_20d", -1, "low_vol", "cautious"),
    ("dv_ratio", +1, "dividend", "cautious"),
    ("roe", +1, "quality_safety", "cautious"),
    ("gpm", +1, "quality_safety", "cautious"),
    ("accr", -1, "quality_safety", "cautious"),
    ("beta", -1, "tail", "cautious"),
    ("tail_beta", -1, "tail", "cautious"),
)


def test_ranker_factors_match_spec_exactly() -> None:
    got = tuple((f.name, f.sign, f.block, f.grade) for f in spec.RANKER_FACTORS)
    assert got == _EXPECTED_FACTORS
    assert len(spec.RANKER_FACTORS) == 7


def test_block_composition() -> None:
    assert spec.factors_in_block("low_vol") == ("vol_20d",)
    assert spec.factors_in_block("dividend") == ("dv_ratio",)
    assert spec.factors_in_block("quality_safety") == ("roe", "gpm", "accr")
    assert spec.factors_in_block("tail") == ("beta", "tail_beta")
    with pytest.raises(KeyError):
        spec.factors_in_block("nope")


def test_block_weights_committed_and_sum_to_one() -> None:
    assert spec.BLOCK_WEIGHTS == (
        ("low_vol", 0.35),
        ("dividend", 0.35),
        ("quality_safety", 0.20),
        ("tail", 0.10),
    )
    assert sum(w for _, w in spec.BLOCK_WEIGHTS) == pytest.approx(1.0)
    assert spec.block_weight("low_vol") == 0.35
    assert spec.block_weight("tail") == 0.10
    with pytest.raises(KeyError):
        spec.block_weight("nope")


def test_every_block_has_weight_and_factors() -> None:
    weighted = {name for name, _ in spec.BLOCK_WEIGHTS}
    assert weighted == set(spec.BLOCK_NAMES)
    for block in spec.BLOCK_NAMES:
        assert any(f.block == block for f in spec.RANKER_FACTORS)


def test_factor_sign_lookup() -> None:
    assert spec.factor_sign("vol_20d") == -1
    assert spec.factor_sign("dv_ratio") == +1
    assert spec.factor_sign("accr") == -1
    assert spec.factor_sign("tail_beta") == -1
    with pytest.raises(KeyError):
        spec.factor_sign("nope")


def test_universe_filters_committed_thresholds() -> None:
    u = spec.UNIVERSE_FILTERS
    assert u.max_lottery_exclude_quantile == 0.90
    assert u.roe_floor == 0.0
    assert u.gpm_floor_quantile == 0.10
    assert u.dividend_min_percentile == 0.50
    assert u.exclusion_four_piece_applied is True
    assert u.at_limit_unfillable_removed is True
    assert u.bottom_30pct_size_cut_applied is True


def test_containers_field_identical_to_slot_frontier() -> None:
    frontier_by_label = {c.label: c for c in FRONTIER}
    for container in spec.CONTAINERS:
        anchor = frontier_by_label[container.label]
        assert container.slots == anchor.slots, container.label
        assert container.cap_percent == anchor.cap_percent, container.label
    assert tuple(c.label for c in spec.CONTAINERS) == ("eq_5", "buf40_5")
    buf = frontier_by_label["buf40_5"]
    assert buf.slots * buf.cap_percent == 40  # ≈40% gross / 60% cash buffer


def test_horizon_is_monthly() -> None:
    assert spec.HORIZON == 20


def test_beta_params_committed_and_bind_factor_definition() -> None:
    # Committed beta definition (codex R1 P1: bound into the hash so a window /
    # tail-quantile change is a spec change).
    p = spec.BETA_PARAMS
    assert p.market_proxy == "510300.SH"
    assert (p.window, p.min_obs) == (60, 40)
    assert (p.tail_quantile, p.tail_min_obs) == (0.30, 12)
    # ... and they must NOT silently drift from beta_factor's own defaults.
    assert p.window == bf.BETA_WINDOW
    assert p.min_obs == bf.BETA_MIN_OBS
    assert p.tail_quantile == bf.TAIL_QUANTILE
    assert p.tail_min_obs == bf.TAIL_MIN_OBS


def test_beta_definition_is_in_the_hash_payload() -> None:
    assert "beta_params" in spec._canonical_payload()


def test_gate_calibration_not_relaxed() -> None:
    g = spec.GATE_CALIBRATION
    assert g.dsr_threshold == 0.95
    assert g.pbo_threshold == 0.5
    assert g.spa_method == "hansen"
    assert g.rw_method == "romano_wolf"
    assert g.cpcv_purge_embargo == spec.HORIZON - 1 == 19
    assert g.deflation_n == 2417  # non-zeroing ledger floor pre-D1


def test_spec_hash_deterministic() -> None:
    assert spec.spec_hash() == spec.spec_hash()
    assert len(spec.spec_hash()) == 64
    int(spec.spec_hash(), 16)  # valid hex


def test_source_prose_excluded_from_hash() -> None:
    # The committed payload must NOT carry free-text source pointers (prose edits
    # cannot perturb the scientific hash).
    payload = spec._canonical_payload()
    dumped = str(payload)
    assert "Robeco" not in dumped
    assert "factor_lib" not in dumped
    assert "daily_basic" not in dumped


def test_all_constants_are_frozen() -> None:
    frozen_instances = [
        spec.RANKER_FACTORS[0],
        spec.UNIVERSE_FILTERS,
        spec.CONTAINERS[0],
        spec.GATE_CALIBRATION,
        spec.BETA_PARAMS,
    ]
    for obj in frozen_instances:
        assert dataclasses.is_dataclass(obj)
        assert obj.__dataclass_params__.frozen  # type: ignore[attr-defined]
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(obj, next(iter(dataclasses.fields(obj))).name, None)


def test_factor_spec_validates_inputs() -> None:
    with pytest.raises(ValueError):
        spec.FactorSpec("x", 0, "low_vol", "cautious", "")  # bad sign
    with pytest.raises(ValueError):
        spec.FactorSpec("x", 1, "nope", "cautious", "")  # bad block
    with pytest.raises(ValueError):
        spec.FactorSpec("x", 1, "low_vol", "bogus", "")  # bad grade


def test_module_is_pure_no_backend_import() -> None:
    source = Path(spec.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import backend",
        "from backend",
    ):
        assert forbidden not in source, forbidden
