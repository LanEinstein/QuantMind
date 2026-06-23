"""§3.8B objective bottom-confirmation gate — QGR-3 ⑧ slow leg (build-new).

owner's "买跌票" must distinguish a HEALTHY base (a quality name pulled back into
support, basing on dried-up volume) from a WASHOUT / falling knife ("跌了再跌").
This module is that objective multi-indicator gate. It is an OVERLAY, NOT a
rankable additive factor (main doc §3.8B): each condition is a pure ``bool|None``
(``None`` = inputs missing → cannot evaluate → fail-closed), and the composite
uses one-veto-is-enough logic. Its honest validation is the CONDITIONAL forward
-return discrimination of confirmed vs not-confirmed names (the diagnostic
module), never a rank-IC ranking axis.

Conditions (main doc §3.8B ①②④⑤⑥; ③ deliberately deferred — see below):
* ① **缩量** ``vol_dryup`` — recent turnover below its own prior baseline
  (a base forms on dried-up, not climaxing, volume). Clean PIT (daily_basic).
* ② **站稳筹码成本带** ``above_cost_band`` — close at/above the cyq_perf median
  holder cost (the band is reclaimed → limited overhead supply). ⚠️ cyq_perf is
  Tushare's MODEL-derived chip distribution (§3.5), so this condition is kept
  OUT of the clean-PIT core and is ABLATABLE — the gate works without it.
* ④ **无破位** ``no_breakdown`` — the close did not make a fresh N-day low (a
  knife keeps breaking support; a base holds it). Clean PIT (adj_close).
* ⑤ **无困境** ``no_distress`` — not a point-in-time ST / 退 name (namechange
  PIT). Clean PIT. (Halts are excluded by construction — a suspended name has no
  daily bar, so it never reaches the cohort.)
* ⑥ **基本面质量地板** ``quality_floor`` — positive ROE + gross margin + earnings
  yield (a profitable real business, not a loss-making value trap). Clean PIT
  (research-side fundamentals_pit + pe_ttm). Does NOT touch backend/.
* ③ **资金流企稳** — DELIBERATELY DEFERRED, not an oversight: moneyflow /
  moneyflow_hsgt / margin were NOT ingested in QGR-1, and §3.6 flags daily
  moneyflow as a trap (no robust predictive power — order-splitting destroys the
  "big order = smart money" premise). The stabilisation it would proxy is carried
  by the clean ①缩量 + ④无破位 price-volume conditions instead.

Thresholds are NATURAL boundaries committed in advance (a recent/baseline ratio
of 1.0; the median holder cost; a fresh window-low; the zero profitability floor)
— no data-tuned magic numbers, so the gate adds minimal researcher degrees of
freedom (anti-p-hacking, main doc §4.1 bottom-confirmation caveat).

All mechanisms reference EXISTING ``EconomicMechanism`` string values
(liquidity_premium / mean_reversion / quality_premium) for provenance only — this
module promotes nothing, so it touches no governance enum. Pure + deterministic;
no ``backend`` import (reuses the already-tested ``factor_lib.turnover_spike``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .cyq_perf_pit import ChipRecord
from .factor_lib import (
    QGR_TURNOVER_BASE_WINDOW,
    QGR_TURNOVER_SHORT_WINDOW,
    turnover_spike,
)

# --- committed-in-advance thresholds / windows (natural boundaries, no tuning) ---
NO_BREAKDOWN_WINDOW: int = 20  # a fresh low below the prior 20 closes = a breakdown
ROE_FLOOR: float = 0.0  # ROE (%) must be non-negative (a profitable business)
GPM_FLOOR: float = 0.0  # gross-profit margin (%) must be non-negative


@dataclass(frozen=True)
class BottomConfirmCondition:
    """One gate condition's identity + provenance (immutable).

    ``in_core`` marks the clean-PIT conditions that form the ablatable core; the
    cyq_perf cost-band condition is NOT in core (model-derived, §3.5). ``mechanism``
    references an existing ``EconomicMechanism`` value for provenance only (this
    module promotes nothing → no governance enum is touched).
    """

    name: str
    in_core: bool
    mechanism: str
    description: str


BOTTOM_CONFIRM_CONDITIONS: tuple[BottomConfirmCondition, ...] = (
    BottomConfirmCondition(
        name="vol_dryup",
        in_core=True,
        mechanism="liquidity_premium",
        description="① 缩量: recent turnover below its prior baseline (mean short / "
        "mean prior base < 1) — a base forms on dried-up, not climaxing, volume.",
    ),
    BottomConfirmCondition(
        name="no_breakdown",
        in_core=True,
        mechanism="mean_reversion",
        description="④ 无破位: close did not make a fresh 20d low (held support; a "
        "falling knife keeps breaking it).",
    ),
    BottomConfirmCondition(
        name="no_distress",
        in_core=True,
        mechanism="quality_premium",
        description="⑤ 无困境: not a point-in-time ST / 退 name (namechange PIT).",
    ),
    BottomConfirmCondition(
        name="quality_floor",
        in_core=True,
        mechanism="quality_premium",
        description="⑥ 质量地板: positive ROE + gross margin + earnings yield (a "
        "profitable real business, not a loss-making value trap).",
    ),
    BottomConfirmCondition(
        name="above_cost_band",
        in_core=False,  # cyq_perf model-derived (§3.5) → ablatable, not core
        mechanism="mean_reversion",
        description="② 站稳筹码成本带: close ≥ cyq_perf median holder cost "
        "(reclaimed band → limited overhead supply). MODEL-derived, ablatable.",
    ),
)

CORE_CONDITION_NAMES: tuple[str, ...] = tuple(
    c.name for c in BOTTOM_CONFIRM_CONDITIONS if c.in_core
)


def vol_dryup(
    turnover_rates: list[float],
    short: int = QGR_TURNOVER_SHORT_WINDOW,
    base: int = QGR_TURNOVER_BASE_WINDOW,
) -> bool | None:
    """① True iff recent turnover is BELOW its prior baseline (``ratio < 1``).

    Reuses the already-tested :func:`factor_lib.turnover_spike` (= ratio − 1) so
    the dry-up is the exact mirror of the fast-leg attention spike: ``spike < 0``.
    ``None`` propagates from ``turnover_spike`` (insufficient / malformed history).
    """
    spike = turnover_spike(turnover_rates, short=short, base=base)
    return None if spike is None else spike < 0.0


def no_breakdown(
    adj_closes: list[float], window: int = NO_BREAKDOWN_WINDOW
) -> bool | None:
    """④ True iff today's close is NOT a fresh ``window``-day low.

    ``breakdown`` = today's close strictly below the minimum of the PRIOR
    ``window`` closes (support broken); ``no_breakdown`` is its negation. ``None``
    when fewer than ``window + 1`` closes or any value in the window is non-finite
    (fail-closed — cannot confirm support held).
    """
    if window <= 0 or len(adj_closes) < window + 1:
        return None
    today = adj_closes[-1]
    prior = adj_closes[-(window + 1) : -1]
    if not math.isfinite(today) or not all(math.isfinite(c) for c in prior):
        return None
    return today >= min(prior)


def no_distress(*, is_st: bool | None) -> bool | None:
    """⑤ True iff the name is NOT a point-in-time ST / 退 name.

    ``is_st`` comes from ``NameChangePIT.is_st_asof`` (always a bool when a
    namechange index is available); ``None`` when no PIT name source is wired
    (the condition cannot be evaluated → fail-closed)."""
    return None if is_st is None else (not is_st)


def quality_floor(
    *,
    roe: float | None,
    gpm: float | None,
    ep_ttm: float | None,
    roe_floor: float = ROE_FLOOR,
    gpm_floor: float = GPM_FLOOR,
) -> bool | None:
    """⑥ True iff ROE ≥ floor AND gross margin ≥ floor AND earnings yield > 0.

    A profitable real business (the "high-value dip" §3.8B wants), not a
    loss-making value trap. ``ep_ttm`` = 1 / pe_ttm (negative for a loss-maker →
    fails the floor). ``None`` when any input is missing — quality cannot be
    confirmed without fundamentals (fail-closed)."""
    if roe is None or gpm is None or ep_ttm is None:
        return None
    if not (math.isfinite(roe) and math.isfinite(gpm) and math.isfinite(ep_ttm)):
        return None
    return roe >= roe_floor and gpm >= gpm_floor and ep_ttm > 0.0


def above_cost_band(*, raw_close: float, chip: ChipRecord | None) -> bool | None:
    """② True iff the close is at/above the cyq_perf median holder cost.

    ``chip`` is ``None`` on a pre-2018 / missing cyq_perf day → the condition
    cannot be evaluated (``None``); the gate's core never depends on it (§3.5
    model-derived caveat). ``None`` also on a non-finite close."""
    if chip is None or not math.isfinite(raw_close):
        return None
    return raw_close >= chip.cost_50pct


def gate_all(conditions: list[bool | None]) -> bool | None:
    """Composite gate: ``True`` iff all True; ``False`` if ANY is False (one veto
    is enough, even with unknowns present); ``None`` if no False but some unknown.
    """
    if any(c is False for c in conditions):
        return False
    if any(c is None for c in conditions):
        return None
    return True


def _flag(value: bool | None) -> float | None:
    """A ``bool|None`` condition as a panel float (1.0 / 0.0 / None)."""
    return None if value is None else float(value)


def compute_bottom_confirmation(
    *,
    adj_closes: list[float],
    turnover_rates: list[float],
    raw_close: float,
    is_st: bool | None,
    roe: float | None,
    gpm: float | None,
    ep_ttm: float | None,
    chip: ChipRecord | None,
) -> dict[str, float | None]:
    """The bottom-confirmation column vector for one (code, day) — raw flags.

    Emits the five per-condition flags, the clean-PIT ``bc_core_confirmed`` (4
    core conditions) and the ``bc_full_confirmed`` (core + the cyq_perf band), plus
    two continuous cyq_perf reads (``bc_cost_premium`` = close/cost_50pct − 1 and
    ``bc_winner_rate``) for the diagnostic's continuous / ablation views. Every
    field is ``None`` (→ panel NaN) where its inputs are missing (fail-closed)."""
    c_dry = vol_dryup(turnover_rates)
    c_brk = no_breakdown(adj_closes)
    c_dis = no_distress(is_st=is_st)
    c_qual = quality_floor(roe=roe, gpm=gpm, ep_ttm=ep_ttm)
    c_band = above_cost_band(raw_close=raw_close, chip=chip)

    core = gate_all([c_dry, c_brk, c_dis, c_qual])
    full = gate_all([c_dry, c_brk, c_dis, c_qual, c_band])

    cost_premium: float | None = None
    if chip is not None and math.isfinite(raw_close):
        cost_premium = raw_close / chip.cost_50pct - 1.0
    winner_rate = chip.winner_rate if chip is not None else None

    return {
        "bc_vol_dryup": _flag(c_dry),
        "bc_no_breakdown": _flag(c_brk),
        "bc_no_distress": _flag(c_dis),
        "bc_quality_floor": _flag(c_qual),
        "bc_above_cost_band": _flag(c_band),
        "bc_core_confirmed": _flag(core),
        "bc_full_confirmed": _flag(full),
        "bc_cost_premium": cost_premium,
        "bc_winner_rate": winner_rate,
    }


BOTTOM_CONFIRM_COLUMNS: tuple[str, ...] = (
    "bc_vol_dryup",
    "bc_no_breakdown",
    "bc_no_distress",
    "bc_quality_floor",
    "bc_above_cost_band",
    "bc_core_confirmed",
    "bc_full_confirmed",
    "bc_cost_premium",
    "bc_winner_rate",
)


__all__ = [
    "BOTTOM_CONFIRM_COLUMNS",
    "BOTTOM_CONFIRM_CONDITIONS",
    "CORE_CONDITION_NAMES",
    "GPM_FLOOR",
    "NO_BREAKDOWN_WINDOW",
    "ROE_FLOOR",
    "BottomConfirmCondition",
    "above_cost_band",
    "compute_bottom_confirmation",
    "gate_all",
    "no_breakdown",
    "no_distress",
    "quality_floor",
    "vol_dryup",
]
