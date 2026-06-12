"""Auto-demotion — incumbent counterfactual baseline + cooldown
(AB-004 / P2-2-amendment-2026-06-12 §1.3; codex P0-5/P2-2).

After a promotion the INCUMBENT keeps running in shadow as the live
counterfactual baseline; the challenger demotes when it RELATIVELY
underperforms that baseline over K days — relative, not absolute, so a
bear market never kills a good strategy for losing less than the old
one would have.

Demotion is a DECISION + a rollback intent (AB-003 manifest chain) —
it never touches the broker: held positions ride their entry-time sell
stack (the AA-004 nameplate; the entry_policy_hash semantics are
adversarially pinned there), and the demoted artifact stays in the
registry/experiment ledger forever (replayable provenance, never
deleted).

A per-family promotion cooldown prevents promote/demote oscillation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

import structlog
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger(component="strategy_evolution.demotion")

DEMOTION_OBSERVATION_DAYS = 10
"""K — minimum live days before a relative-underperformance verdict.
Shorter windows demote on noise; the K-day bound plus the bps floor
below makes the trigger deterministic and replayable."""

DEMOTION_RELATIVE_FLOOR_BPS = -150.0
"""Cumulative challenger-minus-incumbent excess (bps of equity) at or
below which the challenger demotes. -150bps over >=10 days is far
outside friction noise on a ¥100k account yet small enough to cut a
genuinely worse policy before it compounds."""

PROMOTION_COOLDOWN_DAYS = 10
"""Amendment §1.3 — after any promote/demote in a family, no further
promotion for N days (oscillation damper)."""


class DemotionDecision(BaseModel):
    """Deterministic demotion ruling for one active challenger."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    family: str = Field(min_length=1, max_length=128)
    artifact_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    demote: bool
    observed_days: int = Field(ge=0)
    cumulative_excess_bps: float
    detail: str = Field(max_length=256)


def evaluate_demotion(
    *,
    family: str,
    artifact_hash: str,
    challenger_daily_pnl: Sequence[float],
    incumbent_counterfactual_daily_pnl: Sequence[float],
    equity_base: float,
    observation_days: int = DEMOTION_OBSERVATION_DAYS,
    relative_floor_bps: float = DEMOTION_RELATIVE_FLOOR_BPS,
) -> DemotionDecision:
    """Pure K-day relative-underperformance judgement.

    The two series are SAME-LENGTH aligned daily PnL since activation
    (live challenger vs shadow incumbent on the same PIT inputs).
    Fail-closed on malformed inputs: mismatched lengths or a
    non-positive equity base produce a NON-demoting decision with the
    defect named — a broken counterfactual must page a human, not
    auto-rollback on garbage.
    """
    n = len(challenger_daily_pnl)
    if n != len(incumbent_counterfactual_daily_pnl):
        return DemotionDecision(
            family=family,
            artifact_hash=artifact_hash,
            demote=False,
            observed_days=0,
            cumulative_excess_bps=0.0,
            detail=(
                f"series length mismatch ({n} vs "
                f"{len(incumbent_counterfactual_daily_pnl)}); "
                f"counterfactual broken — no verdict"
            ),
        )
    if equity_base <= 0.0 or not math.isfinite(equity_base):
        return DemotionDecision(
            family=family,
            artifact_hash=artifact_hash,
            demote=False,
            observed_days=n,
            cumulative_excess_bps=0.0,
            detail=f"invalid equity base {equity_base}; no verdict",
        )

    cumulative_excess = sum(
        c - i
        for c, i in zip(
            challenger_daily_pnl,
            incumbent_counterfactual_daily_pnl,
            strict=True,
        )
    )
    excess_bps = cumulative_excess / equity_base * 10_000.0

    if n < observation_days:
        return DemotionDecision(
            family=family,
            artifact_hash=artifact_hash,
            demote=False,
            observed_days=n,
            cumulative_excess_bps=excess_bps,
            detail=f"only {n}/{observation_days} observation days",
        )

    demote = excess_bps <= relative_floor_bps
    return DemotionDecision(
        family=family,
        artifact_hash=artifact_hash,
        demote=demote,
        observed_days=n,
        cumulative_excess_bps=excess_bps,
        detail=(
            f"cumulative excess {excess_bps:.1f}bps vs floor "
            f"{relative_floor_bps}bps over {n}d"
        ),
    )


def is_in_promotion_cooldown(
    *,
    last_action_at: datetime | None,
    now: datetime,
    cooldown_days: int = PROMOTION_COOLDOWN_DAYS,
) -> bool:
    """Whether the family's promote/demote cooldown is still running."""
    if last_action_at is None:
        return False
    return now - last_action_at < timedelta(days=cooldown_days)


__all__ = [
    "DEMOTION_OBSERVATION_DAYS",
    "DEMOTION_RELATIVE_FLOOR_BPS",
    "PROMOTION_COOLDOWN_DAYS",
    "DemotionDecision",
    "evaluate_demotion",
    "is_in_promotion_cooldown",
]
