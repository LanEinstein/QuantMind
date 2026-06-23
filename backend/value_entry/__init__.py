"""Objective bottom-confirmation entry gate for the value sleeve (AF-004).

提前埋伏, not 接飞刀: a value 埋伏 may only enter a name that is a *confirmed
bottom* — price has stabilised above holder cost, selling volume has dried up,
the name is not making fresh lows, and it has NOT already been chased (no
extended run-up / not near the 52-week high / chips not euphoric). The gate is a
deterministic :class:`~backend.value_assembly.assembler.EntryGate`: it forces a
non-confirmed code's value score to 0.0 so it can never clear the value gate.

Pure, deterministic, 0 LLM, import-isolated (``backend.marketdata_snapshot`` for
the PIT chip snapshot only). Thresholds live in a runtime-immutable config
(amendment-gated); the empirical calibration of the symbols is the QGR slow-leg's
job (shared 思路, independent backend implementation).
"""

from __future__ import annotations

from backend.value_entry.bottom_confirmation import (
    BottomConfirmation,
    BottomConfirmationConfig,
    BottomSignals,
    ChipCost,
    PriceWindow,
)

__all__ = [
    "BottomConfirmation",
    "BottomConfirmationConfig",
    "BottomSignals",
    "ChipCost",
    "PriceWindow",
]
