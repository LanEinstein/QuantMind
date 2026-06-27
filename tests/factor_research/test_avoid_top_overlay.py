"""Tests for the avoid-top EXIT overlays (treatment + placebos).

Two layers: (1) direct ``orders_for_day`` unit tests with hand-built contexts for
the trigger logic (rolling-top confirmation / P-B stop / queue re-emit / re-entry
lock / placebo draw); (2) end-to-end through the C0b simulator for the byte-exact
invariant and real T+1 fills.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.backtest.event_loop import DayBar
from backend.backtest.harness import BacktestSpec, run_backtest
from backend.backtest.portfolio import OpeningLot
from backend.backtest.strategy import HeldPosition, PortfolioView
from scripts.factor_research.avoid_top_overlay import (
    AvoidTopExitConfig,
    AvoidTopOverlay,
    PlaceboPlan,
    RandomHeldExitOverlay,
    StopOnlyOverlay,
)
from scripts.factor_research.avoid_top_panel import AvoidTopTriggerTable
from scripts.factor_research.e2e_simulator import (
    ExitOverlayContext,
    HeldContext,
    run_e2e_backtest,
)
from scripts.factor_research.gate_backtest import (
    PanelScoreProvider,
    default_friction,
    default_strategy_config,
)

_A = "600519.SH"
_DAYS = tuple(f"202001{d:02d}" for d in range(1, 13))


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _bar(
    code: str, day: str, close: float, *, limit_down: float | None = None
) -> DayBar:
    return DayBar(
        code=code,
        trade_date=day,
        open_cents=round(close * 100),
        high_cents=round((close + 1.0) * 100),
        low_cents=round((close - 1.0) * 100),
        close_cents=round(close * 100),
        adv_volume=1.0e9,
        limit_up_cents=round(close * 1.1 * 100),
        limit_down_cents=round(
            (limit_down if limit_down is not None else close * 0.9) * 100
        ),
        board="sh_main",
        transfer_fee_applies=False,
    )


def _held(code: str, *, volume: int, cost: float, mark: float, age: int) -> HeldContext:
    return HeldContext(
        code=code,
        volume=volume,
        cost_cents=round(cost * 100),
        market_value_cents=round(mark * 100 * volume),
        holding_age_trading_days=age,
    )


def _ctx(
    day: str,
    idx: int,
    held: Mapping[str, HeldContext],
    bars: Mapping[str, DayBar],
) -> ExitOverlayContext:
    holdings = tuple(
        HeldPosition(
            code=h.code,
            volume=h.volume,
            holding_age_trading_days=h.holding_age_trading_days,
        )
        for h in held.values()
    )
    view = PortfolioView(
        trade_date=day, total_equity_cents=10_000_000, cash_cents=0, holdings=holdings
    )
    return ExitOverlayContext(
        day=day,
        current_index=idx,
        view=view,
        bars=dict(bars),
        rotation_decision_orders=(),
        held=tuple(held.values()),
    )


def _triggers(crowded_by_date: dict[str, frozenset[str]]) -> AvoidTopTriggerTable:
    return AvoidTopTriggerTable(
        rebalance_dates=tuple(sorted(crowded_by_date)),
        crowded_by_date=crowded_by_date,
        top_q=0.90,
    )


# --------------------------------------------------------------------------- #
# 1. Rolling-top confirmation (P-A): crowded ALONE never sells — needs rollover #
# --------------------------------------------------------------------------- #
def test_avoid_top_needs_crowded_and_rollover() -> None:
    ov = AvoidTopOverlay(_triggers({_DAYS[0]: frozenset({_A})}), AvoidTopExitConfig())
    held = {_A: _held(_A, volume=100, cost=100.0, mark=100.0, age=1)}
    # Day 0: only 1 close observed → cannot confirm a rolling-top → no sell.
    assert (
        ov.orders_for_day(_ctx(_DAYS[0], 0, held, {_A: _bar(_A, _DAYS[0], 100.0)}))
        == ()
    )
    # Rising (crowded but extending, NOT rolled over) → still no sell (P-A).
    for i in range(1, 5):
        px = 100.0 + i
        held = {_A: _held(_A, volume=100, cost=100.0, mark=px, age=i + 1)}
        assert (
            ov.orders_for_day(_ctx(_DAYS[i], i, held, {_A: _bar(_A, _DAYS[i], px)}))
            == ()
        )
    # Now roll over ≥3% off the recent peak (104 → 100) → avoid-top SELL fires.
    held = {_A: _held(_A, volume=100, cost=100.0, mark=100.0, age=6)}
    orders = ov.orders_for_day(_ctx(_DAYS[5], 5, held, {_A: _bar(_A, _DAYS[5], 100.0)}))
    assert len(orders) == 1
    assert (
        orders[0].code == _A and not orders[0].side_is_buy and orders[0].volume == 100
    )
    assert ov.first_trigger_events("avoid_top")[-1].code == _A


def test_not_crowded_never_avoid_top_sells_even_on_rollover() -> None:
    ov = AvoidTopOverlay(_triggers({_DAYS[0]: frozenset()}), AvoidTopExitConfig())
    # Build a clear rolling-top, but the name is NOT in the crowded set → no sell.
    for i, px in enumerate((100.0, 104.0, 100.0)):
        held = {_A: _held(_A, volume=100, cost=100.0, mark=px, age=i + 1)}
        assert (
            ov.orders_for_day(_ctx(_DAYS[i], i, held, {_A: _bar(_A, _DAYS[i], px)}))
            == ()
        )


# --------------------------------------------------------------------------- #
# 2. P-B mandatory stop: fires on −12% unrealized regardless of crowding.       #
# --------------------------------------------------------------------------- #
def test_stop_fires_without_crowding_or_confirmation() -> None:
    ov = StopOnlyOverlay(AvoidTopExitConfig(stop_loss_frac=0.12))
    held = {_A: _held(_A, volume=100, cost=100.0, mark=87.0, age=3)}  # −13% < −12%
    orders = ov.orders_for_day(_ctx(_DAYS[0], 0, held, {_A: _bar(_A, _DAYS[0], 87.0)}))
    assert len(orders) == 1 and orders[0].code == _A
    assert ov.first_trigger_events("stop")[-1].code == _A


def test_stop_does_not_fire_above_threshold() -> None:
    ov = StopOnlyOverlay(AvoidTopExitConfig(stop_loss_frac=0.12))
    held = {_A: _held(_A, volume=100, cost=100.0, mark=90.0, age=3)}  # −10% > −12%
    assert (
        ov.orders_for_day(_ctx(_DAYS[0], 0, held, {_A: _bar(_A, _DAYS[0], 90.0)})) == ()
    )


# --------------------------------------------------------------------------- #
# 3. Queue: a pending name re-emits every day until it leaves the book.         #
# --------------------------------------------------------------------------- #
def test_pending_exit_reemits_while_still_held() -> None:
    ov = StopOnlyOverlay(AvoidTopExitConfig(stop_loss_frac=0.12))
    held = {_A: _held(_A, volume=100, cost=100.0, mark=80.0, age=3)}
    o1 = ov.orders_for_day(_ctx(_DAYS[0], 0, held, {_A: _bar(_A, _DAYS[0], 80.0)}))
    assert len(o1) == 1  # stop trigger
    # Still held next day (un-filled / trapped) → re-emit (reason 'queued').
    o2 = ov.orders_for_day(_ctx(_DAYS[1], 1, held, {_A: _bar(_A, _DAYS[1], 80.0)}))
    assert len(o2) == 1 and o2[0].code == _A
    assert ov.first_trigger_events("queued")[-1].code == _A


# --------------------------------------------------------------------------- #
# 4. Re-entry lock: a pending name that leaves, then re-appears within the lock #
#    window, is re-sold (reason 'reentry').                                      #
# --------------------------------------------------------------------------- #
def test_reentry_lock_resells_rebought_name() -> None:
    # crowded only at day0; a later rebalance (day3) marks it NOT crowded, so the
    # re-appearance is driven purely by the re-entry lock, not a fresh avoid-top.
    trig = _triggers({_DAYS[0]: frozenset({_A}), _DAYS[3]: frozenset()})
    ov = AvoidTopOverlay(trig, AvoidTopExitConfig(reentry_lock_days=5))
    # day0: hold, 1 close → no fire.
    ov.orders_for_day(
        _ctx(
            _DAYS[0],
            0,
            {_A: _held(_A, volume=100, cost=100.0, mark=100.0, age=1)},
            {_A: _bar(_A, _DAYS[0], 100.0)},
        )
    )
    # day1: rollover (100→90) while crowded → avoid_top sell; A becomes pending.
    o1 = ov.orders_for_day(
        _ctx(
            _DAYS[1],
            1,
            {_A: _held(_A, volume=100, cost=100.0, mark=90.0, age=2)},
            {_A: _bar(_A, _DAYS[1], 90.0)},
        )
    )
    assert len(o1) == 1 and ov.first_trigger_events("avoid_top")
    # day2: A left the book (sold) → re-entry lock armed (until idx 2+5=7).
    ov.orders_for_day(_ctx(_DAYS[2], 2, {}, {_A: _bar(_A, _DAYS[2], 92.0)}))
    # day3: ranker re-bought A; NOT crowded now, −8% (no stop) → re-entry re-sell.
    o3 = ov.orders_for_day(
        _ctx(
            _DAYS[3],
            3,
            {_A: _held(_A, volume=100, cost=100.0, mark=92.0, age=1)},
            {_A: _bar(_A, _DAYS[3], 92.0)},
        )
    )
    assert len(o3) == 1 and o3[0].code == _A
    assert ov.first_trigger_events("reentry")[-1].code == _A


def test_reentry_beats_signal_in_reason_label() -> None:
    # A name re-bought during its lock window AND freshly crowded+rolled-over is
    # labeled 'reentry' (a force-out), NOT 'avoid_top' — so the avoid-top calendar
    # counts only the ORIGINAL avoid-top exit, never the lock-driven re-sell
    # (codex P2: precedence reentry > signal).
    trig = _triggers({_DAYS[0]: frozenset({_A}), _DAYS[3]: frozenset({_A})})
    ov = AvoidTopOverlay(trig, AvoidTopExitConfig(reentry_lock_days=5))
    ov.orders_for_day(
        _ctx(
            _DAYS[0],
            0,
            {_A: _held(_A, volume=100, cost=100.0, mark=100.0, age=1)},
            {_A: _bar(_A, _DAYS[0], 100.0)},
        )
    )
    ov.orders_for_day(
        _ctx(
            _DAYS[1],
            1,
            {_A: _held(_A, volume=100, cost=100.0, mark=90.0, age=2)},
            {_A: _bar(_A, _DAYS[1], 90.0)},
        )
    )
    ov.orders_for_day(_ctx(_DAYS[2], 2, {}, {_A: _bar(_A, _DAYS[2], 92.0)}))
    # day3: re-bought, crowded AND rolled over (89 vs peak 100), −11% (no stop).
    o3 = ov.orders_for_day(
        _ctx(
            _DAYS[3],
            3,
            {_A: _held(_A, volume=100, cost=100.0, mark=89.0, age=1)},
            {_A: _bar(_A, _DAYS[3], 89.0)},
        )
    )
    assert len(o3) == 1 and o3[0].code == _A
    assert ov.first_trigger_events("reentry")[-1].code == _A
    # Exactly ONE avoid_top event (day1) — the lock-driven re-sell is not counted.
    assert len(ov.first_trigger_events("avoid_top")) == 1


def test_placebo_excludes_reentry_locked_names() -> None:
    # A reentry-locked held name must not be spent by the placebo's random draw
    # (it would be force-exited anyway) — codex P2.
    plan = PlaceboPlan(seed=7, counts_by_index={0: 1})
    ov = RandomHeldExitOverlay(plan, AvoidTopExitConfig())
    ov._reentry_lock_until = {"A.SH": 10}  # A is under an active lock at idx 0
    held = {
        "A.SH": _held("A.SH", volume=100, cost=10.0, mark=10.0, age=2),
        "B.SH": _held("B.SH", volume=100, cost=10.0, mark=10.0, age=2),
    }
    bars = {c: _bar(c, _DAYS[0], 10.0) for c in held}
    orders = ov.orders_for_day(_ctx(_DAYS[0], 0, held, bars))
    placebo = [o.code for o in orders if o.code != "A.SH"]
    # The placebo draw landed on B (not the locked A); A still exits via reentry.
    assert "B.SH" in placebo
    assert ov.first_trigger_events("placebo") and all(
        e.code != "A.SH" for e in ov.first_trigger_events("placebo")
    )


# --------------------------------------------------------------------------- #
# 5. Placebo: count per day (calendar / rate) + seeded determinism.             #
# --------------------------------------------------------------------------- #
def test_placebo_calendar_count_and_determinism() -> None:
    held = {
        "A.SH": _held("A.SH", volume=100, cost=10.0, mark=10.0, age=2),
        "B.SH": _held("B.SH", volume=100, cost=10.0, mark=10.0, age=2),
        "C.SH": _held("C.SH", volume=100, cost=10.0, mark=10.0, age=2),
    }
    bars = {c: _bar(c, _DAYS[0], 10.0) for c in held}
    plan = PlaceboPlan(seed=20260622, counts_by_day={_DAYS[0]: 2})
    ov1 = RandomHeldExitOverlay(plan, AvoidTopExitConfig())
    ov2 = RandomHeldExitOverlay(plan, AvoidTopExitConfig())
    o1 = ov1.orders_for_day(_ctx(_DAYS[0], 0, held, bars))
    o2 = ov2.orders_for_day(_ctx(_DAYS[0], 0, held, bars))
    assert len(o1) == 2  # matched the calendar count
    assert {o.code for o in o1} == {o.code for o in o2}  # same seed → same draw


def test_placebo_rate_count_by_index() -> None:
    held = {"A.SH": _held("A.SH", volume=100, cost=10.0, mark=10.0, age=2)}
    bars = {"A.SH": _bar("A.SH", _DAYS[0], 10.0)}
    plan = PlaceboPlan(seed=1, counts_by_index={0: 1})
    ov = RandomHeldExitOverlay(plan, AvoidTopExitConfig())
    assert len(ov.orders_for_day(_ctx(_DAYS[0], 0, held, bars))) == 1
    # A.SH left the book (sold); index 1 has no scheduled exit → nothing emitted
    # (no queued re-emit because nothing is still pending+held).
    assert ov.orders_for_day(_ctx(_DAYS[1], 1, {}, {})) == ()


# --------------------------------------------------------------------------- #
# 6. End-to-end through the C0b simulator.                                      #
# --------------------------------------------------------------------------- #
class _DictBarSource:
    def __init__(self, bars_by_day: dict[str, dict[str, DayBar]]) -> None:
        self._b = bars_by_day

    def trading_days(self) -> tuple[str, ...]:
        return tuple(sorted(self._b))

    def bars_on(self, day: str) -> Mapping[str, DayBar]:
        return self._b.get(day, {})


def _empty_provider() -> PanelScoreProvider:
    return PanelScoreProvider({day: [] for day in _DAYS})


def test_avoid_top_overlay_byte_exact_when_nothing_triggers() -> None:
    # Rising prices + empty crowded set ⇒ no avoid-top, no stop ⇒ byte-exact.
    bars = {day: {_A: _bar(_A, day, 100.0 + i)} for i, day in enumerate(_DAYS)}
    src = _DictBarSource(bars)
    spec = BacktestSpec(
        initial_capital_cents=500_000 * 100,
        opening_positions=(OpeningLot(code=_A, volume=1000, cost_cents=100_00),),
    )
    cfg, friction = default_strategy_config(), default_friction()
    frozen = run_backtest(
        spec=spec,
        bar_source=src,
        provider=_empty_provider(),
        strategy_config=cfg,
        friction_params=friction,
    )
    e2e = run_e2e_backtest(
        spec=spec,
        bar_source=src,
        provider=_empty_provider(),
        strategy_config=cfg,
        friction_params=friction,
        exit_overlay=AvoidTopOverlay(
            _triggers({_DAYS[0]: frozenset()}), AvoidTopExitConfig()
        ),
    )
    assert e2e.backtest_result == frozen
    assert e2e.overlay_sell_signals == 0


def test_avoid_top_overlay_sells_crowded_rollover_through_simulator() -> None:
    # Price rises to a peak then rolls over ≥3%; A is crowded ⇒ avoid-top SELL
    # fires and fills T+1 (exactly once; then nothing left to sell).
    path = [
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
        100.0,
        99.0,
        99.0,
        99.0,
        99.0,
        99.0,
        99.0,
    ]
    bars = {day: {_A: _bar(_A, day, path[i])} for i, day in enumerate(_DAYS)}
    src = _DictBarSource(bars)
    spec = BacktestSpec(
        initial_capital_cents=500_000 * 100,
        opening_positions=(OpeningLot(code=_A, volume=1000, cost_cents=100_00),),
    )
    res = run_e2e_backtest(
        spec=spec,
        bar_source=src,
        provider=_empty_provider(),
        strategy_config=default_strategy_config(),
        friction_params=default_friction(),
        exit_overlay=AvoidTopOverlay(
            _triggers({_DAYS[0]: frozenset({_A})}), AvoidTopExitConfig()
        ),
    )
    sell_fills = [f for f in res.backtest_result.fills if not f.side_is_buy]
    assert (
        len(sell_fills) == 1
        and sell_fills[0].code == _A
        and sell_fills[0].volume == 1000
    )
    # Flat afterwards; conservation holds.
    assert all(p.code != _A for p in res.backtest_result.equity_curve[-1].positions)
    assert "position_conservation" not in {
        v.kind for v in res.backtest_result.invariant_report.violations
    }
