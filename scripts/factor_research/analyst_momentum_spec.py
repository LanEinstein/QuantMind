"""Frozen, pre-declared spec for the analyst-revision-momentum ranking candidate.

Ranking candidate #2 of the DS defensive-selection line (owner-directed 2026-07-04
after DS-D2 branch (c) refuted the reversal ranking edge at the book layer). The
question, under the SAME dev-selection protocol as D2 (amendment 2026-07-04): does an
information-flow ranking factor — the *change* in broker analyst consensus — earn a
book-layer edge where the price-derived reversal factor did NOT? This is the one
mechanism branch (c) does not cover (reversal is price/volume; analyst revision is
information flow, orthogonal by construction — Lv 2025, McNichols-O'Brien).

Anti-p-hacking anchor (REUSE, do not re-select): the factor subset + committed prior
signs are taken VERBATIM from the round-4 R4-4 orthogonal analyst block frozen in
``alpha_pivot_spec`` (the analyst block) — ``{np_rev, rev_diff, cover_chg}``,
all +1 (attractive-high). ``eps_rev`` (collinear with np_rev), ``rating_chg``, ``disp``,
and ``tp_impl`` (``tp`` = 利润总额 ambiguity) were PRE-DECLARED dropped in round 4; they
are NOT reconsidered here. The factor construction (PIT windows) is
``analyst_revision_pit`` verbatim (report_date<d gate, FY-aligned same-year revision,
cross-broker median, n≥3 for
breadth/coverage). Nothing is fit in-sample.

Owner-confirmed framing (2026-07-04): FULL universe (standard exclusions), 20d MONTHLY
horizon (analyst revision is a slow 1-6 month effect — Lv 2025; round-4 IC screened at
10/20d — NOT the 5d reversal cadence), single committed horizon (no sweep).

Design invariants (asserted by ``tests/factor_research/test_analyst_momentum_spec.py``):
  * pure constants — zero IO, zero ``backend.{llm,agents,mirofish,risk}`` import;
  * :data:`RANKER_FACTORS` (names + signs) == the ``alpha_pivot_spec`` analyst block
    (drift guard — the committed subset is reused, not re-selected);
  * factor names ⊆ ``analyst_revision_pit.ANALYST_FACTOR_NAMES`` and their attractive
    direction matches ``factor_lib.R4_FACTORS`` (no silent sign flip);
  * analyst PIT windows == ``analyst_revision_pit`` module defaults (bound into the hash
    so a window change is a spec change);
  * :data:`CONTAINERS` matches ``slot_frontier.FRONTIER`` field-for-field;
  * :data:`DSR_ROLE` == ``"disclosure_only"`` (amendment: DSR pre-declared to FAIL);
  * :func:`spec_hash` is deterministic (same input → same SHA256).

Once :func:`spec_hash` is stamped into the trial ledger, nothing here may change for the
remainder of the cut.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

CANDIDATE: str = "analyst_revision_momentum"


@dataclass(frozen=True)
class FactorSpec:
    """One pre-declared ranker factor + its committed prior sign and provenance.

    ``sign`` is the a-priori direction (+1 long the high leg) fixed *before* evaluation;
    reused verbatim from the round-4 R4-4 orthogonal subset (never fit). ``source`` is a
    documentation pointer only (excluded from :func:`spec_hash`).
    """

    name: str
    sign: int
    source: str

    def __post_init__(self) -> None:
        if self.sign not in (-1, 1):
            raise ValueError(f"sign must be ±1, got {self.sign!r} for {self.name}")


_SRC = "round-4 R4-4 orthogonal subset; analyst_revision_pit report_date<d (frozen)"

# Committed analyst subset — REUSED verbatim from alpha_pivot_spec's analyst block.
RANKER_FACTORS: tuple[FactorSpec, ...] = (
    FactorSpec("np_rev", +1, _SRC),  # FY1 net-profit consensus revision (magnitude)
    FactorSpec("rev_diff", +1, _SRC),  # per-broker net up/down diffusion (breadth, n≥3)
    FactorSpec("cover_chg", +1, _SRC),  # log change in FY1-covering broker count
)

# Equal weight within the single analyst block (the composite is the mean of the signed
# per-date z-scores; no per-factor weight is fit — the committed subset carries no
# in-sample weighting, so equal weight is the neutral pre-declared choice).
FACTOR_WEIGHT_RULE: str = "equal_weight_signed_zscore_mean"


# --------------------------------------------------------------------------- #
# Analyst PIT windows (committed = analyst_revision_pit defaults; bound to hash #
# so a staleness / lookback / level change is a spec change).                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AnalystWindows:
    """The committed report_rc aggregation windows (analyst_revision_pit defaults)."""

    staleness_days: int
    lookback_days: int
    level_window_days: int


ANALYST_WINDOWS: AnalystWindows = AnalystWindows(
    staleness_days=90,
    lookback_days=90,
    level_window_days=180,
)


# --------------------------------------------------------------------------- #
# Horizon + cadence — 20d monthly (owner-confirmed; slow info-flow factor).    #
# --------------------------------------------------------------------------- #

HORIZON: int = 20
REBALANCE_FREQ: int = 20


# --------------------------------------------------------------------------- #
# Containers (dual: eq_5 science gate + buf40_5 deployment gate).              #
# MUST be field-identical to slot_frontier.FRONTIER same-labelled configs.     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ContainerSpec:
    """One ≤5-slot container: label + slot count + per-name cap percent."""

    label: str
    slots: int
    cap_percent: int


CONTAINERS: tuple[ContainerSpec, ...] = (
    ContainerSpec("eq_5", 5, 100),
    ContainerSpec("buf40_5", 5, 8),  # ≈40% gross / 60% cash buffer (P-E floor)
)


# --------------------------------------------------------------------------- #
# Neutralization + placebo config (committed).                                #
# --------------------------------------------------------------------------- #

NEUTRALIZATION: tuple[str, ...] = (
    "industry_sw_l1",
    "log_circ_mv",
    "winsor_0.01",
    "min_obs_20",
)

PLACEBO_SEED: int = 20260704
PLACEBO_TOP_N: int = 5
BEATS_PLACEBO_T: float = 2.0
"""Strict one-sided paired-t hurdle (the selection main gate; not the lenient t>1)."""


# --------------------------------------------------------------------------- #
# Four-gate calibration (NOT relaxed) — DSR pre-declared to FAIL, disclosed.   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GateCalibration:
    """Anti-overfit four-gate thresholds + CPCV parameters (committed, NOT relaxed)."""

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
    deflation_n=2419,  # non-zeroing ledger floor pre-analyst-momentum (post ds.d2)
)

DSR_ROLE: str = "disclosure_only"
"""Amendment 2026-07-04: DSR/SPA/RW pre-declared to FAIL, computed + disclosed +
ledger-appended, but NOT promotion gates."""

PROMOTION_GATES: tuple[str, ...] = (
    "beats_own_random_placebo_joint_t2",
    "bear_cum_nonneg",
    "crash_slices_nonneg",
    "net_pnl_positive",
)

AMENDMENT: str = (
    "qgr-certification-rearch-amendment-2026-07-04-"
    "dev-selection-forward-certification.md"
)


# --------------------------------------------------------------------------- #
# Spec hash — canonical JSON (sort_keys) → SHA256; deterministic.             #
# --------------------------------------------------------------------------- #


def _canonical_payload() -> dict[str, object]:
    """The committed scientific content (prose ``source`` fields excluded)."""
    return {
        "candidate": CANDIDATE,
        "ranker_factors": [{"name": f.name, "sign": f.sign} for f in RANKER_FACTORS],
        "factor_weight_rule": FACTOR_WEIGHT_RULE,
        "analyst_windows": asdict(ANALYST_WINDOWS),
        "horizon": HORIZON,
        "rebalance_freq": REBALANCE_FREQ,
        "containers": [asdict(c) for c in CONTAINERS],
        "neutralization": list(NEUTRALIZATION),
        "placebo": {
            "seed": PLACEBO_SEED,
            "top_n": PLACEBO_TOP_N,
            "beats_placebo_t": BEATS_PLACEBO_T,
        },
        "gate_calibration": asdict(GATE_CALIBRATION),
        "dsr_role": DSR_ROLE,
        "promotion_gates": list(PROMOTION_GATES),
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


def factor_sign(factor: str) -> int:
    """The committed prior sign (±1) of ``factor`` (raises for unknown)."""
    for f in RANKER_FACTORS:
        if f.name == factor:
            return f.sign
    raise KeyError(factor)


__all__ = [
    "AMENDMENT",
    "ANALYST_WINDOWS",
    "BEATS_PLACEBO_T",
    "CANDIDATE",
    "CONTAINERS",
    "DSR_ROLE",
    "FACTOR_WEIGHT_RULE",
    "GATE_CALIBRATION",
    "HORIZON",
    "NEUTRALIZATION",
    "PLACEBO_SEED",
    "PLACEBO_TOP_N",
    "PROMOTION_GATES",
    "RANKER_FACTORS",
    "REBALANCE_FREQ",
    "AnalystWindows",
    "ContainerSpec",
    "FactorSpec",
    "GateCalibration",
    "factor_sign",
    "spec_hash",
]
