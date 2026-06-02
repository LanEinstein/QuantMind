"""Deterministic thesis-derivation config (Phase W-001).

Runtime-immutable thresholds for the whitelist invalidation templates. Mirrors
the ``slot_portfolio`` / ``RiskConfig`` discipline (CLAUDE.md §2.4): no
hot-reload, a change is a git diff + amendment + restart. The defaults are
conservative (a thesis "breaks" only on a clear adverse move), in keeping with
the precision-over-recall alert-fatigue red line — the deterministic break adds
SELL pressure, so a false break would force an exit.
"""

from __future__ import annotations

from dataclasses import dataclass

FEATURE_CODE_VERSION = "position_thesis/v1"
"""Pinned derivation-code version. Bump when the threshold maths changes so a
stale thesis (older version) is distinguishable on replay."""


@dataclass(frozen=True)
class ThesisDerivationConfig:
    """Locked parameters for the three whitelist invalidation templates.

    Attributes:
        anchor_drawdown_pct: ANCHOR_DRAWDOWN fires when price falls this far
            below the entry anchor (0.12 = 12%).
        time_stop_trade_days: TIME_STOP fires when the holding has been held
            this many trading days (the catalyst window elapsed).
        score_decay_pct: SCORE_DECAY fires when the fresh Line-1 composite score
            falls this fraction below the entry score (relative to its
            magnitude, so it is well-defined for a signed score).
    """

    anchor_drawdown_pct: float = 0.12
    time_stop_trade_days: int = 30
    score_decay_pct: float = 0.50

    def __post_init__(self) -> None:
        if not 0.0 < self.anchor_drawdown_pct < 1.0:
            raise ValueError("anchor_drawdown_pct must be in (0, 1)")
        if not 1 <= self.time_stop_trade_days <= 500:
            raise ValueError("time_stop_trade_days must be in [1, 500]")
        if not 0.0 < self.score_decay_pct < 1.0:
            raise ValueError("score_decay_pct must be in (0, 1)")


__all__ = ["FEATURE_CODE_VERSION", "ThesisDerivationConfig"]
