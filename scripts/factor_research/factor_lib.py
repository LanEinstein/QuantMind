"""A-share factor library — pure per-code factor computations.

Phase 3 of the factor-strategy research project. These are deterministic,
side-effect-free functions over a single code's daily series (oldest →
newest, qfq-adjusted closes) plus its point-in-time ``daily_basic`` fields
(earnings yield from ``pe_ttm``, turnover from ``turnover_rate``). They are
the research-time extension of ``backend/screening/factors.py`` and follow
the same conventions deliberately:

* **No fabrication** — every function returns ``None`` on insufficient
  history rather than guessing; the panel builder treats a ``None`` factor
  as a fail-closed drop (never an optimistic default).
* **Pure stdlib** — no numpy, no ``backend`` import, so a PIT replay
  reproduces the exact factor value bit-for-bit across environments.
* **Raw values, literature-tagged direction** — each function returns the
  *raw* quantity; orientation (whether high or low is attractive) lives in
  the :data:`FACTORS` registry so the cross-sectional panel can rank
  consistently and the IC study can confirm or refute the literature sign
  on real A-share data.

Factor selection follows the Phase 1 survey
(``docs/research/factor-theory-survey-2026-06-16.md``): the A-share
home-field families are short-term **reversal**, **low-volatility / IVOL**,
anti-**lottery (MAX)**, **value (E/P, not B/M)**, and turnover /
liquidity sentiment — with momentum demoted (weak/absent in China). The
``ret_20d`` factor is deliberately the same raw trailing return the live
``screener.FACTOR_WEIGHTS`` scores as *momentum* (attractive-high); the IC
study measures its true sign to confirm/refute the live weight's alignment.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# Default windows (locked constants; reversal/vol/max at the ~1-month
# horizon the A-share literature documents; the 5-day reversal respects the
# T+1 ">=2-day close-to-close" mandate from the survey).
REVERSAL_SHORT_WINDOW: int = 5
REVERSAL_MONTH_WINDOW: int = 20
VOLATILITY_WINDOW: int = 20
MAX_RETURN_WINDOW: int = 20
AMIHUD_WINDOW: int = 20
TURNOVER_WINDOW: int = 20


def _all_finite(values: list[float]) -> bool:
    """True iff every value is a finite float (no NaN/inf)."""
    return all(math.isfinite(v) for v in values)


def _returns(closes: list[float]) -> list[float]:
    """Simple daily returns; ``len(returns) == len(closes) - 1``.

    A non-positive prior close yields a 0.0 return for that step rather
    than dividing by zero — mirrors ``backend.screening.factors`` so a
    halted/corrupt bar does not blow up the computation. A non-finite
    ``prev``/``cur`` yields ``nan`` (NOT 0.0) so it is never silently
    masked as a real zero return — callers drop windows containing it.
    """
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:], strict=False):
        if not (math.isfinite(prev) and math.isfinite(cur)):
            out.append(math.nan)
        elif prev > 0:
            out.append(cur / prev - 1.0)
        else:
            out.append(0.0)
    return out


def trailing_return(closes: list[float], window: int) -> float | None:
    """Trailing ``window``-day return ``close[-1] / close[-1-window] - 1``.

    The shared primitive behind reversal (low = recent loser, attractive)
    and the momentum diagnostic. ``None`` when history is too short or the
    base price is non-positive.
    """
    if window <= 0 or len(closes) <= window:
        return None
    base = closes[-1 - window]
    last = closes[-1]
    if not (math.isfinite(base) and math.isfinite(last)) or base <= 0:
        return None
    return last / base - 1.0


def return_volatility(
    closes: list[float], window: int = VOLATILITY_WINDOW
) -> float | None:
    """Population stdev of the trailing ``window`` daily returns (IVOL proxy).

    A realised-volatility proxy for the low-volatility anomaly. ``None`` if
    fewer than ``window`` daily moves are available.
    """
    if window <= 1 or len(closes) < window + 1:
        return None
    tail = closes[-(window + 1) :]
    if not _all_finite(tail):  # guard: pstdev raises on NaN, fail closed
        return None
    return statistics.pstdev(_returns(tail))


def max_daily_return(
    closes: list[float], window: int = MAX_RETURN_WINDOW
) -> float | None:
    """Maximum single-day return over the trailing ``window`` (MAX/lottery).

    Bali-Cakici-Whitelaw lottery proxy: high MAX = recently-spiked,
    retail-overbid name, expected to underperform. ``None`` if too short.
    """
    if window <= 0 or len(closes) < window + 1:
        return None
    tail = closes[-(window + 1) :]
    if not _all_finite(tail):
        return None
    return max(_returns(tail))


def amihud_illiquidity(
    closes: list[float],
    amounts: list[float],
    window: int = AMIHUD_WINDOW,
) -> float | None:
    """Amihud illiquidity ``mean(|daily return| / traded amount)`` × 1e9.

    Scaled by 1e9 only to keep the raw magnitude readable (amounts are in
    ¥thousand from Tushare ``daily``); cross-sectional ranking is
    scale-invariant so the constant is cosmetic. High = illiquid. Days with
    a non-positive traded amount are skipped (a halt is not illiquidity
    evidence). ``None`` if fewer than ``window`` usable days remain.
    """
    rets = _returns(closes)
    if window <= 0 or len(rets) < window:
        return None
    # rets[i] corresponds to the move into closes[i+1]; align amounts to the
    # same later day so |return| and its day's turnover match.
    paired = [
        (abs(r), amounts[i + 1])
        for i, r in enumerate(rets[-window:], start=len(rets) - window)
        if i + 1 < len(amounts)
    ]
    # Fail-closed on any non-finite |return| (NaN close upstream) or amount;
    # ``len(usable) < window`` then drops the whole factor for the code-day.
    usable = [
        (ar, amt)
        for ar, amt in paired
        if amt > 0 and math.isfinite(ar) and math.isfinite(amt)
    ]
    if len(usable) < window:
        return None
    value = statistics.fmean(ar / amt for ar, amt in usable) * 1e9
    return value if math.isfinite(value) else None


def earnings_yield(pe_ttm: float | None) -> float | None:
    """Earnings yield ``1 / pe_ttm`` (the A-share value axis; E/P not B/M).

    Liu-Stambaugh-Yuan show E/P subsumes book-to-market in China. A
    non-positive or missing trailing P/E (loss-making firm) yields ``None``
    — fail-closed, so loss-makers are dropped rather than assigned a
    fabricated or misleadingly-negative value.
    """
    if pe_ttm is None or not math.isfinite(pe_ttm) or pe_ttm <= 0:
        return None
    return 1.0 / pe_ttm


def mean_turnover(
    turnover_rates: list[float], window: int = TURNOVER_WINDOW
) -> float | None:
    """Mean trailing ``window``-day turnover rate (speculation/sentiment).

    High recent turnover proxies retail speculative attention/overpricing
    (the China PMO/turnover anomaly) → expected to underperform. ``None``
    if fewer than ``window`` observations, or any are negative (malformed).
    """
    if window <= 0 or len(turnover_rates) < window:
        return None
    recent = turnover_rates[-window:]
    # Fail closed on non-finite (NaN turnover on a halt) or negative
    # (malformed) — never average a NaN/inf into a real factor value.
    if not _all_finite(recent) or any(t < 0 for t in recent):
        return None
    return statistics.fmean(recent)


@dataclass(frozen=True)
class FactorDef:
    """One factor's identity + literature-expected orientation.

    ``attractive_high`` encodes the Phase-1-survey orientation (does a HIGH
    raw value rank as more attractive for a long-only book?). It is the
    *prior*, not the verdict — the IC study measures the empirical sign on
    train data and may refute it (notably for ``ret_20d``: the live screener
    treats it as momentum/attractive-high; the survey predicts A-share
    reversal, i.e. attractive-LOW). ``mechanism`` matches a
    ``backend.strategy_evolution.mechanism_registry.EconomicMechanism``
    value so a promoted weighting can pass the mechanism gate.
    """

    name: str
    min_history: int
    attractive_high: bool
    mechanism: str
    expected_ic_sign: int  # +1 / -1 / 0 (literature prior on raw-factor IC)
    description: str


# The A-share-aligned candidate factor set (Phase 1 survey §2/§4). Raw
# trailing returns at the 5/20-day reversal horizons, realised vol, MAX,
# E/P value, turnover sentiment, Amihud illiquidity. ``ret_20d`` doubles as
# the live-``momentum_20d`` diagnostic (its IC sign tests the live weight).
FACTORS: tuple[FactorDef, ...] = (
    FactorDef(
        name="ret_5d",
        min_history=REVERSAL_SHORT_WINDOW + 1,
        attractive_high=False,
        mechanism="mean_reversion",
        expected_ic_sign=-1,
        description="Trailing 5d return — short-term reversal (>=2d, T+1 safe).",
    ),
    FactorDef(
        name="ret_20d",
        min_history=REVERSAL_MONTH_WINDOW + 1,
        attractive_high=False,
        mechanism="mean_reversion",
        expected_ic_sign=-1,
        description="Trailing 20d return — 1-month reversal; also the live "
        "momentum_20d diagnostic (live weight treats it attractive-high).",
    ),
    FactorDef(
        name="vol_20d",
        min_history=VOLATILITY_WINDOW + 1,
        attractive_high=False,
        mechanism="low_volatility_anomaly",
        expected_ic_sign=-1,
        description="20d return volatility — low-volatility / IVOL anomaly.",
    ),
    FactorDef(
        name="max_20d",
        min_history=MAX_RETURN_WINDOW + 1,
        attractive_high=False,
        mechanism="low_volatility_anomaly",
        expected_ic_sign=-1,
        description="Max daily return over 20d — anti-lottery (MAX) effect.",
    ),
    FactorDef(
        name="ep_ttm",
        min_history=1,
        attractive_high=True,
        mechanism="value_premium",
        expected_ic_sign=1,
        description="Earnings yield 1/pe_ttm — A-share value (E/P, not B/M).",
    ),
    FactorDef(
        name="turn_20d",
        min_history=TURNOVER_WINDOW,
        attractive_high=False,
        mechanism="liquidity_premium",
        expected_ic_sign=-1,
        description="Mean 20d turnover rate — turnover/sentiment overpricing.",
    ),
    FactorDef(
        name="amihud_20d",
        min_history=AMIHUD_WINDOW + 1,
        attractive_high=False,
        mechanism="liquidity_premium",
        expected_ic_sign=-1,
        description="Amihud illiquidity 20d — prefer liquid (tradability).",
    ),
)

FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in FACTORS)
FACTORS_BY_NAME: dict[str, FactorDef] = {f.name: f for f in FACTORS}


@dataclass(frozen=True)
class ResearchFactorVector:
    """One code's raw factor vector (all ``None`` where N/A). Frozen."""

    ret_5d: float | None
    ret_20d: float | None
    vol_20d: float | None
    max_20d: float | None
    ep_ttm: float | None
    turn_20d: float | None
    amihud_20d: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {
            "ret_5d": self.ret_5d,
            "ret_20d": self.ret_20d,
            "vol_20d": self.vol_20d,
            "max_20d": self.max_20d,
            "ep_ttm": self.ep_ttm,
            "turn_20d": self.turn_20d,
            "amihud_20d": self.amihud_20d,
        }


def compute_factor_vector(
    *,
    closes: list[float],
    amounts: list[float],
    turnover_rates: list[float],
    pe_ttm: float | None,
) -> ResearchFactorVector:
    """Compute the raw research factor vector for one code as-of a date.

    Pure: identical inputs always yield an identical vector. Inputs are the
    PIT-visible series ending on the decision date (oldest → newest), qfq
    closes + ¥ traded amounts + daily turnover-rate series + the day's
    trailing P/E. Insufficient history surfaces as ``None`` fields.
    """
    return ResearchFactorVector(
        ret_5d=trailing_return(closes, REVERSAL_SHORT_WINDOW),
        ret_20d=trailing_return(closes, REVERSAL_MONTH_WINDOW),
        vol_20d=return_volatility(closes),
        max_20d=max_daily_return(closes),
        ep_ttm=earnings_yield(pe_ttm),
        turn_20d=mean_turnover(turnover_rates),
        amihud_20d=amihud_illiquidity(closes, amounts),
    )


# ===========================================================================
# Round-2 factor families (R2-2): trend / quality / growth.
#
# These fill the round-1 gap (the seven factors above are all
# value/defensive/reversal — none can track a cap-weighted index in a
# large-cap bull, the round-1 FAIL root cause). They live in a SEPARATE
# registry (``R2_FACTORS``) so the round-1 panel/IC/weight-search modules that
# key off ``FACTORS`` / ``ResearchFactorVector`` are byte-for-byte unaffected.
#
# Trend factors use existing price series (no new data). Quality/growth read
# the PIT fundamentals (``fundamentals_pit`` — ann_date + vintage gated). Per
# the Phase-1 survey A-share momentum is weak/≈0, so the trend family is
# honestly tagged for regime/index tracking, NOT assumed to carry alpha; the
# IC study confirms or refutes on real data.
# ===========================================================================

# Trend windows (locked constants).
MOMENTUM_LOOKBACK: int = 252  # ~12 months
MOMENTUM_SKIP: int = 21  # skip the most recent ~1 month (avoid reversal noise)
HIGH_WINDOW: int = 250  # 52-week high lookback
SLOPE_WINDOW: int = 60  # trend-slope regression window


def momentum_skip(
    closes: list[float],
    lookback: int = MOMENTUM_LOOKBACK,
    skip: int = MOMENTUM_SKIP,
) -> float | None:
    """``close[-1-skip] / close[-1-lookback] - 1`` — 12-1 momentum.

    Skips the most recent ``skip`` bars so the well-documented short-term
    reversal does not contaminate the medium-term trend signal. ``None`` when
    history is too short, a window bar is non-finite, or the base is
    non-positive.
    """
    if lookback <= skip or skip < 0 or len(closes) < lookback + 1:
        return None
    base = closes[-1 - lookback]
    top = closes[-1 - skip]
    if not (math.isfinite(base) and math.isfinite(top)) or base <= 0:
        return None
    return top / base - 1.0


def distance_from_high(closes: list[float], window: int = HIGH_WINDOW) -> float | None:
    """``close[-1] / max(close[-window:]) - 1`` — distance from the 52-week high.

    George-Hwang nearness-to-high: a value near 0 (at the high) is the
    attractive end. Always ``<= 0``. ``None`` when too short, any window bar is
    non-finite, or the high is non-positive.
    """
    if window <= 0 or len(closes) < window:
        return None
    tail = closes[-window:]
    if not _all_finite(tail):
        return None
    high = max(tail)
    if high <= 0:
        return None
    return closes[-1] / high - 1.0


def trend_slope(closes: list[float], window: int = SLOPE_WINDOW) -> float | None:
    """OLS slope of ``log(close)`` vs the bar index over the trailing ``window``.

    Units = average log-return per day (an uptrend → positive). Pure stdlib
    least-squares. ``None`` when too short, any bar is non-finite or
    non-positive (log undefined), or the design is degenerate.
    """
    if window <= 1 or len(closes) < window:
        return None
    tail = closes[-window:]
    if not _all_finite(tail) or any(c <= 0 for c in tail):
        return None
    ys = [math.log(c) for c in tail]
    n = window
    mean_x = (n - 1) / 2.0
    mean_y = statistics.fmean(ys)
    num = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(ys))
    den = sum((i - mean_x) ** 2 for i in range(n))
    if den <= 0:
        return None
    slope = num / den
    return slope if math.isfinite(slope) else None


@runtime_checkable
class _FundamentalLike(Protocol):
    """Structural type for a PIT fundamentals record (avoids a backend import).

    Satisfied by :class:`fundamentals_pit.FundamentalRecord`; keeping it a
    Protocol preserves this module's pure-stdlib runtime import graph.
    """

    def get(self, field: str) -> float | None: ...


# Round-2 factor name → raw ``fina_indicator_vip`` field it reads.
FUNDAMENTAL_FACTOR_FIELDS: dict[str, str] = {
    "roe": "roe",
    "gpm": "grossprofit_margin",
    "np_yoy": "netprofit_yoy",
    "rev_yoy": "or_yoy",
}


def compute_trend_factors(closes: list[float]) -> dict[str, float | None]:
    """The three trend factors for one code as-of a date (raw values)."""
    return {
        "mom_12_1": momentum_skip(closes),
        "dist_high": distance_from_high(closes),
        "trend_slope": trend_slope(closes),
    }


def compute_fundamental_factors(
    record: _FundamentalLike | None,
) -> dict[str, float | None]:
    """The quality/growth factors from a PIT fundamentals record (raw values).

    A ``None`` record (no fundamentals known as-of the date) → all ``None``
    fail-closed; a non-finite field → ``None`` (never a fabricated value).
    """
    out: dict[str, float | None] = {}
    for name, field in FUNDAMENTAL_FACTOR_FIELDS.items():
        value = record.get(field) if record is not None else None
        out[name] = value if (value is not None and math.isfinite(value)) else None
    return out


# The round-2 registry. ``mechanism`` matches an ``EconomicMechanism`` value
# where one exists (momentum_continuation / quality_premium); the growth
# family is honestly tagged ``"growth_premium"`` which is INTENTIONALLY not yet
# a registered mechanism — the live promotion gate (has_valid_mechanism) will
# reject a growth-weighted strategy fail-closed until a future amendment adds
# GROWTH_PREMIUM. R2-2 is offline research and never invokes that gate.
R2_FACTORS: tuple[FactorDef, ...] = (
    FactorDef(
        name="mom_12_1",
        min_history=MOMENTUM_LOOKBACK + 1,
        attractive_high=True,
        mechanism="momentum_continuation",
        expected_ic_sign=1,
        description="12-1 month momentum (skip recent month). A-share momentum "
        "is weak/≈0 (survey §2.9): role = regime/index tracking, not assumed alpha.",
    ),
    FactorDef(
        name="dist_high",
        min_history=HIGH_WINDOW,
        attractive_high=True,
        mechanism="momentum_continuation",
        expected_ic_sign=1,
        description="Distance from 250d high (<=0; near 0 = near high) — "
        "George-Hwang 52-week-high; weak A-share prior, honestly tested.",
    ),
    FactorDef(
        name="trend_slope",
        min_history=SLOPE_WINDOW,
        attractive_high=True,
        mechanism="momentum_continuation",
        expected_ic_sign=1,
        description="OLS slope of log price over 60d (per-day log trend); "
        "weak A-share prior, honestly tested.",
    ),
    FactorDef(
        name="roe",
        min_history=0,
        attractive_high=True,
        mechanism="quality_premium",
        expected_ic_sign=1,
        description="Return on equity (PIT ann_date) — quality/profitability; "
        "weak/conditional in A-share (survey §2.7).",
    ),
    FactorDef(
        name="gpm",
        min_history=0,
        attractive_high=True,
        mechanism="quality_premium",
        expected_ic_sign=1,
        description="Gross profit margin (PIT) — Novy-Marx gross profitability, "
        "robust to earnings management.",
    ),
    FactorDef(
        name="np_yoy",
        min_history=0,
        attractive_high=True,
        mechanism="growth_premium",
        expected_ic_sign=1,
        description="Net-profit YoY growth (PIT). 'growth_premium' is NOT yet a "
        "registered EconomicMechanism (promotion fail-closed until amendment).",
    ),
    FactorDef(
        name="rev_yoy",
        min_history=0,
        attractive_high=True,
        mechanism="growth_premium",
        expected_ic_sign=1,
        description="Operating-revenue YoY growth (PIT) — growth; weak A-share "
        "prior, honestly tested.",
    ),
)

R2_FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in R2_FACTORS)
R2_FACTORS_BY_NAME: dict[str, FactorDef] = {f.name: f for f in R2_FACTORS}
# Merged lookup for the diagnostic IC study (round-1 + round-2).
ALL_FACTORS_BY_NAME: dict[str, FactorDef] = {**FACTORS_BY_NAME, **R2_FACTORS_BY_NAME}


__all__ = [
    "ALL_FACTORS_BY_NAME",
    "AMIHUD_WINDOW",
    "FACTORS",
    "FACTORS_BY_NAME",
    "FACTOR_NAMES",
    "FUNDAMENTAL_FACTOR_FIELDS",
    "HIGH_WINDOW",
    "MAX_RETURN_WINDOW",
    "MOMENTUM_LOOKBACK",
    "MOMENTUM_SKIP",
    "R2_FACTORS",
    "R2_FACTORS_BY_NAME",
    "R2_FACTOR_NAMES",
    "REVERSAL_MONTH_WINDOW",
    "REVERSAL_SHORT_WINDOW",
    "SLOPE_WINDOW",
    "TURNOVER_WINDOW",
    "VOLATILITY_WINDOW",
    "FactorDef",
    "ResearchFactorVector",
    "amihud_illiquidity",
    "compute_factor_vector",
    "compute_fundamental_factors",
    "compute_trend_factors",
    "distance_from_high",
    "earnings_yield",
    "max_daily_return",
    "mean_turnover",
    "momentum_skip",
    "return_volatility",
    "trailing_return",
    "trend_slope",
]
