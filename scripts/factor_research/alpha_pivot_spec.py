"""Frozen, pre-declared *fixed prior spec* for the alpha-pivot composite ranker.

This module is the single source of truth for the alpha-pivot cut (AP-0..AP-3).
Per the composite-spec outline (2026-06-27, codex 2-round converged) and the
implementation plan §3, the ENTIRE factor set + every committed prior sign +
the block-weight rule + the universe filters + the two containers + the four-gate
calibration + the return-blind power inputs are pre-declared and hashed **before
any evaluation touches returns**. Once :func:`spec_hash` is stamped into the
trial ledger (AP0-002), nothing here may change for the remainder of the cut —
that immutability is exactly what defends the cut from the round-1..4 mining debt
(no grid search, no best-of, no inclusion screen, no in-sample sign/weight fit).

Design invariants (asserted by ``tests/factor_research/test_alpha_pivot_spec.py``):
  * pure constants — zero IO, zero ``backend.{llm,agents,mirofish,risk}`` import;
  * exactly 10 ranker factors across 3 blocks with the committed prior signs;
  * block weights sum to 1.0 (reversal 0.5 / analyst 0.25 / quality 0.25);
  * :data:`CONTAINERS` matches ``slot_frontier.FRONTIER`` field-for-field (the
    byte-exact anchor to the disclosed frontier eq_5 / buf40_5 configs);
  * :func:`spec_hash` is deterministic (same input → same SHA256).

The IC-disclosure runner (AP-1) may READ these constants but MUST NEVER write
them: the "IC only discloses, never re-selects the spec" discipline (§6) lives in
the honesty of keeping the composition frozen here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

# --------------------------------------------------------------------------- #
# Ranker factors (enter the ≤5 ranking; committed prior signs, never fit).     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FactorSpec:
    """One pre-declared ranker factor + its committed prior sign and provenance.

    ``sign`` is the a-priori direction (``+1`` long the high leg, ``-1`` long the
    low leg) fixed *before* evaluation; ``grade`` is the provenance confidence
    (``"verified"`` ✅ = from-scratch survivor, ``"cautious"`` 🟡 = literature /
    monthly-horizon prior) that DERIVES the block weight — it is never an
    in-sample IC read. ``source`` is a documentation pointer only (excluded from
    :func:`spec_hash` so prose edits cannot perturb the scientific hash).
    """

    name: str
    sign: int
    block: str
    grade: str
    source: str

    def __post_init__(self) -> None:
        if self.sign not in (-1, 1):
            raise ValueError(f"sign must be ±1, got {self.sign!r} for {self.name}")
        if self.block not in BLOCK_NAMES:
            raise ValueError(f"unknown block {self.block!r} for {self.name}")
        if self.grade not in ("verified", "cautious"):
            raise ValueError(f"unknown grade {self.grade!r} for {self.name}")


BLOCK_NAMES: tuple[str, ...] = ("reversal", "analyst", "quality")

_ANALYST_SRC = "round-4 R4-4 orthogonal subset; analyst_revision_pit report_date<d"
_QUALITY_SRC = "round-1/2 + AF-003; fundamentals_pit ann_date<d vintage"

RANKER_FACTORS: tuple[FactorSpec, ...] = (
    # Reversal block (✅ verified — QGR-3 ⑦ from-scratch survivors; 5td-native).
    FactorSpec("rev_1d", -1, "reversal", "verified", "QGR-3 ⑦ survivor neut|t|4.4"),
    FactorSpec("max_5d", -1, "reversal", "verified", "QGR-3 ⑦ survivor neut|t|11.3"),
    FactorSpec("turn_spike", -1, "reversal", "verified", "QGR-3 ⑦ survivor neut|t|5.3"),
    # Analyst-revision block (🟡 cautious — round-4 R4-4 orthogonal subset;
    # report_date<d PIT; tp_impl pre-declared dropped: `tp`=利润总额 ambiguity).
    FactorSpec("np_rev", +1, "analyst", "cautious", _ANALYST_SRC),
    FactorSpec("rev_diff", +1, "analyst", "cautious", _ANALYST_SRC),
    FactorSpec("cover_chg", +1, "analyst", "cautious", _ANALYST_SRC),
    # Quality block (🟡 cautious — round-1/2 carry + AF-003; ann_date<d vintage).
    FactorSpec("roe", +1, "quality", "cautious", _QUALITY_SRC),
    FactorSpec("gpm", +1, "quality", "cautious", _QUALITY_SRC),
    FactorSpec("ep_ttm", +1, "quality", "cautious", _QUALITY_SRC),
    FactorSpec("accr", -1, "quality", "cautious", "factor_lib.accruals_sloan"),
)

# --------------------------------------------------------------------------- #
# Block weights (owner decision #1: horizon/confidence weighted, NOT fit).     #
# provenance grade points: verified=2, cautious=1 → normalized per block.      #
# reversal 0.5 / analyst 0.25 / quality 0.25 (committed; frozen before eval).  #
# --------------------------------------------------------------------------- #

BLOCK_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("reversal", 0.5),
    ("analyst", 0.25),
    ("quality", 0.25),
)


# --------------------------------------------------------------------------- #
# Universe filters (binary include/exclude; NOT ranked; committed thresholds). #
# Bottom-confirmation core-4 (QGR-3 ⑧, verbatim) as a universe-health gate;    #
# cyq cost band pre-declared dropped (QGR-3 ⑧ proved it non-load-bearing).     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UniverseFilters:
    """Pre-declared binary universe filters (never enter the ranking).

    ``bottom_confirmation_core`` mirrors ``bottom_confirmation.CORE_CONDITION_NAMES``
    verbatim (the test asserts equality). The remaining booleans record the
    committed inclusion of the system's existing hard exclusions and the removal
    of at-limit unfillable names (the reversal loser-leg 跌停 飞刀).
    """

    bottom_confirmation_core: tuple[str, ...]
    cyq_cost_band_included: bool
    exclusion_four_piece_applied: bool
    at_limit_unfillable_removed: bool


UNIVERSE_FILTERS: UniverseFilters = UniverseFilters(
    bottom_confirmation_core=(
        "vol_dryup",
        "no_breakdown",
        "no_distress",
        "quality_floor",
    ),
    cyq_cost_band_included=False,
    exclusion_four_piece_applied=True,
    at_limit_unfillable_removed=True,
)


# --------------------------------------------------------------------------- #
# Containers (dual: eq_5 science gate + buf40_5 deployment gate).              #
# MUST be field-identical to slot_frontier.FRONTIER same-labelled configs.     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ContainerSpec:
    """One ≤5-slot container: label + slot count + per-name cap percent.

    ``buf40_5`` = 5 slots × 8% cap ≈ 40% gross / 60% cash buffer (satisfies the
    P-E ≥40% cash floor). Byte-anchored to ``slot_frontier.FRONTIER``.
    """

    label: str
    slots: int
    cap_percent: int


CONTAINERS: tuple[ContainerSpec, ...] = (
    ContainerSpec("eq_5", 5, 100),
    ContainerSpec("buf40_5", 5, 8),
)


# --------------------------------------------------------------------------- #
# Four-gate calibration (NOT relaxed) + CPCV purge/embargo.                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GateCalibration:
    """Anti-overfit four-gate thresholds + CPCV parameters (committed)."""

    dsr_threshold: float
    pbo_threshold: float
    spa_method: str
    rw_method: str
    cpcv_purge_embargo: int
    deflation_n: int


GATE_CALIBRATION: GateCalibration = GateCalibration(
    dsr_threshold=0.95,
    pbo_threshold=0.5,
    spa_method="hansen",
    rw_method="romano_wolf",
    cpcv_purge_embargo=4,  # = horizon - 1
    deflation_n=2417,  # legacy effective floor + AP appended effective (AP0-002)
)


# --------------------------------------------------------------------------- #
# Return-blind power inputs (AP-0.5; owner decision #2 K=2 + codex R2-1).      #
# All pre-declared: normal moments, HAC conservative-upper-bound rule, T =     #
# disclosed pure-reversal ONC effective N, K, and the SR_ref SOURCE (read at   #
# AP-0.5 from disclosed frontier output — zero new peek, no number smuggled).  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PowerInputs:
    """Pre-declared inputs for the return-blind DSR back-solve (AP-0.5).

    ``t_onc_effective`` is the disclosed structural effective sample size of the
    pure-reversal eq_5 rebalance-return series (frontier output; a disclosed
    quantity, not a new read of the A4 composite). AP-0.5 re-asserts it against
    the disclosed series; a mismatch is surfaced, never silently overwritten.
    ``sr_ref_source`` names WHERE AP-0.5 reads SR_ref — the value itself is not
    hard-coded here (it is disclosed, derived at AP-0.5, and hashing a measured
    float would make the spec non-deterministic).
    """

    skew: float
    kurtosis: float
    hac_lag: int
    hac_rule: str
    horizon: int
    rebalance_freq: int
    t_onc_effective: int
    deflation_n: int
    k_power: float
    sr_ref_source: str


POWER_INPUTS: PowerInputs = PowerInputs(
    skew=0.0,
    kurtosis=3.0,
    hac_lag=4,  # = horizon - 1
    hac_rule="conservative_upper_bound",  # honest_gates.hac_variance_inflation
    horizon=5,
    rebalance_freq=5,
    t_onc_effective=497,  # disclosed pure-reversal eq_5 ONC effective N (frontier)
    deflation_n=2417,
    k_power=2.0,  # go iff SR_req <= K * SR_ref (owner decision #2)
    sr_ref_source="frontier disclosed pure-reversal eq_5 annualized SR (zero new peek)",
)


# --------------------------------------------------------------------------- #
# Spec hash — canonical JSON (sort_keys) → SHA256; deterministic.             #
# --------------------------------------------------------------------------- #


def _canonical_payload() -> dict[str, object]:
    """The committed scientific content (prose ``source`` fields excluded).

    Everything that defines the *fixed prior spec* — factor names/signs/blocks/
    grades, block weights, filters, containers, gate thresholds, power inputs —
    goes into the hash; free-text provenance pointers do not (so documentation
    wording never perturbs the scientific hash).
    """
    return {
        "ranker_factors": [
            {"name": f.name, "sign": f.sign, "block": f.block, "grade": f.grade}
            for f in RANKER_FACTORS
        ],
        "block_weights": [list(bw) for bw in BLOCK_WEIGHTS],
        "universe_filters": {
            "bottom_confirmation_core": list(UNIVERSE_FILTERS.bottom_confirmation_core),
            "cyq_cost_band_included": UNIVERSE_FILTERS.cyq_cost_band_included,
            "exclusion_four_piece": UNIVERSE_FILTERS.exclusion_four_piece_applied,
            "at_limit_unfillable_removed": UNIVERSE_FILTERS.at_limit_unfillable_removed,
        },
        "containers": [asdict(c) for c in CONTAINERS],
        "gate_calibration": asdict(GATE_CALIBRATION),
        "power_inputs": asdict(POWER_INPUTS),
    }


def spec_hash() -> str:
    """Deterministic SHA256 over the canonical committed spec payload."""
    payload = json.dumps(
        _canonical_payload(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def block_weight(block: str) -> float:
    """The committed weight of ``block`` (raises for an unknown block)."""
    for name, weight in BLOCK_WEIGHTS:
        if name == block:
            return weight
    raise KeyError(block)


__all__ = [
    "BLOCK_NAMES",
    "BLOCK_WEIGHTS",
    "CONTAINERS",
    "GATE_CALIBRATION",
    "POWER_INPUTS",
    "RANKER_FACTORS",
    "UNIVERSE_FILTERS",
    "ContainerSpec",
    "FactorSpec",
    "GateCalibration",
    "PowerInputs",
    "UniverseFilters",
    "block_weight",
    "spec_hash",
]
