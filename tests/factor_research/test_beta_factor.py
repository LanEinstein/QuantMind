"""Unit tests for :mod:`scripts.factor_research.beta_factor` (rolling / tail beta)."""

from __future__ import annotations

import math
from pathlib import Path

from scripts.factor_research import beta_factor as bf


def _closes_from_returns(returns: list[float], start: float = 100.0) -> list[float]:
    """Reconstruct a close series (most-recent last) from daily simple returns."""
    closes = [start]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    return closes


def test_beta_of_exact_multiple_is_the_multiple() -> None:
    # Stock returns = 2x market returns each day → beta exactly 2.0.
    rng = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01] * 10  # 60 days, > min_obs
    market = _closes_from_returns(rng)
    stock = _closes_from_returns([2.0 * r for r in rng])
    beta = bf.market_beta(stock, market, window=60, min_obs=40)
    assert beta is not None
    assert math.isclose(beta, 2.0, rel_tol=1e-9)


def test_beta_zero_when_stock_flat() -> None:
    rng = [0.01, -0.02, 0.015, -0.005] * 15
    market = _closes_from_returns(rng)
    stock = [100.0] * len(market)  # perfectly flat → zero covariance
    beta = bf.market_beta(stock, market, window=50, min_obs=30)
    assert beta is not None
    assert math.isclose(beta, 0.0, abs_tol=1e-9)


def test_none_when_too_few_obs() -> None:
    market = _closes_from_returns([0.01, -0.01, 0.02])  # only 3 returns
    stock = _closes_from_returns([0.02, -0.02, 0.04])
    assert bf.market_beta(stock, market, window=60, min_obs=40) is None


def test_none_when_market_variance_degenerate() -> None:
    market = [100.0] * 61  # flat market → zero variance
    stock = _closes_from_returns([0.01, -0.01] * 30)
    assert bf.market_beta(stock, market, min_obs=40) is None


def test_trailing_window_only_uses_recent_bars() -> None:
    # Old half: beta 3; recent 60 bars: beta 1. A 60-window must recover ~1.
    old = [0.01, -0.02, 0.03, -0.01] * 20  # 80 days beta-3 region
    recent = [0.008, -0.012, 0.02, -0.006] * 20  # 80 days beta-1 region
    market = _closes_from_returns(old + recent)
    stock_r = [3.0 * r for r in old] + [1.0 * r for r in recent]
    stock = _closes_from_returns(stock_r)
    beta = bf.market_beta(stock, market, window=60, min_obs=40)
    assert beta is not None
    assert math.isclose(beta, 1.0, rel_tol=1e-6)


def test_non_finite_bars_are_dropped_not_masked() -> None:
    rng = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01] * 10
    market = _closes_from_returns(rng)
    stock = _closes_from_returns([2.0 * r for r in rng])
    stock[5] = math.nan  # a corrupt bar mid-series → its two pairs drop
    beta = bf.market_beta(stock, market, window=60, min_obs=30)
    assert beta is not None
    assert math.isclose(beta, 2.0, rel_tol=1e-6)


def test_tail_beta_restricts_to_down_tail_days() -> None:
    # Market return spans −0.05..+0.05 monotonically; on down days (m<0) the stock
    # moves 3x, on up days 1x. The bottom-30% quantile is all down days (with
    # variance), so tail_beta must recover ≈ 3.0 while full beta is a 1/3 blend.
    market_r = [-0.05 + 0.10 * i / 79 for i in range(80)]
    stock_r = [(3.0 * m if m < 0 else 1.0 * m) for m in market_r]
    market = _closes_from_returns(market_r)
    stock = _closes_from_returns(stock_r)
    tb = bf.tail_beta(stock, market, window=80, tail_quantile=0.30, min_obs=12)
    assert tb is not None
    assert math.isclose(tb, 3.0, rel_tol=1e-6)
    # The full-window beta blends the up (1x) and down (3x) regimes → strictly < 3.
    full = bf.market_beta(stock, market, window=80, min_obs=40)
    assert full is not None
    assert full < tb


def test_tail_beta_none_when_insufficient_tail() -> None:
    rng = [0.01] * 60  # no down days at all → empty tail
    market = _closes_from_returns(rng)
    stock = _closes_from_returns([0.02] * 60)
    assert bf.tail_beta(stock, market, window=60, min_obs=12) is None


def test_near_degenerate_market_variance_fails_closed() -> None:
    # Market moves by a tiny 1e-9 each day (var ~1e-18, below the floor) → None,
    # not an arbitrary roundoff beta (codex R1 P1).
    market = _closes_from_returns([1e-9, -1e-9] * 30)
    stock = _closes_from_returns([0.01, -0.01] * 30)
    assert bf.market_beta(stock, market, min_obs=40) is None


def test_corrupt_current_close_pair_is_dropped() -> None:
    # A zero/negative current close must drop that pair, never fabricate a return
    # (codex R1 P1 — guard current closes, not only the base).
    rng = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01] * 10
    market = _closes_from_returns(rng)
    stock = _closes_from_returns([2.0 * r for r in rng])
    stock[7] = 0.0  # corrupt current close → its adjacent pairs drop
    stock[8] = -5.0  # negative close → dropped
    beta = bf.market_beta(stock, market, window=60, min_obs=30)
    assert beta is not None
    assert math.isclose(beta, 2.0, rel_tol=1e-6)


def test_module_is_pure_no_backend_import() -> None:
    source = Path(bf.__file__).read_text(encoding="utf-8")
    assert "import backend" not in source
    assert "from backend" not in source
