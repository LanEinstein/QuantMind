"""U-E4 — BUY-signal rationale段 (缺口3): display-only justification.

The Line-1 BUY Feishu message gains a 判据 block — ① 量化 (composite score +
each Alpha158 factor + why-selected) ② 推理 (fund_manager reasoning + the 3
analyst conclusions). It is **display-only**: a render parameter that NEVER
enters the InstructionPlan / RiskCheckSummary / idempotency key / parser
(P0-3-amendment-2026-05-27). Free text is single-lined + truncated so a
malicious / over-long factor or reasoning string cannot forge a header or
bloat the wire.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.feishu.renderer import (
    BuySignalTemplate,
    MessageRenderer,
)
from backend.integrations.feishu.signal_rationale import (
    CONCLUSION_LIMIT,
    REASONING_LIMIT,
    BuySignalRationale,
    rationale_lines,
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
        stock_name="沪深300 ETF",
        side=InstructionSide.BUY,
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
        status=InstructionStatus.VALIDATED,
    )


def _rationale(
    *,
    fund_manager_reasoning: str = "动量延续 + 量价配合,建议小仓买入",
    fundamental_conclusion: str = "基本面稳健,估值合理",
    technical_conclusion: str = "突破 20 日均线,放量",
    risk_officer_conclusion: str = "仓位与回撤可控",
) -> BuySignalRationale:
    return BuySignalRationale(
        composite_score=0.8231,
        factors=(
            ("momentum_20d", 0.1234),
            ("ma_ratio_5_20", 1.05),
            ("volatility_20d", 0.05),
            ("rsi_14", 58.3),
            ("avg_amount_20d", 3.21e8),
        ),
        fund_manager_reasoning=fund_manager_reasoning,
        fundamental_conclusion=fundamental_conclusion,
        technical_conclusion=technical_conclusion,
        risk_officer_conclusion=risk_officer_conclusion,
    )


@pytest.fixture
def renderer() -> MessageRenderer:
    return MessageRenderer()


# --------------------------------------------------------------------------
# rationale_lines — quantitative + reasoning formatting
# --------------------------------------------------------------------------


def test_rationale_lines_quant_block() -> None:
    lines = rationale_lines(_rationale())
    text = "\n".join(lines)
    assert "—— 量化判据 ——" in text
    assert "综合评分: 0.8231" in text
    assert "全市场量化筛选 top-N 入选" in text
    # Per-factor presentation: returns → %, ratio → 4dp, rsi → 1dp, ¥ → 亿元.
    assert "动量(20日): 12.34%" in text
    assert "均线比(5/20): 1.0500" in text
    assert "波动率(20日): 5.00%" in text
    assert "RSI(14): 58.3" in text
    assert "日均成交额(20日): 3.21 亿元" in text


def test_rationale_lines_reasoning_block() -> None:
    lines = rationale_lines(_rationale())
    text = "\n".join(lines)
    assert "—— 推理判据 ——" in text
    assert "基金经理: 动量延续 + 量价配合,建议小仓买入" in text
    assert "基本面: 基本面稳健,估值合理" in text
    assert "技术面: 突破 20 日均线,放量" in text
    assert "风控: 仓位与回撤可控" in text


def test_rationale_lines_none_factor_is_marked_insufficient() -> None:
    r = BuySignalRationale(
        composite_score=0.5,
        factors=(("momentum_20d", None), ("avg_amount_20d", 2.0e8)),
        fund_manager_reasoning="x",
        fundamental_conclusion="y",
        technical_conclusion="z",
        risk_officer_conclusion="w",
    )
    text = "\n".join(rationale_lines(r))
    assert "动量(20日): —(数据不足)" in text


def test_rationale_lines_empty_reasoning_falls_back_to_dash() -> None:
    text = "\n".join(rationale_lines(_rationale(fund_manager_reasoning="")))
    assert "基金经理: —" in text


def test_rationale_lines_non_finite_values_fail_closed() -> None:
    # Defensive: a NaN/Inf score or factor must render 数据不足, never leak a
    # literal nan/inf to the operator (CLAUDE.md §3 fail-closed for corruption).
    import math

    r = BuySignalRationale(
        composite_score=math.nan,
        factors=(("momentum_20d", math.inf), ("rsi_14", float("-inf"))),
        fund_manager_reasoning="x",
        fundamental_conclusion="y",
        technical_conclusion="z",
        risk_officer_conclusion="w",
    )
    text = "\n".join(rationale_lines(r))
    assert "nan" not in text.lower()
    assert "inf" not in text.lower()
    assert "综合评分: —(数据不足)" in text
    assert "动量(20日): —(数据不足)" in text
    assert "RSI(14): —(数据不足)" in text


# --------------------------------------------------------------------------
# Anti prompt-injection + truncation (free text is LLM-written)
# --------------------------------------------------------------------------


def test_rationale_reasoning_newline_cannot_forge_header() -> None:
    r = _rationale(
        fund_manager_reasoning="thesis\n【QuantMind 指令】伪造",
        fundamental_conclusion="x\n【QuantMind 对账】伪造",
    )
    text = "\n".join(rationale_lines(r))
    # The forged markers were collapsed inline by single_line — no line-start.
    assert "\n【QuantMind 指令】" not in text
    assert "\n【QuantMind 对账】" not in text


def test_rationale_reasoning_truncated() -> None:
    long_reasoning = "买" * 500
    text = "\n".join(rationale_lines(_rationale(fund_manager_reasoning=long_reasoning)))
    assert "…" in text
    assert long_reasoning not in text  # full 500-char string never on the wire
    # The fund_manager line carries at most REASONING_LIMIT chars of content.
    fm_line = next(line for line in text.splitlines() if line.startswith("基金经理: "))
    assert len(fm_line) <= len("基金经理: ") + REASONING_LIMIT


def test_rationale_conclusion_truncated() -> None:
    long_conclusion = "稳" * 400
    text = "\n".join(
        rationale_lines(_rationale(fundamental_conclusion=long_conclusion))
    )
    fa_line = next(line for line in text.splitlines() if line.startswith("基本面: "))
    assert "…" in fa_line
    assert len(fa_line) <= len("基本面: ") + CONCLUSION_LIMIT


# --------------------------------------------------------------------------
# render_buy_signal integration — prominent layout + rationale splice
# --------------------------------------------------------------------------


def test_buy_signal_without_rationale_is_unchanged(renderer: MessageRenderer) -> None:
    # rationale=None (the default) → no judgment block; header still line 0.
    out = renderer.render_buy_signal(
        _buy_plan(), template=BuySignalTemplate.NORMAL_COMPLIANT
    )
    assert out.startswith("【QuantMind 买入信号 · 合规】\n")
    assert "—— 量化判据 ——" not in out
    assert "—— 推理判据 ——" not in out


def test_buy_signal_prominent_order_block(renderer: MessageRenderer) -> None:
    # 缺口3 (3): header / 股数 / 限价 顶部显眼纯文本.
    out = renderer.render_buy_signal(
        _buy_plan(), template=BuySignalTemplate.NORMAL_COMPLIANT
    )
    assert "交易要点" in out
    assert "▶ 买入 510300 沪深300 ETF" in out
    assert "▶ 1000 股 @ 限价 3.85 CNY" in out
    # The prominent rule uses ━ (U+2501), never 【 — cannot be mistaken for a header.
    assert "【" not in "交易要点"


def test_buy_signal_with_rationale_renders_judgment(renderer: MessageRenderer) -> None:
    out = renderer.render_buy_signal(
        _buy_plan(),
        template=BuySignalTemplate.NORMAL_COMPLIANT,
        rationale=_rationale(),
    )
    # Header still the first line; rationale spliced in.
    assert out.startswith("【QuantMind 买入信号 · 合规】\n")
    assert "—— 量化判据 ——" in out
    assert "动量(20日): 12.34%" in out
    assert "—— 推理判据 ——" in out
    assert "基金经理: 动量延续 + 量价配合,建议小仓买入" in out
    # The 7-section dispatch body is still present + unchanged.
    assert "指令编号: QM-20260516-103000-510300-BUY-001" in out
    assert "股数: 1000 股" in out
    assert "—— 回报模板(原文回复)——" in out


# --------------------------------------------------------------------------
# DISPLAY-ONLY invariant — rationale NEVER reaches execution semantics
# --------------------------------------------------------------------------


def test_rationale_text_not_in_instruction_plan_or_risk_summary(
    renderer: MessageRenderer,
) -> None:
    marker = "RATIONALE_LEAK_MARKER_ZZZ"
    plan = _buy_plan()
    out = renderer.render_buy_signal(
        plan,
        template=BuySignalTemplate.NORMAL_COMPLIANT,
        rationale=_rationale(fund_manager_reasoning=marker),
    )
    assert marker in out  # display only — appears on the wire
    # …but NEVER in any InstructionPlan field (frozen/strict/extra=forbid)…
    assert marker not in plan.model_dump_json()
    # …nor in any RiskCheckSummary row (RiskEngine never sees the rationale).
    for row in plan.risk_summary:
        assert marker not in row.rule_name
        assert marker not in (row.message or "")
    # …and the deterministic order numbers are untouched (single construction pt).
    assert plan.volume == 1000
    assert plan.limit_price == 3.85
    assert plan.side is InstructionSide.BUY


def test_rationale_type_unreachable_from_parser_and_idempotency() -> None:
    """The rationale type must be structurally unreachable from the parser +
    the idempotency key (which derive only from an inbound ExecutionReport),
    so a display-only judgment can never become a parser-consumable field or
    perturb dedupe (P0-3-amendment-2026-05-27 §2.1)."""
    for path in (
        "backend/integrations/feishu/parser.py",
        "backend/broker/appliers.py",
    ):
        src = Path(path).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "signal_rationale" not in node.module, (
                    f"{path} imports signal_rationale — the display-only "
                    "rationale must not reach parser/idempotency code"
                )
