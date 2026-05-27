"""ExecutionReportParser — strict-regex only (P0-4 §1.1, B-003).

This module is a thin adapter from raw user text → typed
:class:`ExecutionReport`. **No LLM is invoked here** (P0-4 §1.1.2 red
line); a parse failure raises :class:`ExecutionReportParseError` so the
orchestrator can transition the plan into ``AMBIGUOUS`` and dispatch a
pre-written clarification message.

Side-effects (status transitions, MockBroker writes, Feishu sends) live
in higher-level callers; this function is pure (text in, value out, no
IO).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import NamedTuple

from pydantic import ValidationError

from backend.execution.regex_patterns import (
    R_AMEND_FILLED,
    R_AMEND_PARTIAL,
    R_AMEND_UNFILLED,
    R_FILLED,
    R_PARTIAL,
    R_POST_CLOSE_FILLED,
    R_POST_CLOSE_PARTIAL,
    R_POST_CLOSE_UNFILLED,
    R_UNFILLED,
)
from backend.models.execution import (
    REPORT_SCHEMA_V2_SYSTEM_FEE,
    ExecutionReport,
    ExecutionReportChannel,
    ExecutionReportKind,
    ExecutionReportPrefix,
)


class ExecutionReportParseError(ValueError):
    """Raised when raw text does not match any locked regex.

    The orchestrator catches this and transitions the InstructionPlan
    to ``AMBIGUOUS`` (P0-4 §1.1.1). The ``reason`` attribute is the
    short tag used by the audit event and Feishu clarification router.
    """

    def __init__(self, message: str, *, reason: str = "no_pattern_match") -> None:
        super().__init__(message)
        self.reason = reason


class _ParseTarget(NamedTuple):
    pattern: re.Pattern[str]
    kind: ExecutionReportKind
    prefix: ExecutionReportPrefix


_K = ExecutionReportKind
_P = ExecutionReportPrefix
_TARGETS: tuple[_ParseTarget, ...] = (
    _ParseTarget(R_AMEND_FILLED, _K.FILLED, _P.AMEND),
    _ParseTarget(R_AMEND_PARTIAL, _K.PARTIAL, _P.AMEND),
    _ParseTarget(R_AMEND_UNFILLED, _K.UNFILLED, _P.AMEND),
    _ParseTarget(R_POST_CLOSE_FILLED, _K.FILLED, _P.POST_CLOSE),
    _ParseTarget(R_POST_CLOSE_PARTIAL, _K.PARTIAL, _P.POST_CLOSE),
    _ParseTarget(R_POST_CLOSE_UNFILLED, _K.UNFILLED, _P.POST_CLOSE),
    _ParseTarget(R_FILLED, _K.FILLED, _P.NONE),
    _ParseTarget(R_PARTIAL, _K.PARTIAL, _P.NONE),
    _ParseTarget(R_UNFILLED, _K.UNFILLED, _P.NONE),
)
"""Order matters — prefixed forms (更正/盘后补录) are tried first so a
report like ``更正 已执行 ...`` never falls through to the bare FILLED
pattern by accident."""


# P0-4 §1.2 only allows collapsing consecutive horizontal whitespace
# (spaces, tabs) into a single ASCII space. Newlines inside `reason`
# survive because UNFILLED regexes are compiled with re.DOTALL.
_INNER_WS = re.compile(r"[ \t]+")


def _normalise(text: str) -> str:
    """Trim + collapse inner whitespace per P0-4 §1.2 preface rules."""
    stripped = text.strip()
    return _INNER_WS.sub(" ", stripped)


def parse_execution_report(
    raw_text: str,
    *,
    channel: ExecutionReportChannel,
    received_at: datetime,
    parsed_at: datetime | None = None,
    report_id: str | None = None,
) -> ExecutionReport:
    """Parse user-provided execution report text.

    Args:
        raw_text: original Feishu message body or frontend submission.
        channel: where the report came from (FEISHU vs FRONTEND mirror).
        received_at: timestamp the message was first observed.
        parsed_at: timestamp the parser ran (defaults to ``received_at``).
        report_id: deterministic id (caller may pass one to make tests
            reproducible); falls back to a fresh UUID when omitted.

    Raises:
        ExecutionReportParseError: when the text matches no locked
            regex. The orchestrator transitions the plan into
            ``AMBIGUOUS`` and routes the pre-written clarification
            template based on ``reason``.
    """
    normalized = _normalise(raw_text)
    if not normalized:
        raise ExecutionReportParseError(
            "empty report body", reason="empty_payload"
        )

    for target in _TARGETS:
        match = target.pattern.fullmatch(normalized)
        if match is None:
            continue
        # A regex match still has to pass model invariants (positive
        # volumes, side/code cross-check, etc.). Surface those as a
        # uniform ExecutionReportParseError so the orchestrator can
        # transition the plan into AMBIGUOUS instead of letting a raw
        # ValidationError escape the fail-closed flow — caught by
        # codex-review cycle 1.
        try:
            return _build_report(
                match=match,
                target=target,
                channel=channel,
                received_at=received_at,
                parsed_at=parsed_at or received_at,
                report_id=report_id or _new_report_id(),
                raw_text=raw_text,
            )
        except ValidationError as exc:
            raise ExecutionReportParseError(
                f"semantic validation failed on {target.kind.value} "
                f"report: {exc.errors()[0].get('msg', exc)}",
                reason="field_cross_check_failed",
            ) from exc

    raise ExecutionReportParseError(
        f"no regex matched payload of length {len(normalized)}",
        reason="no_pattern_match",
    )


def _new_report_id() -> str:
    return f"erp-{uuid.uuid4().hex[:16]}"


def _build_report(
    *,
    match: re.Match[str],
    target: _ParseTarget,
    channel: ExecutionReportChannel,
    received_at: datetime,
    parsed_at: datetime,
    report_id: str,
    raw_text: str,
) -> ExecutionReport:
    groups = match.groupdict()
    instruction_id = groups["instruction_id"]

    if target.kind is ExecutionReportKind.FILLED:
        # P0-4-amendment-2026-05-27 §2.1/§2.4 — the FILLED regex no longer
        # captures a 手续费 group; the owner reports「价 + 量」and the
        # system derives the fee. Every parsed report is therefore v2.
        return ExecutionReport(
            report_id=report_id,
            instruction_id=instruction_id,
            kind=target.kind,
            prefix=target.prefix,
            channel=channel,
            report_schema_version=REPORT_SCHEMA_V2_SYSTEM_FEE,
            side_zh=groups["side_zh"],
            stock_code=groups["stock_code"],
            filled_volume=int(groups["volume"]),
            fill_price=float(groups["fill_price"]),
            raw_text=raw_text,
            received_at=received_at,
            parsed_at=parsed_at,
        )
    if target.kind is ExecutionReportKind.PARTIAL:
        return ExecutionReport(
            report_id=report_id,
            instruction_id=instruction_id,
            kind=target.kind,
            prefix=target.prefix,
            channel=channel,
            report_schema_version=REPORT_SCHEMA_V2_SYSTEM_FEE,
            side_zh=groups["side_zh"],
            stock_code=groups["stock_code"],
            filled_volume=int(groups["filled_volume"]),
            remain_volume=int(groups["remain_volume"]),
            fill_price=float(groups["fill_price"]),
            raw_text=raw_text,
            received_at=received_at,
            parsed_at=parsed_at,
        )
    # UNFILLED
    return ExecutionReport(
        report_id=report_id,
        instruction_id=instruction_id,
        kind=target.kind,
        prefix=target.prefix,
        channel=channel,
        report_schema_version=REPORT_SCHEMA_V2_SYSTEM_FEE,
        reason=groups["reason"].strip(),
        raw_text=raw_text,
        received_at=received_at,
        parsed_at=parsed_at,
    )


__all__ = [
    "ExecutionReportParseError",
    "parse_execution_report",
]
