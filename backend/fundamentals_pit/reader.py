"""PIT statement vintage reader → AF-003 quality-metric records (AF-002).

Two PIT landmines (mirrored from the research-side ``statements_pit.py``):

* **vintage (ann_date)** — a period's numbers are first announced ~1 month after
  the period end and may be RESTATED later. Joining on ``end_date`` alone would
  use numbers that did not exist yet (look-ahead). Every announced vintage is
  kept so the as-known value is recoverable; the downstream AF-003 selector
  (:func:`backend.quality_fundamentals.quality.fundamentals_scores`) picks the
  latest vintage announced on/before the decision date.
* **report_type** — income/cashflow/balancesheet return MULTIPLE report_type
  rows per ``(ts_code, end_date)`` (consolidated YTD, single-quarter, parent-
  only, …). Only the consolidated YTD report (``report_type == '1'``) is kept;
  a blank/missing report_type is DROPPED fail-closed. ``fina_indicator_vip`` has
  no report_type column → it is built with ``report_type_filter=None``.

dtype trap: a default ``read_csv`` floatifies all-numeric ``ann_date`` /
``end_date`` columns (``20240331`` → ``20240331.0``). Identity / date / flag
columns are read as ``str``; only the requested numeric fields are coerced.

Pure + deterministic; reads bytes from the ``SnapshotStore`` only. Import
isolation: ``backend.marketdata_snapshot`` + ``backend.quality_fundamentals``
only — never ``backend.{llm,agents,mirofish}``.
"""

from __future__ import annotations

import io
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore
from backend.quality_fundamentals.quality import QualityMetric

VENDOR = "tushare"
CONSOLIDATED_REPORT_TYPE = "1"
"""合并报表 — the only report_type the statement factors read."""

EP_FINA = "fina_indicator_vip"
EP_INCOME = "income_vip"
EP_CASHFLOW = "cashflow_vip"
EP_BALANCESHEET = "balancesheet_vip"

_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD
# Identity / date / flag columns kept as literal strings (never floatified).
_STR_COLS: tuple[str, ...] = (
    "ts_code",
    "ann_date",
    "end_date",
    "report_type",
    "update_flag",
)


def _opt_float(value: object) -> float | None:
    """Coerce a cell to ``float`` or ``None`` (NaN / missing → None)."""
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


class StatementVintage(NamedTuple):
    """One announced version of a (code, report-period) statement snapshot."""

    ann_date: str
    end_date: str
    update_flag: str
    vals: tuple[float | None, ...]  # aligned to the PIT's ``fields``


def _read_period(
    store: SnapshotStore,
    endpoint: str,
    period: str,
    fields: Sequence[str],
) -> pd.DataFrame | None:
    """Read one period snapshot of ``endpoint`` dtype-safe (``None`` if absent).

    A missing snapshot returns ``None`` rather than raising — a value sleeve
    asof a quarter not yet ingested simply has fewer vintages (conservative),
    never a hard failure mid-screen.
    """
    snapshot = store.latest(vendor=VENDOR, endpoint=endpoint, trade_date=period)
    if snapshot is None:
        return None
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


@dataclass(frozen=True)
class BackendStatementPIT:
    """Per-code vintage index over one endpoint's period snapshots (immutable)."""

    fields: tuple[str, ...]
    by_code: dict[str, tuple[StatementVintage, ...]]

    @classmethod
    def build(
        cls,
        store: SnapshotStore,
        periods: Sequence[str],
        *,
        endpoint: str,
        fields: Sequence[str],
        report_type_filter: str | None = CONSOLIDATED_REPORT_TYPE,
    ) -> BackendStatementPIT:
        """Assemble the per-code vintage index across ``periods`` (fail-closed).

        Rows without a well-formed ``ann_date`` are dropped (a value cannot be
        PIT-gated without an announcement date). When ``report_type_filter`` is
        set, ONLY rows whose ``report_type`` equals it survive — blank/missing or
        mismatched report_type is DROPPED fail-closed. Endpoints with no
        report_type column (``fina_indicator_vip``) MUST pass
        ``report_type_filter=None``.
        """
        field_tuple = tuple(fields)
        staged: dict[str, list[StatementVintage]] = defaultdict(list)
        for period in periods:
            frame = _read_period(store, endpoint, period, field_tuple)
            if frame is None:
                continue
            for row in frame.itertuples(index=False):
                ann = str(getattr(row, "ann_date", "")).strip()
                if not _DATE_RE.match(ann):
                    continue
                if report_type_filter is not None:
                    rt = str(getattr(row, "report_type", "")).strip()
                    if rt != report_type_filter:
                        continue
                ts = str(getattr(row, "ts_code", "")).strip()
                end = str(getattr(row, "end_date", "")).strip()
                flag = str(getattr(row, "update_flag", "")).strip()
                vals = tuple(_opt_float(getattr(row, f, None)) for f in field_tuple)
                staged[ts].append(StatementVintage(ann, end, flag, vals))
        return cls(
            fields=field_tuple,
            by_code={
                code: tuple(
                    sorted(vs, key=lambda v: (v.end_date, v.ann_date, v.update_flag))
                )
                for code, vs in staged.items()
            },
        )

    def _field_index(self, field: str) -> int | None:
        try:
            return self.fields.index(field)
        except ValueError:
            return None

    def announced_values(self, code: str, field: str) -> list[tuple[str, float]]:
        """All ``(ann_date, value)`` records for ``field`` (every vintage).

        Feeds :func:`backend.quality_fundamentals.quality.fundamentals_scores`,
        which does the PIT selection (latest announce ≤ decision date) and the
        cross-sectional ranking. Dirty / missing values are skipped.
        """
        idx = self._field_index(field)
        if idx is None:
            return []
        out: list[tuple[str, float]] = []
        for v in self.by_code.get(code, ()):  # noqa: B007 - tuple iteration
            value = v.vals[idx]
            if value is not None:
                out.append((v.ann_date, value))
        return out

    def latest_vintage_by_end(self, code: str) -> dict[str, StatementVintage]:
        """Per report period, the latest-announced vintage (restatement-aware)."""
        best: dict[str, StatementVintage] = {}
        for v in self.by_code.get(code, ()):
            cur = best.get(v.end_date)
            if cur is None or (v.ann_date, v.update_flag) > (
                cur.ann_date,
                cur.update_flag,
            ):
                best[v.end_date] = v
        return best


def recent_quarter_ends(decision_date: str, n_periods: int) -> tuple[str, ...]:
    """The ``n_periods`` Chinese-fiscal quarter-ends strictly before a YYYYMMDD.

    Deterministic, no wall-clock. Quarter-ends are 0331 / 0630 / 0930 / 1231;
    only those announced (and so periods that ended) before the decision date are
    candidates. Returns oldest → newest.
    """
    if not _DATE_RE.match(decision_date):
        raise ValueError(f"decision_date {decision_date!r} must be YYYYMMDD")
    if n_periods <= 0:
        return ()
    year = int(decision_date[:4])
    ends: list[str] = []
    # Walk back from the decision year, emitting quarter-ends < decision_date.
    y = year
    while len([e for e in ends if e < decision_date]) < n_periods + 4 and y >= 2000:
        for mmdd in ("1231", "0930", "0630", "0331"):
            ends.append(f"{y}{mmdd}")
        y -= 1
    usable = sorted(e for e in set(ends) if e < decision_date)
    return tuple(usable[-n_periods:])


def quality_metric_records(
    store: SnapshotStore,
    *,
    codes: Sequence[str],
    periods: Sequence[str],
) -> dict[str, dict[QualityMetric, list[tuple[str, float]]]]:
    """Build the AF-003 ``fundamentals_scores`` input from PIT statements.

    Per code, per :class:`QualityMetric`, a list of ``(ann_date, value)`` records
    across ``periods`` (the AF-003 selector PIT-picks the latest announce ≤ the
    decision date). Populated metrics:

    * **ROE / GPM** — directly from ``fina_indicator_vip`` (every vintage).
    * **ACCRUALS** — ``(n_income − n_cashflow_act) / total_assets`` per report
      period, joined across income/cashflow/balancesheet. The record's ann_date
      is the **max** of the three statements' latest-vintage ann_dates, so the
      accrual only becomes usable once all three components were announced — no
      look-ahead (a component restated after a decision date gates the whole
      accrual out, conservatively).

    EP_TTM is intentionally left unpopulated here: the cheapness dimension
    (earnings yield / PE) is carried by the AF-002 valuation factor instead, so
    quality (ROE/GPM/accruals) and cheapness (PE/PB/dividend) stay distinct and
    are never double-counted. A code with no statement data maps to an empty
    metric map (AF-003 then yields ``None`` and drops the component).
    """
    code_set = list(dict.fromkeys(codes))
    fina = BackendStatementPIT.build(
        store,
        periods,
        endpoint=EP_FINA,
        fields=["roe", "gross_margin"],
        report_type_filter=None,
    )
    income = BackendStatementPIT.build(
        store,
        periods,
        endpoint=EP_INCOME,
        fields=["n_income"],
    )
    cashflow = BackendStatementPIT.build(
        store,
        periods,
        endpoint=EP_CASHFLOW,
        fields=["n_cashflow_act"],
    )
    balancesheet = BackendStatementPIT.build(
        store,
        periods,
        endpoint=EP_BALANCESHEET,
        fields=["total_assets"],
    )

    out: dict[str, dict[QualityMetric, list[tuple[str, float]]]] = {}
    for code in code_set:
        metrics: dict[QualityMetric, list[tuple[str, float]]] = {}
        roe = fina.announced_values(code, "roe")
        gpm = fina.announced_values(code, "gross_margin")
        if roe:
            metrics[QualityMetric.ROE] = roe
        if gpm:
            metrics[QualityMetric.GPM] = gpm
        accruals = _accrual_records(code, income, cashflow, balancesheet)
        if accruals:
            metrics[QualityMetric.ACCRUALS] = accruals
        if metrics:
            out[code] = metrics
    return out


def _accrual_records(
    code: str,
    income: BackendStatementPIT,
    cashflow: BackendStatementPIT,
    balancesheet: BackendStatementPIT,
) -> list[tuple[str, float]]:
    """``(ann_date, accrual_ratio)`` per report period, PIT-safe (see caller)."""
    inc = income.latest_vintage_by_end(code)
    cfo = cashflow.latest_vintage_by_end(code)
    bs = balancesheet.latest_vintage_by_end(code)
    out: list[tuple[str, float]] = []
    # Sort the shared report periods (oldest → newest) so the record order is
    # deterministic across PYTHONHASHSEED — a set-intersection iteration order
    # would otherwise let AF-003's latest-announcement selector pick a different
    # accrual when two periods share an ann_date (codex AF-002 P2).
    for end_date in sorted(inc.keys() & cfo.keys() & bs.keys()):
        ni = inc[end_date].vals[0]
        oc = cfo[end_date].vals[0]
        ta = bs[end_date].vals[0]
        if ni is None or oc is None or ta is None or ta == 0.0:
            continue
        accrual = (ni - oc) / ta
        if not math.isfinite(accrual):
            continue
        ann = max(inc[end_date].ann_date, cfo[end_date].ann_date, bs[end_date].ann_date)
        out.append((ann, accrual))
    return out
