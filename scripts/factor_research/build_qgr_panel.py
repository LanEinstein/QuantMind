"""Build the QGR-3 short-term factor panel on TRAIN+VAL data only (build-new ⑦).

The framework re-research (QGR) reframes quant as the first-pass stock gate, so
this panel carries the genuinely short-horizon fast-leg factors (reversal +
forced lottery removal — :data:`factor_lib.QGR_FACTORS`) the round-1..4 panels
never had, plus the day-``d`` limit-tradability flags and the industry/size
neutralization inputs the IC study needs.

It is a SEPARATE module from ``build_factor_panel`` (already at its size ceiling)
and reuses that module's pure, already-tested primitives (``_cohort``,
``_forward_returns``, ``_board_ok``, the exclusion constants) so the round-1..4
build paths stay byte-for-byte unaffected. The per-day frame parse in
``_ingest_day_qgr`` deliberately MIRRORS (copies, not imports) ``_ingest_day`` —
a 35-line duplication accepted to avoid editing the byte-frozen round-1 ingest;
the two must be kept in lockstep if the shared parse ever changes. Same sacred-split
discipline: the feature window is ``train_val + embargo`` only, every date funnels
through :meth:`LockedSplit.assert_all_not_test`, features for date ``d`` use bars
``<= d`` and forward labels use bars ``> d`` inside the embargo — the sealed test
window is physically unreachable here.

PIT / survivorship correctness is inherited verbatim from ``build_factor_panel``
(per-day ``daily`` frame = the tradable universe; ``adj_close = raw·adj_factor``
hfq, future-invariant; ratios cancel the adjustment reference). The added
``stk_limit`` band is the RAW same-day price compared against the RAW close.
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

# Reuse the pure, already-tested primitives + exclusion constants. These never
# touch the QGR-only fields, so the round-1..4 paths remain byte-identical.
from .build_factor_panel import (
    DEFAULT_REBALANCE_FREQ,
    _board_ok,
    _CodeSeries,
    _cohort,
    _latest_snapshot_key,
)
from .factor_lib import (
    FACTOR_NAMES,
    QGR2_FACTOR_NAMES,
    QGR_FACTOR_NAMES,
    compute_factor_vector,
    compute_qgr2_factors,
    compute_qgr_factors,
)
from .industry_pit import IndustryPIT
from .limit_board_pit import read_limit_board
from .limit_status_pit import read_limits
from .locked_split import LockedSplit

log = structlog.get_logger(component="factor_research.build_qgr_panel")

VENDOR = "tushare"
_LIMIT_TOL = 1e-4  # rounding tolerance for "close == limit band"
# QGR forward-return horizons: 1 (fast-leg T+1) + 5/10/20 (the round-1 set).
# Local to the QGR panel — the round-1..4 builders keep their FORWARD_HORIZONS.
QGR_FORWARD_HORIZONS: tuple[int, ...] = (1, 5, 10, 20)
# Extra (non-factor) columns the QGR panel carries for the IC diagnostic.
QGR_PANEL_EXTRA_COLS: tuple[str, ...] = (
    "industry_l1",
    "circ_mv",
    "log_circ_mv",
    "at_up_limit_d",
    "at_down_limit_d",
)


def _forward_returns_qgr(adj_close: list[float], pos: int) -> dict[str, float | None]:
    """Forward returns at the QGR horizons (adds the T+1 fast-leg vs round-1).

    Mirrors ``build_factor_panel._forward_returns`` (incl. the ``fwd == fwd`` NaN
    guard) — kept a separate copy only because the horizon set differs; keep the
    two in lockstep if the forward-return formula ever changes."""
    base = adj_close[pos]
    out: dict[str, float | None] = {}
    for h in QGR_FORWARD_HORIZONS:
        nxt = pos + h
        if nxt < len(adj_close) and base > 0:
            fwd = adj_close[nxt]
            out[f"fwd_ret_{h}d"] = (fwd / base - 1.0) if fwd == fwd else None
        else:
            out[f"fwd_ret_{h}d"] = None
    return out


@dataclass
class _QGRCodeSeries(_CodeSeries):
    """``_CodeSeries`` extended with the per-day raw up/down price-limit bands.

    Subclassing keeps the reused ``_cohort`` / ``_passes_liquidity_price``
    primitives type-sound (a ``_QGRCodeSeries`` IS-A ``_CodeSeries``) while adding
    the QGR-only limit fields (NaN where a code has no ``stk_limit`` row).
    """

    up_limit: list[float] = field(default_factory=list)  # raw up-limit (NaN=missing)
    down_limit: list[float] = field(default_factory=list)
    # tranche-2: raw open / pre_close (1-day momentum) + per-day limit_list_d record.
    open_p: list[float] = field(default_factory=list)
    pre_close: list[float] = field(default_factory=list)
    lb_avail: list[bool] = field(default_factory=list)  # limit_list_d snapshot existed
    lb_limit: list[str | None] = field(default_factory=list)  # 'U'/'D'/'Z'/None
    lb_times: list[float | None] = field(default_factory=list)  # consecutive limit days
    lb_open: list[float | None] = field(default_factory=list)  # board open_times


def _ingest_day_qgr(
    store: SnapshotStore, day: str, series: dict[str, _QGRCodeSeries]
) -> int:
    """Parse one day's daily/adj/basic + stk_limit and append to each series.

    Mirrors ``build_factor_panel._ingest_day`` exactly for the shared fields
    (same fail-closed inner-join on daily↔adj + left-join daily_basic) and
    additionally attaches the raw up/down price-limit band (NaN when a code has
    no ``stk_limit`` row that day).
    """
    # NOTE: this parse MIRRORS build_factor_panel._ingest_day verbatim (daily↔adj
    # inner-join + daily_basic left-join, board + finite filters); keep the two in
    # lockstep if either changes. The only QGR addition is the stk_limit band.
    daily_s = store.latest(vendor=VENDOR, endpoint="daily", trade_date=day)
    adj_s = store.latest(vendor=VENDOR, endpoint="adj_factor", trade_date=day)
    basic_s = store.latest(vendor=VENDOR, endpoint="daily_basic", trade_date=day)
    if daily_s is None or adj_s is None or basic_s is None:
        return 0
    daily = pd.read_csv(
        io.BytesIO(daily_s.raw_payload),
        usecols=["ts_code", "open", "close", "pre_close", "amount"],
    )
    adj = pd.read_csv(io.BytesIO(adj_s.raw_payload), usecols=["ts_code", "adj_factor"])
    basic = pd.read_csv(
        io.BytesIO(basic_s.raw_payload),
        usecols=["ts_code", "turnover_rate", "pe_ttm", "circ_mv"],
    )
    limits = read_limits(store, day)
    lb_avail, lb_records = read_limit_board(store, day)
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
        # open / pre_close may be NaN on a partial-trading day; not filtered here
        # (the row's other factors stay valid) — intraday_return / overnight_gap
        # guard with math.isfinite, so the 1-day factors fail closed to None.
        up, down = limits.get(str(r.ts_code), (nan, nan))
        cs.up_limit.append(up)
        cs.down_limit.append(down)
        cs.open_p.append(float(r.open))
        cs.pre_close.append(float(r.pre_close))
        limit, times, opens = lb_records.get(str(r.ts_code), (None, None, None))
        cs.lb_avail.append(lb_avail)
        cs.lb_limit.append(limit)
        cs.lb_times.append(times)
        cs.lb_open.append(opens)
    return len(m)


def _ingest_series_qgr(
    store: SnapshotStore, feature_dates: list[str]
) -> dict[str, _QGRCodeSeries]:
    """Stream the daily/adj/basic + stk_limit frames into per-code PIT series."""
    series: dict[str, _QGRCodeSeries] = defaultdict(_QGRCodeSeries)
    for i, day in enumerate(feature_dates):
        _ingest_day_qgr(store, day, series)
        if i % 250 == 0:
            log.info("qgr_panel_ingest_progress", day=day, codes=len(series))
    return series


def _at_limit(raw_close: float, band: float, *, upper: bool) -> bool:
    """True iff ``raw_close`` closed at (within tol of) the day's band.

    ``upper`` → at the up-limit (``close >= up·(1−tol)``); else at the down-limit
    (``close <= down·(1+tol)``). A missing / non-positive band → ``False`` (a
    flag we cannot confirm is never asserted)."""
    if not (math.isfinite(band) and band > 0 and math.isfinite(raw_close)):
        return False
    if upper:
        return raw_close >= band * (1.0 - _LIMIT_TOL)
    return raw_close <= band * (1.0 + _LIMIT_TOL)


def _build_rows_qgr(
    series: dict[str, _QGRCodeSeries],
    rebalance_dates: list[str],
    *,
    industry: IndustryPIT,
) -> list[dict[str, object]]:
    """One row per (rebalance-date × surviving code): features <= d, labels > d.

    Same investable cross-section + bottom-30% size cut as the round-1 builder
    (reused ``_cohort``); adds the QGR short factors, the round-1 cross-sectional
    carry cluster (for the orthogonality check), the day-``d`` limit flags, and
    the PIT industry / log-size neutralization inputs.
    """
    rows: list[dict[str, object]] = []
    # _cohort is typed for the parent _CodeSeries; the dict is invariant, so cast
    # (sound — every value IS a _QGRCodeSeries). The cohort hands back parent-typed
    # cs, so re-bind to the subclass instance (same object) for the limit fields.
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
            vec = compute_qgr_factors(
                closes=closes,
                turnover_rates=cs.turnover[: pos + 1],
                raw_closes=cs.raw_close[: pos + 1],
                up_limits=cs.up_limit[: pos + 1],
            )
            # Round-1 cross-sectional carry cluster (same inputs) — the fast-leg
            # cousins the QGR short factors must add a NEW axis beyond.
            carry = compute_factor_vector(
                closes=closes,
                amounts=cs.amount[: pos + 1],
                turnover_rates=cs.turnover[: pos + 1],
                pe_ttm=cs.pe_ttm[pos],
            )
            # tranche-2: 1-day momentum (day-d raw open/close/pre_close) + limit-
            # board structure from the PRIOR day (`<d`); pos 0 has no prior day →
            # limit factors fail closed (available=False).
            if pos >= 1:
                prev_avail, prev_limit = cs.lb_avail[pos - 1], cs.lb_limit[pos - 1]
                prev_times, prev_open = cs.lb_times[pos - 1], cs.lb_open[pos - 1]
            else:
                prev_avail, prev_limit, prev_times, prev_open = False, None, None, None
            vec2 = compute_qgr2_factors(
                open_price=cs.open_p[pos],
                close=cs.raw_close[pos],
                pre_close=cs.pre_close[pos],
                prev_limit=prev_limit,
                prev_limit_times=prev_times,
                prev_open_times=prev_open,
                limit_data_available=prev_avail,
            )
            circ = cs.circ_mv[pos]
            raw_close = cs.raw_close[pos]
            row: dict[str, object] = {"date": day, "code": code, "ts_code": cs.ts_code}
            row.update(carry.as_dict())
            row.update(vec)
            row.update(vec2)
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
        log.info("qgr_panel_rebalance", day=day, cohort=len(cohort), rows=len(rows))
    return rows


def _panel_frame_qgr(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Assemble the QGR panel frame with the canonical column order."""
    cols = [
        "date",
        "code",
        "ts_code",
        *FACTOR_NAMES,
        *QGR_FACTOR_NAMES,
        *QGR2_FACTOR_NAMES,
        *(f"fwd_ret_{h}d" for h in QGR_FORWARD_HORIZONS),
        *QGR_PANEL_EXTRA_COLS,
    ]
    return pd.DataFrame(rows, columns=cols)


def build_qgr_panel(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    industry: IndustryPIT,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    max_rebalances: int = 0,
) -> pd.DataFrame:
    """Build the QGR-3 train_val short-term panel (one row per rebalance-date × code).

    Same sacred-split discipline as ``build_factor_panel.build_panel`` (feature
    window = train_val + embargo, ``assert_all_not_test`` guard); the injected
    PIT industry lookup is in/out-date gated by ``day`` so no test bar is read.
    """
    feature_dates = [*split.train_val_dates, *split.embargo_dates]
    split.assert_all_not_test(feature_dates)  # hard guard: never touch test
    series = _ingest_series_qgr(store, feature_dates)
    rebalance_dates = list(split.train_val_dates[::rebalance_freq])
    if max_rebalances:
        rebalance_dates = rebalance_dates[:max_rebalances]
    return _panel_frame_qgr(_build_rows_qgr(series, rebalance_dates, industry=industry))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument("--out", default="data/factor_research/panel_train_val_qgr.csv")
    parser.add_argument("--rebalance-freq", type=int, default=DEFAULT_REBALANCE_FREQ)
    parser.add_argument("--max-rebalances", type=int, default=0)
    parser.add_argument(
        "--asof",
        default=None,
        help="index_member_all as-of snapshot key (defaults to the latest stored; "
        "PIT membership comes from the table's in/out dates, not this asof)",
    )
    args = parser.parse_args()

    split = LockedSplit.load(args.lock, args.snapshot_root)
    store = SnapshotStore(args.snapshot_root)
    asof = args.asof or _latest_snapshot_key(args.snapshot_root, "index_member_all")
    industry = IndustryPIT.build(store, asof)
    panel = build_qgr_panel(
        split,
        store,
        industry=industry,
        rebalance_freq=args.rebalance_freq,
        max_rebalances=args.max_rebalances,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(args.out, index=False)
    log.info("qgr_panel_written", out=args.out, rows=len(panel))
    print(f"[written: {args.out}] rows={len(panel)} dates={panel['date'].nunique()}")


if __name__ == "__main__":
    main()


__all__ = [
    "QGR_PANEL_EXTRA_COLS",
    "build_qgr_panel",
]
