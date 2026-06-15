"""AE-004 harness integration — event loop, barrier, oracles, invariants."""

from __future__ import annotations

import datetime as dt

from backend.backtest.harness import (
    BacktestSpec,
    run_backtest,
    to_acceptance_report,
)
from backend.backtest.invariants import InvariantVerdict
from backend.backtest.strategy import CodeHealth
from backend.services.acceptance_report import AcceptanceOutcome
from tests.backtest._builders import (
    BROKER_FRICTION,
    DailySignals,
    StaticBarSource,
    StaticScoreProvider,
    candidate,
    make_bar,
    make_strategy_config,
)

_DAYS = ("20260102", "20260105", "20260106", "20260107")


def _rising_inputs() -> tuple[StaticBarSource, StaticScoreProvider]:
    closes = {
        "20260102": 1_000,
        "20260105": 1_100,
        "20260106": 1_200,
        "20260107": 1_300,
    }
    opens = {"20260102": 1_000, "20260105": 1_000, "20260106": 1_100, "20260107": 1_200}
    bars = {
        day: {
            "600000": make_bar(
                "600000", day, open_cents=opens[day], close_cents=closes[day]
            )
        }
        for day in _DAYS
    }
    signals = {
        day: DailySignals(
            trade_date=day,
            quant_candidates=(candidate("600000", 0.9),),
            health={"600000": CodeHealth(line1_percentile=0.9, composite_score=0.9)},
        )
        for day in _DAYS
    }
    return StaticBarSource(bars), StaticScoreProvider(signals)


def _run(**kwargs):
    bar_source, provider = _rising_inputs()
    return run_backtest(
        spec=BacktestSpec(initial_capital_cents=900_000),
        bar_source=bar_source,
        provider=provider,
        strategy_config=make_strategy_config(),
        friction_params=BROKER_FRICTION,
        **kwargs,
    )


def test_rising_run_grows_equity_and_is_consistent() -> None:
    result = _run()
    assert result.trading_days == 4
    assert result.fill_count == 1
    assert result.invariant_report.verdict is InvariantVerdict.CONSISTENT
    assert result.final_equity_cents > result.initial_capital_cents
    assert result.pnl_cents > 0
    assert len(result.daily_returns) == 4
    assert result.signal_count >= 1
    assert 0.0 <= result.avg_exposure_ratio <= 1.0
    assert result.monthly_turnover >= 0.0


def test_zipline_barrier_fills_on_next_bar() -> None:
    result = _run()
    # decided on day0 (20260102), filled on day1 (20260105) — T+1 by construction.
    assert result.fills[0].trade_date == "20260105"
    assert result.fills[0].side_is_buy


def test_limit_up_blocks_buy_fill() -> None:
    days = ("20260102", "20260105")
    bars = {
        "20260102": {"600000": make_bar("600000", "20260102", open_cents=1_000)},
        # next bar opens AT the upper limit → a BUY cannot fill.
        "20260105": {
            "600000": make_bar(
                "600000", "20260105", open_cents=1_100, limit_up_cents=1_100
            )
        },
    }
    signals = {
        day: DailySignals(
            trade_date=day,
            quant_candidates=(candidate("600000", 0.9),),
            health={"600000": CodeHealth(line1_percentile=0.9, composite_score=0.9)},
        )
        for day in days
    }
    result = run_backtest(
        spec=BacktestSpec(initial_capital_cents=900_000),
        bar_source=StaticBarSource(bars),
        provider=StaticScoreProvider(signals),
        strategy_config=make_strategy_config(),
        friction_params=BROKER_FRICTION,
    )
    assert result.fill_count == 0


def test_adv_cap_partial_fill_harsh_or_equal() -> None:
    days = ("20260102", "20260105")
    bars = {
        day: {
            "600000": make_bar(
                "600000", day, open_cents=100, close_cents=100, adv_volume=100_000.0
            )
        }
        for day in days
    }
    signals = {
        day: DailySignals(
            trade_date=day,
            quant_candidates=(candidate("600000", 0.9),),
            health={"600000": CodeHealth(line1_percentile=0.9, composite_score=0.9)},
        )
        for day in days
    }
    result = run_backtest(
        spec=BacktestSpec(initial_capital_cents=1_000_000_000),  # huge → wants size
        bar_source=StaticBarSource(bars),
        provider=StaticScoreProvider(signals),
        strategy_config=make_strategy_config(),
        friction_params=BROKER_FRICTION,
    )
    # 5% of 100_000 ADV = 5_000 shares — the fill is capped far below request.
    assert result.fill_count == 1
    assert result.fills[0].volume == 5_000


def test_golden_vectors_match_and_diverge() -> None:
    baseline = _run()
    matched = _run(golden_vectors=baseline.decision_vectors)
    assert matched.golden_vector_result is not None
    assert matched.golden_vector_result.matched

    tampered = list(baseline.decision_vectors)
    from backend.backtest.golden_vector import DecisionVector

    tampered[0] = DecisionVector(
        trade_date=tampered[0].trade_date, shortlist=("999999",)
    )
    diverged = _run(golden_vectors=tampered)
    assert diverged.golden_vector_result is not None
    assert not diverged.golden_vector_result.matched


def test_determinism_bit_for_bit() -> None:
    a = _run()
    b = _run()
    assert a.equity_curve == b.equity_curve
    assert a.fills == b.fills
    assert a.decision_vectors == b.decision_vectors


def test_order_lapses_when_code_untradable_next_day() -> None:
    # decided on day0; day1 has no bar for the code → the order lapses (no fill,
    # never a stale-price fill).
    bars = {
        "20260102": {"600000": make_bar("600000", "20260102", open_cents=1_000)},
        "20260105": {},
    }
    signals = {
        day: DailySignals(
            trade_date=day,
            quant_candidates=(candidate("600000", 0.9),),
            health={"600000": CodeHealth(line1_percentile=0.9, composite_score=0.9)},
        )
        for day in ("20260102", "20260105")
    }
    result = run_backtest(
        spec=BacktestSpec(initial_capital_cents=900_000),
        bar_source=StaticBarSource(bars),
        provider=StaticScoreProvider(signals),
        strategy_config=make_strategy_config(),
        friction_params=BROKER_FRICTION,
    )
    assert result.fill_count == 0


def test_flat_run_when_nothing_affordable_is_consistent() -> None:
    # A share priced far above the whole account → no lot is affordable → no
    # orders, no fills; equity stays flat and the invariants hold.
    bars = {
        day: {"600000": make_bar("600000", day, open_cents=1_000_000_000)}
        for day in _DAYS
    }
    signals = {
        day: DailySignals(
            trade_date=day,
            quant_candidates=(candidate("600000", 0.9),),
            health={"600000": CodeHealth(line1_percentile=0.9, composite_score=0.9)},
        )
        for day in _DAYS
    }
    result = run_backtest(
        spec=BacktestSpec(initial_capital_cents=900_000),
        bar_source=StaticBarSource(bars),
        provider=StaticScoreProvider(signals),
        strategy_config=make_strategy_config(),
        friction_params=BROKER_FRICTION,
    )
    assert result.fill_count == 0
    assert result.signal_count == 0
    assert result.invariant_report.verdict is InvariantVerdict.CONSISTENT
    assert result.final_equity_cents == result.initial_capital_cents


def _two_day(open1: int, close1: int) -> tuple[StaticBarSource, StaticScoreProvider]:
    days = ("20260102", "20260105")
    bars = {
        "20260102": {
            "600000": make_bar(
                "600000", "20260102", open_cents=1_000, close_cents=1_000
            )
        },
        "20260105": {
            "600000": make_bar(
                "600000", "20260105", open_cents=open1, close_cents=close1
            )
        },
    }
    signals = {
        day: DailySignals(
            trade_date=day,
            quant_candidates=(candidate("600000", 0.9),),
            health={"600000": CodeHealth(line1_percentile=0.9, composite_score=0.9)},
        )
        for day in days
    }
    return StaticBarSource(bars), StaticScoreProvider(signals)


def test_gap_up_fill_does_not_false_trip_exposure_cap() -> None:
    # Sized at the decision-day close (within the 15% cap); the next bar gaps up
    # 100%. The exposure invariant must value the position at the decision close,
    # not the inflated fill, so the run stays CONSISTENT (regression: F2).
    bar_source, provider = _two_day(open1=2_000, close1=2_000)
    result = run_backtest(
        spec=BacktestSpec(initial_capital_cents=900_000),
        bar_source=bar_source,
        provider=provider,
        strategy_config=make_strategy_config(),
        friction_params=BROKER_FRICTION,
    )
    assert result.fill_count == 1
    assert result.invariant_report.verdict is InvariantVerdict.CONSISTENT


def test_unaffordable_gap_up_lapses_without_negative_cash() -> None:
    # The fill gaps up far beyond what the close-sized order can afford → the BUY
    # lapses (cash-only broker would reject); cash never goes negative (F3).
    bar_source, provider = _two_day(open1=100_000, close1=100_000)
    result = run_backtest(
        spec=BacktestSpec(initial_capital_cents=900_000),
        bar_source=bar_source,
        provider=provider,
        strategy_config=make_strategy_config(),
        friction_params=BROKER_FRICTION,
    )
    assert result.fill_count == 0
    assert all(s.cash_cents >= 0 for s in result.equity_curve)
    assert result.final_equity_cents == result.initial_capital_cents


def test_decision_vectors_carry_scores_and_diverge_on_tamper() -> None:
    result = _run()
    first = result.decision_vectors[0]
    assert first.scores.get("600000") == 0.9  # the quant score is threaded through

    from backend.backtest.golden_vector import DecisionVector, verify_decision_vectors

    tampered = list(result.decision_vectors)
    tampered[0] = DecisionVector(
        trade_date=first.trade_date,
        shortlist=first.shortlist,
        sell_codes=first.sell_codes,
        buy_codes=first.buy_codes,
        scores={"600000": 0.5},  # wrong score
    )
    assert not verify_decision_vectors(result.decision_vectors, tampered).matched


def test_to_acceptance_report_maps_strategy_metrics() -> None:
    result = _run()
    now = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    report = to_acceptance_report(result, now=now)
    by_name = {m.name: m.value for m in report.metrics}
    # The 45-day window ending at the run's last day (2026-01-07) is fully
    # populated on the static calendar, so a clean rising run PASSes; the three
    # strategy metrics must be threaded through from the BacktestResult.
    assert report.outcome is AcceptanceOutcome.PASS
    assert by_name["max_drawdown_pct"] == round(result.max_drawdown_pct, 6)
    assert by_name["pnl_cny"] == round(result.pnl_cents / 100.0, 6)
    assert by_name["pnl_cny"] > 0.0
