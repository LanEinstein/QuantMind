"""Deterministic invalidation-threshold derivation (Phase W-001).

The heart of the direction-② red line: the buy-time thesis pillars are LLM
**text**, but every machine-checkable invalidation threshold is computed **here**
— a pure function of the buy-time snapshot (price + score + dates) with **no**
LLM input whatsoever. :func:`build_position_thesis` takes the pillar text as an
opaque payload and never reads it when deriving thresholds, so changing the
pillar wording cannot change a single threshold (the W-001 adversarial test).

Pure + import-isolated (``backend/position_thesis/CLAUDE.md``): no
``backend.{llm,agents,mirofish}`` import, no ``InstructionPlan`` construction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from backend.models.position_thesis import (
    Comparator,
    InvalidationTemplate,
    PositionThesis,
    ThesisInvalidationCondition,
)
from backend.position_thesis.config import (
    FEATURE_CODE_VERSION,
    ThesisDerivationConfig,
)


class ThesisDerivationError(ValueError):
    """Raised when the buy-time snapshot is too dirty to derive a thesis."""


@dataclass(frozen=True)
class ThesisEntrySnapshot:
    """The non-LLM buy-time inputs the thresholds are derived from."""

    entry_price: float
    entry_score: float
    trade_date: str


def derive_invalidation_conditions(
    snapshot: ThesisEntrySnapshot,
    config: ThesisDerivationConfig | None = None,
) -> tuple[ThesisInvalidationCondition, ...]:
    """Derive the fixed whitelist invalidation conditions (deterministic).

    Returns one condition per whitelist template, in locked order
    (ANCHOR_DRAWDOWN, TIME_STOP, SCORE_DECAY). The set is computed **independent
    of the pillar text** — the stronger red-line position than a per-pillar
    mapping (the LLM can never influence a metric / comparator / threshold).

    Raises:
        ThesisDerivationError: ``entry_price`` is non-finite / non-positive (a
            thesis must never be built on a dirty fill price — fail-closed).
    """
    cfg = config or ThesisDerivationConfig()
    if not math.isfinite(snapshot.entry_price) or snapshot.entry_price <= 0:
        raise ThesisDerivationError(
            f"entry_price {snapshot.entry_price!r} must be finite and > 0"
        )
    if not math.isfinite(snapshot.entry_score):
        raise ThesisDerivationError(
            f"entry_score {snapshot.entry_score!r} must be finite"
        )

    anchor_floor = snapshot.entry_price * (1.0 - cfg.anchor_drawdown_pct)
    # Relative decay defined for a signed score: threshold = score − pct·|score|.
    score_floor = snapshot.entry_score - cfg.score_decay_pct * abs(
        snapshot.entry_score
    )
    return (
        ThesisInvalidationCondition(
            template=InvalidationTemplate.ANCHOR_DRAWDOWN,
            metric_name="price",
            comparator=Comparator.LT,
            threshold=round(anchor_floor, 6),
            anchor=round(snapshot.entry_price, 6),
            feature_code_version=FEATURE_CODE_VERSION,
        ),
        ThesisInvalidationCondition(
            template=InvalidationTemplate.TIME_STOP,
            metric_name="holding_trade_days",
            comparator=Comparator.GT,
            threshold=float(cfg.time_stop_trade_days),
            anchor=0.0,
            feature_code_version=FEATURE_CODE_VERSION,
        ),
        ThesisInvalidationCondition(
            template=InvalidationTemplate.SCORE_DECAY,
            metric_name="line1_score",
            comparator=Comparator.LT,
            threshold=round(score_floor, 6),
            anchor=round(snapshot.entry_score, 6),
            feature_code_version=FEATURE_CODE_VERSION,
        ),
    )


def build_position_thesis(
    *,
    instruction_id: str,
    signal_id: str,
    stock_code: str,
    stock_name: str,
    created_at: datetime,
    trade_date: str,
    pillars: tuple[str, ...],
    entry_price: float,
    entry_score: float,
    snapshot_id: str,
    evidence_ids: tuple[str, ...] = (),
    catalyst_window_end: datetime | None = None,
    config: ThesisDerivationConfig | None = None,
) -> PositionThesis:
    """Assemble a :class:`PositionThesis` from the buy-time context.

    ``pillars`` is the LLM advisory text (opaque here); the invalidation
    thresholds are derived deterministically from ``entry_price`` /
    ``entry_score`` only. The returned thesis carries the replay references
    (``signal_id`` / ``snapshot_id`` / ``feature_code_version`` /
    ``evidence_ids``) so a consumer can rebuild the buy-time feature inputs.
    """
    cfg = config or ThesisDerivationConfig()
    conditions = derive_invalidation_conditions(
        ThesisEntrySnapshot(
            entry_price=entry_price,
            entry_score=entry_score,
            trade_date=trade_date,
        ),
        cfg,
    )
    return PositionThesis(
        instruction_id=instruction_id,
        signal_id=signal_id,
        stock_code=stock_code,
        stock_name=stock_name,
        created_at=created_at,
        trade_date=trade_date,
        pillars=pillars,
        invalidation_conditions=conditions,
        time_stop_trade_days=cfg.time_stop_trade_days,
        catalyst_window_end=catalyst_window_end,
        evidence_ids=evidence_ids,
        entry_price=entry_price,
        entry_score=entry_score,
        snapshot_id=snapshot_id,
        feature_code_version=FEATURE_CODE_VERSION,
    )


__all__ = [
    "ThesisDerivationError",
    "ThesisEntrySnapshot",
    "build_position_thesis",
    "derive_invalidation_conditions",
]
