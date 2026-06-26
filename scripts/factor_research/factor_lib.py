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


# ===========================================================================
# Round-3 factor families (R3-2): earnings-surprise / accruals / asset-growth.
#
# The round-2 alpha source proved too thin (locked-test excess −0.26%). These
# add two zero-cost orthogonal alpha sources from PIT financial statements:
#   * SUE (post-earnings drift) — single-quarter ex-recurring net profit
#     (fina_indicator_vip.profit_dedt, YTD → seasonal-difference standardised).
#   * accruals (Sloan) + asset-growth — annual income/cashflow/balancesheet.
# They live in a SEPARATE R3_FACTORS registry so the round-2 panel/search/IC
# modules keyed on R2_FACTORS / R2_FACTOR_NAMES are byte-for-byte unaffected.
# All are deterministic + pure-stdlib; a PIT replay reproduces them bit-exact.
# ===========================================================================

SUE_DIFF_WINDOW: int = 8  # trailing seasonal-difference window for the SUE σ
SUE_MIN_DIFFS: int = 6  # need this many seasonal diffs to standardise honestly
_QUARTER_MMDD: tuple[str, ...] = ("0331", "0630", "0930", "1231")


def _prior_period_same_fy(end_date: str) -> str | None:
    """Immediately preceding report period in the SAME fiscal year.

    ``0630→0331`` / ``0930→0630`` / ``1231→0930`` (same year); ``0331`` returns
    ``None`` (Q1 YTD already IS the single quarter).
    """
    year, mmdd = end_date[:4], end_date[4:]
    if mmdd not in _QUARTER_MMDD or mmdd == "0331":
        return None
    return f"{year}{_QUARTER_MMDD[_QUARTER_MMDD.index(mmdd) - 1]}"


def _same_quarter_prev_year(end_date: str) -> str:
    """Same fiscal quarter, one year earlier (``YYYY-1`` + same MMDD)."""
    return f"{int(end_date[:4]) - 1}{end_date[4:]}"


def _single_quarter_series(
    ytd_by_end: dict[str, float | None],
) -> dict[str, float]:
    """YTD → single-quarter net profit per period (Q1 = YTD; else YTD differenced).

    A period whose YTD (or, for Q2-Q4, the same-year prior YTD) is missing/NaN is
    omitted — never fabricated.
    """
    out: dict[str, float] = {}
    for end, v in ytd_by_end.items():
        if v is None or not math.isfinite(v) or end[4:] not in _QUARTER_MMDD:
            continue
        if end[4:] == "0331":
            out[end] = v
            continue
        prior = _prior_period_same_fy(end)
        pv = ytd_by_end.get(prior) if prior else None
        if pv is not None and math.isfinite(pv):
            out[end] = v - pv
    return out


def earnings_surprise_sue(ytd_by_end: dict[str, float | None]) -> float | None:
    """Standardised unexpected earnings (Foster SUE) from a YTD net-profit series.

    ``ytd_by_end`` = ``{end_date: as-known YTD profit_dedt}``. Computes the
    single-quarter series, the seasonal differences ``Δq_t = q_t − q_{t-4}``, and
    returns ``Δq_latest / stdev(trailing SUE_DIFF_WINDOW diffs)``. ``None`` when
    fewer than :data:`SUE_MIN_DIFFS` seasonal diffs are available or the
    dispersion is non-positive (fail-closed — never a fabricated surprise).
    """
    sq = _single_quarter_series(ytd_by_end)
    if not sq:
        return None
    diffs: dict[str, float] = {}
    for end, q in sq.items():
        prev = _same_quarter_prev_year(end)
        if prev in sq:
            diffs[end] = q - sq[prev]
    if not diffs:
        return None
    ends = sorted(diffs)
    window = [diffs[e] for e in ends[-SUE_DIFF_WINDOW:]]
    if len(window) < SUE_MIN_DIFFS:
        return None
    sigma = statistics.stdev(window)
    if not (math.isfinite(sigma) and sigma > 0):
        return None
    sue = diffs[ends[-1]] / sigma
    return sue if math.isfinite(sue) else None


def _annual_pair(total_assets: dict[str, float | None]) -> tuple[str, str] | None:
    """``(latest_annual_end, prior_annual_end)`` of two CONSECUTIVE year-ends.

    Both must be 12-31 reports present with finite values; non-consecutive
    annuals (a missing year) → ``None`` (an asset-growth across a gap would be a
    multi-year change, not 1-year — fail-closed).
    """
    annuals = sorted(
        e
        for e, v in total_assets.items()
        if e.endswith("1231") and v is not None and math.isfinite(v)
    )
    if not annuals:
        return None
    latest = annuals[-1]
    prev = f"{int(latest[:4]) - 1}1231"
    if total_assets.get(prev) is None:
        return None
    return latest, prev


def accruals_sloan(
    n_income: float | None,
    cfo: float | None,
    ta_now: float | None,
    ta_prev: float | None,
) -> float | None:
    """Cash-flow accruals ``(net profit − operating CFO) / avg total assets``.

    High accruals = lower earnings quality → expected to underperform
    (attractive-low). ``None`` on any missing input or a non-positive average
    asset base.
    """
    vals = (n_income, cfo, ta_now, ta_prev)
    if any(v is None or not math.isfinite(v) for v in vals):
        return None
    avg_ta = (ta_now + ta_prev) / 2.0  # type: ignore[operator]
    if avg_ta <= 0:
        return None
    return (n_income - cfo) / avg_ta  # type: ignore[operator]


def asset_growth(ta_now: float | None, ta_prev: float | None) -> float | None:
    """Year-on-year total-asset growth ``TA_t / TA_{t-1} − 1`` (Cooper-Gulen-Schill).

    High growth (over-investment) → expected to underperform (attractive-low).
    ``None`` on a missing input or non-positive prior asset base.
    """
    if ta_now is None or ta_prev is None:
        return None
    if not (math.isfinite(ta_now) and math.isfinite(ta_prev)) or ta_prev <= 0:
        return None
    return ta_now / ta_prev - 1.0


def compute_statement_factors(
    *,
    profit_dedt_ytd: dict[str, float | None],
    n_income_ytd: dict[str, float | None],
    cfo_ytd: dict[str, float | None],
    total_assets: dict[str, float | None],
) -> dict[str, float | None]:
    """The three R3 factors from a code's PIT statement series (raw values).

    Inputs are ``{end_date: as-known value}`` maps (the panel builder marshals
    them from the PIT readers). SUE uses every quarter; accruals + asset-growth
    use the latest two CONSECUTIVE annual reports. Any insufficiency → ``None``.
    """
    pair = _annual_pair(total_assets)
    if pair is None:
        accr = ag = None
    else:
        latest, prev = pair
        ta_now, ta_prev = total_assets[latest], total_assets[prev]
        accr = accruals_sloan(
            n_income_ytd.get(latest), cfo_ytd.get(latest), ta_now, ta_prev
        )
        ag = asset_growth(ta_now, ta_prev)
    return {
        "sue": earnings_surprise_sue(profit_dedt_ytd),
        "accr": accr,
        "asset_growth": ag,
    }


# The round-3 registry. ``accr`` maps to the registered ``quality_premium``
# mechanism (low accruals = higher earnings quality); ``sue`` and
# ``asset_growth`` are honestly tagged with mechanisms NOT yet in the
# ``EconomicMechanism`` enum (post_earnings_drift / asset_growth_anomaly), so the
# live promotion gate stays fail-closed until a future amendment — same posture
# as ``growth_premium``. R3 is offline research and never invokes that gate.
R3_FACTORS: tuple[FactorDef, ...] = (
    FactorDef(
        name="sue",
        min_history=0,
        attractive_high=True,
        mechanism="post_earnings_drift",
        expected_ic_sign=1,
        description="Standardised unexpected earnings (Foster SUE) on single-"
        "quarter ex-recurring net profit — post-earnings-announcement drift. "
        "'post_earnings_drift' is NOT yet a registered EconomicMechanism.",
    ),
    FactorDef(
        name="accr",
        min_history=0,
        attractive_high=False,
        mechanism="quality_premium",
        expected_ic_sign=-1,
        description="Sloan cash-flow accruals (net profit − operating CFO)/avg "
        "assets — low accruals = higher earnings quality (attractive-low).",
    ),
    FactorDef(
        name="asset_growth",
        min_history=0,
        attractive_high=False,
        mechanism="asset_growth_anomaly",
        expected_ic_sign=-1,
        description="YoY total-asset growth (Cooper-Gulen-Schill investment "
        "factor) — over-investment underperforms (attractive-low). "
        "'asset_growth_anomaly' is NOT yet a registered EconomicMechanism.",
    ),
)

R3_FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in R3_FACTORS)
R3_FACTORS_BY_NAME: dict[str, FactorDef] = {f.name: f for f in R3_FACTORS}


# ===========================================================================
# Round-4 factor family (R4-3): analyst-revision momentum (report_rc).
#
# The first family that is NOT a zero-cost financial-report derivative — it is an
# INFORMATION-FLOW signal (broker earnings-estimate revisions), the classic
# orthogonal alpha the three FAIL rounds lacked. The factor MATH lives in the
# cohesive ``analyst_revision_pit`` module (it is tightly coupled to the report
# stream's PIT windows / per-broker dedup / FY alignment); this registry only
# carries each factor's identity + the LITERATURE-prior sign. A-share analyst
# signs differ from the US (Liu-Zhang 2023) → the sign is verified from zero in
# the R4-4 IC study, never assumed. The names mirror
# ``analyst_revision_pit.ANALYST_FACTOR_NAMES`` (a test asserts equality).
#
# Mechanisms (analyst_revision / analyst_dispersion / analyst_coverage) are
# INTENTIONALLY not registered EconomicMechanism values — the live promotion gate
# stays fail-closed until a future amendment, same posture as growth_premium /
# post_earnings_drift / asset_growth_anomaly. R4 is offline research and never
# invokes that gate. They live in a SEPARATE R4_FACTORS registry so the
# round-1/2/3 panel / search / IC modules keyed on the earlier registries are
# byte-for-byte unaffected.
# ===========================================================================
R4_FACTORS: tuple[FactorDef, ...] = (
    FactorDef(
        name="np_rev",
        min_history=0,
        attractive_high=True,
        mechanism="analyst_revision",
        expected_ic_sign=1,
        description="Net-profit consensus revision momentum (FY1, trailing) — "
        "broker estimate up-revisions drift positive (Chan-Jegadeesh-Lakonishok). "
        "'analyst_revision' is NOT a registered EconomicMechanism.",
    ),
    FactorDef(
        name="eps_rev",
        min_history=0,
        attractive_high=True,
        mechanism="analyst_revision",
        expected_ic_sign=1,
        description="EPS consensus revision momentum (FY1, trailing) — likely "
        "collinear with np_rev (R4-4 picks one).",
    ),
    FactorDef(
        name="rev_diff",
        min_history=0,
        attractive_high=True,
        mechanism="analyst_revision",
        expected_ic_sign=1,
        description="Revision diffusion (n_up − n_down)/n_total of brokers revising "
        "their own FY1 estimate — breadth (orthogonal to np_rev's magnitude); n≥3.",
    ),
    FactorDef(
        name="rating_chg",
        min_history=0,
        attractive_high=True,
        mechanism="analyst_revision",
        expected_ic_sign=1,
        description="Net rating upgrade diffusion over the window (ordinal rating, "
        "house-agnostic map; unknown vocab → NaN fail-closed); n≥3.",
    ),
    FactorDef(
        name="tp_impl",
        min_history=0,
        attractive_high=True,
        mechanism="analyst_revision",
        expected_ic_sign=1,
        description="Target-price implied return median(min_price)/close − 1 "
        "(min_price = the real target price, NOT tp=total-profit); ~30% coverage.",
    ),
    FactorDef(
        name="disp",
        min_history=0,
        attractive_high=False,
        mechanism="analyst_dispersion",
        expected_ic_sign=-1,
        description="FY1 EPS forecast dispersion std/|mean| (Diether-Malloy-"
        "Scherbina) — high disagreement underperforms (attractive-low); n≥3. "
        "'analyst_dispersion' is NOT a registered EconomicMechanism.",
    ),
    FactorDef(
        name="cover_chg",
        min_history=0,
        attractive_high=True,
        mechanism="analyst_coverage",
        expected_ic_sign=1,
        description="Δ coverage breadth ln(n_now/n_back) of brokers with a live "
        "FY1 estimate — weak prior, honestly tested. 'analyst_coverage' is NOT a "
        "registered EconomicMechanism.",
    ),
)

R4_FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in R4_FACTORS)
R4_FACTORS_BY_NAME: dict[str, FactorDef] = {f.name: f for f in R4_FACTORS}


# ===========================================================================
# QGR-3 short-term factor family ⑦ (fast leg): reversal + forced lottery removal.
#
# The framework re-research (QGR) reframes quant as the FIRST-pass stock gate, not
# an index-tracker — so this family is the genuinely short-horizon A-share home
# turf the round-1..4 monthly/defensive factors never touched: 1-3 day reversal
# (the strongest A-share cross-sectional effect — Carpenter-Lu-Whitelaw RFS 2021;
# Gao-Jiang-Xiong-Xiong NBER 2023 document daily, not weekly/monthly, reversal)
# and the forced negative overlay that says what NOT to buy — short-window MAX,
# abnormal turnover, and the limit-CENSORED MAX count (Leippold-Wang-Zhou JFE
# 2022; Nartea-Wu PBFJ 2018; "MAX is not the max under daily price limits," PBFJ
# 2021: a name that closes at its up-limit has its true demand-return censored, so
# repeated limit-up closes are a lottery signal the raw return understates).
#
# They live in a SEPARATE ``QGR_FACTORS`` registry so the round-1..4 panel / IC /
# search modules keyed on the earlier registries are byte-for-byte unaffected.
# Every factor is attractive-LOW (high = a name to avoid); the IC study verifies
# the sign from zero on real A-share data (the literature prior is never assumed).
# All mechanisms are EXISTING ``EconomicMechanism`` values (mean_reversion /
# low_volatility_anomaly / liquidity_premium) — no governance enum change.
# ===========================================================================

QGR_REVERSAL_1D: int = 1  # 1-day close-to-close reversal
QGR_REVERSAL_3D: int = 3  # 3-day reversal
QGR_MAX_SHORT_WINDOW: int = 5  # short-window MAX (round-1 max_20d is monthly)
QGR_TURNOVER_SHORT_WINDOW: int = 5  # recent-attention window
QGR_TURNOVER_BASE_WINDOW: int = 20  # baseline-attention window
QGR_LIMIT_WINDOW: int = 5  # trailing window for the limit-up census


def turnover_spike(
    turnover_rates: list[float],
    short: int = QGR_TURNOVER_SHORT_WINDOW,
    base: int = QGR_TURNOVER_BASE_WINDOW,
) -> float | None:
    """Recent turnover vs its PRIOR baseline: ``mean(short) / mean(prior base) − 1``.

    The ``base`` baseline window is the ``base`` days immediately BEFORE the last
    ``short`` days (non-overlapping), so a recent surge is not diluted by being
    averaged into its own baseline — a cleaner "abnormal attention" measure than a
    trailing MA, and orthogonal to round-1 ``turn_20d`` (a level, not a change). A
    burst of recent turnover proxies retail speculative attention (the China
    turnover anomaly) → expected to underperform (attractive-low). ``None`` if
    fewer than ``short + base`` observations, any window value is non-finite (a
    halt's NaN turnover) or negative (malformed), or the baseline mean is
    non-positive (fail-closed — never a fabricated spike).
    """
    if short <= 0 or base <= 0 or len(turnover_rates) < short + base:
        return None
    recent = turnover_rates[-short:]
    prior = turnover_rates[-(short + base) : -short]
    if not (_all_finite(recent) and _all_finite(prior)):
        return None
    if any(t < 0 for t in recent) or any(t < 0 for t in prior):
        return None
    base_mean = statistics.fmean(prior)
    if base_mean <= 0:
        return None
    value = statistics.fmean(recent) / base_mean - 1.0
    return value if math.isfinite(value) else None


def limit_up_count(
    raw_closes: list[float],
    up_limits: list[float],
    window: int = QGR_LIMIT_WINDOW,
    tol: float = 1e-4,
) -> float | None:
    """Number of trailing ``window`` days that CLOSED at the up-limit.

    A close at (within ``tol`` of) the day's ``up_limit`` is a censored
    observation — the true demand-return was truncated at the price band — so a
    high count flags a repeatedly limit-locked, retail-overbid lottery name
    ("MAX is not the max under daily price limits"). Uses the RAW (unadjusted)
    close vs the RAW ``up_limit`` (both same-day, so no adjustment needed).
    ``None`` when history is too short or ANY limit price in the window is
    missing/NaN/non-positive (fail-closed — an unknown band cannot be censused).
    The two series must be day-aligned and equal-length (the panel appends them
    in lockstep); a length mismatch means the trailing windows would compare
    different days, so it fails closed rather than silently mis-censusing.
    """
    if len(raw_closes) != len(up_limits):
        return None
    if window <= 0 or len(raw_closes) < window or len(up_limits) < window:
        return None
    rc = raw_closes[-window:]
    ul = up_limits[-window:]
    if not (_all_finite(rc) and _all_finite(ul)) or any(u <= 0 for u in ul):
        return None
    return float(sum(1 for c, u in zip(rc, ul, strict=True) if c >= u * (1.0 - tol)))


QGR_FACTORS: tuple[FactorDef, ...] = (
    FactorDef(
        name="rev_1d",
        min_history=QGR_REVERSAL_1D + 1,
        attractive_high=False,
        mechanism="mean_reversion",
        expected_ic_sign=-1,
        description="Trailing 1d return — A-share daily reversal (Gao et al. 2023; "
        "the strongest short-horizon cross-sectional effect).",
    ),
    FactorDef(
        name="rev_3d",
        min_history=QGR_REVERSAL_3D + 1,
        attractive_high=False,
        mechanism="mean_reversion",
        expected_ic_sign=-1,
        description="Trailing 3d return — short-term reversal (Carpenter-Lu-"
        "Whitelaw RFS 2021); buy the oversold-but-healthy name.",
    ),
    FactorDef(
        name="max_5d",
        min_history=QGR_MAX_SHORT_WINDOW + 1,
        attractive_high=False,
        mechanism="low_volatility_anomaly",
        expected_ic_sign=-1,
        description="Max daily return over 5d — short-window anti-lottery (MAX) "
        "effect; complements round-1 monthly max_20d at the fast-leg horizon.",
    ),
    FactorDef(
        name="turn_spike",
        min_history=QGR_TURNOVER_SHORT_WINDOW + QGR_TURNOVER_BASE_WINDOW,
        attractive_high=False,
        mechanism="liquidity_premium",
        expected_ic_sign=-1,
        description="Recent/prior-baseline turnover spike mean(5d)/mean(prior "
        "20d)−1 — speculative-attention burst (Nartea-Wu PBFJ 2018), "
        "attractive-low; orthogonal to round-1 turn_20d (level not change).",
    ),
    FactorDef(
        name="n_limit_up_5d",
        min_history=QGR_LIMIT_WINDOW,
        attractive_high=False,
        mechanism="low_volatility_anomaly",
        expected_ic_sign=-1,
        description="Count of up-limit closes over 5d — limit-CENSORED MAX "
        "('MAX is not the max under daily price limits', PBFJ 2021); repeated "
        "limit-locks flag a retail-overbid lottery name.",
    ),
)

QGR_FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in QGR_FACTORS)
QGR_FACTORS_BY_NAME: dict[str, FactorDef] = {f.name: f for f in QGR_FACTORS}


def compute_qgr_factors(
    *,
    closes: list[float],
    turnover_rates: list[float],
    raw_closes: list[float],
    up_limits: list[float],
) -> dict[str, float | None]:
    """The QGR short-term factor vector for one code as-of a date (raw values).

    ``closes`` = qfq-adjusted (returns are adjustment-invariant); ``raw_closes`` /
    ``up_limits`` = RAW same-day prices for the limit-up census. Insufficient
    history surfaces as ``None`` per field (never a fabricated value).
    """
    return {
        "rev_1d": trailing_return(closes, QGR_REVERSAL_1D),
        "rev_3d": trailing_return(closes, QGR_REVERSAL_3D),
        "max_5d": max_daily_return(closes, QGR_MAX_SHORT_WINDOW),
        "turn_spike": turnover_spike(turnover_rates),
        "n_limit_up_5d": limit_up_count(raw_closes, up_limits),
    }


# ===========================================================================
# QGR-3 ⑦ tranche-2: 1-day momentum (§3.1.2) + limit-board structure (§3.3).
#
# The fast-leg completion: A-share 1-day intraday momentum / overnight gap
# (Gao-Jiang-Xiong-Xiong NBER 2023 — A-share has daily, not weekly/monthly,
# momentum; the overnight gap is a T+1 REVERSAL, the §3.6 trap, sign verified
# from zero) plus limit-board structure tags from ``limit_list_d`` (consecutive
# limit-up streak / broke-board fade), used STRICTLY on the prior day (`<d`) —
# same-day ``limit_list_d`` is only complete after the close, so using day d for
# a day-d feature is look-ahead (§3.3 / §3.6). ``limit_list_d`` starts 2020, so a
# day with no snapshot yields None (fail-closed), distinct from a present-but-
# not-on-board day (0). Separate ``QGR2_FACTORS`` registry → tranche-1 / round-1..4
# untouched; mechanisms reuse existing enum values (no governance change).
# ===========================================================================


def intraday_return(open_price: float, close: float) -> float | None:
    """Day-d intraday return ``close / open − 1`` (the 1-day momentum core).

    Gao et al. (2023): A-share intraday strength carries to T+1. ``None`` on a
    non-positive / non-finite open or a non-finite close (fail-closed)."""
    if not (math.isfinite(open_price) and math.isfinite(close)) or open_price <= 0:
        return None
    return close / open_price - 1.0


def overnight_gap(pre_close: float, open_price: float) -> float | None:
    """Overnight gap ``open / pre_close − 1`` — a T+1 REVERSAL signal in A-shares.

    §3.6 trap: an up-gap is adverse-selected and fades on T+1 (attractive-low),
    the OPPOSITE of treating it as momentum. ``None`` on a non-positive /
    non-finite prior close or a non-finite open."""
    if not (math.isfinite(pre_close) and math.isfinite(open_price)) or pre_close <= 0:
        return None
    return open_price / pre_close - 1.0


def limit_streak_prev(
    prev_limit: str | None,
    prev_limit_times: float | None,
    *,
    available: bool,
) -> float | None:
    """Consecutive limit-up count as of the PRIOR day (`<d`), from ``limit_list_d``.

    A high streak is a recently-run, high-attention name (§3.8 "don't chase the
    高位"). ``available`` = whether a ``limit_list_d`` snapshot exists for the
    prior day at all (pre-2020 → ``False`` → ``None``, can't tell). When present
    but the stock was not limit-up (``prev_limit != 'U'``) the streak is a known
    ``0``. A limit-up prior day with a non-finite ``limit_times`` → ``None``."""
    if not available:
        return None
    if prev_limit != "U":
        return 0.0
    if prev_limit_times is None or not math.isfinite(prev_limit_times):
        return None
    return float(prev_limit_times)


def broke_board_prev(
    prev_limit: str | None,
    prev_open_times: float | None,
    *,
    available: bool,
) -> float | None:
    """Did the stock SEAL then BREAK its up-limit on the prior day (`<d`)? (fade tag).

    A limit-up day with ``open_times > 0`` = the board opened (sold into) = a fade
    signal. ``available`` semantics match :func:`limit_streak_prev` (pre-2020 →
    ``None``; present-but-not-limit-up → ``0``)."""
    if not available:
        return None
    if prev_limit != "U":
        return 0.0
    if prev_open_times is None or not math.isfinite(prev_open_times):
        return None  # limit-up day with unknown open_times → can't tell (fail-closed)
    return 1.0 if prev_open_times > 0 else 0.0


QGR2_FACTORS: tuple[FactorDef, ...] = (
    FactorDef(
        name="intraday_ret_1d",
        min_history=1,
        attractive_high=True,
        mechanism="momentum_continuation",
        expected_ic_sign=1,
        description="Day-d intraday return close/open−1 — A-share 1-day momentum "
        "(Gao et al. 2023); fast leg (T+1), expected to decay/reverse by day 2-3.",
    ),
    FactorDef(
        name="overnight_gap_1d",
        min_history=1,
        attractive_high=False,
        mechanism="mean_reversion",
        expected_ic_sign=-1,
        description="Overnight gap open/pre_close−1 — A-share T+1 REVERSAL (§3.6 "
        "trap: up-gap fades, NOT momentum); attractive-low.",
    ),
    FactorDef(
        name="limit_streak_prev",
        min_history=1,
        attractive_high=False,
        mechanism="mean_reversion",
        expected_ic_sign=-1,
        description="Consecutive limit-up streak as of `<d` (limit_list_d, 2020+) — "
        "high = recently-run high-attention name (§3.8 don't chase 高位), "
        "attractive-low; verified from zero.",
    ),
    FactorDef(
        name="broke_board_prev",
        min_history=1,
        attractive_high=False,
        mechanism="mean_reversion",
        expected_ic_sign=-1,
        description="Sealed-then-broke up-limit on `<d` (limit_list_d open_times>0) "
        "— failed limit = fade tag, attractive-low.",
    ),
)

QGR2_FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in QGR2_FACTORS)
QGR2_FACTORS_BY_NAME: dict[str, FactorDef] = {f.name: f for f in QGR2_FACTORS}


def compute_qgr2_factors(
    *,
    open_price: float,
    close: float,
    pre_close: float,
    prev_limit: str | None,
    prev_limit_times: float | None,
    prev_open_times: float | None,
    limit_data_available: bool,
) -> dict[str, float | None]:
    """The QGR-3 tranche-2 factor vector for one code as-of a date (raw values).

    Price inputs are RAW same-day (intraday / gap are scale-invariant ratios).
    The limit-board fields are the PRIOR day's ``limit_list_d`` record (`<d`);
    ``limit_data_available`` flags whether that prior day had a snapshot at all
    (pre-2020 → False → limit factors None). Insufficient inputs surface as
    ``None`` per field (never fabricated)."""
    return {
        "intraday_ret_1d": intraday_return(open_price, close),
        "overnight_gap_1d": overnight_gap(pre_close, open_price),
        "limit_streak_prev": limit_streak_prev(
            prev_limit, prev_limit_times, available=limit_data_available
        ),
        "broke_board_prev": broke_board_prev(
            prev_limit, prev_open_times, available=limit_data_available
        ),
    }


# ===========================================================================
# Main-force-intent batch A: crowding / over-extension / blow-off EXIT family.
#
# The macro program's load-bearing finding (§2.1) is an ASYMMETRY: the tradable
# edge of main-force footprints is on the RISK/EXIT side, not entry timing. This
# family operationalises that — per-name proxies for a crowded / over-extended /
# blow-off state, used as a REDUCE/EXIT/veto gate (NOT a ranking alpha): high =
# a name to TRIM, attractive-LOW. The honest prior (arXiv 2512.11913 / SSRN
# 3803954 / PMO AEL2024) is that crowding predicts CRASH PROBABILITY (fat left
# tail), not mean return — so the diagnostic's load-bearing test is a conditional
# left-tail, with the mean-IC expected weak; the sign is still verified from zero.
# Every mechanism is an EXISTING ``EconomicMechanism`` value (no governance enum
# change); a SEPARATE ``CROWDING_FACTORS`` registry keeps round-1..4 / QGR panels
# byte-for-byte unaffected.
# ===========================================================================

CROWDING_BIAS_WINDOW: int = 20  # trailing window for the close-to-mean deviation
CROWDING_AMPLITUDE_WINDOW: int = 20  # high/low-state amplitude window
CROWDING_BLOWOFF_WINDOW: int = 20  # run-up window for the blow-off product
CROWDING_BLOWOFF_SHORT: int = 5  # recent-turnover window
CROWDING_BLOWOFF_BASE: int = 15  # prior-baseline turnover window (5+15=20d total)


def price_bias(closes: list[float], window: int = CROWDING_BIAS_WINDOW) -> float | None:
    """Close-to-trailing-mean deviation (乖离): ``close / mean(close[-window:]) - 1``.

    The §2.2 crowding 'close-price deviation' component — price stretched far above
    its own ``window``-day mean is over-extended (attractive-LOW: a high positive
    bias flags an EXIT/avoid). qfq closes (scale-invariant within the window).
    ``None`` if fewer than ``window`` observations, any is non-finite, or the
    trailing mean is non-positive (fail-closed — never a fabricated bias).
    """
    if window <= 0 or len(closes) < window:
        return None
    recent = closes[-window:]
    if not _all_finite(recent):
        return None
    mean = statistics.fmean(recent)
    if mean <= 0:
        return None
    value = recent[-1] / mean - 1.0
    return value if math.isfinite(value) else None


def ideal_amplitude(
    adj_closes: list[float],
    highs: list[float],
    lows: list[float],
    pre_closes: list[float],
    window: int = CROWDING_AMPLITUDE_WINDOW,
) -> float | None:
    """Ideal-amplitude factor (理想振幅, Kaiyuan): high-state minus low-state mean
    intraday amplitude over the trailing ``window`` days.

    For each day, amplitude = ``(high - low) / pre_close`` (same-day RAW ratio,
    scale-invariant); a day is 'high-state' if its qfq close ``>=`` the window
    median close, else 'low-state'. Factor = ``mean(amp|high) - mean(amp|low)``.
    Kaiyuan reports it a NEGATIVE/exit factor (a name that thrashes harder in its
    high-price state is distributing, not accumulating — the OPPOSITE of the
    bullish 'violent wash-out then rally' folklore; attractive-LOW). The macro
    program (§8) flags the broker's size-neutral claim as UNREPLICATED → the
    diagnostic re-tests the sign from zero with our own size neutralization.
    ``None`` if fewer than ``window`` aligned days, any input is non-finite, any
    ``pre_close`` is non-positive, any ``high < low``, or either state is empty
    (degenerate split — fail-closed).
    """
    n = window
    if n <= 1 or min(len(adj_closes), len(highs), len(lows), len(pre_closes)) < n:
        return None
    c, h, lo, pc = adj_closes[-n:], highs[-n:], lows[-n:], pre_closes[-n:]
    if not (_all_finite(c) and _all_finite(h) and _all_finite(lo) and _all_finite(pc)):
        return None
    if any(p <= 0 for p in pc) or any(hi < lw for hi, lw in zip(h, lo, strict=True)):
        return None
    med = statistics.median(c)
    high_amps = [(h[i] - lo[i]) / pc[i] for i in range(n) if c[i] >= med]
    low_amps = [(h[i] - lo[i]) / pc[i] for i in range(n) if c[i] < med]
    if not high_amps or not low_amps:
        return None
    value = statistics.fmean(high_amps) - statistics.fmean(low_amps)
    return value if math.isfinite(value) else None


def blowoff(
    closes: list[float],
    turnover_rates: list[float],
    window: int = CROWDING_BLOWOFF_WINDOW,
    short: int = CROWDING_BLOWOFF_SHORT,
    base: int = CROWDING_BLOWOFF_BASE,
) -> float | None:
    """Blow-off top intensity: ``max(ret_window, 0) × max(turnover_spike, 0)``.

    Only a name that BOTH ran up over ``window`` days AND on a recent turnover
    surge scores (negative parts clipped to 0): a crowded high-volume blow-off —
    the 'reversal blow-off' EXIT signal the macro program names (attractive-LOW;
    high = exhausted, trim). Reuses :func:`trailing_return` + :func:`turnover_spike`
    (so it inherits their fail-closed guards). ``None`` if either component is
    undefined (insufficient / non-finite history).
    """
    ret = trailing_return(closes, window)
    tspike = turnover_spike(turnover_rates, short, base)
    if ret is None or tspike is None:
        return None
    value = max(ret, 0.0) * max(tspike, 0.0)
    return value if math.isfinite(value) else None


CROWDING_FACTORS: tuple[FactorDef, ...] = (
    FactorDef(
        name="bias_20d",
        min_history=CROWDING_BIAS_WINDOW,
        attractive_high=False,
        mechanism="mean_reversion",
        expected_ic_sign=-1,
        description="Close / trailing-20d-mean − 1 (乖离 over-extension) — §2.2 "
        "crowding close-deviation component; high positive bias = stretched, EXIT.",
    ),
    FactorDef(
        name="ideal_amplitude_20d",
        min_history=CROWDING_AMPLITUDE_WINDOW,
        attractive_high=False,
        mechanism="low_volatility_anomaly",
        expected_ic_sign=-1,
        description="High-state minus low-state mean intraday amplitude over 20d "
        "(理想振幅, Kaiyuan) — a negative/exit factor; size-neutral claim re-tested "
        "from zero (macro program §8).",
    ),
    FactorDef(
        name="blowoff_20d",
        min_history=CROWDING_BLOWOFF_WINDOW + 1,
        attractive_high=False,
        mechanism="liquidity_premium",
        expected_ic_sign=-1,
        description="max(ret_20d,0)·max(turnover_spike_5/15,0) — crowded high-volume "
        "blow-off top (reversal blow-off); high = exhausted, EXIT/trim.",
    ),
)

CROWDING_FACTOR_NAMES: tuple[str, ...] = tuple(f.name for f in CROWDING_FACTORS)
CROWDING_FACTORS_BY_NAME: dict[str, FactorDef] = {f.name: f for f in CROWDING_FACTORS}


def compute_crowding_factors(
    *,
    adj_closes: list[float],
    highs: list[float],
    lows: list[float],
    pre_closes: list[float],
    turnover_rates: list[float],
) -> dict[str, float | None]:
    """The batch-A crowding/blow-off EXIT factor vector for one code as-of a date.

    ``adj_closes`` = qfq (bias + amplitude state splits are adjustment-consistent);
    ``highs`` / ``lows`` / ``pre_closes`` = RAW same-day (amplitude is a
    scale-invariant ratio); ``turnover_rates`` = ``daily_basic`` turnover. All
    attractive-LOW (high = a crowded/over-extended name to TRIM). Insufficient
    history surfaces as ``None`` per field (never fabricated); the sign is verified
    from zero in :mod:`crowding_factor_diagnostics`.
    """
    return {
        "bias_20d": price_bias(adj_closes),
        "ideal_amplitude_20d": ideal_amplitude(adj_closes, highs, lows, pre_closes),
        "blowoff_20d": blowoff(adj_closes, turnover_rates),
    }


# Merged lookup for the diagnostic IC study (round-1 + round-2 + round-3 + round-4
# + QGR-3 short-term tranche-1 + tranche-2 + batch-A crowding). A ``_neut`` variant
# resolves to its base factor's prior.
ALL_FACTORS_BY_NAME: dict[str, FactorDef] = {
    **FACTORS_BY_NAME,
    **R2_FACTORS_BY_NAME,
    **R3_FACTORS_BY_NAME,
    **R4_FACTORS_BY_NAME,
    **QGR_FACTORS_BY_NAME,
    **QGR2_FACTORS_BY_NAME,
    **CROWDING_FACTORS_BY_NAME,
}


__all__ = [
    "ALL_FACTORS_BY_NAME",
    "AMIHUD_WINDOW",
    "CROWDING_AMPLITUDE_WINDOW",
    "CROWDING_BIAS_WINDOW",
    "CROWDING_BLOWOFF_BASE",
    "CROWDING_BLOWOFF_SHORT",
    "CROWDING_BLOWOFF_WINDOW",
    "CROWDING_FACTORS",
    "CROWDING_FACTORS_BY_NAME",
    "CROWDING_FACTOR_NAMES",
    "FACTORS",
    "FACTORS_BY_NAME",
    "FACTOR_NAMES",
    "FUNDAMENTAL_FACTOR_FIELDS",
    "HIGH_WINDOW",
    "MAX_RETURN_WINDOW",
    "MOMENTUM_LOOKBACK",
    "MOMENTUM_SKIP",
    "QGR2_FACTORS",
    "QGR2_FACTORS_BY_NAME",
    "QGR2_FACTOR_NAMES",
    "QGR_FACTORS",
    "QGR_FACTORS_BY_NAME",
    "QGR_FACTOR_NAMES",
    "QGR_LIMIT_WINDOW",
    "QGR_MAX_SHORT_WINDOW",
    "QGR_REVERSAL_1D",
    "QGR_REVERSAL_3D",
    "QGR_TURNOVER_BASE_WINDOW",
    "QGR_TURNOVER_SHORT_WINDOW",
    "R2_FACTORS",
    "R2_FACTORS_BY_NAME",
    "R2_FACTOR_NAMES",
    "R3_FACTORS",
    "R3_FACTORS_BY_NAME",
    "R3_FACTOR_NAMES",
    "R4_FACTORS",
    "R4_FACTORS_BY_NAME",
    "R4_FACTOR_NAMES",
    "REVERSAL_MONTH_WINDOW",
    "REVERSAL_SHORT_WINDOW",
    "SLOPE_WINDOW",
    "SUE_DIFF_WINDOW",
    "SUE_MIN_DIFFS",
    "TURNOVER_WINDOW",
    "VOLATILITY_WINDOW",
    "FactorDef",
    "ResearchFactorVector",
    "accruals_sloan",
    "amihud_illiquidity",
    "asset_growth",
    "blowoff",
    "broke_board_prev",
    "compute_crowding_factors",
    "compute_factor_vector",
    "compute_fundamental_factors",
    "compute_qgr2_factors",
    "compute_qgr_factors",
    "compute_statement_factors",
    "compute_trend_factors",
    "distance_from_high",
    "earnings_surprise_sue",
    "earnings_yield",
    "ideal_amplitude",
    "intraday_return",
    "limit_streak_prev",
    "limit_up_count",
    "max_daily_return",
    "mean_turnover",
    "momentum_skip",
    "overnight_gap",
    "price_bias",
    "turnover_spike",
    "return_volatility",
    "trailing_return",
    "trend_slope",
]
