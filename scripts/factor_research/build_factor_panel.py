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

from .factor_lib import (
    FACTOR_NAMES,
    R2_FACTOR_NAMES,
    compute_factor_vector,
    compute_fundamental_factors,
    compute_trend_factors,
)
from .fundamentals_pit import FundamentalsPIT
from .industry_pit import IndustryPIT
from .ingest_round2_data import report_periods
from .locked_split import LockedSplit

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
    series: dict[str, _CodeSeries], day: str
) -> tuple[list[tuple[str, _CodeSeries, int]], float] | None:
    """The day's investable cross-section + the bottom-30% market-cap cutoff.

    Codes with a bar today that pass the liquidity / unit-price / finite-size
    filters; ``None`` when the cross-section is too thin (<20) to rank. The
    bottom-30% size cut (Liu-Stambaugh-Yuan shell mandate) is returned for the
    caller to apply per code.
    """
    cohort: list[tuple[str, _CodeSeries, int]] = []
    for code, cs in series.items():
        pos = cs.pos_of_date.get(day)
        if pos is None or not _passes_liquidity_price(cs, pos):
            continue
        if not math.isfinite(cs.circ_mv[pos]):  # need a size to rank on
            continue
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
        choices=("r1", "r2"),
        default="r1",
        help="r2 = round-2 panel (train_val only): +trend/quality/growth +"
        " PIT industry/size for neutralization → panel_train_val_r2.csv",
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
    if args.factor_set == "r2" and args.mode == "test":
        # No sanctioned round-2 test-panel path exists (the test window stays
        # sealed for round-2); r2 builds train_val only. Refuse rather than
        # silently write train rows to a test output path (codex P2).
        raise SystemExit(
            "factor-set r2 has no sanctioned test path — the round-2 test "
            "window is sealed; r2 builds train_val only. Refusing."
        )
    if args.factor_set == "r2":
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
