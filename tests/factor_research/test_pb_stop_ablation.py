"""Unit tests for the MD-1 P-B stop-loss ablation building blocks (no IO)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from backend.backtest.strategy import (
    CodeHealth,
    DailySignals,
    HeldPosition,
    PortfolioView,
    QuantCandidate,
)
from scripts.factor_research.gate_backtest import default_strategy_config
from scripts.factor_research.pb_stop_ablation import (
    MDD_IMPROVEMENT_MIN,
    MIN_STOP_FILLS,
    NET_RETENTION_MIN,
    churn_count,
    executed_stop_fills,
    make_stop_decide,
    stop_flags_from_series,
    verdict,
)


@dataclass(frozen=True)
class _Series:
    """Duck-typed PricedSeries: only what stop_flags_from_series reads."""

    code: str
    dates: np.ndarray
    adjusted_closes: np.ndarray


def _flat_series(code: str, n: int, level: float) -> _Series:
    return _Series(
        code=code,
        dates=np.arange(20200101, 20200101 + n, dtype=np.int32),
        adjusted_closes=np.full(n, level),
    )


def test_stop_flags_bottom_decile_of_cross_section() -> None:
    n = 25
    # 10 codes: nine flat, one crashing 50% over the window → bottom decile.
    series = [_flat_series(f"C{i:02d}.SZ", n, 10.0) for i in range(9)]
    crash = np.linspace(10.0, 5.0, n)
    series.append(
        _Series(
            code="BAD.SZ",
            dates=np.arange(20200101, 20200101 + n, dtype=np.int32),
            adjusted_closes=crash,
        )
    )
    flags, stats = stop_flags_from_series(series, window=20, quantile=0.10)
    last_day = str(20200101 + n - 1)
    assert "BAD.SZ" in flags[last_day]
    assert all("C" not in c for c in flags[last_day])
    assert stats["securities_without_trailing_history"] == 0


def test_stop_flags_exclusions_and_short_history() -> None:
    series = [
        _flat_series("A.SZ", 25, 10.0),
        _flat_series("B.BJ", 25, 10.0),  # BJ excluded
        _flat_series("X.SZ", 25, 10.0),  # in exclude set
        _flat_series("Y.SZ", 10, 10.0),  # too short
    ]
    flags, stats = stop_flags_from_series(
        series, window=20, quantile=0.10, exclude=frozenset({"X.SZ"})
    )
    seen = set().union(*flags.values()) if flags else set()
    assert "B.BJ" not in seen and "X.SZ" not in seen and "Y.SZ" not in seen
    assert stats["securities_without_trailing_history"] == 1


def _signals(day: str, candidates: list[tuple[str, float]]) -> DailySignals:
    health = {
        code: CodeHealth(line1_percentile=0.9, composite_score=score)
        for code, score in candidates
    }
    return DailySignals(
        trade_date=day,
        quant_candidates=tuple(
            QuantCandidate(code=c, score=s) for c, s in candidates
        ),
        health=health,
    )


@dataclass(frozen=True)
class _Bar:
    close_cents: int = 1000


def _view(holdings: dict[str, int]) -> PortfolioView:
    return PortfolioView(
        trade_date="20200101",
        total_equity_cents=100_000_00,
        cash_cents=50_000_00,
        holdings=tuple(
            HeldPosition(code=c, volume=v, holding_age_trading_days=5)
            for c, v in holdings.items()
        ),
    )


def test_stop_decide_force_sells_flagged_holding() -> None:
    log: list = []
    decide = make_stop_decide({"20200101": frozenset({"HELD.SZ"})}, intents_log=log)
    decision = decide(
        signals=_signals("20200101", []),
        view=_view({"HELD.SZ": 300}),
        bars={},
        config=default_strategy_config(),
    )
    assert decision.sell_codes == ("HELD.SZ",)
    sells = [o for o in decision.orders if not o.side_is_buy]
    assert len(sells) == 1 and sells[0].volume == 300
    assert log == [("20200101", ("HELD.SZ",))]


def test_stop_decide_vetoes_buy_of_flagged_name() -> None:
    log: list = []
    decide = make_stop_decide({"20200101": frozenset({"FLAG.SZ"})}, intents_log=log)
    decision = decide(
        signals=_signals("20200101", [("FLAG.SZ", 1.0), ("OK.SZ", 0.9)]),
        view=_view({}),
        bars={"FLAG.SZ": _Bar(), "OK.SZ": _Bar()},
        config=default_strategy_config(),
    )
    assert "FLAG.SZ" not in decision.buy_codes
    assert all(not (o.side_is_buy and o.code == "FLAG.SZ") for o in decision.orders)
    assert log == []  # nothing held, nothing stop-sold


def test_stop_decide_no_flags_returns_base_unchanged() -> None:
    log: list = []
    decide = make_stop_decide({}, intents_log=log)
    signals = _signals("20200101", [("OK.SZ", 0.9)])
    view = _view({})
    bars = {"OK.SZ": _Bar()}
    config = default_strategy_config()
    from backend.backtest.strategy import decide_day

    assert decide(signals=signals, view=view, bars=bars, config=config) == decide_day(
        signals=signals, view=view, bars=bars, config=config
    )


@dataclass(frozen=True)
class _Fill:
    trade_date: str
    code: str
    side_is_buy: bool
    volume: int = 100


@dataclass
class _FakeBacktest:
    fills: tuple = ()


@dataclass
class _FakeGateResult:
    backtest_result: _FakeBacktest = field(default_factory=_FakeBacktest)


def test_executed_stop_fills_matches_next_day_fills_only() -> None:
    days = ["d1", "d2", "d3"]
    intents = [("d1", ("A",)), ("d2", ("B",))]
    result = _FakeGateResult(
        _FakeBacktest(
            fills=(
                _Fill("d2", "A", False),  # executed stop
                _Fill("d2", "C", False),  # rotation sell, not a stop
                _Fill("d3", "B", True),  # buy, ignored
            )
        )
    )
    fills = executed_stop_fills(intents, result, days)  # type: ignore[arg-type]
    assert fills == (("d2", "A"),)


def test_churn_counts_rebuy_within_two_rebalances_plus_fill_day() -> None:
    days = [f"d{i}" for i in range(1, 10)]  # d1..d9
    result = _FakeGateResult(
        _FakeBacktest(
            fills=(
                _Fill("d5", "A", True),
                _Fill("d7", "C", True),  # decided ON 2nd rebalance d6, fills d7
                _Fill("d9", "B", True),
            )
        )
    )
    rebs = ["d1", "d4", "d6", "d8"]
    stop_fills = [("d2", "A"), ("d2", "B"), ("d2", "C")]
    # Horizon for a d2 stop = 2nd later rebalance d6 PLUS its fill day d7.
    # A re-bought d5 → churn; C re-bought d7 (fill of a d6 decision) → churn
    # (codex P2 — the old rebalance-date cutoff missed it); B at d9 → not.
    assert churn_count(stop_fills, result, rebs, days) == 2  # type: ignore[arg-type]


def test_verdict_branches() -> None:
    base = dict(base_mdd=0.20, base_net=100.0)
    insufficient = verdict(
        stop_fill_count=MIN_STOP_FILLS - 1, stop_mdd=0.10, stop_net=95.0, **base
    )
    assert insufficient["verdict"] == "INSUFFICIENT_EVENTS"
    adopt = verdict(
        stop_fill_count=MIN_STOP_FILLS,
        stop_mdd=0.20 - MDD_IMPROVEMENT_MIN,
        stop_net=NET_RETENTION_MIN * 100.0,
        **base,
    )
    assert adopt["verdict"] == "ADOPT_SIGNAL"
    shallow = verdict(
        stop_fill_count=MIN_STOP_FILLS, stop_mdd=0.19, stop_net=95.0, **base
    )
    assert shallow["verdict"] == "NO_ADOPT"
    costly = verdict(
        stop_fill_count=MIN_STOP_FILLS, stop_mdd=0.10, stop_net=79.0, **base
    )
    assert costly["verdict"] == "NO_ADOPT"


def test_decide_fn_seam_defaults_to_decide_day() -> None:
    import inspect

    from backend.backtest.harness import run_backtest
    from backend.backtest.strategy import decide_day

    assert (
        inspect.signature(run_backtest).parameters["decide_fn"].default is decide_day
    )


def test_prereg_constants_frozen() -> None:
    assert MIN_STOP_FILLS == 15
    assert MDD_IMPROVEMENT_MIN == 0.02
    assert NET_RETENTION_MIN == 0.80
