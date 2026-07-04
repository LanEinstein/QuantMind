"""Frozen, pre-declared spec for candidate D2 — reversal ranking on a defensive book.

Single source of truth for candidate D2 (DS defensive-selection line). Per
``docs/research/ds-d2-implementation-plan-2026-07-04.md`` (the authoritative build
sheet) and the amendment
``qgr-certification-rearch-amendment-2026-07-04-dev-selection-forward-certification.md``,
D2 asks a *re-characterised* question: **does the ≤5-slot reversal RANKING layer earn
its place stacked on the defensive sleeve?** — NOT "can it clear the four gates" (it
cannot; the DSR≥0.95 in-sample certification was proven arithmetically unreachable for
every cross-sectional ranking candidate, so DSR is pre-declared to FAIL and disclosed,
not gated).

D2 differs from A0 (the pure-reversal book slot_frontier already ran) by exactly ONE
change: a binary universe **filter** applied before ranking (keep the lower-volatility,
non-lottery, dividend-or-quality names). The ranker itself is NOT re-implemented — the
ablation calls ``exit_veto_panel.build_ranker_table`` so the reversal score is
byte-identical to A0 by REUSE, not by copy. That byte-identity lets the same harness run
A0 (a byte anchor to ``slot_frontier_result.json``) and D2 side by side.

Design invariants (asserted by ``tests/factor_research/test_defensive_d2_spec.py``):
  * pure constants — zero IO, zero ``backend.{llm,agents,mirofish,risk}`` import;
  * :data:`RANKER_FACTORS` == ``exit_veto_panel.RANKER_FACTORS`` (drift guard — the
    ranker is reused, not duplicated);
  * :data:`CONTAINERS` matches ``slot_frontier.FRONTIER`` field-for-field (eq_5 science
    gate + buf40_5 deployment gate) so the containers are A0-parity;
  * :data:`HORIZON` / :data:`REBALANCE_FREQ` == 5 (A0 / frontier cadence — reversal is a
    fast leg, not the D1 monthly defensive leg);
  * :data:`DSR_ROLE` == ``"disclosure_only"`` (amendment: DSR pre-declared to FAIL);
  * :func:`spec_hash` is deterministic (same input → same SHA256).

Once :func:`spec_hash` is stamped into the trial ledger, nothing here may change for the
remainder of the D2 dev cut — that immutability defends the cut from the round-1..4
mining debt (no grid search, no best-of, no in-sample fit). The universe-filter
thresholds are committed directly from the D1 provenance table + the candidate doc
(``defensive-candidate-D2-reversal-on-defensive-universe-2026-07-03.md``), never derived
from an in-sample read.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

CANDIDATE: str = "D2_reversal_on_defensive_universe"

# --------------------------------------------------------------------------- #
# Ranker — byte-identical to A0 by REUSE (never re-implemented here). The       #
# ablation calls ``exit_veto_panel.build_ranker_table``; this module only       #
# MIRRORS the factor tuple so a drift between the two is caught by a test.       #
# --------------------------------------------------------------------------- #

RANKER_FACTORS: tuple[str, ...] = ("rev_1d", "max_5d", "turn_spike")
"""QGR-3 fast-leg reversal survivors — MUST equal ``exit_veto_panel.RANKER_FACTORS``."""

RANKER_IMPLEMENTATION: str = "exit_veto_panel.build_ranker_table"
"""Documentation pointer: the ranker is reused (byte-identity), not copied here."""

CROWD_FACTOR: str = "ideal_amplitude_20d"
"""Crowding axis carried through neutralization so the reused ``build_ranker_table``
(which requires its ``_neut`` column) runs unchanged — A0 parity."""


# --------------------------------------------------------------------------- #
# Universe filter — D2's ONLY change vs A0 (binary include/exclude, NOT ranked). #
# Applied per date on the RAW columns BEFORE the reused ranker runs.            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class D2UniverseFilter:
    """Pre-declared binary defensive universe filter (never enters the ranking).

    A name is KEPT iff it clears BOTH always-on exclusions AND at least one of the two
    defensive-quality inclusion branches:

    * ``vol_keep_max_quantile`` — keep only ``vol_20d`` at/below its per-date quantile
      (drop the highest-volatility tail); a missing ``vol_20d`` is dropped.
    * ``max20d_lottery_exclude_quantile`` — drop the top ``max_20d`` decile (lottery /
      fattest left tail); a missing ``max_20d`` is dropped.
    * branch one — ``dividend_min_percentile``: ``dv_ratio`` at/above its per-date
      percentile (sustainable-dividend anchor); a missing ``dv_ratio`` fails it.
    * branch two — ``roe_floor`` / ``gpm_floor_quantile``: ``roe`` > floor AND ``gpm``
      above its bottom-decile quantile (quality-safety anchor); a missing ``roe`` or
      ``gpm`` fails it.

    Committed missing rule (fail-closed): ``vol_20d`` / ``max_20d`` missing → dropped;
    both defensive branches failing (incl. via a missing input) → dropped.
    """

    vol_keep_max_quantile: float
    max20d_lottery_exclude_quantile: float
    dividend_min_percentile: float
    roe_floor: float
    gpm_floor_quantile: float


D2_UNIVERSE_FILTER: D2UniverseFilter = D2UniverseFilter(
    vol_keep_max_quantile=0.60,
    max20d_lottery_exclude_quantile=0.90,
    dividend_min_percentile=0.50,
    roe_floor=0.0,
    gpm_floor_quantile=0.10,
)


# --------------------------------------------------------------------------- #
# Horizon + rebalance cadence — A0 / slot_frontier parity (fast reversal leg). #
# --------------------------------------------------------------------------- #

HORIZON: int = 5
"""Weekly rebalance horizon — A0 parity (``slot_frontier.HORIZON``); fast leg."""

REBALANCE_FREQ: int = 5
"""Weekly rotation cadence — A0 parity (``slot_frontier.REBALANCE_FREQ``)."""


# --------------------------------------------------------------------------- #
# Containers (dual: eq_5 science gate + buf40_5 deployment gate).              #
# MUST be field-identical to slot_frontier.FRONTIER same-labelled configs.     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ContainerSpec:
    """One ≤5-slot container: label + slot count + per-name cap percent.

    ``buf40_5`` = 5 slots × 8% cap ≈ 40% gross / 60% cash buffer (the P-E ≥40% cash
    floor). Anchored to ``slot_frontier.FRONTIER`` on the scientific fields only.
    """

    label: str
    slots: int
    cap_percent: int


CONTAINERS: tuple[ContainerSpec, ...] = (
    ContainerSpec("eq_5", 5, 100),
    ContainerSpec("buf40_5", 5, 8),
)


# --------------------------------------------------------------------------- #
# Neutralization + placebo config (committed; A0-parity neutralization).       #
# --------------------------------------------------------------------------- #

NEUTRALIZATION: tuple[str, ...] = (
    "industry_sw_l1",
    "log_circ_mv",
    "winsor_0.01",
    "min_obs_20",
)
"""Committed neutralization recipe — one-for-one with ``slot_frontier`` (industry SW-L1
+ log size residualization, 1% winsor, min 20 obs), on the FULL panel before the filter
so A0's ranker table is byte-reproducible."""

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
    cpcv_purge_embargo=HORIZON - 1,  # = 4
    deflation_n=2418,  # non-zeroing ledger floor pre-D2 (D1 appended → 2418)
)

DSR_ROLE: str = "disclosure_only"
"""Amendment 2026-07-04: DSR/SPA/RW are pre-declared to FAIL, computed + disclosed +
appended to the ledger, but NOT promotion gates. See :data:`AMENDMENT`."""

PROMOTION_GATES: tuple[str, ...] = (
    "beats_own_random_placebo_joint_t2",
    "bear_cum_nonneg",
    "crash_slices_nonneg",
    "net_pnl_positive",
)
"""The dev SELECTION gate (owner judges): the promotion decision inputs after the
amendment moved certification to the forward window."""


# --------------------------------------------------------------------------- #
# Pre-registered three-branch decision tree. Only the branch KEYS ("a"/"b"/"c")#
# enter the hash — the branch descriptions are documentation and the decision  #
# logic is encoded structurally in ``defensive_d2_ablation._read`` (an          #
# editorial reword must not perturb the frozen scientific digest). The RUNTIME  #
# read is a diagnostic surface — the owner judges per the amendment.           #
# --------------------------------------------------------------------------- #

DECISION_BRANCHES: tuple[tuple[str, str], ...] = (
    (
        "a",
        "d2_beats_own_placebo_joint AND owner_gates_improved -> ranking layer advances,"
        " freeze + send to the forward queue",
    ),
    (
        "b",
        "NOT d2_beats_own_placebo_joint AND the d2 container still shows a sleeve risk"
        " profile -> drop the ranking layer, product = sleeve-only",
    ),
    (
        "c",
        "NOT a0_beats_own_placebo_joint -> the reversal book-layer ranking edge is"
        " refuted (its net P&L was universe/rotation/exposure, not ranking); ranking"
        " layer death sentence + a qualitative correction. If it co-occurs with d2"
        " beating placebo, disclose the contradiction honestly for the owner to judge.",
    ),
)

AMENDMENT: str = (
    "qgr-certification-rearch-amendment-2026-07-04-"
    "dev-selection-forward-certification.md"
)


# --------------------------------------------------------------------------- #
# Spec hash — canonical JSON (sort_keys) → SHA256; deterministic.             #
# --------------------------------------------------------------------------- #


def _canonical_payload() -> dict[str, object]:
    """The committed scientific content (prose pointers excluded from the hash)."""
    return {
        "candidate": CANDIDATE,
        "ranker_factors": list(RANKER_FACTORS),
        "ranker_implementation": RANKER_IMPLEMENTATION,
        "crowd_factor": CROWD_FACTOR,
        "universe_filter": asdict(D2_UNIVERSE_FILTER),
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
        # Keys only — the branch descriptions are prose (excluded so a reword cannot
        # perturb the frozen digest); the branch logic lives in ``_read``.
        "decision_branches": [b[0] for b in DECISION_BRANCHES],
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


__all__ = [
    "AMENDMENT",
    "BEATS_PLACEBO_T",
    "CANDIDATE",
    "CONTAINERS",
    "CROWD_FACTOR",
    "D2_UNIVERSE_FILTER",
    "DECISION_BRANCHES",
    "DSR_ROLE",
    "GATE_CALIBRATION",
    "HORIZON",
    "NEUTRALIZATION",
    "PLACEBO_SEED",
    "PLACEBO_TOP_N",
    "PROMOTION_GATES",
    "RANKER_FACTORS",
    "RANKER_IMPLEMENTATION",
    "REBALANCE_FREQ",
    "ContainerSpec",
    "D2UniverseFilter",
    "GateCalibration",
    "spec_hash",
]
