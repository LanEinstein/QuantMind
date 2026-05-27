"""ExecutionReport model (P0-4 §1.1.1 / B-003).

Captures every user-originated Feishu reply (plus front-end mirror) in
typed, frozen form. The parser (Phase B-003) emits these; the applier
(Phase E) consumes them. Free-text ``reason`` is preserved verbatim for
audit; ``raw_text`` keeps the original message body in case the parser
misclassifies a borderline case.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExecutionReportKind(StrEnum):
    """Successfully parsed report shape (P0-4 §1.2)."""

    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    UNFILLED = "UNFILLED"


class ExecutionReportPrefix(StrEnum):
    """Optional prefix indicating intent (P0-4 §1.2.4 / §1.2.5)."""

    NONE = "NONE"
    AMEND = "AMEND"
    POST_CLOSE = "POST_CLOSE"


class ExecutionReportChannel(StrEnum):
    """Where the report originated (P1-5 §2 红线 5)."""

    FEISHU = "FEISHU"
    FRONTEND = "FRONTEND"


# P0-4-amendment-2026-05-27 §2.4 — report schema versioning. The owner's
# FILLED report shape changed from「价 + 量 + 费」to「价 + 量」: the owner
# no longer reports the fee; the system computes the fee-inclusive cost.
#   * v1 (legacy) — carried an owner-reported ``fee`` (the pre-amendment
#     FILLED regex with 手续费). Kept for deterministic replay of any
#     persisted v1 event; never produced by the current parser.
#   * v2 (current) — owner reports fill_price + volume only; the system
#     derives commission / stamp tax / transfer fee via
#     :func:`backend.broker.cost_calculator.calculate_cost`
#     (``apply_slippage_model=False``).
REPORT_SCHEMA_V1_OWNER_FEE = 1
REPORT_SCHEMA_V2_SYSTEM_FEE = 2


class ExecutionReport(BaseModel):
    """A successfully parsed user execution report.

    Failure to parse never produces this object — the parser raises an
    :class:`ExecutionReportParseError` and the orchestrator sets the
    plan to ``AMBIGUOUS`` instead (B-003 / P0-4 §1.1.1).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    report_id: str = Field(min_length=1, max_length=64)
    instruction_id: str = Field(
        pattern=r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL)-\d{3}$"
    )
    kind: ExecutionReportKind
    prefix: ExecutionReportPrefix = ExecutionReportPrefix.NONE
    channel: ExecutionReportChannel
    report_schema_version: int = Field(
        default=REPORT_SCHEMA_V2_SYSTEM_FEE,
        ge=REPORT_SCHEMA_V1_OWNER_FEE,
        le=REPORT_SCHEMA_V2_SYSTEM_FEE,
    )
    """P0-4-amendment-2026-05-27 §2.4. v1 = owner-reported fee (legacy),
    v2 = system-computed fee (current). Drives whether ``fee`` must be
    present (v1) or absent (v2) on a FILLED report, and which cost path
    the applier takes."""

    side_zh: str | None = Field(default=None, pattern=r"^(买入|卖出)$")
    stock_code: str | None = Field(default=None, pattern=r"^\d{6}$")
    filled_volume: int | None = Field(default=None, ge=0)
    remain_volume: int | None = Field(default=None, ge=0)
    fill_price: float | None = Field(default=None, gt=0.0)
    fee: float | None = Field(default=None, ge=0.0)
    reason: str | None = Field(default=None, max_length=200)

    raw_text: str = Field(min_length=1, max_length=2048)
    received_at: datetime
    parsed_at: datetime

    @model_validator(mode="after")
    def _check_kind_field_consistency(self) -> ExecutionReport:
        k = self.kind
        # P0-4-amendment-2026-05-27 §2.4 — v1 is the *legacy owner-fee
        # FILLED* schema only. PARTIAL / UNFILLED never carried a fee, so
        # they are always v2; allowing a v1 PARTIAL would pass this model
        # yet crash deep in apply_external_fill ("v1 requires fee"). Reject
        # the inconsistent combination at the boundary instead.
        if (
            self.report_schema_version == REPORT_SCHEMA_V1_OWNER_FEE
            and k is not ExecutionReportKind.FILLED
        ):
            raise ValueError(
                f"report_schema_version v1 is only valid for FILLED, "
                f"got kind={k.value}"
            )
        if k is ExecutionReportKind.FILLED:
            self._require_present(
                "side_zh", "stock_code", "filled_volume", "fill_price"
            )
            # P0-4-amendment-2026-05-27 §2.4 — fee presence is version-
            # gated: v1 (legacy) carries the owner-reported fee, v2 omits
            # it (the system derives the fee-inclusive cost). Pretending
            # v2 has fee=0 is explicitly forbidden — the version decides.
            if self.report_schema_version == REPORT_SCHEMA_V1_OWNER_FEE:
                self._require_present("fee")
            else:
                self._require_absent("fee")
            self._require_absent("remain_volume", "reason")
            if self.filled_volume is not None and self.filled_volume <= 0:
                raise ValueError("FILLED report requires filled_volume > 0")
            self._check_side_and_code_consistency()
        elif k is ExecutionReportKind.PARTIAL:
            self._require_present(
                "side_zh",
                "stock_code",
                "filled_volume",
                "remain_volume",
                "fill_price",
            )
            # PARTIAL never carried a fee (the regex never captured one);
            # the system always computes it. Forbid it explicitly so a
            # mis-built report fails fast rather than silently dropping it.
            self._require_absent("reason", "fee")
            if self.filled_volume is not None and self.filled_volume <= 0:
                raise ValueError("PARTIAL report requires filled_volume > 0")
            if self.remain_volume is not None and self.remain_volume <= 0:
                raise ValueError("PARTIAL report requires remain_volume > 0")
            self._check_side_and_code_consistency()
        elif k is ExecutionReportKind.UNFILLED:
            self._require_present("reason")
            self._require_absent(
                "side_zh",
                "stock_code",
                "filled_volume",
                "remain_volume",
                "fill_price",
                "fee",
            )
        if self.parsed_at < self.received_at:
            raise ValueError("parsed_at must be >= received_at")
        return self

    def _check_side_and_code_consistency(self) -> None:
        """Enforce P0-4 §1.2.1 cross-check: side_zh ↔ instruction_id side,
        and stock_code ↔ instruction_id code. Mismatch → AMBIGUOUS path."""
        # instruction_id format: QM-YYYYMMDD-HHMMSS-CODE-SIDE-SEQ
        parts = self.instruction_id.split("-")
        if len(parts) != 6:  # pragma: no cover — guarded by Field pattern
            return
        id_code = parts[3]
        id_side = parts[4]
        if self.stock_code is not None and self.stock_code != id_code:
            raise ValueError(
                f"stock_code {self.stock_code!r} does not match "
                f"instruction_id code {id_code!r}"
            )
        if self.side_zh is not None:
            expected_zh = "买入" if id_side == "BUY" else "卖出"
            if self.side_zh != expected_zh:
                raise ValueError(
                    f"side_zh {self.side_zh!r} does not match "
                    f"instruction_id side {id_side!r}"
                )

    # -- helpers -------------------------------------------------------------

    def _require_present(self, *fields: str) -> None:
        for f in fields:
            if getattr(self, f) is None:
                raise ValueError(
                    f"kind={self.kind.value} requires field {f!r}"
                )

    def _require_absent(self, *fields: str) -> None:
        for f in fields:
            if getattr(self, f) is not None:
                raise ValueError(
                    f"kind={self.kind.value} must not carry field {f!r}"
                )


__all__ = [
    "REPORT_SCHEMA_V1_OWNER_FEE",
    "REPORT_SCHEMA_V2_SYSTEM_FEE",
    "ExecutionReport",
    "ExecutionReportChannel",
    "ExecutionReportKind",
    "ExecutionReportPrefix",
]
