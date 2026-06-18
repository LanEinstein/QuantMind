"""Round-2 PIT data ingest — index weights, SW industry, fundamentals (R2-1).

Offline batch extension of the round-1 PIT layer (AE-001). Pulls the three new
Tushare endpoints the round-2 *benchmark-relative* redo needs and persists each
byte-exact into the K-002 ``SnapshotStore`` (raw bytes + sha256), plus a
survivorship-keyed coverage manifest so a partial pull can never masquerade as
the full market (round-2 plan §4.1; codex P1-5):

* ``index_weight`` (000300.SH) — CSI300 constituent weights; one snapshot per
  month, queried by month range (the publish date need not be a month-end) and
  keyed by the query month-end;
* ``fina_indicator_vip`` — full-market fundamentals per report period (ROE /
  margin / growth), keyed by the period ``end_date``. The PIT ``ann_date`` join
  + restatement/vintage handling lives in the R2-2 factor builder, not here;
* ``index_member_all`` — the SW industry membership table (``in_date`` /
  ``out_date``), one as-of pull keyed by the pull date (PIT-reconstructable);
* ``stock_basic`` L + D rosters — the explicit survivorship universe the
  fundamentals coverage manifest is checked against (``all_codes`` = listed +
  delisted; never derived from the *current* listed universe).

Discipline mirrors AE-001: idempotent / resumable (skip an already-stored
``(endpoint, key)`` *before* fetching), fail-closed (an empty required frame
stores nothing and records a failure so a re-run retries), deterministic. It is
OFFLINE batch — never wired into a runtime cron, never on the realtime path; the
real multi-hundred-call run is owner-gated, and ``--dry-run`` prints the plan
(period + month-end enumeration) with no network calls first.

Import isolation: ``backend.data.*`` via per-line ``# noqa: TID251`` (the ban on
``backend.{llm,agents,mirofish}`` stays active); ``backend.marketdata_snapshot``
is import-allowed.
"""

from __future__ import annotations

import argparse
import asyncio
import io
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd
import structlog

from backend.data.historical_ingest.rate_limiter import RateLimiter  # noqa: TID251
from backend.data.historical_ingest.serialization import (  # noqa: TID251
    canonical_csv_bytes,
    parse_csv_bytes,
)
from backend.data.historical_ingest.universe import (  # noqa: TID251
    SurvivorshipUniverse,
)
from backend.data.tushare_client import TushareClient  # noqa: TID251
from backend.marketdata_snapshot.coverage import CoverageManifest, CoverageStore
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotOverwriteError, SnapshotStore

from .locked_split import load_daily_calendar

log = structlog.get_logger(component="factor_research.ingest_round2")

VENDOR = "tushare"
CSI300_CODE = "000300.SH"
# Tushare ``index_weight('000300.SH')`` has no data before 2016-01 (probed
# 2026-06-18: 2015 returns 0 rows; first publish date 20160129). Months before
# this are skipped — their emptiness is a permanent vendor limit, not a failure
# to retry — so the benchmark-relative arm's weight history simply starts 2016.
CSI300_WEIGHT_FIRST_MONTH = "201601"
# Store-level endpoint tags. The L/D rosters share an as-of date, so they get
# distinct tags (the snapshot key is (vendor, endpoint, trade_date)); the real
# Tushare call is ``stock_basic`` with the list_status param either way.
EP_INDEX_WEIGHT = "index_weight"
EP_FINA = "fina_indicator_vip"
EP_INDEX_MEMBER = "index_member_all"
EP_STOCK_BASIC_L = "stock_basic_listed"
EP_STOCK_BASIC_D = "stock_basic_delisted"
STOCK_BASIC_FIELDS = "ts_code,name,list_date,delist_date"
_QUARTER_ENDS = ("0331", "0630", "0930", "1231")

_STATUS_INGESTED = "ingested"
_STATUS_SKIPPED = "skipped"
_STATUS_FAILED = "failed"


@runtime_checkable
class _Round2Client(Protocol):
    """The new-endpoint subset of :class:`TushareClient` this job needs."""

    async def index_weight(
        self,
        index_code: str,
        *,
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame: ...
    async def fina_indicator_vip(self, period: str) -> pd.DataFrame: ...
    async def index_member_all(self) -> pd.DataFrame: ...
    async def stock_basic(self, *, list_status: str, fields: str) -> pd.DataFrame: ...


@dataclass(frozen=True)
class EndpointResult:
    """Outcome of one (endpoint, key) pull (immutable)."""

    endpoint: str
    key: str  # the 8-digit snapshot trade_date / report period this pull keys on
    status: str
    rows: int
    sha256: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class Round2IngestReport:
    """Immutable summary of a round-2 ingest run."""

    results: tuple[EndpointResult, ...]
    fina_coverage: tuple[CoverageManifest, ...] = ()

    def _count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    @property
    def ingested(self) -> int:
        return self._count(_STATUS_INGESTED)

    @property
    def skipped(self) -> int:
        return self._count(_STATUS_SKIPPED)

    @property
    def failed(self) -> int:
        return self._count(_STATUS_FAILED)

    @property
    def failures(self) -> tuple[EndpointResult, ...]:
        return tuple(r for r in self.results if r.status == _STATUS_FAILED)


# --- pure enumerators --------------------------------------------------------


def report_periods(first_year: int, last_date: str) -> list[str]:
    """Quarterly report-period end-dates (YYYYMMDD) from ``first_year``..``last_date``.

    Returns every ``{year}{0331|0630|0930|1231}`` ≤ ``last_date``. These are the
    ``fina_indicator_vip`` period keys — the report-period ends, NOT trade dates
    (the PIT ``ann_date`` gating happens later in the factor builder).
    """
    if not (len(last_date) == 8 and last_date.isdigit()):
        raise ValueError(f"last_date {last_date!r} must be YYYYMMDD")
    last_year = int(last_date[:4])
    out: list[str] = []
    for year in range(first_year, last_year + 1):
        for mmdd in _QUARTER_ENDS:
            period = f"{year}{mmdd}"
            if period <= last_date:
                out.append(period)
    return out


def month_end_trade_dates(
    calendar: Sequence[str], *, include_partial_last: bool = False
) -> list[str]:
    """True month-end trade date of each COMPLETE ``YYYYMM`` in ``calendar``.

    The highest month present may be mid-month (the calendar ends wherever the
    data ends), so its "last" date is not a real month-end and would shift on a
    later re-run — producing a *second* index_weight snapshot for the same month
    (codex P1-2). It is therefore dropped unless ``include_partial_last`` is set:
    a month is included only once the calendar proves complete by extending into
    a later month, which makes the per-month key stable + the ingest idempotent.
    """
    last_by_month: dict[str, str] = {}
    for d in sorted(calendar):
        last_by_month[d[:6]] = d
    months = sorted(last_by_month)
    if not include_partial_last and months:
        months = months[:-1]  # drop the highest (possibly-incomplete) month
    return [last_by_month[m] for m in months]


# --- persistence (fetch only when absent — idempotent / resumable) -----------


async def _ingest_one(
    store: SnapshotStore,
    *,
    endpoint: str,
    trade_date: str,
    params: dict[str, str],
    fetch: Callable[[], Awaitable[pd.DataFrame]],
    now: Callable[[], datetime],
    require_non_empty: bool,
    rate_limiter: RateLimiter | None = None,
) -> EndpointResult:
    """Skip-if-present, else fetch + persist one snapshot byte-exact.

    The presence check runs *before* any throttle/fetch, so a resume re-run over
    already-stored keys costs no rate-limit budget and no network call.
    """
    existing = store.latest(vendor=VENDOR, endpoint=endpoint, trade_date=trade_date)
    if existing is not None:
        return EndpointResult(
            endpoint=endpoint,
            key=trade_date,
            status=_STATUS_SKIPPED,
            rows=int(existing.metadata.get("rows", 0)),
            sha256=existing.raw_payload_sha256,
        )
    if rate_limiter is not None:
        await asyncio.to_thread(rate_limiter.acquire)
    try:
        frame = await fetch()
    except Exception as exc:  # noqa: BLE001 - record + continue (resume retries)
        log.warning(
            "round2_fetch_failed", endpoint=endpoint, key=trade_date, error=str(exc)
        )
        return EndpointResult(
            endpoint=endpoint,
            key=trade_date,
            status=_STATUS_FAILED,
            rows=0,
            error=str(exc),
        )
    if require_non_empty and (frame is None or frame.empty):
        log.error("round2_empty_required", endpoint=endpoint, key=trade_date)
        return EndpointResult(
            endpoint=endpoint,
            key=trade_date,
            status=_STATUS_FAILED,
            rows=0,
            error="empty frame for a required pull",
        )
    frame = frame if frame is not None else pd.DataFrame()
    raw = canonical_csv_bytes(frame)
    snapshot = MarketDataSnapshot.create(
        vendor=VENDOR,
        endpoint=endpoint,
        params=params,
        trade_date=trade_date,
        raw_payload=raw,
        encoding="csv",
        compression="none",
        fetch_time_utc=now(),
        metadata={"rows": int(len(frame))},
    )
    try:
        store.put(snapshot)
    except SnapshotOverwriteError:
        return EndpointResult(
            endpoint=endpoint,
            key=trade_date,
            status=_STATUS_SKIPPED,
            rows=int(len(frame)),
            sha256=snapshot.raw_payload_sha256,
        )
    return EndpointResult(
        endpoint=endpoint,
        key=trade_date,
        status=_STATUS_INGESTED,
        rows=int(len(frame)),
        sha256=snapshot.raw_payload_sha256,
    )


# --- per-endpoint ingests ----------------------------------------------------


async def ingest_index_weight(
    client: _Round2Client,
    store: SnapshotStore,
    calendar: Sequence[str],
    *,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
    first_month: str = CSI300_WEIGHT_FIRST_MONTH,
) -> list[EndpointResult]:
    """One CSI300 weight snapshot per COMPLETE calendar month from ``first_month``.

    Months before ``first_month`` are skipped (Tushare has no CSI300 weights
    before 2016 — a permanent vendor limit, not a transient failure to retry).
    """
    out: list[EndpointResult] = []
    for d in month_end_trade_dates(calendar):
        if d[:6] < first_month:
            continue
        month_start = d[:6] + "01"
        out.append(
            await _ingest_one(
                store,
                endpoint=EP_INDEX_WEIGHT,
                trade_date=d,
                params={
                    "index_code": CSI300_CODE,
                    "start_date": month_start,
                    "end_date": d,
                },
                fetch=partial(
                    client.index_weight,
                    CSI300_CODE,
                    start_date=month_start,
                    end_date=d,
                ),
                now=now,
                require_non_empty=True,
                rate_limiter=rate_limiter,
            )
        )
    return out


async def ingest_fina_indicator(
    client: _Round2Client,
    store: SnapshotStore,
    periods: Sequence[str],
    *,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
) -> list[EndpointResult]:
    """One full-market fundamentals snapshot per report period."""
    out: list[EndpointResult] = []
    for period in periods:
        out.append(
            await _ingest_one(
                store,
                endpoint=EP_FINA,
                trade_date=period,
                params={"period": period},
                fetch=partial(client.fina_indicator_vip, period),
                now=now,
                require_non_empty=True,
                rate_limiter=rate_limiter,
            )
        )
    return out


async def ingest_index_member_all(
    client: _Round2Client,
    store: SnapshotStore,
    asof: str,
    *,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
) -> EndpointResult:
    """One as-of SW industry membership snapshot (in/out dates embedded)."""
    return await _ingest_one(
        store,
        endpoint=EP_INDEX_MEMBER,
        trade_date=asof,
        params={"asof": asof},
        fetch=client.index_member_all,
        now=now,
        require_non_empty=True,
        rate_limiter=rate_limiter,
    )


async def ingest_stock_basic(
    client: _Round2Client,
    store: SnapshotStore,
    asof: str,
    *,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
) -> list[EndpointResult]:
    """As-of listed (L) + delisted (D) rosters — the survivorship universe.

    Both are required non-empty: an empty delisted roster would silently make
    the universe survivorship-biased (the very red line this guards).
    """
    out: list[EndpointResult] = []
    for endpoint, list_status in (
        (EP_STOCK_BASIC_L, "L"),
        (EP_STOCK_BASIC_D, "D"),
    ):
        out.append(
            await _ingest_one(
                store,
                endpoint=endpoint,
                trade_date=asof,
                params={"list_status": list_status},
                fetch=partial(
                    client.stock_basic,
                    list_status=list_status,
                    fields=STOCK_BASIC_FIELDS,
                ),
                now=now,
                require_non_empty=True,
                rate_limiter=rate_limiter,
            )
        )
    return out


# --- survivorship + coverage -------------------------------------------------


def _read_frame(store: SnapshotStore, endpoint: str, trade_date: str) -> pd.DataFrame:
    snapshot = store.latest(vendor=VENDOR, endpoint=endpoint, trade_date=trade_date)
    if snapshot is None:
        raise FileNotFoundError(f"no snapshot for {endpoint} as-of {trade_date}")
    return parse_csv_bytes(snapshot.raw_payload)


def _read_roster_frame(store: SnapshotStore, endpoint: str, asof: str) -> pd.DataFrame:
    """Read a stored roster as ALL-STRING columns (dates stay literal).

    The stored bytes are clean (e.g. ``20260610``), but a roster's mostly-empty
    ``delist_date`` column re-infers to ``float64`` under default ``read_csv``,
    rendering ``20260610`` as ``20260610.0`` — which ``SurvivorshipUniverse``
    rejects. ``dtype=str`` + ``keep_default_na=False`` keeps every 8-digit date a
    literal string and an empty cell an empty string.
    """
    snapshot = store.latest(vendor=VENDOR, endpoint=endpoint, trade_date=asof)
    if snapshot is None:
        raise FileNotFoundError(f"no snapshot for {endpoint} as-of {asof}")
    return pd.read_csv(
        io.StringIO(snapshot.raw_payload.decode("utf-8")),
        dtype=str,
        keep_default_na=False,
    )


def load_survivorship(store: SnapshotStore, asof: str) -> SurvivorshipUniverse:
    """Build the survivorship universe from the stored L + D rosters."""
    listed = _read_roster_frame(store, EP_STOCK_BASIC_L, asof)
    delisted = _read_roster_frame(store, EP_STOCK_BASIC_D, asof)
    return SurvivorshipUniverse.from_stock_basic(listed, delisted)


def build_fina_coverage_manifests(
    store: SnapshotStore,
    periods: Sequence[str],
    universe: SurvivorshipUniverse,
) -> list[CoverageManifest]:
    """One coverage manifest PER report period (codex P1-1, fail-closed).

    A cross-period ``ts_code`` union would let a wholly-missing period (or a
    code missing from one period but present in another) masquerade as
    ``completeness=1.0``. Instead each period gets its own manifest:

    * ``requested`` = the codes tradable as-of that period end
      (``universe.tradable_asof(period)``) — the precise denominator, so a
      long-delisted code is not falsely flagged as missing a recent report, and
      a code listed in that period that filed no report IS flagged;
    * ``delivered`` = the ``ts_code`` set of that period's stored snapshot.

    A missing period snapshot raises :class:`FileNotFoundError` (fail-closed —
    never silently skipped); the caller builds coverage only once every period
    ingested successfully.
    """
    if not periods:
        raise ValueError("no report periods to build coverage from")
    manifests: list[CoverageManifest] = []
    for period in periods:
        snapshot = store.latest(vendor=VENDOR, endpoint=EP_FINA, trade_date=period)
        if snapshot is None:
            raise FileNotFoundError(
                f"fina_indicator period {period} snapshot missing — "
                "cannot build coverage (fail-closed)"
            )
        frame = parse_csv_bytes(snapshot.raw_payload)
        delivered = {str(c).strip() for c in frame.get("ts_code", pd.Series(dtype=str))}
        requested = sorted(universe.tradable_asof(period))
        manifests.append(
            CoverageManifest(
                granularity="period",
                endpoint=EP_FINA,
                params={"period": period},
                session_start=period,
                session_end=period,
                requested_universe=tuple(requested),
                delivered_universe=tuple(sorted(delivered)),
            )
        )
    return manifests


# --- orchestrator ------------------------------------------------------------


def _put_coverage_idempotent(
    coverage_store: CoverageStore, manifest: CoverageManifest
) -> None:
    """Append a coverage manifest only if absent or content-changed (codex P2-1).

    The endpoint snapshots are idempotent but ``CoverageStore`` is append-only,
    so an unconditional re-put would grow a duplicate row every resume re-run.
    Skip only when a *byte-identical* manifest already exists for
    ``(endpoint, session_end)`` — compared via the full ``model_dump`` so a
    corrected ``params`` / ``granularity`` (same universes) still appends a fix.
    """
    existing = coverage_store.get(
        endpoint=manifest.endpoint, session_end=manifest.session_end
    )
    if existing is not None and existing.model_dump(mode="json") == manifest.model_dump(
        mode="json"
    ):
        return
    coverage_store.put(manifest)


def _build_coverage(
    store: SnapshotStore,
    coverage_store: CoverageStore,
    *,
    periods: Sequence[str],
    asof: str,
    blocking: bool,
) -> tuple[list[EndpointResult], tuple[CoverageManifest, ...]]:
    """Build + persist per-period fundamentals coverage, fail-closed.

    Coverage is built only when every fundamentals + roster pull succeeded (a
    partial run's coverage would mislead, and its failures are already loud in
    ``results``). When it IS attempted, a missing period snapshot or unbuildable
    survivorship universe is a data-integrity gap → recorded as a FAILED
    ``EndpointResult`` (fail-closed, so ``report.failed`` and the CLI exit
    reflect it), never a bare warning that lets the run report success (codex
    verify P1). Returns ``(extra_results, coverage_manifests)``.
    """
    if not periods:
        return [], ()
    if blocking:
        log.warning("round2_coverage_skipped", reason="blocking ingest failures")
        return [], ()
    try:
        universe = load_survivorship(store, asof)
        manifests = build_fina_coverage_manifests(store, periods, universe)
    except (FileNotFoundError, ValueError) as exc:
        log.error("round2_coverage_failed", error=str(exc))
        return [
            EndpointResult(
                endpoint="coverage",
                key=asof,
                status=_STATUS_FAILED,
                rows=0,
                error=str(exc),
            )
        ], ()
    for manifest in manifests:
        _put_coverage_idempotent(coverage_store, manifest)
    return [], tuple(manifests)


async def ingest_round2(
    client: _Round2Client,
    store: SnapshotStore,
    coverage_store: CoverageStore,
    *,
    calendar: Sequence[str],
    first_year: int,
    asof: str,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
) -> Round2IngestReport:
    """Run all round-2 ingests + build the per-period fundamentals coverage."""
    results: list[EndpointResult] = []
    results.extend(
        await ingest_stock_basic(
            client, store, asof, now=now, rate_limiter=rate_limiter
        )
    )
    results.append(
        await ingest_index_member_all(
            client, store, asof, now=now, rate_limiter=rate_limiter
        )
    )
    results.extend(
        await ingest_index_weight(
            client, store, calendar, now=now, rate_limiter=rate_limiter
        )
    )
    last_date = calendar[-1] if calendar else asof
    periods = report_periods(first_year, last_date)
    results.extend(
        await ingest_fina_indicator(
            client, store, periods, now=now, rate_limiter=rate_limiter
        )
    )

    blocking = any(
        r.status == _STATUS_FAILED
        and r.endpoint in (EP_FINA, EP_STOCK_BASIC_L, EP_STOCK_BASIC_D)
        for r in results
    )
    cov_results, coverage = _build_coverage(
        store, coverage_store, periods=periods, asof=asof, blocking=blocking
    )
    results.extend(cov_results)
    return Round2IngestReport(results=tuple(results), fina_coverage=coverage)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--first-year", type=int, default=2015)
    parser.add_argument(
        "--asof",
        default="",
        help="as-of YYYYMMDD for membership/rosters (default today UTC)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan; NO network calls"
    )
    parser.add_argument(
        "--max-per-minute",
        type=int,
        default=400,
        help="Tushare call throttle (<=0 disables); 5000-pt tier allows ~500/min",
    )
    args = parser.parse_args()

    calendar = load_daily_calendar(args.snapshot_root)
    asof = args.asof or datetime.now(UTC).strftime("%Y%m%d")
    last_date = calendar[-1] if calendar else asof

    if args.dry_run:
        month_ends = [
            d
            for d in month_end_trade_dates(calendar)
            if d[:6] >= CSI300_WEIGHT_FIRST_MONTH
        ]
        periods = report_periods(args.first_year, last_date)
        print(
            f"[dry-run] calendar {len(calendar)} td "
            f"({calendar[0] if calendar else '-'}..{last_date})"
        )
        me_span = f"{month_ends[0]}..{month_ends[-1]}" if month_ends else "-"
        pd_span = f"{periods[0]}..{periods[-1]}" if periods else "-"
        print(f"[dry-run] index_weight month snapshots: {len(month_ends)} ({me_span})")
        print(f"[dry-run] fina_indicator periods: {len(periods)} ({pd_span})")
        print(f"[dry-run] index_member_all + stock_basic L/D as-of {asof}")
        print("[dry-run] no network calls made.")
        return

    client = TushareClient()  # real, owner-gated heavy run
    store = SnapshotStore(args.snapshot_root)
    # Match the AE-001 coverage layout: snapshot_root/coverage/coverage.jsonl.
    coverage_store = CoverageStore(Path(args.snapshot_root) / "coverage")
    report = asyncio.run(
        ingest_round2(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=args.first_year,
            asof=asof,
            now=lambda: datetime.now(UTC),
            rate_limiter=RateLimiter(args.max_per_minute),
        )
    )
    print(
        f"round2 ingest: ingested={report.ingested} skipped={report.skipped} "
        f"failed={report.failed}"
    )
    if report.fina_coverage:
        worst = min(report.fina_coverage, key=lambda m: m.completeness)
        n_incomplete = sum(1 for m in report.fina_coverage if not m.is_complete)
        print(
            f"fina coverage: {len(report.fina_coverage)} period manifests, "
            f"{n_incomplete} incomplete; worst {worst.session_end} "
            f"completeness={worst.completeness:.4f} "
            f"missing={len(worst.missing_symbols)}"
        )
    for fail in report.failures:
        print(f"  FAILED {fail.endpoint} {fail.key}: {fail.error}")
    # Non-zero exit on any failure (fail-closed) so a resume re-run is obvious.
    raise SystemExit(1 if report.failed else 0)


if __name__ == "__main__":
    main()


__all__ = [
    "CSI300_CODE",
    "EP_FINA",
    "EP_INDEX_MEMBER",
    "EP_INDEX_WEIGHT",
    "EP_STOCK_BASIC_D",
    "EP_STOCK_BASIC_L",
    "EndpointResult",
    "Round2IngestReport",
    "build_fina_coverage_manifests",
    "ingest_fina_indicator",
    "ingest_index_member_all",
    "ingest_index_weight",
    "ingest_round2",
    "ingest_stock_basic",
    "load_survivorship",
    "month_end_trade_dates",
    "report_periods",
]
