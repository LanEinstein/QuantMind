"""Frozen, pre-declared spec for SLV-2 — the quality (gross-profitability) sleeve.

Preregistration: ``docs/research/defensive-sleeve2-preregistration-2026-08-23.md``
(committed before this implementation). SLV-2 tests ONE claim: inside the SAME
validated defensive universe (D1 gates, reused verbatim — never re-tuned) and the
SAME ``buf40_5`` cash-buffer container, selecting the 5 highest RAW gross-profit-margin
names (instead of SLV-1's 5 highest dividend-yield names) is an independently
deployable risk-product leg.

Overlap discipline (the plan's component-independence requirement): at every
rebalance date the 5 names SLV-1's committed rule would hold (``dv_ratio`` desc,
ties by ``ts_code`` asc, head 5, within the same gated universe) are EXCLUDED from
SLV-2's candidates BEFORE selection — the two books are holdings-disjoint by
construction. Asset-level correlation stays high (both long A-share equity) and is
DISCLOSED, never claimed away.

Judging (STRICTER than SLV-1 — preregistration §5): SLV-2's reason to exist is the
SELECTION itself, so beating the exposure-matched random placebo is a HARD gate here
(paired-t ≥ 2.0), alongside net P&L > 0, bear-regime cumulative ≥ 0, and a HARD
MDD ≤ 0.20 bound. DSR / SPA / RW stay disclosure-only. FAIL on any criterion =
sealed; the R layer stays single-leg.

Design invariants (``tests/factor_research/test_defensive_sleeve2_spec.py``):
pure constants — zero IO; universe filter == D1's ``UNIVERSE_FILTERS`` (structural
equality); container == frontier buf40_5; deterministic :func:`spec_hash`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .defensive_sleeve_spec import CONTAINER, UNIVERSE_FILTER, ContainerSpec

PRODUCT: str = "defensive_sleeve2_v1"

# Universe filter + container are REUSED (imported), never re-tuned here.
UNIVERSE_FILTER = UNIVERSE_FILTER
CONTAINER: ContainerSpec = CONTAINER

# --------------------------------------------------------------------------- #
# Selection — RAW gross profit margin, top-5 equal weight (Codex decision 2).  #
# --------------------------------------------------------------------------- #

SELECTION_FACTOR: str = "gpm"
"""Highest RAW gross-profit-margin names within the (post-exclusion) defensive
universe — the Novy-Marx-style single-factor profitability rule, mirroring SLV-1's
simplest-deterministic-rule template. No neutralization, no composite."""

SELECTION_TOP_N: int = 5
SELECTION_WEIGHTING: str = "equal_weight"

# --------------------------------------------------------------------------- #
# SLV-1 book exclusion — the mechanical overlap discipline (prereg §2).        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Slv1Exclusion:
    """The committed definition of 'SLV-1's book' removed from SLV-2 candidates."""

    factor: str
    top_n: int
    order: str


SLV1_EXCLUSION: Slv1Exclusion = Slv1Exclusion(
    factor="dv_ratio",
    top_n=5,
    order="dv_ratio desc, ts_code asc, finite dv_ratio only",
)

HORIZON: int = 20
REBALANCE_FREQ: int = 20

# --------------------------------------------------------------------------- #
# Science gate — FOUR hard criteria (prereg §5; stricter than SLV-1).          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScienceGate2:
    """All four must hold on ``sleeve2_buf40_5`` or the sleeve FAILS (sealed)."""

    net_pnl_positive: bool
    bear_cum_nonneg: bool
    mdd_hard_bound: float
    placebo_t_min: float


SCIENCE_GATE: ScienceGate2 = ScienceGate2(
    net_pnl_positive=True,
    bear_cum_nonneg=True,
    mdd_hard_bound=0.20,  # HARD gate for SLV-2 (SLV-1 held 19.58% under the same bound)
    placebo_t_min=2.0,  # selection must beat same-pool random — else no second leg
)

PLACEBO_SEED: int = 20260823  # frozen in the preregistration (§6)
LEDGER_FAMILY: str = "ds.defensive_sleeve2"
LEDGER_ROUND: str = "slv2-science-gate"
LEDGER_DATE: str = "2026-08-23"


def _canonical_payload() -> dict[str, object]:
    """The committed scientific content only (prose excluded — SLV-1 convention)."""
    return {
        "product": PRODUCT,
        "universe_filter": asdict(UNIVERSE_FILTER),
        "selection": {
            "factor": SELECTION_FACTOR,
            "top_n": SELECTION_TOP_N,
            "weighting": SELECTION_WEIGHTING,
        },
        "slv1_exclusion": asdict(SLV1_EXCLUSION),
        "container": asdict(CONTAINER),
        "horizon": HORIZON,
        "rebalance_freq": REBALANCE_FREQ,
        "science_gate": asdict(SCIENCE_GATE),
        "placebo_seed": PLACEBO_SEED,
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
    "CONTAINER",
    "HORIZON",
    "LEDGER_DATE",
    "LEDGER_FAMILY",
    "LEDGER_ROUND",
    "PLACEBO_SEED",
    "PRODUCT",
    "REBALANCE_FREQ",
    "SCIENCE_GATE",
    "SELECTION_FACTOR",
    "SELECTION_TOP_N",
    "SELECTION_WEIGHTING",
    "SLV1_EXCLUSION",
    "UNIVERSE_FILTER",
    "ScienceGate2",
    "Slv1Exclusion",
    "spec_hash",
]
