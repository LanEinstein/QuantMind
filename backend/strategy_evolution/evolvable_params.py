"""Frozen non-evolvable set + evolvable whitelist with immutable clamps
(AB-005 / P2-2-amendment-2026-06-12 §1.4/§1.5).

The safety boundary of self-evolution. Two locked structures:

* :data:`FROZEN_NON_EVOLVABLE` — the safety floor (amendment §1.4):
  any candidate change touching one of these names is rejected
  REGARDLESS of its score. Adversarial tests seed a "high-Sharpe"
  variant of a frozen parameter and assert rejection.
* :data:`EVOLVABLE_WHITELIST` — every evolvable parameter with an
  IMMUTABLE clamp interval (the clamp itself is module-constant code:
  changing it requires an amendment + commit + restart, never a
  promotion). Safety-adjacent parameters (anything that can DELAY a
  SELL) additionally carry the "stops only tighten" monotonic
  constraint inherited from the D1 amendments.

Everything here is pure data + pure validation — no IO, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

FROZEN_NON_EVOLVABLE: frozenset[str] = frozenset(
    {
        # Safety foundation (CLAUDE.md §2.0 — never evolvable).
        "safety.real_order_placement",
        "safety.feishu_human_gate_live",
        "safety.localhost_only",
        "safety.llm_write_permission_classes",
        "safety.instruction_plan_single_constructor",
        "safety.pit_reproducibility",
        "safety.risk_engine_pure_function",
        # Position triple (P0-7).
        "risk.max_single_stock_pct",
        "risk.max_total_position_pct",
        "risk.max_single_instruction_amount",
        # Slot cap (P0-7-amendment-2026-06-01).
        "risk.max_total_positions",
        # Circuit breaker quartet (P0-7).
        "risk.circuit_max_daily_orders",
        "risk.circuit_daily_loss_pct",
        "risk.circuit_consecutive_losses",
        "risk.circuit_cooldown_minutes",
        # Budget quartet (P1-7).
        "budget.daily_hard_cny",
        "budget.monthly_soft_cny",
        "budget.kimi_daily_cap_cny",
        "budget.soft_threshold_ratio",
        # Universe exclusion quartet (P0-9).
        "universe.exclude_st",
        "universe.exclude_star_market",
        "universe.exclude_bse",
        "universe.exclude_convertible_bonds",
        # RiskEngine 14-check existence/semantics (P0-7/P0-10).
        "risk.fourteen_check_set",
        # Reconciliation thresholds (P0-5).
        "reconciliation.cash_tolerance_cny",
        "reconciliation.cost_price_tolerance_cny",
        "reconciliation.volume_tolerance",
        # Mode switch (P0-1).
        "mode.feishu_interactive_enabled",
    }
)
"""Amendment §1.4 — the frozen floor. Names are canonical dotted ids;
the validator rejects ANY change whose name is in this set."""


class ClampKind(StrEnum):
    FLOAT = "float"
    INT = "int"


class TightenDirection(StrEnum):
    """Which way the "stops only tighten" monotone constraint points.

    ``DOWN`` — only decreases allowed (e.g. a take-profit delay knob);
    ``UP`` — only increases allowed (e.g. a stop level that must not
    move further from entry). Applied ONLY to safety-adjacent params.
    """

    DOWN = "down"
    UP = "up"


@dataclass(frozen=True)
class EvolvableParamSpec:
    """One whitelisted parameter and its immutable clamp."""

    name: str
    clamp_min: float
    clamp_max: float
    kind: ClampKind = ClampKind.FLOAT
    safety_adjacent: bool = False
    tighten_direction: TightenDirection | None = None
    group: str | None = None
    """Validation group: ``selector_weights`` (must sum to 1) /
    ``theme_tier_weights`` (descending order constraint)."""


_SPECS: tuple[EvolvableParamSpec, ...] = (
    # --- Line-2 trigger coefficients (amendment §1.5). Parameters that
    # can DELAY a protective SELL are safety-adjacent: tighter clamps +
    # the D1 "stops only tighten" monotone constraint.
    EvolvableParamSpec(
        name="line2.atr_stop_mult",
        clamp_min=1.0,
        clamp_max=4.0,
        safety_adjacent=True,
        tighten_direction=TightenDirection.DOWN,
    ),
    EvolvableParamSpec(
        name="line2.r_multiple",
        clamp_min=0.5,
        clamp_max=2.5,
    ),
    EvolvableParamSpec(
        name="line2.time_stop_trade_days",
        clamp_min=3,
        clamp_max=30,
        kind=ClampKind.INT,
        safety_adjacent=True,
        tighten_direction=TightenDirection.DOWN,
    ),
    EvolvableParamSpec(
        name="line2.drawdown_quantile",
        clamp_min=0.80,
        clamp_max=0.95,
        safety_adjacent=True,
        tighten_direction=TightenDirection.DOWN,
    ),
    EvolvableParamSpec(
        name="line2.strength_sell_threshold",
        clamp_min=0.5,
        clamp_max=2.0,
        safety_adjacent=True,
        tighten_direction=TightenDirection.DOWN,
    ),
    # --- Selector factor weights (normalisation group: sum == 1).
    EvolvableParamSpec(
        name="selector.weight_momentum",
        clamp_min=0.0,
        clamp_max=0.6,
        group="selector_weights",
    ),
    EvolvableParamSpec(
        name="selector.weight_volatility",
        clamp_min=0.0,
        clamp_max=0.6,
        group="selector_weights",
    ),
    EvolvableParamSpec(
        name="selector.weight_liquidity",
        clamp_min=0.0,
        clamp_max=0.6,
        group="selector_weights",
    ),
    EvolvableParamSpec(
        name="selector.weight_value",
        clamp_min=0.0,
        clamp_max=0.6,
        group="selector_weights",
    ),
    EvolvableParamSpec(
        name="selector.weight_quality",
        clamp_min=0.0,
        clamp_max=0.6,
        group="selector_weights",
    ),
    # --- Style slot quota (AC; integer domain 0..2).
    EvolvableParamSpec(
        name="allocation.value_slot_quota",
        clamp_min=0,
        clamp_max=2,
        kind=ClampKind.INT,
    ),
    # --- Theme tier weights (order group: tier1 >= ... >= tier4).
    EvolvableParamSpec(
        name="theme.tier1_weight",
        clamp_min=0.5,
        clamp_max=1.0,
        group="theme_tier_weights",
    ),
    EvolvableParamSpec(
        name="theme.tier2_weight",
        clamp_min=0.3,
        clamp_max=1.0,
        group="theme_tier_weights",
    ),
    EvolvableParamSpec(
        name="theme.tier3_weight",
        clamp_min=0.1,
        clamp_max=0.9,
        group="theme_tier_weights",
    ),
    EvolvableParamSpec(
        name="theme.tier4_weight",
        clamp_min=0.0,
        clamp_max=0.7,
        group="theme_tier_weights",
    ),
)

EVOLVABLE_WHITELIST: MappingProxyType[str, EvolvableParamSpec] = (
    MappingProxyType({spec.name: spec for spec in _SPECS})
)
"""Immutable name → spec map. There is no runtime mutation surface;
adding a parameter is a code change + amendment + restart."""

_TIER_ORDER = (
    "theme.tier1_weight",
    "theme.tier2_weight",
    "theme.tier3_weight",
    "theme.tier4_weight",
)

SELECTOR_WEIGHT_SUM_TOLERANCE = 1e-6


class FrozenParamViolationError(ValueError):
    """A candidate change touched the frozen non-evolvable set."""


@dataclass(frozen=True)
class ParamValidation:
    """Outcome of validating one candidate parameter set."""

    passed: bool
    violations: tuple[str, ...]


def validate_param_change(
    name: str,
    proposed: float,
    *,
    current: float | None = None,
) -> tuple[str, ...]:
    """Violations for one parameter change (empty tuple = clean).

    Frozen-set membership raises immediately (red-line class, never a
    soft violation); clamp/monotone/type checks return named strings.
    """
    if name in FROZEN_NON_EVOLVABLE:
        raise FrozenParamViolationError(
            f"{name} is in the frozen non-evolvable set "
            f"(P2-2-amendment-2026-06-12 §1.4) — no score can justify it"
        )
    spec = EVOLVABLE_WHITELIST.get(name)
    if spec is None:
        return (f"{name}: not in the evolvable whitelist",)

    violations: list[str] = []
    if not spec.clamp_min <= proposed <= spec.clamp_max:
        violations.append(
            f"{name}: {proposed} outside immutable clamp "
            f"[{spec.clamp_min}, {spec.clamp_max}]"
        )
    if spec.kind is ClampKind.INT and proposed != int(proposed):
        violations.append(f"{name}: {proposed} not an integer")
    if (
        spec.safety_adjacent
        and spec.tighten_direction is not None
        and current is not None
    ):
        loosened = (
            proposed > current
            if spec.tighten_direction is TightenDirection.DOWN
            else proposed < current
        )
        if loosened:
            violations.append(
                f"{name}: safety-adjacent — stops only tighten "
                f"(current {current}, proposed {proposed} loosens)"
            )
    return tuple(violations)


def validate_param_set(
    proposed: dict[str, float],
    *,
    current: dict[str, float] | None = None,
) -> ParamValidation:
    """Validate a full candidate parameter set (pure).

    Per-parameter checks + group constraints: selector weights present
    in the set must sum to 1 (when the whole group is present), theme
    tier weights must be non-increasing tier1→tier4.
    """
    currents = current or {}
    violations: list[str] = []
    for name, value in sorted(proposed.items()):
        violations.extend(
            validate_param_change(
                name, value, current=currents.get(name)
            )
        )

    selector_names = [
        n
        for n, spec in EVOLVABLE_WHITELIST.items()
        if spec.group == "selector_weights"
    ]
    proposed_selector = [n for n in selector_names if n in proposed]
    if proposed_selector:
        if set(proposed_selector) != set(selector_names):
            violations.append(
                "selector_weights: partial weight set — the whole "
                "group must be proposed together (normalisation)"
            )
        else:
            total = sum(proposed[n] for n in selector_names)
            if abs(total - 1.0) > SELECTOR_WEIGHT_SUM_TOLERANCE:
                violations.append(
                    f"selector_weights: sum {total:.6f} != 1.0"
                )

    proposed_tiers = [n for n in _TIER_ORDER if n in proposed]
    if proposed_tiers:
        if proposed_tiers != list(_TIER_ORDER):
            violations.append(
                "theme_tier_weights: partial tier set — all four "
                "tiers must be proposed together (order constraint)"
            )
        else:
            values = [proposed[n] for n in _TIER_ORDER]
            if any(
                a < b for a, b in zip(values, values[1:], strict=False)
            ):
                violations.append(
                    f"theme_tier_weights: order violated "
                    f"(need tier1>=tier2>=tier3>=tier4, got {values})"
                )

    return ParamValidation(
        passed=not violations, violations=tuple(violations)
    )


__all__ = [
    "EVOLVABLE_WHITELIST",
    "FROZEN_NON_EVOLVABLE",
    "SELECTOR_WEIGHT_SUM_TOLERANCE",
    "ClampKind",
    "EvolvableParamSpec",
    "FrozenParamViolationError",
    "ParamValidation",
    "TightenDirection",
    "validate_param_change",
    "validate_param_set",
]
