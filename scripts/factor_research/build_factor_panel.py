"""Build the cross-sectional factor panel on TRAIN+VAL data only (Phase 3).

Reads the byte-exact PIT snapshots (``daily`` / ``daily_basic`` /
``adj_factor``) and produces a tidy panel of one row per
``(rebalance_date, code)`` with the raw research factors (``factor_lib``) and
the forward returns (the labels) used by the IC study + weight search.

Sacred-split discipline (the core integrity guarantee):
* Every date read is funnelled through :meth:`LockedSplit.assert_not_test`, so
  the held-out test window is physically unreachable here.
* Features for date ``d`` use only bars ``<= d``; forward-return labels use
  bars ``> d`` but stay inside ``train_val + embargo`` (the 20-td embargo was
  sized to exactly cover the 20-td max label horizon — see the lock file), so
  no label ever peeks into test.

PIT / survivorship correctness:
* The per-date ``daily`` frame *is* the tradable universe that day —
  survivorship-unbiased by construction (a code that later delisted is present
  in its era and simply absent afterwards; a later IPO appears only from its
  listing). No ``stock_basic`` lifecycle needed.
* The universal adjusted price is ``adj_close = raw_close * adj_factor`` (hfq).
  Tushare's ``adj_factor`` is the hfq cumulative factor — verified monotonic
  non-decreasing per code (600519: 6.125→8.15 over 2015-2025), i.e.
  **future-invariant** (a past date's factor does not restate when a later
  dividend occurs). Every consumer takes a ratio, so the adjustment reference
  cancels and a return between d1,d2 depends only on corporate actions within
  [d1,d2] — PIT-correct, no future-split contamination, for both trailing
  features and forward labels. (Only the unit-price exclusion uses the RAW
  close — the true tradable price — never the hfq level.)

Exclusions (PIT, applied per rebalance date):
* board whitelist (code-based: drops 科创688 / 北交8 via ``classify_board``);
* sufficient history (>= MIN_HISTORY_BARS bars — also drops the newest IPOs);
* liquidity (mean 20-bar amount >= 2亿) and price (last close <= 500元);
* cross-sectional bottom-30% market-cap exclusion (the Liu-Stambaugh-Yuan
  shell-stock mandate from the Phase 1 survey).

Limitation (documented): without point-in-time names this build does not apply
an explicit ST-name exclusion; ST names concentrate in the micro-cap / illiquid
tail already removed by the bottom-30% size + liquidity filters. The final
strategy can add a PIT name source if the IC study shows it matters.
"""

from __future__ import annotations

import argparse
import io
import math
import statistics
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd
import structlog

# backend.data is a legitimate dependency of this offline research module
# (board classification only); the per-line noqa keeps the global TID251 ban
# ACTIVE for backend.{llm,agents,mirofish}.
from backend.data.stock_metadata import (  # noqa: TID251
    ForbiddenCodeError,
    UnknownCodeError,
    classify_board,
)
from backend.marketdata_snapshot.store import SnapshotStore
from backend.services.universe_policy import BOARD_WHITELIST

from .analyst_revision_pit import (
    LEVEL_WINDOW_DAYS,
    LOOKBACK_DAYS,
    STALENESS_DAYS,
    AnalystRevisionPIT,
)
from .factor_lib import (
    FACTOR_NAMES,
    R2_FACTOR_NAMES,
    R3_FACTOR_NAMES,
    R4_FACTOR_NAMES,
    compute_factor_vector,
    compute_fundamental_factors,
    compute_statement_factors,
    compute_trend_factors,
)
from .fundamentals_pit import FundamentalsPIT
from .industry_pit import IndustryPIT
from .ingest_round2_data import (
    EP_BALANCESHEET,
    EP_CASHFLOW,
    EP_FINA,
    EP_INCOME,
    REPORT_RC_FIRST_YEAR,
    report_periods,
    report_rc_month_ranges,
)
from .locked_split import LockedSplit
from .namechange_pit import NameChangePIT, namechange_snapshot_keys
from .statements_pit import PeriodStatementPIT, StatementRecord

log = structlog.get_logger(component="factor_research.build_panel")

# Need enough trailing bars for every 20-day factor (20-day return needs the
# bar 21 back). Also doubles as the newest-IPO exclusion.
MIN_HISTORY_BARS: int = 21
# Liquidity floor: mean 20-bar traded amount >= 2亿. Tushare daily.amount is in
# 千元, so 2亿元 = 200_000 千元.
MIN_AVG_AMOUNT_THOUSAND_YUAN: float = 200_000.0
MAX_UNIT_PRICE_YUAN: float = 500.0
# Liu-Stambaugh-Yuan: drop the smallest 30% (shell-value contamination).
SIZE_EXCLUDE_QUANTILE: float = 0.30
# Forward-return label horizons (trading-bar based, in the code's own series).
FORWARD_HORIZONS: tuple[int, ...] = (5, 10, 20)
DEFAULT_REBALANCE_FREQ: int = 5  # weekly — a short-term rotation cadence
# Non-test trailing bars prepended before test_start so the FIRST test
# rebalance date has a full 20-day factor + liquidity window (needs >= 21
# bars). These buffer bars are embargo/train (never test) — reading them is
# not a covenant breach; only the test bars themselves are the sanctioned read.
TEST_FEATURE_BUFFER_TD: int = 30


@dataclass
class _CodeSeries:
    """One code's PIT series over the feature window (oldest → newest)."""

    ts_code: str = ""  # full Tushare code (600519.SH) for fundamentals/industry join
    dates: list[str] = field(default_factory=list)
    adj_close: list[float] = field(default_factory=list)  # raw * adj_factor (hfq)
    raw_close: list[float] = field(default_factory=list)  # unadjusted, for price filter
    amount: list[float] = field(default_factory=list)
    turnover: list[float] = field(default_factory=list)
    pe_ttm: list[float] = field(default_factory=list)
    circ_mv: list[float] = field(default_factory=list)
    pos_of_date: dict[str, int] = field(default_factory=dict)


@cache
def _board_ok(code: str) -> bool:
    """True iff the code is on the tradable board whitelist (PIT, code-based).

    Cached: the board of a code never changes, and it is queried once per
    code per trading day (~13M times over the full build).
    """
    try:
        board = classify_board(code)
    except (ForbiddenCodeError, UnknownCodeError):
        return False
    return board.value in BOARD_WHITELIST


def _ingest_day(store: SnapshotStore, day: str, series: dict[str, _CodeSeries]) -> int:
    """Parse one day's 3 frames and append to each code's series.

    Returns the number of codes appended. A code is appended only when it has
    a ``daily`` bar AND an ``adj_factor`` AND a ``daily_basic`` row that day
    (fail-closed: a code missing any leg is skipped, never fabricated).
    """
    daily_s = store.latest(vendor="tushare", endpoint="daily", trade_date=day)
    adj_s = store.latest(vendor="tushare", endpoint="adj_factor", trade_date=day)
    basic_s = store.latest(vendor="tushare", endpoint="daily_basic", trade_date=day)
    if daily_s is None or adj_s is None or basic_s is None:
        return 0
    # Parse only the needed columns (much faster than the full frame), then
    # vectorised join + filter; the per-row loop runs over ~2k board-ok bars.
    daily = pd.read_csv(
        io.BytesIO(daily_s.raw_payload), usecols=["ts_code", "close", "amount"]
    )
    adj = pd.read_csv(io.BytesIO(adj_s.raw_payload), usecols=["ts_code", "adj_factor"])
    basic = pd.read_csv(
        io.BytesIO(basic_s.raw_payload),
        usecols=["ts_code", "turnover_rate", "pe_ttm", "circ_mv"],
    )
    # inner-join daily↔adj (a real bar needs both); left-join daily_basic
    # (turnover/pe/circ_mv may be NaN — handled fail-closed downstream).
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
        if not cs.ts_code:  # set once — the full code never changes per 6-digit
            cs.ts_code = str(r.ts_code)
        cs.pos_of_date[day] = len(cs.dates)
        cs.dates.append(day)
        cs.adj_close.append(float(r.adj_close))
        cs.raw_close.append(float(r.close))
        cs.amount.append(float(r.amount))
        cs.turnover.append(float(r.turnover_rate))
        cs.pe_ttm.append(float(r.pe_ttm))
        cs.circ_mv.append(float(r.circ_mv))
    return len(m)


def _forward_returns(adj_close: list[float], pos: int) -> dict[str, float | None]:
    """Forward returns at each horizon from ``pos`` (None if not enough bars)."""
    base = adj_close[pos]
    out: dict[str, float | None] = {}
    for h in FORWARD_HORIZONS:
        nxt = pos + h
        if nxt < len(adj_close) and base > 0:
            fwd = adj_close[nxt]
            out[f"fwd_ret_{h}d"] = (fwd / base - 1.0) if fwd == fwd else None
        else:
            out[f"fwd_ret_{h}d"] = None
    return out


def _passes_liquidity_price(cs: _CodeSeries, pos: int) -> bool:
    """Liquidity + unit-price exclusions from the trailing window at ``pos``."""
    if pos + 1 < MIN_HISTORY_BARS:
        return False
    amt_window = cs.amount[pos + 1 - 20 : pos + 1]
    if len(amt_window) < 20:
        return False
    if statistics.fmean(amt_window) < MIN_AVG_AMOUNT_THOUSAND_YUAN:
        return False
    # Filter on the RAW (unadjusted) price — the actual tradable price. The
    # hfq adj_close would be wrong here: adj_factor ranges ~0.6..8 across
    # stocks, so an adjusted-price cap would bias the universe by dividend
    # history rather than true unit price.
    last_price = cs.raw_close[pos]
    return 0 < last_price <= MAX_UNIT_PRICE_YUAN


def _ingest_series(
    store: SnapshotStore, feature_dates: list[str]
) -> dict[str, _CodeSeries]:
    """Stream the 3 frames per day into per-code PIT series (oldest → newest)."""
    series: dict[str, _CodeSeries] = defaultdict(_CodeSeries)
    for i, day in enumerate(feature_dates):
        _ingest_day(store, day, series)
        if i % 250 == 0:
            log.info("panel_ingest_progress", day=day, codes=len(series))
    return series


def _cohort(
    series: dict[str, _CodeSeries],
    day: str,
    *,
    st_filter: Callable[[str, str], bool] | None = None,
) -> tuple[list[tuple[str, _CodeSeries, int]], float] | None:
    """The day's investable cross-section + the bottom-30% market-cap cutoff.

    Codes with a bar today that pass the liquidity / unit-price / finite-size
    filters; ``None`` when the cross-section is too thin (<20) to rank. The
    bottom-30% size cut (Liu-Stambaugh-Yuan shell mandate) is returned for the
    caller to apply per code. ``st_filter(ts_code, day) -> True`` excludes a
    point-in-time ST / 退 name BEFORE the size cut (R3-2; default ``None`` keeps
    the round-1/round-2 cohort byte-identical).
    """
    cohort: list[tuple[str, _CodeSeries, int]] = []
    for code, cs in series.items():
        pos = cs.pos_of_date.get(day)
        if pos is None or not _passes_liquidity_price(cs, pos):
            continue
        if not math.isfinite(cs.circ_mv[pos]):  # need a size to rank on
            continue
        if st_filter is not None and st_filter(cs.ts_code, day):
            continue  # PIT ST exclusion (hard, like the board whitelist)
        cohort.append((code, cs, pos))
    if len(cohort) < 20:  # too thin a cross-section to rank
        return None
    cmvs = [cs.circ_mv[pos] for _, cs, pos in cohort]
    return cohort, _quantile(cmvs, SIZE_EXCLUDE_QUANTILE)


def _build_rows(
    series: dict[str, _CodeSeries], rebalance_dates: list[str]
) -> list[dict[str, object]]:
    """One row per (rebalance-date × surviving code): features <= d, labels > d."""
    rows: list[dict[str, object]] = []
    for day in rebalance_dates:
        selected = _cohort(series, day)
        if selected is None:
            continue
        cohort, cmv_cut = selected
        for code, cs, pos in cohort:
            if cs.circ_mv[pos] < cmv_cut:
                continue
            vec = compute_factor_vector(
                closes=cs.adj_close[: pos + 1],
                amounts=cs.amount[: pos + 1],
                turnover_rates=cs.turnover[: pos + 1],
                pe_ttm=cs.pe_ttm[pos],
            )
            fwd = _forward_returns(cs.adj_close, pos)
            row: dict[str, object] = {"date": day, "code": code}
            row.update(vec.as_dict())
            row.update(fwd)
            rows.append(row)
        log.info("panel_rebalance", day=day, cohort=len(cohort), rows=len(rows))
    return rows


def _panel_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Assemble the tidy panel frame with the canonical column order."""
    cols = ["date", "code", *FACTOR_NAMES, *(f"fwd_ret_{h}d" for h in FORWARD_HORIZONS)]
    return pd.DataFrame(rows, columns=cols)


def build_panel(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    max_rebalances: int = 0,
) -> pd.DataFrame:
    """Build the train_val factor panel (one row per rebalance-date × code)."""
    # Feature window = train_val + embargo (embargo gives forward-label room),
    # strictly excluding test. Build per-code series streaming once over dates.
    feature_dates = [*split.train_val_dates, *split.embargo_dates]
    split.assert_all_not_test(feature_dates)  # hard guard: never touch test
    series = _ingest_series(store, feature_dates)
    # Rebalance only on train_val dates (labels may extend into embargo).
    rebalance_dates = list(split.train_val_dates[::rebalance_freq])
    if max_rebalances:
        rebalance_dates = rebalance_dates[:max_rebalances]
    return _panel_frame(_build_rows(series, rebalance_dates))


def build_test_panel(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
) -> pd.DataFrame:
    """PHASE 4 ONE-SHOT — the SOLE sanctioned reader of the sacred test window.

    Unlike :func:`build_panel` (which fails closed on any test date), this
    deliberately reads test bars: at each test rebalance date ``d`` the features
    use bars ``<= d`` (a :data:`TEST_FEATURE_BUFFER_TD` buffer of non-test
    history before test_start, then test bars up to ``d``) and the forward-return
    labels use test bars ``> d``. Rebalances ONLY on test dates. The test-set
    covenant permits exactly one such evaluation, for the strategy frozen in
    Phase 3 — never call this during strategy development.
    """
    pre_test = [*split.train_val_dates, *split.embargo_dates]  # all non-test
    buffer = list(pre_test[-TEST_FEATURE_BUFFER_TD:])
    feature_dates = [*buffer, *split.test_dates]
    log.warning(
        "phase4_test_panel_build",
        note="reading the SACRED test window (one-shot, sanctioned)",
        buffer_td=len(buffer),
        test_td=len(split.test_dates),
        test_start=split.test_dates[0],
        test_end=split.test_dates[-1],
    )
    series = _ingest_series(store, feature_dates)
    rebalance_dates = list(split.test_dates[::rebalance_freq])
    return _panel_frame(_build_rows(series, rebalance_dates))


# ===========================================================================
# Round-2 panel (R2-2): round-1 factors + trend/quality/growth + the
# neutralization inputs (PIT industry L1 + log market cap). Written to a
# SEPARATE file (panel_train_val_r2.csv); the round-1 build_panel /
# build_test_panel paths above are byte-for-byte unchanged.
# ===========================================================================

# Extra (non-factor) columns the round-2 panel carries for neutralization.
R2_PANEL_EXTRA_COLS: tuple[str, ...] = ("industry_l1", "circ_mv", "log_circ_mv")


class _FundamentalRecordLike(Protocol):
    """Structural type for a PIT fundamentals record (decouples from the class)."""

    def get(self, field: str) -> float | None: ...


class _FundamentalsLookup(Protocol):
    """Injected PIT fundamentals lookup (satisfied by ``FundamentalsPIT``)."""

    def asof(
        self, code: str, decision_date: str, *, extra_lag_days: int = 0
    ) -> _FundamentalRecordLike | None: ...


class _IndustryLookup(Protocol):
    """Injected PIT industry lookup (satisfied by ``IndustryPIT``)."""

    def l1_asof(self, code: str, decision_date: str) -> str | None: ...


def _build_rows_r2(
    series: dict[str, _CodeSeries],
    rebalance_dates: list[str],
    *,
    fundamentals: _FundamentalsLookup,
    industry: _IndustryLookup,
    extra_lag_days: int,
) -> list[dict[str, object]]:
    """Round-2 rows: round-1 + trend + quality/growth + neutralization inputs.

    Same investable cross-section + bottom-30% size cut as :func:`_build_rows`.
    Fundamentals/industry are joined by the full ``ts_code`` as-of ``day`` (the
    fundamentals lookup is ann_date-gated; the industry lookup is in/out-date
    gated) — both PIT and never reading beyond ``day``.
    """
    rows: list[dict[str, object]] = []
    for day in rebalance_dates:
        selected = _cohort(series, day)
        if selected is None:
            continue
        cohort, cmv_cut = selected
        for code, cs, pos in cohort:
            if cs.circ_mv[pos] < cmv_cut:
                continue
            closes = cs.adj_close[: pos + 1]
            vec = compute_factor_vector(
                closes=closes,
                amounts=cs.amount[: pos + 1],
                turnover_rates=cs.turnover[: pos + 1],
                pe_ttm=cs.pe_ttm[pos],
            )
            record = fundamentals.asof(cs.ts_code, day, extra_lag_days=extra_lag_days)
            circ = cs.circ_mv[pos]
            row: dict[str, object] = {
                "date": day,
                "code": code,
                "ts_code": cs.ts_code,
            }
            row.update(vec.as_dict())
            row.update(compute_trend_factors(closes))
            row.update(compute_fundamental_factors(record))
            row["industry_l1"] = industry.l1_asof(cs.ts_code, day)
            row["circ_mv"] = circ
            row["log_circ_mv"] = (
                math.log(circ) if (math.isfinite(circ) and circ > 0) else None
            )
            row.update(_forward_returns(cs.adj_close, pos))
            rows.append(row)
        log.info("panel_r2_rebalance", day=day, cohort=len(cohort), rows=len(rows))
    return rows


def _panel_frame_r2(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Assemble the round-2 panel frame with the canonical column order."""
    cols = [
        "date",
        "code",
        "ts_code",
        *FACTOR_NAMES,
        *R2_FACTOR_NAMES,
        *R2_PANEL_EXTRA_COLS,
        *(f"fwd_ret_{h}d" for h in FORWARD_HORIZONS),
    ]
    return pd.DataFrame(rows, columns=cols)


def build_panel_r2(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    fundamentals: _FundamentalsLookup,
    industry: _IndustryLookup,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    max_rebalances: int = 0,
    extra_lag_days: int = 0,
) -> pd.DataFrame:
    """Build the round-2 train_val panel (raw factors + neutralization inputs).

    Same sacred-split discipline as :func:`build_panel` (feature window =
    train_val + embargo, ``assert_all_not_test`` guard); fundamentals/industry
    are PIT and gated by ``day`` so no test bar is ever read during development.
    """
    feature_dates = [*split.train_val_dates, *split.embargo_dates]
    split.assert_all_not_test(feature_dates)  # hard guard: never touch test
    series = _ingest_series(store, feature_dates)
    rebalance_dates = list(split.train_val_dates[::rebalance_freq])
    if max_rebalances:
        rebalance_dates = rebalance_dates[:max_rebalances]
    return _panel_frame_r2(
        _build_rows_r2(
            series,
            rebalance_dates,
            fundamentals=fundamentals,
            industry=industry,
            extra_lag_days=extra_lag_days,
        )
    )


def build_test_panel_r2(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    fundamentals: _FundamentalsLookup,
    industry: _IndustryLookup,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    extra_lag_days: int = 0,
) -> pd.DataFrame:
    """R2-6 ONE-SHOT — the SOLE sanctioned reader of the sacred test window for round-2.

    The round-2 analogue of :func:`build_test_panel`. Unlike :func:`build_panel_r2`
    (which fails closed on any test date), this deliberately reads test bars: at
    each test rebalance date ``d`` the features use bars ``<= d`` (a
    :data:`TEST_FEATURE_BUFFER_TD` buffer of non-test history before test_start,
    then test bars up to ``d``) and the forward-return labels use test bars
    ``> d``. Fundamentals/industry remain PIT (ann_date / in-out-date gated by
    ``d``), so reading them for a test date is the sanctioned test read, NOT
    leakage. Rebalances ONLY on test dates. The test-set covenant permits exactly
    one such evaluation, for the strategy git-frozen in R2-5 — never call this
    during strategy development.
    """
    pre_test = [*split.train_val_dates, *split.embargo_dates]  # all non-test
    buffer = list(pre_test[-TEST_FEATURE_BUFFER_TD:])
    feature_dates = [*buffer, *split.test_dates]
    log.warning(
        "r2_locked_test_panel_build",
        note="reading the SACRED test window (round-2 one-shot, sanctioned)",
        buffer_td=len(buffer),
        test_td=len(split.test_dates),
        test_start=split.test_dates[0],
        test_end=split.test_dates[-1],
    )
    series = _ingest_series(store, feature_dates)
    rebalance_dates = list(split.test_dates[::rebalance_freq])
    return _panel_frame_r2(
        _build_rows_r2(
            series,
            rebalance_dates,
            fundamentals=fundamentals,
            industry=industry,
            extra_lag_days=extra_lag_days,
        )
    )


# ===========================================================================
# Round-3 panel (R3-2): round-2 columns + earnings-surprise / accruals /
# asset-growth (PIT statements) + a PIT ST exclusion. Written to a SEPARATE
# file (panel_train_val_r3.csv); build_panel_r2 / build_test_panel_r2 above are
# byte-for-byte unchanged.
# ===========================================================================


class _StatementLookup(Protocol):
    """Injected PIT statement lookup (satisfied by ``PeriodStatementPIT``)."""

    def as_known(
        self, code: str, decision_date: str, *, extra_lag_days: int = 0
    ) -> dict[str, StatementRecord]: ...


class _StLookup(Protocol):
    """Injected PIT ST-name lookup (satisfied by ``NameChangePIT``)."""

    def is_st_asof(self, code: str, decision_date: str) -> bool: ...


def _field_series(
    known: dict[str, StatementRecord], field: str
) -> dict[str, float | None]:
    """``{end_date: value}`` for one field across a code's as-known records."""
    return {end: rec.get(field) for end, rec in known.items()}


def _build_rows_r3(
    series: dict[str, _CodeSeries],
    rebalance_dates: list[str],
    *,
    fundamentals: _FundamentalsLookup,
    industry: _IndustryLookup,
    fina_stmt: _StatementLookup,
    income: _StatementLookup,
    cashflow: _StatementLookup,
    balancesheet: _StatementLookup,
    namechange: _StLookup,
    extra_lag_days: int,
) -> list[dict[str, object]]:
    """Round-3 rows: round-2 columns + SUE/accruals/asset-growth + PIT ST drop.

    Same investable cross-section + bottom-30% size cut as :func:`_build_rows_r2`,
    PLUS a point-in-time ST exclusion in the cohort. The three statement factors
    are computed from the PIT readers (all ann_date-gated by ``day`` — never
    reading beyond it). Deliberately parallel to ``_build_rows_r2`` rather than
    sharing its body, so the round-2 builder stays byte-frozen.
    """
    rows: list[dict[str, object]] = []
    for day in rebalance_dates:
        selected = _cohort(series, day, st_filter=namechange.is_st_asof)
        if selected is None:
            continue
        cohort, cmv_cut = selected
        for code, cs, pos in cohort:
            if cs.circ_mv[pos] < cmv_cut:
                continue
            closes = cs.adj_close[: pos + 1]
            vec = compute_factor_vector(
                closes=closes,
                amounts=cs.amount[: pos + 1],
                turnover_rates=cs.turnover[: pos + 1],
                pe_ttm=cs.pe_ttm[pos],
            )
            record = fundamentals.asof(cs.ts_code, day, extra_lag_days=extra_lag_days)
            stmt = compute_statement_factors(
                profit_dedt_ytd=_field_series(
                    fina_stmt.as_known(cs.ts_code, day, extra_lag_days=extra_lag_days),
                    "profit_dedt",
                ),
                n_income_ytd=_field_series(
                    income.as_known(cs.ts_code, day, extra_lag_days=extra_lag_days),
                    "n_income",
                ),
                cfo_ytd=_field_series(
                    cashflow.as_known(cs.ts_code, day, extra_lag_days=extra_lag_days),
                    "n_cashflow_act",
                ),
                total_assets=_field_series(
                    balancesheet.as_known(
                        cs.ts_code, day, extra_lag_days=extra_lag_days
                    ),
                    "total_assets",
                ),
            )
            circ = cs.circ_mv[pos]
            row: dict[str, object] = {
                "date": day,
                "code": code,
                "ts_code": cs.ts_code,
            }
            row.update(vec.as_dict())
            row.update(compute_trend_factors(closes))
            row.update(compute_fundamental_factors(record))
            row.update(stmt)
            row["industry_l1"] = industry.l1_asof(cs.ts_code, day)
            row["circ_mv"] = circ
            row["log_circ_mv"] = (
                math.log(circ) if (math.isfinite(circ) and circ > 0) else None
            )
            row.update(_forward_returns(cs.adj_close, pos))
            rows.append(row)
        log.info("panel_r3_rebalance", day=day, cohort=len(cohort), rows=len(rows))
    return rows


def _panel_frame_r3(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Assemble the round-3 panel frame with the canonical column order."""
    cols = [
        "date",
        "code",
        "ts_code",
        *FACTOR_NAMES,
        *R2_FACTOR_NAMES,
        *R3_FACTOR_NAMES,
        *R2_PANEL_EXTRA_COLS,
        *(f"fwd_ret_{h}d" for h in FORWARD_HORIZONS),
    ]
    return pd.DataFrame(rows, columns=cols)


@dataclass(frozen=True)
class _R3Inputs:
    """The PIT readers a round-3 panel build injects (immutable)."""

    fundamentals: _FundamentalsLookup
    industry: _IndustryLookup
    fina_stmt: _StatementLookup
    income: _StatementLookup
    cashflow: _StatementLookup
    balancesheet: _StatementLookup
    namechange: _StLookup


def build_panel_r3(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    inputs: _R3Inputs,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    max_rebalances: int = 0,
    extra_lag_days: int = 0,
) -> pd.DataFrame:
    """Build the round-3 train_val panel (round-2 columns + R3 factors + PIT ST).

    Same sacred-split discipline as :func:`build_panel_r2` (feature window =
    train_val + embargo, ``assert_all_not_test`` guard); fundamentals / industry /
    statements / namechange are PIT and gated by ``day`` so no test bar is read.
    """
    feature_dates = [*split.train_val_dates, *split.embargo_dates]
    split.assert_all_not_test(feature_dates)  # hard guard: never touch test
    series = _ingest_series(store, feature_dates)
    rebalance_dates = list(split.train_val_dates[::rebalance_freq])
    if max_rebalances:
        rebalance_dates = rebalance_dates[:max_rebalances]
    return _panel_frame_r3(
        _build_rows_r3(
            series,
            rebalance_dates,
            fundamentals=inputs.fundamentals,
            industry=inputs.industry,
            fina_stmt=inputs.fina_stmt,
            income=inputs.income,
            cashflow=inputs.cashflow,
            balancesheet=inputs.balancesheet,
            namechange=inputs.namechange,
            extra_lag_days=extra_lag_days,
        )
    )


def build_test_panel_r3(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    inputs: _R3Inputs,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    extra_lag_days: int = 0,
) -> pd.DataFrame:
    """R3-6 ONE-SHOT — the SOLE sanctioned reader of the sacred test window for round-3.

    The round-3 analogue of :func:`build_test_panel_r2`. Reads test bars (features
    ``<= d`` from a :data:`TEST_FEATURE_BUFFER_TD` buffer + test bars to ``d``;
    forward labels from test bars ``> d``); statements / industry / fundamentals /
    namechange stay PIT (gated by ``d``). Rebalances ONLY on test dates. The
    covenant permits exactly one such evaluation, for the strategy git-frozen in
    R3-5 — never call this during strategy development.
    """
    pre_test = [*split.train_val_dates, *split.embargo_dates]  # all non-test
    buffer = list(pre_test[-TEST_FEATURE_BUFFER_TD:])
    feature_dates = [*buffer, *split.test_dates]
    log.warning(
        "r3_locked_test_panel_build",
        note="reading the SACRED test window (round-3 one-shot, sanctioned)",
        buffer_td=len(buffer),
        test_td=len(split.test_dates),
        test_start=split.test_dates[0],
        test_end=split.test_dates[-1],
    )
    series = _ingest_series(store, feature_dates)
    rebalance_dates = list(split.test_dates[::rebalance_freq])
    return _panel_frame_r3(
        _build_rows_r3(
            series,
            rebalance_dates,
            fundamentals=inputs.fundamentals,
            industry=inputs.industry,
            fina_stmt=inputs.fina_stmt,
            income=inputs.income,
            cashflow=inputs.cashflow,
            balancesheet=inputs.balancesheet,
            namechange=inputs.namechange,
            extra_lag_days=extra_lag_days,
        )
    )


def build_r3_inputs(
    store: SnapshotStore,
    snapshot_root: str,
    *,
    last_period_date: str,
    industry_asof: str = "",
) -> _R3Inputs:
    """Construct the round-3 PIT readers from the stored snapshots (R3-2 / R3-6).

    ``last_period_date`` bounds the fundamentals/statement report periods (the
    ann_date<d gate keeps them PIT regardless). Shared by the train_val build and
    the R3-6 one-shot so both read identically-constructed readers.
    """
    periods = report_periods(2015, last_period_date)
    fundamentals = FundamentalsPIT.build(store, periods)
    asof = industry_asof or _latest_snapshot_key(snapshot_root, "index_member_all")
    industry = IndustryPIT.build(store, asof)
    fina_stmt = PeriodStatementPIT.build(
        store,
        periods,
        endpoint=EP_FINA,
        fields=["profit_dedt"],
        report_type_filter=None,
    )
    income = PeriodStatementPIT.build(
        store, periods, endpoint=EP_INCOME, fields=["n_income"]
    )
    cashflow = PeriodStatementPIT.build(
        store, periods, endpoint=EP_CASHFLOW, fields=["n_cashflow_act"]
    )
    balancesheet = PeriodStatementPIT.build(
        store, periods, endpoint=EP_BALANCESHEET, fields=["total_assets"]
    )
    namechange = NameChangePIT.build(store, namechange_snapshot_keys(snapshot_root))
    log.info(
        "panel_r3_inputs",
        fina_periods=len(periods),
        fundamentals_codes=len(fundamentals.by_code),
        income_codes=len(income.by_code),
        balancesheet_codes=len(balancesheet.by_code),
        namechange_codes=len(namechange.by_code),
        industry_asof=asof,
    )
    return _R3Inputs(
        fundamentals=fundamentals,
        industry=industry,
        fina_stmt=fina_stmt,
        income=income,
        cashflow=cashflow,
        balancesheet=balancesheet,
        namechange=namechange,
    )


# ===========================================================================
# Round-4 panel (R4-3): round-3 columns + analyst-revision factors (report_rc
# PIT). Written to a SEPARATE file (panel_train_val_r4.csv); build_panel_r3 /
# build_test_panel_r3 above are byte-for-byte unchanged. The R4_CARRY search
# (R4-5) consumes ALL carry columns' *_neut, so the r4 panel carries the full
# round-1/2/3 factor set PLUS the new analyst columns.
# ===========================================================================


class _AnalystLookup(Protocol):
    """Injected PIT analyst-revision lookup (satisfied by ``AnalystRevisionPIT``)."""

    def factors(
        self,
        code: str,
        decision_date: str,
        *,
        close: float | None,
        staleness_days: int = ...,
        lookback_days: int = ...,
        level_window_days: int = ...,
    ) -> dict[str, float | None]: ...


@dataclass(frozen=True)
class _R4Inputs:
    """The PIT readers a round-4 panel build injects (round-3 set + analyst)."""

    r3: _R3Inputs
    analyst: _AnalystLookup


def report_rc_month_keys(
    report_rc_last_date: str,
    *,
    test_start: str,
    sanctioned_test_read: bool,
) -> list[str]:
    """report_rc month snapshot keys through ``report_rc_last_date`` (firewalled).

    FIREWALL: unless ``sanctioned_test_read`` (the R4-6 one-shot), every key MUST
    be ``< test_start`` — a development build can never load a test-era analyst
    report. Raises :class:`RuntimeError` fail-closed otherwise. Pure (no I/O), so
    the firewall is unit-testable without a store.
    """
    ranges = report_rc_month_ranges(REPORT_RC_FIRST_YEAR, report_rc_last_date)
    keys = [k for _, _, k in ranges]
    leaked = [k for k in keys if k >= test_start]
    if leaked and not sanctioned_test_read:
        raise RuntimeError(
            f"report_rc firewall: {len(leaked)} month key(s) >= test_start "
            f"{test_start} (e.g. {leaked[0]}) — a development build must load only "
            "pre-test analyst months. Refusing to materialise the sealed window."
        )
    if sanctioned_test_read and leaked:
        log.warning(
            "r4_analyst_sanctioned_test_read",
            months=len(keys),
            test_era_months=len(leaked),
            last=keys[-1] if keys else "-",
        )
    return keys


def _build_rows_r4(
    series: dict[str, _CodeSeries],
    rebalance_dates: list[str],
    *,
    inputs: _R4Inputs,
    extra_lag_days: int,
    staleness_days: int,
    lookback_days: int,
    level_window_days: int,
) -> list[dict[str, object]]:
    """Round-4 rows: round-3 columns + the seven analyst-revision factors.

    Same investable cross-section + bottom-30% size cut + PIT ST exclusion as
    :func:`_build_rows_r3`. The analyst factors are joined by the full ``ts_code``
    as-of ``day`` (the analyst reader is ``report_date < day`` gated — PIT, never
    reading beyond ``day``) using the code's RAW close on ``day`` (the
    target-price factor compares an absolute target price to the tradable price).
    Deliberately parallel to ``_build_rows_r3`` so the round-3 builder stays
    byte-frozen.
    """
    r3 = inputs.r3
    fundamentals = r3.fundamentals
    industry = r3.industry
    fina_stmt = r3.fina_stmt
    income = r3.income
    cashflow = r3.cashflow
    balancesheet = r3.balancesheet
    namechange = r3.namechange
    rows: list[dict[str, object]] = []
    for day in rebalance_dates:
        selected = _cohort(series, day, st_filter=namechange.is_st_asof)
        if selected is None:
            continue
        cohort, cmv_cut = selected
        for code, cs, pos in cohort:
            if cs.circ_mv[pos] < cmv_cut:
                continue
            closes = cs.adj_close[: pos + 1]
            vec = compute_factor_vector(
                closes=closes,
                amounts=cs.amount[: pos + 1],
                turnover_rates=cs.turnover[: pos + 1],
                pe_ttm=cs.pe_ttm[pos],
            )
            record = fundamentals.asof(cs.ts_code, day, extra_lag_days=extra_lag_days)
            stmt = compute_statement_factors(
                profit_dedt_ytd=_field_series(
                    fina_stmt.as_known(cs.ts_code, day, extra_lag_days=extra_lag_days),
                    "profit_dedt",
                ),
                n_income_ytd=_field_series(
                    income.as_known(cs.ts_code, day, extra_lag_days=extra_lag_days),
                    "n_income",
                ),
                cfo_ytd=_field_series(
                    cashflow.as_known(cs.ts_code, day, extra_lag_days=extra_lag_days),
                    "n_cashflow_act",
                ),
                total_assets=_field_series(
                    balancesheet.as_known(
                        cs.ts_code, day, extra_lag_days=extra_lag_days
                    ),
                    "total_assets",
                ),
            )
            analyst = inputs.analyst.factors(
                cs.ts_code,
                day,
                close=cs.raw_close[pos],
                staleness_days=staleness_days,
                lookback_days=lookback_days,
                level_window_days=level_window_days,
            )
            circ = cs.circ_mv[pos]
            row: dict[str, object] = {
                "date": day,
                "code": code,
                "ts_code": cs.ts_code,
            }
            row.update(vec.as_dict())
            row.update(compute_trend_factors(closes))
            row.update(compute_fundamental_factors(record))
            row.update(stmt)
            row.update(analyst)
            row["industry_l1"] = industry.l1_asof(cs.ts_code, day)
            row["circ_mv"] = circ
            row["log_circ_mv"] = (
                math.log(circ) if (math.isfinite(circ) and circ > 0) else None
            )
            row.update(_forward_returns(cs.adj_close, pos))
            rows.append(row)
        log.info("panel_r4_rebalance", day=day, cohort=len(cohort), rows=len(rows))
    return rows


def _panel_frame_r4(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Assemble the round-4 panel frame with the canonical column order."""
    cols = [
        "date",
        "code",
        "ts_code",
        *FACTOR_NAMES,
        *R2_FACTOR_NAMES,
        *R3_FACTOR_NAMES,
        *R4_FACTOR_NAMES,
        *R2_PANEL_EXTRA_COLS,
        *(f"fwd_ret_{h}d" for h in FORWARD_HORIZONS),
    ]
    return pd.DataFrame(rows, columns=cols)


def build_panel_r4(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    inputs: _R4Inputs,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    max_rebalances: int = 0,
    extra_lag_days: int = 0,
    staleness_days: int = STALENESS_DAYS,
    lookback_days: int = LOOKBACK_DAYS,
    level_window_days: int = LEVEL_WINDOW_DAYS,
) -> pd.DataFrame:
    """Build the round-4 train_val panel (round-3 columns + analyst factors).

    Same sacred-split discipline as :func:`build_panel_r3` (feature window =
    train_val + embargo, ``assert_all_not_test`` guard); the analyst reader is
    constructed from report_rc months strictly ``< test_start`` (firewall in
    :func:`build_r4_inputs`) and is ``report_date < day`` gated, so no test bar
    or test-era report is read during development.
    """
    feature_dates = [*split.train_val_dates, *split.embargo_dates]
    split.assert_all_not_test(feature_dates)  # hard guard: never touch test
    series = _ingest_series(store, feature_dates)
    rebalance_dates = list(split.train_val_dates[::rebalance_freq])
    if max_rebalances:
        rebalance_dates = rebalance_dates[:max_rebalances]
    return _panel_frame_r4(
        _build_rows_r4(
            series,
            rebalance_dates,
            inputs=inputs,
            extra_lag_days=extra_lag_days,
            staleness_days=staleness_days,
            lookback_days=lookback_days,
            level_window_days=level_window_days,
        )
    )


def build_test_panel_r4(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    inputs: _R4Inputs,
    rebalance_freq: int = DEFAULT_REBALANCE_FREQ,
    extra_lag_days: int = 0,
    staleness_days: int = STALENESS_DAYS,
    lookback_days: int = LOOKBACK_DAYS,
    level_window_days: int = LEVEL_WINDOW_DAYS,
) -> pd.DataFrame:
    """R4-6 ONE-SHOT — the SOLE sanctioned reader of the sacred test window for round-4.

    The round-4 analogue of :func:`build_test_panel_r3`. Reads test bars (features
    ``<= d`` from a :data:`TEST_FEATURE_BUFFER_TD` buffer + test bars to ``d``;
    forward labels from test bars ``> d``); statements / industry / fundamentals /
    namechange / analyst stay PIT (gated by ``d``). The R4-6 runner constructs
    ``inputs`` via :func:`build_r4_inputs` with ``sanctioned_test_read=True``
    (report_rc months through test_end); rebalances ONLY on test dates. The
    covenant permits exactly one such evaluation, for the strategy git-frozen in
    R4-5 — never call during development.
    """
    pre_test = [*split.train_val_dates, *split.embargo_dates]  # all non-test
    buffer = list(pre_test[-TEST_FEATURE_BUFFER_TD:])
    feature_dates = [*buffer, *split.test_dates]
    log.warning(
        "r4_locked_test_panel_build",
        note="reading the SACRED test window (round-4 one-shot, sanctioned)",
        buffer_td=len(buffer),
        test_td=len(split.test_dates),
        test_start=split.test_dates[0],
        test_end=split.test_dates[-1],
    )
    series = _ingest_series(store, feature_dates)
    rebalance_dates = list(split.test_dates[::rebalance_freq])
    return _panel_frame_r4(
        _build_rows_r4(
            series,
            rebalance_dates,
            inputs=inputs,
            extra_lag_days=extra_lag_days,
            staleness_days=staleness_days,
            lookback_days=lookback_days,
            level_window_days=level_window_days,
        )
    )


def build_r4_inputs(
    store: SnapshotStore,
    snapshot_root: str,
    *,
    last_period_date: str,
    report_rc_last_date: str,
    test_start: str,
    sanctioned_test_read: bool = False,
    industry_asof: str = "",
) -> _R4Inputs:
    """Construct the round-4 PIT readers (round-3 set + analyst-revision).

    ``report_rc_last_date`` bounds the report_rc month snapshots loaded
    (``report_date < day`` keeps them PIT regardless). FIREWALL: unless
    ``sanctioned_test_read`` (the R4-6 one-shot), every loaded month key MUST be
    ``< test_start`` — a development build can never materialise a test-era
    analyst report. Raises :class:`RuntimeError` fail-closed if it would.
    """
    r3 = build_r3_inputs(
        store,
        snapshot_root,
        last_period_date=last_period_date,
        industry_asof=industry_asof,
    )
    month_keys = report_rc_month_keys(
        report_rc_last_date,
        test_start=test_start,
        sanctioned_test_read=sanctioned_test_read,
    )
    analyst = AnalystRevisionPIT.build(store, month_keys)
    log.info(
        "panel_r4_inputs",
        report_rc_months=len(month_keys),
        analyst_codes=len(analyst.by_code),
        sanctioned_test_read=sanctioned_test_read,
    )
    return _R4Inputs(r3=r3, analyst=analyst)


def _latest_snapshot_key(snapshot_root: str, endpoint: str) -> str:
    """Highest ``trade_date`` stored for ``endpoint`` (for the as-of CLI default).

    Reads the snapshot index directly (no backend import beyond the store path).
    """
    index_path = Path(snapshot_root) / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"snapshot index not found: {index_path}")
    import json

    keys: set[str] = set()
    with index_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("endpoint") == endpoint:
                keys.add(str(rec["trade_date"]))
    if not keys:
        raise FileNotFoundError(f"no {endpoint} snapshot in {snapshot_root}")
    return max(keys)


def _quantile(values: list[float], q: float) -> float:
    """Lower-interpolated q-quantile (deterministic; empty → -inf)."""
    if not values:
        return float("-inf")
    s = sorted(values)
    idx = int(q * (len(s) - 1))
    return s[idx]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument("--rebalance-freq", type=int, default=DEFAULT_REBALANCE_FREQ)
    parser.add_argument(
        "--mode",
        choices=("train_val", "test"),
        default="train_val",
        help="test = PHASE 4 ONE-SHOT sanctioned read of the sacred test window",
    )
    parser.add_argument(
        "--factor-set",
        choices=("r1", "r2", "r3", "r4"),
        default="r1",
        help="r2 = round-2 panel (+trend/quality/growth + PIT industry/size); "
        "r3 = round-2 columns + SUE/accruals/asset-growth + PIT ST exclusion; "
        "r4 = round-3 columns + analyst-revision factors (report_rc PIT) "
        "(train_val only) → panel_train_val_r4.csv",
    )
    parser.add_argument(
        "--industry-asof",
        default="",
        help="index_member_all as-of key (default: latest stored snapshot)",
    )
    parser.add_argument(
        "--fund-lag-days",
        type=int,
        default=0,
        help="extra calendar-day lag on fundamentals availability (r2)",
    )
    parser.add_argument(
        "--staleness-days", type=int, default=STALENESS_DAYS,
        help="analyst-revision staleness window N (r4; R4-4 sweeps 90/180)",
    )
    parser.add_argument(
        "--lookback-days", type=int, default=LOOKBACK_DAYS,
        help="analyst-revision look-back Δ (r4; R4-4 sweeps 90/60)",
    )
    parser.add_argument(
        "--level-window-days", type=int, default=LEVEL_WINDOW_DAYS,
        help="analyst target-price / dispersion level window (r4; default 180)",
    )
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--max-rebalances", type=int, default=0, help="0=all (else cap, smoke run)"
    )
    args = parser.parse_args()

    split = LockedSplit.load(args.lock, args.snapshot_root)
    store = SnapshotStore(args.snapshot_root)
    log.info(
        "panel_build_start",
        mode=args.mode,
        factor_set=args.factor_set,
        train_val=len(split.train_val_dates),
        embargo=len(split.embargo_dates),
        test_sealed=len(split.test_dates),
    )
    if args.factor_set in ("r2", "r3", "r4") and args.mode == "test":
        # The round-2/3/4 test window has exactly ONE sanctioned reader each —
        # build_test_panel_r2 / _r3 / _r4, called only by the R2-6 / R3-6 / R4-6
        # one-shot runners AFTER the strategy is git-frozen. The generic CLI
        # refuses, so a development run can never accidentally materialise the
        # test panel here (the freeze-then-read covenant lives in the runner).
        runner = {
            "r2": "scripts.factor_research.r2_locked_test",
            "r3": "scripts.factor_research.round3_locked_test",
            "r4": "scripts.factor_research.round4_locked_test",
        }[args.factor_set]
        raise SystemExit(
            f"factor-set {args.factor_set} test panel is built ONLY by its one-shot "
            f"runner ({runner}), after the strategy is git-frozen. The generic CLI "
            "refuses to read the sealed test window."
        )
    if args.factor_set == "r4":
        r4_inputs = build_r4_inputs(
            store,
            args.snapshot_root,
            last_period_date=split.train_val_dates[-1],
            report_rc_last_date=split.train_val_dates[-1],
            test_start=split.test_dates[0],
            industry_asof=args.industry_asof,
        )
        panel = build_panel_r4(
            split,
            store,
            inputs=r4_inputs,
            rebalance_freq=args.rebalance_freq,
            max_rebalances=args.max_rebalances,
            extra_lag_days=args.fund_lag_days,
            staleness_days=args.staleness_days,
            lookback_days=args.lookback_days,
            level_window_days=args.level_window_days,
        )
        default_out = "data/factor_research/panel_train_val_r4.csv"
    elif args.factor_set == "r3":
        inputs = build_r3_inputs(
            store,
            args.snapshot_root,
            last_period_date=split.train_val_dates[-1],
            industry_asof=args.industry_asof,
        )
        panel = build_panel_r3(
            split,
            store,
            inputs=inputs,
            rebalance_freq=args.rebalance_freq,
            max_rebalances=args.max_rebalances,
            extra_lag_days=args.fund_lag_days,
        )
        default_out = "data/factor_research/panel_train_val_r3.csv"
    elif args.factor_set == "r2":
        # Fundamentals only need report periods whose end_date <= train_val end
        # (later reports are announced after train_val and never selected as-of a
        # train_val date — and asof's ann_date<d gate keeps it PIT regardless).
        periods = report_periods(2015, split.train_val_dates[-1])
        fundamentals = FundamentalsPIT.build(store, periods)
        asof = args.industry_asof or _latest_snapshot_key(
            args.snapshot_root, "index_member_all"
        )
        industry = IndustryPIT.build(store, asof)
        log.info(
            "panel_r2_inputs",
            fina_periods=len(periods),
            fina_codes=len(fundamentals.by_code),
            industry_asof=asof,
            industry_codes=len(industry.by_code),
        )
        panel = build_panel_r2(
            split,
            store,
            fundamentals=fundamentals,
            industry=industry,
            rebalance_freq=args.rebalance_freq,
            max_rebalances=args.max_rebalances,
            extra_lag_days=args.fund_lag_days,
        )
        default_out = "data/factor_research/panel_train_val_r2.csv"
    elif args.mode == "test":
        panel = build_test_panel(split, store, rebalance_freq=args.rebalance_freq)
        default_out = "data/factor_research/panel_test.csv"
    else:
        panel = build_panel(
            split,
            store,
            rebalance_freq=args.rebalance_freq,
            max_rebalances=args.max_rebalances,
        )
        default_out = "data/factor_research/panel_train_val.csv"
    args.out = args.out or default_out
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # CSV (not parquet) — no pyarrow/fastparquet dependency, human-inspectable.
    panel.to_csv(out, index=False)
    log.info(
        "panel_build_done",
        rows=len(panel),
        codes=panel["code"].nunique() if len(panel) else 0,
        dates=panel["date"].nunique() if len(panel) else 0,
        out=str(out),
    )
    print(
        f"panel rows={len(panel)} codes={panel['code'].nunique() if len(panel) else 0} "
        f"dates={panel['date'].nunique() if len(panel) else 0} -> {out}"
    )


if __name__ == "__main__":
    main()
