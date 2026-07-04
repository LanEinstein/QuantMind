"""Frozen, pre-declared spec for the DEPLOYABLE defensive sleeve (product foundation).

After the ranking layer was refuted across BOTH mechanisms (DS-D2 branch (c): price
reversal; DS-AM: analyst information flow), and AP-0.5 proved in-sample DSR≥0.95
certification arithmetically unreachable for any cross-sectional ranker, the defensive
SLEEVE is the one validated, load-bearing leg of the whole research program. Its value
is a RISK PROPERTY (near-mechanical MDD bound from a defensive-universe filter + a cash
buffer), NOT ranking alpha — so, per the 2026-07-04 certification-rearch amendment, the
placebo / DSR machinery does not apply to it (they are disclosed, not gated), and its
value can be verified FORWARD in months (a risk property) rather than the decades a
Sharpe would need.

This spec freezes the deployable sleeve so the confirmatory science-gate backtest + the
pre-registered forward validation both evaluate a definition committed BEFORE they run
(anti-p-hacking). Nothing is fit in-sample.

Composition (committed):
  * defensive universe filter = the D1-VALIDATED dividend-low-vol filter, reused
    (drift-guarded to ``defensive_d1_spec.UNIVERSE_FILTERS``) — D1 proved this FILTER +
    a cash buffer controls drawdown (buf40_5 MDD 14.78% vs CSI300 45%, bear cum ≥ 0),
    while its RANKER was rejected; so the sleeve keeps the filter and drops the ranker.
  * selection within the universe = the SIMPLEST deterministic rule (amendment: "宇宙内
    选择退化为最简单确定性规则如 dv_ratio top-5 等权") — dv_ratio top-5 equal weight; NO
    block-weighted ranker (rejected), NO reversal / analyst ranker (refuted).
  * container = ``buf40_5`` (5 slots × 8% cap ≈ 40% gross / 60% cash buffer; the P-E
    ≥40% cash floor; the drawdown-control mechanism), byte-anchored to slot_frontier.
  * analyst-momentum tilt = OPTIONAL, OFF by default (DS-AM showed a thin auxiliary edge
    on the buffered container — a legitimate tie-break within the top candidates, not a
    standalone selector; disclosed, disabled in the base deployable spec).

Judging (amendment — the sleeve does NOT claim ranking alpha):
  * dev science gate = net P&L > 0 + bear-regime cumulative ≥ 0 + mechanical MDD
    bound holds (disclosed vs CSI300) + beats a NAIVE within-universe baseline on risk;
    placebo / DSR / SPA / RW computed + DISCLOSED, not gated.
  * certification = FORWARD only: pre-registered risk kill-switch (below) + go-live
    P0-6 45-day shadow; returns are monitored, never t-tested for significance.

Design invariants (asserted by ``tests/factor_research/test_defensive_sleeve_spec.py``):
  * pure constants — zero IO, zero ``backend.{llm,agents,mirofish,risk}`` import;
  * :data:`UNIVERSE_FILTER` == D1's ``UNIVERSE_FILTERS`` (reuse not re-tune);
  * :data:`CONTAINER` matches ``slot_frontier.FRONTIER`` buf40_5 field-for-field;
  * :func:`spec_hash` is deterministic (same input → same SHA256).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

PRODUCT: str = "defensive_sleeve_v1"


# --------------------------------------------------------------------------- #
# Defensive universe filter — the D1-validated dividend-low-vol filter, REUSED. #
# (drift-guarded to defensive_d1_spec.UNIVERSE_FILTERS; never re-tuned here).   #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UniverseFilter:
    """The committed defensive universe filter (== D1's, reused verbatim)."""

    max_lottery_exclude_quantile: float
    roe_floor: float
    gpm_floor_quantile: float
    dividend_min_percentile: float
    exclusion_four_piece_applied: bool
    at_limit_unfillable_removed: bool
    bottom_30pct_size_cut_applied: bool


UNIVERSE_FILTER: UniverseFilter = UniverseFilter(
    max_lottery_exclude_quantile=0.90,
    roe_floor=0.0,
    gpm_floor_quantile=0.10,
    dividend_min_percentile=0.50,
    exclusion_four_piece_applied=True,
    at_limit_unfillable_removed=True,
    bottom_30pct_size_cut_applied=True,
)


# --------------------------------------------------------------------------- #
# Selection within the universe — the SIMPLEST deterministic rule (amendment). #
# NO ranker (block-weighted rejected; reversal / analyst refuted).            #
# --------------------------------------------------------------------------- #

SELECTION_FACTOR: str = "dv_ratio"
"""Pick the highest-dividend-yield names within the defensive universe (a single,
committed, deterministic factor — the amendment's simplest-rule example)."""

SELECTION_TOP_N: int = 5
SELECTION_WEIGHTING: str = "equal_weight"


# --------------------------------------------------------------------------- #
# Optional analyst-momentum tilt (DS-AM: thin auxiliary edge; OFF by default). #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AnalystTilt:
    """A documented OPTIONAL tie-break within the top candidates (never a selector).

    DS-AM showed analyst momentum is a thin, insignificant auxiliary edge on the buf
    container (positive vs random, t +0.14 / +1.17) — a tie-break, not a
    standalone selector. Disabled in the base spec; enabling it is a documented
    variant that must be re-frozen + re-validated forward.
    """

    enabled: bool
    factors: tuple[str, ...]
    role: str


ANALYST_TILT: AnalystTilt = AnalystTilt(
    enabled=False,
    factors=("np_rev", "rev_diff", "cover_chg"),
    role="tie_break_within_top_candidates_only",
)


# --------------------------------------------------------------------------- #
# Container + horizon — buf40_5 deployment gate, 20d monthly (D1 cadence).     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ContainerSpec:
    """The deployment container: 5 slots × 8% cap ≈ 40% gross / 60% cash buffer."""

    label: str
    slots: int
    cap_percent: int


CONTAINER: ContainerSpec = ContainerSpec("buf40_5", 5, 8)

HORIZON: int = 20
REBALANCE_FREQ: int = 20


# --------------------------------------------------------------------------- #
# Dev science gate (risk-property; placebo/DSR disclosed, NOT gated).          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScienceGate:
    """The committed dev acceptance for the sleeve (a RISK claim, not ranking alpha).

    ``mdd_disclose_bound`` is the mechanical drawdown expectation from the 40%-gross
    buffer (≈ D1 buf40_5's 14.78%, disclosed with headroom); a dev MDD far above it
    signals the filter/buffer is not doing its job. ``net_pnl_positive`` / ``bear_cum_
    nonneg`` are owner criteria. DSR / SPA / RW are computed and DISCLOSED (the sleeve
    makes no ranking claim, so beating a random placebo is NOT required).
    """

    net_pnl_positive: bool
    bear_cum_nonneg: bool
    mdd_disclose_bound: float
    dsr_role: str


SCIENCE_GATE: ScienceGate = ScienceGate(
    net_pnl_positive=True,
    bear_cum_nonneg=True,
    mdd_disclose_bound=0.20,  # buffer-implied ~15-20%; disclosed, not a hard gate
    dsr_role="disclosure_only",
)


# --------------------------------------------------------------------------- #
# Pre-registered FORWARD kill-switch (certification = survival, NOT t-test).   #
# Committed BEFORE the forward window; a breach STOPS the sleeve (fail-closed). #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ForwardKillSwitch:
    """Pre-registered forward risk kill-switch (amendment: survival certification).

    * ``mdd_kill`` — realized forward MDD above this = the mechanical bound broke
      (structural failure of the filter/buffer) → stop.
    * ``bear_cum_kill`` — forward bear-regime cumulative below this = the sleeve's
      defensive claim (bear ≥ 0) is falsified → stop.
    * ``baseline_underperf_periods`` — consecutive periods the sleeve trails its
      naive within-universe baseline (dv_ratio top-5 eq) by any margin →
      stop (the sleeve adds no value over the simplest rule).
    * ``min_forward_periods`` — below this the sleeve is ACCRUING; no verdict is
      issued (never certify on a too-short window).
    """

    mdd_kill: float
    bear_cum_kill: float
    baseline_underperf_periods: int
    min_forward_periods: int


FORWARD_KILL_SWITCH: ForwardKillSwitch = ForwardKillSwitch(
    mdd_kill=0.25,  # > buffer-implied ~15-20% → structural break
    bear_cum_kill=-0.05,  # bear regime materially negative → defensive claim broken
    baseline_underperf_periods=6,  # ~6 months trailing the simplest rule → no value-add
    min_forward_periods=8,  # < 8 monthly periods = ACCRUING, no verdict (P0-6 aligned)
)

GO_LIVE_GATE: str = "P0-6 45-day rolling shadow + owner-gated activation"
AMENDMENT: str = (
    "qgr-certification-rearch-amendment-2026-07-04-"
    "dev-selection-forward-certification.md"
)


# --------------------------------------------------------------------------- #
# Spec hash — canonical JSON (sort_keys) → SHA256; deterministic.             #
# --------------------------------------------------------------------------- #


def _canonical_payload() -> dict[str, object]:
    """The committed scientific content only.

    Free-text governance prose (:data:`GO_LIVE_GATE`, :data:`AMENDMENT`) is EXCLUDED — a
    semantically-neutral reword must not mutate the frozen scientific hash (the D1/D2/AM
    convention: the hash covers committed constants, not re-wordable prose).
    """
    return {
        "product": PRODUCT,
        "universe_filter": asdict(UNIVERSE_FILTER),
        "selection": {
            "factor": SELECTION_FACTOR,
            "top_n": SELECTION_TOP_N,
            "weighting": SELECTION_WEIGHTING,
        },
        "analyst_tilt": asdict(ANALYST_TILT),
        "container": asdict(CONTAINER),
        "horizon": HORIZON,
        "rebalance_freq": REBALANCE_FREQ,
        "science_gate": asdict(SCIENCE_GATE),
        "forward_kill_switch": asdict(FORWARD_KILL_SWITCH),
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
    "ANALYST_TILT",
    "CONTAINER",
    "FORWARD_KILL_SWITCH",
    "GO_LIVE_GATE",
    "HORIZON",
    "PRODUCT",
    "REBALANCE_FREQ",
    "SCIENCE_GATE",
    "SELECTION_FACTOR",
    "SELECTION_TOP_N",
    "SELECTION_WEIGHTING",
    "UNIVERSE_FILTER",
    "AnalystTilt",
    "ContainerSpec",
    "ForwardKillSwitch",
    "ScienceGate",
    "UniverseFilter",
    "spec_hash",
]
