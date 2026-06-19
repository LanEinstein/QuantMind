"""Point-in-time financial-statement reader with vintage handling (R3-2).

The round-3 generalisation of :mod:`fundamentals_pit`: a single
:class:`PeriodStatementPIT` reads ANY period-keyed Tushare snapshot
(``income_vip`` / ``cashflow_vip`` / ``balancesheet_vip`` for the accruals /
asset-growth factors, and ``fina_indicator_vip`` for the SUE ``profit_dedt``
series) and exposes the values **known as of a decision date** — the vintage
announced (``ann_date``) strictly before the date, taking within each report
period the latest restatement announced by then. ``fundamentals_pit.py`` is left
byte-unchanged (round-2 reproducibility); this is a separate, cohesive module.

Two PIT landmines this handles (round-2 plan §4.1 / R3 kickoff §3):

* **vintage (ann_date)** — a period's numbers are first announced ~1 month after
  the period end and may be RESTATED later. Joining on ``end_date`` would use
  numbers that did not yet exist (look-ahead). Each ``(ts_code, end_date)`` keeps
  every announced vintage so the as-known value (``ann_date < d``) is recoverable.
* **report_type** — the three statements return MULTIPLE ``report_type`` rows per
  ``(ts_code, end_date)`` (consolidated YTD, single-quarter, adjusted, parent-only,
  …). Only the consolidated YTD report (``report_type == '1'``) is kept, so a
  single-quarter or parent-only row never contaminates a YTD level.
  ``fina_indicator_vip`` has no ``report_type`` column → the filter is skipped
  (``report_type_filter=None``).

dtype trap (round-2 real-run lesson): a snapshot read with the default
``read_csv`` floatifies all-numeric ``ann_date`` / ``end_date`` columns
(``20240331`` → ``20240331.0``). Identity / date / flag columns are read as
``str``; only the requested numeric fields are coerced to ``float``.

Pure + deterministic; reads bytes from the ``SnapshotStore`` only. Import
isolation: ``backend.marketdata_snapshot`` only; never
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

from .fundamentals_pit import VintageAudit, _days_between

VENDOR = "tushare"
# Consolidated YTD report (合并报表) — the only report_type the statement factors
# read; single-quarter / adjusted / parent-only rows are dropped.
CONSOLIDATED_REPORT_TYPE = "1"
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD
# Identity / date / flag columns kept as literal strings (never floatified).
_STR_COLS: tuple[str, ...] = (
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "update_flag",
)


def read_statement_period(
    store: SnapshotStore,
    endpoint: str,
    period: str,
    fields: Sequence[str],
) -> pd.DataFrame:
    """Read one period snapshot of ``endpoint``, dtype-safe.

    Identity / date / flag columns stay literal strings; the requested ``fields``
    are coerced to ``float`` (empty cell → ``NaN``). A missing snapshot raises
    :class:`FileNotFoundError` (fail-closed).
    """
    snapshot = store.latest(vendor=VENDOR, endpoint=endpoint, trade_date=period)
    if snapshot is None:
        raise FileNotFoundError(f"no {endpoint} snapshot for period {period}")
    frame = pd.read_csv(
        io.StringIO(snapshot.raw_payload.decode("utf-8")),
        dtype=str,
        keep_default_na=False,
    )
    for col in _STR_COLS:
        if col not in frame.columns:
            frame[col] = ""
    for field in fields:
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
    """One announced version of a (code, report-period) statement snapshot."""

    ann_date: str
    end_date: str
    update_flag: str
    vals: tuple[float | None, ...]  # aligned to the PIT's ``fields``


@dataclass(frozen=True)
class StatementRecord:
    """The statement values known for one code/period as of a decision date."""

    ts_code: str
    end_date: str
    ann_date: str
    update_flag: str
    fields: tuple[str, ...]
    vals: tuple[float | None, ...]

    def get(self, field: str) -> float | None:
        """Value for ``field`` (``None`` if absent / NaN)."""
        try:
            return self.vals[self.fields.index(field)]
        except ValueError:
            return None


def _cutoff(decision_date: str, extra_lag_days: int) -> str:
    """The exclusive ann_date ceiling: a vintage is usable iff ``ann_date <`` it."""
    if extra_lag_days <= 0:
        return decision_date
    shifted = datetime.strptime(decision_date, "%Y%m%d") - timedelta(
        days=extra_lag_days
    )
    return shifted.strftime("%Y%m%d")


@dataclass(frozen=True)
class PeriodStatementPIT:
    """Per-code vintage index over one endpoint's period snapshots (immutable)."""

    fields: tuple[str, ...]
    by_code: dict[str, tuple[_Vintage, ...]]

    @classmethod
    def build(
        cls,
        store: SnapshotStore,
        periods: Sequence[str],
        *,
        endpoint: str,
        fields: Sequence[str],
        report_type_filter: str | None = CONSOLIDATED_REPORT_TYPE,
    ) -> PeriodStatementPIT:
        """Assemble the per-code vintage index across ``periods``.

        Rows without a usable ``ann_date`` are dropped fail-closed (without an
        announcement date a value cannot be PIT-gated). When ``report_type_filter``
        is set, ONLY rows whose ``report_type`` equals it survive — a blank /
        missing ``report_type`` is DROPPED fail-closed (codex P2: an unclassified
        row could otherwise be selected as a period's latest vintage and feed a
        single-quarter / parent-only number into a YTD factor). Endpoints with no
        ``report_type`` column (e.g. ``fina_indicator_vip``) MUST be built with
        ``report_type_filter=None`` to bypass the filter entirely.
        """
        field_tuple = tuple(fields)
        staged: dict[str, list[_Vintage]] = defaultdict(list)
        for period in periods:
            frame = read_statement_period(store, endpoint, period, field_tuple)
            for row in frame.itertuples(index=False):
                ann = str(getattr(row, "ann_date", "")).strip()
                if not _DATE_RE.match(ann):
                    continue
                if report_type_filter is not None:
                    rt = str(getattr(row, "report_type", "")).strip()
                    # Drop blank/missing OR mismatched report_type (fail-closed);
                    # only report_type_filter=None bypasses the filter.
                    if rt != report_type_filter:
                        continue
                ts = str(getattr(row, "ts_code", "")).strip()
                end = str(getattr(row, "end_date", "")).strip()
                flag = str(getattr(row, "update_flag", "")).strip()
                vals = tuple(_opt_float(getattr(row, f, None)) for f in field_tuple)
                staged[ts].append(_Vintage(ann, end, flag, vals))
        return cls(
            fields=field_tuple,
            by_code={
                code: tuple(
                    sorted(vs, key=lambda v: (v.end_date, v.ann_date, v.update_flag))
                )
                for code, vs in staged.items()
            },
        )

    def _record(self, code: str, v: _Vintage) -> StatementRecord:
        return StatementRecord(
            ts_code=code,
            end_date=v.end_date,
            ann_date=v.ann_date,
            update_flag=v.update_flag,
            fields=self.fields,
            vals=v.vals,
        )

    def as_known(
        self, code: str, decision_date: str, *, extra_lag_days: int = 0
    ) -> dict[str, StatementRecord]:
        """As-known record PER report period, keyed by ``end_date``.

        For every period with a vintage announced strictly before the (lag-adjusted)
        decision date, the latest such announcement (``ann_date``, tie-broken by
        ``update_flag``) is kept — the as-of-``decision_date`` value of that period.
        A restatement announced after the decision date is never used. Empty dict
        when the code has no vintage known by then.
        """
        vintages = self.by_code.get(code)
        if not vintages:
            return {}
        cutoff = _cutoff(decision_date, extra_lag_days)
        best_by_end: dict[str, _Vintage] = {}
        for v in vintages:
            if v.ann_date >= cutoff:
                continue
            cur = best_by_end.get(v.end_date)
            if cur is None or (v.ann_date, v.update_flag) > (
                cur.ann_date,
                cur.update_flag,
            ):
                best_by_end[v.end_date] = v
        return {end: self._record(code, v) for end, v in best_by_end.items()}

    def asof(
        self, code: str, decision_date: str, *, extra_lag_days: int = 0
    ) -> StatementRecord | None:
        """The single latest-period as-known record (``None`` if none known)."""
        known = self.as_known(code, decision_date, extra_lag_days=extra_lag_days)
        if not known:
            return None
        return known[max(known)]


def statement_vintage_audit(pit: PeriodStatementPIT) -> VintageAudit:
    """Restatement-contamination stats for a statement PIT (honest disclosure).

    Mirrors :func:`fundamentals_pit.vintage_audit` over the generic statement
    vintage index: a ``(code, end_date)`` counts as *restated* when it carries ≥2
    distinct ``ann_date`` values. Reuses the shared :class:`VintageAudit` shape so
    the R3-3 diagnostic reports income/cashflow/balancesheet contamination the
    same way R2-2 reported fina.
    """
    code_periods = 0
    restated = 0
    ann_lags: list[int] = []
    restate_gaps: list[int] = []
    for vintages in pit.by_code.values():
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
    "CONSOLIDATED_REPORT_TYPE",
    "PeriodStatementPIT",
    "StatementRecord",
    "read_statement_period",
    "statement_vintage_audit",
]
