"""Build the batch-A crowding / blow-off EXIT panel on TRAIN+VAL data (build-new).

The main-force-intent macro program's batch A operationalises the RISK/EXIT side of
the asymmetry (§2.1): per-name crowding / over-extension / blow-off proxies
(:data:`factor_lib.CROWDING_FACTORS`) used as a REDUCE/EXIT/veto gate. This panel
carries those factors plus — for the collinearity check that the EXIT factors must
add a NEW axis beyond — the round-1 cross-sectional carry cluster and the QGR
fast-leg, the day-``d`` limit-tradability flags, and the industry / size
neutralization inputs the diagnostic needs.

It is a SEPARATE module from ``build_qgr_panel`` (and ``build_factor_panel``, at
its size ceiling) and reuses their pure, already-tested primitives (``_cohort`` +
the bottom-30% size cut, ``_at_limit`` / ``_forward_returns_qgr`` / the QGR forward
horizons) so the round-1..4 / QGR build paths stay byte-for-byte unaffected. The
ONLY new ingest field vs the QGR panel is the RAW intraday ``high`` / ``low``
(needed by the ideal-amplitude factor); the per-day parse otherwise MIRRORS
``build_qgr_panel._ingest_day_qgr`` (copy, not import — kept in lockstep).

Same sacred-split discipline: the feature window is ``train_val + embargo`` only,
every date funnels through :meth:`LockedSplit.assert_all_not_test`, features for
date ``d`` use bars ``<= d`` and forward labels use bars ``> d`` — the sealed test
window is physically unreachable here. PIT / survivorship correctness is inherited
verbatim (per-day ``daily`` frame = the tradable universe; ``adj_close = raw·
adj_factor`` hfq; ratios cancel the adjustment reference; RAW high/low/pre_close
for the scale-invariant amplitude).
"""

from __future__ import annotations

import argparse
import io
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import structlog

from backend.marketdata_snapshot.store import SnapshotStore

from .build_factor_panel import (
    DEFAULT_REBALANCE_FREQ,
    _board_ok,
    _CodeSeries,
    _cohort,
    _latest_snapshot_key,
)
from .build_qgr_panel import (
    QGR_FORWARD_HORIZONS,
    _at_limit,
    _forward_returns_qgr,
)
from .factor_lib import (
    CROWDING_FACTOR_NAMES,
    FACTOR_NAMES,
    QGR_FACTOR_NAMES,
    compute_crowding_factors,
    compute_factor_vector,
    compute_qgr_factors,
)
from .industry_pit import IndustryPIT
from .leak_probe import assert_no_future_leak_sweep
from .limit_status_pit import read_limits
from .locked_split import LockedSplit

log = structlog.get_logger(component="factor_research.build_crowding_panel")

VENDOR = "tushare"
# Extra (non-factor) columns the crowding panel carries for the IC diagnostic.
CROWDING_PANEL_EXTRA_COLS: tuple[str, ...] = (
    "industry_l1",
    "circ_mv",
    "log_circ_mv",
    "at_up_limit_d",
    "at_down_limit_d",
)


@dataclass
class _CrowdingCodeSeries(_CodeSeries):
    """``_CodeSeries`` + the RAW per-day intraday high/low/pre_close + limit bands.

    Subclassing keeps the reused ``_cohort`` / ``_passes_liquidity_price``
    primitives type-sound (a ``_CrowdingCodeSeries`` IS-A ``_CodeSeries``) while
    adding the crowding-only fields (NaN where a code has no row that day).
    """

    high: list[float] = field(default_factory=list)  # raw intraday high
    low: list[float] = field(default_factory=list)  # raw intraday low
    pre_close_p: list[float] = field(default_factory=list)  # raw prior close
    up_limit: list[float] = field(default_factory=list)  # raw up-limit (NaN=missing)
    down_limit: list[float] = field(default_factory=list)


def _ingest_day_crowding(
    store: SnapshotStore, day: str, series: dict[str, _CrowdingCodeSeries]
) -> int:
    """Parse one day's daily/adj/basic + stk_limit and append to each series.

    MIRRORS ``build_qgr_panel._ingest_day_qgr`` for the shared fields (same
    fail-closed inner-join on daily↔adj + left-join daily_basic + board/finite
    filters); the only additions are the RAW high/low (for amplitude). Keep in
    lockstep with the QGR parse if the shared logic ever changes.
    """
    daily_s = store.latest(vendor=VENDOR, endpoint="daily", trade_date=day)
    adj_s = store.latest(vendor=VENDOR, endpoint="adj_factor", trade_date=day)
    basic_s = store.latest(vendor=VENDOR, endpoint="daily_basic", trade_date=day)
    if daily_s is None or adj_s is None or basic_s is None:
        return 0
    daily = pd.read_csv(
        io.BytesIO(daily_s.raw_payload),
        usecols=["ts_code", "high", "low", "close", "pre_close", "amount"],
    )
    adj = pd.read_csv(io.BytesIO(adj_s.raw_payload), usecols=["ts_code", "adj_factor"])
    basic = pd.read_csv(
        io.BytesIO(basic_s.raw_payload),
        usecols=["ts_code", "turnover_rate", "pe_ttm", "circ_mv"],
    )
    limits = read_limits(store, day)
    m = daily.merge(adj, on="ts_code", how="inner").merge(
        basic, on="ts_code", how="left"
    )
    m["code"] = m["ts_code"].astype(str).str.split(".").str[0]
    m = m[m["code"].map(_board_ok)]
    m["adj_close"] = m["close"] * m["adj_factor"]
    m = m[
        np.isfinite(m["adj_close"]) & np.isfinite(m["close"]) & np.isfinite(m["amount"])
    ]
    nan = float("nan")
    for r in m.itertuples(index=False):
        cs = series[r.code]
        if not cs.ts_code:
            cs.ts_code = str(r.ts_code)
        cs.pos_of_date[day] = len(cs.dates)
        cs.dates.append(day)
        cs.adj_close.append(float(r.adj_close))
        cs.raw_close.append(float(r.close))
        cs.amount.append(float(r.amount))
        cs.turnover.append(float(r.turnover_rate))
        cs.pe_ttm.append(float(r.pe_ttm))
        cs.circ_mv.append(float(r.circ_mv))
        cs.high.append(float(r.high))
        cs.low.append(float(r.low))
        cs.pre_close_p.append(float(r.pre_close))
        up, down = limits.get(str(r.ts_code), (nan, nan))
        cs.up_limit.append(up)
        cs.down_limit.append(down)
    return len(m)


def _ingest_series_crowding(
    store: SnapshotStore, feature_dates: list[str]
) -> dict[str, _CrowdingCodeSeries]:
    """Stream the daily/adj/basic + stk_limit frames into per-code PIT series."""
    series: dict[str, _CrowdingCodeSeries] = defaultdict(_CrowdingCodeSeries)
    for i, day in enumerate(feature_dates):
        _ingest_day_crowding(store, day, series)
        if i % 250 == 0:
            log.info("crowding_panel_ingest_progress", day=day, codes=len(series))
    return series


def _build_rows_crowding(
    series: dict[str, _CrowdingCodeSeries],
    rebalance_dates: list[str],
    *,
    industry: IndustryPIT,
) -> list[dict[str, object]]:
    """One row per (rebalance-date × surviving code): features <= d, labels > d.

    Same investable cross-section + bottom-30% size cut as the round-1 builder
    (reused ``_cohort``); adds the crowding EXIT factors, the round-1 carry cluster
    + QGR fast-leg (for the orthogonality check), the day-``d`` limit flags, and the
    PIT industry / log-size neutralization inputs.
    """
    rows: list[dict[str, object]] = []
    cohort_input = cast("dict[str, _CodeSeries]", series)
    for day in rebalance_dates:
        selected = _cohort(cohort_input, day)
        if selected is None:
            continue
        cohort, cmv_cut = selected
        for code, _parent_cs, pos in cohort:
            cs = series[code]
            if cs.circ_mv[pos] < cmv_cut:
                continue
            closes = cs.adj_close[: pos + 1]
            crowd = compute_crowding_factors(
                adj_closes=closes,
                highs=cs.high[: pos + 1],
                lows=cs.low[: pos + 1],
                pre_closes=cs.pre_close_p[: pos + 1],
                turnover_rates=cs.turnover[: pos + 1],
            )
            carry = compute_factor_vector(
                closes=closes,
                amounts=cs.amount[: pos + 1],
                turnover_rates=cs.turnover[: pos + 1],
                pe_ttm=cs.pe_ttm[pos],
            )
            qgr = compute_qgr_factors(
                closes=closes,
                turnover_rates=cs.turnover[: pos + 1],
                raw_closes=cs.raw_close[: pos + 1],
                up_limits=cs.up_limit[: pos + 1],
            )
            circ = cs.circ_mv[pos]
            raw_close = cs.raw_close[pos]
            row: dict[str, object] = {"date": day, "code": code, "ts_code": cs.ts_code}
            row.update(carry.as_dict())
            row.update(qgr)
            row.update(crowd)
            row.update(_forward_returns_qgr(cs.adj_close, pos))
            row["industry_l1"] = industry.l1_asof(cs.ts_code, day)
            row["circ_mv"] = circ
            row["log_circ_mv"] = (
                math.log(circ) if (math.isfinite(circ) and circ > 0) else None
            )
            row["at_up_limit_d"] = _at_limit(raw_close, cs.up_limit[pos], upper=True)
            row["at_down_limit_d"] = _at_limit(
                raw_close, cs.down_limit[pos], upper=False
            )
            rows.append(row)
        log.info(
            "crowding_panel_rebalance", day=day, cohort=len(cohort), rows=len(rows)
        )
    return rows


def _panel_frame_crowding(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Assemble the crowding panel frame with the canonical column order."""
    cols = [
        "date",
        "code",
        "ts_code",
        *FACTOR_NAMES,
        *QGR_FACTOR_NAMES,
        *CROWDING_FACTOR_NAMES,
        *(f"fwd_ret_{h}d" for h in QGR_FORWARD_HORIZONS),
        *CROWDING_PANEL_EXTRA_COLS,
    ]
    return pd.DataFrame(rows, columns=cols)


def build_crowding_panel(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    industry: IndustryPIT,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    max_rebalances: int = 0,
) -> pd.DataFrame:
    """Build the batch-A train_val crowding panel (one row per rebalance-date × code).

    Same sacred-split discipline as ``build_qgr_panel`` (feature window = train_val
    + embargo, ``assert_all_not_test`` guard); the injected PIT industry lookup is
    in/out-date gated by ``day`` so no test bar is read.
    """
    feature_dates = [*split.train_val_dates, *split.embargo_dates]
    split.assert_all_not_test(feature_dates)  # hard guard: never touch test
    series = _ingest_series_crowding(store, feature_dates)
    rebalance_dates = list(split.train_val_dates[::rebalance_freq])
    if max_rebalances:
        rebalance_dates = rebalance_dates[:max_rebalances]
    return _panel_frame_crowding(
        _build_rows_crowding(series, rebalance_dates, industry=industry)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--out", default="data/factor_research/panel_train_val_crowding.csv"
    )
    parser.add_argument("--rebalance-freq", type=int, default=DEFAULT_REBALANCE_FREQ)
    parser.add_argument("--max-rebalances", type=int, default=0)
    parser.add_argument(
        "--asof",
        default=None,
        help="index_member_all as-of snapshot key (defaults to the latest stored)",
    )
    parser.add_argument(
        "--leak-check",
        type=int,
        default=0,
        help="run the future-NaN poison sweep on the REAL builder over N cutoffs "
        "sampled across train_val (review #4; default 0 = off — each cutoff costs "
        "~2 extra full builds, so this is an owner-gated on-demand check)",
    )
    args = parser.parse_args()

    split = LockedSplit.load(args.lock, args.snapshot_root)
    store = SnapshotStore(args.snapshot_root)
    asof = args.asof or _latest_snapshot_key(args.snapshot_root, "index_member_all")
    industry = IndustryPIT.build(store, asof)
    panel = build_crowding_panel(
        split,
        store,
        industry=industry,
        rebalance_freq=args.rebalance_freq,
        max_rebalances=args.max_rebalances,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(args.out, index=False)
    log.info("crowding_panel_written", out=args.out, rows=len(panel))
    print(f"[written: {args.out}] rows={len(panel)} dates={panel['date'].nunique()}")

    if args.leak_check > 0:
        _run_leak_check(split, store, industry, n_cutoffs=args.leak_check)


def _run_leak_check(
    split: LockedSplit,
    store: SnapshotStore,
    industry: IndustryPIT,
    *,
    n_cutoffs: int,
) -> None:
    """Future-NaN poison sweep on the REAL builder over ``n_cutoffs`` train_val dates.

    Rebuilds the panel under poison at each sampled cutoff — empirically falsifies
    look-ahead on the real PIT bytes (review #4). Expensive (~2 builds/cutoff), so
    opt-in. The feature columns are the crowding/carry/QGR factors (NOT the fwd_ret
    labels, which are expected to change under poison)."""
    feature_cols = [*FACTOR_NAMES, *QGR_FACTOR_NAMES, *CROWDING_FACTOR_NAMES]
    rebals = list(split.train_val_dates[::DEFAULT_REBALANCE_FREQ])
    # Sample cutoffs spread across the interior (skip the first/last few so the
    # window below each cutoff has rebalance dates with full factor history).
    interior = rebals[len(rebals) // 5 : -2] or rebals
    step = max(1, len(interior) // max(1, n_cutoffs))
    cutoffs = interior[::step][:n_cutoffs]

    def _build(s: object) -> pd.DataFrame:
        return build_crowding_panel(
            split, cast("SnapshotStore", s), industry=industry
        )

    log.info("crowding_leak_check_start", cutoffs=cutoffs)
    reports = assert_no_future_leak_sweep(
        _build, store, cutoffs=cutoffs, feature_cols=feature_cols
    )
    for r in reports:
        print(f"[leak-check cutoff {r.cutoff}] {r.detail}")
    print(f"[leak-check] {len(reports)} cutoff(s) PASSED — builder is leak-free.")


if __name__ == "__main__":
    main()


__all__ = [
    "CROWDING_PANEL_EXTRA_COLS",
    "build_crowding_panel",
]
