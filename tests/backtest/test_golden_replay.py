"""Tests for AE-003 golden replay (same-source proof, §2.5).

A recorded two-day scenario sized to the ¥9k budget; the replay must
reconstruct the live-recorded equity curve to the cent, and the closed-form
cash/position invariants must hold (codex J3).
"""

from __future__ import annotations

import pytest

from backend.backtest.golden_replay import (
    ConservationError,
    ReplayDay,
    ReplayEquityPoint,
    ReplayFill,
    ReplayPosition,
    assert_conservation,
    compare_to_golden,
    replay_equity_curve,
)

# Initial cash ¥9000.00 = 900000 分.
_INITIAL = 900_000


def _scenario() -> list[ReplayDay]:
    return [
        ReplayDay(
            trade_date="2026-06-01",
            fills=(
                ReplayFill(
                    code="000001",
                    side="BUY",
                    volume=200,
                    price_cents=1050,  # ¥10.50
                    cost_cents=500,  # ¥5.00 commission
                ),
            ),
            close_marks_cents={"000001": 1060},  # ¥10.60 close
        ),
        ReplayDay(
            trade_date="2026-06-02",
            fills=(
                ReplayFill(
                    code="000001",
                    side="SELL",
                    volume=200,
                    price_cents=1080,  # ¥10.80
                    cost_cents=500,
                ),
            ),
            close_marks_cents={},  # flat after the sell
        ),
    ]


def _recorded_golden() -> list[ReplayEquityPoint]:
    # Day1: cash 900000 − (200*1050 + 500) = 689500; MV 200*1060 = 212000.
    # Day2: cash 689500 + (200*1080 − 500) = 905000; MV 0.
    return [
        ReplayEquityPoint(
            trade_date="2026-06-01",
            cash_cents=689_500,
            market_value_cents=212_000,
            total_equity_cents=901_500,
            positions=(
                ReplayPosition(code="000001", volume=200, cost_cents=1052),
            ),
        ),
        ReplayEquityPoint(
            trade_date="2026-06-02",
            cash_cents=905_000,
            market_value_cents=0,
            total_equity_cents=905_000,
            positions=(),
        ),
    ]


def test_replay_matches_recorded_golden() -> None:
    replayed = replay_equity_curve(initial_cash_cents=_INITIAL, days=_scenario())
    result = compare_to_golden(replayed, _recorded_golden())
    assert result.matched, result.divergences


def test_divergent_record_is_flagged() -> None:
    replayed = replay_equity_curve(initial_cash_cents=_INITIAL, days=_scenario())
    bad = _recorded_golden()
    bad[1] = ReplayEquityPoint(
        trade_date="2026-06-02",
        cash_cents=905_001,  # off by one 分
        market_value_cents=0,
        total_equity_cents=905_001,
        positions=(),
    )
    result = compare_to_golden(replayed, bad)
    assert not result.matched
    assert any(d.field_name == "cash_cents" for d in result.divergences)


def test_swapped_holding_with_equal_market_value_flagged() -> None:
    # A same-source proof must catch a holding swap even when the aggregate
    # market value is identical (codex AE-003 P2).
    replayed = replay_equity_curve(initial_cash_cents=_INITIAL, days=_scenario())
    bad = _recorded_golden()
    bad[0] = ReplayEquityPoint(
        trade_date="2026-06-01",
        cash_cents=689_500,
        market_value_cents=212_000,  # same MV
        total_equity_cents=901_500,
        positions=(
            ReplayPosition(code="600519", volume=200, cost_cents=1052),  # diff code
        ),
    )
    result = compare_to_golden(replayed, bad)
    assert not result.matched
    assert any(d.field_name == "positions" for d in result.divergences)


def test_date_misalignment_flagged() -> None:
    replayed = replay_equity_curve(initial_cash_cents=_INITIAL, days=_scenario())
    bad = _recorded_golden()
    bad[0] = ReplayEquityPoint(
        trade_date="2025-12-31",  # wrong date, same aggregates
        cash_cents=689_500,
        market_value_cents=212_000,
        total_equity_cents=901_500,
        positions=(ReplayPosition(code="000001", volume=200, cost_cents=1052),),
    )
    result = compare_to_golden(replayed, bad)
    assert not result.matched
    assert any(d.field_name == "trade_date" for d in result.divergences)


def test_length_mismatch_flagged() -> None:
    replayed = replay_equity_curve(initial_cash_cents=_INITIAL, days=_scenario())
    result = compare_to_golden(replayed, _recorded_golden()[:1])
    assert not result.matched
    assert result.divergences[0].field_name == "length"


def test_cash_conservation_holds() -> None:
    replayed = replay_equity_curve(initial_cash_cents=_INITIAL, days=_scenario())
    assert_conservation(
        initial_cash_cents=_INITIAL,
        days=_scenario(),
        final_cash_cents=replayed[-1].cash_cents,
    )


def test_cash_conservation_violation_raises() -> None:
    with pytest.raises(ConservationError, match="conservation"):
        assert_conservation(
            initial_cash_cents=_INITIAL,
            days=_scenario(),
            final_cash_cents=999_999,  # wrong final cash
        )


def test_oversell_fails_closed() -> None:
    days = [
        ReplayDay(
            trade_date="2026-06-01",
            fills=(
                ReplayFill(code="000001", side="SELL", volume=100, price_cents=1000),
            ),
        )
    ]
    with pytest.raises(ConservationError, match="exceeds held"):
        replay_equity_curve(initial_cash_cents=_INITIAL, days=days)


def test_missing_close_mark_fails_closed() -> None:
    days = [
        ReplayDay(
            trade_date="2026-06-01",
            fills=(
                ReplayFill(
                    code="000001", side="BUY", volume=200, price_cents=1050
                ),
            ),
            close_marks_cents={},  # held but no mark
        )
    ]
    with pytest.raises(ConservationError, match="no closing mark"):
        replay_equity_curve(initial_cash_cents=_INITIAL, days=days)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": "000001", "side": "BUY", "volume": 0, "price_cents": 1000},
        {"code": "000001", "side": "BUY", "volume": -5, "price_cents": 1000},
        {"code": "000001", "side": "BUY", "volume": 100, "price_cents": 0},
        {"code": "000001", "side": "BUY", "volume": 100, "price_cents": -1},
        {
            "code": "000001",
            "side": "BUY",
            "volume": 100,
            "price_cents": 1000,
            "cost_cents": -1,
        },
        {"code": "000001", "side": "HOLD", "volume": 100, "price_cents": 1000},
    ],
)
def test_malformed_fill_rejected_at_construction(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ReplayFill(**kwargs)  # type: ignore[arg-type]


def test_duplicate_position_code_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate position code"):
        ReplayEquityPoint(
            trade_date="2026-06-01",
            cash_cents=0,
            market_value_cents=0,
            total_equity_cents=0,
            positions=(
                ReplayPosition(code="000001", volume=50, cost_cents=1000),
                ReplayPosition(code="000001", volume=100, cost_cents=1000),
            ),
        )


def test_opening_positions_replayed() -> None:
    days = [
        ReplayDay(
            trade_date="2026-06-01",
            fills=(),
            close_marks_cents={"600519": 170_000},  # ¥1700.00
        )
    ]
    replayed = replay_equity_curve(
        initial_cash_cents=100_000,
        opening_positions=(
            ReplayPosition(code="600519", volume=10, cost_cents=160_000),
        ),
        days=days,
    )
    assert replayed[0].market_value_cents == 10 * 170_000
    assert replayed[0].total_equity_cents == 100_000 + 1_700_000
