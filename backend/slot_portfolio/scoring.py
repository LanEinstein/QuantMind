"""Deterministic rotation scoring (Phase V-002).

The two halves of the ≤5-slot rotation decision, as pure functions over a
PIT-pinned frame (P0-7-amendment-2026-06-01-five-slot-rotation §1.3):

* :func:`evaluate_incumbent_weakness` — is a held position *independently weak*?
  All **7** conditions must hold (codex round-2 §Q2 lock). The "independently
  weak" gate is the protection core: a healthy incumbent is **never** sold just
  to chase a challenger — it roots out "sell a good holding to chase a phantom".
* :func:`evaluate_challenger_margin` — does a candidate beat the incumbent it
  would replace **by an absolute margin** (not merely on rank)?

Both are deterministic: identical inputs + config always yield the identical
result, so the decision replays bit-exact off the same pinned frame. The module
is pure quant — it reads only the existing **Line-1 quant score** + the
**deterministic Line-2 incumbent health**, decoupled from direction ① (theme
conviction) and ② (thesis-health). Those two may later be folded in as extra
provenance-tagged components of the composite (replacement) score via amendment
**without** changing this interface (§1.6 — the replacement score is "an
interface with provenance tags + deterministic normalisation", not a
mega-dependency on ①②).

Red lines (``backend/slot_portfolio/CLAUDE.md``):

1. Pure functions, no IO, no ``import backend.{llm,agents,mirofish}`` (redline
   ``[V-002]`` + module-contract test enforce the closure).
2. **Never constructs an InstructionPlan** — this layer only proposes; the
   single construction point (R0 §4) stays the builder.
3. Fail-closed toward *inaction*: any non-finite / out-of-range numeric input
   makes the incumbent "not weak" and the challenger "not winning", so corrupt
   data can never trigger a rotation SELL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.utils.decision_compare import decision_compare

# The deterministic components the ship-first replacement (composite) score is
# built from. Provenance tag only — directions ① (theme) / ② (thesis) append
# their own component names here via amendment when they merge (§1.6).
SHIP_FIRST_SCORE_COMPONENTS: tuple[str, ...] = ("line1_quant", "line2_health")


class SlotPortfolioError(ValueError):
    """Raised on a malformed scoring input or config invariant violation."""


# ---------------------------------------------------------------------------
# Threshold config (the slices ``policy.RotationPolicyConfig`` wraps)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IncumbentWeakConfig:
    """Thresholds for the 7-condition ``incumbent_independently_weak`` gate."""

    min_holding_age_trading_days: int  # condition 3
    max_line1_percentile: float        # condition 4 — weak if <= this (P40)
    min_rank_deterioration_pct: float  # condition 5 — entry−now >= this (20pct)
    score_below_median_mad_mult: float  # condition 6a — median−score >= mult·MAD
    drawdown_soft_threshold: float     # condition 6c — drawdown >= this


@dataclass(frozen=True)
class ChallengerMarginConfig:
    """Thresholds for the ``challenger wins by margin`` gate."""

    min_percentile: float            # challenger percentile >= this (P75)
    min_rank_lead_pct: float         # challenger − incumbent percentile >= this
    min_composite_score_margin: float  # absolute composite margin (not rank)


# ---------------------------------------------------------------------------
# Inputs — deterministic state of one held position / one candidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IncumbentState:
    """Deterministic state of one held position, for the weakness gate.

    All numeric quant fields come from the same PIT-pinned Line-1 frame +
    deterministic Line-2 health observation the orchestration layer assembles;
    this module never fetches them. ``line1_percentile`` / ``entry_percentile``
    are cross-sectional percentiles in [0, 1] (higher = stronger).
    ``composite_score`` is the replacement (rotation) score — ship-first it is
    the Line-1 quant composite (same scale as the screener's score).
    """

    code: str
    line1_percentile: float          # condition 4
    composite_score: float           # margin comparison currency
    entry_percentile: float          # condition 5 — percentile at entry/rebalance
    holding_age_trading_days: int    # condition 3
    protective_stop_active: bool     # condition 1 (weak only if NOT active)
    hard_exit_pending: bool          # condition 2 (weak only if NOT pending)
    # condition 6 — >= 1 of these three confirmations must fire:
    score_median_20d: float          # 6a inputs
    score_mad_20d: float
    anomaly_flag_active: bool        # 6b (Line-2 deterministic anomaly flag)
    drawdown_from_local_high: float  # 6c, in [0, 1]
    # condition 7 — no deterministic veto:
    suspended: bool
    limit_down_unsellable: bool
    corporate_action_unsafe: bool


@dataclass(frozen=True)
class ChallengerState:
    """Deterministic state of one new Line-1 candidate (a rotation challenger).

    ``qualified`` means it passed the **pure-quant** qualification + every
    buy-side hard gate (screening exclusions + affordability) — the theme/peer
    layers never qualify here (§1.6 decoupling). ``line1_percentile`` ∈ [0, 1];
    ``composite_score`` is the same replacement-score currency as the incumbent.
    """

    code: str
    qualified: bool
    line1_percentile: float
    composite_score: float


# ---------------------------------------------------------------------------
# Results — full per-condition breakdown (diagnostics + auditability)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IncumbentWeakness:
    """The 7-condition breakdown; ``independently_weak`` is their conjunction."""

    code: str
    independently_weak: bool
    no_protective_stop: bool       # condition 1
    no_hard_exit: bool             # condition 2
    aged_enough: bool              # condition 3
    percentile_weak: bool          # condition 4
    rank_deteriorated: bool        # condition 5
    has_confirmation: bool         # condition 6 (>=1 of the three below)
    no_veto: bool                  # condition 7
    score_below_median_mad: bool   # confirmation 6a
    anomaly_confirmation: bool     # confirmation 6b
    drawdown_confirmation: bool    # confirmation 6c


@dataclass(frozen=True)
class ChallengerMargin:
    """The margin breakdown; ``wins_by_margin`` is the conjunction of all four."""

    challenger_code: str
    incumbent_code: str
    wins_by_margin: bool
    qualified: bool                    # passed pure-quant + buy-side hard gates
    percentile_strong: bool            # challenger percentile >= P75
    rank_lead_sufficient: bool         # challenger − incumbent percentile >= lead
    composite_margin_sufficient: bool  # absolute composite margin (not rank)


# ---------------------------------------------------------------------------
# Pure scoring
# ---------------------------------------------------------------------------


def _is_pct(x: float) -> bool:
    """A finite percentile in the closed unit interval."""
    return math.isfinite(x) and 0.0 <= x <= 1.0


def evaluate_incumbent_weakness(
    incumbent: IncumbentState, config: IncumbentWeakConfig
) -> IncumbentWeakness:
    """Evaluate the 7-condition ``incumbent_independently_weak`` gate.

    ``independently_weak`` is True only when **all 7** conditions hold. This is
    the protection core (§1.3): a position that is not independently weak is
    **never** rotated out, no matter how strong a challenger looks. Fail-closed
    toward inaction — a non-finite / out-of-range percentile makes the position
    *not weak* (so corrupt data can never trigger a SELL).
    """
    # Conditions 1, 2, 7 are pure booleans — no numeric fragility.
    no_protective_stop = not incumbent.protective_stop_active
    no_hard_exit = not incumbent.hard_exit_pending
    no_veto = not (
        incumbent.suspended
        or incumbent.limit_down_unsellable
        or incumbent.corporate_action_unsafe
    )

    # Condition 3 — held long enough (min hold period).
    aged_enough = incumbent.holding_age_trading_days >= (
        config.min_holding_age_trading_days
    )

    # Condition 4 — current Line-1 percentile in the weak band (<= P40). A
    # non-finite percentile fails closed to "not weak".
    # Threshold comparisons go through the fixed-point ``decision_compare``
    # (AE-003) so a borderline gate cannot flip on a numpy-version (NEP 50)
    # float-repr change — the rotation decision must replay bit-exact.
    percentile_weak = _is_pct(incumbent.line1_percentile) and decision_compare(
        incumbent.line1_percentile, config.max_line1_percentile, "<="
    )

    # Condition 5 — rank deteriorated by >= min since entry/last rebalance.
    rank_deteriorated = (
        _is_pct(incumbent.line1_percentile)
        and _is_pct(incumbent.entry_percentile)
        and decision_compare(
            incumbent.entry_percentile - incumbent.line1_percentile,
            config.min_rank_deterioration_pct,
            ">=",
        )
    )

    # Condition 6 — >= 1 of three deterministic confirmations.
    # 6a: own score below its 20d median by >= mult·MAD (needs real dispersion;
    #     a zero/non-finite MAD yields no confirmation here, fail-closed).
    score_below_median_mad = (
        math.isfinite(incumbent.score_mad_20d)
        and incumbent.score_mad_20d > 0.0
        and math.isfinite(incumbent.score_median_20d)
        and math.isfinite(incumbent.composite_score)
        and decision_compare(
            incumbent.score_median_20d - incumbent.composite_score,
            config.score_below_median_mad_mult * incumbent.score_mad_20d,
            ">=",
        )
    )
    # 6b: Line-2 deterministic anomaly flag.
    anomaly_confirmation = incumbent.anomaly_flag_active
    # 6c: drawdown from a local high past the soft threshold.
    drawdown_confirmation = math.isfinite(
        incumbent.drawdown_from_local_high
    ) and decision_compare(
        incumbent.drawdown_from_local_high, config.drawdown_soft_threshold, ">="
    )
    has_confirmation = (
        score_below_median_mad or anomaly_confirmation or drawdown_confirmation
    )

    independently_weak = (
        no_protective_stop
        and no_hard_exit
        and aged_enough
        and percentile_weak
        and rank_deteriorated
        and has_confirmation
        and no_veto
    )
    return IncumbentWeakness(
        code=incumbent.code,
        independently_weak=independently_weak,
        no_protective_stop=no_protective_stop,
        no_hard_exit=no_hard_exit,
        aged_enough=aged_enough,
        percentile_weak=percentile_weak,
        rank_deteriorated=rank_deteriorated,
        has_confirmation=has_confirmation,
        no_veto=no_veto,
        score_below_median_mad=score_below_median_mad,
        anomaly_confirmation=anomaly_confirmation,
        drawdown_confirmation=drawdown_confirmation,
    )


def evaluate_challenger_margin(
    challenger: ChallengerState,
    incumbent: IncumbentState,
    config: ChallengerMarginConfig,
) -> ChallengerMargin:
    """Evaluate whether ``challenger`` beats ``incumbent`` by an absolute margin.

    ``wins_by_margin`` is True only when the challenger (a) is qualified, (b)
    sits at >= P75, (c) leads the incumbent by >= the rank-lead percentile, AND
    (d) beats it on the **absolute composite score** (not merely on rank — §1.3
    "预期组合分须以绝对 margin 胜出"). Fail-closed toward inaction on any
    non-finite percentile / score.
    """
    finite = (
        _is_pct(challenger.line1_percentile)
        and _is_pct(incumbent.line1_percentile)
        and math.isfinite(challenger.composite_score)
        and math.isfinite(incumbent.composite_score)
    )
    percentile_strong = finite and decision_compare(
        challenger.line1_percentile, config.min_percentile, ">="
    )
    rank_lead_sufficient = finite and decision_compare(
        challenger.line1_percentile - incumbent.line1_percentile,
        config.min_rank_lead_pct,
        ">=",
    )
    composite_margin_sufficient = finite and decision_compare(
        challenger.composite_score - incumbent.composite_score,
        config.min_composite_score_margin,
        ">=",
    )

    wins_by_margin = (
        challenger.qualified
        and percentile_strong
        and rank_lead_sufficient
        and composite_margin_sufficient
    )
    return ChallengerMargin(
        challenger_code=challenger.code,
        incumbent_code=incumbent.code,
        wins_by_margin=wins_by_margin,
        qualified=challenger.qualified,
        percentile_strong=percentile_strong,
        rank_lead_sufficient=rank_lead_sufficient,
        composite_margin_sufficient=composite_margin_sufficient,
    )


__all__ = [
    "SHIP_FIRST_SCORE_COMPONENTS",
    "ChallengerMargin",
    "ChallengerMarginConfig",
    "ChallengerState",
    "IncumbentState",
    "IncumbentWeakConfig",
    "IncumbentWeakness",
    "SlotPortfolioError",
    "evaluate_challenger_margin",
    "evaluate_incumbent_weakness",
]
