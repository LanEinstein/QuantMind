import numpy as np

from scripts.yeren_research.m3_520_executability_audit import (
    ExecutionBar,
    _bar,
    _blocked,
    audit_bars,
)
from scripts.yeren_research.pit_priced_panel import PricedSeries


def _series(dates):
    length = len(dates)
    return PricedSeries(
        code="000001.SZ",
        dates=np.asarray(dates, dtype=np.int32),
        opens=np.full(length, 10.0),
        closes=np.full(length, 10.0),
        pct_chg=np.zeros(length),
        adj=np.ones(length),
    )


def _exec_bar(side, open_price, gap=0):
    return ExecutionBar(
        code="000001.SZ",
        trade_date=20230104,
        side=side,
        open_price=open_price,
        calendar_gap=gap,
    )


def test_entry_at_the_up_limit_open_is_reported_as_unreachable():
    limits = {"up_limit": 11.0, "down_limit": 9.0}

    assert _blocked(_exec_bar("entry", 11.0), limits) == "open_at_up_limit"
    assert _blocked(_exec_bar("entry", 10.5), limits) is None


def test_exit_at_the_down_limit_open_is_reported_as_unreachable():
    limits = {"up_limit": 11.0, "down_limit": 9.0}

    assert _blocked(_exec_bar("exit", 9.0), limits) == "open_at_down_limit"
    assert _blocked(_exec_bar("exit", 9.5), limits) is None


def test_a_down_limit_never_blocks_a_purchase():
    limits = {"up_limit": 11.0, "down_limit": 9.0}

    assert _blocked(_exec_bar("entry", 9.0), limits) is None


def test_the_no_limit_sentinel_is_not_read_as_a_limit_hit():
    # Tushare writes an out-of-range sentinel when no limit applies that day.
    limits = {"up_limit": 99999.999, "down_limit": 0.01}

    assert _blocked(_exec_bar("entry", 99999.999), limits) is None
    assert _blocked(_exec_bar("exit", 0.01), limits) is None


def test_calendar_gap_counts_trading_days_the_security_did_not_trade():
    calendar_index = {20230103: 0, 20230104: 1, 20230105: 2, 20230106: 3}
    series = _series([20230103, 20230106])

    assert _bar(series, 1, "entry", calendar_index).calendar_gap == 2
    assert _bar(series, 0, "entry", calendar_index).calendar_gap == 0


def test_audit_bars_counts_missing_facts_halts_and_delays():
    bars = (
        _exec_bar("entry", 11.0),
        _exec_bar("entry", 10.0, gap=3),
        _exec_bar("exit", 9.0),
    )
    constraints = {
        ("000001.SZ", 20230104): {
            "up_limit": 11.0,
            "down_limit": 9.0,
            "suspend_timing": "9:36-9:46",
        }
    }

    result = audit_bars(bars, constraints)

    assert result["entry"]["bars"] == 2
    assert result["entry"]["open_at_up_limit"] == 1
    assert result["entry"]["intraday_halt_on_fill_day"] == 2
    assert result["entry"]["fill_delayed_by_missing_days"] == 1
    assert result["entry"]["max_calendar_gap"] == 3
    assert result["exit"]["open_at_down_limit"] == 1


def test_audit_bars_flags_bars_with_no_stored_limit_row():
    result = audit_bars((_exec_bar("entry", 10.0),), {})

    assert result["entry"]["no_limit_row"] == 1


def test_a_signal_on_the_last_stored_bar_is_counted_as_never_fillable():
    from scripts.yeren_research.m3_520 import RuleSpec
    from scripts.yeren_research.m3_520_executability_audit import (
        collect_execution_bars,
    )

    # A falling series that turns up on its very last bar: the entry signal
    # fires with no next open in existence.
    closes = np.asarray(
        [30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 16, 15]
        + [14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1, 1, 1, 1, 9],
        dtype=float,
    )
    dates = np.arange(20230101, 20230101 + len(closes), dtype=np.int32)
    series = PricedSeries(
        code="000001.SZ",
        dates=dates,
        opens=closes.copy(),
        closes=closes,
        pct_chg=np.zeros(len(closes)),
        adj=np.ones(len(closes)),
    )
    calendar_index = {int(day): pos for pos, day in enumerate(dates)}

    _bars, unfillable = collect_execution_bars(
        (series,),
        spec=RuleSpec(stop_days=3),
        start_date=int(dates[0]),
        end_date=int(dates[-1]),
        calendar_index=calendar_index,
        excluded=frozenset(),
    )

    assert unfillable["entry_signals_without_any_next_bar"] == 1


def test_an_exit_signal_on_the_last_stored_bar_is_counted_as_never_fillable():
    from scripts.yeren_research.m3_520 import RuleSpec
    from scripts.yeren_research.m3_520_executability_audit import (
        collect_execution_bars,
    )

    # Falls for thirty bars, bottoms out, rises into an entry, then collapses on
    # the final bar: the exit signal fires with no next open in existence.
    closes = np.asarray(
        [40 - step for step in range(30)]
        + [10.0, 10.0, 10.0, 10.8, 11.6, 12.4, 13.2, 1.0],
        dtype=float,
    )
    dates = np.arange(20230101, 20230101 + len(closes), dtype=np.int32)
    series = PricedSeries(
        code="000001.SZ",
        dates=dates,
        opens=closes.copy(),
        closes=closes,
        pct_chg=np.zeros(len(closes)),
        adj=np.ones(len(closes)),
    )

    bars, unfillable = collect_execution_bars(
        (series,),
        spec=RuleSpec(stop_days=3),
        start_date=int(dates[0]),
        end_date=int(dates[-1]),
        calendar_index={int(day): pos for pos, day in enumerate(dates)},
        excluded=frozenset(),
    )

    assert unfillable["entry_signals_without_any_next_bar"] == 0
    assert unfillable["trades_without_exit_execution_bar"] == 1
    assert unfillable["exit_signals_on_the_final_stored_bar"] == 1
    assert [bar.side for bar in bars] == ["entry"]
