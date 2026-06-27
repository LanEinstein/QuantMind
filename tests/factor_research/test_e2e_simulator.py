"""Tests for the C0b end-to-end research simulator.

The load-bearing test is :func:`test_overlay_disabled_is_byte_exact_to_frozen_engine`
— with a no-op overlay the simulator MUST field-equal the frozen
``run_backtest`` (codex R1-#1). The rest validate the EXIT injection plumbing
(T+1 fill, limit-down queue + trapped MTM, conservation) and the frozen contract.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.backtest.event_loop import DayBar
from backend.backtest.harness import BacktestSpec, run_backtest
from backend.backtest.portfolio import OpeningLot
from backend.backtest.strategy import OrderIntent
from scripts.factor_research.e2e_simulator import (
    ExitExecutionContract,
    ExitOverlay,
    ExitOverlayContext,
    HeldContext,
    NoOpExitOverlay,
    _merge_pending,
    run_e2e_backtest,
)
from scripts.factor_research.gate_backtest import (
    PanelScoreProvider,
    default_friction,
    default_strategy_config,
)

_CODES = ("600519.SH", "600036.SH", "601318.SH")
_DAYS = tuple(f"202301{d:02d}" for d in range(4, 16))  # 12 trading days


class _DictBarSource:
    """A minimal in-memory BarSource for full control over limit days."""

    def __init__(self, bars_by_day: Mapping[str, Mapping[str, DayBar]]) -> None:
        self._b = {d: dict(v) for d, v in bars_by_day.items()}

    def trading_days(self) -> tuple[str, ...]:
        return tuple(sorted(self._b))

    def bars_on(self, day: str) -> Mapping[str, DayBar]:
        return self._b.get(day, {})


def _bar(
    code: str,
    day: str,
    close: float,
    *,
    open_px: float | None = None,
    limit_up: float | None = None,
    limit_down: float | None = None,
    adv: float = 1.0e9,
) -> DayBar:
    o = close if open_px is None else open_px
    return DayBar(
        code=code,
        trade_date=day,
        open_cents=round(o * 100),
        high_cents=round((max(o, close) + 1.0) * 100),
        low_cents=round((min(o, close) - 1.0) * 100),
        close_cents=round(close * 100),
        adv_volume=adv,
        limit_up_cents=round((limit_up if limit_up is not None else close * 1.1) * 100),
        limit_down_cents=round(
            (limit_down if limit_down is not None else close * 0.9) * 100
        ),
        board="sh_main",
        transfer_fee_applies=False,
    )


def _rising_source() -> _DictBarSource:
    """Gently rising prices, ample ADV, no limit hits (the strategy buys/holds)."""
    bars: dict[str, dict[str, DayBar]] = {}
    for i, day in enumerate(_DAYS):
        bars[day] = {}
        for j, code in enumerate(_CODES):
            px = 100.0 + j * 10.0 + i * 0.5
            bars[day][code] = _bar(code, day, px)
    return bars_view(bars)


def bars_view(bars: dict[str, dict[str, DayBar]]) -> _DictBarSource:
    return _DictBarSource(bars)


def _ranked_provider() -> PanelScoreProvider:
    return PanelScoreProvider(
        {day: [(_CODES[0], 0.9), (_CODES[1], 0.6), (_CODES[2], 0.3)] for day in _DAYS}
    )


def _empty_provider() -> PanelScoreProvider:
    return PanelScoreProvider({day: [] for day in _DAYS})


# --------------------------------------------------------------------------- #
# 1. THE load-bearing invariant: overlay-disabled ≡ frozen engine byte-exact.  #
# --------------------------------------------------------------------------- #
def test_overlay_disabled_is_byte_exact_to_frozen_engine() -> None:
    src = _rising_source()
    provider = _ranked_provider()
    cfg = default_strategy_config()
    friction = default_friction()
    spec = BacktestSpec(initial_capital_cents=1_000_000 * 100)

    frozen = run_backtest(
        spec=spec,
        bar_source=src,
        provider=provider,
        strategy_config=cfg,
        friction_params=friction,
    )
    e2e = run_e2e_backtest(
        spec=spec,
        bar_source=src,
        provider=provider,
        strategy_config=cfg,
        friction_params=friction,
        exit_overlay=None,
    )
    # The whole frozen-engine result must compare equal field-by-field.
    assert e2e.backtest_result == frozen
    assert e2e.overlay_orders == ()
    assert e2e.overlay_sell_signals == 0
    assert e2e.overlay_buy_signals == 0


def test_noop_overlay_instance_also_byte_exact() -> None:
    # Passing an explicit NoOpExitOverlay() takes the same path as None.
    src = _rising_source()
    provider = _ranked_provider()
    cfg = default_strategy_config()
    friction = default_friction()
    spec = BacktestSpec(initial_capital_cents=1_000_000 * 100)
    frozen = run_backtest(
        spec=spec, bar_source=src, provider=provider,
        strategy_config=cfg, friction_params=friction,
    )
    e2e = run_e2e_backtest(
        spec=spec, bar_source=src, provider=provider, strategy_config=cfg,
        friction_params=friction, exit_overlay=NoOpExitOverlay(),
    )
    assert e2e.backtest_result == frozen


# --------------------------------------------------------------------------- #
# 2. EXIT injection plumbing: a forced SELL fills T+1, conservation holds.      #
# --------------------------------------------------------------------------- #
class _ForcedExitOverlay:
    """Sells the full held volume of ``target`` every day until it is gone.

    Re-emitting every day exercises the queue semantics (contract §2): an
    un-fillable SELL simply re-appears next day; once tradeable it fills once.
    """

    def __init__(self, target: str) -> None:
        self._target = target

    def orders_for_day(self, ctx: ExitOverlayContext) -> tuple[OrderIntent, ...]:
        held = ctx.held_by_code.get(self._target)
        if held is None or held.volume <= 0:
            return ()
        return (OrderIntent(code=self._target, side_is_buy=False, volume=held.volume),)


def _forced_exit_setup() -> tuple[BacktestSpec, _DictBarSource, PanelScoreProvider]:
    # Seed one opening position; empty candidates so rotation/buy never fire and
    # the overlay is the only order source (clean isolation).
    bars: dict[str, dict[str, DayBar]] = {}
    for i, day in enumerate(_DAYS):
        bars[day] = {_CODES[0]: _bar(_CODES[0], day, 100.0 + i * 0.5)}
    spec = BacktestSpec(
        initial_capital_cents=500_000 * 100,
        opening_positions=(OpeningLot(code=_CODES[0], volume=1000, cost_cents=100_00),),
    )
    return spec, bars_view(bars), _empty_provider()


def test_forced_exit_fills_next_bar_and_conserves() -> None:
    spec, src, provider = _forced_exit_setup()
    cfg = default_strategy_config()
    res = run_e2e_backtest(
        spec=spec, bar_source=src, provider=provider, strategy_config=cfg,
        friction_params=default_friction(), exit_overlay=_ForcedExitOverlay(_CODES[0]),
    )
    sell_fills = [f for f in res.backtest_result.fills if not f.side_is_buy]
    # Exactly one SELL fill of the opening lot (it sells, then nothing left).
    assert len(sell_fills) == 1
    assert sell_fills[0].code == _CODES[0]
    assert sell_fills[0].volume == 1000
    # Position is flat afterwards; conservation (the hard guarantee) holds.
    assert all(
        p.code != _CODES[0] for p in res.backtest_result.equity_curve[-1].positions
    )
    kinds = {v.kind for v in res.backtest_result.invariant_report.violations}
    assert "cash_conservation" not in kinds
    assert "position_conservation" not in kinds
    # The overlay signalled the SELL on >=1 day (day 0 at least).
    assert res.overlay_sell_signals >= 1


# --------------------------------------------------------------------------- #
# 3. Queue + trapped MTM: limit-down blocks the fill, overlay re-emits.         #
# --------------------------------------------------------------------------- #
def test_limit_down_exit_queues_and_traps_then_fills() -> None:
    # Opening lot in code X. Overlay signals SELL from day 0 (fills day 1's open).
    # Engineer day 1 X at limit-down (open <= limit_down) -> SELL lapses, X stays
    # held (trapped, marked). Day 2 X tradeable -> SELL fills. Signal-hit (>=2
    # emit days) vs fillable-hit (1 SELL fill).
    code = _CODES[0]
    bars: dict[str, dict[str, DayBar]] = {}
    for i, day in enumerate(_DAYS):
        if i == 1:
            # gap to limit-down: open == limit_down, well below the prior close.
            bars[day] = {
                code: _bar(
                    code, day, 90.0, open_px=90.0, limit_down=90.0, limit_up=110.0
                )
            }
        else:
            px = 100.0 + i * 0.1
            bars[day] = {code: _bar(code, day, px)}
    spec = BacktestSpec(
        initial_capital_cents=500_000 * 100,
        opening_positions=(OpeningLot(code=code, volume=1000, cost_cents=100_00),),
    )
    res = run_e2e_backtest(
        spec=spec, bar_source=bars_view(bars), provider=_empty_provider(),
        strategy_config=default_strategy_config(),
        friction_params=default_friction(), exit_overlay=_ForcedExitOverlay(code),
    )
    sell_fills = [f for f in res.backtest_result.fills if not f.side_is_buy]
    # Trapped through the limit-down day, but eventually sells exactly once.
    assert len(sell_fills) == 1
    assert sell_fills[0].trade_date != _DAYS[1]  # NOT filled on the limit-down day
    # Signalled on more days than it filled (re-emitted while trapped) — the
    # signal-hit vs fillable-hit split (codex R1-#8).
    assert res.overlay_sell_signals >= 2
    # On the limit-down day the position was still held & marked (trapped MTM).
    day1_snapshot = res.backtest_result.equity_curve[1]
    assert any(p.code == code for p in day1_snapshot.positions)
    assert "position_conservation" not in {
        v.kind for v in res.backtest_result.invariant_report.violations
    }


# --------------------------------------------------------------------------- #
# 4. Frozen contract: deterministic id, change-detecting.                       #
# --------------------------------------------------------------------------- #
def test_contract_id_is_deterministic_and_change_sensitive() -> None:
    a = ExitExecutionContract()
    b = ExitExecutionContract()
    assert a.contract_id == b.contract_id
    assert len(a.contract_id) == 16
    changed = ExitExecutionContract(do_t_requires_positive_unrealized=False)
    assert changed.contract_id != a.contract_id


def test_run_reports_contract_id() -> None:
    spec, src, provider = _forced_exit_setup()
    res = run_e2e_backtest(
        spec=spec, bar_source=src, provider=provider,
        strategy_config=default_strategy_config(),
        friction_params=default_friction(),
    )
    assert res.contract_id == ExitExecutionContract().contract_id


# --------------------------------------------------------------------------- #
# 5. Units: HeldContext unrealized P&L + merge precedence.                       #
# --------------------------------------------------------------------------- #
def test_held_context_unrealized_pnl() -> None:
    # cost 10.00/share * 1000 = 10_000_00; mark 12_000_00 -> +2_000_00 unrealized.
    h = HeldContext(
        code="X", volume=1000, cost_cents=1000, market_value_cents=1_200_000,
        holding_age_trading_days=3,
    )
    assert h.unrealized_pnl_cents == 1_200_000 - 1000 * 1000
    assert h.unrealized_pnl_cents == 200_000


def test_merge_pending_rotation_takes_precedence() -> None:
    rotation = (
        OrderIntent(code="A", side_is_buy=False, volume=500),
        OrderIntent(code="B", side_is_buy=True, volume=300),
    )
    overlay = (
        OrderIntent(code="A", side_is_buy=False, volume=999),  # dropped (A claimed)
        OrderIntent(code="C", side_is_buy=False, volume=100),  # kept
    )
    merged = _merge_pending(rotation_orders=rotation, overlay_orders=overlay)
    assert merged == rotation + (OrderIntent(code="C", side_is_buy=False, volume=100),)
    # Empty overlay returns the rotation tuple unchanged (byte-exact path).
    assert _merge_pending(rotation_orders=rotation, overlay_orders=()) is rotation


def test_forced_overlay_satisfies_protocol() -> None:
    assert isinstance(_ForcedExitOverlay("X"), ExitOverlay)
    assert isinstance(NoOpExitOverlay(), ExitOverlay)
