"""Production orchestration layer (Phase U).

The composition root that wires the double-line MVP into a live system:
Line-1 selection (screen → budget → candidate → 4-agent debate → builder)
and Line-2 monitoring (anomaly / intraday triggers → builder) into Feishu
dispatch + the broker mirror. It is the *only* layer allowed to import
across the otherwise-isolated feature packages (screening / agents_team /
monitoring / services / data / marketdata_snapshot).

Red lines this layer must honour (it composes, it does not weaken):

* It **never constructs** :class:`~backend.models.instruction.InstructionPlan`
  — only ``instruction_plan_builder`` does (R0 §4 single construction point).
* It carries **no LLM-derived value into any decision field** — the
  builder + RiskEngine remain the deterministic gate.
* Data fetched here is persisted point-in-time via
  :class:`~backend.marketdata_snapshot.MarketDataSnapshot` before use
  (R0 §3), so every signal is replayable.
"""

from backend.orchestration.line1_frame import (
    DERIVED_ENDPOINT,
    DERIVED_VENDOR,
    SCREENER_FRAME_HEADER,
    Line1FrameAssembler,
    Line1FrameError,
    Line1FrameResult,
)

__all__ = [
    "DERIVED_ENDPOINT",
    "DERIVED_VENDOR",
    "SCREENER_FRAME_HEADER",
    "Line1FrameAssembler",
    "Line1FrameError",
    "Line1FrameResult",
]
