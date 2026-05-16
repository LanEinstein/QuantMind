"""Plain-text Feishu message renderer (P0-2 / P0-3 / F-002).

The renderer is the **single source of truth** for every outbound
Feishu message text. LLMs never compose Feishu wire text
(P0-2 §1.2 / P0-3 §1.3 / CLAUDE.md §2.6); only this module — driven by
typed inputs — emits a string that
:meth:`backend.integrations.feishu.client.FeishuClient.send_message`
will send.

Templates are deliberately rigid:

* Fixed line order, fixed labels, fixed numeric formatting.
* No string interpolation from LLM output.
* No HTML, no Lark interactive blocks (P0-2 §2.5 — plain text only in
  phase 1; F-005 reconciliation cards may upgrade to ``interactive``
  later by adding a new renderer entry point, **not** by editing the
  text path).

Output is locked via snapshot tests (``tests/test_feishu_renderer.py``)
so future changes require an explicit code review + test update.

Categories (F-002 ships #1; F-004 / F-005 / F-006 plug into the same
helpers):

1. InstructionPlan dispatch (BUY/SELL) — :meth:`render_instruction_plan`
2. Execution-report clarification 5 templates (F-004) — :meth:`render_clarification`
3. Daily reconciliation request (F-005) — :meth:`render_reconciliation_request`
4. System alert (F-006) — :meth:`render_alert`
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from backend.models.instruction import (
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    RiskCheckSummary,
)

_SH = ZoneInfo("Asia/Shanghai")
"""All visible timestamps render in Asia/Shanghai (the operator's local
trading clock). Stored as a module constant so the renderer is purely
text — no tz argument plumbing."""


# === Public types ====================================================


class FeishuMessageKind(StrEnum):
    """Locked outbound message kinds — every send must be one of these.

    Tightened deliberately: a sixth kind is a P0-2 §2.5 / red-line
    extension, not a casual addition. Tests assert the enum membership
    stays at five (F-004 + F-005 + F-006 fill in the remaining four).
    """

    INSTRUCTION_PLAN = "instruction_plan"
    CLARIFICATION = "clarification"
    RECONCILIATION_REQUEST = "reconciliation_request"
    RECONCILIATION_RESULT = "reconciliation_result"
    ALERT = "alert"


class ClarificationTemplate(StrEnum):
    """Five pre-written clarification templates (P0-4 §1.1.1).

    The parser hits one of these when raw text fails every regex; the
    template id maps 1:1 to the
    :class:`backend.services.execution_report_parser.ExecutionReportParseError.reason`
    enumeration so the orchestrator picks the right text without
    case-by-case logic.
    """

    NO_PATTERN_MATCH = "no_pattern_match"
    EMPTY_PAYLOAD = "empty_payload"
    FIELD_CROSS_CHECK_FAILED = "field_cross_check_failed"
    UNKNOWN_INSTRUCTION_ID = "unknown_instruction_id"
    EXPIRED_INSTRUCTION = "expired_instruction"


# === Renderer ========================================================


class MessageRenderer:
    """Compose every outbound Feishu plain-text message.

    The renderer is **stateless** — every method is a pure function of
    its arguments. Tests can therefore call it without fixture setup.

    Why a class instead of free functions? It lets future amendments
    inject domain-specific helpers (e.g. ``currency`` for multi-currency
    accounts) without changing the import surface that
    :class:`Alerter` (F-006) and the InstructionPlan dispatcher are
    coupled to.
    """

    # -- InstructionPlan dispatch (F-002 core) -------------------------

    def render_instruction_plan(
        self, plan: InstructionPlan
    ) -> str:
        """Render a BUY/SELL InstructionPlan into a 7-section block.

        HOLD plans are not routable (P0-3 §1.3.1) and must never reach
        this method — a :class:`ValueError` here is a fail-closed signal
        that an upstream guard slipped.
        """
        if plan.side is InstructionSide.HOLD:
            raise ValueError(
                "HOLD plan is not routable — render_instruction_plan must "
                "only be called for BUY/SELL (P0-3 §1.3.1)"
            )
        if plan.status not in (
            InstructionStatus.VALIDATED,
            InstructionStatus.DISPATCHED,
        ):
            raise ValueError(
                f"status={plan.status.value} cannot be dispatched to "
                "Feishu — VALIDATED or DISPATCHED only"
            )

        side_zh = "买入" if plan.side is InstructionSide.BUY else "卖出"
        amount_cny = (
            Decimal(str(plan.limit_price))  # type: ignore[arg-type]
            * Decimal(plan.volume)  # type: ignore[arg-type]
        )
        risk_lines = _format_risk_summary(plan.risk_summary)
        position_line = _format_position_summary(plan)

        # Locked layout — order, labels, line breaks. Any change must
        # update the snapshot test in tests/test_feishu_renderer.py.
        lines = [
            "【QuantMind 指令】",
            f"指令编号: {plan.instruction_id}",
            f"操作: {side_zh} {plan.stock_code} {plan.stock_name}",
            f"股数: {plan.volume} 股",
            f"限价: {_format_money(plan.limit_price)} CNY",
            f"预计金额: {_format_money(amount_cny)} CNY",
            f"有效期: {_format_local_ts(plan.valid_until)} 前(同日 14:55 截止)",
            position_line,
            "—— 风险摘要 ——",
            *risk_lines,
            f"失效说明: {plan.invalidation_summary}",
            "—— 回报模板(原文回复)——",
            *_REPORT_TEMPLATE_BLOCK,
        ]
        return "\n".join(lines)

    # -- Clarification (F-004 surface) ---------------------------------

    def render_clarification(
        self,
        *,
        template: ClarificationTemplate,
        instruction_id: str | None = None,
        raw_text_excerpt: str | None = None,
    ) -> str:
        """Render one of the five pre-written clarification templates.

        Only ``instruction_id`` and ``raw_text_excerpt`` are interpolated
        — the rest of the text is a code constant. ``instruction_id`` is
        validated against the canonical regex so an attacker who
        controls upstream cannot inject arbitrary characters into the
        clarification body (P0-2 §2.6 — prompt-injection defence).
        """
        if (
            instruction_id is not None
            and not _INSTRUCTION_ID_RE.fullmatch(instruction_id)
        ):
            raise ValueError(
                f"instruction_id {instruction_id!r} fails canonical pattern"
            )
        body = _CLARIFICATION_BODIES[template]
        excerpt_block = ""
        if raw_text_excerpt:
            excerpt_block = (
                f"\n原始文本节选: {_truncate(raw_text_excerpt, 80)}"
            )
        id_block = ""
        if instruction_id:
            id_block = f"\n指令编号: {instruction_id}"
        return (
            "【QuantMind 澄清】\n"
            f"{body}"
            f"{id_block}"
            f"{excerpt_block}\n"
            "—— 请按以下格式回复 ——\n"
            + "\n".join(_REPORT_TEMPLATE_BLOCK)
        )

    # -- Reconciliation request (F-005 surface) ------------------------

    def render_reconciliation_request(
        self,
        *,
        ticket_id: str,
        trade_date: str,
        expected_cash_cny: float,
        expected_positions: Mapping[str, int],
        expected_total_equity_cny: float,
    ) -> str:
        """Render the 16:00 daily reconciliation prompt (P0-5 §1).

        Sent to the **decision** chat (not the alert chat — those are
        isolated per P0-2-amendment-2026-05-16). ``ticket_id`` flows
        through verbatim so the operator's reply is unambiguous.
        """
        if not _RECONCILIATION_ID_RE.fullmatch(ticket_id):
            raise ValueError(
                f"ticket_id {ticket_id!r} must match ^RECON-\\d{{8}}-\\d{{3}}$"
            )
        if not _TRADE_DATE_RE.fullmatch(trade_date):
            raise ValueError(
                f"trade_date {trade_date!r} must be YYYY-MM-DD"
            )

        if expected_positions:
            position_lines = [
                f"  {code} {volume} 股"
                for code, volume in sorted(expected_positions.items())
            ]
        else:
            position_lines = ["  (无持仓)"]

        return "\n".join(
            [
                "【QuantMind 对账】",
                f"对账编号: {ticket_id}",
                f"交易日: {trade_date}",
                f"系统现金: {_format_money(expected_cash_cny)} CNY",
                "系统持仓:",
                *position_lines,
                f"系统总权益: {_format_money(expected_total_equity_cny)} CNY",
                "—— 请按以下格式回复 ——",
                "1. 采纳系统镜像 → 回复『采纳镜像』",
                "2. 采纳用户回报 → 回复『采纳回报 现金 <数额> 持仓 <code> <股数> ...』",
                "3. 对账更正 → 回复『更正 现金 <数额> 持仓 <code> <股数> ...』",
            ]
        )

    # -- Reconciliation result (F-005 surface) -------------------------

    def render_reconciliation_result(
        self,
        *,
        ticket_id: str,
        resolution: str,
        cash_delta_cny: float,
        position_deltas: Mapping[str, int],
    ) -> str:
        """Render the post-decision summary message back to the operator."""
        if not _RECONCILIATION_ID_RE.fullmatch(ticket_id):
            raise ValueError(
                f"ticket_id {ticket_id!r} must match ^RECON-\\d{{8}}-\\d{{3}}$"
            )
        position_lines = (
            [
                f"  {code} {'+' if d >= 0 else ''}{d} 股"
                for code, d in sorted(position_deltas.items())
            ]
            if position_deltas
            else ["  (无调整)"]
        )
        return "\n".join(
            [
                "【QuantMind 对账已落账】",
                f"对账编号: {ticket_id}",
                f"裁定: {resolution}",
                f"现金调整: {'+' if cash_delta_cny >= 0 else ''}"
                f"{_format_money(cash_delta_cny)} CNY",
                "持仓调整:",
                *position_lines,
            ]
        )

    # -- Alerts (F-006 surface) ---------------------------------------

    def render_alert(
        self,
        *,
        alert_type: str,
        severity: str,
        message: str,
        fired_at: datetime,
    ) -> str:
        """Render a system alert into the dedicated alert chat.

        Alerter goes to ``FEISHU_ALERT_CHAT_ID`` (P0-2-amendment-2026-05-16
        §4 red line 7) — the **decision** chat never receives alerts so
        they cannot pollute the order/recon thread.
        """
        if not message:
            raise ValueError("alert message must not be empty")
        # Strip control characters so a bad alerter call cannot inject
        # newlines that fake an Instruction or Reconciliation header.
        sanitized = _strip_controls(message)
        return "\n".join(
            [
                f"【QuantMind 告警 / {severity.upper()}】",
                f"类型: {alert_type}",
                f"时间: {_format_local_ts(fired_at)}",
                f"消息: {sanitized}",
            ]
        )


# === Helpers ========================================================


def _format_money(value: float | Decimal) -> str:
    """Two-decimal CNY rendering with no thousands separator.

    Snapshot-locked — changing the format breaks every downstream test.
    """
    quantized = Decimal(str(value)).quantize(Decimal("0.01"))
    # Drop trailing zero only when scale is exactly two? No — keep
    # exactly 2dp so amounts read consistently.
    return f"{quantized:.2f}"


def _format_local_ts(ts: datetime) -> str:
    """Render in Asia/Shanghai HH:MM:SS — operator-friendly."""
    local = ts.astimezone(_SH)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def _format_risk_summary(
    summary: tuple[RiskCheckSummary, ...],
) -> list[str]:
    """Render the 14-check summary into one operator-readable line each.

    Builder + RiskEngine fill ``passed`` (True/False/None); failures and
    pending-eval entries are always surfaced — they would otherwise be
    invisible to the operator.
    """
    lines: list[str] = []
    for idx, row in enumerate(summary, start=1):
        marker = (
            "✓"
            if row.passed is True
            else ("✗" if row.passed is False else "—")
        )
        suffix = ""
        if row.passed is False and row.message:
            suffix = f" · {row.message}"
        lines.append(f"  {marker} #{idx:02d} {row.rule_name}{suffix}")
    return lines


def _format_position_summary(plan: InstructionPlan) -> str:
    """Single-line preview of pre/post position percentages."""
    ps = plan.position_summary
    if ps is None:  # pragma: no cover — invariant via _check_side_invariants
        return "仓位预览: 不可用"
    return (
        f"仓位预览: 单股 {ps.pre_position_pct:.2%} → {ps.post_position_pct:.2%}"
        f" · 总仓 {ps.pre_total_position_pct:.2%} → {ps.post_total_position_pct:.2%}"
        f" · 现金 {_format_money(ps.pre_cash)} → {_format_money(ps.post_cash)} CNY"
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _strip_controls(text: str) -> str:
    """Strip C0/C1 control characters that could spoof a renderer header.

    Keeps newlines + every printable codepoint (Unicode general category
    not starting with ``C``). The renderer body labels are prefixed
    with ``【QuantMind ...】`` markers; an attacker embedding raw control
    bytes to forge those markers is the threat — printable text
    (including Chinese punctuation) flows through unmodified.
    """
    import unicodedata as _u

    return "".join(
        ch
        for ch in text
        if ch == "\n" or not _u.category(ch).startswith("C")
    )


# --- regex / constant tables -----------------------------------------

_INSTRUCTION_ID_RE = re.compile(r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$")
_RECONCILIATION_ID_RE = re.compile(r"^RECON-\d{8}-\d{3}$")
_TRADE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_REPORT_TEMPLATE_BLOCK: tuple[str, ...] = (
    "1. 已执行 <编号> <买入|卖出> <代码> <股数>股 成交价 <价> 手续费 <费>",
    "2. 部分执行 <编号> <买入|卖出> <代码> <成交>股 成交价 <价> 剩余 <未成交>股",
    "3. 未执行 <编号> 原因: <原因>",
    "4. 更正/盘后补录: 在 1/2/3 前加 `更正 ` 或 `盘后补录 `",
)
"""Locked template block reused by InstructionPlan + Clarification
prompts so the operator sees the same wire shape regardless of message
kind. Mirrors :mod:`backend.execution.regex_patterns`."""


_CLARIFICATION_BODIES: Mapping[ClarificationTemplate, str] = {
    ClarificationTemplate.NO_PATTERN_MATCH: (
        "无法识别消息内容,无法应用到模拟账本。"
    ),
    ClarificationTemplate.EMPTY_PAYLOAD: (
        "收到空消息,无法判断回报类型。"
    ),
    ClarificationTemplate.FIELD_CROSS_CHECK_FAILED: (
        "回报字段与指令不一致(代码/方向/股数其中之一不匹配)。"
    ),
    ClarificationTemplate.UNKNOWN_INSTRUCTION_ID: (
        "指令编号无法在系统中找到,请确认编号或重新发送。"
    ),
    ClarificationTemplate.EXPIRED_INSTRUCTION: (
        "指令已过期(超过当日 14:55 截止),如有补录请加前缀『盘后补录』。"
    ),
}


__all__ = [
    "ClarificationTemplate",
    "FeishuMessageKind",
    "MessageRenderer",
]
