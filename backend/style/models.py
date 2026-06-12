"""Style classification primitives (Phase AC-001).

A held position / candidate carries a deterministic ``style`` label —
``SHORT_TERM`` (the momentum-math edge of the current 5-factor stack) or
``VALUE`` (a name that cleared the three-tier value-line screen). The label is
**display-only**: it threads through the InstructionPlan reasoning context,
evidence text, the front-end and the Feishu message, and it conditions the
*soft* sell layer (take-profit band / time-stop / review cadence, AC-006) — but
it **never** changes a single hard-risk number (stop-loss, circuit breaker,
position-triple, 14-check, sellable volume). That invariant is nailed by the
AC-006 adversarial tests.

This module is a pure, import-isolated container (``backend/style/CLAUDE.md``):
no ``backend.{llm,agents,mirofish}`` import, deterministic same-input →
same-output so a classification can be replayed from a buy-time snapshot.

AC-001 builds the **container**; the value path activates once AC-003 supplies a
three-tier ``value_score``. Until then ``value_score`` is ``None`` for every
candidate and :func:`~backend.style.classifier.classify_style` returns
``SHORT_TERM`` — bit-identical to the pre-AC pure-quant behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StyleTag(StrEnum):
    """The deterministic style label landed at buy time.

    A two-value enum keeps the classification a total, replayable function.
    ``SHORT_TERM`` is the default (the current 5-factor momentum stack);
    ``VALUE`` is only assigned when the three-tier value-line score clears the
    gate *and* a deterministic thesis backs the hold (a value position must
    have a why-we-hold record).
    """

    SHORT_TERM = "short_term"
    VALUE = "value"


@dataclass(frozen=True)
class StyleInputs:
    """The deterministic inputs the style decision is derived from.

    Mirrors the amendment's input list (P0-8-amendment-2026-06-12 §1.1):
    the quant factor spectrum (momentum / volatility / trend), the three-tier
    ``value_score`` (None until AC-003), and whether a deterministic thesis can
    be derived for the name. Every field is a plain number / bool so the
    classification replays bit-exact from a buy-time snapshot — no LLM text, no
    wall-clock, no mutable state.
    """

    momentum_20d: float | None = None
    volatility_20d: float | None = None
    ma_ratio_5_20: float | None = None
    value_score: float | None = None
    """The AC-003 three-tier value-line composite (∈ [0, 1]); None pre-AC-003."""
    thesis_derivable: bool = True
    """Whether a deterministic PositionThesis can be derived for the hold."""


@dataclass(frozen=True)
class StyleClassifierConfig:
    """Runtime-immutable style-decision parameters (offline-tuned only).

    ``value_gate`` is the three-tier ``value_score`` threshold a name must clear
    to be labelled VALUE. It is a frozen constant here; a change is a
    decision-boundary move recalibrated offline via the P2-2 evolution whitelist
    + 45-day shadow + human gate (never hot-reloaded), mirroring the
    ThesisDerivationConfig discipline.
    """

    value_gate: float = 0.60


@dataclass(frozen=True)
class StyleClassification:
    """The deterministic style verdict + the replay-stable rationale.

    ``reason`` is a display-only, replay-stable string (no wall-clock, fixed
    float precision) — it is rendered for the owner, never parsed back into a
    numeric field.
    """

    style: StyleTag
    value_score: float | None
    value_gate: float
    thesis_derivable: bool
    feature_code_version: str
    reason: str


__all__ = [
    "StyleClassification",
    "StyleClassifierConfig",
    "StyleInputs",
    "StyleTag",
]
