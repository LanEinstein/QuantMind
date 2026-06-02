"""Deterministic thesis-health evaluation (Phase W-001 / consumed by W-004).

Given a :class:`PositionThesis` and a current PIT observation, decide which
whitelist invalidation conditions are **broken** — a pure, replayable function
(same thesis + same observation → same verdict). The Line-2 monitoring
``THESIS_QUANT_BREAK`` trigger (W-004) calls this to turn a broken thesis into
SELL pressure; it adds the import only at the data level (the model), so the
monitoring layer stays zero-LLM.

A condition whose ``current_value`` is unavailable (e.g. no fresh Line-1 score
intraday) is **skipped** — never counted as broken (fail-closed: never fabricate
a SELL from missing data).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.models.position_thesis import (
    InvalidationTemplate,
    PositionThesis,
    ThesisHealth,
    ThesisInvalidationCondition,
)


@dataclass(frozen=True)
class ThesisObservation:
    """Current PIT values for the whitelist metrics (``None`` = unavailable)."""

    current_price: float | None = None
    holding_trade_days: int | None = None
    current_score: float | None = None


def _current_value(
    template: InvalidationTemplate, obs: ThesisObservation
) -> float | None:
    """Map a template to its observed value (``None`` when unavailable)."""
    if template is InvalidationTemplate.ANCHOR_DRAWDOWN:
        return obs.current_price
    if template is InvalidationTemplate.TIME_STOP:
        return None if obs.holding_trade_days is None else float(
            obs.holding_trade_days
        )
    if template is InvalidationTemplate.SCORE_DECAY:
        return obs.current_score
    return None


def evaluate_condition(
    condition: ThesisInvalidationCondition, obs: ThesisObservation
) -> bool | None:
    """Return ``True`` (broken) / ``False`` (intact) / ``None`` (unevaluable)."""
    value = _current_value(condition.template, obs)
    if value is None:
        return None
    return condition.is_broken(value)


@dataclass(frozen=True)
class ThesisHealthResult:
    """Deterministic health rollup for one thesis at one observation.

    ``health`` is BROKEN when ≥1 condition is broken, else INTACT — the
    deterministic path never returns WEAKENING (that is the LLM advisory's
    judgement in W-002). ``broken`` lists the offending conditions for the SELL
    rationale; ``evaluated`` counts the conditions that had data.
    """

    health: ThesisHealth
    broken: tuple[ThesisInvalidationCondition, ...]
    evaluated: int


def evaluate_thesis_health(
    thesis: PositionThesis, obs: ThesisObservation
) -> ThesisHealthResult:
    """Roll up the per-condition verdicts into a deterministic health result."""
    broken: list[ThesisInvalidationCondition] = []
    evaluated = 0
    for cond in thesis.invalidation_conditions:
        verdict = evaluate_condition(cond, obs)
        if verdict is None:
            continue
        evaluated += 1
        if verdict:
            broken.append(cond)
    health = ThesisHealth.BROKEN if broken else ThesisHealth.INTACT
    return ThesisHealthResult(
        health=health, broken=tuple(broken), evaluated=evaluated
    )


__all__ = [
    "ThesisHealthResult",
    "ThesisObservation",
    "evaluate_condition",
    "evaluate_thesis_health",
]
