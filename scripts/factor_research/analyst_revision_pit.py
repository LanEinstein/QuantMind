"""Point-in-time analyst-revision factor aggregator over report_rc (R4-3).

The round-4 alpha source is the *change* in broker analyst earnings consensus —
the most classic orthogonal alpha (information flow, not price/financial-report
derived), and the first family the three FAIL rounds were missing. This module
reads the byte-exact ``report_rc`` month snapshots (R4-2) and exposes, for any
decision date ``d`` and code ``s``, the analyst-revision factor vector computed
strictly point-in-time.

Why each design choice (calibrated on the REAL train_val snapshots, R4-3 step-0):

* **PIT gate = ``report_date < d`` ONLY — NOT ``create_time``.** A probe of the
  real archive showed ``create_time`` is a one-time *bulk-load* timestamp for the
  whole pre-2022 history (every 2014-2020 row carries create_time ≈ 2022-05-03,
  i.e. ``create_time − report_date`` of 800-3000 days). Gating on
  ``create_time < d`` would therefore ZERO OUT all pre-2022 analyst data for any
  decision date before the 2022 bulk load — catastrophic. ``report_date`` is the
  genuine publication / availability date (a report published 2014-01-15 was
  actionable D+1 regardless of when Tushare's DB happened to import it); the bulk
  load is an archival import, not selective per-report backfill. ``create_time``
  is retained only as a deterministic dedup tie-breaker.
* **FY alignment (the year-roll trap).** ``quarter`` = the forecast TARGET fiscal
  year (``YYYYQ4`` = annual; non-Q4 rows are <1% junk → dropped). One report emits
  one row per forecast year. A revision MUST difference the SAME fiscal year at
  both window ends, or a year-roll produces a spurious jump. We anchor the target
  year on the decision date (:func:`target_fy_asof`, a calendar-only rule:
  prior year through March, current year from April — A-share annuals are due
  by 30 Apr) and look up that SAME year at the look-back window. When the
  look-back consensus for that year does not exist (the analysts had not started
  covering it yet), the factor fails closed to ``None``.
* **Sparse stream → trailing window + cross-broker median.** Only ~1.5 brokers
  cover a (code, date), so a same-day consensus is meaningless; each factor
  aggregates a trailing staleness window (90d main / 180d robust), taking each
  broker's LATEST report in the window (canonicalised ``org_name``) then the
  cross-broker MEDIAN (robust to a single house's outlier).
* **Within-stock revision, never level.** A-share ratings are ~95% buy (no
  cross-sectional discrimination) and target prices are systematically
  optimistic; only the *change* is clean (McNichols-O'Brien self-selection).
* **Diffusion / dispersion need n≥3 brokers** (fail-closed ``None`` otherwise) so
  1-2-broker noise never drives the signal.

All factors are RAW (winsorisation + industry/size neutralisation happen
downstream in :mod:`neutralize` / the R4-4 diagnostic, exactly as for r1/r2/r3).
The A-share sign is NOT assumed (Liu-Zhang 2023: A-share analyst signs differ
from the US); the registry sign is a literature *prior* the R4-4 IC study
confirms or refutes from zero.

Pure + deterministic; reads bytes from the ``SnapshotStore`` only. Import
isolation: ``backend.marketdata_snapshot`` only; never
``backend.{llm,agents,mirofish}``.
"""

from __future__ import annotations

import io
import math
import re
import statistics
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

import pandas as pd
import structlog

from backend.marketdata_snapshot.store import SnapshotStore

log = structlog.get_logger(component="factor_research.analyst_revision_pit")

VENDOR = "tushare"
EP_REPORT_RC = "report_rc"
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD report_date
# Annual forecast target year (the dominant ~99% of rows); non-Q4 quarters
# (quarterly forecasts / the junk 'Q' / blank) cannot be FY-aligned and are dropped.
_QUARTER_RE = re.compile(r"^(\d{4})Q4$")
# report_type rows that are NOT individual-stock forecasts → dropped (the np/eps
# of an industry/strategy or IPO report is not a single stock's forecast).
DROP_REPORT_TYPES: frozenset[str] = frozenset({"非个股", "新股"})

# Default windows (R4-4 sweeps staleness 90/180 + lookback 90/60; the level
# window for target-price / dispersion is the literature 180d).
STALENESS_DAYS: int = 90
LOOKBACK_DAYS: int = 90
LEVEL_WINDOW_DAYS: int = 180
# Broker-count floors: a median consensus needs ≥1 broker; the count-based
# diffusion / dispersion factors need ≥3 so 1-2 brokers never drive them.
MIN_CONSENSUS_BROKERS: int = 1
MIN_DIFFUSION_BROKERS: int = 3

# The R4 analyst-revision factor names (authoritative; factor_lib's R4_FACTORS
# registry uses the same names — a test asserts they match).
ANALYST_FACTOR_NAMES: tuple[str, ...] = (
    "np_rev",
    "eps_rev",
    "rev_diff",
    "rating_chg",
    "tp_impl",
    "disp",
    "cover_chg",
)

# House-agnostic rating → ordinal map (frozen). Built from the real vocabulary
# probed across 2014-2025. Higher = more bullish. An UNKNOWN word (or the literal
# 无 / blank) maps to ``None`` fail-closed — NEVER defaulted to a numeric tier, so
# a never-before-seen house vocabulary cannot silently inject a 0. ASCII tokens
# are matched case-insensitively (UPPER keys); Chinese tokens match exactly.
_RATING_ORDINAL: dict[str, int] = {
    # 5 — strong buy / buy
    "买入": 5,
    "强烈推荐": 5,
    "强推": 5,
    "强烈买入": 5,
    "强买": 5,
    "买进": 5,
    "确信买入": 5,
    "BUY": 5,
    "STRONG BUY": 5,
    # 4 — accumulate / outperform / overweight (incl. the cautious variants)
    "增持": 4,
    "推荐": 4,
    "优于大市": 4,
    "跑赢行业": 4,
    "强于大市": 4,
    "优于大盘": 4,
    "谨慎推荐": 4,
    "审慎推荐": 4,
    "谨慎增持": 4,
    "审慎增持": 4,
    "谨慎买入": 4,
    "OVERWEIGHT": 4,
    "OUTPERFORM": 4,
    "ACCUMULATE": 4,
    "ADD": 4,
    # 3 — hold / neutral / market-perform
    "持有": 3,
    "中性": 3,
    "区间操作": 3,
    "同步大市": 3,
    "HOLD": 3,
    "NEUTRAL": 3,
    "EQUAL WEIGHT": 3,
    "EQUAL-WEIGHT": 3,
    "MARKET PERFORM": 3,
    "MARKETPERFORM": 3,
    # 2 — reduce / underperform / underweight
    "减持": 2,
    "回避": 2,
    "弱于大市": 2,
    "REDUCE": 2,
    "UNDERWEIGHT": 2,
    "UNDERPERFORM": 2,
    # 1 — sell
    "卖出": 1,
    "SELL": 1,
}


def target_fy_asof(decision_date: str) -> int:
    """The FY1 target year anchored on ``decision_date`` (calendar-only, PIT-safe).

    A-share annual reports are legally due 30 April; analysts roll their FY1
    anchor to the current calendar year around then. Rule: months Jan-Mar → the
    PRIOR year is FY1 (its annual is not yet reported); from April → the CURRENT
    year (matches the real data's dominant near-year at both boundaries). The
    SAME anchored year is used at both window ends so a revision is a clean
    same-FY difference, never a year-roll jump.
    """
    year, month = int(decision_date[:4]), int(decision_date[4:6])
    return year - 1 if month <= 3 else year


def _opt_float(value: object) -> float | None:
    """Coerce a cell to a finite ``float`` or ``None`` (blank / NaN / ±inf → None)."""
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _canon_org(name: object) -> str:
    """Canonicalise a broker name: strip + collapse internal whitespace."""
    return re.sub(r"\s+", "", str(name).strip()) if name is not None else ""


def rating_ordinal(raw: object) -> int | None:
    """House-agnostic ordinal for a rating token (``None`` for unknown / 无 / blank).

    Fail-closed: an unrecognised vocabulary item is ``None`` (dropped), never a
    fabricated tier. ASCII is matched case-insensitively; Chinese matches exactly.
    """
    token = str(raw).strip() if raw is not None else ""
    if not token:
        return None
    hit = _RATING_ORDINAL.get(token)
    if hit is None and token.isascii():
        hit = _RATING_ORDINAL.get(token.upper())
    return hit


def _parse_target_fy(quarter: object) -> int | None:
    """Forecast target fiscal year from a ``YYYYQ4`` quarter (``None`` if non-Q4)."""
    m = _QUARTER_RE.match(str(quarter).strip())
    return int(m.group(1)) if m else None


class _Report(NamedTuple):
    """One PIT-relevant report_rc row (an individual-stock forecast)."""

    report_date: str  # YYYYMMDD — the PIT availability date
    create_time: str  # insert/update stamp — dedup tie-break only (NOT a PIT gate)
    org: str  # canonical broker name
    target_fy: int | None  # forecast target year (None = non-annual, FY factors skip)
    net_profit: float | None  # np forecast (万元)
    eps: float | None  # eps forecast (元)
    rating_ord: int | None  # ordinal rating (None = unknown / 无)
    min_price: float | None  # target price (元); max_price ≈ always empty


def _shift_days(date_str: str, days: int) -> str:
    """``date_str`` (YYYYMMDD) minus ``days`` calendar days, as YYYYMMDD."""
    return (datetime.strptime(date_str, "%Y%m%d") - timedelta(days=days)).strftime(
        "%Y%m%d"
    )


def read_report_rc_month(store: SnapshotStore, key: str) -> pd.DataFrame:
    """Read one report_rc month snapshot, dtype-safe (dates stay literal strings).

    ``report_date`` / ``create_time`` and the rating text must NOT floatify, so
    the whole frame is read ``dtype=str`` + ``keep_default_na=False`` (a blank
    cell stays ``""``); numeric coercion happens per-field in the parser. A
    missing snapshot raises :class:`FileNotFoundError` (fail-closed).
    """
    snapshot = store.latest(vendor=VENDOR, endpoint=EP_REPORT_RC, trade_date=key)
    if snapshot is None:
        raise FileNotFoundError(f"no report_rc snapshot for month key {key}")
    return pd.read_csv(
        io.StringIO(snapshot.raw_payload.decode("utf-8")),
        dtype=str,
        keep_default_na=False,
    )


def _parse_rows(frame: pd.DataFrame) -> dict[str, dict[tuple[str, ...], _Report]]:
    """Parse + filter + dedup one month frame into ``{ts_code: {dedup_key: _Report}}``.

    Drops non-individual report types, rows without a PIT ``report_date`` or a
    broker, and de-duplicates exact ``(report_date, org, author, quarter)`` rows
    keeping the latest ``create_time`` (the 0-1.8% literal duplicates in the
    source — a multi-FY report keeps one row per FY because ``quarter`` is in the
    key). Returned as a per-code map so :meth:`AnalystRevisionPIT.build` merges
    months without re-deduping across them.
    """
    staged: dict[str, dict[tuple[str, ...], _Report]] = defaultdict(dict)
    for row in frame.itertuples(index=False):
        report_type = str(getattr(row, "report_type", "")).strip()
        if report_type in DROP_REPORT_TYPES:
            continue
        report_date = str(getattr(row, "report_date", "")).strip()
        if not _DATE_RE.match(report_date):
            continue  # cannot PIT-gate without a publication date
        org = _canon_org(getattr(row, "org_name", ""))
        if not org:
            continue  # cannot attribute to a broker → cannot dedupe per-broker
        ts = str(getattr(row, "ts_code", "")).strip()
        if not ts:
            continue
        quarter_raw = str(getattr(row, "quarter", "")).strip()
        author = str(getattr(row, "author_name", "")).strip()
        create_time = str(getattr(row, "create_time", "")).strip()
        report = _Report(
            report_date=report_date,
            create_time=create_time,
            org=org,
            target_fy=_parse_target_fy(quarter_raw),
            net_profit=_opt_float(getattr(row, "np", None)),
            eps=_opt_float(getattr(row, "eps", None)),
            rating_ord=rating_ordinal(getattr(row, "rating", None)),
            min_price=_opt_float(getattr(row, "min_price", None)),
        )
        dedup_key = (report_date, org, author, quarter_raw)
        prior = staged[ts].get(dedup_key)
        if prior is None or report.create_time > prior.create_time:
            staged[ts][dedup_key] = report
    return staged


@dataclass(frozen=True)
class AnalystRevisionPIT:
    """Per-code report_rc index → PIT analyst-revision factor vectors (immutable)."""

    by_code: dict[str, tuple[_Report, ...]]
    # Parallel sorted report_date tuple per code (bisect key — avoids rebuilding it).
    _dates: dict[str, tuple[str, ...]]

    @classmethod
    def build(
        cls, store: SnapshotStore, month_keys: Sequence[str]
    ) -> AnalystRevisionPIT:
        """Build the per-code report index from the report_rc month snapshots.

        ``month_keys`` are the report_rc snapshot keys (calendar month-ends) to
        load; the CALLER (the panel builder) is responsible for the firewall —
        passing only ``< test_start`` keys for a development build. A missing
        month snapshot raises :class:`FileNotFoundError` (fail-closed).
        """
        merged: dict[str, dict[tuple[str, ...], _Report]] = defaultdict(dict)
        for key in month_keys:
            for ts, by_key in _parse_rows(read_report_rc_month(store, key)).items():
                merged[ts].update(by_key)
        by_code: dict[str, tuple[_Report, ...]] = {}
        dates: dict[str, tuple[str, ...]] = {}
        for ts, by_key in merged.items():
            reports = tuple(
                sorted(by_key.values(), key=lambda r: (r.report_date, r.create_time))
            )
            by_code[ts] = reports
            dates[ts] = tuple(r.report_date for r in reports)
        log.info(
            "analyst_revision_pit_built",
            months=len(month_keys),
            codes=len(by_code),
        )
        return cls(by_code=by_code, _dates=dates)

    def _window(self, code: str, lo: str, hi: str) -> tuple[_Report, ...]:
        """Reports for ``code`` with ``lo <= report_date < hi`` (bisect-sliced)."""
        reports = self.by_code.get(code)
        if not reports:
            return ()
        dates = self._dates[code]
        return reports[bisect_left(dates, lo) : bisect_left(dates, hi)]

    def factors(
        self,
        code: str,
        decision_date: str,
        *,
        close: float | None,
        staleness_days: int = STALENESS_DAYS,
        lookback_days: int = LOOKBACK_DAYS,
        level_window_days: int = LEVEL_WINDOW_DAYS,
    ) -> dict[str, float | None]:
        """The R4 analyst-revision factor vector for ``code`` as of ``decision_date``.

        ``close`` is the code's RAW (unadjusted) price on the decision date (the
        target-price-implied factor compares an absolute target price to it).
        All factors fail closed to ``None`` when the analyst data is absent /
        too stale / too thin. PIT: every report used has ``report_date <`` the
        relevant window end.
        """
        level_lo = _shift_days(decision_date, level_window_days)
        back_hi = _shift_days(decision_date, lookback_days)
        back_lo = _shift_days(decision_date, level_window_days + lookback_days)
        return analyst_factor_vector(
            self._window(code, level_lo, decision_date),
            self._window(code, back_lo, back_hi),
            close=close,
            staleness_days=staleness_days,
            lookback_days=lookback_days,
            level_window_days=level_window_days,
            decision_date=decision_date,
        )


# --- pure factor math (operates on report slices — store-free, testable) -----


def _broker_latest(
    reports: Sequence[_Report],
    lo: str,
    hi: str,
    field: str,
    *,
    target_fy: int | None,
) -> dict[str, float]:
    """Per-broker latest in-window value of ``field`` (``{org: value}``).

    A report counts only if ``lo <= report_date < hi`` (strict upper = PIT),
    ``field`` is finite, and — when ``target_fy`` is given — its target year
    matches (so np / eps / dispersion difference the SAME fiscal year; ratings /
    target prices pass ``target_fy=None`` since they are not FY-specific). Each
    broker keeps its latest report by ``(report_date, create_time)``.
    """
    latest: dict[str, _Report] = {}
    for r in reports:
        if not (lo <= r.report_date < hi):
            continue
        if target_fy is not None and r.target_fy != target_fy:
            continue
        value = getattr(r, field)
        if value is None or not math.isfinite(value):
            continue
        cur = latest.get(r.org)
        if cur is None or (r.report_date, r.create_time) > (
            cur.report_date,
            cur.create_time,
        ):
            latest[r.org] = r
    return {org: float(getattr(r, field)) for org, r in latest.items()}


def _revision_ratio(now: dict[str, float], back: dict[str, float]) -> float | None:
    """``(median(now) − median(back)) / |median(back)|`` (None if undefined)."""
    if len(now) < MIN_CONSENSUS_BROKERS or len(back) < MIN_CONSENSUS_BROKERS:
        return None
    c_now = statistics.median(now.values())
    c_back = statistics.median(back.values())
    if not (math.isfinite(c_now) and math.isfinite(c_back)) or c_back == 0.0:
        return None
    out = (c_now - c_back) / abs(c_back)
    return out if math.isfinite(out) else None


def _diffusion(now: dict[str, float], back: dict[str, float]) -> float | None:
    """Per-broker net up/down diffusion ``(n_up − n_down) / n_total`` (n≥3)."""
    common = set(now) & set(back)
    if len(common) < MIN_DIFFUSION_BROKERS:
        return None
    n_up = sum(1 for o in common if now[o] > back[o])
    n_down = sum(1 for o in common if now[o] < back[o])
    return (n_up - n_down) / len(common)


def analyst_factor_vector(
    window_now: Sequence[_Report],
    window_back: Sequence[_Report],
    *,
    close: float | None,
    decision_date: str,
    staleness_days: int = STALENESS_DAYS,
    lookback_days: int = LOOKBACK_DAYS,
    level_window_days: int = LEVEL_WINDOW_DAYS,
) -> dict[str, float | None]:
    """Compute the 7 analyst-revision factors from a code's report slices (pure).

    ``window_now`` covers ``[d − level_window, d)`` and ``window_back`` covers
    ``[d − level_window − lookback, d − lookback)`` (the two slices the PIT class
    bisects); the staleness sub-windows are re-cut inside. ``target_fy`` is
    anchored on ``decision_date`` and used at BOTH ends (clean same-FY revision).
    """
    d = decision_date
    d_back = _shift_days(d, lookback_days)
    fy = target_fy_asof(d)
    # Staleness sub-window bounds for the consensus / diffusion factors.
    stale_lo = _shift_days(d, staleness_days)
    back_lo = _shift_days(d_back, staleness_days)

    np_now = _broker_latest(window_now, stale_lo, d, "net_profit", target_fy=fy)
    np_back = _broker_latest(window_back, back_lo, d_back, "net_profit", target_fy=fy)
    eps_now = _broker_latest(window_now, stale_lo, d, "eps", target_fy=fy)
    eps_back = _broker_latest(window_back, back_lo, d_back, "eps", target_fy=fy)
    rat_now = _broker_latest(window_now, stale_lo, d, "rating_ord", target_fy=None)
    rat_back = _broker_latest(
        window_back, back_lo, d_back, "rating_ord", target_fy=None
    )

    # Coverage = brokers with a live FY1 estimate over the LEVEL window, d vs d−Δ.
    level_lo = _shift_days(d, level_window_days)
    back_level_lo = _shift_days(d_back, level_window_days)
    cov_now = _broker_latest(window_now, level_lo, d, "net_profit", target_fy=fy)
    cov_back = _broker_latest(
        window_back, back_level_lo, d_back, "net_profit", target_fy=fy
    )
    cover_chg: float | None = None
    if len(cov_now) > 0 and len(cov_back) > 0:
        cover_chg = math.log(len(cov_now) / len(cov_back))

    # Target-price implied return + dispersion use the LEVEL window (180d), n≥1 / n≥3.
    tp_window = _broker_latest(window_now, level_lo, d, "min_price", target_fy=None)
    tp_impl: float | None = None
    if tp_window and close is not None and math.isfinite(close) and close > 0:
        med_tp = statistics.median(tp_window.values())
        if math.isfinite(med_tp):
            tp_impl = med_tp / close - 1.0

    eps_level = _broker_latest(window_now, level_lo, d, "eps", target_fy=fy)
    disp: float | None = None
    if len(eps_level) >= MIN_DIFFUSION_BROKERS:
        vals = list(eps_level.values())
        mean = statistics.fmean(vals)
        if mean != 0.0 and math.isfinite(mean):
            sd = statistics.pstdev(vals)
            cand = sd / abs(mean)
            disp = cand if math.isfinite(cand) else None

    return {
        "np_rev": _revision_ratio(np_now, np_back),
        "eps_rev": _revision_ratio(eps_now, eps_back),
        "rev_diff": _diffusion(np_now, np_back),
        "rating_chg": _diffusion(rat_now, rat_back),
        "tp_impl": tp_impl,
        "disp": disp,
        "cover_chg": cover_chg,
    }


__all__ = [
    "ANALYST_FACTOR_NAMES",
    "DROP_REPORT_TYPES",
    "LEVEL_WINDOW_DAYS",
    "LOOKBACK_DAYS",
    "MIN_CONSENSUS_BROKERS",
    "MIN_DIFFUSION_BROKERS",
    "STALENESS_DAYS",
    "AnalystRevisionPIT",
    "analyst_factor_vector",
    "rating_ordinal",
    "read_report_rc_month",
    "target_fy_asof",
]
