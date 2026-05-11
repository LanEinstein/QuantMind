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
        if k is ExecutionReportKind.FILLED:
            self._require_present(
                "side_zh", "stock_code", "filled_volume", "fill_price", "fee"
            )
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
            self._require_absent("reason")
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
    "ExecutionReport",
    "ExecutionReportChannel",
    "ExecutionReportKind",
    "ExecutionReportPrefix",
]
