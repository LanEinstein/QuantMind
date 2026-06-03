"""Unit tests for the Line-2 intraday deterministic triggers (Phase U-C3).

Pure-function coverage of ``backend.monitoring.intraday_triggers``: quote
freshness fail-closed, canonical serialisation, the two deterministic SELL
triggers (drawdown / ATR trailing stop) + their priority, the intraday ADD
(reusing add_position bans, live-price driven), and the LINE2-MON- context
guard. The runner integration test exercises make_intraday_sell_context end
to end; here we cover the pure logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.broker.models import AccountInfo, Position
from backend.models.market import WatchlistMarketSnapshot
from backend.monitoring.add_position import AddConfig, AddRejectReason, MarketRegime
from backend.monitoring.intraday_calibration import DrawdownCalibrationConfig
from backend.monitoring.intraday_triggers import (
    INTRADAY_QUOTE_HEADER,
    IntradaySellIntent,
    IntradayTriggerConfig,
    IntradayTriggerKind,
    evaluate_intraday_add_intents,
    evaluate_intraday_sell_intents,
    filter_fresh_quotes,
    make_intraday_sell_context,
    serialize_intraday_quotes,
)

_NOW = datetime(2026, 5, 15, 10, 30, 0, tzinfo=UTC)


def _spot(
    code: str,
    *,
    price: float,
    prev_close: float,
    name: str = "测试ETF",
    snapshot_at: datetime | None = None,
    volume: float = 1_000_000.0,
    amount: float = 3.0e8,
) -> WatchlistMarketSnapshot:
    return WatchlistMarketSnapshot(
        code=code,
        name=name,
        price=price,
        open=prev_close,
        high=max(price, prev_close),
        low=min(price, prev_close),
        prev_close=prev_close,
        change_pct=(price - prev_close) / prev_close * 100 if prev_close else 0.0,
        volume=volume,
        amount=amount,
        turnover_rate=1.0,
        source="adata",
        snapshot_at=snapshot_at or (_NOW - timedelta(seconds=2)),
    )


def _position(
    code: str, *, volume: int = 300, available: int = 300, cost: float = 4.0
) -> Position:
    return Position(
        code=code,
        volume=volume,
        available_volume=available,
        cost_price=cost,
        market_value=volume * cost,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def _account(total: float = 100_000.0) -> AccountInfo:
    return AccountInfo(
        total_assets=total,
        available_cash=total * 0.9,
        frozen_cash=0.0,
        market_value=total * 0.1,
        total_pnl=0.0,
        total_pnl_pct=0.0,
        initial_capital=total,
    )


# ---------------------------------------------------------------------------
# filter_fresh_quotes — invariant 3 (stale / missing fail-closed)
# ---------------------------------------------------------------------------


def test_filter_fresh_quotes_partitions_fresh_and_stale() -> None:
    spots = {
        "510300": _spot("510300", price=4.2, prev_close=4.5),  # fresh
        "510500": _spot("510500", price=0.0, prev_close=6.0),  # no price → stale
        "159949": _spot(
            "159949", price=3.0, prev_close=3.1,
            snapshot_at=_NOW - timedelta(seconds=120),  # too old → stale
        ),
    }
    fresh, stale = filter_fresh_quotes(
        spots, list(spots), now=_NOW, max_staleness_seconds=60.0
    )
    assert fresh == frozenset({"510300"})
    assert set(stale) == {"510500", "159949"}


def test_filter_fresh_quotes_same_instant_is_stale() -> None:
    # A quote tagged exactly at ``now`` is NOT strictly before the decision,
    # so it fails closed (mirrors the InstructionPlan invariant — codex U-C3 P1).
    spots = {"510300": _spot("510300", price=4.2, prev_close=4.5, snapshot_at=_NOW)}
    fresh, stale = filter_fresh_quotes(
        spots, ["510300"], now=_NOW, max_staleness_seconds=60.0
    )
    assert fresh == frozenset()
    assert set(stale) == {"510300"}


def test_filter_fresh_quotes_missing_and_future_dated_are_stale() -> None:
    spots = {
        "510300": _spot(
            "510300", price=4.2, prev_close=4.5,
            snapshot_at=_NOW + timedelta(seconds=30),  # future-dated → stale
        ),
    }
    fresh, stale = filter_fresh_quotes(
        spots, ["510300", "510500"], now=_NOW, max_staleness_seconds=60.0
    )
    assert fresh == frozenset()
    assert set(stale) == {"510300", "510500"}  # 510500 absent → stale


# ---------------------------------------------------------------------------
# serialize_intraday_quotes — deterministic, sorted, comma-safe
# ---------------------------------------------------------------------------


def test_serialize_intraday_quotes_is_deterministic_and_sorted() -> None:
    spots = {
        "510500": _spot("510500", price=6.0, prev_close=6.1, name="中证500,ETF"),
        "510300": _spot("510300", price=4.2, prev_close=4.5),
    }
    raw, rows = serialize_intraday_quotes(spots, ["510500", "510300"])
    lines = raw.decode("utf-8").splitlines()
    assert lines[0] == ",".join(INTRADAY_QUOTE_HEADER)
    assert lines[1].startswith("510300,")  # sorted by code
    assert lines[2].startswith("510500,")
    assert "中证500 ETF" in lines[2]  # comma sanitised → space
    # Per-code row bytes match the serialized line (consumed-row lineage).
    assert rows["510300"] == lines[1].encode("utf-8")
    assert rows["510500"] == lines[2].encode("utf-8")


# ---------------------------------------------------------------------------
# evaluate_intraday_sell_intents — the two deterministic triggers
# ---------------------------------------------------------------------------


def _volatile_closes(n: int = 30) -> tuple[float, ...]:
    # Alternating 4.2/4.8 → close_atr ≈ 0.6, recent_high = 4.8, wide ATR band.
    return tuple(4.2 if i % 2 else 4.8 for i in range(n))


def _rising_closes(n: int = 21) -> tuple[float, ...]:
    # 4.0 → 5.0 by +0.05/day → close_atr ≈ 0.05, recent_high = 5.0, tight band.
    return tuple(round(4.0 + 0.05 * i, 4) for i in range(n))


def test_sell_drawdown_trigger_fires_alone() -> None:
    spots = {"510300": _spot("510300", price=4.185, prev_close=4.5)}  # -7%
    closes = {"510300": _volatile_closes()}  # wide ATR band (stop ≈ 3.6)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (_position("510300"),)
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP
    assert intents[0].available_volume == 300
    assert intents[0].limit_price == 4.185


def test_sell_atr_trailing_stop_fires_alone() -> None:
    spots = {"510500": _spot("510500", price=4.85, prev_close=5.0)}  # -3% (no dd)
    closes = {"510500": _rising_closes()}  # recent_high 5.0, atr .05, stop 4.9
    intents = evaluate_intraday_sell_intents(
        spots, closes, (_position("510500"),)
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.ATR_TRAILING_STOP
    assert intents[0].limit_price < intents[0].stop_level  # price below the stop


def test_sell_both_triggers_prefer_atr_priority() -> None:
    # -8% drawdown AND below the ATR stop → structural ATR_TRAILING_STOP wins.
    spots = {"510500": _spot("510500", price=4.6, prev_close=5.0)}
    closes = {"510500": _rising_closes()}
    intents = evaluate_intraday_sell_intents(
        spots, closes, (_position("510500"),)
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.ATR_TRAILING_STOP


def test_sell_atr_requires_full_recent_high_window() -> None:
    # With fewer than recent_high_window (20) daily closes the ATR trailing
    # stop must NOT fire (an incomplete window understates the recent high and
    # would trip the stop early — codex U-C3 P2). Drawdown stays < threshold,
    # so the whole code produces no intent.
    short = tuple(round(4.0 + 0.05 * i, 4) for i in range(15))  # 15 < 20
    spots = {"510500": _spot("510500", price=4.55, prev_close=4.7)}  # -3.2% (no dd)
    intents = evaluate_intraday_sell_intents(
        spots, {"510500": short}, (_position("510500"),)
    )
    assert intents == ()


def test_sell_skips_when_nothing_settled() -> None:
    spots = {"510300": _spot("510300", price=4.185, prev_close=4.5)}
    closes = {"510300": _volatile_closes()}
    intents = evaluate_intraday_sell_intents(
        spots, closes, (_position("510300", available=0),)
    )
    assert intents == ()


def test_sell_no_trigger_on_calm_quote() -> None:
    spots = {"510300": _spot("510300", price=4.49, prev_close=4.5)}  # -0.2%
    closes = {"510300": _volatile_closes()}  # stop ≈ 3.6, price well above
    intents = evaluate_intraday_sell_intents(
        spots, closes, (_position("510300"),)
    )
    assert intents == ()


# ---------------------------------------------------------------------------
# P-005 — take-profit (+1R tranche) + over-allocation weight-trim triggers
# ---------------------------------------------------------------------------


def _near_high_closes(n: int = 21) -> tuple[float, ...]:
    # Rises 4.50 → 4.95 by +0.0225/day → close_atr ≈ 0.0225, recent_high 4.95.
    # A live price AT the recent high stays just above the ATR stop (4.95 −
    # 2×0.0225 ≈ 4.905), so the ATR exit does NOT fire — isolating take-profit.
    return tuple(round(4.50 + 0.0225 * i, 4) for i in range(n))


def test_take_profit_fires_and_sells_tranche() -> None:
    # price 4.95 ≥ cost 4.0 + 1R; ATR exit does not fire (price ≥ stop).
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}  # -1% calm
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=300, available=300, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account()
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.TAKE_PROFIT
    assert intents[0].available_volume == 100  # floor(300 × 0.5 / 100) × 100
    assert intents[0].limit_price == 4.95


def test_take_profit_suppressed_when_already_taken() -> None:
    # P-006 gate: a still-open episode that already took profit does not repeat.
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=300, available=300, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        take_profit_already_taken=frozenset({"510300"}),
    )
    assert intents == ()


def test_take_profit_sub_one_lot_tranche_skips() -> None:
    # 100 settled × 0.5 = 50 → floors below one lot → no take-profit (never 0).
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=100, available=100, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account()
    )
    assert intents == ()


def test_take_profit_skips_underwater_position() -> None:
    # Cost 4.5 above the recent high (3.82): price 3.79 is a loss, so no
    # take-profit, and ≥ the ATR stop (3.74) so no ATR exit either → no intent.
    closes_series = tuple(3.78 if i % 2 else 3.82 for i in range(25))
    spots = {"510300": _spot("510300", price=3.79, prev_close=3.80)}  # calm
    pos = _position("510300", volume=300, available=300, cost=4.5)
    intents = evaluate_intraday_sell_intents(
        spots, {"510300": closes_series}, (pos,), account=_account()
    )
    assert intents == ()


def test_weight_trim_fires_and_trims_to_target() -> None:
    # weight 5000×4.0 / 100k = 20% > 15%×1.10 (16.5%) → trim back toward 13%.
    spots = {"510300": _spot("510300", price=4.0, prev_close=4.02)}  # calm
    closes = {"510300": _volatile_closes()}  # ATR stop 3.6 (well below 4.0)
    pos = _position("510300", volume=5000, available=5000, cost=4.5)  # below cost
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(100_000.0),
        max_single_stock_pct=0.15,
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.WEIGHT_TRIM
    # excess = 5000×4.0 − 0.13×100k = 7000 → floor(7000 / 400) × 100 = 1700.
    assert intents[0].available_volume == 1700
    assert intents[0].available_volume % 100 == 0


def test_weight_trim_within_band_does_not_fire() -> None:
    # weight 4000×4.0 / 100k = 16% ≤ 16.5% band → no trim.
    spots = {"510300": _spot("510300", price=4.0, prev_close=4.02)}
    closes = {"510300": _volatile_closes()}
    pos = _position("510300", volume=4000, available=4000, cost=4.5)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(100_000.0),
        max_single_stock_pct=0.15,
    )
    assert intents == ()


def test_priority_take_profit_over_weight_trim() -> None:
    # Both fire (in +1R profit AND over-weight) → take-profit wins.
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=5000, available=5000, cost=4.0)  # 24.75% wt
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(100_000.0),
        max_single_stock_pct=0.15,
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.TAKE_PROFIT


def test_priority_drawdown_over_take_profit() -> None:
    # In +1R profit AND an −8% intraday drawdown, ATR not fired → the risk exit
    # (drawdown) outranks take-profit (a protective exit is never masked).
    closes_series = tuple(4.45 if i % 2 else 4.55 for i in range(25))  # high 4.55
    spots = {"510300": _spot("510300", price=4.6, prev_close=5.0)}  # -8% dd
    pos = _position("510300", volume=300, available=300, cost=4.0)  # in profit
    intents = evaluate_intraday_sell_intents(
        spots, {"510300": closes_series}, (pos,), account=_account()
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP


def test_no_account_skips_take_profit_and_trim() -> None:
    # Back-compat: legacy callers pass no account → only the two risk exits;
    # a +1R / over-weight position produces no intent.
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=5000, available=5000, cost=4.0)
    intents = evaluate_intraday_sell_intents(spots, closes, (pos,))  # no account
    assert intents == ()


# ---------------------------------------------------------------------------
# drawdown_calibration — per-stock adaptive DRAWDOWN_STOP threshold (D1-a)
# (P0-7-amendment-2026-06-03)
# ---------------------------------------------------------------------------


def _dd_closes(low: float, high: float, n: int = 70) -> tuple[float, ...]:
    return tuple(low if i % 2 else high for i in range(n))


def test_drawdown_calibration_none_is_fixed_baseline() -> None:
    # No calibration → the static 5% threshold: a −6% drawdown fires (v4 parity).
    spots = {"510300": _spot("510300", price=9.4, prev_close=10.0)}  # -6%
    closes = {"510300": _dd_closes(8.0, 8.4)}  # ATR stop 7.6 ≪ 9.4 → no ATR exit
    pos = _position("510300", volume=300, available=300, cost=12.0)  # underwater
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account()
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP
    # The effective threshold (= static 5% with no calibration) is carried so the
    # persisted record reproduces the decision on replay (codex P2).
    assert intents[0].effective_drawdown_threshold == 0.05


def test_drawdown_calibration_widens_for_volatile_stock() -> None:
    # Same −6% drawdown, but the stock's ~5% daily volatility derives a ~7.5%
    # adaptive threshold → the −6% move is NOT abnormal for it → no stop.
    spots = {"510300": _spot("510300", price=9.4, prev_close=10.0)}  # -6%
    closes = {"510300": _dd_closes(8.0, 8.4)}
    pos = _position("510300", volume=300, available=300, cost=12.0)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        drawdown_calibration=DrawdownCalibrationConfig(),
    )
    assert intents == ()


def test_drawdown_calibration_tightens_for_calm_stock() -> None:
    # A calm stock: the fixed 5% would not stop a −4% drop, but its ~0.25%
    # volatility derives the 3% floor → the −4% move IS abnormal → stop fires.
    spots = {"510300": _spot("510300", price=9.6, prev_close=10.0)}  # -4%
    closes = {"510300": _dd_closes(8.00, 8.02)}  # ATR stop 7.98 ≪ 9.6 → no ATR
    pos = _position("510300", volume=300, available=300, cost=12.0)

    base = evaluate_intraday_sell_intents(spots, closes, (pos,), account=_account())
    assert base == ()  # fixed 5% → -4% does not fire

    adaptive = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        drawdown_calibration=DrawdownCalibrationConfig(),
    )
    assert len(adaptive) == 1
    assert adaptive[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP
    # Fired on the adaptive 3% floor → the record carries 3%, NOT the static 5%
    # (else replay would recompute -4% vs 5% → not fired → reject a real signal).
    assert adaptive[0].effective_drawdown_threshold == 0.03


def test_drawdown_calibration_insufficient_history_uses_fixed() -> None:
    # Too few daily closes → derive returns None → fall back to the fixed 5%,
    # never a looser-than-intended stop. The −6% drawdown still fires.
    spots = {"510300": _spot("510300", price=9.4, prev_close=10.0)}  # -6%
    closes = {"510300": _dd_closes(8.0, 8.4, n=10)}  # < min_history+1 → None
    pos = _position("510300", volume=300, available=300, cost=12.0)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        drawdown_calibration=DrawdownCalibrationConfig(),
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP


def test_drawdown_calibration_recorded_on_lower_priority_sell() -> None:
    # codex P2: under a WIDENED adaptive threshold (~7.5%), a −6% move does NOT
    # trip the drawdown stop, but a WEIGHT_TRIM fires — and it must carry the
    # adaptive threshold (not the static 5%), else the manifest would imply a
    # drawdown stop should have fired and replay/audit would reject the trim.
    spots = {"510300": _spot("510300", price=9.4, prev_close=10.0)}  # -6%
    closes = {"510300": _dd_closes(8.0, 8.4)}  # ATR stop 7.6 ≪ 9.4; adaptive ~7.5%
    pos = _position("510300", volume=5000, available=5000, cost=12.0)  # 47% wt
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(100_000.0),
        max_single_stock_pct=0.15,
        drawdown_calibration=DrawdownCalibrationConfig(),
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.WEIGHT_TRIM
    assert intents[0].effective_drawdown_threshold is not None
    assert 0.07 <= intents[0].effective_drawdown_threshold <= 0.08


def test_regime_bear_tightens_adaptive_drawdown() -> None:
    # D1-b: a −6.5% move does not breach the volatile stock's ~7.5% adaptive
    # threshold, but a BEAR regime tightens it to 7.5%×0.8 = 6% → it now fires.
    spots = {"510300": _spot("510300", price=9.35, prev_close=10.0)}  # -6.5%
    closes = {"510300": _dd_closes(8.0, 8.4)}  # adaptive ~7.5%; ATR stop 7.6 ≪ 9.35
    pos = _position("510300", volume=300, available=300, cost=12.0)

    non_bear = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        drawdown_calibration=DrawdownCalibrationConfig(),
    )
    assert non_bear == ()  # 7.5% threshold not breached by -6.5%

    bear = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        drawdown_calibration=DrawdownCalibrationConfig(),
        regime=MarketRegime.BEAR,
    )
    assert len(bear) == 1
    assert bear[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP
    # The tightened threshold is recorded (replay reproduces the bear decision).
    assert bear[0].effective_drawdown_threshold == 0.06


def test_regime_non_bear_does_not_tighten() -> None:
    # A NEUTRAL/BULL regime leaves the adaptive threshold unconditioned.
    spots = {"510300": _spot("510300", price=9.35, prev_close=10.0)}  # -6.5%
    closes = {"510300": _dd_closes(8.0, 8.4)}
    pos = _position("510300", volume=300, available=300, cost=12.0)
    out = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        drawdown_calibration=DrawdownCalibrationConfig(),
        regime=MarketRegime.NEUTRAL,
    )
    assert out == ()


# ---------------------------------------------------------------------------
# long_term_hold_codes — thesis-gated take-profit exemption
# (P0-10-amendment-line2-2026-06-03)
# ---------------------------------------------------------------------------


def test_long_term_hold_exempt_from_take_profit() -> None:
    # Same +1R scenario as test_take_profit_fires_and_sells_tranche, but the
    # code is a long-term hold (intact thesis) → take-profit is exempt.
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=300, available=300, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        long_term_hold_codes=frozenset({"510300"}),
    )
    assert intents == ()


def test_non_long_term_code_takes_profit_normally() -> None:
    # The gate is per-code: a +1R code NOT in the set still takes profit.
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=300, available=300, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        long_term_hold_codes=frozenset({"600519"}),  # a different code
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.TAKE_PROFIT


def test_empty_long_term_hold_reproduces_baseline() -> None:
    # Default empty set → identical to the pre-amendment (v3) behaviour.
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=300, available=300, cost=4.0)
    base = evaluate_intraday_sell_intents(spots, closes, (pos,), account=_account())
    exempt_empty = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(),
        long_term_hold_codes=frozenset(),
    )
    assert base == exempt_empty
    assert base[0].trigger_kind is IntradayTriggerKind.TAKE_PROFIT


def test_long_term_hold_hard_cap_trims_back_to_cap() -> None:
    # Over the 15% hard cap (20% weight): a long-term hold is exempt from the
    # soft trim but STILL trimmed back to exactly 15% (not 13%).
    spots = {"510300": _spot("510300", price=4.0, prev_close=4.02)}  # calm
    closes = {"510300": _volatile_closes()}  # ATR stop 3.6 (well below 4.0)
    pos = _position("510300", volume=5000, available=5000, cost=4.5)  # 20% wt
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(100_000.0),
        max_single_stock_pct=0.15,
        long_term_hold_codes=frozenset({"510300"}),
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.WEIGHT_TRIM
    # excess to the 15% cap = 5000×4.0 − 0.15×100k = 5000 → ceil(5000/400)×100.
    assert intents[0].available_volume == 1300  # rounded UP so post-trim ≤ 15%
    # Post-trim weight must be AT/BELOW the 15% hard cap (codex P2 invariant).
    remaining = (5000 - intents[0].available_volume) * 4.0
    assert remaining / 100_000.0 <= 0.15


def test_long_term_hold_trims_inside_soft_band_at_hard_cap() -> None:
    # 16% weight: a NORMAL hold is within the 16.5% soft band → no trim, but a
    # long-term hold is over the 15% hard cap → trims back to 15%.
    spots = {"510300": _spot("510300", price=4.0, prev_close=4.02)}
    closes = {"510300": _volatile_closes()}
    pos = _position("510300", volume=4000, available=4000, cost=4.5)  # 16% wt
    normal = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(100_000.0),
        max_single_stock_pct=0.15,
    )
    assert normal == ()  # within the soft band
    long_term = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(100_000.0),
        max_single_stock_pct=0.15,
        long_term_hold_codes=frozenset({"510300"}),
    )
    assert len(long_term) == 1
    assert long_term[0].trigger_kind is IntradayTriggerKind.WEIGHT_TRIM
    # excess to 15% = 4000×4.0 − 0.15×100k = 1000 → ceil(1000/400)×100 = 300.
    assert long_term[0].available_volume == 300  # rounded UP so post-trim ≤ 15%
    remaining = (4000 - long_term[0].available_volume) * 4.0
    assert remaining / 100_000.0 <= 0.15


def test_long_term_hold_hard_cap_trim_clamped_to_single_instruction_cap() -> None:
    # A large winner well over the cap: the full excess (¥100k) would exceed the
    # ¥50k single-instruction cap (check #9 rejects SELLs too) → clamp to a VALID
    # ¥50k partial trim that reduces the position now (codex P2 cycle-2), rather
    # than emit a rejected order that dedups and strands the position over-cap.
    spots = {"510300": _spot("510300", price=10.0, prev_close=10.05)}  # calm
    closes = {"510300": _volatile_closes()}  # recent high 4.8 ≪ 10 → no ATR exit
    pos = _position("510300", volume=25000, available=25000, cost=8.0)  # 25% wt
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(1_000_000.0),
        max_single_stock_pct=0.15,
        long_term_hold_codes=frozenset({"510300"}),
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.WEIGHT_TRIM
    # excess ¥100k → 10000 sh, clamped to ¥50k cap → 5000 sh.
    assert intents[0].available_volume == 5000
    assert intents[0].available_volume * 10.0 <= 50_000.0  # within check #9


def test_long_term_hold_below_hard_cap_rides_free() -> None:
    # 14% weight, in +1R profit: exempt from take-profit AND below the hard cap
    # → no intent (the conviction winner rides free).
    spots = {"510300": _spot("510300", price=4.95, prev_close=5.0)}
    closes = {"510300": _near_high_closes()}
    pos = _position("510300", volume=2800, available=2800, cost=4.0)  # ~13.9% wt
    intents = evaluate_intraday_sell_intents(
        spots, closes, (pos,), account=_account(100_000.0),
        max_single_stock_pct=0.15,
        long_term_hold_codes=frozenset({"510300"}),
    )
    assert intents == ()


def test_long_term_hold_still_stops_out_on_drawdown() -> None:
    # The exemption NEVER relaxes a protective stop: an −8% drawdown on a
    # long-term hold still fires DRAWDOWN_STOP (full settled volume).
    closes_series = tuple(4.45 if i % 2 else 4.55 for i in range(25))
    spots = {"510300": _spot("510300", price=4.6, prev_close=5.0)}  # -8% dd
    pos = _position("510300", volume=300, available=300, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, {"510300": closes_series}, (pos,), account=_account(),
        long_term_hold_codes=frozenset({"510300"}),
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP
    assert intents[0].available_volume == 300  # full settled, not a tranche


def test_long_term_hold_still_stops_out_on_atr() -> None:
    # ATR trailing stop also still fires for a long-term hold (stop not relaxed).
    closes = _volatile_closes()  # recent high 4.8, ATR ≈ 0.6 → stop ≈ 3.6
    spots = {"510300": _spot("510300", price=3.5, prev_close=3.55)}  # < stop
    pos = _position("510300", volume=300, available=300, cost=3.0)
    intents = evaluate_intraday_sell_intents(
        spots, {"510300": closes}, (pos,), account=_account(),
        long_term_hold_codes=frozenset({"510300"}),
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.ATR_TRAILING_STOP


# ---------------------------------------------------------------------------
# make_intraday_sell_context — LINE2-MON- guard
# ---------------------------------------------------------------------------


def test_make_intraday_sell_context_rejects_non_line2_signal_id() -> None:
    intent = IntradaySellIntent(
        code="510300", name="沪深300ETF", available_volume=300, limit_price=4.185,
        trigger_kind=IntradayTriggerKind.DRAWDOWN_STOP, anomaly_reason="x",
        drawdown_pct=-0.07, atr=0.0, recent_high=0.0, stop_level=0.0,
    )
    # The prefix guard fires before any heavy object is touched.
    try:
        make_intraday_sell_context(
            intent, now=_NOW, signal_id="SIG-bad", seq=1, snapshot_at=_NOW,
            account=None, positions=(), prev_close=None, daily_state=None,
            stock_meta=None, risk_engine=None, open_tickets=(),
            circuit_breaker=None, data_quality=None, watchlist_policy=None,
            watchlist_signal=None,
        )
    except ValueError as exc:
        assert "LINE2-MON-" in str(exc)
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected ValueError for non-LINE2 signal_id")


# ---------------------------------------------------------------------------
# evaluate_intraday_add_intents — reuse add_position bans (live-price driven)
# ---------------------------------------------------------------------------

_NEUTRAL_INDEX = tuple(5.0 for _ in range(25))  # flat → NEUTRAL regime
_BEAR_INDEX = tuple(round(6.0 - 0.05 * i, 4) for i in range(25))  # falling → BEAR


def _add_closes() -> tuple[float, ...]:
    # Oscillating around 4.8 → ma_long ≈ 4.8, wide ATR (no false ATR-SELL),
    # last close not used for the dip (live price drives the add).
    return tuple(4.5 if i % 2 else 5.1 for i in range(30))


def test_add_fires_on_oversold_vs_cost() -> None:
    spots = {"159949": _spot("159949", price=4.8, prev_close=4.85)}
    closes = {"159949": _add_closes()}
    pos = (_position("159949", volume=100, available=100, cost=5.0),)  # -4% dip
    result = evaluate_intraday_add_intents(
        spots, closes, pos, _account(), index_closes=_NEUTRAL_INDEX
    )
    assert len(result.intents) == 1
    add = result.intents[0]
    assert add.code == "159949"
    assert add.add_volume > 0
    assert add.limit_price == 4.8


def test_add_blocked_in_bear_regime() -> None:
    spots = {"159949": _spot("159949", price=4.8, prev_close=4.85)}
    closes = {"159949": _add_closes()}
    pos = (_position("159949", volume=100, available=100, cost=5.0),)
    result = evaluate_intraday_add_intents(
        spots, closes, pos, _account(), index_closes=_BEAR_INDEX
    )
    assert result.intents == ()
    assert result.rejections[0].reason is AddRejectReason.BEAR_REGIME


def test_add_anti_martingale_rejects_deep_underwater() -> None:
    spots = {"159949": _spot("159949", price=4.0, prev_close=4.05)}  # -20% vs cost
    closes = {"159949": _add_closes()}
    pos = (_position("159949", volume=100, available=100, cost=5.0),)
    result = evaluate_intraday_add_intents(
        spots, closes, pos, _account(), index_closes=_NEUTRAL_INDEX
    )
    assert result.intents == ()
    assert result.rejections[0].reason is AddRejectReason.MARTINGALE


def test_add_rejects_when_not_below_cost() -> None:
    spots = {"159949": _spot("159949", price=5.2, prev_close=5.0)}  # above cost
    closes = {"159949": _add_closes()}
    pos = (_position("159949", volume=100, available=100, cost=5.0),)
    result = evaluate_intraday_add_intents(
        spots, closes, pos, _account(), index_closes=_NEUTRAL_INDEX
    )
    assert result.intents == ()
    assert result.rejections[0].reason is AddRejectReason.NOT_OVERSOLD


def test_add_rejects_on_no_headroom() -> None:
    # Position already ~19% of equity → over the 15% single-stock cap.
    spots = {"159949": _spot("159949", price=4.8, prev_close=4.85)}
    closes = {"159949": _add_closes()}
    pos = (_position("159949", volume=4000, available=4000, cost=5.0),)
    result = evaluate_intraday_add_intents(
        spots, closes, pos, _account(), index_closes=_NEUTRAL_INDEX
    )
    assert result.intents == ()
    assert result.rejections[0].reason is AddRejectReason.NO_HEADROOM


def test_intraday_trigger_config_is_frozen() -> None:
    cfg = IntradayTriggerConfig()
    assert cfg.drawdown_threshold == 0.05
    try:
        cfg.drawdown_threshold = 0.1  # type: ignore[misc]
    except AttributeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("IntradayTriggerConfig must be frozen")


def test_add_config_default_reused() -> None:
    # Sanity: the intraday add reuses the locked add_position thresholds.
    assert AddConfig().max_add_drawdown_pct == 0.10
    assert AddConfig().max_single_stock_pct == 0.15


# ---------------------------------------------------------------------------
# THESIS_QUANT_BREAK (W-004) — strictly ADD-only sell pressure
# ---------------------------------------------------------------------------


def test_thesis_break_fires_full_volume_sell_when_no_risk_exit() -> None:
    # Price flat (no drawdown), no closes (no ATR), but the deterministic thesis
    # is broken → a full settled-volume THESIS_QUANT_BREAK exit.
    spots = {"510300": _spot("510300", price=4.0, prev_close=4.0)}
    pos = _position("510300", volume=300, available=300, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, {}, (pos,), account=_account(),
        thesis_break_by_code={"510300": "买入逻辑失效(确定性)"},
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.THESIS_QUANT_BREAK
    assert intents[0].available_volume == 300  # full settled exit
    assert "买入逻辑失效" in intents[0].anomaly_reason


def test_empty_thesis_break_map_reproduces_baseline() -> None:
    # ADD-only red line: an empty map must reproduce the prior outputs exactly
    # (a flat, ATR-less, profit-less position yields NO intent either way).
    spots = {"510300": _spot("510300", price=4.0, prev_close=4.0)}
    pos = _position("510300", volume=300, available=300, cost=4.0)
    baseline = evaluate_intraday_sell_intents(spots, {}, (pos,), account=_account())
    with_empty = evaluate_intraday_sell_intents(
        spots, {}, (pos,), account=_account(), thesis_break_by_code={}
    )
    assert baseline == with_empty == ()


def test_drawdown_stop_outranks_thesis_break() -> None:
    # A protective stop is NEVER masked by a thesis break (priority red line).
    spots = {"510300": _spot("510300", price=3.7, prev_close=4.0)}  # -7.5% drawdown
    pos = _position("510300", volume=300, available=300, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, {}, (pos,), account=_account(),
        thesis_break_by_code={"510300": "thesis broke"},
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP


def test_atr_trailing_stop_outranks_thesis_break() -> None:
    # The ATR trailing stop (strongest protective exit) also wins.
    spots = {"510300": _spot("510300", price=3.5, prev_close=3.55)}
    # 20+ closes with a high near 5.0 so recent_high − 2·ATR is above 3.5.
    closes = tuple([5.0] * 19 + [3.6])
    pos = _position("510300", volume=300, available=300, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, {"510300": closes}, (pos,), account=_account(),
        thesis_break_by_code={"510300": "thesis broke"},
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.ATR_TRAILING_STOP


def test_thesis_break_outranks_take_profit_full_exit_not_tranche() -> None:
    # A broken thesis FULLY exits rather than merely taking a +1R tranche — this
    # is ADDED pressure (full > tranche), never a relaxation of take-profit.
    spots = {"510300": _spot("510300", price=4.6, prev_close=4.0)}
    closes = tuple([3.9 + 0.01 * i for i in range(20)])  # gives a small ATR
    pos = _position("510300", volume=400, available=400, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, {"510300": closes}, (pos,), account=_account(),
        thesis_break_by_code={"510300": "thesis broke"},
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.THESIS_QUANT_BREAK
    assert intents[0].available_volume == 400  # full, not a 0.5 tranche


def test_thesis_break_does_not_widen_take_profit_when_intact() -> None:
    # A code NOT in the break map (thesis intact) → take-profit behaves exactly
    # as before (the feature never loosens an existing trigger).
    spots = {"510300": _spot("510300", price=4.6, prev_close=4.0)}
    closes = tuple([3.9 + 0.01 * i for i in range(20)])
    pos = _position("510300", volume=400, available=400, cost=4.0)
    without = evaluate_intraday_sell_intents(
        spots, {"510300": closes}, (pos,), account=_account()
    )
    with_intact = evaluate_intraday_sell_intents(
        spots, {"510300": closes}, (pos,), account=_account(),
        thesis_break_by_code={"000001": "other code broke"},  # not this code
    )
    assert without == with_intact  # identical → take-profit not widened


def test_thesis_break_clamps_to_single_instruction_cap() -> None:
    # codex W-004 P2: a large full exit (> ¥50k single-instruction cap) is
    # CLAMPED so the SELL passes RiskEngine check #9 — the feature can only ADD
    # pressure, never have a rejected oversized full-exit replace a passing
    # smaller trigger. 30000 @ 4.0 = ¥120k → clamp to floor(50000/400)*100=12500.
    spots = {"510300": _spot("510300", price=4.0, prev_close=4.0)}
    pos = _position("510300", volume=30_000, available=30_000, cost=4.0)
    intents = evaluate_intraday_sell_intents(
        spots, {}, (pos,), account=_account(total=500_000.0),
        thesis_break_by_code={"510300": "thesis broke"},
        max_single_instruction_amount=50_000.0,
    )
    assert len(intents) == 1
    assert intents[0].trigger_kind is IntradayTriggerKind.THESIS_QUANT_BREAK
    assert intents[0].available_volume == 12_500  # clamped to the ¥50k cap
    assert intents[0].available_volume * intents[0].limit_price <= 50_000.0
