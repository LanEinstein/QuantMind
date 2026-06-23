"""AF-006 做T overlay — bounded, floor-protected, T+1, env-OFF byte-identical."""

from __future__ import annotations

import pytest

from backend.value_swing.swing_overlay import (
    SwingConfig,
    SwingPosition,
    evaluate_swing,
)


def _cfg(**kw: object) -> SwingConfig:
    base = dict(
        enabled=True,
        base_floor_fraction=0.60,
        max_swing_fraction=0.40,
        sell_premium=0.05,
        buy_discount=0.05,
        lot_size=100,
        max_round_trips_per_day=1,
    )
    base.update(kw)
    return SwingConfig(**base)  # type: ignore[arg-type]


def _pos(**kw: object) -> SwingPosition:
    base = dict(
        code="600519.SH",
        total_volume=1000,
        available_volume=1000,
        target_volume=1000,
        reference_price=10.0,
        last_price=10.0,
        round_trips_done_today=0,
    )
    base.update(kw)
    return SwingPosition(**base)  # type: ignore[arg-type]


# ---- env-OFF default = byte-identical pure hold ---------------------------


def test_disabled_returns_none() -> None:
    # Even with a screaming SELL signal, a disabled overlay never acts.
    out = evaluate_swing(_pos(last_price=20.0), SwingConfig())  # enabled=False default
    assert out is None


# ---- SELL-high (above band) ----------------------------------------------


def test_sell_above_band_trims_swing_tranche() -> None:
    out = evaluate_swing(_pos(last_price=11.0), _cfg())  # 11 ≥ 10*(1.05)=10.5
    assert out is not None
    assert out.side == "sell"
    assert out.volume == 400  # full swing band (40% of 1000)
    assert out.limit_price == 11.0


def test_sell_never_breaks_base_floor() -> None:
    # Only 700 held, floor is 600 → at most 100 may be trimmed (leaves the floor).
    out = evaluate_swing(
        _pos(total_volume=700, available_volume=700, last_price=11.0), _cfg()
    )
    assert out is not None and out.side == "sell"
    assert out.volume == 100
    assert 700 - out.volume == 600  # floor intact


def test_sell_only_settled_shares_t_plus_1() -> None:
    # 1000 held but only 200 settled (rest bought today) → sell ≤ 200 (T+1).
    out = evaluate_swing(
        _pos(total_volume=1000, available_volume=200, last_price=11.0), _cfg()
    )
    assert out is not None and out.side == "sell"
    assert out.volume == 200


def test_sell_floored_to_lot() -> None:
    # available 250 settled, floor leaves room for 400, band 400 → min=250 → 200 (lot).
    out = evaluate_swing(
        _pos(total_volume=1000, available_volume=250, last_price=11.0), _cfg()
    )
    assert out is not None and out.volume == 200  # 250 floored to whole lots


def test_sell_blocked_when_at_floor() -> None:
    # Held == floor (600) → no swing capacity to trim → None.
    out = evaluate_swing(
        _pos(total_volume=600, available_volume=600, last_price=11.0), _cfg()
    )
    assert out is None


# ---- BUY-low (below band) -------------------------------------------------


def test_buy_below_band_rebuilds_toward_core() -> None:
    # Previously trimmed to 600; price below band → rebuy toward target (≤ band).
    out = evaluate_swing(
        _pos(total_volume=600, available_volume=600, last_price=9.0), _cfg()
    )  # 9 ≤ 10*(0.95)=9.5
    assert out is not None and out.side == "buy"
    assert out.volume == 400  # min(band 400, target-total 400)
    assert out.limit_price == 9.0


def test_buy_none_when_already_at_target() -> None:
    out = evaluate_swing(_pos(total_volume=1000, last_price=9.0), _cfg())
    assert out is None  # no room below target → nothing to rebuy


# ---- in-band / round-trip / guards ---------------------------------------


def test_in_band_returns_none() -> None:
    assert evaluate_swing(_pos(last_price=10.2), _cfg()) is None  # within ±5%


def test_round_trip_budget_exhausted() -> None:
    out = evaluate_swing(
        _pos(last_price=11.0, round_trips_done_today=1),
        _cfg(max_round_trips_per_day=1),
    )
    assert out is None


def test_dirty_inputs_fail_closed() -> None:
    assert evaluate_swing(_pos(reference_price=0.0, last_price=11.0), _cfg()) is None
    assert evaluate_swing(_pos(target_volume=0, last_price=11.0), _cfg()) is None


def test_malformed_state_fail_closed() -> None:
    # codex AF-006 P1/P2: a non-finite/negative/fractional volume or counter must
    # never slip a tranche through or bypass the round-trip cap.
    inf = float("inf")
    assert evaluate_swing(_pos(total_volume=inf, last_price=11.0), _cfg()) is None  # type: ignore[arg-type]
    assert evaluate_swing(_pos(available_volume=inf, last_price=11.0), _cfg()) is None  # type: ignore[arg-type]
    assert evaluate_swing(_pos(target_volume=inf, last_price=11.0), _cfg()) is None  # type: ignore[arg-type]
    assert evaluate_swing(_pos(total_volume=950.5, last_price=11.0), _cfg()) is None  # type: ignore[arg-type]
    # A negative counter must not bypass a zero round-trip budget.
    assert (
        evaluate_swing(
            _pos(last_price=11.0, round_trips_done_today=-1),
            _cfg(max_round_trips_per_day=0),
        )
        is None
    )


def test_tiny_target_no_lot_none() -> None:
    # target 100, swing 40% = 40 < lot 100 → no whole-lot swing possible.
    out = evaluate_swing(
        _pos(
            total_volume=100, available_volume=100, target_volume=100, last_price=11.0
        ),
        _cfg(),
    )
    assert out is None


def test_deterministic_replay() -> None:
    p, c = _pos(last_price=11.0), _cfg()
    assert evaluate_swing(p, c) == evaluate_swing(p, c)


# ---- config validation ----------------------------------------------------


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        _cfg(base_floor_fraction=0.7, max_swing_fraction=0.4)  # sum > 1
    with pytest.raises(ValueError):
        _cfg(base_floor_fraction=1.5)
    with pytest.raises(ValueError):
        _cfg(sell_premium=-0.1)
    with pytest.raises(ValueError):
        _cfg(lot_size=0)
    with pytest.raises(ValueError):
        _cfg(max_round_trips_per_day=-1)
    with pytest.raises(ValueError):
        SwingConfig(enabled="yes")  # type: ignore[arg-type]
