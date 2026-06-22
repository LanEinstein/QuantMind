"""Frozen data structures for the policy→theme→申万 L3 mapping (AF-001).

The mapping is an owner-confirmed, git-frozen artifact (the 关键人工 gate): the
LLM never authors it and the runtime never mutates it. These types validate the
mapping's shape at construction so a malformed entry fails closed rather than
silently tilting the value sleeve toward the wrong names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.theme_research.sop_schema import ThemeTier

_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD (effective_from = policy release date)
_SW_L3_RE = re.compile(r"^\d{6}\.SI$")  # 申万 L3 index code, e.g. 850816.SI

# YAML tier label → ThemeTier (the driver hierarchy 国家事件>政策>技术>个股).
_TIER_BY_NAME: dict[str, ThemeTier] = {
    "national_event": ThemeTier.NATIONAL_EVENT,
    "policy": ThemeTier.POLICY,
    "tech": ThemeTier.TECH,
    "stock": ThemeTier.STOCK,
}


def tier_from_name(name: str) -> ThemeTier:
    """Map a YAML tier label to :class:`ThemeTier` (fail-closed on unknown)."""
    try:
        return _TIER_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown theme tier {name!r}; expected one of {sorted(_TIER_BY_NAME)}"
        ) from exc


@dataclass(frozen=True)
class PolicyTheme:
    """One national-strategy theme → its 申万 L3 constituents + policy anchor.

    ``effective_from`` is the **public release date** of the policy that
    establishes the theme; the resolver only tilts toward the theme on/after
    that date (anti-hindsight). ``sw_l3_codes`` are 申万 L3 index codes whose
    PIT membership (in/out dated) defines the theme's stock set.
    """

    theme_id: str
    name_cn: str
    tier: ThemeTier
    effective_from: str  # YYYYMMDD
    policy_source: str
    sw_l3_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.theme_id or not self.theme_id.strip():
            raise ValueError("theme_id must be non-empty")
        if not self.name_cn or not self.name_cn.strip():
            raise ValueError(f"theme {self.theme_id!r}: name_cn must be non-empty")
        if not isinstance(self.tier, ThemeTier):
            raise ValueError(f"theme {self.theme_id!r}: tier must be a ThemeTier")
        if not _DATE_RE.match(self.effective_from):
            raise ValueError(
                f"theme {self.theme_id!r}: effective_from {self.effective_from!r} "
                "must be YYYYMMDD"
            )
        if not self.policy_source or not self.policy_source.strip():
            raise ValueError(
                f"theme {self.theme_id!r}: policy_source must be non-empty"
            )
        if not self.sw_l3_codes:
            raise ValueError(f"theme {self.theme_id!r}: sw_l3_codes must be non-empty")
        for code in self.sw_l3_codes:
            if not _SW_L3_RE.match(code):
                raise ValueError(
                    f"theme {self.theme_id!r}: bad 申万 L3 code {code!r} "
                    "(expected NNNNNN.SI)"
                )

    def is_active(self, decision_date: str) -> bool:
        """Whether the theme tilts on ``decision_date`` (``effective_from <= d``)."""
        return self.effective_from <= decision_date


@dataclass(frozen=True)
class PolicyThemeRegistry:
    """Immutable set of policy themes (the frozen mapping).

    ``frozen`` = ``status: frozen`` in the YAML (owner-confirmed). A ``draft``
    registry is loadable for review/testing but consumers must treat it as
    non-authoritative until the owner freezes it.
    """

    version: str
    frozen: bool
    themes: tuple[PolicyTheme, ...]

    def __post_init__(self) -> None:
        if not self.themes:
            raise ValueError("policy theme registry must contain at least one theme")
        seen: set[str] = set()
        for theme in self.themes:
            if theme.theme_id in seen:
                raise ValueError(f"duplicate theme_id {theme.theme_id!r}")
            seen.add(theme.theme_id)

    def active(self, decision_date: str) -> tuple[PolicyTheme, ...]:
        """Themes whose ``effective_from <= decision_date`` (anti-hindsight)."""
        return tuple(t for t in self.themes if t.is_active(decision_date))

    def l3_to_themes(self) -> dict[str, tuple[str, ...]]:
        """Map each 申万 L3 code → the theme_ids that claim it (deterministic)."""
        out: dict[str, list[str]] = {}
        for theme in self.themes:
            for code in theme.sw_l3_codes:
                out.setdefault(code, []).append(theme.theme_id)
        return {l3: tuple(ids) for l3, ids in out.items()}


__all__ = [
    "PolicyTheme",
    "PolicyThemeRegistry",
    "tier_from_name",
]
