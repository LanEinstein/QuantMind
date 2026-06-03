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
# v1 outputs bit-for-bit.
FEATURE_CODE_VERSION: str = "monitoring.intraday_calibration/v2"


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
        c
        for c in closes
        if isinstance(c, (int, float)) and math.isfinite(c) and c > 0
    ]
    # Need min_history+1 closes to form min_history returns.
    if len(series) < cfg.min_history + 1:
        return None
    if len(series) > cfg.window + 1:
        series = series[-(cfg.window + 1):]
    returns = [
        abs(series[i] / series[i - 1] - 1.0) for i in range(1, len(series))
    ]
    if len(returns) < cfg.min_history:
        return None
    scale = _nearest_rank_percentile(sorted(returns), cfg.percentile)
    raw = scale * cfg.multiplier
    if is_bear:
        raw *= cfg.bear_multiplier
    return max(cfg.floor, min(cfg.ceiling, raw))


__all__ = [
    "FEATURE_CODE_VERSION",
    "DrawdownCalibrationConfig",
    "derive_drawdown_threshold",
]
