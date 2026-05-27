"""M-006 — Feishu BUY-signal 4-template renderer tests.

Four visually-distinct BUY templates (budget-tier outcomes) all go through
``MessageRenderer`` (the single source of truth — no LLM composes wire text):
NORMAL_COMPLIANT / ETF_CONCENTRATION_EXCEPTION (needs confirmation) /
NO_COMPLIANT_TRADE / PAPER_ONLY. The ETF confirmation flow embeds the
instruction_id (validated against the canonical ^QM- regex — the classic
leakage point). Golden strings are kept beside the assertions for review.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.feishu.renderer import (
    BuySignalTemplate,
    MessageRenderer,
)
from backend.models.instruction import (
    DataSnapshot,
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    PositionSummary,
    RiskCheckSummary,
)

_SH = ZoneInfo("Asia/Shanghai")


def _risk_summary_passed() -> tuple[RiskCheckSummary, ...]:
    return tuple(
        RiskCheckSummary(rule_name=f"rule_{i:02d}", passed=True, message="")
        for i in range(14)
    )


def _buy_plan(
    *, side: InstructionSide = InstructionSide.BUY,
    status: InstructionStatus = InstructionStatus.VALIDATED,
) -> InstructionPlan:
    created = datetime(2026, 5, 16, 10, 30, 0, tzinfo=_SH)
    snapshot = datetime(2026, 5, 16, 10, 29, 50, tzinfo=_SH)
    valid_until = datetime(2026, 5, 16, 14, 55, 0, tzinfo=_SH)
    side_token = side.value
    return InstructionPlan(
        instruction_id=f"QM-20260516-103000-510300-{side_token}-001",
        created_at=created,
        valid_until=valid_until,
        trade_date="2026-05-16",
        stock_code="510300",
        stock_name="沪深300 ETF",
        side=side,
        volume=1000,
        limit_price=3.85,
        data_snapshot=DataSnapshot(
            snapshot_at=snapshot, quote_source="adata",
            is_trading_day=True, is_trading_hours=True,
        ),
        evidence_ids=("NEWS-20260516-0001",),
        position_summary=PositionSummary(
            pre_position_pct=0.04, post_position_pct=0.078,
            pre_total_position_pct=0.32, post_total_position_pct=0.358,
            pre_cash=200_000.0, post_cash=196_150.0,
        ),
        risk_summary=_risk_summary_passed(),
        risk_validation_id="rv-001",
        signal_id="sig-001",
        analysis_record_id="ar-001",
        debate_round_count=1,
        invalidation_summary="若沪深300当日跌幅≥1%即失效",
        status=status,
    )


@pytest.fixture
def renderer() -> MessageRenderer:
    return MessageRenderer()


# --------------------------------------------------------------------------
# Golden headers — the at-a-glance discriminator between the 4 outcomes
# --------------------------------------------------------------------------


def test_normal_compliant_header(renderer: MessageRenderer) -> None:
    out = renderer.render_buy_signal(
        _buy_plan(), template=BuySignalTemplate.NORMAL_COMPLIANT
    )
    assert out.startswith("【QuantMind 买入信号 · 合规】\n")
    assert "分层: 正常合规额度" in out
    # Shares the dispatch body with render_instruction_plan.
    assert "指令编号: QM-20260516-103000-510300-BUY-001" in out
    assert "股数: 1000 股" in out
    assert "—— 回报模板(原文回复)——" in out


def test_etf_concentration_exception_header_and_confirmation(
    renderer: MessageRenderer,
) -> None:
    out = renderer.render_buy_signal(
        _buy_plan(), template=BuySignalTemplate.ETF_CONCENTRATION_EXCEPTION
    )
    assert out.startswith("【QuantMind 买入信号 · ETF 集中度例外 · 需确认】\n")
    assert "RiskEngine 独立再校验通过" in out
    # Confirmation block embeds the (regex-valid) instruction_id verbatim.
    assert "—— 需人工确认 ——" in out
    assert "确认执行请回复:确认 QM-20260516-103000-510300-BUY-001" in out


def test_paper_only_header(renderer: MessageRenderer) -> None:
    out = renderer.render_buy_signal(
        _buy_plan(), template=BuySignalTemplate.PAPER_ONLY
    )
    assert out.startswith("【QuantMind 买入信号 · 仅模拟】\n")
    assert "仅记入模拟账本,不构成实盘建议" in out
    # Paper-only has no confirmation block.
    assert "需人工确认" not in out


def test_no_compliant_trade_has_no_order_fields(renderer: MessageRenderer) -> None:
    out = renderer.render_no_compliant_trade(
        stock_code="600519", stock_name="贵州茅台", reason="Micro 预算低于 ETF 一手成本"
    )
    assert out.startswith("【QuantMind 无合规交易】\n")
    assert "标的: 600519 贵州茅台" in out
    assert "本次不产生指令" in out
    assert "原因: Micro 预算低于 ETF 一手成本" in out
    # Crucially: no order/dispatch fields that could be mistaken for a trade.
    assert "股数" not in out
    assert "限价" not in out
    assert "指令编号" not in out


# --------------------------------------------------------------------------
# Guards — fail-closed inputs
# --------------------------------------------------------------------------


def test_no_compliant_trade_template_rejected_by_render_buy_signal(
    renderer: MessageRenderer,
) -> None:
    with pytest.raises(ValueError, match="render_no_compliant_trade"):
        renderer.render_buy_signal(
            _buy_plan(), template=BuySignalTemplate.NO_COMPLIANT_TRADE
        )


def test_render_buy_signal_is_buy_only(renderer: MessageRenderer) -> None:
    sell = _buy_plan(side=InstructionSide.SELL)
    with pytest.raises(ValueError, match="BUY-only"):
        renderer.render_buy_signal(
            sell, template=BuySignalTemplate.NORMAL_COMPLIANT
        )


def test_render_buy_signal_rejects_non_dispatchable_status(
    renderer: MessageRenderer,
) -> None:
    draft = _buy_plan(status=InstructionStatus.DRAFT)  # not yet VALIDATED
    with pytest.raises(ValueError, match="cannot be dispatched"):
        renderer.render_buy_signal(
            draft, template=BuySignalTemplate.NORMAL_COMPLIANT
        )


def test_no_compliant_trade_rejects_bad_code(renderer: MessageRenderer) -> None:
    with pytest.raises(ValueError, match="6 digits"):
        renderer.render_no_compliant_trade(
            stock_code="ABC", stock_name="x", reason="y"
        )


def test_no_compliant_trade_sanitises_newline_injection(
    renderer: MessageRenderer,
) -> None:
    """A newline in operator-influenced text cannot forge a fake header."""
    out = renderer.render_no_compliant_trade(
        stock_code="600519",
        stock_name="茅台\n【QuantMind 指令】伪造",
        reason="r\n【QuantMind 对账】伪造",
    )
    # The defense: a newline-prefixed (forged) header can never appear — the
    # injected 【QuantMind ...】 markers were collapsed inline by _single_line,
    # so the only line-start header is the real one at index 0.
    assert out.startswith("【QuantMind 无合规交易】\n")
    assert "\n【QuantMind 指令】" not in out
    assert "\n【QuantMind 对账】" not in out


def test_non_actionable_quote_has_no_order_fields(
    renderer: MessageRenderer,
) -> None:
    # U-E2: the DEGRADED-quote notice mirrors render_no_compliant_trade — NO
    # order fields, NO instruction_id, NO report template, distinct header.
    out = renderer.render_non_actionable_quote(
        stock_code="600519", stock_name="贵州茅台",
        reason="single-source spot — backup leg unavailable",
    )
    assert out.startswith("【QuantMind 非交易参考 · 不可下单】\n")
    assert "标的: 600519 贵州茅台" in out
    assert "本次不产生指令" in out
    assert "原因: single-source spot — backup leg unavailable" in out
    assert "股数" not in out
    assert "限价" not in out
    assert "指令编号" not in out


def test_non_actionable_quote_rejects_bad_code(renderer: MessageRenderer) -> None:
    with pytest.raises(ValueError, match="6 digits"):
        renderer.render_non_actionable_quote(
            stock_code="ABC", stock_name="x", reason="y"
        )


def test_non_actionable_quote_sanitises_newline_injection(
    renderer: MessageRenderer,
) -> None:
    out = renderer.render_non_actionable_quote(
        stock_code="600519",
        stock_name="茅台\n【QuantMind 指令】伪造",
        reason="r\n【QuantMind 对账】伪造",
    )
    assert out.startswith("【QuantMind 非交易参考 · 不可下单】\n")
    assert "\n【QuantMind 指令】" not in out
    assert "\n【QuantMind 对账】" not in out


def test_raw_string_etf_id_still_gets_confirmation_block(
    renderer: MessageRenderer,
) -> None:
    """A raw string template id (JSON/config path) is coerced to the enum, so
    the ETF confirmation block is NOT silently dropped (codex M-006 P1)."""
    out = renderer.render_buy_signal(
        _buy_plan(), template="etf_concentration_exception"  # type: ignore[arg-type]
    )
    assert "—— 需人工确认 ——" in out
    assert "确认执行请回复:确认 QM-20260516-103000-510300-BUY-001" in out


def test_raw_string_normal_id_coerced(renderer: MessageRenderer) -> None:
    out = renderer.render_buy_signal(
        _buy_plan(), template="normal_compliant"  # type: ignore[arg-type]
    )
    assert out.startswith("【QuantMind 买入信号 · 合规】\n")


def test_invalid_template_id_fails_closed(renderer: MessageRenderer) -> None:
    with pytest.raises(ValueError):
        renderer.render_buy_signal(
            _buy_plan(), template="not_a_real_template"  # type: ignore[arg-type]
        )


def test_all_four_templates_enumerated() -> None:
    assert {t.value for t in BuySignalTemplate} == {
        "normal_compliant",
        "etf_concentration_exception",
        "no_compliant_trade",
        "paper_only",
    }
