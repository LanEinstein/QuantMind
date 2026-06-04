"""Unit tests for the per-stock drawdown calibration (D1-a).

P0-7-amendment-2026-06-03-adaptive-intraday-thresholds: the intraday
DRAWDOWN_STOP threshold is derived from the stock's |daily return| percentile,
clamped to ``[floor, ceiling]``. Pure + deterministic.
"""

from __future__ import annotations

import math

import pytest

from backend.monitoring.intraday_calibration import (
    DrawdownCalibrationConfig,
    TakeProfitCalibrationConfig,
    derive_drawdown_threshold,
    effective_r_multiple,
)


def _alternating(low: float, high: float, n: int = 70) -> tuple[float, ...]:
    return tuple(low if i % 2 else high for i in range(n))


def test_volatile_stock_widens_threshold() -> None:
    # ~5% daily moves → 90th pct |return| ≈ 0.05 × 1.5 = 0.075 (between bounds).
    out = derive_drawdown_threshold(_alternating(8.0, 8.4))
    assert out is not None
    assert 0.07 <= out <= 0.08


def test_calm_stock_clamps_to_floor() -> None:
    # ~0.25% daily moves → 0.0025 × 1.5 = 0.00375 → clamped up to the 3% floor.
    out = derive_drawdown_threshold(_alternating(8.00, 8.02))
    assert out == pytest.approx(0.03)


def test_extreme_stock_clamps_to_ceiling() -> None:
    # ~20% daily moves → 0.2 × 1.5 = 0.3 → clamped down to the 12% ceiling.
    out = derive_drawdown_threshold(_alternating(10.0, 12.0))
    assert out == pytest.approx(0.12)


def test_insufficient_history_returns_none() -> None:
    # < min_history + 1 closes → None (caller falls back to the static default).
    assert derive_drawdown_threshold(_alternating(8.0, 8.4, n=20)) is None


def test_non_finite_and_non_positive_closes_filtered() -> None:
    # NaN / 0 / negative closes are dropped; if too few clean closes remain → None.
    dirty = (10.0, float("nan"), 0.0, -5.0, 10.2)
    assert derive_drawdown_threshold(dirty) is None


def test_deterministic() -> None:
    series = _alternating(8.0, 8.4)
    assert derive_drawdown_threshold(series) == derive_drawdown_threshold(series)


def test_window_truncates_to_recent_history() -> None:
    # A long calm tail then a volatile recent window: only the recent window
    # (cfg.window) drives the threshold → reflects current volatility.
    calm = list(_alternating(8.00, 8.02, n=200))
    cfg = DrawdownCalibrationConfig(window=60)
    out = derive_drawdown_threshold(tuple(calm), cfg)
    assert out == pytest.approx(0.03)  # calm tail → floor


def test_custom_config_changes_result() -> None:
    series = _alternating(8.0, 8.4)
    tight = DrawdownCalibrationConfig(multiplier=1.0)
    wide = DrawdownCalibrationConfig(multiplier=2.0)
    t = derive_drawdown_threshold(series, tight)
    w = derive_drawdown_threshold(series, wide)
    assert t is not None and w is not None
    assert w > t  # a larger multiplier → wider threshold


def test_all_flat_closes_floor() -> None:
    # Zero volatility → percentile 0 → clamped to the floor (never 0 threshold).
    flat = tuple(10.0 for _ in range(70))
    out = derive_drawdown_threshold(flat)
    assert out == pytest.approx(0.03)
    assert out is not None and math.isfinite(out)


def test_bear_regime_tightens_threshold() -> None:
    # D1-b: a BEAR regime tightens the threshold by bear_multiplier (0.8).
    series = _alternating(8.0, 8.4)  # base ≈ 0.075 (well within bounds)
    base = derive_drawdown_threshold(series)
    bear = derive_drawdown_threshold(series, is_bear=True)
    assert base is not None and bear is not None
    assert bear < base
    assert bear == pytest.approx(base * 0.8)


def test_bear_tightening_clamped_to_floor() -> None:
    # Tightening must not push the stop below the floor (still a real stop).
    series = _alternating(8.00, 8.05)  # tiny vol → base already near the floor
    out = derive_drawdown_threshold(series, is_bear=True)
    assert out == pytest.approx(0.03)


def test_is_bear_false_reproduces_base() -> None:
    series = _alternating(8.0, 8.4)
    assert derive_drawdown_threshold(
        series, is_bear=False
    ) == derive_drawdown_threshold(series)


# ---------------------------------------------------------------------------
# D1-c — regime-conditioned take-profit multiple
# (P0-7-amendment-2026-06-04-regime-conditioned-takeprofit)
# ---------------------------------------------------------------------------


def test_effective_r_multiple_three_tiers() -> None:
    cfg = TakeProfitCalibrationConfig()
    assert effective_r_multiple(cfg, is_bull=True, is_bear=False) == 1.3
    assert effective_r_multiple(cfg, is_bull=False, is_bear=False) == 1.0
    assert effective_r_multiple(cfg, is_bull=False, is_bear=True) == 0.6


def test_effective_r_multiple_clamps_floor_and_ceiling() -> None:
    # The clamp guards a (mis)recalibration: never take profit earlier than
    # +floor·R (noise churn) nor push the target past +ceiling·R (a target so
    # far the take-profit never fires).
    cfg = TakeProfitCalibrationConfig(bear_r_multiple=0.1, bull_r_multiple=5.0)
    assert effective_r_multiple(cfg, is_bull=False, is_bear=True) == 0.5
    assert effective_r_multiple(cfg, is_bull=True, is_bear=False) == 2.0


def test_effective_r_multiple_bear_wins_over_bull() -> None:
    # Defensive precedence: classify_regime can never emit both, but if a
    # caller ever passes both flags the conservative (earlier-lock) tier wins.
    cfg = TakeProfitCalibrationConfig()
    assert effective_r_multiple(cfg, is_bull=True, is_bear=True) == 0.6


def test_effective_r_multiple_deterministic() -> None:
    cfg = TakeProfitCalibrationConfig()
    assert effective_r_multiple(cfg, is_bull=False, is_bear=True) == (
        effective_r_multiple(cfg, is_bull=False, is_bear=True)
    )


def test_takeprofit_calibration_config_frozen() -> None:
    # Runtime-immutable meta-parameters (recalibrated only offline, P2-2).
    import dataclasses

    cfg = TakeProfitCalibrationConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.bear_r_multiple = 0.9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# E1 — derive_entry_anchored_stop (P0-7-amendment-2026-06-04)
# ---------------------------------------------------------------------------


def test_entry_anchored_fresh_entry_initial_governs() -> None:
    from backend.monitoring.intraday_calibration import (
        ChandelierConfig,
        derive_entry_anchored_stop,
    )

    out = derive_entry_anchored_stop(
        (), cost=10.0, atr=0.5, config=ChandelierConfig()
    )
    assert out is not None
    assert out.anchor == 10.0  # no closes since entry → anchor = cost
    assert out.initial_stop == 9.0  # 10 − 2×0.5
    assert out.chandelier_stop == 8.5  # 10 − 3×0.5
    assert out.governing == "initial"
    assert out.stop_level == 9.0


def test_entry_anchored_chandelier_takes_over_after_rally() -> None:
    from backend.monitoring.intraday_calibration import (
        ChandelierConfig,
        derive_entry_anchored_stop,
    )

    out = derive_entry_anchored_stop(
        (10.5, 12.0, 11.2), cost=10.0, atr=0.5, config=ChandelierConfig()
    )
    assert out is not None
    assert out.anchor == 12.0
    assert out.chandelier_stop == 10.5  # 12 − 1.5 > initial 9.0
    assert out.governing == "chandelier"
    assert out.stop_level == 10.5


def test_entry_anchored_anchor_floored_at_cost() -> None:
    from backend.monitoring.intraday_calibration import (
        ChandelierConfig,
        derive_entry_anchored_stop,
    )

    # Closes all below cost (a position that only fell) → anchor = cost,
    # never below the entry itself.
    out = derive_entry_anchored_stop(
        (9.5, 9.2), cost=10.0, atr=0.5, config=ChandelierConfig()
    )
    assert out is not None
    assert out.anchor == 10.0
    assert out.governing == "initial"


def test_entry_anchored_filters_non_finite_and_rejects_bad_inputs() -> None:
    from backend.monitoring.intraday_calibration import (
        ChandelierConfig,
        derive_entry_anchored_stop,
    )

    cfg = ChandelierConfig()
    out = derive_entry_anchored_stop(
        (float("nan"), float("inf"), -3.0, 11.0), cost=10.0, atr=0.5, config=cfg
    )
    assert out is not None
    assert out.anchor == 11.0  # only the finite positive close survives
    assert derive_entry_anchored_stop((), cost=0.0, atr=0.5, config=cfg) is None
    assert derive_entry_anchored_stop((), cost=10.0, atr=0.0, config=cfg) is None
    assert (
        derive_entry_anchored_stop((), cost=10.0, atr=float("nan"), config=cfg)
        is None
    )
