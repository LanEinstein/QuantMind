"""Tests for the avoid-top ablation P&L decomposition + placebo-plan building.

These validate the frozen 4-way algebra and the placebo calendar/rate matching
with hand-built events + fills (no heavy PIT run); the full end-to-end plumbing is
validated by the ``--smoke-periods`` real-data run.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.backtest.event_loop import DayBar
from backend.backtest.portfolio import AppliedFill
from scripts.factor_research.avoid_top_ablation import (
    ArmResult,
    _build_placebo_plans,
    _crash_slice_table,
    _match_avoid_top_fills,
    decompose_pnl,
)
from scripts.factor_research.avoid_top_overlay import (
    AvoidTopExitConfig,
    AvoidTopOverlay,
    ExitEvent,
)
from scripts.factor_research.avoid_top_panel import AvoidTopTriggerTable


class _DV:
    """Minimal DecisionVector stand-in (the matcher reads trade_date + sell_codes)."""

    def __init__(self, trade_date: str, sell_codes: tuple[str, ...]) -> None:
        self.trade_date = trade_date
        self.sell_codes = sell_codes


class _ResultStub:
    """Minimal stand-in carrying the ``.fills`` + ``.decision_vectors`` read."""

    def __init__(
        self, fills: tuple[AppliedFill, ...], decision_vectors: tuple[_DV, ...] = ()
    ) -> None:
        self.fills = fills
        self.decision_vectors = decision_vectors


class _BarSourceStub:
    def __init__(self, closes_by_day: Mapping[str, Mapping[str, int]]) -> None:
        self._c = closes_by_day

    def bars_on(self, day: str) -> dict[str, DayBar]:
        out: dict[str, DayBar] = {}
        for code, close in self._c.get(day, {}).items():
            out[code] = DayBar(
                code=code,
                trade_date=day,
                open_cents=close,
                high_cents=close,
                low_cents=close,
                close_cents=close,
                adv_volume=1e9,
                limit_up_cents=close * 2,
                limit_down_cents=1,
                board="sh_main",
                transfer_fee_applies=False,
            )
        return out


def _sell_fill(code: str, day: str, price_cents: int, volume: int) -> AppliedFill:
    return AppliedFill(
        trade_date=day,
        code=code,
        side_is_buy=False,
        volume=volume,
        fill_price_cents=price_cents,
        gross_cents=price_cents * volume,
        commission_cents=5,
        stamp_tax_cents=10,
        transfer_fee_cents=0,
        slippage_cents=5,
        net_cents=price_cents * volume - 20,
        board="sh_main",
        transfer_fee_applies=False,
    )


def _overlay_with_events(events: list[ExitEvent]) -> AvoidTopOverlay:
    ov = AvoidTopOverlay(
        AvoidTopTriggerTable(rebalance_dates=(), crowded_by_date={}, top_q=0.9),
        AvoidTopExitConfig(),
    )
    ov.events = events
    return ov


def _ev(day: str, idx: int, code: str) -> ExitEvent:
    return ExitEvent(
        day=day,
        current_index=idx,
        code=code,
        volume=100,
        reason="avoid_top",
        holding_age=3,
        cost_cents=1000,
        market_value_cents=100_000,
        unrealized_pnl_cents=0,
    )


_DAYS = [f"2020010{i}" for i in range(1, 8)]


def test_match_avoid_top_fills_pairs_event_to_next_sell() -> None:
    ov = _overlay_with_events([_ev(_DAYS[0], 0, "A.SH")])
    # Fill is the next SELL of A on/after the signal day (queue may delay it).
    result = _ResultStub((_sell_fill("A.SH", _DAYS[1], 1000, 100),))
    closes = {_DAYS[1]: {"A.SH": 1000}, _DAYS[3]: {"A.SH": 900}}  # fell over horizon
    matched, unfilled = _match_avoid_top_fills(
        ov,
        result,
        _BarSourceStub(closes),
        _DAYS,  # type: ignore[arg-type]
    )
    assert unfilled == 0 and len(matched) == 1
    assert matched[0].code == "A.SH" and matched[0].sell_price_cents == 1000


def test_unfilled_event_counted_when_no_sell_fill() -> None:
    ov = _overlay_with_events([_ev(_DAYS[0], 0, "A.SH")])
    result = _ResultStub(())  # never filled (trapped)
    matched, unfilled = _match_avoid_top_fills(
        ov,
        result,
        _BarSourceStub({}),
        _DAYS,  # type: ignore[arg-type]
    )
    assert unfilled == 1 and matched == []


def test_rotation_sell_is_excluded_from_avoid_top_matching() -> None:
    ov = _overlay_with_events([_ev(_DAYS[0], 0, "A.SH")])
    # The only SELL of A on day1 is a ROTATION sell (decided day0 → fills day1);
    # it must NOT be attributed to avoid-top (codex P1) — the event is left
    # unmatched (the rotation exit was non-incremental).
    result = _ResultStub(
        (_sell_fill("A.SH", _DAYS[1], 1000, 100),),
        decision_vectors=(_DV(_DAYS[0], ("A.SH",)),),
    )
    matched, unfilled = _match_avoid_top_fills(
        ov,
        result,
        _BarSourceStub({}),
        _DAYS,  # type: ignore[arg-type]
    )
    assert matched == [] and unfilled == 1


def test_decompose_identity_and_avoided_loss() -> None:
    # One avoid-top exit at 10.00; price falls to 9.00 over the horizon → avoided a
    # 100-share × ¥1.00 = ¥100 loss. friction = 20分 = ¥0.20.
    ov = _overlay_with_events([_ev(_DAYS[0], 0, "A.SH")])
    result = _ResultStub((_sell_fill("A.SH", _DAYS[1], 1000, 100),))
    # The frozen horizon (10td) clamps to the window end (_DAYS[6]) here; the
    # forward close there is 900 → held would have lost ¥1.00/share.
    closes = {_DAYS[1]: {"A.SH": 1000}, _DAYS[6]: {"A.SH": 900}}
    avoid_top_arm = ArmResult("avoid_top", 500.0, 0.0, 0.1, 1.0, 2, 0.5, True, 1, ())
    stop_only_arm = ArmResult("stop_only", 100.0, 0.0, 0.1, 1.0, 1, 0.5, True, 0, ())
    pnl = decompose_pnl(
        avoid_top_overlay=ov,
        avoid_top_arm=avoid_top_arm,
        stop_only_arm=stop_only_arm,
        avoid_top_result=result,
        bar_source=_BarSourceStub(closes),
        daily_days=_DAYS,  # type: ignore[arg-type]
    )
    assert pnl["avoided_loss_yuan"] == 100.0  # ①: dodged the ¥100 drop
    assert pnl["missed_gain_yuan"] == 0.0  # ②
    # ④ = explicit fees only (commission 5 + stamp 10 + transfer 0 = 15分); slippage
    # is embedded in the post-slippage fill price, not double-counted here.
    assert abs(float(pnl["exit_cost_yuan"]) - 0.15) < 1e-9  # ④
    # ③ is the residual closing the identity net = (①−②) + ③ − ④.
    net = avoid_top_arm.net_pnl_yuan - stop_only_arm.net_pnl_yuan
    recon = (
        float(pnl["avoided_loss_yuan"])
        - float(pnl["missed_gain_yuan"])
        + float(pnl["redeployment_residual_yuan"])
        - float(pnl["exit_cost_yuan"])
    )
    assert abs(recon - net) < 1e-9
    assert pnl["favourable_trade_off"] is True  # ① 100 > ② 0 + ④ 0.20


def test_crash_slice_table_assigns_periods_by_start_date() -> None:
    # 3 periods; the middle one starts inside the 2018 bear window.
    arm = ArmResult("avoid_top", 0.0, 0.0, 0.0, 0.0, 0, 0.0, True, 0, (0.1, -0.2, 0.05))
    period_dates = ["20170101", "20180601", "20190101"]
    table = _crash_slice_table([arm], period_dates)
    bear = table["avoid_top"]["2018_bear"]
    assert bear["n"] == 1.0
    assert abs(bear["cum_return"] - (-0.2)) < 1e-9
    assert abs(bear["worst_period"] - (-0.2)) < 1e-9
    # A window with no period falls back to 0 cum / NaN worst.
    assert table["avoid_top"]["2015_jun_crash"]["n"] == 0.0


def test_build_placebo_plans_matches_calendar_and_total() -> None:
    events = [
        _ev(_DAYS[0], 0, "A.SH"),
        _ev(_DAYS[0], 0, "B.SH"),  # 2 on day0
        _ev(_DAYS[2], 2, "C.SH"),  # 1 on day2
    ]
    cal, rate = _build_placebo_plans(_overlay_with_events(events), _DAYS)
    assert cal.counts_by_day == {_DAYS[0]: 2, _DAYS[2]: 1}
    assert sum(rate.counts_by_index.values()) == 3  # rate-matched total
