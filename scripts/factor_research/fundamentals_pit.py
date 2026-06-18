"""Point-in-time fundamentals reader with vintage handling (R2-2 / S1).

Reads the ``fina_indicator_vip`` report-period snapshots (R2-1) and exposes an
as-of lookup that returns the fundamentals **known as of a decision date** — the
value announced (``ann_date``) strictly before the decision date, taking the
latest report period and within it the latest restatement announced by then.

Why vintage matters (the PIT landmine — round-2 plan §4.1 / codex P1-3):
``fina_indicator_vip`` keys on the report-period ``end_date``, but a quarter's
numbers are first announced ~1 month later (``ann_date``) and may be RESTATED
months/years on. Joining on ``end_date`` would use numbers that did not yet
exist (look-ahead). Probing the real snapshots (2026-06-18) showed each period
returns MULTIPLE vintages per ``(ts_code, end_date)`` — each row carrying its
own ``ann_date`` + ``update_flag`` — so the first announcement and any
restatements are both present and we can reconstruct the as-known value
(``ann_date < d``). This is stronger PIT than the plan feared, but Tushare may
not retain *every* historical vintage, so :func:`vintage_audit` quantifies the
residual restatement contamination for honest disclosure.

dtype trap (round-2 real-run lesson): a fina snapshot read back with the default
``read_csv`` coerces the all-numeric ``ann_date`` / ``end_date`` columns to
``float64`` (``20240331`` → ``20240331.0``). :func:`read_fina_period` reads
date / code / flag columns as ``str`` and coerces only the numeric factor
columns to ``float``.

Pure + deterministic; reads bytes from the ``SnapshotStore`` only. Import
isolation: ``backend.data.*`` via per-line ``# noqa: TID251``; never
``backend.{llm,agents,mirofish}``.
"""

from __future__ import annotations

import io
import re
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore

from .ingest_round2_data import EP_FINA

VENDOR = "tushare"

# The raw fina_indicator_vip fields the round-2 quality/growth factors consume.
# Quality: ROE level + gross profit margin (Novy-Marx, robust to earnings
# management). Growth: net-profit YoY + operating-revenue YoY. (Earnings
# stability is intentionally NOT built here: cumulative quarterly ROE is
# seasonality-confounded across Q1..Q4, so a naive std would measure seasonality
# not stability — deferred to a same-quarter/annualised series if quality earns
# its place in R2-4.)
FUNDAMENTAL_FIELDS: tuple[str, ...] = (
    "roe",
    "grossprofit_margin",
    "netprofit_yoy",
    "or_yoy",
)
# Identity columns kept as literal strings (never floatified — the dtype trap).
_STR_COLS: tuple[str, ...] = ("ts_code", "ann_date", "end_date", "update_flag")
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD


def read_fina_period(store: SnapshotStore, period: str) -> pd.DataFrame:
    """Read one ``fina_indicator_vip`` period snapshot, dtype-safe.

    Date / code / flag columns stay literal strings (the all-numeric
    ``ann_date`` / ``end_date`` would otherwise re-infer to ``float64`` on
    read-back — the round-2 real-run trap); the factor columns are coerced to
    ``float`` (empty cell → ``NaN``). A missing period snapshot raises
    :class:`FileNotFoundError` (fail-closed).
    """
    snapshot = store.latest(vendor=VENDOR, endpoint=EP_FINA, trade_date=period)
    if snapshot is None:
        raise FileNotFoundError(f"no fina_indicator_vip snapshot for period {period}")
    frame = pd.read_csv(
        io.StringIO(snapshot.raw_payload.decode("utf-8")),
        dtype=str,
        keep_default_na=False,
    )
    for col in _STR_COLS:
        if col not in frame.columns:
            frame[col] = ""
    for field in FUNDAMENTAL_FIELDS:
        if field in frame.columns:
            frame[field] = pd.to_numeric(
                frame[field].replace("", pd.NA), errors="coerce"
            )
        else:
            frame[field] = pd.NA
    return frame


def _opt_float(value: object) -> float | None:
    """Coerce a cell to ``float`` or ``None`` (NaN / missing → None)."""
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # drop NaN


class _Vintage(NamedTuple):
    """One announced version of a (code, report-period) fundamental snapshot."""

    ann_date: str
    end_date: str
    update_flag: str
    vals: tuple[float | None, ...]  # aligned to FUNDAMENTAL_FIELDS


@dataclass(frozen=True)
class FundamentalRecord:
    """The fundamentals known for one code as of a decision date (immutable)."""

    ts_code: str
    end_date: str
    ann_date: str
    update_flag: str
    vals: tuple[float | None, ...]

    def get(self, field: str) -> float | None:
        """Factor value for ``field`` (``None`` if absent / NaN)."""
        try:
            return self.vals[FUNDAMENTAL_FIELDS.index(field)]
        except ValueError:
            return None

    @property
    def values(self) -> dict[str, float | None]:
        return dict(zip(FUNDAMENTAL_FIELDS, self.vals, strict=True))


def _cutoff(decision_date: str, extra_lag_days: int) -> str:
    """The exclusive ann_date ceiling: a vintage is usable iff ``ann_date <`` it.

    With ``extra_lag_days == 0`` this is the decision date itself (strict-before
    = a same-day announcement is not yet tradable). A positive lag shifts the
    ceiling earlier by that many calendar days.
    """
    if extra_lag_days <= 0:
        return decision_date
    shifted = datetime.strptime(decision_date, "%Y%m%d") - timedelta(
        days=extra_lag_days
    )
    return shifted.strftime("%Y%m%d")


@dataclass(frozen=True)
class FundamentalsPIT:
    """Per-code vintage index over the fina period snapshots (immutable)."""

    by_code: dict[str, tuple[_Vintage, ...]]

    @classmethod
    def build(cls, store: SnapshotStore, periods: Sequence[str]) -> FundamentalsPIT:
        """Assemble the per-code vintage index across ``periods``.

        Rows without a usable ``ann_date`` (YYYYMMDD) are dropped fail-closed —
        without an announcement date a value cannot be PIT-gated.
        """
        idx = FUNDAMENTAL_FIELDS  # local alias for the per-row comprehension
        staged: dict[str, list[_Vintage]] = defaultdict(list)
        for period in periods:
            frame = read_fina_period(store, period)
            for row in frame.itertuples(index=False):
                ann = str(getattr(row, "ann_date", "")).strip()
                if not _DATE_RE.match(ann):
                    continue
                ts = str(getattr(row, "ts_code", "")).strip()
                end = str(getattr(row, "end_date", "")).strip()
                flag = str(getattr(row, "update_flag", "")).strip()
                vals = tuple(_opt_float(getattr(row, f, None)) for f in idx)
                staged[ts].append(_Vintage(ann, end, flag, vals))
        # Deterministic ordering inside each code (stable selection downstream).
        return cls(
            by_code={
                code: tuple(
                    sorted(vs, key=lambda v: (v.end_date, v.ann_date, v.update_flag))
                )
                for code, vs in staged.items()
            }
        )

    def asof(
        self, code: str, decision_date: str, *, extra_lag_days: int = 0
    ) -> FundamentalRecord | None:
        """Fundamentals known for ``code`` as of ``decision_date``.

        Selects, among vintages announced strictly before the (lag-adjusted)
        decision date, the latest report period (``end_date``) and within it the
        latest announcement (``ann_date``, tie-broken by ``update_flag``). This
        is the as-known value: a restatement announced after the decision date
        is never used. ``None`` when the code has no vintage known by then.
        """
        vintages = self.by_code.get(code)
        if not vintages:
            return None
        cutoff = _cutoff(decision_date, extra_lag_days)
        candidates = [v for v in vintages if v.ann_date < cutoff]
        if not candidates:
            return None
        max_end = max(v.end_date for v in candidates)
        best = max(
            (v for v in candidates if v.end_date == max_end),
            key=lambda v: (v.ann_date, v.update_flag),
        )
        return FundamentalRecord(
            ts_code=code,
            end_date=best.end_date,
            ann_date=best.ann_date,
            update_flag=best.update_flag,
            vals=best.vals,
        )


@dataclass(frozen=True)
class VintageAudit:
    """Restatement statistics for honest PIT-contamination disclosure."""

    n_codes: int
    n_code_periods: int  # distinct (code, end_date)
    n_restated_code_periods: int  # (code, end_date) with >=2 distinct ann_date
    restatement_rate: float
    ann_lag_days_median: float | None  # median (ann_date - end_date), days
    restate_gap_days_median: float | None  # median (latest_ann - first_ann)


def _days_between(later: str, earlier: str) -> int | None:
    """Calendar days ``later - earlier`` (both YYYYMMDD), or None if malformed."""
    if not (_DATE_RE.match(later) and _DATE_RE.match(earlier)):
        return None
    return (
        datetime.strptime(later, "%Y%m%d") - datetime.strptime(earlier, "%Y%m%d")
    ).days


def vintage_audit(pit: FundamentalsPIT) -> VintageAudit:
    """Quantify restatement prevalence across the whole vintage index.

    A ``(code, end_date)`` is counted as *restated* when it has ≥2 distinct
    ``ann_date`` values (the genuine cross-announcement revision) — the
    same-``ann_date`` ``update_flag`` 0/1 duplicates are NOT restatements.
    """
    code_periods = 0
    restated = 0
    ann_lags: list[int] = []
    restate_gaps: list[int] = []
    for code, vintages in pit.by_code.items():
        by_end: dict[str, set[str]] = defaultdict(set)
        for v in vintages:
            by_end[v.end_date].add(v.ann_date)
        for end_date, ann_dates in by_end.items():
            code_periods += 1
            first_ann = min(ann_dates)
            lag = _days_between(first_ann, end_date)
            if lag is not None:
                ann_lags.append(lag)
            if len(ann_dates) >= 2:
                restated += 1
                gap = _days_between(max(ann_dates), first_ann)
                if gap is not None:
                    restate_gaps.append(gap)
    rate = restated / code_periods if code_periods else 0.0
    return VintageAudit(
        n_codes=len(pit.by_code),
        n_code_periods=code_periods,
        n_restated_code_periods=restated,
        restatement_rate=rate,
        ann_lag_days_median=(statistics.median(ann_lags) if ann_lags else None),
        restate_gap_days_median=(
            statistics.median(restate_gaps) if restate_gaps else None
        ),
    )


__all__ = [
    "FUNDAMENTAL_FIELDS",
    "FundamentalRecord",
    "FundamentalsPIT",
    "VintageAudit",
    "read_fina_period",
    "vintage_audit",
]
