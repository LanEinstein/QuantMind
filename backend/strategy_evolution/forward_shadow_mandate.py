"""Forward-shadow mandate + honest dashboard (AE-005 / amendment §2.1/§2.4).

The lane's two terminal artefacts:

* :class:`ForwardShadowMandate` — the **stage-3 declaration**. A candidate that
  clears the historical prefilter + per-candidate verify does NOT promote; it
  earns a mandate to *enter* a 45-day frozen forward shadow (predeclared
  metrics + a calendar-time floor + zero parameter edits during the window).
  Promotion to ACTIVE still requires the shadow to *complete* AND a human pin
  (amendment §2.1 — "候选不得因『通过历史门』就上线"). At creation a mandate is
  always PENDING — :meth:`ForwardShadowMandate.is_shadow_window_complete` is
  False — so the historical funnel can never, by itself, promote anything.

* :class:`HonestDashboard` — the **honest dashboard** (amendment §2.4): the
  numbers an owner must read before any pin (cumulative N, sentinel pass rate,
  PBO/SPA disclosure, days since last promotion, admission margin). Its design
  goal is to keep giving the owner reasons NOT to trust the machine.

Pure data + pure predicates — no IO, no wall-clock (``now`` is injected).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from backend.strategy_evolution.mechanism_registry import EconomicMechanism

MIN_FORWARD_SHADOW_CALENDAR_DAYS = 45
"""The frozen forward shadow's calendar-time floor (amendment §2.1, P0-6 verbatim)."""

PREDECLARED_FORWARD_SHADOW_METRICS: tuple[str, ...] = (
    "max_drawdown_pct",
    "pnl_cny",
    "csi300_excess_pct",
    "instruction_completion_rate",
    "report_accuracy_rate",
)
"""The metrics the forward shadow is judged on — DECLARED at mandate creation
and frozen for the window (no metric shopping after the fact)."""


@dataclass(frozen=True)
class ForwardShadowMandate:
    """A candidate's mandate to ENTER (not pass) the frozen forward shadow."""

    batch_id: str
    family: str
    mechanism: EconomicMechanism
    candidate_param_hash: str
    frozen_param_values: tuple[tuple[str, float], ...]
    predeclared_metrics: tuple[str, ...]
    prefilter_excess_sharpe: float
    calendar_start_date: str
    min_calendar_days: int
    created_at: dt.datetime

    def is_shadow_window_complete(self, *, as_of: dt.date) -> bool:
        """Whether ``min_calendar_days`` have elapsed since the start (calendar).

        At creation ``as_of == calendar_start_date`` ⇒ False: a freshly-declared
        mandate has served no forward time, so it cannot promote. Completion is a
        NECESSARY (not sufficient) condition — a human pin still follows.
        """
        start = dt.date.fromisoformat(self.calendar_start_date)
        return (as_of - start).days >= self.min_calendar_days


@dataclass(frozen=True)
class HonestDashboard:
    """The pre-pin honesty panel (amendment §2.4 — mandatory before any pin)."""

    batch_id: str
    family: str
    cumulative_n: int
    real_candidate_count: int
    sentinel_count: int
    sentinels_passed: int
    survivors: int
    pbo: float
    spa_p_value: float
    n_observations: int
    min_observations_required: int
    batch_admitted: bool
    days_since_last_promotion: int | None

    @property
    def sentinel_integrity_ok(self) -> bool:
        """A broken gate lets a sentinel through; this MUST be True."""
        return self.sentinels_passed == 0

    @property
    def space_has_alpha_signal(self) -> bool:
        """False when no real candidate survived — the space showed no edge."""
        return self.survivors > 0

    def summary(self) -> str:
        """One-line honesty digest for the audit row / Feishu notification."""
        return (
            f"batch {self.batch_id[:12]} family={self.family} N={self.cumulative_n} "
            f"survivors={self.survivors}/{self.real_candidate_count} "
            f"sentinels_passed={self.sentinels_passed}/{self.sentinel_count} "
            f"PBO={self.pbo:.3f} SPA_p={self.spa_p_value:.3f} "
            f"admitted={self.batch_admitted} "
            f"obs={self.n_observations}/{self.min_observations_required}"
        )


__all__ = [
    "MIN_FORWARD_SHADOW_CALENDAR_DAYS",
    "PREDECLARED_FORWARD_SHADOW_METRICS",
    "ForwardShadowMandate",
    "HonestDashboard",
]
