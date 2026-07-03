"""Build the D1 dividend-low-vol defensive factor panel (TRAIN+VAL only).

The DS defensive-selection line's candidate D1 (``defensive-candidate-D1-dividend-
lowvol-core-2026-07-03.md``) selects inherently defensive names — low realized
volatility + sustainable high dividend yield + quality-safety + low market/tail
beta — in a ≤5 container over a monthly (20d) horizon. This module materialises the
one-row-per ``(rebalance-date × surviving code)`` factor panel the ranker + ablation
consume, carrying EXACTLY the committed D1 columns:

    date, code, ts_code,
    vol_20d, max_20d, dv_ratio, roe, gpm, accr, beta, tail_beta,
    industry_l1, circ_mv, log_circ_mv, fwd_ret_20d

It is a SEPARATE module from ``build_qgr_panel`` / ``build_factor_panel`` (both at
their size ceiling) and reuses their pure, already-tested primitives (``_cohort`` +
the bottom-30% size cut, PIT ST via ``NameChangePIT``, the R3 accruals wiring via
``compute_statement_factors``, ``FundamentalsPIT.asof`` roe/gpm, ``IndustryPIT``)
so those build paths stay byte-for-byte unaffected. The only genuinely new inputs
are ``dv_ratio`` (added to the ``daily_basic`` read) and the rolling
``beta`` / ``tail_beta`` computed against the CSI300 proxy (``fund_daily``
510300.SH) via :mod:`scripts.factor_research.beta_factor`.

Sacred-split discipline (inherited verbatim): the feature window is
``train_val + embargo`` only, every date funnels through
:meth:`LockedSplit.assert_all_not_test`, features for date ``d`` use bars ``<= d``
and the forward label uses the bar 20 td ``> d`` inside the embargo — the sealed
test window is physically unreachable here. Fundamentals / statements are
ann_date-gated (``< d``); ST is PIT. Fail-closed (``None``) on any missing /
non-finite input so a thin or corrupt window never fabricates a factor.
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

from . import beta_factor
from .build_factor_panel import (
    _board_ok,
    _CodeSeries,
    _cohort,
    _field_series,
    _latest_snapshot_key,
    _R3Inputs,
    build_r3_inputs,
)
from .defensive_d1_spec import BETA_PARAMS
from .factor_lib import compute_statement_factors, max_daily_return, return_volatility
from .locked_split import LockedSplit

log = structlog.get_logger(component="factor_research.defensive_d1_panel")

VENDOR = "tushare"
MARKET_PROXY: str = BETA_PARAMS.market_proxy  # 510300.SH CSI300 ETF
FORWARD_HORIZON: int = 20  # monthly (spec.HORIZON); the single D1 label horizon
DEFAULT_REBALANCE_FREQ: int = 20  # monthly rotation cadence (slow defensive factors)

# The committed D1 panel schema (candidate doc §2 + §4). Kept as an explicit tuple
# so the ranker / ablation can assert the frame's column contract.
D1_PANEL_COLUMNS: tuple[str, ...] = (
    "date",
    "code",
    "ts_code",
    "vol_20d",
    "max_20d",
    "dv_ratio",
    "roe",
    "gpm",
    "accr",
    "beta",
    "tail_beta",
    "industry_l1",
    "circ_mv",
    "log_circ_mv",
    "fwd_ret_20d",
)


@dataclass
class _D1CodeSeries(_CodeSeries):
    """``_CodeSeries`` extended with the per-day dividend yield (``dv_ratio``).

    Subclassing keeps the reused ``_cohort`` / ``_passes_liquidity_price``
    primitives type-sound (a ``_D1CodeSeries`` IS-A ``_CodeSeries``) while adding
    the D1-only dividend field (NaN where a code has no ``daily_basic.dv_ratio``
    that day — handled fail-closed downstream).
    """

    dv_ratio: list[float] = field(default_factory=list)


def _ingest_day_d1(
    store: SnapshotStore, day: str, series: dict[str, _D1CodeSeries]
) -> int:
    """Parse one day's daily/adj/basic and append to each code's series.

    Mirrors ``build_factor_panel._ingest_day`` for the shared fields (same
    fail-closed inner-join on daily↔adj + left-join daily_basic, board + finite
    filters) and additionally reads ``dv_ratio`` from ``daily_basic`` (the D1
    dividend leg). Keep in lockstep with ``_ingest_day`` if the shared parse changes.
    """
    daily_s = store.latest(vendor=VENDOR, endpoint="daily", trade_date=day)
    adj_s = store.latest(vendor=VENDOR, endpoint="adj_factor", trade_date=day)
    basic_s = store.latest(vendor=VENDOR, endpoint="daily_basic", trade_date=day)
    if daily_s is None or adj_s is None or basic_s is None:
        return 0
    daily = pd.read_csv(
        io.BytesIO(daily_s.raw_payload), usecols=["ts_code", "close", "amount"]
    )
    adj = pd.read_csv(io.BytesIO(adj_s.raw_payload), usecols=["ts_code", "adj_factor"])
    basic = pd.read_csv(
        io.BytesIO(basic_s.raw_payload),
        usecols=["ts_code", "turnover_rate", "pe_ttm", "circ_mv", "dv_ratio"],
    )
    m = daily.merge(adj, on="ts_code", how="inner").merge(
        basic, on="ts_code", how="left"
    )
    m["code"] = m["ts_code"].astype(str).str.split(".").str[0]
    m = m[m["code"].map(_board_ok)]
    m["adj_close"] = m["close"] * m["adj_factor"]
    m = m[
        np.isfinite(m["adj_close"]) & np.isfinite(m["close"]) & np.isfinite(m["amount"])
    ]
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
        cs.dv_ratio.append(float(r.dv_ratio))  # NaN when daily_basic lacks the code
    return len(m)


def _ingest_series_d1(
    store: SnapshotStore, feature_dates: list[str]
) -> dict[str, _D1CodeSeries]:
    """Stream the daily/adj/basic frames into per-code PIT series (oldest → newest)."""
    series: dict[str, _D1CodeSeries] = defaultdict(_D1CodeSeries)
    for i, day in enumerate(feature_dates):
        _ingest_day_d1(store, day, series)
        if i % 250 == 0:
            log.info("d1_panel_ingest_progress", day=day, codes=len(series))
    return series


def ingest_market_closes(
    store: SnapshotStore, feature_dates: list[str], *, proxy: str = MARKET_PROXY
) -> dict[str, float]:
    """``{trade_date: close}`` for the CSI300 proxy over the feature window.

    Reads ``fund_daily`` per day and picks the ``proxy`` (510300.SH) close — the
    market-return series the rolling beta / tail-beta regress against. A day with
    no proxy bar is simply absent (the beta alignment skips it fail-closed).
    """
    out: dict[str, float] = {}
    for day in feature_dates:
        snap = store.latest(vendor=VENDOR, endpoint="fund_daily", trade_date=day)
        if snap is None:
            continue
        frame = pd.read_csv(
            io.BytesIO(snap.raw_payload), usecols=["ts_code", "close"]
        )
        row = frame[frame["ts_code"].astype(str) == proxy]
        if row.empty:
            continue
        close = float(row["close"].iloc[0])
        if math.isfinite(close) and close > 0:
            out[day] = close
    return out


def aligned_market_window(
    dates: list[str],
    adj_closes: list[float],
    market_closes: dict[str, float],
    pos: int,
    n: int,
) -> tuple[list[float], list[float]]:
    """Date-aligned trailing ``(stock_closes, market_closes)`` ending at ``pos``.

    Walks backward from the code's position ``pos`` collecting up to ``n`` most-
    recent dates where BOTH the stock (finite, positive ``adj_close``) and the
    market proxy have a bar, so the two returned close lists are same-day aligned
    (chronological, most-recent last). A halted market/stock day is skipped on both
    sides together — the beta OLS then never pairs mismatched dates.
    """
    stock: list[float] = []
    mkt: list[float] = []
    i = pos
    while i >= 0 and len(stock) < n:
        mc = market_closes.get(dates[i])
        sc = adj_closes[i]
        if mc is not None and math.isfinite(sc) and sc > 0.0:
            stock.append(sc)
            mkt.append(mc)
        i -= 1
    stock.reverse()
    mkt.reverse()
    return stock, mkt


def _finite_or_none(value: float | None) -> float | None:
    """Coerce a non-finite (or missing) fundamental value to ``None`` (fail-closed).

    ROE / GPM come from the upstream ``fina_indicator_vip`` parse; guard them the
    same way ``dv_ratio`` / ``accr`` are guarded so a NaN never reaches the panel
    (codex P2 — never fabricate; a missing quality value stays missing).
    """
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _forward_return_20d(adj_close: list[float], pos: int) -> float | None:
    """The single 20td forward return (label ``> d``); ``None`` if too few bars.

    Mirrors ``build_factor_panel._forward_returns`` (incl. the ``fwd == fwd`` NaN
    guard) for the one D1 horizon.
    """
    base = adj_close[pos]
    nxt = pos + FORWARD_HORIZON
    if nxt < len(adj_close) and base > 0:
        fwd = adj_close[nxt]
        return (fwd / base - 1.0) if fwd == fwd else None
    return None


def _accruals(inputs: _R3Inputs, ts_code: str, day: str) -> float | None:
    """The R3 Sloan accruals for ``(ts_code, day)`` via the shared PIT wiring.

    Reuses ``build_factor_panel``'s exact ``compute_statement_factors`` call (income
    ``n_income`` / cashflow ``n_cashflow_act`` / balancesheet ``total_assets``, all
    ann_date-gated by ``day``) and takes only the ``accr`` output.
    """
    stmt = compute_statement_factors(
        profit_dedt_ytd=_field_series(
            inputs.fina_stmt.as_known(ts_code, day), "profit_dedt"
        ),
        n_income_ytd=_field_series(inputs.income.as_known(ts_code, day), "n_income"),
        cfo_ytd=_field_series(
            inputs.cashflow.as_known(ts_code, day), "n_cashflow_act"
        ),
        total_assets=_field_series(
            inputs.balancesheet.as_known(ts_code, day), "total_assets"
        ),
    )
    return stmt["accr"]


def _build_rows_d1(
    series: dict[str, _D1CodeSeries],
    rebalance_dates: list[str],
    *,
    inputs: _R3Inputs,
    market_closes: dict[str, float],
) -> list[dict[str, object]]:
    """One row per (rebalance-date × surviving code): features <= d, label > d.

    Same investable cross-section + bottom-30% size cut + PIT ST exclusion as the
    round-3 builder (reused ``_cohort`` with ``namechange.is_st_asof``). The always-
    on universe filters (board whitelist / liquidity / price / size cut / PIT ST)
    live here; the committed RANK-time exclusion gates (lottery decile / ROE floor /
    GPM decile / dividend median) are applied by the ranker on the RAW columns.
    """
    rows: list[dict[str, object]] = []
    n_win = BETA_PARAMS.window + 1  # trailing pairs the rolling beta needs
    cohort_input = cast("dict[str, _CodeSeries]", series)
    for day in rebalance_dates:
        selected = _cohort(cohort_input, day, st_filter=inputs.namechange.is_st_asof)
        if selected is None:
            continue
        cohort, cmv_cut = selected
        for code, _parent_cs, pos in cohort:
            cs = series[code]
            if cs.circ_mv[pos] < cmv_cut:  # bottom-30% size cut
                continue
            closes = cs.adj_close[: pos + 1]
            dv_raw = cs.dv_ratio[pos]
            record = inputs.fundamentals.asof(cs.ts_code, day)
            roe = _finite_or_none(record.get("roe") if record is not None else None)
            gpm = _finite_or_none(
                record.get("grossprofit_margin") if record is not None else None
            )
            stock_w, mkt_w = aligned_market_window(
                cs.dates, cs.adj_close, market_closes, pos, n_win
            )
            beta = beta_factor.market_beta(
                stock_w, mkt_w, window=BETA_PARAMS.window, min_obs=BETA_PARAMS.min_obs
            )
            tbeta = beta_factor.tail_beta(
                stock_w,
                mkt_w,
                window=BETA_PARAMS.window,
                tail_quantile=BETA_PARAMS.tail_quantile,
                min_obs=BETA_PARAMS.tail_min_obs,
            )
            circ = cs.circ_mv[pos]
            rows.append(
                {
                    "date": day,
                    "code": code,
                    "ts_code": cs.ts_code,
                    "vol_20d": return_volatility(closes),
                    "max_20d": max_daily_return(closes),
                    "dv_ratio": float(dv_raw) if math.isfinite(dv_raw) else None,
                    "roe": roe,
                    "gpm": gpm,
                    "accr": _accruals(inputs, cs.ts_code, day),
                    "beta": beta,
                    "tail_beta": tbeta,
                    "industry_l1": inputs.industry.l1_asof(cs.ts_code, day),
                    "circ_mv": circ,
                    "log_circ_mv": (
                        math.log(circ)
                        if (math.isfinite(circ) and circ > 0)
                        else None
                    ),
                    "fwd_ret_20d": _forward_return_20d(cs.adj_close, pos),
                }
            )
        log.info("d1_panel_rebalance", day=day, cohort=len(cohort), rows=len(rows))
    return rows


def build_defensive_d1_panel(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    inputs: _R3Inputs,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    max_rebalances: int = 0,
) -> pd.DataFrame:
    """Build the D1 train_val defensive panel (one row per rebalance-date × code).

    Same sacred-split discipline as ``build_factor_panel.build_panel_r3`` (feature
    window = train_val + embargo, ``assert_all_not_test`` guard); fundamentals /
    statements / industry / namechange are PIT and ``day``-gated so no test bar is
    read. The CSI300 proxy closes are ingested over the same feature window.
    """
    rebalance_dates = list(split.train_val_dates[::rebalance_freq])
    if max_rebalances:
        rebalance_dates = rebalance_dates[:max_rebalances]
    # Ingest only the feature window the chosen rebalances actually need: from the
    # start through the last rebalance + the 20td forward-label horizon (all trailing
    # history before each rebalance is retained). For the full build the last
    # rebalance sits near the train_val end so this spans essentially train_val +
    # embargo; for a --max-rebalances smoke it trims the ingest to the head, making
    # the smoke fast without changing the per-row computation.
    all_feature = [*split.train_val_dates, *split.embargo_dates]
    last_idx = all_feature.index(rebalance_dates[-1])
    end_idx = min(last_idx + FORWARD_HORIZON, len(all_feature) - 1)
    feature_dates = all_feature[: end_idx + 1]
    split.assert_all_not_test(feature_dates)  # hard guard: never touch test
    series = _ingest_series_d1(store, feature_dates)
    market_closes = ingest_market_closes(store, feature_dates)
    log.info(
        "d1_panel_market_proxy",
        proxy=MARKET_PROXY,
        market_days=len(market_closes),
        feature_days=len(feature_dates),
    )
    rows = _build_rows_d1(
        series, rebalance_dates, inputs=inputs, market_closes=market_closes
    )
    return pd.DataFrame(rows, columns=list(D1_PANEL_COLUMNS))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--out", default="data/factor_research/panel_train_val_defensive_d1.csv"
    )
    parser.add_argument("--rebalance-freq", type=int, default=DEFAULT_REBALANCE_FREQ)
    parser.add_argument(
        "--max-rebalances", type=int, default=0, help="0=all (else cap, smoke run)"
    )
    parser.add_argument(
        "--industry-asof",
        default="",
        help="index_member_all as-of key (default: latest stored snapshot)",
    )
    args = parser.parse_args()

    split = LockedSplit.load(args.lock, args.snapshot_root)
    store = SnapshotStore(args.snapshot_root)
    asof = args.industry_asof or _latest_snapshot_key(
        args.snapshot_root, "index_member_all"
    )
    # Bound the statement report periods to the last rebalance date actually built
    # (the ann_date<d gate keeps them PIT either way). For the full build this is the
    # train_val end; for a --max-rebalances smoke it loads only the head periods.
    rebs = list(split.train_val_dates[::args.rebalance_freq])
    if args.max_rebalances:
        rebs = rebs[: args.max_rebalances]
    last_period_date = rebs[-1] if rebs else split.train_val_dates[-1]
    # Reuse the round-3 PIT readers: fundamentals (roe/gpm) + industry + statements
    # (accr) + PIT ST namechange — identical construction to build_panel_r3.
    inputs = build_r3_inputs(
        store,
        args.snapshot_root,
        last_period_date=last_period_date,
        industry_asof=asof,
    )
    panel = build_defensive_d1_panel(
        split,
        store,
        inputs=inputs,
        rebalance_freq=args.rebalance_freq,
        max_rebalances=args.max_rebalances,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out, index=False)
    log.info("d1_panel_written", out=str(out), rows=len(panel))
    print(
        f"[written: {out}] rows={len(panel)} "
        f"dates={panel['date'].nunique() if len(panel) else 0}"
    )


if __name__ == "__main__":
    main()


__all__ = [
    "D1_PANEL_COLUMNS",
    "aligned_market_window",
    "build_defensive_d1_panel",
    "ingest_market_closes",
]
