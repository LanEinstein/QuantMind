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
from backend.monitoring.add_position import AddConfig, AddRejectReason
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
