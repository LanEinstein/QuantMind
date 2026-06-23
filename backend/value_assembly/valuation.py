"""AF-002 valuation (cheapness) factor — high-dividend / low PE-PB (PIT).

The replacement for the policy-theme map's deleted 传统高股息 layer: the AF-001
codex PIT-soundness gate ruled that 高股息/低估值 is a *value factor*, not a
regime tilt, so it lives in the value score rather than the theme registry.

Reads the ``daily_basic`` PIT snapshot (dividend yield ``dv_ratio``, trailing
earnings yield via ``pe_ttm``, book cheapness via ``pb``), ranks each dimension
cross-sectionally against the candidate set, and blends the present ranks into a
[0, 1] cheapness score (``None`` when no dimension is available, so the surface
tier drops the component rather than fabricating one). Pure, deterministic, 0
LLM; reads bytes from the SnapshotStore only.
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore
from backend.screening.value_factors import clamp01, percentile_rank

VENDOR = "tushare"
ENDPOINT_DAILY_BASIC = "daily_basic"


def _opt_pos(value: object) -> float | None:
    """A strictly-positive finite float, else ``None`` (a non-positive PE/PB is
    a loss-making / dirty quote that must not pose as 'cheap')."""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out > 0.0 else None


def _opt_nonneg(value: object) -> float | None:
    """A finite, non-negative float, else ``None`` (dividend yield ≥ 0)."""
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) and out >= 0.0 else None


def valuation_scores(
    store: SnapshotStore,
    *,
    codes: Sequence[str],
    decision_date: str,
) -> dict[str, float | None]:
    """Per-code cheapness composite ∈ [0, 1], or ``None`` when no dimension known.

    ``decision_date`` (YYYYMMDD) must be a trading day with a ``daily_basic``
    snapshot (the Line-1 frame's trade date). Dividend yield ranks high-is-good;
    PE-TTM and PB rank low-is-good (inverted). A code's composite is the mean of
    its present (signed) cross-sectional ranks; a code absent from the snapshot,
    or with no usable dimension, maps to ``None``.
    """
    snapshot = store.latest(
        vendor=VENDOR, endpoint=ENDPOINT_DAILY_BASIC, trade_date=decision_date
    )
    code_set = list(dict.fromkeys(codes))
    if snapshot is None:
        return {code: None for code in code_set}

    frame = pd.read_csv(
        io.StringIO(snapshot.raw_payload.decode("utf-8")),
        dtype=str,
        keep_default_na=False,
    )
    want = set(code_set)
    dv: dict[str, float] = {}
    pe: dict[str, float] = {}
    pb: dict[str, float] = {}
    for row in frame.itertuples(index=False):
        code = str(getattr(row, "ts_code", "")).strip()
        if code not in want:
            continue
        if (v := _opt_nonneg(getattr(row, "dv_ratio", None))) is not None:
            dv[code] = v
        if (v := _opt_pos(getattr(row, "pe_ttm", None))) is not None:
            pe[code] = v
        if (v := _opt_pos(getattr(row, "pb", None))) is not None:
            pb[code] = v

    dv_pop = list(dv.values())
    pe_pop = list(pe.values())
    pb_pop = list(pb.values())

    out: dict[str, float | None] = {}
    for code in code_set:
        ranks: list[float] = []
        if code in dv:
            r = percentile_rank(dv[code], dv_pop, higher_is_better=True)
            if r is not None:
                ranks.append(r)
        if code in pe:
            r = percentile_rank(pe[code], pe_pop, higher_is_better=False)
            if r is not None:
                ranks.append(r)
        if code in pb:
            r = percentile_rank(pb[code], pb_pop, higher_is_better=False)
            if r is not None:
                ranks.append(r)
        out[code] = clamp01(sum(ranks) / len(ranks)) if ranks else None
    return out


__all__ = ["valuation_scores"]
