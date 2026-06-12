"""Three-tier value-line factors — pure PIT functions (Phase AC-002 / AC-003).

The value line answers a different question than the SHORT_TERM 5-factor stack:
*is this name a value hold* — does it sit on a real trend, has capital
recognised it, and do ≥2 independent logics + fundamentals back it. Like
:mod:`backend.screening.factors` every function here is deterministic and
side-effect-free over **point-in-time** inputs (returns / amounts / fundamentals
known at the decision date), so a value classification replays bit-exact.

No LLM, no IO, no ``backend.{llm,agents,mirofish}`` import. The cross-sectional
normalisation + the three-tier composite live downstream (AC-003
``compute_value_score``); this module produces the per-code raw signals.

PIT discipline (codex P1-4 / P1-5): the event-study window must be **fully
elapsed** by the decision date — the caller passes an ``event_offset`` that
leaves ``event_window`` observed bars after it; a window running off the end of
the series returns ``None`` rather than peeking at unobserved/future data.
Fundamentals are keyed by **announcement date**, never report-period end, so a
not-yet-announced quarter never leaks in (the caller filters before building the
inputs; this module never invents a value).

AC-002 = the mid tier (capital recognition + capacity). AC-003 extends this file
with the bottom + surface tiers and the composite.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

# Locked default windows (mirror the screening horizon).
EVENT_STUDY_WINDOW: int = 5
"""Trading days of post-event reaction measured by the event study."""
BETA_WINDOW: int = 60
"""Trailing days for the elasticity (beta) estimate."""
RESONANCE_TARGET: int = 2
"""Distinct independent logics for a full resonance score (owner: ≥2 core)."""


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def clamp01(value: float | None) -> float:
    """Clamp to [0, 1]; a dirty / missing value is 0.0 (conservative)."""
    if not _finite(value):
        return 0.0
    return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]


def event_study_abnormal_return(
    stock_returns: tuple[float, ...],
    market_returns: tuple[float, ...],
    event_offset: int | None,
    window: int = EVENT_STUDY_WINDOW,
) -> float | None:
    """Cumulative abnormal return in the ``window`` bars after a pinned event.

    ``stock_returns`` / ``market_returns`` are aligned PIT daily-return series
    (oldest → newest); ``event_offset`` is the index of the event bar (the
    pinned EVENT/THEME node's date, mapped upstream). The abnormal return is the
    stock return minus the market/sector return — "did the name react more than
    the tape" — summed over ``(event_offset, event_offset + window]``.

    Returns ``None`` (no measurable reaction) when there is no event, the series
    are mis-aligned, or the window is **not fully observed** — never peeks past
    the end of the series (PIT, codex P1-4).
    """
    if event_offset is None or window <= 0:
        return None
    n = len(stock_returns)
    if n == 0 or len(market_returns) != n:
        return None
    start = event_offset + 1
    end = event_offset + window
    if event_offset < 0 or end >= n:
        # Window runs off the end → not fully observed by the decision date.
        return None
    car = 0.0
    for i in range(start, end + 1):
        s = stock_returns[i]
        m = market_returns[i]
        if not (math.isfinite(s) and math.isfinite(m)):
            return None
        car += s - m
    return car


def amihud_illiquidity(
    returns: tuple[float, ...],
    amounts: tuple[float, ...],
) -> float | None:
    """Amihud (2002) illiquidity = mean(|return| / traded-amount).

    A higher value = more price impact per ¥ traded = *less* liquid (a value
    hold wants capacity = low Amihud). Aligned PIT series; ``None`` when empty,
    mis-aligned, or every bar has non-positive amount (no tradable signal).
    """
    n = len(returns)
    if n == 0 or len(amounts) != n:
        return None
    ratios: list[float] = []
    for r, a in zip(returns, amounts, strict=True):
        if not (math.isfinite(r) and math.isfinite(a)) or a <= 0:
            continue
        ratios.append(abs(r) / a)
    if not ratios:
        return None
    return statistics.fmean(ratios)


def free_float_capacity(
    free_float_shares: float | None,
    last_close: float | None,
) -> float | None:
    """Free-float market cap (¥) = free-float shares × last close.

    The capacity a name can absorb without the order moving it. ``None`` on a
    dirty / missing input (fail-closed — never an optimistic capacity).
    """
    if free_float_shares is None or last_close is None:
        return None
    if not (math.isfinite(free_float_shares) and math.isfinite(last_close)):
        return None
    if free_float_shares <= 0 or last_close <= 0:
        return None
    return free_float_shares * last_close


@dataclass(frozen=True)
class MidTierInputs:
    """Per-code PIT inputs for the mid tier (capital recognition + capacity)."""

    stock_returns: tuple[float, ...] = ()
    market_returns: tuple[float, ...] = ()
    event_offset: int | None = None
    event_window: int = EVENT_STUDY_WINDOW
    amounts: tuple[float, ...] = ()
    free_float_shares: float | None = None
    last_close: float | None = None
    turnover_rate: float | None = None
    """PIT daily_basic turnover fraction (sector/turnover percentile input)."""
    northbound_holding_pct: float | None = None
    """PIT northbound holding fraction of free float (capital-recognition input)."""
    main_capital_net: float | None = None
    """PIT net main-capital inflow (¥); capital-recognition input."""


@dataclass(frozen=True)
class MidTierFactors:
    """Per-code raw mid-tier signals (normalised into [0,1] by AC-003)."""

    abnormal_return: float | None
    amihud_illiquidity: float | None
    free_float_capacity: float | None
    turnover_rate: float | None
    northbound_holding_pct: float | None
    main_capital_net: float | None


def compute_mid_tier(inputs: MidTierInputs) -> MidTierFactors:
    """Compute the per-code raw mid-tier signals (deterministic, PIT)."""
    return MidTierFactors(
        abnormal_return=event_study_abnormal_return(
            inputs.stock_returns,
            inputs.market_returns,
            inputs.event_offset,
            inputs.event_window,
        ),
        amihud_illiquidity=amihud_illiquidity(inputs.stock_returns, inputs.amounts),
        free_float_capacity=free_float_capacity(
            inputs.free_float_shares, inputs.last_close
        ),
        turnover_rate=inputs.turnover_rate if _finite(inputs.turnover_rate) else None,
        northbound_holding_pct=(
            inputs.northbound_holding_pct
            if _finite(inputs.northbound_holding_pct)
            else None
        ),
        main_capital_net=(
            inputs.main_capital_net if _finite(inputs.main_capital_net) else None
        ),
    )


# ---------------------------------------------------------------------------
# Surface tier (AC-003): logic resonance + fundamentals (PIT) + elasticity.
# ---------------------------------------------------------------------------


def resonance_count(family_ids: Sequence[str]) -> int:
    """Count of **independent** evidence families supporting a name (codex P1-4).

    The surface tier rewards ≥2 *independent* logics. Repeated references from
    the **same LLM run** are one echo, not two logics — so we count distinct
    family ids (the caller maps each supporting KG edge to its evidence family /
    run id, deduping same-run repeats). Blank ids are ignored.
    """
    return len({fid for fid in family_ids if fid and fid.strip()})


def resonance_score(count: int, target: int = RESONANCE_TARGET) -> float:
    """Normalise a distinct-family count to [0, 1] (saturates at ``target``)."""
    if target <= 0 or count <= 0:
        return 0.0
    return min(1.0, count / target)


def beta(
    stock_returns: tuple[float, ...],
    market_returns: tuple[float, ...],
    window: int = BETA_WINDOW,
) -> float | None:
    """OLS beta of a name vs the market over the trailing ``window`` returns.

    Elasticity proxy (a value hold with a real catalyst should move *with* its
    theme). Aligned PIT return series; ``None`` on misalignment, too-short
    history, or a degenerate (zero-variance) market window.
    """
    n = len(stock_returns)
    if n < window or len(market_returns) != n or window <= 1:
        return None
    s = stock_returns[-window:]
    m = market_returns[-window:]
    if not all(math.isfinite(x) for x in s) or not all(math.isfinite(x) for x in m):
        return None
    var_m = statistics.pvariance(m)
    if var_m <= 0:
        return None
    mean_s = statistics.fmean(s)
    mean_m = statistics.fmean(m)
    cov = statistics.fmean(
        [(si - mean_s) * (mi - mean_m) for si, mi in zip(s, m, strict=True)]
    )
    return cov / var_m


def pit_fundamentals_value(
    records: Sequence[tuple[str, float]],
    as_of_date: str,
) -> float | None:
    """Latest fundamentals value **announced on or before** ``as_of_date`` (PIT).

    ``records`` is ``(announce_date, value)`` pairs (ISO ``YYYY-MM-DD``). Keyed
    by **announcement date**, never the report-period end, so a quarter that has
    not been disclosed by the decision date can never leak in (codex P1-5).
    Returns the most recently announced finite value, or ``None`` if none
    qualifies.
    """
    best_date = ""
    best_value: float | None = None
    for announce_date, value in records:
        if announce_date > as_of_date or not math.isfinite(value):
            continue
        if announce_date >= best_date:
            best_date = announce_date
            best_value = value
    return best_value


def percentile_rank(
    value: float | None,
    population: Sequence[float],
    *,
    higher_is_better: bool = True,
) -> float | None:
    """Cross-sectional percentile rank of ``value`` within ``population`` → [0,1].

    Helper for the orchestrator to normalise a raw mid-tier signal (CAR /
    capacity / capital flow) against the candidate cross-section before feeding
    the composite. Ties share the mean rank; ``invert`` via ``higher_is_better``
    (e.g. Amihud illiquidity: lower is better). ``None`` on a dirty value /
    empty population. Deterministic.
    """
    if value is None or not math.isfinite(value):
        return None
    finite_pop = [p for p in population if math.isfinite(p)]
    if not finite_pop:
        return None
    below = sum(1 for p in finite_pop if p < value)
    equal = sum(1 for p in finite_pop if p == value)
    rank = (below + 0.5 * equal) / len(finite_pop)
    return rank if higher_is_better else 1.0 - rank


__all__ = [
    "BETA_WINDOW",
    "EVENT_STUDY_WINDOW",
    "RESONANCE_TARGET",
    "MidTierFactors",
    "MidTierInputs",
    "amihud_illiquidity",
    "beta",
    "clamp01",
    "compute_mid_tier",
    "event_study_abnormal_return",
    "free_float_capacity",
    "percentile_rank",
    "pit_fundamentals_value",
    "resonance_count",
    "resonance_score",
]
