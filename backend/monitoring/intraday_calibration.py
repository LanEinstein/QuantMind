"""Deterministic per-stock intraday-threshold calibration (D1-a pilot).

The intraday ``DRAWDOWN_STOP`` threshold was a flat 5% vs ``prev_close`` — alert-
fatiguing for volatile names (a normal swing trips it) and too loose for calm ones
(it only fires after a large drop). Here the threshold is **derived from the
stock's own daily-return volatility**: a high percentile of its absolute daily
returns is the natural per-stock scale for "an abnormal intraday move". Pure +
deterministic (same closes + same config → same threshold), PIT-replayable from
the persisted daily frame.

The META-parameters (percentile / multiplier / floor / ceiling / window /
min_history) are runtime-immutable (a frozen dataclass, code-pinned like
``IntradayTriggerConfig``) and recalibrated **only offline** (P2-2 shadow + human
gate + git + restart) — never at runtime, never by an LLM. The derived threshold
feeds a *protective* stop, so it is clamped: ``floor`` stops over-tightening
(churn) and ``ceiling`` stops the calibration ever widening the stop past a real
risk exit.

Module red line (``backend/monitoring/CLAUDE.md`` import isolation, N-005): pure
quant — **no** ``backend.{llm,agents,agents_team,mirofish}`` import.
P0-7-amendment-2026-06-03-adaptive-intraday-thresholds.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

# Pinned feature-code version — bump when the derivation maths changes so a stale
# replay manifest fails closed. The runner folds this + the config values into its
# config hash, so a recalibration is captured for PIT replay. v2: optional bear-
# regime tightening of the drawdown threshold (D1-b,
# P0-7-amendment-2026-06-03-regime-conditioned-drawdown); is_bear=False reproduces
# v1 outputs bit-for-bit. v3: regime-conditioned take-profit multiple (D1-c,
# P0-7-amendment-2026-06-04-regime-conditioned-takeprofit) — a new, separate
# derivation; the drawdown maths is untouched (v2-identical). v4: tiered
# take-profit ladder config (D1-d, P0-10-amendment-line2-2026-06-04) — a new
# config type only; prior derivations untouched (v3-identical).
FEATURE_CODE_VERSION: str = "monitoring.intraday_calibration/v4"


@dataclass(frozen=True)
class DrawdownCalibrationConfig:
    """Runtime-immutable meta-parameters for the per-stock drawdown threshold.

    Conservative pilot defaults; recalibrated only offline (P2-2). The derived
    threshold is clamped to ``[floor, ceiling]`` so the calibration can never
    over-tighten (churn) nor widen the protective stop past a real risk exit.
    """

    window: int = 60  # trailing daily closes sampled for the return distribution
    min_history: int = 30  # fewer clean returns → fall back to the static default
    percentile: float = 0.90  # p-th percentile of |daily return| = the per-stock scale
    multiplier: float = 1.5  # widen beyond the typical move (fire only on abnormal)
    floor: float = 0.03  # never tighter than 3% (avoid noise stop-outs / churn)
    ceiling: float = 0.12  # never wider than 12% (still a real protective stop)
    # Regime conditioning (D1-b): in a BEAR regime, tighten the drawdown stop to
    # de-risk / preserve capital faster. Applied (× threshold, then re-clamped) only
    # when the caller passes ``is_bear=True``; inert otherwise. Recalibrated only
    # offline (P2-2 shadow + human gate).
    bear_multiplier: float = 0.8  # BEAR regime → 0.8× (tighten 20%, clamped ≥ floor)


def _nearest_rank_percentile(sorted_values: Sequence[float], p: float) -> float:
    """Deterministic nearest-rank percentile (no interpolation ambiguity).

    ``sorted_values`` must be ascending and non-empty. ``p`` in [0, 1].
    """
    n = len(sorted_values)
    # nearest-rank: rank = ceil(p · n), 1-indexed → clamp to [1, n], then 0-index.
    rank = math.ceil(p * n)
    idx = min(max(rank, 1), n) - 1
    return sorted_values[idx]


def derive_drawdown_threshold(
    closes: Sequence[float],
    config: DrawdownCalibrationConfig | None = None,
    *,
    is_bear: bool = False,
) -> float | None:
    """Per-stock intraday drawdown threshold from the |daily return| percentile.

    Returns the clamped threshold, or ``None`` when there is not enough clean
    daily history (the caller then falls back to the static default — never a
    looser-than-intended stop). Deterministic + replayable: only the persisted
    closes and the pinned config influence the result; zero external / LLM input.

    ``is_bear`` (D1-b regime conditioning): in a BEAR market regime, the threshold
    is tightened by ``config.bear_multiplier`` before the clamp — a faster
    protective exit when the market itself is weak. The regime verdict is the
    caller's, derived deterministically from the persisted benchmark index (never
    an LLM / off-market input). ``is_bear=False`` reproduces the prior behaviour.
    """
    cfg = config or DrawdownCalibrationConfig()
    series = [
        c for c in closes if isinstance(c, (int, float)) and math.isfinite(c) and c > 0
    ]
    # Need min_history+1 closes to form min_history returns.
    if len(series) < cfg.min_history + 1:
        return None
    if len(series) > cfg.window + 1:
        series = series[-(cfg.window + 1) :]
    returns = [abs(series[i] / series[i - 1] - 1.0) for i in range(1, len(series))]
    if len(returns) < cfg.min_history:
        return None
    scale = _nearest_rank_percentile(sorted(returns), cfg.percentile)
    raw = scale * cfg.multiplier
    if is_bear:
        raw *= cfg.bear_multiplier
    return max(cfg.floor, min(cfg.ceiling, raw))


@dataclass(frozen=True)
class TakeProfitCalibrationConfig:
    """Runtime-immutable regime tiers for the take-profit ``r_multiple`` (D1-c).

    The intraday TAKE_PROFIT trigger locks a tranche at ``cost + r_multiple×R``;
    these tiers condition WHEN that target sits by market regime — earlier in a
    BEAR / unchanged in NEUTRAL / later in a BULL ("let winners run"). The tiers
    only move the discretionary profit-taking target: protective stops (ATR /
    drawdown) and the hard concentration cap are untouched, so delaying the
    take-profit never removes protection. Recalibrated only offline (P2-2
    shadow + human gate + git + restart) — never at runtime, never by an LLM
    (P0-7-amendment-2026-06-04-regime-conditioned-takeprofit).
    """

    bull_r_multiple: float = 1.3  # BULL → later target (ride the trend)
    neutral_r_multiple: float = 1.0  # NEUTRAL → the static default exactly
    bear_r_multiple: float = 0.6  # BEAR → earlier lock-in (preserve gains)
    # The clamp guards a (mis)recalibration: ``floor`` stops the target landing
    # so close to cost that noise triggers churn sells; ``ceiling`` stops it
    # drifting so far the take-profit never fires (lock-in in name only).
    floor: float = 0.5
    ceiling: float = 2.0


def effective_r_multiple(
    config: TakeProfitCalibrationConfig,
    *,
    is_bull: bool,
    is_bear: bool,
) -> float:
    """Regime-conditioned take-profit multiple (pure, deterministic).

    The regime verdict is the caller's, derived deterministically from the
    persisted benchmark index (never an LLM / off-market input) and passed as
    two booleans so this module stays import-clean (zero ``backend.*`` sub-
    package imports — the D1-b convention). ``is_bear`` wins over ``is_bull``
    defensively (``classify_regime`` can never emit both; if a caller ever
    does, the conservative earlier-lock tier applies). Both ``False`` =
    NEUTRAL. The result is clamped to ``[floor, ceiling]``.
    """
    if is_bear:
        raw = config.bear_r_multiple
    elif is_bull:
        raw = config.bull_r_multiple
    else:
        raw = config.neutral_r_multiple
    return max(config.floor, min(config.ceiling, raw))


@dataclass(frozen=True)
class TieredTakeProfitConfig:
    """Runtime-immutable take-profit tier ladder (D1-d).

    Tier ``k`` (0-based, gated by the episode's tiers-taken count) targets
    ``cost + tiers[k] × eff_r × R`` and sells ``tranche_fraction`` of the
    current settled volume — with the default ladder ``(1.0, 2.0)`` that is
    "+1R sell half → +2R sell another tranche → residual rides the trailing
    stop". Composes with the D1-c regime multiple (``eff_r``): a BEAR regime
    shifts the WHOLE ladder earlier. Recalibrated only offline (P2-2 shadow
    + human gate + git + restart) — never at runtime, never by an LLM
    (P0-10-amendment-line2-2026-06-04).
    """

    tiers: tuple[float, ...] = (1.0, 2.0)

    def __post_init__(self) -> None:
        if not self.tiers:
            raise ValueError("TieredTakeProfitConfig.tiers must be non-empty")
        prev = 0.0
        for t in self.tiers:
            if not (isinstance(t, (int, float)) and math.isfinite(t) and t > 0):
                raise ValueError(
                    f"TieredTakeProfitConfig.tiers entry {t!r} must be a "
                    "finite positive number"
                )
            if t <= prev:
                raise ValueError(
                    "TieredTakeProfitConfig.tiers must be strictly ascending"
                )
            prev = t


__all__ = [
    "FEATURE_CODE_VERSION",
    "DrawdownCalibrationConfig",
    "TakeProfitCalibrationConfig",
    "TieredTakeProfitConfig",
    "derive_drawdown_threshold",
    "effective_r_multiple",
]
