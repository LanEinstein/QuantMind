"""InstructionPlan strict schema (P0-3 + P0-7 amendment + P0-8 §1.6.2).

InstructionPlan is the system's **only** executable trading object. It
replaces ``TradingSignal`` in the execution path — ``TradingSignal``
survives only as the intermediate output of ``fund_manager_node``.

Locked invariants (anything violating these is a red line):

* ``instruction_id`` matches ``^QM-\\d{8}-\\d{6}-\\d{6}-(BUY|SELL|HOLD)-\\d{3}$``
* ``side ∈ {BUY, SELL, HOLD}``; HOLD never routes
* ``valid_until`` strictly after ``created_at``, same local trading day,
  ≤ 14:55 Asia/Shanghai (P0-3 §1.4)
* ``data_snapshot.snapshot_at < created_at`` (data precedes decision)
* ``risk_summary`` length **exactly 14** — 7 from P0-3 plus 7-14 added by
  P0-7 amendment; ``passed`` is ``bool | None`` to let Phase D fill in
  the late seven once RiskEngine expands.
* ``evidence_ids`` entries respect P0-8 §1.6.2 prefix set
* ``debate_round_count ≥ 1`` (P0-1 §1.6 — zero rounds = LLM bypass)
* Frozen + strict + ``extra='forbid'`` so no LLM smuggles new fields in.

Status mutation is allowed only via ``model_copy(update={...})`` driven
by the state machine module (Phase B-003); we never mutate in place.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.evidence import validate_evidence_id

_SH = ZoneInfo("Asia/Shanghai")
"""Asia/Shanghai zone; locked because 14:55 cutoff and trade_date are
both expressed in local market time (P0-3 §1.4)."""

_VOLUME_LOT_SIZE = 100
"""A-share minimum trading lot. Mirrors RiskConfig.volume_lot_size; kept
as a private constant here so model validation can run before risk
config is loaded — the model never overrides risk_config but it does
need a baseline to enforce volume_lot_size at the schema layer."""

_INSTRUCTION_ID_PATTERN = r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$"
"""Single source of truth for the instruction_id format (P0-3 §1.2).
Also imported by the frontend JS mirror and redline-check.sh."""


class InstructionSide(StrEnum):
    """Trading direction. HOLD never routes to broker or Feishu."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class InstructionStatus(StrEnum):
    """InstructionPlan state machine (P0-3 §1.1.1).

    Allowed transitions are owned by ``backend/services/instruction_state_machine.py``
    (Phase B-003). Reading this enum alone is not enough — any mutation
    must go through the state machine to keep the audit trail clean.
    """

    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    DISPATCHED = "DISPATCHED"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"


_NEEDS_REASON: frozenset[InstructionStatus] = frozenset(
    {InstructionStatus.REJECTED, InstructionStatus.AMBIGUOUS}
)


class DataSnapshot(BaseModel):
    """Snapshot of data + intelligence relied on by the decision (P0-3 §1.5)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    snapshot_at: datetime
    quote_source: str = Field(min_length=1, max_length=64)
    quote_latency_ms: int | None = Field(default=None, ge=0)
    news_sources_by_domain: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    news_window_seconds: int | None = Field(default=None, ge=0)
    prev_close: float | None = Field(default=None, gt=0.0)
    is_trading_day: bool
    is_trading_hours: bool


class PositionSummary(BaseModel):
    """Pre/post execution position snapshot derived from MockBroker state."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    pre_position_pct: float = Field(ge=0.0, le=1.0)
    post_position_pct: float = Field(ge=0.0, le=1.0)
    pre_total_position_pct: float = Field(ge=0.0, le=1.0)
    post_total_position_pct: float = Field(ge=0.0, le=1.0)
    pre_cash: float = Field(ge=0.0)
    post_cash: float = Field(ge=0.0)


class RiskCheckSummary(BaseModel):
    """Single-check entry within InstructionPlan.risk_summary.

    ``passed`` is ``bool | None`` (P0-7 amendment): the first seven
    indices populate to True/False per the RiskEngine 7-check, and
    indices 7-13 carry ``None`` while Phase D wires the 14-check
    extension. Once RiskEngine reaches 14-check, all entries become
    bool again.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    rule_name: str = Field(min_length=1, max_length=64)
    passed: bool | None = None
    threshold: str | None = Field(default=None, max_length=128)
    actual: str | None = Field(default=None, max_length=128)
    message: str = Field(default="", max_length=256)


class InstructionPlan(BaseModel):
    """Executable trading plan (P0-3 §1.1.3).

    HOLD plans are still validated end-to-end (so the decision_ledger
    can show *why* hold was chosen), but they never reach broker or
    Feishu — see ``backend/services/instruction_plan.is_routable``.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # --- identity & timing ---
    instruction_id: str = Field(pattern=_INSTRUCTION_ID_PATTERN)
    created_at: datetime
    valid_until: datetime
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    # --- target & direction ---
    stock_code: str = Field(pattern=r"^\d{6}$")
    stock_name: str = Field(min_length=1, max_length=64)
    side: InstructionSide

    # --- execution params (BUY/SELL only) ---
    volume: int | None = Field(default=None, ge=_VOLUME_LOT_SIZE)
    limit_price: float | None = Field(default=None, gt=0.0)

    # --- data snapshot (by-value) ---
    data_snapshot: DataSnapshot

    # --- evidence (by-reference) ---
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)

    # --- position summary (by-value; HOLD = None) ---
    position_summary: PositionSummary | None = None

    # --- risk (mixed) ---
    risk_summary: tuple[RiskCheckSummary, ...] = Field(min_length=14, max_length=14)
    risk_validation_id: str = Field(min_length=1, max_length=64)

    # --- multi-agent debate trace (by-reference) ---
    signal_id: str = Field(min_length=1, max_length=64)
    analysis_record_id: str = Field(min_length=1, max_length=64)
    debate_round_count: int = Field(ge=1)

    # --- invalidation hint (short human text) ---
    invalidation_summary: str = Field(min_length=1, max_length=200)

    # --- state machine ---
    status: InstructionStatus = InstructionStatus.DRAFT
    rejection_reason: str | None = Field(default=None, max_length=256)

    # -------------------------------------------------------------------------
    # Validators
    # -------------------------------------------------------------------------

    @model_validator(mode="after")
    def _check_stock_name(self) -> InstructionPlan:
        name = self.stock_name
        for ch in name:
            cat = unicodedata.category(ch)
            if cat.startswith("C"):
                raise ValueError(
                    f"stock_name contains control character {ch!r}"
                )
        return self

    @model_validator(mode="after")
    def _check_evidence_prefixes(self) -> InstructionPlan:
        for eid in self.evidence_ids:
            validate_evidence_id(eid)
        return self

    @model_validator(mode="after")
    def _check_volume_lot(self) -> InstructionPlan:
        if self.volume is not None and self.volume % _VOLUME_LOT_SIZE != 0:
            raise ValueError(
                f"volume {self.volume} must be a multiple of {_VOLUME_LOT_SIZE}"
            )
        return self

    @model_validator(mode="after")
    def _check_side_invariants(self) -> InstructionPlan:
        if self.side is InstructionSide.HOLD:
            if self.volume is not None or self.limit_price is not None:
                raise ValueError(
                    "HOLD plan must have volume=None and limit_price=None"
                )
            if self.position_summary is not None:
                raise ValueError("HOLD plan must have position_summary=None")
        else:  # BUY / SELL
            if self.volume is None or self.limit_price is None:
                raise ValueError(
                    f"{self.side.value} plan requires volume and limit_price"
                )
            if self.position_summary is None:
                raise ValueError(
                    f"{self.side.value} plan requires position_summary"
                )
        return self

    @model_validator(mode="after")
    def _check_timing(self) -> InstructionPlan:
        # snapshot precedes decision
        if self.data_snapshot.snapshot_at >= self.created_at:
            raise ValueError(
                "data_snapshot.snapshot_at must be strictly before created_at"
            )

        created_local = self.created_at.astimezone(_SH)
        valid_local = self.valid_until.astimezone(_SH)

        # valid_until strictly after created_at
        if valid_local <= created_local:
            raise ValueError("valid_until must be strictly after created_at")

        # same trading day in Asia/Shanghai
        if valid_local.date() != created_local.date():
            raise ValueError(
                "valid_until must be the same Asia/Shanghai date as created_at"
            )

        # cutoff at 14:55 local
        cutoff = created_local.replace(
            hour=14, minute=55, second=0, microsecond=0
        )
        if valid_local > cutoff:
            raise ValueError(
                f"valid_until {valid_local.isoformat()} exceeds the "
                f"14:55 Asia/Shanghai cutoff {cutoff.isoformat()}"
            )

        # trade_date must equal local date
        expected = created_local.strftime("%Y-%m-%d")
        if self.trade_date != expected:
            raise ValueError(
                f"trade_date {self.trade_date!r} must match created_at "
                f"Asia/Shanghai date {expected!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_id_components(self) -> InstructionPlan:
        # instruction_id pieces must agree with explicit fields.
        # Format: QM-YYYYMMDD-HHMMSS-CODE-SIDE-SEQ
        _, ymd, hms, code, side_text, _seq = self.instruction_id.split("-")
        created_local = self.created_at.astimezone(_SH)
        if ymd != created_local.strftime("%Y%m%d"):
            raise ValueError(
                "instruction_id date prefix must match created_at "
                "Asia/Shanghai date"
            )
        # P0-3 §1.2 locks HHMMSS to the Asia/Shanghai creation time so
        # multiple instructions in the same second can be ordered. A
        # drifting time segment would silently break correlation and
        # audit ordering — caught by codex-review cycle 1.
        if hms != created_local.strftime("%H%M%S"):
            raise ValueError(
                "instruction_id time prefix must match created_at "
                "Asia/Shanghai HHMMSS"
            )
        if code != self.stock_code:
            raise ValueError(
                "instruction_id code segment must match stock_code"
            )
        if side_text != self.side.value:
            raise ValueError(
                "instruction_id side segment must match side field"
            )
        return self

    @model_validator(mode="after")
    def _check_rejection_reason(self) -> InstructionPlan:
        if self.status in _NEEDS_REASON:
            if not self.rejection_reason:
                raise ValueError(
                    f"status={self.status.value} requires rejection_reason"
                )
        else:
            if self.rejection_reason is not None:
                raise ValueError(
                    f"status={self.status.value} must not carry rejection_reason"
                )
        return self


__all__ = [
    "DataSnapshot",
    "InstructionPlan",
    "InstructionSide",
    "InstructionStatus",
    "PositionSummary",
    "RiskCheckSummary",
]
