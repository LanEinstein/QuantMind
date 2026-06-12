"""PositionThesis — the persisted "why we bought" record (Phase W-001).

The missing primitive (line-strategy design draft 方向②): when Line-1 routes a
BUY the system records *why* it bought, in a shape that lets two later consumers
reason about whether the thesis still holds:

* **W-002 LLM advisory review** (post-close, orchestration layer) compares the
  current evidence against the original ``pillars`` text → intact / weakening /
  broken — *evidence-only*, never a decision.
* **W-004 deterministic ``THESIS_QUANT_BREAK``** (Line-2 monitoring, zero-LLM)
  evaluates the ``invalidation_conditions`` against PIT market data → a SELL
  through the single construction point.

Two-layer split (P0-10-amendment-line2-2026-06-01 §1.1) — the central red-line
tension of direction ②:

* ``pillars`` are **LLM text** (the fund_manager / analyst reasoning). P0-10
  permits the LLM to write free-text reasoning, so storing it here is compliant.
* ``invalidation_conditions`` are **deterministic** — each threshold is derived
  by a whitelist quant template (:mod:`backend.position_thesis.derivation`) from
  the buy-time snapshot *only*. The LLM pillar text **never** picks an indicator,
  a comparator, or a threshold (codex round-1: "LLM 选阈值 = 把语义偷渡进零 LLM
  SELL 路径"). The two layers are deliberately **decoupled** — the deterministic
  set is computed independent of the pillar text — which is the stronger
  red-line position than a per-pillar mapping that could leak LLM influence.

This model is **not** an :class:`~backend.models.instruction.InstructionPlan`
(R0 §4 / M-004 single construction point stays intact): it carries no
``side`` / ``volume`` / ``limit_price`` / ``RiskCheckSummary`` order field, only
a factual ``entry_price`` anchor + replay references. A PositionThesis can never
be turned into an order; the SELL it may eventually justify is built downstream
by ``instruction_plan_builder`` from deterministic inputs.

It is **explicitly persisted at buy time** (root-cause fix for P-006: the
2026-05-31 take-profit gate was rejected for trying to *reconstruct* a thesis
from ``broker_events``, which lack ``evidence_ids`` — too fragile). The
:class:`~backend.position_thesis.store.PositionThesisStore` writes it once when
the BUY routes; the consumers read it, never re-derive it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.evidence import validate_evidence_id
from backend.style.models import StyleTag

# Mirror the InstructionPlan id pattern (P0-3 §1.2) so a thesis links to a
# canonical instruction_id and a malformed link fails closed at construction.
_INSTRUCTION_ID_PATTERN = r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$"

MIN_PILLARS = 3
MAX_PILLARS = 5
"""3–5 buy-logic pillars (P0-10-amendment-line2-2026-06-01 §1.1)."""


class Comparator(StrEnum):
    """Deterministic crossing direction for an invalidation condition.

    ``LT`` → broken when ``current_value < threshold`` (e.g. price below a
    drawdown floor); ``GT`` → broken when ``current_value > threshold`` (e.g.
    holding days past a time-stop). A two-value enum keeps the comparison a
    pure, replayable function — never an LLM-chosen operator.
    """

    LT = "lt"
    GT = "gt"


class InvalidationTemplate(StrEnum):
    """Locked whitelist of deterministic quant invalidation templates.

    Adding a template is a decision-boundary change — it requires a
    ``P0-10-amendment-line2-*`` before any code change (codex round-1: only
    pre-approved whitelist templates, never an LLM-selected metric/threshold).
    Each template maps to a single ``current_value`` the consumer can compute
    from PIT data alone:

    * ``ANCHOR_DRAWDOWN`` — live/close price vs ``entry_price × (1 − pct)``
      (``LT``). Price-based; computable intraday + daily.
    * ``TIME_STOP`` — holding trading-days vs ``time_stop_trade_days`` (``GT``).
      Date-based; fires once the catalyst window elapses without the thesis
      playing out.
    * ``SCORE_DECAY`` — the holding's fresh Line-1 composite score vs
      ``entry_score × (1 − pct)`` (``LT``). Needs a re-screen, so it is a daily
      signal; a consumer without a fresh score skips it (fail-closed = not
      broken — never fabricate a break).
    """

    ANCHOR_DRAWDOWN = "anchor_drawdown"
    TIME_STOP = "time_stop"
    SCORE_DECAY = "score_decay"


class ThesisHealth(StrEnum):
    """Health verdict for a thesis (shared by the LLM advisory + quant paths)."""

    INTACT = "intact"
    WEAKENING = "weakening"
    BROKEN = "broken"


class ThesisInvalidationCondition(BaseModel):
    """One deterministic, machine-checkable invalidation threshold.

    Frozen + strict + ``extra='forbid'``: the derivation module is the single
    writer, and a future refactor that tried to smuggle an LLM-chosen field
    here would fail validation at construction. ``is_broken`` is the pure
    predicate both the W-001 evaluation rollup and the W-004 monitoring trigger
    call, so the crossing maths has one source of truth.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    template: InvalidationTemplate
    metric_name: str = Field(min_length=1, max_length=64)
    comparator: Comparator
    threshold: float
    anchor: float
    """The buy-time baseline the threshold was derived from (entry price /
    entry score / 0 for date-based). Audit + replay only — never re-read as an
    order field."""
    feature_code_version: str = Field(min_length=1, max_length=64)

    def is_broken(self, current_value: float) -> bool:
        """Return ``True`` iff ``current_value`` crosses the threshold.

        Pure + total: a non-finite ``current_value`` is treated as **not
        broken** (fail-closed — never fabricate a SELL from a dirty number).
        """
        import math

        if not math.isfinite(current_value):
            return False
        if self.comparator is Comparator.LT:
            return current_value < self.threshold
        return current_value > self.threshold


class PositionThesis(BaseModel):
    """The persisted buy-time thesis for one held position.

    Frozen + strict + ``extra='forbid'`` (P0-3 §2 redline 12 discipline). The
    model intentionally carries **no** order/decision field — only a factual
    ``entry_price`` anchor + the replay references (``signal_id`` / ``snapshot_id``
    / ``feature_code_version`` / ``evidence_ids``) needed to reconstruct the
    buy-time feature inputs (R0 §3 PIT).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    instruction_id: str = Field(pattern=_INSTRUCTION_ID_PATTERN)
    """The canonical BUY instruction_id this thesis explains (the join key)."""
    signal_id: str = Field(min_length=1, max_length=128)
    """Line-1 run signal_id — the SignalInputManifest lookup key (replay)."""
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str = Field(min_length=1, max_length=64)
    created_at: datetime
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    pillars: tuple[str, ...]
    """3–5 buy-logic pillars — LLM advisory text (fund_manager / analysts)."""
    invalidation_conditions: tuple[ThesisInvalidationCondition, ...] = Field(
        min_length=1, max_length=8
    )
    """Deterministic whitelist invalidation thresholds (no LLM influence)."""

    time_stop_trade_days: int = Field(ge=1, le=500)
    catalyst_window_end: datetime | None = None

    evidence_ids: tuple[str, ...] = ()
    """Original buy-time evidence references (validated prefixes; may be empty
    on the current Line-1 path which does not yet collect evidence)."""

    entry_price: float = Field(gt=0)
    """The buy-time fill/limit anchor — a factual price, NOT an order field."""
    entry_score: float
    """The buy-time Line-1 composite score (SCORE_DECAY anchor)."""

    snapshot_id: str = Field(min_length=1, max_length=64)
    """The PIT market-frame snapshot_id consumed at buy (offline replay)."""
    feature_code_version: str = Field(min_length=1, max_length=64)
    """Pinned derivation-code version so a stale thesis replay fails closed."""

    style: StyleTag | None = None
    """The deterministic buy-time style label (AC-001). ``None`` on legacy
    theses written before AC-001 (additive, backward-compatible) and on the
    pure-quant path until AC-003 lights up the value score. Display-only +
    soft-layer-only — it never changes a hard-risk number (AC-006 invariant)."""

    @model_validator(mode="after")
    def _check(self) -> PositionThesis:
        if not (MIN_PILLARS <= len(self.pillars) <= MAX_PILLARS):
            raise ValueError(
                f"PositionThesis requires {MIN_PILLARS}-{MAX_PILLARS} pillars, "
                f"got {len(self.pillars)}"
            )
        for pillar in self.pillars:
            if not pillar.strip():
                raise ValueError("PositionThesis pillar text must be non-empty")
            if len(pillar) > 2048:
                raise ValueError("PositionThesis pillar text exceeds 2048 chars")
        for eid in self.evidence_ids:
            validate_evidence_id(eid)  # raises on an unknown / malformed prefix
        return self


__all__ = [
    "MAX_PILLARS",
    "MIN_PILLARS",
    "Comparator",
    "InvalidationTemplate",
    "PositionThesis",
    "StyleTag",
    "ThesisHealth",
    "ThesisInvalidationCondition",
]
