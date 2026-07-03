"""Frozen, pre-declared *fixed prior spec* for the D1 dividend-low-vol defensive ranker.

Single source of truth for candidate D1 (DS defensive-selection line). Per
``docs/research/defensive-candidate-D1-dividend-lowvol-core-2026-07-03.md`` and the
synthesis (``defensive-selection-research-synthesis-2026-07-03.md``), the ENTIRE
factor set + every committed prior sign + the block-weight rule + the universe
filters + the two containers + the four-gate calibration are pre-declared and
hashed **before any evaluation touches returns**. Once :func:`spec_hash` is stamped
into the trial ledger, nothing here may change for the remainder of the D1 dev cut —
that immutability defends the cut from the round-1..4 mining debt (no grid search,
no best-of, no inclusion screen, no in-sample sign/weight fit).

D1 hypothesis (H1): directly selecting inherently defensive names (low realized
volatility + sustainable high dividend yield + quality-safety) in a ≤5 container over
a monthly (20d) horizon achieves bear/crash non-negative + materially lower drawdown
than the pure-reversal book on train_val — even at lower bull-market beta.

Design invariants (asserted by ``tests/factor_research/test_defensive_D1_spec.py``):
  * pure constants — zero IO, zero ``backend.{llm,agents,mirofish,risk}`` import;
  * exactly 7 ranker factors across 4 blocks with the committed prior signs;
  * block weights sum to 1.0 (low_vol 0.35 / dividend 0.35 / quality_safety 0.20 /
    tail 0.10 — committed from provenance confidence, NOT in-sample fit);
  * :data:`CONTAINERS` matches ``slot_frontier.FRONTIER`` field-for-field (eq_5
    science gate + buf40_5 deployment gate);
  * :func:`spec_hash` is deterministic (same input → same SHA256).

Grade convention (honest): D1 has NO from-scratch QGR-3 survivor — every defensive
factor is a literature / monthly-horizon prior, so all are graded ``"cautious"``.
The block weights are committed directly from the D1 provenance table (low-vol HIGH +
dividend HIGH anchors → 0.35 each; quality MED → 0.20; tail-beta MED tie-breaker →
0.10), documented below; they are never derived from an in-sample IC read.
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
    low leg) fixed *before* evaluation. ``grade`` is provenance confidence
    (``"verified"`` = from-scratch survivor, ``"cautious"`` = literature /
    monthly-horizon prior); for D1 all factors are ``"cautious"``. ``source`` is a
    documentation pointer only (excluded from :func:`spec_hash` so prose edits
    cannot perturb the scientific hash).
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


BLOCK_NAMES: tuple[str, ...] = ("low_vol", "dividend", "quality_safety", "tail")

_LOWVOL_SRC = (
    "Robeco/Blitz-Hanauer-van Vliet Volatility Effect China 2021; "
    "factor_lib.return_volatility"
)
_DIV_SRC = "S&P China low-vol high-div 50; SSE H50040; daily_basic.dv_ratio PIT"
_QUALITY_SRC = "Asness-Frazzini-Pedersen QMJ 2019 + AF-003; fundamentals_pit ann_date<d"
_TAIL_SRC = (
    "Frazzini-Pedersen BAB; Tail beta China Applied Econ 2019; rolling OLS vs CSI300"
)

RANKER_FACTORS: tuple[FactorSpec, ...] = (
    # Low-volatility block (anchor; low realized vol better).
    FactorSpec("vol_20d", -1, "low_vol", "cautious", _LOWVOL_SRC),
    # Dividend block (anchor; higher sustainable yield better — cheapness support).
    FactorSpec("dv_ratio", +1, "dividend", "cautious", _DIV_SRC),
    # Quality-safety block (ROE/GPM up, accruals down — safety > pure profitability).
    FactorSpec("roe", +1, "quality_safety", "cautious", _QUALITY_SRC),
    FactorSpec("gpm", +1, "quality_safety", "cautious", _QUALITY_SRC),
    FactorSpec("accr", -1, "quality_safety", "cautious", "factor_lib.accruals_sloan"),
    # Tail block (small-weight tie-breaker; low beta / low tail-beta better).
    FactorSpec("beta", -1, "tail", "cautious", _TAIL_SRC),
    FactorSpec("tail_beta", -1, "tail", "cautious", _TAIL_SRC),
)

# --------------------------------------------------------------------------- #
# Block weights (committed from provenance confidence, NOT fit).               #
# low_vol 0.35 / dividend 0.35 (HIGH-confidence anchors) / quality_safety 0.20 #
# (MED) / tail 0.10 (MED tie-breaker). Frozen before evaluation.               #
# --------------------------------------------------------------------------- #

BLOCK_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("low_vol", 0.35),
    ("dividend", 0.35),
    ("quality_safety", 0.20),
    ("tail", 0.10),
)


# --------------------------------------------------------------------------- #
# Beta / tail-beta factor definition (committed; bound into the spec hash so a #
# window / tail-quantile / proxy change is a spec change — codex R1 P1).       #
# ``test_defensive_d1_spec`` asserts these equal ``beta_factor``'s defaults so #
# the two never silently drift.                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BetaParams:
    """The committed scientific definition of the ``beta`` / ``tail_beta`` factors.

    ``market_proxy`` is the CSI300 ETF whose ``fund_daily`` closes are the market
    return series. ``window`` / ``min_obs`` / ``tail_quantile`` / ``tail_min_obs``
    are the rolling-OLS parameters — pinned here (not only in ``beta_factor``) so
    changing them changes :func:`spec_hash` (they alter the factor values).
    """

    market_proxy: str
    window: int
    min_obs: int
    tail_quantile: float
    tail_min_obs: int


BETA_PARAMS: BetaParams = BetaParams(
    market_proxy="510300.SH",
    window=60,
    min_obs=40,
    tail_quantile=0.30,
    tail_min_obs=12,
)


# --------------------------------------------------------------------------- #
# Universe filters (binary include/exclude; NOT ranked; committed thresholds). #
# Hard exclusions applied BEFORE ranking (D1 doc §2).                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UniverseFilters:
    """Pre-declared binary universe filters (never enter the ranking).

    ``max_lottery_exclude_quantile`` — exclude the top decile of ``max_20d``
    (lottery/MAX names, the fattest left tail; ideal RMAX 涨跌停-corrected).
    ``roe_floor`` / ``gpm_floor_quantile`` — the value-trap quality floor (drop
    ROE≤0 and the bottom GPM decile). ``dividend_min_percentile`` — anti-crowding
    valuation anchor: the dividend leg requires at-least-median dividend-yield
    percentile (a bid-up crowded name has a LOWER yield → excluded). ``exclusion_
    four_piece`` = ST/科创/北交/可转债; ``at_limit_unfillable_removed`` = 涨跌停
    unfillable; ``bottom_30pct_size_cut`` = Liu-Stambaugh-Yuan shell exclusion
    (drop smallest 30% by circ_mv), applied in the panel builder / neutralization.
    """

    max_lottery_exclude_quantile: float
    roe_floor: float
    gpm_floor_quantile: float
    dividend_min_percentile: float
    exclusion_four_piece_applied: bool
    at_limit_unfillable_removed: bool
    bottom_30pct_size_cut_applied: bool


UNIVERSE_FILTERS: UniverseFilters = UniverseFilters(
    max_lottery_exclude_quantile=0.90,
    roe_floor=0.0,
    gpm_floor_quantile=0.10,
    dividend_min_percentile=0.50,
    exclusion_four_piece_applied=True,
    at_limit_unfillable_removed=True,
    bottom_30pct_size_cut_applied=True,
)


# --------------------------------------------------------------------------- #
# Containers (dual: eq_5 science gate + buf40_5 deployment gate).              #
# MUST be field-identical to slot_frontier.FRONTIER same-labelled configs.     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ContainerSpec:
    """One ≤5-slot container: label + slot count + per-name cap percent.

    ``buf40_5`` = 5 slots × 8% cap ≈ 40% gross / 60% cash buffer (satisfies the
    P-E ≥40% cash floor). Anchored to ``slot_frontier.FRONTIER`` on the SCIENTIFIC
    fields only (``label`` / ``slots`` / ``cap_percent``); the frontier's free-text
    ``note`` is prose and deliberately not mirrored here (nor hashed).
    """

    label: str
    slots: int
    cap_percent: int


CONTAINERS: tuple[ContainerSpec, ...] = (
    ContainerSpec("eq_5", 5, 100),
    ContainerSpec("buf40_5", 5, 8),
)


# --------------------------------------------------------------------------- #
# Horizon + four-gate calibration (NOT relaxed) + CPCV purge/embargo.          #
# --------------------------------------------------------------------------- #

HORIZON: int = 20
"""Monthly rebalance horizon (D1 doc §3: defensive factors are slow → 20d)."""


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
    cpcv_purge_embargo=HORIZON - 1,  # = 19
    deflation_n=2417,  # non-zeroing ledger floor pre-D1; D1 appends + recomputes
)


# --------------------------------------------------------------------------- #
# Spec hash — canonical JSON (sort_keys) → SHA256; deterministic.             #
# --------------------------------------------------------------------------- #


def _canonical_payload() -> dict[str, object]:
    """The committed scientific content (prose ``source`` fields excluded)."""
    return {
        "candidate": "D1_dividend_lowvol_defensive_core",
        "ranker_factors": [
            {"name": f.name, "sign": f.sign, "block": f.block, "grade": f.grade}
            for f in RANKER_FACTORS
        ],
        "block_weights": [list(bw) for bw in BLOCK_WEIGHTS],
        "beta_params": asdict(BETA_PARAMS),
        "universe_filters": asdict(UNIVERSE_FILTERS),
        "containers": [asdict(c) for c in CONTAINERS],
        "horizon": HORIZON,
        "gate_calibration": asdict(GATE_CALIBRATION),
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


def factor_sign(factor: str) -> int:
    """The committed prior sign (±1) of ``factor`` (raises for unknown)."""
    for f in RANKER_FACTORS:
        if f.name == factor:
            return f.sign
    raise KeyError(factor)


def factors_in_block(block: str) -> tuple[str, ...]:
    """The ranker factor names belonging to ``block`` (ordered as declared)."""
    if block not in BLOCK_NAMES:
        raise KeyError(block)
    return tuple(f.name for f in RANKER_FACTORS if f.block == block)


__all__ = [
    "BETA_PARAMS",
    "BLOCK_NAMES",
    "BLOCK_WEIGHTS",
    "CONTAINERS",
    "GATE_CALIBRATION",
    "HORIZON",
    "RANKER_FACTORS",
    "UNIVERSE_FILTERS",
    "BetaParams",
    "ContainerSpec",
    "FactorSpec",
    "GateCalibration",
    "UniverseFilters",
    "block_weight",
    "factor_sign",
    "factors_in_block",
    "spec_hash",
]
