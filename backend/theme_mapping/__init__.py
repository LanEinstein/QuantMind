"""Policy→theme→申万 L3 mapping (AF-001 / QGR-3 ⑧ — shared single source of truth).

The long-term value sleeve (Phase AF) and the short-term quant gate's main-theme
dimension (QGR-4) both anchor candidates to national-strategy themes (the『十五五』
priorities). This package is the **one** place that mapping lives, so the two
lanes can never diverge:

* :mod:`backend.theme_mapping.models` — frozen ``PolicyTheme`` /
  ``PolicyThemeRegistry`` data structures.
* :mod:`backend.theme_mapping.registry` — fail-closed loader for the
  ``config/policy_themes.yaml`` mapping (schema-validated, owner-frozen).
* :mod:`backend.theme_mapping.sector_pit` — point-in-time 申万 L3 membership from
  ``index_member_all`` (in/out dated; no look-ahead).
* :mod:`backend.theme_mapping.resolver` — the deterministic resolver that, on a
  given date, gives a code its active themes + the ``theme_coverage`` ∈ [0, 1]
  bottom-tier value-score input.

Anti-hindsight is structural: a theme only tilts from its ``effective_from``
(policy release date) forward, and membership is reconstructed from the L3
in/out windows — never from today's classification.

Pure + deterministic + 0 LLM. May read ``backend.marketdata_snapshot`` (PIT
bytes) and ``backend.theme_research`` (ThemeTier + tier weights); must NOT import
``backend.{llm, agents, mirofish}``.
"""

from backend.theme_mapping.models import PolicyTheme, PolicyThemeRegistry
from backend.theme_mapping.registry import (
    PolicyThemeConfigError,
    load_policy_theme_registry,
)
from backend.theme_mapping.resolver import ThemeResolver
from backend.theme_mapping.sector_pit import SectorMembershipPIT

__all__ = [
    "PolicyTheme",
    "PolicyThemeConfigError",
    "PolicyThemeRegistry",
    "SectorMembershipPIT",
    "ThemeResolver",
    "load_policy_theme_registry",
]
