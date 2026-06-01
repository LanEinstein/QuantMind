"""Deterministic ≤5-slot portfolio rotation (Phase V).

Pure-quant upstream layer that decides *whether* to rotate one held position
out for a stronger challenger when the portfolio is full (≤5). It only proposes
+ records intent — it never constructs an :class:`InstructionPlan` (R0 §4) and
never imports ``backend.{llm,agents,mirofish}`` (redline ``[V-002]``). The
RiskEngine 14-check stays the independent authority; this layer feeds the
builder, it does not replace any check.

Governance: ``docs/decisions/P0-7-amendment-2026-06-01-five-slot-rotation.md`` +
R0 §4 (single construction point). Module contract: ``CLAUDE.md`` in this dir.
"""

from __future__ import annotations

from backend.slot_portfolio.policy import (
    ChurnConfig,
    ExpiryConfig,
    RotationPolicyConfig,
    RotationProposal,
    load_rotation_policy_config,
    propose_rotation,
)
from backend.slot_portfolio.rotation_intent import (
    ChurnGateInputs,
    ExpiryOutcome,
    ExpiryOutcomeKind,
    RotationEvent,
    RotationEventType,
    RotationGateResult,
    RotationIntent,
    RotationIntentError,
    RotationIntentStore,
    apply_churn_gates,
    build_intent_id,
    build_rotation_intent,
    compute_expires_at,
    is_expired,
    resolve_expiry,
)
from backend.slot_portfolio.scoring import (
    SHIP_FIRST_SCORE_COMPONENTS,
    ChallengerMargin,
    ChallengerMarginConfig,
    ChallengerState,
    IncumbentState,
    IncumbentWeakConfig,
    IncumbentWeakness,
    SlotPortfolioError,
    evaluate_challenger_margin,
    evaluate_incumbent_weakness,
)

__all__ = [
    "SHIP_FIRST_SCORE_COMPONENTS",
    "ChallengerMargin",
    "ChallengerMarginConfig",
    "ChallengerState",
    "ChurnConfig",
    "ChurnGateInputs",
    "ExpiryConfig",
    "ExpiryOutcome",
    "ExpiryOutcomeKind",
    "IncumbentState",
    "IncumbentWeakConfig",
    "IncumbentWeakness",
    "RotationEvent",
    "RotationEventType",
    "RotationGateResult",
    "RotationIntent",
    "RotationIntentError",
    "RotationIntentStore",
    "RotationPolicyConfig",
    "RotationProposal",
    "SlotPortfolioError",
    "apply_churn_gates",
    "build_intent_id",
    "build_rotation_intent",
    "compute_expires_at",
    "evaluate_challenger_margin",
    "evaluate_incumbent_weakness",
    "is_expired",
    "load_rotation_policy_config",
    "propose_rotation",
    "resolve_expiry",
]
