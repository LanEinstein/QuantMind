"""Alpha158-subset technical factors — pure functions on a price/amount series.

Phase L-002. These are deterministic, side-effect-free functions over a
per-code daily series (oldest → newest). They are computed *on the K
snapshot bytes* so a replay reproduces the exact factor vector
(``backend/marketdata_snapshot`` PIT contract). No LLM, no IO, no
``backend.{llm,agents,mirofish}`` import.

We deliberately implement a small, well-understood Alpha158 subset in the
standard library rather than pulling in qlib/vectorbt: the inputs here
are a single code's close + amount lists, the maths is elementary, and a
hand-rolled pure implementation is the most reproducible (bit-stable
across environments) substrate for the PIT replay requirement. The
cross-sectional ranking that turns these per-code vectors into a single
score lives in :mod:`backend.screening.screener` (it needs the whole
surviving universe).

Every function returns ``None`` when there is insufficient history rather
than guessing — the screener treats a ``None`` liquidity/price as a
fail-closed exclusion, never an optimistic default.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# Default windows (locked constants; mirror the exclusion-rule horizon).
MOMENTUM_WINDOW: int = 20
MA_SHORT_WINDOW: int = 5
MA_LONG_WINDOW: int = 20
VOLATILITY_WINDOW: int = 20
RSI_WINDOW: int = 14
LIQUIDITY_WINDOW: int = 20


def _returns(closes: list[float]) -> list[float]:
    """Simple daily returns; ``len(returns) == len(closes) - 1``.

    A non-positive prior close yields a 0.0 return for that step rather
    than dividing by zero (a halted / corrupt bar should not blow up the
    whole screen — the row is still subject to fail-closed exclusion
    upstream on missing price).
    """
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:], strict=False):
        out.append((cur / prev - 1.0) if prev > 0 else 0.0)
    return out


def momentum(closes: list[float], window: int = MOMENTUM_WINDOW) -> float | None:
    """Trailing ``window``-day return ``close[-1] / close[-1-window] - 1``."""
    if len(closes) <= window:
        return None
    base = closes[-1 - window]
    if base <= 0:
        return None
    return closes[-1] / base - 1.0


def moving_average(values: list[float], window: int) -> float | None:
    """Mean of the trailing ``window`` values (``None`` if too short)."""
    if window <= 0 or len(values) < window:
        return None
    return statistics.fmean(values[-window:])


def ma_ratio(
    closes: list[float],
    short: int = MA_SHORT_WINDOW,
    long: int = MA_LONG_WINDOW,
) -> float | None:
    """Short MA / long MA — a trend-strength factor (``>1`` = uptrend)."""
    ma_short = moving_average(closes, short)
    ma_long = moving_average(closes, long)
    if ma_short is None or ma_long is None or ma_long <= 0:
        return None
    return ma_short / ma_long


def volatility(closes: list[float], window: int = VOLATILITY_WINDOW) -> float | None:
    """Population stdev of trailing ``window`` daily returns (risk factor)."""
    rets = _returns(closes)
    if len(rets) < window:
        return None
    return statistics.pstdev(rets[-window:])


def rsi(closes: list[float], window: int = RSI_WINDOW) -> float | None:
    """Wilder-style RSI over ``window`` days, scaled 0–100.

    Returns ``None`` when there is less than ``window`` daily moves. A
    zero average loss (only gains in the window) yields 100.0.
    """
    rets = _returns(closes)
    if len(rets) < window:
        return None
    window_rets = rets[-window:]
    gains = [r for r in window_rets if r > 0]
    losses = [-r for r in window_rets if r < 0]
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def avg_amount(amounts: list[float], window: int = LIQUIDITY_WINDOW) -> float | None:
    """Mean trailing ``window``-day traded amount (¥). ``None`` if too short.

    This doubles as the liquidity exclusion input: fewer than ``window``
    bars → ``None`` → fail-closed exclusion (no optimistic fallback).
    """
    if len(amounts) < window:
        return None
    return statistics.fmean(amounts[-window:])


@dataclass(frozen=True)
class FactorVector:
    """The per-code Alpha158-subset factor vector (all ``None`` if N/A).

    Frozen so a computed vector cannot be mutated after the screen reads
    it. ``avg_amount_20d`` is reused by the screener as the liquidity
    exclusion input (single source of truth — computed once, here).
    """

    momentum_20d: float | None
    ma_ratio_5_20: float | None
    volatility_20d: float | None
    rsi_14: float | None
    avg_amount_20d: float | None


def compute_factors(closes: list[float], amounts: list[float]) -> FactorVector:
    """Compute the Alpha158-subset vector from a code's close + amount series.

    Pure: identical inputs always yield an identical vector (PIT-replay
    reproducible). Insufficient history surfaces as ``None`` fields, not
    fabricated values.
    """
    return FactorVector(
        momentum_20d=momentum(closes),
        ma_ratio_5_20=ma_ratio(closes),
        volatility_20d=volatility(closes),
        rsi_14=rsi(closes),
        avg_amount_20d=avg_amount(amounts),
    )


__all__ = [
    "LIQUIDITY_WINDOW",
    "MA_LONG_WINDOW",
    "MA_SHORT_WINDOW",
    "MOMENTUM_WINDOW",
    "RSI_WINDOW",
    "VOLATILITY_WINDOW",
    "FactorVector",
    "avg_amount",
    "compute_factors",
    "ma_ratio",
    "momentum",
    "moving_average",
    "rsi",
    "volatility",
]
