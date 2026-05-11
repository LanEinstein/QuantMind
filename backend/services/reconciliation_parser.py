"""Reconciliation reply parser (P0-5 §1.3, B-004).

Strict regex only — no LLM in the path (CLAUDE.md §2.6 / P0-2 §2 红线 6).
Five distinct user reply forms map to typed events; anything else
raises :class:`ReconciliationParseError` so the orchestrator can flag
the ticket as AMBIGUOUS and ship a pre-written clarification.

The parser also covers the "positions" sub-string grammar used by
对账差异 / 对账更正 — that grammar is the only allowed shape for
:class:`ReportedPosition` lists in user replies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from backend.models.reconciliation import (
    TICKET_ID_PATTERN,
    ReportedPosition,
)

_TICKET = rf"(?P<ticket_id>{TICKET_ID_PATTERN[1:-1]})"  # strip ^ and $
_CASH = r"(?P<cash>\d+(?:\.\d+)?)"
_POS = r"(?P<positions>.+)"

R_RECON_OK = re.compile(rf"^对账无误 {_TICKET}$")
R_RECON_MISMATCH = re.compile(
    rf"^对账差异 {_TICKET} 现金 {_CASH} 持仓 {_POS}$",
    flags=re.DOTALL,
)
R_RECON_AMEND = re.compile(
    rf"^对账更正 {_TICKET} 现金 {_CASH} 持仓 {_POS}$",
    flags=re.DOTALL,
)
# P0-5 §1.3.1.4 stated intent: 全角中文冒号 「:」 (U+FF1A) only — never the
# half-width ASCII colon. Spelled out via ： so the source byte-stream
# stays unambiguous on every editor.
_FULL_COLON = "："
R_RECON_RESOLVE_USER = re.compile(
    rf"^对账采纳{_FULL_COLON}用户回报 {_TICKET}$"
)
R_RECON_RESOLVE_SYSTEM = re.compile(
    rf"^对账采纳{_FULL_COLON}系统镜像 {_TICKET}$"
)

R_POSITION_ITEM = re.compile(
    r"^(?P<code>\d{6}) (?P<volume>\d+)股 成本 (?P<cost_price>\d+(?:\.\d+)?)$"
)
R_POSITIONS_NONE = re.compile(r"^无$")

_INNER_WS = re.compile(r"[ \t]+")


class ReconciliationReplyKind(StrEnum):
    """Allowed user reply forms (P0-5 §1.3.1)."""

    OK = "OK"
    MISMATCH = "MISMATCH"
    AMEND = "AMEND"
    RESOLVE_USER = "RESOLVE_USER"
    RESOLVE_SYSTEM = "RESOLVE_SYSTEM"


@dataclass(frozen=True)
class ReconciliationReply:
    """Parsed user reply to a daily-reconciliation request."""

    kind: ReconciliationReplyKind
    ticket_id: str
    cash: float | None = None
    positions: tuple[ReportedPosition, ...] | None = None


class ReconciliationParseError(ValueError):
    """Raised when no locked regex matches; orchestrator flags AMBIGUOUS."""

    def __init__(self, message: str, *, reason: str = "no_pattern_match") -> None:
        super().__init__(message)
        self.reason = reason


def _normalise(text: str) -> str:
    return _INNER_WS.sub(" ", text.strip())


def _parse_positions(block: str) -> tuple[ReportedPosition, ...]:
    """Parse the positions sub-grammar; raises on any malformed item.

    Empty positions are expressed with the literal "无" (P0-5 §1.3.2);
    return an empty tuple in that case.
    """
    block = block.strip()
    if R_POSITIONS_NONE.fullmatch(block):
        return ()
    items = [seg.strip() for seg in block.split(";")]
    parsed: list[ReportedPosition] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            raise ReconciliationParseError(
                f"empty position segment in {block!r}",
                reason="positions_malformed",
            )
        m = R_POSITION_ITEM.fullmatch(item)
        if m is None:
            raise ReconciliationParseError(
                f"position segment {item!r} did not match",
                reason="positions_malformed",
            )
        code = m["code"]
        if code in seen:
            raise ReconciliationParseError(
                f"duplicate position code {code}",
                reason="positions_duplicate",
            )
        seen.add(code)
        # ``ReportedPosition`` enforces lot size + non-negative cost; any
        # ValidationError surfaces as a uniform parser failure so the
        # orchestrator sees a single error type.
        try:
            parsed.append(
                ReportedPosition(
                    code=code,
                    volume=int(m["volume"]),
                    cost_price=float(m["cost_price"]),
                )
            )
        except ValidationError as exc:
            raise ReconciliationParseError(
                f"position segment {item!r} failed validation: {exc}",
                reason="positions_field_invalid",
            ) from exc
    return tuple(parsed)


def parse_reconciliation_reply(raw_text: str) -> ReconciliationReply:
    """Parse a user's reply to a daily-reconciliation flow.

    Raises:
        ReconciliationParseError: when no locked regex matches or the
            positions sub-grammar fails. ``reason`` is the audit tag.
    """
    text = _normalise(raw_text)
    if not text:
        raise ReconciliationParseError(
            "empty reconciliation reply", reason="empty_payload"
        )

    if (m := R_RECON_OK.fullmatch(text)) is not None:
        return ReconciliationReply(
            kind=ReconciliationReplyKind.OK,
            ticket_id=m["ticket_id"],
        )
    if (m := R_RECON_MISMATCH.fullmatch(text)) is not None:
        positions = _parse_positions(m["positions"])
        return ReconciliationReply(
            kind=ReconciliationReplyKind.MISMATCH,
            ticket_id=m["ticket_id"],
            cash=float(m["cash"]),
            positions=positions,
        )
    if (m := R_RECON_AMEND.fullmatch(text)) is not None:
        positions = _parse_positions(m["positions"])
        return ReconciliationReply(
            kind=ReconciliationReplyKind.AMEND,
            ticket_id=m["ticket_id"],
            cash=float(m["cash"]),
            positions=positions,
        )
    if (m := R_RECON_RESOLVE_USER.fullmatch(text)) is not None:
        return ReconciliationReply(
            kind=ReconciliationReplyKind.RESOLVE_USER,
            ticket_id=m["ticket_id"],
        )
    if (m := R_RECON_RESOLVE_SYSTEM.fullmatch(text)) is not None:
        return ReconciliationReply(
            kind=ReconciliationReplyKind.RESOLVE_SYSTEM,
            ticket_id=m["ticket_id"],
        )

    raise ReconciliationParseError(
        "no reconciliation pattern matched", reason="no_pattern_match"
    )


__all__ = [
    "R_POSITION_ITEM",
    "R_POSITIONS_NONE",
    "R_RECON_AMEND",
    "R_RECON_MISMATCH",
    "R_RECON_OK",
    "R_RECON_RESOLVE_SYSTEM",
    "R_RECON_RESOLVE_USER",
    "ReconciliationParseError",
    "ReconciliationReply",
    "ReconciliationReplyKind",
    "parse_reconciliation_reply",
]
