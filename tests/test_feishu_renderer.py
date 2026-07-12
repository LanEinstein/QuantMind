"""F-002 — MessageRenderer snapshot + invariant tests.

The renderer is the **single source of truth** for Feishu wire text;
this file freezes the exact output for every locked message kind so
the snapshot fails any time a future change touches the body. Golden
strings are kept right next to the assertions for human review.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.feishu.renderer import (
    ClarificationTemplate,
    FeishuMessageKind,
    MessageRenderer,
)
from backend.models.execution import (
    ExecutionReport,
    ExecutionReportChannel,
    ExecutionReportKind,
    ExecutionReportPrefix,
)
from backend.models.instruction import (
    DataSnapshot,
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    PositionSummary,
    RiskCheckSummary,
)
from backend.services.execution_report_parser import (
    ExecutionReportParseError,
    parse_execution_report,
)

_SH = ZoneInfo("Asia/Shanghai")


def _risk_summary_passed() -> tuple[RiskCheckSummary, ...]:
    """14-row risk summary, all PASS — locks the dispatch happy-path."""
    rules = (
        "single_stock_pct_<=15",
        "price_deviation_<=10",
        "volume_multiple_of_lot",
        "long_only",
        "single_cash_<=5w",
        "no_open_ticket",
        "valid_until_<=14:55",
        "total_position_pct_<=70",
        "single_instruction_amount_<=50000",
        "daily_new_instruction_count_<=5",
        "universe_whitelist",
        "limit_up_down_block",
        "daily_loss_halt_>=-5",
        "consecutive_loss_<3",
    )
    return tuple(
        RiskCheckSummary(rule_name=name, passed=True, message="")
        for name in rules
    )


def _buy_plan() -> InstructionPlan:
    created = datetime(2026, 5, 16, 10, 30, 0, tzinfo=_SH)
    snapshot = datetime(2026, 5, 16, 10, 29, 50, tzinfo=_SH)
    valid_until = datetime(2026, 5, 16, 14, 55, 0, tzinfo=_SH)
    return InstructionPlan(
        instruction_id="QM-20260516-103000-510300-BUY-001",
        created_at=created,
        valid_until=valid_until,
        trade_date="2026-05-16",
        stock_code="510300",
        stock_name="沪深 300 ETF",
        side=InstructionSide.BUY,
        volume=1000,
        limit_price=3.85,
        data_snapshot=DataSnapshot(
            snapshot_at=snapshot,
            quote_source="adata",
            is_trading_day=True,
            is_trading_hours=True,
        ),
        evidence_ids=("NEWS-20260516-0001",),
        position_summary=PositionSummary(
            pre_position_pct=0.04,
            post_position_pct=0.078,
            pre_total_position_pct=0.32,
            post_total_position_pct=0.358,
            pre_cash=200_000.0,
            post_cash=196_150.0,
        ),
        risk_summary=_risk_summary_passed(),
        risk_validation_id="rv-001",
        signal_id="sig-001",
        analysis_record_id="ar-001",
        debate_round_count=1,
        invalidation_summary="若沪深 300 当日跌幅 ≥ 1% 即失效",
        status=InstructionStatus.VALIDATED,
    )


def _hold_plan() -> InstructionPlan:
    created = datetime(2026, 5, 16, 10, 30, 0, tzinfo=_SH)
    snapshot = datetime(2026, 5, 16, 10, 29, 50, tzinfo=_SH)
    valid_until = datetime(2026, 5, 16, 14, 55, 0, tzinfo=_SH)
    return InstructionPlan(
        instruction_id="QM-20260516-103000-510300-HOLD-001",
        created_at=created,
        valid_until=valid_until,
        trade_date="2026-05-16",
        stock_code="510300",
        stock_name="沪深 300 ETF",
        side=InstructionSide.HOLD,
        data_snapshot=DataSnapshot(
            snapshot_at=snapshot,
            quote_source="adata",
            is_trading_day=True,
            is_trading_hours=True,
        ),
        evidence_ids=("NEWS-20260516-0001",),
        risk_summary=_risk_summary_passed(),
        risk_validation_id="rv-001",
        signal_id="sig-001",
        analysis_record_id="ar-001",
        debate_round_count=1,
        invalidation_summary="HOLD",
        status=InstructionStatus.VALIDATED,
    )


# -----------------------------------------------------------------------------
# InstructionPlan dispatch — snapshot lock
# -----------------------------------------------------------------------------


class TestInstructionPlanDispatch:
    def test_buy_plan_golden_snapshot(self) -> None:
        rendered = MessageRenderer().render_instruction_plan(_buy_plan())
        expected = (
            "【QuantMind 指令】\n"
            "指令编号: QM-20260516-103000-510300-BUY-001\n"
            "操作: 买入 510300 沪深 300 ETF\n"
            "股数: 1000 股\n"
            "限价: 3.85 CNY\n"
            "预计金额: 3850.00 CNY\n"
            "有效期: 2026-05-16 14:55:00 前(同日 14:55 截止)\n"
            "仓位预览: 单股 4.00% → 7.80% · 总仓 32.00% → 35.80% · "
            "现金 200000.00 → 196150.00 CNY\n"
            "—— 风险摘要 ——\n"
            "  ✓ #01 single_stock_pct_<=15\n"
            "  ✓ #02 price_deviation_<=10\n"
            "  ✓ #03 volume_multiple_of_lot\n"
            "  ✓ #04 long_only\n"
            "  ✓ #05 single_cash_<=5w\n"
            "  ✓ #06 no_open_ticket\n"
            "  ✓ #07 valid_until_<=14:55\n"
            "  ✓ #08 total_position_pct_<=70\n"
            "  ✓ #09 single_instruction_amount_<=50000\n"
            "  ✓ #10 daily_new_instruction_count_<=5\n"
            "  ✓ #11 universe_whitelist\n"
            "  ✓ #12 limit_up_down_block\n"
            "  ✓ #13 daily_loss_halt_>=-5\n"
            "  ✓ #14 consecutive_loss_<3\n"
            "失效说明: 若沪深 300 当日跌幅 ≥ 1% 即失效\n"
            "—— 回报模板(原文回复)——\n"
            "1. 已执行 <编号> <买入|卖出> <代码> <股数>股 成交价 <价>\n"
            "2. 部分执行 <编号> <买入|卖出> <代码> <成交>股 成交价 <价> "
            "剩余 <未成交>股\n"
            "3. 未执行 <编号> 原因: <原因>\n"
            "4. 更正/盘后补录: 在 1/2/3 前加 `更正 ` 或 `盘后补录 `\n"
            "(只填成交价(每股)+ 股数;手续费/过户费/印花税由系统按真实费率计算)"
        )
        assert rendered == expected

    def test_failed_check_renders_x_marker(self) -> None:
        plan = _buy_plan()
        # Tamper with the risk_summary tuple to flip one row to False.
        failed_summary = list(plan.risk_summary)
        failed_summary[10] = RiskCheckSummary(
            rule_name="universe_whitelist",
            passed=False,
            message="股票不在 watchlist",
        )
        plan_failing = plan.model_copy(
            update={
                "risk_summary": tuple(failed_summary),
                "status": InstructionStatus.REJECTED,
                "rejection_reason": "universe_whitelist failed",
            }
        )
        # REJECTED plans are not dispatchable — verify our guard fires.
        with pytest.raises(ValueError, match="VALIDATED or DISPATCHED"):
            MessageRenderer().render_instruction_plan(plan_failing)

    def test_pending_check_renders_em_dash(self) -> None:
        plan = _buy_plan()
        pending_summary = list(plan.risk_summary)
        pending_summary[7] = RiskCheckSummary(
            rule_name="total_position_pct_<=70",
            passed=None,
            message="",
        )
        plan_pending = plan.model_copy(
            update={"risk_summary": tuple(pending_summary)}
        )
        rendered = MessageRenderer().render_instruction_plan(plan_pending)
        assert "  — #08 total_position_pct_<=70" in rendered

    def test_hold_plan_rejected_to_send(self) -> None:
        with pytest.raises(ValueError, match="HOLD"):
            MessageRenderer().render_instruction_plan(_hold_plan())

    def test_only_validated_or_dispatched_status_renders(self) -> None:
        plan = _buy_plan()
        for status in (
            InstructionStatus.DRAFT,
            InstructionStatus.FILLED,
            InstructionStatus.EXPIRED,
        ):
            altered = plan.model_copy(update={"status": status})
            with pytest.raises(
                ValueError, match="VALIDATED or DISPATCHED"
            ):
                MessageRenderer().render_instruction_plan(altered)

    def test_dispatched_status_passes(self) -> None:
        plan = _buy_plan().model_copy(
            update={"status": InstructionStatus.DISPATCHED}
        )
        rendered = MessageRenderer().render_instruction_plan(plan)
        assert "指令编号: " in rendered

    def test_sell_plan_renders_sell_label(self) -> None:
        plan = _buy_plan()
        sell_plan = plan.model_copy(
            update={
                "instruction_id": "QM-20260516-103000-510300-SELL-001",
                "side": InstructionSide.SELL,
            }
        )
        rendered = MessageRenderer().render_instruction_plan(sell_plan)
        assert "操作: 卖出 510300" in rendered

    def test_amount_calc_is_decimal_precise(self) -> None:
        plan = _buy_plan().model_copy(update={"limit_price": 0.1, "volume": 300})
        rendered = MessageRenderer().render_instruction_plan(plan)
        # 0.1 × 300 = 30.00 exact decimal
        assert "预计金额: 30.00 CNY" in rendered


# -----------------------------------------------------------------------------
# Clarification — five templates locked
# -----------------------------------------------------------------------------


class TestClarification:
    @pytest.mark.parametrize(
        "template", list(ClarificationTemplate)
    )
    def test_each_template_renders(
        self, template: ClarificationTemplate
    ) -> None:
        rendered = MessageRenderer().render_clarification(template=template)
        assert rendered.startswith("【QuantMind 澄清】\n")
        assert "—— 请按以下格式回复 ——\n" in rendered

    def test_no_pattern_match_snapshot(self) -> None:
        rendered = MessageRenderer().render_clarification(
            template=ClarificationTemplate.NO_PATTERN_MATCH,
            instruction_id="QM-20260516-103000-510300-BUY-001",
            raw_text_excerpt="不认识的格式 xxx",
        )
        expected = (
            "【QuantMind 澄清】\n"
            "无法识别消息内容,无法应用到模拟账本。\n"
            "指令编号: QM-20260516-103000-510300-BUY-001\n"
            "原始文本节选: 不认识的格式 xxx\n"
            "—— 请按以下格式回复 ——\n"
            "1. 已执行 <编号> <买入|卖出> <代码> <股数>股 成交价 <价>\n"
            "2. 部分执行 <编号> <买入|卖出> <代码> <成交>股 成交价 <价> "
            "剩余 <未成交>股\n"
            "3. 未执行 <编号> 原因: <原因>\n"
            "4. 更正/盘后补录: 在 1/2/3 前加 `更正 ` 或 `盘后补录 `\n"
            "(只填成交价(每股)+ 股数;手续费/过户费/印花税由系统按真实费率计算)"
        )
        assert rendered == expected

    def test_omitted_instruction_id_drops_line(self) -> None:
        rendered = MessageRenderer().render_clarification(
            template=ClarificationTemplate.EMPTY_PAYLOAD,
        )
        assert "指令编号:" not in rendered
        assert "原始文本节选:" not in rendered

    def test_invalid_instruction_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="canonical pattern"):
            MessageRenderer().render_clarification(
                template=ClarificationTemplate.NO_PATTERN_MATCH,
                instruction_id="not-an-id",
            )

    def test_excerpt_truncated_to_80_chars(self) -> None:
        long_excerpt = "x" * 200
        rendered = MessageRenderer().render_clarification(
            template=ClarificationTemplate.NO_PATTERN_MATCH,
            raw_text_excerpt=long_excerpt,
        )
        # Truncated to 80 chars total including ellipsis.
        excerpt_line = next(
            line for line in rendered.splitlines() if line.startswith("原始文本节选: ")
        )
        body = excerpt_line.removeprefix("原始文本节选: ")
        assert len(body) == 80
        assert body.endswith("…")


# -----------------------------------------------------------------------------
# Execution ack (P0-4-amendment-2026-05-30b) — success confirmation
# -----------------------------------------------------------------------------


def _exec_report(
    *,
    kind: ExecutionReportKind = ExecutionReportKind.FILLED,
    prefix: ExecutionReportPrefix = ExecutionReportPrefix.POST_CLOSE,
    side_zh: str | None = "买入",
    stock_code: str | None = "510300",
    filled_volume: int | None = 100,
    remain_volume: int | None = None,
    fill_price: float | None = 4.0,
    fee: float | None = None,
    reason: str | None = None,
) -> ExecutionReport:
    return ExecutionReport(
        report_id="erp-test-1",
        instruction_id="QM-20260529-093500-510300-BUY-001",
        kind=kind,
        prefix=prefix,
        channel=ExecutionReportChannel.FEISHU,
        side_zh=side_zh,
        stock_code=stock_code,
        filled_volume=filled_volume,
        remain_volume=remain_volume,
        fill_price=fill_price,
        fee=fee,
        reason=reason,
        raw_text=(
            "盘后补录 已执行 QM-20260529-093500-510300-BUY-001 "
            "买入 510300 100股 成交价 4.00"
        ),
        received_at=datetime(2026, 5, 29, 2, 0, 0, tzinfo=ZoneInfo("UTC")),
        parsed_at=datetime(2026, 5, 29, 2, 0, 1, tzinfo=ZoneInfo("UTC")),
    )


class TestExecutionAck:
    def test_filled_ack_echoes_applied_fields(self) -> None:
        body = MessageRenderer().render_execution_ack(
            report=_exec_report(),
            cash_delta=-405.0,
            broker_event_sequence=11,
        )
        assert body.startswith("【QuantMind 已记录】")
        assert "QM-20260529-093500-510300-BUY-001" in body
        assert "已执行(盘后补录)" in body
        assert "买入 510300 100股 @ 4.0" in body
        assert "-405" in body  # cash outflow
        assert "510300 +100 股" in body  # position delta
        assert "账本序号: 11" in body

    def test_unfilled_ack_single_lines_reason(self) -> None:
        body = MessageRenderer().render_execution_ack(
            report=_exec_report(
                kind=ExecutionReportKind.UNFILLED,
                prefix=ExecutionReportPrefix.NONE,
                side_zh=None,
                stock_code=None,
                filled_volume=None,
                fill_price=None,
                reason="客户\n临时\r取消",
            ),
            cash_delta=0.0,
            broker_event_sequence=None,
        )
        assert "记录为未成交" in body
        # newlines collapsed so no fake 【...】 header can be smuggled in
        assert "客户 临时 取消" in body
        # a None broker sequence is omitted, not rendered as 'None'
        assert "账本序号" not in body

    def test_duplicate_ack_does_not_fabricate_recorded(self) -> None:
        # A deduped re-delivery must NOT render a confident "已记录 + cash +0.00".
        body = MessageRenderer().render_execution_ack(
            report=_exec_report(),
            cash_delta=0.0,
            broker_event_sequence=None,
            is_duplicate=True,
        )
        assert "【QuantMind 已收到】" in body
        assert "未重复入账" in body
        assert "【QuantMind 已记录】" not in body  # not the fresh-record header
        assert "账本现金变动" not in body  # no fabricated zero-cash movement

    def test_negative_zero_cash_delta_no_double_sign(self) -> None:
        body = MessageRenderer().render_execution_ack(
            report=_exec_report(
                kind=ExecutionReportKind.UNFILLED,
                prefix=ExecutionReportPrefix.NONE,
                side_zh=None,
                stock_code=None,
                filled_volume=None,
                fill_price=None,
                reason="无成交",
            ),
            cash_delta=-0.0,
            broker_event_sequence=None,
        )
        assert "+-" not in body
        assert "账本现金变动: 0.00 CNY" in body

    def test_bad_instruction_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="canonical pattern"):
            MessageRenderer().render_execution_ack(
                report=_exec_report().model_copy(
                    update={"instruction_id": "QM-bad"}
                ),
                cash_delta=-405.0,
                broker_event_sequence=1,
            )


# -----------------------------------------------------------------------------
# Reconciliation request / result
# -----------------------------------------------------------------------------


class TestReconciliation:
    def test_request_snapshot(self) -> None:
        rendered = MessageRenderer().render_reconciliation_request(
            ticket_id="RECON-20260516-001",
            trade_date="2026-05-16",
            expected_cash_cny=199_500.50,
            expected_positions={"510300": 1000, "600519": 100},
            expected_total_equity_cny=275_000.00,
        )
        expected = (
            "【QuantMind 对账】\n"
            "对账编号: RECON-20260516-001\n"
            "交易日: 2026-05-16\n"
            "系统现金: 199500.50 CNY\n"
            "系统持仓:\n"
            "  510300 1000 股\n"
            "  600519 100 股\n"
            "系统总权益: 275000.00 CNY\n"
            "—— 请按以下格式之一回复(编号 RECON-20260516-001 原样带上)——\n"
            "· 无误: 对账无误 RECON-20260516-001\n"
            "· 差异(回报实际持仓): 对账差异 RECON-20260516-001 现金 <数额> "
            "持仓 <code> <股数>股 成本 <成本>; …(无持仓填: 无)\n"
            "· 更正(改上次回报): 对账更正 RECON-20260516-001 现金 <数额> "
            "持仓 <code> <股数>股 成本 <成本>; …(无持仓填: 无)\n"
            "· 采纳用户回报: 对账采纳：用户回报 RECON-20260516-001\n"
            "· 采纳系统镜像: 对账采纳：系统镜像 RECON-20260516-001"
        )
        assert rendered == expected

    def test_request_instructions_match_active_parser(self) -> None:
        # P0-5-amendment-2026-06-03 follow-up: the rendered reply instructions
        # MUST match the active parse_reconciliation_reply grammar. They had
        # drifted (采纳镜像 / 采纳回报) — never caught because the initiate path
        # was never wired. Round-trip concrete instances of each instructed form.
        from backend.services.reconciliation_parser import (
            ReconciliationReplyKind,
            parse_reconciliation_reply,
        )

        tid = "RECON-20260516-001"
        rendered = MessageRenderer().render_reconciliation_request(
            ticket_id=tid,
            trade_date="2026-05-16",
            expected_cash_cny=100000.0,
            expected_positions={},
            expected_total_equity_cny=100000.0,
        )
        # Instructed command prefixes appear verbatim in the prompt...
        assert f"对账无误 {tid}" in rendered
        assert f"对账差异 {tid}" in rendered
        assert f"对账更正 {tid}" in rendered
        assert f"对账采纳：用户回报 {tid}" in rendered
        assert f"对账采纳：系统镜像 {tid}" in rendered
        # ...and concrete instances of each parse to the expected kind.
        assert (
            parse_reconciliation_reply(f"对账无误 {tid}").kind
            is ReconciliationReplyKind.OK
        )
        assert (
            parse_reconciliation_reply(
                f"对账差异 {tid} 现金 65123.86 持仓 605111 200股 成本 63.035"
            ).kind
            is ReconciliationReplyKind.MISMATCH
        )
        assert (
            parse_reconciliation_reply(
                f"对账更正 {tid} 现金 65123.86 持仓 605111 200股 成本 63.035"
            ).kind
            is ReconciliationReplyKind.AMEND
        )
        assert (
            parse_reconciliation_reply(f"对账采纳：用户回报 {tid}").kind
            is ReconciliationReplyKind.RESOLVE_USER
        )
        assert (
            parse_reconciliation_reply(f"对账采纳：系统镜像 {tid}").kind
            is ReconciliationReplyKind.RESOLVE_SYSTEM
        )
        # The stale (parser-rejected) forms must be gone.
        assert "采纳镜像" not in rendered
        assert "采纳回报" not in rendered

    def test_request_empty_positions(self) -> None:
        rendered = MessageRenderer().render_reconciliation_request(
            ticket_id="RECON-20260516-001",
            trade_date="2026-05-16",
            expected_cash_cny=1_000_000.0,
            expected_positions={},
            expected_total_equity_cny=1_000_000.0,
        )
        assert "  (无持仓)" in rendered

    def test_request_rejects_bad_ticket_id(self) -> None:
        with pytest.raises(ValueError, match="RECON"):
            MessageRenderer().render_reconciliation_request(
                ticket_id="bad",
                trade_date="2026-05-16",
                expected_cash_cny=0.0,
                expected_positions={},
                expected_total_equity_cny=0.0,
            )

    def test_request_rejects_bad_trade_date(self) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            MessageRenderer().render_reconciliation_request(
                ticket_id="RECON-20260516-001",
                trade_date="2026/05/16",
                expected_cash_cny=0.0,
                expected_positions={},
                expected_total_equity_cny=0.0,
            )

    def test_result_snapshot(self) -> None:
        rendered = MessageRenderer().render_reconciliation_result(
            ticket_id="RECON-20260516-001",
            resolution="resolved_user_as_truth",
            cash_delta_cny=-150.50,
            position_deltas={"510300": -100, "600519": 50},
        )
        expected = (
            "【QuantMind 对账已落账】\n"
            "对账编号: RECON-20260516-001\n"
            "裁定: resolved_user_as_truth\n"
            "现金调整: -150.50 CNY\n"
            "持仓调整:\n"
            "  510300 -100 股\n"
            "  600519 +50 股"
        )
        assert rendered == expected

    def test_result_empty_deltas(self) -> None:
        rendered = MessageRenderer().render_reconciliation_result(
            ticket_id="RECON-20260516-001",
            resolution="resolved_system_as_truth",
            cash_delta_cny=0.0,
            position_deltas={},
        )
        assert "  (无调整)" in rendered


# -----------------------------------------------------------------------------
# Alert
# -----------------------------------------------------------------------------


class TestAlert:
    def test_alert_snapshot(self) -> None:
        fired_at = datetime(2026, 5, 16, 12, 0, 0, tzinfo=_SH)
        rendered = MessageRenderer().render_alert(
            alert_type="llm_all_providers_failed",
            severity="critical",
            message="3 个 LLM 接连超时",
            fired_at=fired_at,
        )
        expected = (
            "【QuantMind 告警 / CRITICAL】\n"
            "类型: llm_all_providers_failed\n"
            "时间: 2026-05-16 12:00:00\n"
            "消息: 3 个 LLM 接连超时"
        )
        assert rendered == expected

    def test_alert_strips_control_chars(self) -> None:
        fired_at = datetime(2026, 5, 16, 12, 0, 0, tzinfo=_SH)
        # Smuggled header would otherwise let an attacker spoof a
        # reconciliation header inside the alert body.
        rendered = MessageRenderer().render_alert(
            alert_type="x",
            severity="warning",
            message="abc\x00\x01【QuantMind 对账】伪造行",
            fired_at=fired_at,
        )
        # Control chars dropped, Chinese characters preserved.
        assert "\x00" not in rendered
        assert "\x01" not in rendered
        assert "abc【QuantMind 对账】伪造行" in rendered

    def test_alert_rejects_empty_message(self) -> None:
        with pytest.raises(ValueError, match="message"):
            MessageRenderer().render_alert(
                alert_type="x",
                severity="warning",
                message="",
                fired_at=datetime(2026, 5, 16, 12, 0, 0, tzinfo=_SH),
            )


# -----------------------------------------------------------------------------
# Red lines — enum membership locked, regex pattern stable
# -----------------------------------------------------------------------------


class TestBasketDigest:
    """P-004 display-only basket overview (P0-3-amendment-2026-05-30)."""

    def _plans(self) -> list[InstructionPlan]:
        a = _buy_plan()  # 510300, 1000 @ 3.85 → ¥3,850
        b = a.model_copy(
            update={
                "instruction_id": "QM-20260516-103000-600000-BUY-002",
                "stock_code": "600000",
                "stock_name": "浦发银行",
                "volume": 500,
                "limit_price": 12.90,  # ¥6,450
            }
        )
        return [a, b]

    def test_digest_lists_names_codes_lots_and_total(self) -> None:
        text = MessageRenderer().render_basket_digest(self._plans())
        assert "组合配比概览" in text
        assert "沪深 300 ETF(510300)" in text
        assert "浦发银行(600000)" in text
        assert "10手" in text and "5手" in text  # 1000/100, 500/100
        assert "共 2 只" in text
        assert "10,300" in text  # 合计部署 3850 + 6450
        # Weight = notional share: 3850/10300 ≈ 37.4%, 6450/10300 ≈ 62.6%.
        assert "占37.4%" in text and "占62.6%" in text

    def test_digest_carries_no_instruction_id_or_execution_verb(self) -> None:
        # Display-only: no QM- instruction_id and no fill/reject verb that the
        # inbound parser keys on — it is a summary, never an order.
        text = MessageRenderer().render_basket_digest(self._plans())
        assert "QM-" not in text
        for verb in ("已成交", "已执行", "已拒绝", "部分成交", "废单"):
            assert verb not in text

    def test_digest_is_not_parseable_as_execution_report(self) -> None:
        # Adversarial: feeding the digest to the inbound parser MUST raise
        # no_pattern_match — it can never be mistaken for / parsed as an order.
        text = MessageRenderer().render_basket_digest(self._plans())
        with pytest.raises(ExecutionReportParseError) as exc:
            parse_execution_report(
                text,
                channel=ExecutionReportChannel.FEISHU,
                received_at=datetime(2026, 5, 31, 10, 0, tzinfo=_SH),
            )
        assert exc.value.reason == "no_pattern_match"

    def test_digest_pilot_banner(self) -> None:
        text = MessageRenderer().render_basket_digest(self._plans(), pilot=True)
        assert "试点" in text  # PILOT banner prepended

    def test_digest_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 1|>= 1|routed BUY"):
            MessageRenderer().render_basket_digest([])


class TestThesisReviewDigest:
    """W-003 — display-only post-close thesis-review digest."""

    def _verdicts(self) -> list:
        from backend.models.position_thesis import ThesisHealth
        from backend.services.thesis_advisory import ThesisAdvisoryVerdict

        return [
            ThesisAdvisoryVerdict(
                code="600519",
                instruction_id="QM-20260601-093500-600519-BUY-001",
                health=ThesisHealth.BROKEN,
                reason_text="主业受供应链冲击,逻辑破坏",
                evidence_id="DEBATE-thesis-20260602-600519",
                trade_date="2026-06-02",
            ),
            ThesisAdvisoryVerdict(
                code="000001",
                instruction_id="QM-20260601-093500-000001-BUY-002",
                health=ThesisHealth.INTACT,
                reason_text="基本面稳健,逻辑完好",
                evidence_id="DEBATE-thesis-20260602-000001",
                trade_date="2026-06-02",
            ),
        ]

    def test_digest_lists_codes_and_health_labels(self) -> None:
        text = MessageRenderer().render_thesis_review_digest(self._verdicts())
        assert "持仓复盘概览" in text
        assert "共复盘 2 只" in text
        assert "600519" in text and "逻辑破坏" in text
        assert "000001" in text and "逻辑完好" in text

    def test_digest_carries_no_instruction_id_or_execution_verb(self) -> None:
        text = MessageRenderer().render_thesis_review_digest(self._verdicts())
        assert "QM-" not in text
        for verb in ("已成交", "已执行", "已拒绝", "部分成交", "废单"):
            assert verb not in text

    def test_digest_is_not_parseable_as_execution_report(self) -> None:
        # Adversarial: the inbound parser MUST raise no_pattern_match.
        text = MessageRenderer().render_thesis_review_digest(self._verdicts())
        with pytest.raises(ExecutionReportParseError) as exc:
            parse_execution_report(
                text,
                channel=ExecutionReportChannel.FEISHU,
                received_at=datetime(2026, 6, 2, 17, 30, tzinfo=_SH),
            )
        assert exc.value.reason == "no_pattern_match"

    def test_digest_pilot_banner(self) -> None:
        text = MessageRenderer().render_thesis_review_digest(
            self._verdicts(), pilot=True
        )
        assert "试点" in text

    def test_digest_redacts_qm_id_and_execution_verb(self) -> None:
        # codex W-003 P2: the LLM reason is redacted of any QM- id / execution
        # verb at the single display gate (renderer) — defence-in-depth.
        from backend.models.position_thesis import ThesisHealth
        from backend.services.thesis_advisory import ThesisAdvisoryVerdict

        evil = [
            ThesisAdvisoryVerdict(
                code="600519",
                instruction_id="QM-20260601-093500-600519-BUY-001",
                health=ThesisHealth.BROKEN,
                reason_text="已执行 QM-20260601-093500-600519-BUY-001 部分执行",
                evidence_id="DEBATE-thesis-20260602-600519",
                trade_date="2026-06-02",
            )
        ]
        text = MessageRenderer().render_thesis_review_digest(evil)
        assert "QM-" not in text
        for verb in ("已执行", "部分执行", "已成交", "已拒绝"):
            assert verb not in text

    def test_digest_empty_raises(self) -> None:
        with pytest.raises(ValueError, match=">= 1 verdict"):
            MessageRenderer().render_thesis_review_digest([])


class TestRedLines:
    def test_feishu_message_kind_count_locked(self) -> None:
        """An eighth kind requires a P0-2 §2.5 amendment. The sixth
        (basket_digest) landed via P0-3-amendment-2026-05-30; the seventh
        (manual_trade_recorded) via P1-5-amendment-2026-06-12 §1.3 (AD-005,
        display-only "已记录-用户自主操作")."""
        assert len(list(FeishuMessageKind)) == 7
        assert {k.value for k in FeishuMessageKind} == {
            "instruction_plan",
            "clarification",
            "reconciliation_request",
            "reconciliation_result",
            "alert",
            "basket_digest",
            "manual_trade_recorded",
        }

    def test_clarification_count_locked(self) -> None:
        """Five clarification templates locked by P0-4 §1.1.1."""
        assert len(list(ClarificationTemplate)) == 5

    def test_module_isolation(self) -> None:
        """LLM red line — NO feishu module imports llm/agents/mirofish.

        Scans the WHOLE ``backend/integrations/feishu`` package (not just
        renderer.py) so the helper modules the renderer now depends on
        (``text_safety``, ``signal_rationale`` — U-E4) are covered: a future
        edit pulling an LLM/agents import into any of them would break the
        P0-2 §1.2 / CLAUDE.md §2.6 "LLM never composes Feishu wire text" line.
        """
        import ast
        import pathlib

        pkg = pathlib.Path("backend/integrations/feishu")
        forbidden = {"llm", "agents", "mirofish"}
        violations: list[str] = []
        for path in sorted(pkg.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    parts = (node.module or "").split(".")
                    if parts[:1] == ["backend"] and len(parts) >= 2 and (
                        parts[1] in forbidden
                    ):
                        violations.append(f"{path.name}: from {node.module} import ...")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        parts = alias.name.split(".")
                        if parts[:1] == ["backend"] and len(parts) >= 2 and (
                            parts[1] in forbidden
                        ):
                            violations.append(f"{path.name}: import {alias.name}")
        assert violations == []

    def test_instruction_id_regex_mirrors_b001(self) -> None:
        """Renderer's instruction_id regex must mirror P0-3 §1.2 source."""
        from backend.models.instruction import _INSTRUCTION_ID_PATTERN

        # The renderer copy of the regex matches the canonical pattern
        # exactly (modulo anchoring style).
        rendered_pattern = re.compile(
            r"^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$"
        ).pattern
        assert rendered_pattern == _INSTRUCTION_ID_PATTERN


class TestSleeveAdvisory:
    """SLV-1 — display-only defensive-sleeve forward target-book digest."""

    @staticmethod
    def _holdings() -> list[dict]:
        return [
            {
                "ts_code": "002271.SZ",
                "name": "东方雨虹",
                "dv_ratio": 16.0867,
                "close": 11.5,
                "target_weight_pct": 8.0,
            },
            {
                "ts_code": "000858.SZ",
                "name": "五粮液",
                "dv_ratio": 11.2946,
                "close": 73.69,
                "target_weight_pct": 8.0,
            },
        ]

    def _render(self, **overrides: object) -> str:
        kwargs: dict = {
            "status": "ACCRUING",
            "spec_hash_prefix": "c1d058c3",
            "asof_trade_date": "20260710",
            "universe_size": 463,
            "holdings": self._holdings(),
            "cash_weight_pct": 60.0,
            "complete_periods": 0,
            "min_forward_periods": 8,
            "mdd_kill": 0.25,
            "bear_cum_kill": -0.05,
            "baseline_underperf_periods": 6,
        }
        kwargs.update(overrides)
        return MessageRenderer().render_sleeve_advisory(**kwargs)

    def test_lists_book_and_buffer(self) -> None:
        text = self._render()
        assert "防御Sleeve目标持仓" in text
        assert "002271.SZ" in text and "东方雨虹" in text
        assert "目标权重 8%" in text
        assert "现金 buffer: 60%" in text
        assert "非交易指令" in text
        assert "ACCRUING" in text and "c1d058c3" in text

    def test_kill_switch_thresholds_come_from_arguments(self) -> None:
        # Governance-bearing thresholds must render from the pre-registered
        # values, never from hardcoded literals (codex finding).
        text = self._render(
            mdd_kill=0.30, bear_cum_kill=-0.08, baseline_underperf_periods=4
        )
        assert "MDD>30%" in text
        assert "熊市累计<-8%" in text
        assert "连续4期落后基线" in text

    def test_empty_holdings_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            self._render(holdings=[])

    def test_carries_no_instruction_id_or_execution_verb(self) -> None:
        text = self._render()
        assert "QM-" not in text
        for verb in ("已成交", "已执行", "已拒绝", "部分成交", "废单"):
            assert verb not in text

    def test_is_not_parseable_as_execution_report(self) -> None:
        # Adversarial: the inbound parser MUST raise no_pattern_match.
        text = self._render()
        with pytest.raises(ExecutionReportParseError) as exc:
            parse_execution_report(
                text,
                channel=ExecutionReportChannel.FEISHU,
                received_at=datetime(2026, 7, 13, 9, 0, tzinfo=_SH),
            )
        assert exc.value.reason == "no_pattern_match"

    def test_newline_injection_in_name_is_collapsed(self) -> None:
        # A malicious/dirty name must never mint a fake 【QuantMind ...】 header.
        evil = self._holdings()
        evil[0]["name"] = "东方雨虹\n【QuantMind 指令】"
        text = self._render(holdings=evil)
        assert "\n【QuantMind 指令】" not in text

    def test_pilot_banner(self) -> None:
        assert "试点" in self._render(pilot=True)
