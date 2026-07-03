"""Rolling market beta + downside tail beta (the DS defensive line's only new factor).

The defensive candidates (D1 tail block) need a name's sensitivity to the market —
no Tushare beta endpoint exists, so we compute it from ``daily`` closes vs the
CSI300 proxy (``fund_daily`` 510300.SH) by trailing OLS. Two committed factors:

* :func:`market_beta` — full-window OLS slope of a name's daily returns on the
  market's (``cov(r_s, r_m) / var(r_m)``). Low beta ⇒ falls less when the market
  falls (committed prior sign −1 in the D1 spec).
* :func:`tail_beta` — the same slope computed **only on the market's down-tail
  days** (market return in its lowest ``tail_quantile``). Low tail-beta ⇒ co-crashes
  less in systematic sell-offs (Tail beta and the cross-section in China, Applied
  Econ 2019; committed prior sign −1). Distinct from Ang-style downside-beta /
  semivariance, whose China sign is unreliable and is NOT committed.

Pure/offline: numpy + stdlib only, no ``backend`` import. PIT-clean by construction —
every read is a trailing window of already-closed bars; fail-closed (``None``) on
insufficient / non-finite / degenerate (zero market variance) inputs so a thin or
corrupt window never fabricates a beta.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

BETA_WINDOW: int = 60
"""Trailing window (≈ one quarter of trading days) for the rolling OLS."""

BETA_MIN_OBS: int = 40
"""Minimum usable return pairs in the window (else ``None`` — too thin)."""

TAIL_QUANTILE: float = 0.30
"""Down-tail cut: market days with return ≤ this within-window quantile."""

TAIL_MIN_OBS: int = 12
"""Minimum down-tail pairs for :func:`tail_beta` (else ``None``)."""

_MIN_MARKET_VAR: float = 1e-12
"""Market-return variance floor. Real A-share daily-return variance is ~1e-4, so
this is ~8 orders below any genuine cross-section — it only rejects a degenerate
(near-constant) window where float roundoff would otherwise yield an arbitrary beta."""


def _aligned_returns(
    stock_closes: Sequence[float], market_closes: Sequence[float]
) -> tuple[list[float], list[float]]:
    """Trailing simple returns for both series, kept only where BOTH are finite.

    The inputs are same-day-aligned close sequences (most-recent last). Returns
    ``(stock_returns, market_returns)`` of equal length with any pair dropped when
    either side is non-finite (a halted/corrupt bar), so the OLS never sees a NaN.
    """
    n = min(len(stock_closes), len(market_closes))
    # Align on the most-recent ``n`` closes (trailing), then diff to returns.
    s = list(stock_closes)[-n:]
    m = list(market_closes)[-n:]
    sr: list[float] = []
    mr: list[float] = []
    for i in range(1, n):
        ps, cs = s[i - 1], s[i]
        pm, cm = m[i - 1], m[i]
        # Require ALL four closes present, finite and strictly positive — a zero /
        # negative / corrupt bar (either the base or the current) is dropped, never
        # turned into a spurious return.
        if not (
            math.isfinite(ps)
            and math.isfinite(cs)
            and math.isfinite(pm)
            and math.isfinite(cm)
            and ps > 0.0
            and cs > 0.0
            and pm > 0.0
            and cm > 0.0
        ):
            continue
        rs = cs / ps - 1.0
        rm = cm / pm - 1.0
        if not (math.isfinite(rs) and math.isfinite(rm)):
            continue
        sr.append(rs)
        mr.append(rm)
    return sr, mr


def _ols_beta(
    stock_returns: Sequence[float], market_returns: Sequence[float]
) -> float | None:
    """``cov(r_s, r_m) / var(r_m)`` (population); ``None`` if market variance is 0."""
    if len(stock_returns) != len(market_returns) or len(market_returns) < 2:
        return None
    s = np.asarray(stock_returns, dtype=np.float64)
    m = np.asarray(market_returns, dtype=np.float64)
    var_m = float(np.var(m))  # population variance
    if not math.isfinite(var_m) or var_m < _MIN_MARKET_VAR:
        return None
    cov = float(np.mean((s - s.mean()) * (m - m.mean())))
    beta = cov / var_m
    return beta if math.isfinite(beta) else None


def market_beta(
    stock_closes: Sequence[float],
    market_closes: Sequence[float],
    *,
    window: int = BETA_WINDOW,
    min_obs: int = BETA_MIN_OBS,
) -> float | None:
    """Trailing full-window OLS beta of the name on the market.

    Uses the most-recent ``window`` return pairs; returns ``None`` when fewer than
    ``min_obs`` finite pairs are available or the market variance is degenerate.
    """
    if window <= 1:
        return None
    sr, mr = _aligned_returns(
        stock_closes[-(window + 1):], market_closes[-(window + 1):]
    )
    if len(mr) < min_obs:
        return None
    return _ols_beta(sr, mr)


def tail_beta(
    stock_closes: Sequence[float],
    market_closes: Sequence[float],
    *,
    window: int = BETA_WINDOW,
    tail_quantile: float = TAIL_QUANTILE,
    min_obs: int = TAIL_MIN_OBS,
) -> float | None:
    """Trailing OLS beta computed only on the market's down-tail days.

    The down-tail is the set of window days whose market return is ≤ the
    ``tail_quantile`` quantile of the window's market returns. Returns ``None`` when
    fewer than ``min_obs`` tail pairs are available or the tail market variance is
    degenerate.
    """
    if window <= 1 or not (0.0 < tail_quantile < 1.0):
        return None
    sr, mr = _aligned_returns(
        stock_closes[-(window + 1):], market_closes[-(window + 1):]
    )
    if len(mr) < min_obs:
        return None
    threshold = float(np.quantile(np.asarray(mr, dtype=np.float64), tail_quantile))
    tail_s = [sr[i] for i in range(len(mr)) if mr[i] <= threshold]
    tail_m = [mr[i] for i in range(len(mr)) if mr[i] <= threshold]
    if len(tail_m) < min_obs:
        return None
    return _ols_beta(tail_s, tail_m)


__all__ = [
    "BETA_MIN_OBS",
    "BETA_WINDOW",
    "TAIL_MIN_OBS",
    "TAIL_QUANTILE",
    "market_beta",
    "tail_beta",
]
