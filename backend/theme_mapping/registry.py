"""Fail-closed loader for the ``config/policy_themes.yaml`` mapping (AF-001).

The mapping is the owner-frozen 关键人工 gate artifact. This loader validates the
whole file at construction (unknown tier, malformed date, empty L3 set, duplicate
id → raise) so a corrupt mapping can never silently mis-tilt the value sleeve.
``status: draft`` loads (for review/testing) but is surfaced via
``registry.frozen == False`` so consumers can require a frozen mapping.

Pure: reads a YAML file, builds frozen dataclasses, no LLM / no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from backend.theme_mapping.models import (
    PolicyTheme,
    PolicyThemeRegistry,
    tier_from_name,
)

DEFAULT_POLICY_THEMES_PATH = Path("config/policy_themes.yaml")

_VALID_STATUS = {"draft", "frozen"}


class PolicyThemeConfigError(ValueError):
    """Raised when the policy-theme mapping file is missing or malformed."""


def _require(mapping: dict[str, Any], key: str, ctx: str) -> Any:
    # A present-but-null YAML value (``key:`` with no value) is treated as
    # missing — otherwise ``str(None)`` would smuggle the literal "None" past the
    # non-empty checks for text fields (fail-closed).
    if key not in mapping or mapping[key] is None:
        raise PolicyThemeConfigError(f"{ctx}: missing required key {key!r}")
    return mapping[key]


def _parse_theme(raw: Any, index: int) -> PolicyTheme:
    ctx = f"themes[{index}]"
    if not isinstance(raw, dict):
        raise PolicyThemeConfigError(f"{ctx}: each theme must be a mapping")
    codes = _require(raw, "sw_l3_codes", ctx)
    if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
        raise PolicyThemeConfigError(f"{ctx}: sw_l3_codes must be a list of strings")
    try:
        return PolicyTheme(
            theme_id=str(_require(raw, "theme_id", ctx)),
            name_cn=str(_require(raw, "name_cn", ctx)),
            tier=tier_from_name(str(_require(raw, "tier", ctx))),
            effective_from=str(_require(raw, "effective_from", ctx)),
            policy_source=str(_require(raw, "policy_source", ctx)),
            sw_l3_codes=tuple(codes),
        )
    except ValueError as exc:  # model validation → config error (fail-closed)
        raise PolicyThemeConfigError(f"{ctx}: {exc}") from exc


def load_policy_theme_registry(
    path: Path | str = DEFAULT_POLICY_THEMES_PATH,
) -> PolicyThemeRegistry:
    """Load + validate the policy-theme mapping (fail-closed).

    Raises :class:`PolicyThemeConfigError` on a missing file, bad YAML, unknown
    ``status``, or any malformed theme.
    """
    p = Path(path)
    if not p.is_file():
        raise PolicyThemeConfigError(f"policy theme mapping not found: {p}")
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyThemeConfigError(f"invalid YAML in {p}: {exc}") from exc
    if not isinstance(doc, dict):
        raise PolicyThemeConfigError(f"{p}: top level must be a mapping")
    status = str(_require(doc, "status", str(p)))
    if status not in _VALID_STATUS:
        raise PolicyThemeConfigError(
            f"{p}: status {status!r} must be one of {sorted(_VALID_STATUS)}"
        )
    raw_themes = _require(doc, "themes", str(p))
    if not isinstance(raw_themes, list) or not raw_themes:
        raise PolicyThemeConfigError(f"{p}: themes must be a non-empty list")
    themes = tuple(_parse_theme(raw, i) for i, raw in enumerate(raw_themes))
    try:
        return PolicyThemeRegistry(
            version=str(_require(doc, "version", str(p))),
            frozen=(status == "frozen"),
            themes=themes,
        )
    except ValueError as exc:
        raise PolicyThemeConfigError(f"{p}: {exc}") from exc


__all__ = [
    "DEFAULT_POLICY_THEMES_PATH",
    "PolicyThemeConfigError",
    "load_policy_theme_registry",
]
