#!/usr/bin/env python
"""W-005 — render-only Phase W (持仓 thesis) dry-run demo.

A self-contained, **render-only** walk of the direction-② thesis path — NO LLM,
NO Mongo, NO network, NO orders. It demonstrates each W-task's output so the
owner can eyeball the shape before enabling the live crons:

1. **W-001** — derive a buy-time :class:`PositionThesis` and print its
   deterministic invalidation thresholds (LLM pillar text shown alongside, but
   the thresholds come only from the entry price/score — no LLM).
2. **W-004** — evaluate a deterministic ``THESIS_QUANT_BREAK`` over a sample PIT
   price below the anchor (zero LLM) + show it is omitted when the thesis holds.
3. **W-002/W-003** — render the display-only Feishu thesis-review digest from
   sample advisory verdicts and ASSERT it is NOT parseable as an execution
   report (the display-only / no-order-token guarantee).

Run: ``python scripts/dry_run_thesis_review.py`` (prints the artifacts + a PASS).
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.integrations.feishu.renderer import MessageRenderer
from backend.models.position_thesis import ThesisHealth
from backend.monitoring.thesis_break import evaluate_thesis_breaks
from backend.position_thesis.derivation import (
    ThesisEntrySnapshot,
    build_position_thesis,
    derive_invalidation_conditions,
)
from backend.services.execution_report_parser import (
    ExecutionReportChannel,
    ExecutionReportParseError,
    parse_execution_report,
)
from backend.services.thesis_advisory import ThesisAdvisoryVerdict

_NOW = datetime(2026, 6, 2, 17, 30, tzinfo=UTC)


def main() -> None:
    print("=== W-001 — 买入时确定性失效阈值(无 LLM)===")
    snap = ThesisEntrySnapshot(
        entry_price=10.0, entry_score=2.0, trade_date="2026-05-10"
    )
    for cond in derive_invalidation_conditions(snap):
        print(
            f"  · {cond.template.value}: {cond.comparator.value} {cond.threshold} "
            f"(anchor={cond.anchor}, ver={cond.feature_code_version})"
        )
    thesis = build_position_thesis(
        instruction_id="QM-20260510-093500-600519-BUY-001",
        signal_id="SIG-20260509-line1",
        stock_code="600519",
        stock_name="贵州茅台",
        created_at=_NOW,
        trade_date="2026-05-10",
        pillars=("[基金经理] 龙头护城河稳固", "[基本面] 估值合理", "[技术面] 动量确认"),
        entry_price=10.0,
        entry_score=2.0,
        snapshot_id="snap-demo",
    )

    print("\n=== W-004 — 确定性 THESIS_QUANT_BREAK(零 LLM,over PIT)===")
    theses = {"600519": thesis}
    broke = evaluate_thesis_breaks(theses, price_by_code={"600519": 8.0})  # < 8.8 floor
    held = evaluate_thesis_breaks(theses, price_by_code={"600519": 9.5})  # intact
    _b = broke.get("600519")
    print(f"  价格 8.0(跌破锚定 8.8)→ break {sorted(broke)}: "
          f"{_b.reason if _b else None}")
    print(f"  价格 9.5(逻辑完好)→ break {sorted(held)}(应为空)")
    assert "600519" in broke and not held, "THESIS_QUANT_BREAK 确定性判定异常"

    print("\n=== W-002/W-003 — display-only 飞书复盘 digest(不可解析为回报)===")
    verdicts = [
        ThesisAdvisoryVerdict(
            code="600519", instruction_id=thesis.instruction_id,
            health=ThesisHealth.BROKEN, reason_text="主业受供应链冲击,逻辑破坏",
            evidence_id="DEBATE-thesis-20260602-600519", trade_date="2026-06-02",
        ),
        ThesisAdvisoryVerdict(
            code="000001", instruction_id="QM-20260510-093500-000001-BUY-002",
            health=ThesisHealth.INTACT, reason_text="基本面稳健,逻辑完好",
            evidence_id="DEBATE-thesis-20260602-000001", trade_date="2026-06-02",
        ),
    ]
    digest = MessageRenderer().render_thesis_review_digest(verdicts)
    print(digest)
    assert "QM-" not in digest and "已执行" not in digest, "digest 含订单类 token"
    try:
        parse_execution_report(
            digest, channel=ExecutionReportChannel.FEISHU, received_at=_NOW
        )
        raise AssertionError("digest 不应可解析为执行回报")
    except ExecutionReportParseError as exc:
        assert exc.reason == "no_pattern_match"

    print(
        "\nPASS — Phase W 持仓 thesis 路径 render-only 演示通过"
        "(零 LLM/零下单/display-only)"
    )


if __name__ == "__main__":
    main()
