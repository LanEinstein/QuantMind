"""Per-style soft-layer parameters (Phase AC-006).

The style label (``SHORT_TERM`` / ``VALUE``, AC-001) conditions ONLY the **soft**
sell layer — *when to actively harvest a gain*, never *when you must escape*. A
VALUE hold (a name on a real trend with capital recognition + ≥2 logics) is given
a **wider take-profit band** so it can run, instead of being clipped at the
SHORT_TERM momentum target.

Red-line invariant (P0-8-amendment-2026-06-12 §1.5, codex P0-8 — nailed by the
AC-006 adversarial tests): style is **never** allowed to touch a hard protection.
The drawdown stop, the ATR trailing stop, ``THESIS_QUANT_BREAK``, the hard-cap
weight trim, the circuit breaker, the position-triple, the 14-check, the sellable
volume and the single construction point are all **style-invariant**. This module
can only ever *widen* a take-profit target (``value_take_profit_r_mult ≥ 1.0``) —
it can never tighten a stop, and by construction it is wired into the take-profit
band alone (see :func:`evaluate_intraday_sell_intents`).

Pure, deterministic, 0 LLM, import-isolated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backend.style import StyleTag


@dataclass(frozen=True)
class StyleSoftConfig:
    """Per-style soft-layer multipliers (offline-tuned, runtime-immutable).

    ``value_take_profit_r_mult`` widens a VALUE hold's take-profit target
    (``effective_r × mult``). Constrained ``>= 1.0`` at construction so the value
    style can only let a winner run **longer**, never trigger an earlier exit —
    and it is applied to the take-profit band only, never to any protective stop.
    """

    value_take_profit_r_mult: float = 1.5

    def __post_init__(self) -> None:
        m = self.value_take_profit_r_mult
        if not isinstance(m, int | float) or not math.isfinite(m) or m < 1.0:
            raise ValueError(
                f"value_take_profit_r_mult must be a finite float >= 1.0 "
                f"(value style only widens TP, never tightens), got {m!r}"
            )


def style_take_profit_r_multiple(
    base_r: float,
    style: StyleTag | str | None,
    config: StyleSoftConfig | None,
) -> float:
    """Style-adjusted take-profit r_multiple (soft band only).

    Returns ``base_r`` unchanged for SHORT_TERM / unknown / absent style or when
    ``config`` is None — so the pre-AC path is bit-identical. A VALUE hold gets
    ``base_r × value_take_profit_r_mult`` (a wider band, ≥ base_r). Never raises
    on a dirty ``base_r`` (returns it as-is — the take-profit maths downstream
    fail-closes on a non-finite target).
    """
    if config is None or style is None:
        return base_r
    tag = style.value if isinstance(style, StyleTag) else str(style)
    if tag != StyleTag.VALUE.value:
        return base_r
    if not math.isfinite(base_r):
        return base_r
    return base_r * config.value_take_profit_r_mult


__all__ = [
    "StyleSoftConfig",
    "style_take_profit_r_multiple",
]
