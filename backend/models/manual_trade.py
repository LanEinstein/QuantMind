"""ExternalExecutionEvent — user-discretionary manual trade domain (AD-005).

P1-5-amendment-2026-06-12 third write endpoint. This is a **separate
domain** from :class:`backend.models.instruction.InstructionPlan`: a manual
trade is something the owner did on their own (took profit / cut a loss /
added) that the system did NOT instruct. It therefore:

* carries a ``UT-`` external id that is **disjoint from the ``QM-`` regex
  space** (codex P0-6 "never fabricates an InstructionPlan"), so the
  execution-report parser can never match a manual-trade message;
* never enters the ``instruction_plans`` collection, the InstructionPlan
  state machine, the decision ledger, or the acceptance stability
  denominators;
* is applied to the single MockBroker mirror through the dedicated
  :class:`backend.broker.appliers.ManualTradeApplier` (reusing
  ``apply_external_fill`` semantics), tagged ``USER_DISCRETIONARY`` for the
  3-way performance split.

The ``UT-`` id mirrors the ``QM-`` structure
(``UT-YYYYMMDD-HHMMSS-CODE-SIDE-SEQ``) so the embedded code/side can be
cross-checked and the embedded date drives T+1 sellable bookkeeping
identically to the instruction path.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.trade_origin import TradeOrigin

# UT-YYYYMMDD-HHMMSS-CODE6-(BUY|SELL)-SEQ3 — same shape as the QM- id but a
# disjoint prefix. Anchored so a stray QM-/free-text id cannot validate.
EXTERNAL_TRADE_ID_PATTERN = r"^UT-\d{8}-\d{6}-(\d{6})-(BUY|SELL)-\d{3}$"
_EXTERNAL_TRADE_ID_RE = re.compile(EXTERNAL_TRADE_ID_PATTERN)

LOT_SIZE = 100
"""A-share board lot — manual volumes must be whole lots (P1-5 §1.2)."""


class ManualTradeSide(StrEnum):
    """Direction of a user-discretionary trade."""

    BUY = "BUY"
    SELL = "SELL"


class ManualTradeReason(StrEnum):
    """Why the owner traded on their own (display-only categorisation)."""

    USER_TAKE_PROFIT = "USER_TAKE_PROFIT"
    USER_STOP_LOSS = "USER_STOP_LOSS"
    USER_ADD = "USER_ADD"
    USER_OTHER = "USER_OTHER"


class ExternalExecutionEvent(BaseModel):
    """A single user-recorded manual trade (immutable, append-only).

    Frozen + strict + ``extra='forbid'`` so a malformed submission fails at
    schema validation rather than mutating the mirror with a misshapen row.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    external_trade_id: str = Field(pattern=EXTERNAL_TRADE_ID_PATTERN)
    code: str = Field(pattern=r"^\d{6}$")
    side: ManualTradeSide
    volume: int = Field(gt=0)
    price: float = Field(gt=0.0)
    executed_at: datetime
    reason: ManualTradeReason
    note: str = Field(default="", max_length=256)
    """Free-text owner note — display-only, never parsed into a numeric or
    decision field (mirrors the 4 LLM-writable text fields' read-only role)."""
    origin: TradeOrigin = TradeOrigin.USER_DISCRETIONARY
    related_instruction_id: str | None = Field(
        default=None, pattern=r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL)-\d{3}$"
    )
    """Set only when the manual trade is a deviation-execution of a system
    suggestion (the front-end's "已自主调整" path); the deviation amount is
    logged upstream — this is provenance only, never a routing handle."""

    @model_validator(mode="after")
    def _check_consistency(self) -> ExternalExecutionEvent:
        if self.origin is not TradeOrigin.USER_DISCRETIONARY:
            raise ValueError(
                "ExternalExecutionEvent.origin must be USER_DISCRETIONARY"
            )
        match = _EXTERNAL_TRADE_ID_RE.match(self.external_trade_id)
        if match is None:  # pragma: no cover — Field pattern already guards
            raise ValueError("external_trade_id failed structural match")
        embedded_code, embedded_side = match.group(1), match.group(2)
        if embedded_code != self.code:
            raise ValueError(
                f"external_trade_id code {embedded_code!r} != code {self.code!r}"
            )
        if embedded_side != self.side.value:
            raise ValueError(
                f"external_trade_id side {embedded_side!r} != side "
                f"{self.side.value!r}"
            )
        if self.volume % LOT_SIZE != 0:
            raise ValueError(
                f"volume {self.volume} must be a whole {LOT_SIZE}-share lot"
            )
        return self

    @property
    def side_is_buy(self) -> bool:
        return self.side is ManualTradeSide.BUY


__all__ = [
    "EXTERNAL_TRADE_ID_PATTERN",
    "ExternalExecutionEvent",
    "ManualTradeReason",
    "ManualTradeSide",
]
