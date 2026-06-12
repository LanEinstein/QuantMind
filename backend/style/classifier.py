"""Deterministic StyleClassifier (Phase AC-001).

:func:`classify_style` is a pure, total function: same :class:`StyleInputs` +
same :class:`StyleClassifierConfig` → same :class:`StyleClassification`
(bit-exact), so a buy-time classification can be replayed offline. No
``backend.{llm,agents,mirofish}`` import — the value-line decision is computed
entirely from deterministic PIT features + the human-pinned theme artifact
(both upstream of this module), never from LLM text.

Decision (AC-001 container; the value path lights up with AC-003's
``value_score``):

* ``VALUE`` iff the three-tier ``value_score`` is present + finite, clears the
  ``value_gate``, **and** a deterministic thesis backs the hold
  (``thesis_derivable``). A value position must have a why-we-hold record — a
  high score with no derivable thesis stays SHORT_TERM (fail-closed toward the
  more conservative soft layer).
* ``SHORT_TERM`` otherwise. Until AC-003 supplies ``value_score`` every name
  classifies SHORT_TERM, bit-identical to the pre-AC pure-quant path.
"""

from __future__ import annotations

import math

from backend.style.models import (
    StyleClassification,
    StyleClassifierConfig,
    StyleInputs,
    StyleTag,
)

STYLE_FEATURE_CODE_VERSION = "style.classifier/v1"
"""Pinned classification-code version; bump on any decision-maths change so a
stale replay fails closed (mirrors screening.factors / position_thesis pins)."""


def _fmt(value: float | None) -> str:
    """Replay-stable float rendering for the display-only ``reason``."""
    if value is None:
        return "na"
    if not math.isfinite(value):
        return "nan"
    return f"{value:.4f}"


def classify_style(
    inputs: StyleInputs,
    config: StyleClassifierConfig | None = None,
) -> StyleClassification:
    """Classify one candidate / holding into a deterministic :class:`StyleTag`.

    Pure + total: a missing / non-finite ``value_score`` (or a name with no
    derivable thesis) yields ``SHORT_TERM`` — never raises, never fabricates a
    VALUE label from a dirty number.
    """
    cfg = config or StyleClassifierConfig()
    score = inputs.value_score
    score_ok = score is not None and math.isfinite(score)
    clears_gate = score_ok and score >= cfg.value_gate  # type: ignore[operator]
    is_value = bool(clears_gate and inputs.thesis_derivable)
    style = StyleTag.VALUE if is_value else StyleTag.SHORT_TERM

    if is_value:
        reason = (
            f"value: score={_fmt(score)}>=gate={_fmt(cfg.value_gate)}, "
            f"thesis_derivable=true"
        )
    elif not score_ok:
        reason = (
            f"short_term: no value_score (mom={_fmt(inputs.momentum_20d)}, "
            f"vol={_fmt(inputs.volatility_20d)}, ma={_fmt(inputs.ma_ratio_5_20)})"
        )
    elif not clears_gate:
        reason = f"short_term: score={_fmt(score)}<gate={_fmt(cfg.value_gate)}"
    else:
        reason = f"short_term: score={_fmt(score)} but no derivable thesis"

    return StyleClassification(
        style=style,
        value_score=score if score_ok else None,
        value_gate=cfg.value_gate,
        thesis_derivable=inputs.thesis_derivable,
        feature_code_version=STYLE_FEATURE_CODE_VERSION,
        reason=reason,
    )


__all__ = [
    "STYLE_FEATURE_CODE_VERSION",
    "classify_style",
]
