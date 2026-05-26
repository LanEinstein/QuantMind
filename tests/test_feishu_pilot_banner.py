"""U-D2 — PILOT go-live banner golden tests (P0-6-amendment-2026-05-25 §2.3).

Every order-bearing Feishu renderer (BUY signal, Line-2 monitoring SELL, Line-2
ADD) must prepend the single-source "模拟盘·人工·试点" banner as the FIRST
line when ``pilot=True`` and leave the message byte-identical when ``pilot``
defaults to ``False``. The banner is a code constant composed in exactly one
place (``renderer._PILOT_BANNER``) so it can never drift or be spoofed.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.integrations.feishu.renderer import (
    _PILOT_BANNER,
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
_BANNER = "「模拟盘 · 人工执行 · 试点」"


def _risk_summary_passed() -> tuple[RiskCheckSummary, ...]:
    return tuple(
        RiskCheckSummary(rule_name=f"rule_{i:02d}", passed=True, message="")
        for i in range(14)
    )


def _plan(
    *,
    side: InstructionSide,
    signal_id: str,
) -> InstructionPlan:
    created = datetime(2026, 5, 16, 10, 30, 0, tzinfo=_SH)
    snapshot = datetime(2026, 5, 16, 10, 29, 50, tzinfo=_SH)
    valid_until = datetime(2026, 5, 16, 14, 55, 0, tzinfo=_SH)
    return InstructionPlan(
        instruction_id=f"QM-20260516-103000-510300-{side.value}-001",
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
        signal_id=signal_id,
        analysis_record_id="ar-001",
        debate_round_count=1,
        invalidation_summary="若沪深300当日跌幅≥1%即失效",
        status=InstructionStatus.VALIDATED,
    )


@pytest.fixture
def renderer() -> MessageRenderer:
    return MessageRenderer()


def test_banner_constant_matches_amendment() -> None:
    # The single source must be the exact amendment §2.3 string.
    assert _PILOT_BANNER == _BANNER


# -- BUY signal ------------------------------------------------------------


def test_buy_signal_prepends_banner_when_pilot(
    renderer: MessageRenderer,
) -> None:
    out = renderer.render_buy_signal(
        _plan(side=InstructionSide.BUY, signal_id="sig-001"),
        template=BuySignalTemplate.NORMAL_COMPLIANT,
        pilot=True,
    )
    # Banner is the FIRST line, header follows.
    assert out.startswith(_BANNER + "\n【QuantMind 买入信号 · 合规】\n")
    assert out.count(_BANNER) == 1


def test_buy_signal_default_has_no_banner(renderer: MessageRenderer) -> None:
    default = renderer.render_buy_signal(
        _plan(side=InstructionSide.BUY, signal_id="sig-001"),
        template=BuySignalTemplate.NORMAL_COMPLIANT,
    )
    explicit_full = renderer.render_buy_signal(
        _plan(side=InstructionSide.BUY, signal_id="sig-001"),
        template=BuySignalTemplate.NORMAL_COMPLIANT,
        pilot=False,
    )
    assert _BANNER not in default
    # Default (FULL) is byte-identical to the pre-amendment output.
    assert default == explicit_full
    assert default.startswith("【QuantMind 买入信号 · 合规】\n")


def test_buy_signal_pilot_is_full_plus_banner_prefix(
    renderer: MessageRenderer,
) -> None:
    plan = _plan(side=InstructionSide.BUY, signal_id="sig-001")
    full = renderer.render_buy_signal(
        plan, template=BuySignalTemplate.NORMAL_COMPLIANT
    )
    pilot = renderer.render_buy_signal(
        plan, template=BuySignalTemplate.NORMAL_COMPLIANT, pilot=True
    )
    # PILOT output == banner line + the unchanged FULL body.
    assert pilot == f"{_BANNER}\n{full}"


# -- Line-2 monitoring SELL ------------------------------------------------


def test_monitoring_sell_prepends_banner_when_pilot(
    renderer: MessageRenderer,
) -> None:
    out = renderer.render_monitoring_sell(
        _plan(side=InstructionSide.SELL, signal_id="LINE2-MON-20260516-001"),
        anomaly_reason="ATR trailing stop breached",
        pilot=True,
    )
    assert out.startswith(_BANNER + "\n【QuantMind 持仓监控 · 卖出信号】\n")


def test_monitoring_sell_default_has_no_banner(
    renderer: MessageRenderer,
) -> None:
    out = renderer.render_monitoring_sell(
        _plan(side=InstructionSide.SELL, signal_id="LINE2-MON-20260516-001"),
        anomaly_reason="ATR trailing stop breached",
    )
    assert _BANNER not in out
    assert out.startswith("【QuantMind 持仓监控 · 卖出信号】\n")


# -- Line-2 ADD ------------------------------------------------------------


def test_add_position_prepends_banner_when_pilot(
    renderer: MessageRenderer,
) -> None:
    out = renderer.render_add_position(
        _plan(side=InstructionSide.BUY, signal_id="LINE2-MON-20260516-002"),
        add_rationale="four-condition scale-in confirmed",
        stop_price=3.70,
        pilot=True,
    )
    assert out.startswith(_BANNER + "\n【QuantMind 持仓监控 · 补仓信号】\n")


def test_add_position_default_has_no_banner(
    renderer: MessageRenderer,
) -> None:
    out = renderer.render_add_position(
        _plan(side=InstructionSide.BUY, signal_id="LINE2-MON-20260516-002"),
        add_rationale="four-condition scale-in confirmed",
        stop_price=3.70,
    )
    assert _BANNER not in out
    assert out.startswith("【QuantMind 持仓监控 · 补仓信号】\n")
