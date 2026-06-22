"""Deterministic value-sleeve capital-allocation policy (AF-005).

Pure, total functions over total equity → (active?, value target capital,
per-sleeve caps). The current sub-¥50k / switch-off state resolves to "value
sleeve dormant", which the RiskEngine consumes as the unchanged single ≤5 pool
(byte-identical). No LLM, no IO beyond the one-shot config load, no trading-stack
import (redline: ``backend.sleeve_policy`` is a quant policy like
``backend.budget_policy``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from backend.style.models import StyleTag

DEFAULT_SLEEVE_POLICY_PATH = Path("config/sleeve_policy.yaml")


class SleevePolicyError(ValueError):
    """Raised when the sleeve-policy config violates an invariant (fail-closed)."""


class Sleeve(StrEnum):
    """The two capital sub-accounts."""

    SHORT = "short"  # fast-compounding ≤5-slot rotation sleeve
    VALUE = "value"  # long-term 埋伏 sleeve (active ≥ ¥50k)


@dataclass(frozen=True)
class SleeveCaps:
    """Per-sleeve max distinct positions (consumed by RiskEngine check #6)."""

    short_max_positions: int
    value_max_positions: int

    def cap_for(self, sleeve: Sleeve, value_active: bool) -> int:
        """Position cap for ``sleeve``.

        When the value sleeve is dormant the value cap is 0 (no value sub-account
        exists yet) and the short cap is the single ≤5 pool — byte-identical to
        the pre-sleeve world. When active, each sleeve gets its own cap.
        """
        if sleeve is Sleeve.SHORT:
            return self.short_max_positions
        return self.value_max_positions if value_active else 0


@dataclass(frozen=True)
class GlidePoint:
    """One glide-path breakpoint: value target weight at/above an equity level."""

    min_equity_yuan: float
    value_weight: float


@dataclass(frozen=True)
class SleevePolicyConfig:
    """Immutable, validated sleeve-allocation config (runtime-immutable)."""

    enabled: bool
    activate_total_equity_yuan: float
    short_working_floor_yuan: float
    caps: SleeveCaps
    glide_path: tuple[GlidePoint, ...]

    def __post_init__(self) -> None:
        if self.activate_total_equity_yuan <= 0:
            raise SleevePolicyError("activate_total_equity_yuan must be > 0")
        if not (0 <= self.short_working_floor_yuan <= self.activate_total_equity_yuan):
            raise SleevePolicyError(
                "short_working_floor_yuan must be in [0, activate_total_equity_yuan]"
            )
        if self.caps.short_max_positions < 1 or self.caps.value_max_positions < 1:
            raise SleevePolicyError("sleeve caps must be >= 1")
        if not self.glide_path:
            raise SleevePolicyError("glide_path must be non-empty")
        prev_eq = -1.0
        prev_w = -1.0
        for pt in self.glide_path:
            if not (0.0 <= pt.value_weight <= 1.0):
                raise SleevePolicyError(f"glide weight {pt.value_weight} not in [0,1]")
            if pt.min_equity_yuan <= prev_eq:
                raise SleevePolicyError("glide_path min_equity must strictly increase")
            if pt.value_weight < prev_w:
                raise SleevePolicyError("glide_path weights must be non-decreasing")
            prev_eq, prev_w = pt.min_equity_yuan, pt.value_weight
        if self.glide_path[0].min_equity_yuan < self.activate_total_equity_yuan:
            raise SleevePolicyError(
                "first glide breakpoint must be >= activate_total_equity_yuan"
            )


class SleevePolicy:
    """Deterministic sleeve activation + target-capital + caps (pure)."""

    def __init__(self, config: SleevePolicyConfig) -> None:
        self._config = config

    @property
    def config(self) -> SleevePolicyConfig:
        return self._config

    def is_value_sleeve_active(
        self, total_equity_yuan: float, *, latched: bool = False
    ) -> bool:
        """Whether the value sleeve is live.

        Requires the master switch AND (equity past the ¥50k trigger OR a prior
        latch). ``latched`` is the persisted one-way flag (the caller stores it):
        once activated, a later dip below the trigger keeps the sleeve active so
        埋伏 positions are never force-liquidated — only new adds stop (the glide
        target shrinks to 0 below the lowest breakpoint).
        """
        if not self._config.enabled:
            return False
        return latched or total_equity_yuan >= self._config.activate_total_equity_yuan

    def assign_sleeve(self, style: StyleTag) -> Sleeve:
        """Map a candidate's style to its capital sleeve (VALUE→value, else short)."""
        return Sleeve.VALUE if style is StyleTag.VALUE else Sleeve.SHORT

    def _glide_weight(self, total_equity_yuan: float) -> float:
        """Value target weight = the highest breakpoint with ``min_equity <= eq``."""
        weight = 0.0
        for pt in self._config.glide_path:
            if total_equity_yuan >= pt.min_equity_yuan:
                weight = pt.value_weight
        return weight

    def value_target_capital_yuan(
        self, total_equity_yuan: float, *, latched: bool = False
    ) -> float:
        """Target ¥ for the value sleeve (0 when dormant).

        The glide weight scales equity, but the short working floor is reserved
        first, so ``value_target = min(weight * equity, max(0, equity - floor))``.
        A latched-but-below-trigger account glides to 0 (stop adding, hold core).
        """
        if not self.is_value_sleeve_active(total_equity_yuan, latched=latched):
            return 0.0
        weighted = self._glide_weight(total_equity_yuan) * total_equity_yuan
        headroom = max(0.0, total_equity_yuan - self._config.short_working_floor_yuan)
        return min(weighted, headroom)

    def cap_for(
        self, sleeve: Sleeve, total_equity_yuan: float, *, latched: bool = False
    ) -> int:
        """Per-sleeve position cap given current activation."""
        active = self.is_value_sleeve_active(total_equity_yuan, latched=latched)
        return self._config.caps.cap_for(sleeve, active)

    def position_admissible(
        self,
        sleeve: Sleeve,
        held_in_sleeve: int,
        total_equity_yuan: float,
        *,
        latched: bool = False,
    ) -> bool:
        """Whether a NEW distinct position in ``sleeve`` fits its cap.

        The pure helper RiskEngine check #6 will call: a new BUY is admissible
        only if the sleeve currently holds fewer than its cap. (Adds to a held
        code and SELLs are handled by the caller and never reach here.)
        """
        return held_in_sleeve < self.cap_for(sleeve, total_equity_yuan, latched=latched)


def _require_number(raw: dict[str, Any], key: str, *, minimum: float) -> float:
    """Fail-closed numeric read: present, real number (not bool), >= minimum.

    Rejecting bool matters because YAML ``true`` is an ``int`` (1) — a relaxed
    floor/threshold must never slip through as a coerced bool/string.
    """
    val = raw.get(key)
    if not isinstance(val, int | float) or isinstance(val, bool) or val < minimum:
        raise SleevePolicyError(f"sleeve_policy.{key} must be a number >= {minimum}")
    return float(val)


def _parse_glide(raw: Any) -> tuple[GlidePoint, ...]:
    if not isinstance(raw, list) or not raw:
        raise SleevePolicyError("sleeve_policy.glide_path must be a non-empty list")
    points: list[GlidePoint] = []
    for entry in raw:
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not all(
                isinstance(x, int | float) and not isinstance(x, bool) for x in entry
            )
        ):
            raise SleevePolicyError(
                "each glide_path entry must be [min_equity_yuan, value_weight]"
            )
        points.append(GlidePoint(float(entry[0]), float(entry[1])))
    return tuple(points)


def load_sleeve_policy_config(
    yaml_path: str | Path = DEFAULT_SLEEVE_POLICY_PATH,
) -> SleevePolicyConfig:
    """Load + validate ``config/sleeve_policy.yaml`` (fail-closed)."""
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"sleeve policy config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise SleevePolicyError("sleeve_policy.yaml top level must be a mapping")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise SleevePolicyError("sleeve_policy.enabled must be a bool")
    caps_raw = raw.get("caps")
    if not isinstance(caps_raw, dict):
        raise SleevePolicyError("sleeve_policy.caps must be a mapping")
    short_cap = caps_raw.get("short_max_positions")
    value_cap = caps_raw.get("value_max_positions")
    if not isinstance(short_cap, int) or isinstance(short_cap, bool) or short_cap < 1:
        raise SleevePolicyError("caps.short_max_positions must be a positive int")
    if not isinstance(value_cap, int) or isinstance(value_cap, bool) or value_cap < 1:
        raise SleevePolicyError("caps.value_max_positions must be a positive int")
    return SleevePolicyConfig(
        enabled=enabled,
        activate_total_equity_yuan=_require_number(
            raw, "activate_total_equity_yuan", minimum=1.0
        ),
        short_working_floor_yuan=_require_number(
            raw, "short_working_floor_yuan", minimum=0.0
        ),
        caps=SleeveCaps(short_cap, value_cap),
        glide_path=_parse_glide(raw.get("glide_path")),
    )
