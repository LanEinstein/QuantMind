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

# AB-003-amendment-2026-06-14 §2.5 — the FROZEN code default used as the
# monotone baseline for safety-adjacent params at ACTIVATION time. The
# "stops only tighten" check (codex/D1) must compare a proposed value against
# this IMMUTABLE baseline, NOT against the last-pinned (possibly already
# evolved) value — otherwise a sequence of promotions could each "only
# tighten vs the previous step" yet creep all the way to ``clamp_max``,
# loosening a protective SELL one notch at a time. Anchoring on the frozen
# code default makes the loosest reachable value the original default, so
# evolution can only ever make a safety-adjacent stop TIGHTER than ship.
#
# Each entry is the literal default of the production dataclass that owns the
# parameter (verified at AE-006): a change to either side is a code change +
# amendment + restart. A safety-adjacent param WITHOUT a registered baseline
# is refused at activation (fail-closed) — its real default + consumer must
# be wired (and its baseline registered here) by a future amendment first.
FROZEN_BASELINE: MappingProxyType[str, float] = MappingProxyType(
    {
        # backend.monitoring.add_position.AddPositionConfig.atr_stop_mult
        "line2.atr_stop_mult": 2.0,
        # backend.position_thesis.config default time_stop_trade_days
        "line2.time_stop_trade_days": 30.0,
    }
)
"""Frozen monotone baselines for safety-adjacent params (§2.5).

A safety-adjacent param missing from this map is NOT activatable: see
:func:`validate_param_set_for_activation`."""

# AE-006 — the params with an END-TO-END wired runtime consumer. Lifting the
# blanket param refusal (the AB-era "no runtime consumption path → reject"
# guard) for the one wired param must NOT silently re-open activation for
# params that still have no consumer: pinning one would land in the lockfile,
# load into the RuntimeParamStore, and log "active" while changing nothing —
# the "silent no-op promotion is worse than a refusal" failure the old guard
# existed to prevent (and the worst case, a safety-adjacent stop). So the
# activation path admits ONLY params plumbed to a live consumer.
#
# Wired today (P1, AB-003-amendment-2026-06-14 §2.3):
#   * allocation.value_slot_quota → CandidateSelector via
#     selector_config_with_params (its take-effect is then gated on the
#     orthogonal AC-005 value-scoring feed, but the plumbing is complete).
# NOT wired (refused at activation until their consumer lands by amendment):
#   * selector.weight_* — needs the real factor-scoring layer (owner-gated,
#     same gate as the AE-005 backtest ScoreProvider; the screener composite
#     uses a 4-factor blend, not these 5 abstract weights).
#   * theme.tierN_weight — the theme research runtime layer is offline.
#   * line2.* — no store reader exists.
RUNTIME_CONSUMED_PARAMS: frozenset[str] = frozenset(
    {
        "allocation.value_slot_quota",
    }
)
"""Params with a wired, boot-reachable runtime consumer (AE-006).

The activation validator refuses any other param so a human pin can never be a
silent no-op. Wiring a new consumer + adding its name here is one amendment."""


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


def validate_param_set_for_activation(
    proposed: dict[str, float],
) -> ParamValidation:
    """Validate a candidate set on the ACTIVATION path (§2.5, fail-closed).

    Identical to :func:`validate_param_set` EXCEPT the monotone "stops only
    tighten" baseline for safety-adjacent params is forced to the IMMUTABLE
    :data:`FROZEN_BASELINE` (the frozen code default), never the last-pinned
    value. This is the single source of truth for staging
    (``write_next_boot_lock``), boot apply (``apply_pending_activation``) and
    the :class:`~backend.strategy_evolution.runtime_param_store.RuntimeParamStore`
    boot load, so the same proposed set is judged identically at every gate.

    It ALSO refuses any param outside :data:`RUNTIME_CONSUMED_PARAMS` — a param
    with no wired runtime consumer would land in the lockfile + store and log
    "active" while changing nothing (a silent no-op promotion, worse than a
    refusal). And a safety-adjacent param with no registered
    :data:`FROZEN_BASELINE` entry is a hard violation — refusing to activate it
    (rather than silently skipping the monotone check) keeps a yet-unwired
    safety knob from being loosened by a hand-crafted manifest.

    Raises:
        FrozenParamViolationError: a proposed name is in the frozen
            non-evolvable set (red-line class; propagated from
            :func:`validate_param_change`).
    """
    baseline: dict[str, float] = {}
    extra_violations: list[str] = []
    for name in proposed:
        # Consumed-param gate (skip frozen names — they raise below with the
        # stronger FrozenParamViolationError, no need for a redundant message).
        if (
            name not in RUNTIME_CONSUMED_PARAMS
            and name not in FROZEN_NON_EVOLVABLE
        ):
            extra_violations.append(
                f"{name}: no runtime consumer wired — activating it would be a "
                f"silent no-op (worse than a refusal); wire its consumer + add "
                f"it to RUNTIME_CONSUMED_PARAMS via an amendment first"
            )
        spec = EVOLVABLE_WHITELIST.get(name)
        if spec is None or not spec.safety_adjacent:
            continue
        if name not in FROZEN_BASELINE:
            extra_violations.append(
                f"{name}: safety-adjacent param has no registered frozen "
                f"baseline (§2.5) — cannot be activated until its real "
                f"default + consumer are wired by an amendment"
            )
            continue
        baseline[name] = FROZEN_BASELINE[name]

    result = validate_param_set(proposed, current=baseline)
    if not extra_violations:
        return result
    return ParamValidation(
        passed=False,
        violations=tuple(extra_violations) + result.violations,
    )


__all__ = [
    "EVOLVABLE_WHITELIST",
    "FROZEN_BASELINE",
    "FROZEN_NON_EVOLVABLE",
    "RUNTIME_CONSUMED_PARAMS",
    "SELECTOR_WEIGHT_SUM_TOLERANCE",
    "ClampKind",
    "EvolvableParamSpec",
    "FrozenParamViolationError",
    "ParamValidation",
    "TightenDirection",
    "validate_param_change",
    "validate_param_set",
    "validate_param_set_for_activation",
]
