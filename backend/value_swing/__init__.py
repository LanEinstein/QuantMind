"""做T (T-swing) cost-lowering overlay for value-sleeve holds (AF-006).

The value sleeve holds a core position long-term (only thesis-break / hard-risk
exits it). On top of that core, an OPTIONAL deterministic 做T overlay shaves the
holding cost by selling a bounded swing tranche when price runs above a reference
band and buying it back below — never touching the base floor, strictly T+1 (only
already-settled shares are sold), round-trip-bounded. 0 LLM, deterministic; the
overlay only DECIDES (like the Line-2 anomaly/add evaluators), it never
constructs an InstructionPlan — the single construction point (builder) turns a
:class:`SwingIntent` into an order with deterministically-derived
side/volume/limit_price.

env-OFF by default: ``SwingConfig.enabled`` ships ``False`` so a value hold is a
pure long hold (``evaluate_swing`` → ``None``), byte-identical. Activation is
owner-gated (the 做T tension with long-term holding → bounded, floor-protected).
"""

from __future__ import annotations

from backend.value_swing.swing_overlay import (
    SwingConfig,
    SwingIntent,
    SwingPosition,
    evaluate_swing,
)

__all__ = [
    "SwingConfig",
    "SwingIntent",
    "SwingPosition",
    "evaluate_swing",
]
