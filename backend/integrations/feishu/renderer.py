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


class BuySignalTemplate(StrEnum):
    """Four visually-distinct BUY-signal templates (M-006 / P0-7-amendment).

    The CandidateSelector → debate → builder pipeline classifies each BUY into
    a budget-tier outcome; the template id maps 1:1 to that outcome so the
    operator can tell at a glance whether a signal is a normal compliant order,
    an ETF concentration-exception that needs explicit confirmation, a
    budget-too-small no-trade, or a paper-only (Micro-tier) signal.
    """

    NORMAL_COMPLIANT = "normal_compliant"
    ETF_CONCENTRATION_EXCEPTION = "etf_concentration_exception"
    NO_COMPLIANT_TRADE = "no_compliant_trade"
    PAPER_ONLY = "paper_only"


# Order-bearing BUY templates that carry a real InstructionPlan; the
# NO_COMPLIANT_TRADE template has no order and uses its own renderer method.
_PLAN_BACKED_BUY_TEMPLATES: frozenset[BuySignalTemplate] = frozenset(
    {
        BuySignalTemplate.NORMAL_COMPLIANT,
        BuySignalTemplate.ETF_CONCENTRATION_EXCEPTION,
        BuySignalTemplate.PAPER_ONLY,
    }
)


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
        self._assert_dispatchable(plan)
        # Locked layout — order, labels, line breaks. Any change must
        # update the snapshot test in tests/test_feishu_renderer.py.
        return "\n".join(["【QuantMind 指令】", *self._dispatch_body_lines(plan)])

    # -- Go-live connectivity smoke (U-D4) -----------------------------

    def render_smoke_ping(
        self,
        *,
        sent_at: datetime,
        pilot: bool = False,
    ) -> str:
        """Render a go-live connectivity smoke ping.

        A fixed, injection-safe literal — no user / LLM / market content —
        so the decision-chat send/receive round-trip can be validated
        without any prompt-injection surface (CLAUDE.md §2.6, "所有飞书
        消息必经 renderer.py"). The header makes unmistakable that this is
        NOT a tradable instruction so the operator never mistakes the
        smoke for an order.
        """
        return "\n".join(
            [
                *self._pilot_prefix(pilot),
                "【QuantMind 连通性自检】",
                "本条为上线前飞书通道自检,非交易指令,无需执行。",
                f"发送时间:{_format_local_ts(sent_at)}",
                "如已收到,请回复『收到 自检』以验证回程链路。",
            ]
        )

    # -- BUY-signal templates (M-006 — 4 budget-tier variants) ---------

    def render_buy_signal(
        self,
        plan: InstructionPlan,
        *,
        template: BuySignalTemplate,
        pilot: bool = False,
    ) -> str:
        """Render a BUY signal in one of the three order-bearing templates.

        ``NORMAL_COMPLIANT`` / ``ETF_CONCENTRATION_EXCEPTION`` / ``PAPER_ONLY``
        all carry a real BUY InstructionPlan; the shared 7-section body is
        reused so the wire shape is consistent, while a distinct header +
        banner make the budget-tier outcome unmistakable. The
        ``NO_COMPLIANT_TRADE`` outcome has no order and uses
        :meth:`render_no_compliant_trade` instead — passing it here is a
        fail-closed ``ValueError``.

        The ETF concentration-exception confirmation flow embeds the
        instruction_id in a reply instruction; it is re-validated against the
        canonical regex here (defence-in-depth — an instruction_id is the
        classic injection / leakage point, P0-2 §2.6 / CLAUDE.md §2.6).
        """
        # Coerce a raw-string template id (e.g. from JSON/config) to the enum
        # FIRST. BuySignalTemplate is a StrEnum, so a raw string would pass the
        # set-membership + header lookup below by string-equality yet fail the
        # later ``is`` identity check, silently dropping the ETF confirmation
        # block (codex M-006 P1). An invalid id raises ValueError (fail-closed).
        template = BuySignalTemplate(template)
        if template not in _PLAN_BACKED_BUY_TEMPLATES:
            raise ValueError(
                f"template {template.value!r} has no InstructionPlan — use "
                "render_no_compliant_trade"
            )
        if plan.side is not InstructionSide.BUY:
            raise ValueError(
                f"render_buy_signal is BUY-only, got {plan.side.value}"
            )
        self._assert_dispatchable(plan)
        if not _INSTRUCTION_ID_RE.fullmatch(plan.instruction_id):
            raise ValueError(
                f"instruction_id {plan.instruction_id!r} fails canonical pattern"
            )

        header, banner = _BUY_SIGNAL_HEADERS[template]
        body = self._dispatch_body_lines(plan)
        lines = [*self._pilot_prefix(pilot), header, banner, *body]
        if template is BuySignalTemplate.ETF_CONCENTRATION_EXCEPTION:
            # Confirmation block — the operator must explicitly confirm the
            # ETF concentration exception before it executes.
            lines.append("—— 需人工确认 ——")
            lines.append(f"确认执行请回复:确认 {plan.instruction_id}")
        return "\n".join(lines)

    def render_monitoring_sell(
        self,
        plan: InstructionPlan,
        *,
        anomaly_reason: str,
        pilot: bool = False,
    ) -> str:
        """Render a Line-2 monitoring SELL signal (N-002).

        Distinct header + an anomaly-trigger banner make it unmistakable that
        this is a position-management exit (Line-2), not a Line-1 stock pick;
        the shared 7-section dispatch body is reused so the operator sees the
        same order / risk / position sections. SELL-only — a BUY/HOLD plan is a
        fail-closed ``ValueError``.

        ``anomaly_reason`` is a deterministic detector string (never LLM) but
        is still collapsed to a single printable line so no embedded newline
        can spoof a 【QuantMind …】 header (P2-1 / CLAUDE.md §2.6). The
        instruction_id is re-validated against the canonical regex
        (defence-in-depth — the classic injection / leakage point).
        """
        if plan.side is not InstructionSide.SELL:
            raise ValueError(
                f"render_monitoring_sell is SELL-only, got {plan.side.value}"
            )
        if not plan.signal_id.startswith(_MONITORING_SIGNAL_PREFIX):
            # Fail closed: an ordinary Line-1 SELL must never be mislabeled with
            # the Line-2 monitoring header. The LINE2-MON- discriminator is the
            # builder's construction marker (P0-10-amendment-2026-05-25); the
            # renderer re-checks it so the wire text cannot diverge from it
            # (codex N-002 P3).
            raise ValueError(
                f"render_monitoring_sell requires a Line-2 plan "
                f"(signal_id {plan.signal_id!r} lacks {_MONITORING_SIGNAL_PREFIX!r})"
            )
        self._assert_dispatchable(plan)
        if not _INSTRUCTION_ID_RE.fullmatch(plan.instruction_id):
            raise ValueError(
                f"instruction_id {plan.instruction_id!r} fails canonical pattern"
            )
        lines = [
            *self._pilot_prefix(pilot),
            "【QuantMind 持仓监控 · 卖出信号】",
            f"异动触发: {_single_line(anomaly_reason)}",
            *self._dispatch_body_lines(plan),
        ]
        return "\n".join(lines)

    def render_add_position(
        self,
        plan: InstructionPlan,
        *,
        add_rationale: str,
        stop_price: float,
        pilot: bool = False,
    ) -> str:
        """Render a Line-2 add-position (补仓) signal (N-003).

        BUY-only — an ADD is a disciplined scale-in onto a held position. The
        banner carries the four-condition rationale + the ATR trailing-stop
        price; the shared 7-section body follows. Fails closed unless the plan
        is a Line-2 BUY (``LINE2-MON-`` signal_id) so an ordinary Line-1 BUY is
        never mislabeled as a monitoring add. ``add_rationale`` is a
        deterministic string but is still collapsed to a single printable line
        (anti header-spoof, P2-1 / CLAUDE.md §2.6).
        """
        if plan.side is not InstructionSide.BUY:
            raise ValueError(
                f"render_add_position is BUY-only, got {plan.side.value}"
            )
        if not plan.signal_id.startswith(_MONITORING_SIGNAL_PREFIX):
            raise ValueError(
                f"render_add_position requires a Line-2 plan "
                f"(signal_id {plan.signal_id!r} lacks {_MONITORING_SIGNAL_PREFIX!r})"
            )
        self._assert_dispatchable(plan)
        if not _INSTRUCTION_ID_RE.fullmatch(plan.instruction_id):
            raise ValueError(
                f"instruction_id {plan.instruction_id!r} fails canonical pattern"
            )
        lines = [
            *self._pilot_prefix(pilot),
            "【QuantMind 持仓监控 · 补仓信号】",
            f"补仓依据: {_single_line(add_rationale)}",
            f"移动止损: {_format_money(stop_price)} CNY",
            *self._dispatch_body_lines(plan),
        ]
        return "\n".join(lines)

    def render_no_compliant_trade(
        self,
        *,
        stock_code: str,
        stock_name: str,
        reason: str,
    ) -> str:
        """Render the NO_COMPLIANT_TRADE first-class outcome (no order).

        Sent when the budget tier admits no compliant trade (e.g. Micro
        budget below the ETF lot floor). Carries the candidate identity + a
        sanitised reason; deliberately has NO order fields so it can never be
        mistaken for a dispatchable instruction.
        """
        if not _STOCK_CODE_RE.fullmatch(stock_code):
            raise ValueError(f"stock_code {stock_code!r} must be 6 digits")
        return "\n".join(
            [
                "【QuantMind 无合规交易】",
                f"标的: {stock_code} {_single_line(stock_name)}",
                "结论: 当前预算/约束下无合规可交易方案,本次不产生指令。",
                f"原因: {_single_line(reason)}",
            ]
        )

    @staticmethod
    def _pilot_prefix(pilot: bool) -> list[str]:
        """Single-source PILOT banner prefix (P0-6-amendment-2026-05-25 §2.3).

        Returns the banner as the leading line when the active go-live tier is
        PILOT, else an empty list (FULL / simulation paths are unchanged). The
        banner is a code constant — never interpolated — so it cannot be
        spoofed or drift across the three order-bearing renderers.
        """
        return [_PILOT_BANNER] if pilot else []

    @staticmethod
    def _assert_dispatchable(plan: InstructionPlan) -> None:
        """Shared guard: only VALIDATED/DISPATCHED BUY/SELL plans dispatch."""
        if plan.side is InstructionSide.HOLD:
            raise ValueError(
                "HOLD plan is not routable — must only render for BUY/SELL "
                "(P0-3 §1.3.1)"
            )
        if plan.status not in (
            InstructionStatus.VALIDATED,
            InstructionStatus.DISPATCHED,
        ):
            raise ValueError(
                f"status={plan.status.value} cannot be dispatched to "
                "Feishu — VALIDATED or DISPATCHED only"
            )

    def _dispatch_body_lines(self, plan: InstructionPlan) -> list[str]:
        """The shared 7-section dispatch body (everything after the header).

        Reused verbatim by :meth:`render_instruction_plan` and
        :meth:`render_buy_signal` so the operator sees identical order /
        risk / position / report sections regardless of the template header.
        """
        side_zh = "买入" if plan.side is InstructionSide.BUY else "卖出"
        amount_cny = (
            Decimal(str(plan.limit_price))  # type: ignore[arg-type]
            * Decimal(plan.volume)  # type: ignore[arg-type]
        )
        return [
            f"指令编号: {plan.instruction_id}",
            f"操作: {side_zh} {plan.stock_code} {plan.stock_name}",
            f"股数: {plan.volume} 股",
            f"限价: {_format_money(plan.limit_price)} CNY",
            f"预计金额: {_format_money(amount_cny)} CNY",
            f"有效期: {_format_local_ts(plan.valid_until)} 前(同日 14:55 截止)",
            _format_position_summary(plan),
            "—— 风险摘要 ——",
            *_format_risk_summary(plan.risk_summary),
            f"失效说明: {plan.invalidation_summary}",
            "—— 回报模板(原文回复)——",
            *_REPORT_TEMPLATE_BLOCK,
        ]

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
            # P2-1: normalise to single-line printable text BEFORE
            # truncating so an embedded newline cannot smuggle a fake
            # 【QuantMind ...】 header into the body.
            safe_excerpt = _truncate(_single_line(raw_text_excerpt), 80)
            excerpt_block = f"\n原始文本节选: {safe_excerpt}"
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
        # P2-1: alerts must NEVER contain literal newlines — every
        # newline would risk spoofing a 【QuantMind ...】 header. Even
        # though _strip_controls preserved newlines, _single_line
        # actively collapses them so an alerter caller cannot inject
        # a fake reconciliation / instruction line.
        sanitized = _single_line(message)
        return "\n".join(
            [
                f"【QuantMind 告警 / {severity.upper()}】",
                f"类型: {alert_type}",
                f"时间: {_format_local_ts(fired_at)}",
                f"消息: {sanitized}",
            ]
        )

    # -- Self-evolution pending notification (X-014 surface) -----------

    def render_evolution_pending(
        self,
        *,
        amendment_id: str,
        artifact_type: str,
        artifact_id: str,
        amendment_path: str,
    ) -> str:
        """Compose the single-line summary the alerter sends to the
        alert chat after the X-013 amendment drafter writes a draft.

        Returned as a single sanitised line so :meth:`render_alert`
        does not have to collapse newlines a second time. The body
        contains the *identifiers* the operator needs to find the
        draft inside SystemStatus.vue self-evolution panel; the prompt
        text and shadow metric details are intentionally NOT included
        (P2-2 §2 — keep prompt-injection vectors out of the alert chat).
        """
        if artifact_type not in _EVOLUTION_ARTIFACT_TYPES:
            raise ValueError(
                f"artifact_type {artifact_type!r} is not one of "
                f"{sorted(_EVOLUTION_ARTIFACT_TYPES)}"
            )
        if not _PENDING_AMENDMENT_PATH_RE.fullmatch(amendment_path):
            raise ValueError(
                f"amendment_path must live under docs/decisions/pending/, "
                f"got {amendment_path!r}"
            )
        safe_amendment_id = _single_line(amendment_id)
        safe_artifact_id = _single_line(artifact_id)
        return (
            f"自进化 pending: id={safe_amendment_id} type={artifact_type} "
            f"artifact={safe_artifact_id} path={amendment_path}"
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


def _single_line(text: str) -> str:
    """Collapse every newline / control character to a single space.

    Used before interpolating operator-controlled free text into the
    template body. Without this normalisation a malicious user could
    embed a literal ``\\n【QuantMind 指令】xxx`` inside their raw text
    and have it render as if a new top-level message header started
    mid-body (codex review session #14 P2-1).
    """
    import unicodedata as _u

    out: list[str] = []
    for ch in text:
        if ch in {"\n", "\r", "\t", "\v", "\f"}:
            out.append(" ")
        elif _u.category(ch).startswith("C"):
            # Drop other C-category control codepoints entirely.
            continue
        else:
            out.append(ch)
    # Collapse runs of whitespace to a single space — keeps the body
    # visually clean and prevents wide spacing tricks.
    return " ".join("".join(out).split())


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
_MONITORING_SIGNAL_PREFIX = "LINE2-MON-"
"""Line-2 ``signal_id`` discriminator (mirrors
``instruction_plan_builder.MONITORING_SIGNAL_PREFIX``). Inlined here so the
renderer stays decoupled from backend.services; ``render_monitoring_sell`` /
``render_add_position`` fail closed unless the plan carries it."""
_RECONCILIATION_ID_RE = re.compile(r"^RECON-\d{8}-\d{3}$")
_TRADE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STOCK_CODE_RE = re.compile(r"^\d{6}$")

# PILOT go-live banner (P0-6-amendment-2026-05-25 §2.3). Single-source
# constant — the renderer is the ONLY place this string is composed so the
# "模拟盘·人工执行·试点" framing can never drift or be spoofed (it is a code
# constant, never interpolated). Prepended as the FIRST line of every
# order-bearing Feishu message while the active go-live tier is PILOT, so the
# operator can never mistake a試点 signal for a real / fully-graduated one.
_PILOT_BANNER = "「模拟盘 · 人工执行 · 试点」"

# (header, banner) per order-bearing BUY template (M-006). Snapshot-locked in
# tests/test_feishu_buy_signal.py — the headers are the at-a-glance visual
# discriminator between budget-tier outcomes.
_BUY_SIGNAL_HEADERS: Mapping[BuySignalTemplate, tuple[str, str]] = {
    BuySignalTemplate.NORMAL_COMPLIANT: (
        "【QuantMind 买入信号 · 合规】",
        "分层: 正常合规额度,经 14-check 通过。",
    ),
    BuySignalTemplate.ETF_CONCENTRATION_EXCEPTION: (
        "【QuantMind 买入信号 · ETF 集中度例外 · 需确认】",
        "分层: ETF 集中度例外(RiskEngine 独立再校验通过),需人工确认。",
    ),
    BuySignalTemplate.PAPER_ONLY: (
        "【QuantMind 买入信号 · 仅模拟】",
        "分层: 预算分层为仅模拟(paper),仅记入模拟账本,不构成实盘建议。",
    ),
}
_PENDING_AMENDMENT_PATH_RE = re.compile(
    r"^docs/decisions/pending/[A-Za-z0-9._-]+\.md$"
)
"""Lock the path shape so the alert chat can never advertise a file
outside ``docs/decisions/pending/`` (P2-2 §2 — amendments live there
exclusively before owner review)."""

_EVOLUTION_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        "prompt",
        "rag_document",
        "risk_parameter_proposal",
        "exemplar_schema",
    }
)
"""Four artifact discriminators the X-008 EvolutionDispatcher routes."""


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
    "BuySignalTemplate",
    "ClarificationTemplate",
    "FeishuMessageKind",
    "MessageRenderer",
]
