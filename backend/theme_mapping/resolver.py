"""Deterministic policy-theme resolver (AF-001).

Given the frozen mapping + a PIT 申万 L3 membership table, this resolves, for a
candidate code on a decision date:

* ``active_theme_ids`` — themes whose ``effective_from <= d`` (anti-hindsight);
* ``code_theme_ids`` — the active themes whose L3 set covers the code that day;
* ``theme_coverage`` — the bottom-tier ``ValueScoreInputs.theme_coverage`` input
  = the **max ThemeTier weight** over the code's active themes (already
  tier-weighted, as the value-score composite expects). It returns ``0.0`` for a
  code that *has* a PIT L3 but is off every active theme (legitimately
  off-mainline), and ``None`` when the code has **no PIT L3 that day** (unknown /
  data gap) so the caller drops the component rather than fabricating a 0.0 — the
  same convention as ``industry_pit.IndustryPIT.l1_asof`` returning ``None``.

Three fail-closed gates compose: ``decision_date`` must be a well-formed
``YYYYMMDD`` (else a dashed/ISO date would silently break the lexical PIT
compare), the registry must be ``frozen`` (owner-confirmed) unless the caller
explicitly opts into a draft, and a tilt requires both an active theme AND a PIT
L3 membership that exact date. Pure, deterministic, 0 LLM — replays bit-exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.theme_mapping.models import PolicyThemeRegistry
from backend.theme_mapping.sector_pit import SectorMembershipPIT
from backend.theme_research.tier_weights import ThemeTierWeights, theme_tier_weight

_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD — the lexical PIT compare needs this


@dataclass(frozen=True)
class ThemeResolver:
    """Resolves a code's active national-strategy themes + theme_coverage PIT.

    ``allow_draft`` must be set explicitly to resolve against a non-frozen
    (``status: draft``) mapping — the value sleeve must never tilt on an
    unconfirmed draft by accident (the owner-freeze 关键人工 gate).
    """

    registry: PolicyThemeRegistry
    membership: SectorMembershipPIT
    tier_weights: ThemeTierWeights = ThemeTierWeights()
    allow_draft: bool = False

    def __post_init__(self) -> None:
        if not self.registry.frozen and not self.allow_draft:
            raise ValueError(
                "ThemeResolver refuses a non-frozen (draft) policy-theme mapping; "
                "pass allow_draft=True only for development/testing, never live"
            )

    @staticmethod
    def _checked_date(decision_date: str) -> str:
        if not _DATE_RE.match(decision_date):
            raise ValueError(
                f"decision_date {decision_date!r} must be YYYYMMDD "
                "(a dashed/ISO date would silently break the PIT compare)"
            )
        return decision_date

    def active_theme_ids(self, decision_date: str) -> frozenset[str]:
        """Theme ids whose ``effective_from <= decision_date``."""
        d = self._checked_date(decision_date)
        return frozenset(t.theme_id for t in self.registry.active(d))

    def code_theme_ids(self, code: str, decision_date: str) -> tuple[str, ...]:
        """Active themes whose L3 set covers ``code`` on ``decision_date``.

        Deterministic order = registry order. Empty when the code is off every
        active theme (or has no PIT L3 that day).
        """
        d = self._checked_date(decision_date)
        l3_set = self.membership.l3_asof(code, d)
        if not l3_set:
            return ()
        return tuple(
            theme.theme_id
            for theme in self.registry.active(d)
            if l3_set.intersection(theme.sw_l3_codes)
        )

    def theme_coverage(self, code: str, decision_date: str) -> float | None:
        """Bottom-tier ``theme_coverage`` for the value score.

        ``None`` when the code has no PIT L3 that day (unknown / data gap → the
        value-score tier mean drops the component). Otherwise the max ThemeTier
        weight over the code's active themes ∈ [0, 1] (``0.0`` = on-market but
        off every national-strategy theme). Tier-weighted by construction, so the
        caller passes it straight into ``ValueScoreInputs.theme_coverage``.
        """
        d = self._checked_date(decision_date)
        l3_set = self.membership.l3_asof(code, d)
        if not l3_set:
            return None
        weights = [
            theme_tier_weight(theme.tier, self.tier_weights)
            for theme in self.registry.active(d)
            if l3_set.intersection(theme.sw_l3_codes)
        ]
        return max(weights) if weights else 0.0


__all__ = ["ThemeResolver"]
