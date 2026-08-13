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
import hashlib
import io
from calendar import monthrange
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

# Round-3 (R3-1) financial-statement endpoints — accruals / asset-growth source.
# Each is full-market per report period (like fina_indicator_vip); the snapshot
# tag EQUALS the client method name so ``getattr(client, endpoint)`` fetches it.
EP_INCOME = "income_vip"
EP_CASHFLOW = "cashflow_vip"
EP_BALANCESHEET = "balancesheet_vip"
STATEMENT_ENDPOINTS: tuple[str, ...] = (EP_INCOME, EP_CASHFLOW, EP_BALANCESHEET)
# Historical name changes (PIT ST-flag source). Paginated by the change
# start_date YEAR so the full timeline is captured under the per-call row cap; an
# empty year is legitimate (no name changes), so namechange is NOT require-non-empty.
EP_NAMECHANGE = "namechange"
# A-share name changes predate the 1998 ST system only trivially; 1990 floor
# covers every name in effect during the 2015-2026 research window with margin.
NAMECHANGE_FIRST_YEAR = 1990
# Canonical namechange columns. A legitimate no-change year returns a ZERO-column
# empty frame whose CSV is bare ``\n`` (unparseable — ``parse_csv_bytes`` raises
# EmptyDataError, codex P2). Empty pages are stored with this header instead so a
# PIT ST-history reader can replay them as a 0-row, known-column frame.
NAMECHANGE_FIELDS: tuple[str, ...] = (
    "ts_code",
    "name",
    "start_date",
    "end_date",
    "ann_date",
    "change_reason",
)

# Round-4 (R4-2) broker analyst forecast / rating endpoint (report_rc) — the
# analyst-revision alpha source. A sparse STREAM (reports on weekends too), ingested
# per calendar month via a paginated date-range query (single-call cap, see
# tushare_client.REPORT_RC_PAGE_LIMIT). Unlike the statements there is NO fixed
# survivorship universe → no coverage manifest; integrity = in-client pagination to
# a short page (no silent truncation) + byte+checksum + idempotent.
EP_REPORT_RC = "report_rc"
# One year before train_val (2015) so a trailing revision window is warm at the
# panel start. report_rc history runs back to <=2010 and EVERY 2014 month is dense
# (real-probe 2026-06-20: 2014 monthly rows 4146-12906, no empty months; 2010-2013
# also dense), so the 2014 floor enumerates only data-bearing months → an empty pull
# is genuine corruption/truncation, NOT a legitimate empty pre-history period. Hence
# require_non_empty=True below stays a valid fail-closed check and never wrongly
# fails a fresh run.
REPORT_RC_FIRST_YEAR = 2014

_STATUS_INGESTED = "ingested"
_STATUS_SKIPPED = "skipped"
_STATUS_FAILED = "failed"

# A per-page rate-limit hook the paginated statement pulls await before each
# real SDK call (one token per call, not one per period — see _ingest_one).
_Throttle = Callable[[], Awaitable[None]]


def _make_throttle(rate_limiter: RateLimiter | None) -> _Throttle | None:
    """Build a per-page throttle that acquires one rate-limit token off-thread.

    Handed to a client method that paginates internally (one token per real SDK
    page); the caller then passes ``rate_limiter=None`` to :func:`_ingest_one` so
    a multi-page pull is not double- or under-throttled.
    """
    if rate_limiter is None:
        return None
    limiter = rate_limiter

    async def throttle() -> None:
        await asyncio.to_thread(limiter.acquire)

    return throttle


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
    async def fina_indicator_vip(
        self, period: str, *, throttle: _Throttle | None = None
    ) -> pd.DataFrame: ...
    async def income_vip(
        self, period: str, *, throttle: _Throttle | None = None
    ) -> pd.DataFrame: ...
    async def cashflow_vip(
        self, period: str, *, throttle: _Throttle | None = None
    ) -> pd.DataFrame: ...
    async def balancesheet_vip(
        self, period: str, *, throttle: _Throttle | None = None
    ) -> pd.DataFrame: ...
    async def report_rc(
        self,
        *,
        start_date: str = "",
        end_date: str = "",
        throttle: _Throttle | None = None,
    ) -> pd.DataFrame: ...
    async def namechange(
        self, *, start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame: ...
    async def index_member_all(self) -> pd.DataFrame: ...
    async def stock_basic(self, *, list_status: str, fields: str) -> pd.DataFrame: ...
    # QGR-1 short-horizon / theme endpoints.
    async def stk_limit(
        self, trade_date: str, *, throttle: _Throttle | None = None
    ) -> pd.DataFrame: ...
    async def limit_list_d(self, trade_date: str) -> pd.DataFrame: ...
    async def suspend_d(self, trade_date: str) -> pd.DataFrame: ...
    async def cyq_perf(
        self, trade_date: str, *, throttle: _Throttle | None = None
    ) -> pd.DataFrame: ...
    async def stk_factor_pro(
        self, trade_date: str, *, throttle: _Throttle | None = None
    ) -> pd.DataFrame: ...
    async def forecast_vip(
        self,
        period: str = "",
        *,
        start_date: str = "",
        end_date: str = "",
        throttle: _Throttle | None = None,
    ) -> pd.DataFrame: ...
    async def express_vip(
        self,
        period: str = "",
        *,
        start_date: str = "",
        end_date: str = "",
        throttle: _Throttle | None = None,
    ) -> pd.DataFrame: ...
    async def ths_index(self, *, index_type: str = "") -> pd.DataFrame: ...
    async def index_classify(
        self, *, level: str = "", src: str = "SW2021"
    ) -> pd.DataFrame: ...


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


def report_rc_month_ranges(
    first_year: int, last_date: str
) -> list[tuple[str, str, str]]:
    """One ``(start_date, end_date, key)`` per calendar month, first_year..last_date.

    report_rc is a sparse STREAM with reports on weekends too, so each month is
    queried over its full CALENDAR span ``[YYYYMM01, last-calendar-day]`` — NOT the
    last *trade* date (which would drop weekend-published reports). The final
    (possibly partial) month is capped at ``last_date`` so the pull never reaches
    past the locked research calendar. ``snapshot_key`` = the end date (stable for a
    locked calendar → idempotent resume). ``first_year`` is one year before
    train_val so a trailing revision window is warm at the start of the panel.
    """
    if not (len(last_date) == 8 and last_date.isdigit()):
        raise ValueError(f"last_date {last_date!r} must be YYYYMMDD")
    last_year, last_month = int(last_date[:4]), int(last_date[4:6])
    out: list[tuple[str, str, str]] = []
    for year in range(first_year, last_year + 1):
        for month in range(1, 13):
            if (year, month) > (last_year, last_month):
                break
            start = f"{year}{month:02d}01"
            end = f"{year}{month:02d}{monthrange(year, month)[1]:02d}"
            if end > last_date:
                end = last_date
            out.append((start, end, end))
    return out


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
    reingest: bool = False,
) -> EndpointResult:
    """Skip-if-present, else fetch + persist one snapshot byte-exact.

    The presence check runs *before* any throttle/fetch, so a resume re-run over
    already-stored keys costs no rate-limit budget and no network call.

    ``reingest=True`` deliberately re-pulls even when a snapshot is already
    stored: the bytes are compared to the latest stored version, and a CHANGED
    payload is appended as a NEW version (append-only restatement, old bytes
    kept) so ``store.latest`` reflects the corrected pull. An UNCHANGED payload
    is reported SKIPPED. This is the one-time repair path for the *_vip
    statements truncated by the pre-pagination per-call cap (R3-1).
    """
    existing = store.latest(vendor=VENDOR, endpoint=endpoint, trade_date=trade_date)
    if existing is not None and not reingest:
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
    new_sha = hashlib.sha256(raw).hexdigest()
    if existing is not None and existing.raw_payload_sha256 == new_sha:
        # reingest path only: the re-pull is byte-identical to the latest stored
        # version (already complete) — no restatement needed.
        return EndpointResult(
            endpoint=endpoint,
            key=trade_date,
            status=_STATUS_SKIPPED,
            rows=int(len(frame)),
            sha256=existing.raw_payload_sha256,
        )
    version = existing.version + 1 if existing is not None else 1
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
        version=version,
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
    """One full-market fundamentals snapshot per report period.

    fina_indicator_vip paginates INSIDE the client (D2, 2026-06-23), so — like
    the statement pulls — the throttle is handed to the client (awaited once per
    page) and ``_ingest_one`` is told NOT to throttle (``rate_limiter=None``);
    otherwise a multi-page period would acquire one token for N real SDK calls
    and silently overrun Tushare's per-minute cap.
    """
    throttle = _make_throttle(rate_limiter)
    out: list[EndpointResult] = []
    for period in periods:
        out.append(
            await _ingest_one(
                store,
                endpoint=EP_FINA,
                trade_date=period,
                params={"period": period},
                fetch=partial(client.fina_indicator_vip, period, throttle=throttle),
                now=now,
                require_non_empty=True,
                rate_limiter=None,
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
    *,
    endpoint: str = EP_FINA,
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
    ingested successfully. ``endpoint`` defaults to ``fina_indicator_vip`` (R2-1
    behavior byte-identical) and is parameterized so the R3-1 income / cashflow /
    balancesheet statements get the same per-period survivorship-keyed coverage.
    """
    if not periods:
        raise ValueError("no report periods to build coverage from")
    manifests: list[CoverageManifest] = []
    for period in periods:
        snapshot = store.latest(vendor=VENDOR, endpoint=endpoint, trade_date=period)
        if snapshot is None:
            raise FileNotFoundError(
                f"{endpoint} period {period} snapshot missing — "
                "cannot build coverage (fail-closed)"
            )
        frame = parse_csv_bytes(snapshot.raw_payload)
        delivered = {str(c).strip() for c in frame.get("ts_code", pd.Series(dtype=str))}
        requested = sorted(universe.tradable_asof(period))
        manifests.append(
            CoverageManifest(
                granularity="period",
                endpoint=endpoint,
                params={"period": period},
                session_start=period,
                session_end=period,
                requested_universe=tuple(requested),
                delivered_universe=tuple(sorted(delivered)),
            )
        )
    return manifests


# --- orchestrator ------------------------------------------------------------


def _put_coverage_manifests_idempotent(
    coverage_store: CoverageStore, manifests: Sequence[CoverageManifest]
) -> None:
    """Append manifests only when absent or content-changed (codex P2-1).

    The endpoint snapshots are idempotent but ``CoverageStore`` is append-only,
    so an unconditional re-put would grow a duplicate row every resume re-run.
    Read all requested keys in one pass; the production coverage log is too
    large to rescan once per report period. Skip only when a *byte-identical*
    manifest already exists for ``(endpoint, session_end)`` — compared via the
    full ``model_dump`` so corrected content still appends a fix.
    """
    keys = {(manifest.endpoint, manifest.session_end) for manifest in manifests}
    existing = coverage_store.get_many(keys)
    for manifest in manifests:
        key = (manifest.endpoint, manifest.session_end)
        previous = existing.get(key)
        if previous is not None and previous.model_dump(
            mode="json"
        ) == manifest.model_dump(mode="json"):
            continue
        coverage_store.put(manifest)
        existing[key] = manifest


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
    _put_coverage_manifests_idempotent(coverage_store, manifests)
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


# --- round-3 statement + namechange ingests (R3-1) ---------------------------


async def ingest_statement(
    client: _Round2Client,
    store: SnapshotStore,
    periods: Sequence[str],
    *,
    endpoint: str,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
    reingest: bool = False,
) -> list[EndpointResult]:
    """One full-market statement snapshot per report period (the 3 R3-1 tables).

    ``endpoint`` is the snapshot tag AND the client method name (e.g.
    ``income_vip``) — the byte-exact / idempotent / fail-closed mechanics are
    shared with :func:`ingest_fina_indicator` via :func:`_ingest_one`.
    ``reingest=True`` re-pulls and version-bumps changed payloads (the R3-1
    truncation repair) — see :func:`_ingest_one`.

    Statement pulls paginate INSIDE the client, so the throttle is handed to the
    client (awaited once per page) and ``_ingest_one`` is told NOT to throttle
    (``rate_limiter=None``) — otherwise a multi-page period would either
    double-count its first page or under-count the rest, overrunning the cap.
    """
    fetch_method = getattr(client, endpoint)
    throttle = _make_throttle(rate_limiter)
    out: list[EndpointResult] = []
    for period in periods:
        out.append(
            await _ingest_one(
                store,
                endpoint=endpoint,
                trade_date=period,
                params={"period": period},
                fetch=partial(fetch_method, period, throttle=throttle),
                now=now,
                require_non_empty=True,
                rate_limiter=None,
                reingest=reingest,
            )
        )
    return out


def namechange_years(first_year: int, asof: str) -> list[int]:
    """Years to page namechange over: ``first_year``..``year(asof)`` inclusive."""
    if not (len(asof) == 8 and asof.isdigit()):
        raise ValueError(f"asof {asof!r} must be YYYYMMDD")
    return list(range(first_year, int(asof[:4]) + 1))


def namechange_pages(first_year: int, asof: str) -> list[tuple[str, str, str]]:
    """``(start_date, end_date, snapshot_key)`` for each namechange year page.

    A COMPLETE past year is ``(YYYY0101, YYYY1231, YYYY1231)`` — a stable key so
    reruns skip it. The CURRENT (in-progress) year is ``(YYYY0101, asof, asof)``:
    the page never requests beyond ``asof`` (no future-dated rows) and is keyed by
    ``asof``, so a later rerun with a larger ``asof`` writes a NEW snapshot that
    captures name changes after the first run instead of being skipped (codex P2).
    """
    asof_year = int(asof[:4])
    pages: list[tuple[str, str, str]] = []
    for year in namechange_years(first_year, asof):
        if year < asof_year:
            pages.append((f"{year}0101", f"{year}1231", f"{year}1231"))
        else:
            pages.append((f"{year}0101", asof, asof))
    return pages


async def ingest_namechange(
    client: _Round2Client,
    store: SnapshotStore,
    *,
    first_year: int,
    asof: str,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
) -> list[EndpointResult]:
    """One namechange snapshot per year of the change ``start_date`` (full timeline).

    ``require_non_empty=False``: a year with no name changes legitimately returns
    an empty frame and is stored (so a resume skips it) — only a fetch EXCEPTION
    is recorded FAILED for retry. The empty frame is normalised to the canonical
    :data:`NAMECHANGE_FIELDS` header so the stored CSV is replayable (codex P2).
    The current year is keyed by ``asof`` (not the year-end) — see
    :func:`namechange_pages`.
    """

    async def _fetch(start: str, end: str) -> pd.DataFrame:
        frame = await client.namechange(start_date=start, end_date=end)
        if frame is None or frame.empty:
            return pd.DataFrame(columns=list(NAMECHANGE_FIELDS))
        return frame

    out: list[EndpointResult] = []
    for start, end, key in namechange_pages(first_year, asof):
        out.append(
            await _ingest_one(
                store,
                endpoint=EP_NAMECHANGE,
                trade_date=key,
                params={"start_date": start, "end_date": end},
                fetch=partial(_fetch, start, end),
                now=now,
                require_non_empty=False,
                rate_limiter=rate_limiter,
            )
        )
    return out


def build_statement_coverage_manifests(
    store: SnapshotStore,
    periods: Sequence[str],
    universe: SurvivorshipUniverse,
) -> list[CoverageManifest]:
    """Per-period survivorship-keyed coverage for all three R3-1 statements."""
    manifests: list[CoverageManifest] = []
    for endpoint in STATEMENT_ENDPOINTS:
        manifests.extend(
            build_fina_coverage_manifests(store, periods, universe, endpoint=endpoint)
        )
    return manifests


def _build_statement_coverage(
    store: SnapshotStore,
    coverage_store: CoverageStore,
    *,
    periods: Sequence[str],
    asof: str,
    blocking: bool,
) -> tuple[list[EndpointResult], tuple[CoverageManifest, ...]]:
    """Build + persist per-period coverage for the 3 statements, fail-closed.

    Mirrors :func:`_build_coverage`: skipped on blocking statement failures;
    a missing snapshot or unbuildable survivorship universe is a FAILED
    ``EndpointResult`` (never a silent pass). Returns ``(extra_results, coverage)``.
    """
    if not periods:
        return [], ()
    if blocking:
        log.warning("round3_coverage_skipped", reason="blocking ingest failures")
        return [], ()
    try:
        universe = load_survivorship(store, asof)
        manifests = build_statement_coverage_manifests(store, periods, universe)
    except (FileNotFoundError, ValueError) as exc:
        log.error("round3_coverage_failed", error=str(exc))
        return [
            EndpointResult(
                endpoint="coverage",
                key=asof,
                status=_STATUS_FAILED,
                rows=0,
                error=str(exc),
            )
        ], ()
    _put_coverage_manifests_idempotent(coverage_store, manifests)
    return [], tuple(manifests)


async def ingest_round3(
    client: _Round2Client,
    store: SnapshotStore,
    coverage_store: CoverageStore,
    *,
    calendar: Sequence[str],
    first_year: int,
    asof: str,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
    namechange_first_year: int = NAMECHANGE_FIRST_YEAR,
    restate_statements: bool = False,
) -> Round2IngestReport:
    """R3-1: ingest income/cashflow/balancesheet (per-period) + namechange (per-year).

    The L/D survivorship rosters (ingested by :func:`ingest_round2`) must already
    be present for the statement coverage step — fail-closed otherwise. Idempotent
    / resumable / rate-limited / byte-exact, same as round-2.

    ``restate_statements=True`` is the truncation-repair path: it re-pulls the 3
    statements with pagination and appends a NEW version wherever the prior pull
    was capped (see :func:`_ingest_one`), then rebuilds coverage from the
    corrected ``store.latest``. namechange (never truncated — small per-year
    pages) is skipped in this mode.
    """
    results: list[EndpointResult] = []
    last_date = calendar[-1] if calendar else asof
    periods = report_periods(first_year, last_date)
    for endpoint in STATEMENT_ENDPOINTS:
        results.extend(
            await ingest_statement(
                client,
                store,
                periods,
                endpoint=endpoint,
                now=now,
                rate_limiter=rate_limiter,
                reingest=restate_statements,
            )
        )
    if not restate_statements:
        results.extend(
            await ingest_namechange(
                client,
                store,
                first_year=namechange_first_year,
                asof=asof,
                now=now,
                rate_limiter=rate_limiter,
            )
        )
    blocking = any(
        r.status == _STATUS_FAILED and r.endpoint in STATEMENT_ENDPOINTS
        for r in results
    )
    cov_results, coverage = _build_statement_coverage(
        store, coverage_store, periods=periods, asof=asof, blocking=blocking
    )
    results.extend(cov_results)
    return Round2IngestReport(results=tuple(results), fina_coverage=coverage)


# --- round-4 report_rc ingest (R4-2) -----------------------------------------


async def ingest_report_rc(
    client: _Round2Client,
    store: SnapshotStore,
    *,
    first_year: int,
    last_date: str,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
) -> list[EndpointResult]:
    """One report_rc snapshot per calendar month (paginated date-range query).

    report_rc paginates INSIDE the client (one rate-limit token per page), so the
    throttle is handed to the client and ``_ingest_one`` is told NOT to throttle —
    mirrors :func:`ingest_statement`. Every enumerated month (from the verified-dense
    2014 floor — see :data:`REPORT_RC_FIRST_YEAR`) has reports, so ``require_non_empty``
    is a valid fail-closed corruption/truncation check; a fetch EXCEPTION is recorded
    FAILED and retried on resume. Snapshots key on the month-range end date.
    """
    throttle = _make_throttle(rate_limiter)
    out: list[EndpointResult] = []
    for start, end, key in report_rc_month_ranges(first_year, last_date):
        out.append(
            await _ingest_one(
                store,
                endpoint=EP_REPORT_RC,
                trade_date=key,
                params={"start_date": start, "end_date": end},
                fetch=partial(
                    client.report_rc, start_date=start, end_date=end, throttle=throttle
                ),
                now=now,
                require_non_empty=True,
                rate_limiter=None,
            )
        )
    return out


async def ingest_round4(
    client: _Round2Client,
    store: SnapshotStore,
    *,
    calendar: Sequence[str],
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
    report_rc_first_year: int = REPORT_RC_FIRST_YEAR,
) -> Round2IngestReport:
    """R4-2: ingest report_rc (broker analyst forecasts/ratings) per calendar month.

    report_rc is a sparse STREAM with no fixed survivorship universe → unlike the
    statements there is NO coverage manifest; integrity = in-client pagination to a
    short page (no silent truncation) + byte+checksum + idempotent resume. Offline
    batch; the real multi-hundred-call run is owner-gated.
    """
    last_date = calendar[-1] if calendar else ""
    if not last_date:
        raise ValueError("empty calendar — cannot enumerate report_rc months")
    results = await ingest_report_rc(
        client,
        store,
        first_year=report_rc_first_year,
        last_date=last_date,
        now=now,
        rate_limiter=rate_limiter,
    )
    return Round2IngestReport(results=tuple(results), fina_coverage=())


# --- QGR-1 short-horizon + 主旋律 ingest (quant first-gate re-research) -------
#
# Endpoint tags EQUAL the client method names so ``getattr(client, endpoint)``
# dispatches. Three categories by query shape (probed read-only 2026-06-21):
#   * FULL-MARKET daily  (stk_limit/cyq_perf/stk_factor_pro): one paginated pull
#     per trade_date covering the whole universe → silently capped at 5000
#     rows/call → paginate; require_non_empty; per-day survivorship coverage.
#   * SPARSE daily       (limit_list_d/suspend_d): only the names with an event
#     that day → cap-immune single call; an EMPTY day is legitimate (no limit
#     stocks / no suspend-resume) → stored replayable with pinned columns; NO
#     survivorship coverage.
#   * PERIOD events      (forecast_vip/express_vip): by report period, paginated;
#     NOT full-universe (only issuers) → NO survivorship coverage (like report_rc).
#   * THEME catalogs     (ths_index/index_classify): one small as-of pull each.
EP_STK_LIMIT = "stk_limit"
EP_LIMIT_LIST_D = "limit_list_d"
EP_SUSPEND_D = "suspend_d"
EP_CYQ_PERF = "cyq_perf"
EP_STK_FACTOR_PRO = "stk_factor_pro"
EP_FORECAST = "forecast_vip"
EP_EXPRESS = "express_vip"
EP_THS_INDEX = "ths_index"
EP_INDEX_CLASSIFY = "index_classify"

# Full-market-by-trade_date endpoints → per-day survivorship coverage + pagination.
QGR_FULLMARKET_DAILY: tuple[str, ...] = (EP_STK_LIMIT, EP_CYQ_PERF, EP_STK_FACTOR_PRO)
# Sparse daily endpoints → single call, empty day legitimate, no coverage.
QGR_SPARSE_DAILY: tuple[str, ...] = (EP_LIMIT_LIST_D, EP_SUSPEND_D)
# Event-stream endpoints → ingested by ann_date month-range (NOT target period),
# paginated, no survivorship coverage. ann_date keying captures a forecast already
# announced for a FUTURE target period (annual forecasts file months ahead) that a
# target-period enumeration stopping at the calendar end would silently drop.
QGR_EVENT_STREAM: tuple[str, ...] = (EP_FORECAST, EP_EXPRESS)

# Per-endpoint first available trade date — days before are skipped (a permanent
# vendor data-availability limit, NOT a transient failure to retry; mirrors the
# index_weight 2016 floor). Probed 2026-06-21: cyq_perf empty before 2018-01,
# limit_list_d empty before 2020-01; stk_limit/suspend_d/stk_factor_pro cover the
# full 2015+ calendar so they have no floor here.
CYQ_PERF_FIRST_DATE = "20180101"
LIMIT_LIST_D_FIRST_DATE = "20200101"
QGR_DAILY_FIRST_DATE: dict[str, str] = {
    EP_CYQ_PERF: CYQ_PERF_FIRST_DATE,
    EP_LIMIT_LIST_D: LIMIT_LIST_D_FIRST_DATE,
}

# Canonical columns for the sparse daily endpoints (probed 2026-06-21) — an empty
# day must serialize WITH this header (not a bare ``\n`` that ``parse_csv_bytes``
# rejects), so a PIT reader replays it as a 0-row, known-column frame (the
# namechange empty-year lesson).
SUSPEND_D_FIELDS: tuple[str, ...] = (
    "ts_code",
    "trade_date",
    "suspend_timing",
    "suspend_type",
)
LIMIT_LIST_D_FIELDS: tuple[str, ...] = (
    "trade_date",
    "ts_code",
    "industry",
    "name",
    "close",
    "pct_chg",
    "amount",
    "limit_amount",
    "float_mv",
    "total_mv",
    "turnover_ratio",
    "fd_amount",
    "first_time",
    "last_time",
    "open_times",
    "up_stat",
    "limit_times",
    "limit",
)
_SPARSE_DAILY_FIELDS: dict[str, tuple[str, ...]] = {
    EP_SUSPEND_D: SUSPEND_D_FIELDS,
    EP_LIMIT_LIST_D: LIMIT_LIST_D_FIELDS,
}

# Event-stream require-non-empty per endpoint (probed 2026-06-21):
#   * forecast_vip: every calendar month has forecasts (min 11/month) → an empty
#     month IS truncation/corruption → require non-empty (fail-closed check).
#   * express_vip: EVENT-clustered, many quiet months legitimately ZERO → must NOT
#     require non-empty; an empty month is stored as a replayable empty frame with
#     the canonical header (the namechange empty-year lesson).
EVENT_STREAM_REQUIRE_NON_EMPTY: dict[str, bool] = {
    EP_FORECAST: True,
    EP_EXPRESS: False,
}
# Canonical columns for an empty express_vip month (forecast never empty → no need).
EXPRESS_VIP_FIELDS: tuple[str, ...] = (
    "ts_code",
    "ann_date",
    "end_date",
    "revenue",
    "operate_profit",
    "total_profit",
    "n_income",
    "total_assets",
    "total_hldr_eqy_exc_min_int",
    "diluted_eps",
    "diluted_roe",
    "yoy_net_profit",
    "bps",
    "open_net_assets",
    "open_bps",
    "perf_summary",
    "update_flag",
)
_EVENT_EMPTY_FIELDS: dict[str, tuple[str, ...]] = {EP_EXPRESS: EXPRESS_VIP_FIELDS}

# SW industry taxonomy for the index_classify catalog pull.
SW_CLASSIFY_SRC = "SW2021"


async def ingest_fullmarket_daily(
    client: _Round2Client,
    store: SnapshotStore,
    calendar: Sequence[str],
    *,
    endpoint: str,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
    first_date: str = "",
) -> list[EndpointResult]:
    """One paginated full-market snapshot per trade date (stk_limit/cyq_perf/…).

    Pagination happens INSIDE the client (one rate-limit token per page), so the
    throttle is handed to the client and ``_ingest_one`` is told NOT to throttle —
    mirrors :func:`ingest_statement`. ``require_non_empty=True``: a trading day
    at/after the endpoint's availability floor must return full-market rows, so an
    empty pull is truncation/corruption (recorded FAILED → retried on resume).
    Days before ``first_date`` are skipped (permanent vendor limit).
    """
    fetch_method = getattr(client, endpoint)
    throttle = _make_throttle(rate_limiter)
    out: list[EndpointResult] = []
    for d in calendar:
        if first_date and d < first_date:
            continue
        out.append(
            await _ingest_one(
                store,
                endpoint=endpoint,
                trade_date=d,
                params={"trade_date": d},
                fetch=partial(fetch_method, d, throttle=throttle),
                now=now,
                require_non_empty=True,
                rate_limiter=None,
            )
        )
    return out


async def ingest_sparse_daily(
    client: _Round2Client,
    store: SnapshotStore,
    calendar: Sequence[str],
    *,
    endpoint: str,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
    first_date: str = "",
) -> list[EndpointResult]:
    """One sparse single-call snapshot per trade date (limit_list_d/suspend_d).

    ``require_non_empty=False``: an empty day is legitimate (no limit stocks / no
    suspend-resume events) and is stored as a replayable empty frame normalised to
    the canonical :data:`_SPARSE_DAILY_FIELDS` header; only a fetch EXCEPTION is
    recorded FAILED for retry. Cap-immune → single call (no per-page throttle).
    Days before ``first_date`` are skipped (permanent vendor limit).
    """
    fetch_method = getattr(client, endpoint)
    columns = list(_SPARSE_DAILY_FIELDS[endpoint])

    async def _fetch(trade_date: str) -> pd.DataFrame:
        frame: pd.DataFrame = await fetch_method(trade_date)
        if frame is None or frame.empty:
            return pd.DataFrame(columns=columns)
        return frame

    out: list[EndpointResult] = []
    for d in calendar:
        if first_date and d < first_date:
            continue
        out.append(
            await _ingest_one(
                store,
                endpoint=endpoint,
                trade_date=d,
                params={"trade_date": d},
                fetch=partial(_fetch, d),
                now=now,
                require_non_empty=False,
                rate_limiter=rate_limiter,
            )
        )
    return out


async def ingest_event_stream(
    client: _Round2Client,
    store: SnapshotStore,
    ranges: Sequence[tuple[str, str, str]],
    *,
    endpoint: str,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
    require_non_empty: bool,
    empty_columns: tuple[str, ...] | None = None,
) -> list[EndpointResult]:
    """One paginated event snapshot per ann_date month-range (forecast/express).

    Keyed by ``ann_date`` window (``ranges`` = ``(start, end, key)`` triples from
    :func:`report_rc_month_ranges`), NOT by target report period — so a forecast
    already announced for a future target period is captured (codex P2-1).
    Pagination is inside the client (throttle per page). ``require_non_empty`` is
    per-endpoint (forecast: True = corruption check; express: False = quiet months
    are legitimately empty). When not required and a month is empty, it is stored as
    a replayable empty frame normalised to ``empty_columns`` (the namechange lesson).
    NOT full-universe → no survivorship coverage (integrity = pagination + checksum).
    """
    fetch_method = getattr(client, endpoint)
    throttle = _make_throttle(rate_limiter)

    async def _fetch(start: str, end: str) -> pd.DataFrame:
        frame: pd.DataFrame = await fetch_method(
            start_date=start, end_date=end, throttle=throttle
        )
        if (frame is None or frame.empty) and empty_columns is not None:
            return pd.DataFrame(columns=list(empty_columns))
        return frame

    out: list[EndpointResult] = []
    for start, end, key in ranges:
        out.append(
            await _ingest_one(
                store,
                endpoint=endpoint,
                trade_date=key,
                params={"start_date": start, "end_date": end},
                fetch=partial(_fetch, start, end),
                now=now,
                require_non_empty=require_non_empty,
                rate_limiter=None,
            )
        )
    return out


async def ingest_theme_catalogs(
    client: _Round2Client,
    store: SnapshotStore,
    asof: str,
    *,
    now: Callable[[], datetime],
    rate_limiter: RateLimiter | None = None,
) -> list[EndpointResult]:
    """As-of THS concept catalog + SW industry classification catalog (主旋律).

    ``ths_index`` = the 同花顺 concept/industry index registry (PIT-stable via its
    ``list_date``); ``index_classify`` = the 申万 industry code↔name tree. Both
    small single pulls keyed by ``asof``. The PIT SW *membership* (in/out dates) is
    ``index_member_all`` (ingested by round-2); THS concept *membership*
    (``ths_member``) carries no in/out dates → non-PIT, deferred to QGR-3.
    """
    out: list[EndpointResult] = []
    out.append(
        await _ingest_one(
            store,
            endpoint=EP_THS_INDEX,
            trade_date=asof,
            params={"asof": asof},
            fetch=client.ths_index,
            now=now,
            require_non_empty=True,
            rate_limiter=rate_limiter,
        )
    )
    out.append(
        await _ingest_one(
            store,
            endpoint=EP_INDEX_CLASSIFY,
            trade_date=asof,
            params={"asof": asof, "src": SW_CLASSIFY_SRC},
            fetch=partial(client.index_classify, src=SW_CLASSIFY_SRC),
            now=now,
            require_non_empty=True,
            rate_limiter=rate_limiter,
        )
    )
    return out


def build_daily_coverage_manifests(
    store: SnapshotStore,
    calendar: Sequence[str],
    universe: SurvivorshipUniverse,
    *,
    endpoint: str,
    first_date: str = "",
    skip_keys: frozenset[tuple[str, str]] | None = None,
) -> list[CoverageManifest]:
    """Per-DAY survivorship-keyed coverage for a full-market daily endpoint.

    Mirrors the ``daily``/``daily`` convention in ``historical_ingest.job``:
    ``requested`` = codes tradable as-of the day (``universe.tradable_asof(d)``);
    ``delivered`` = the day's stored snapshot ``ts_code`` set. A missing snapshot
    raises :class:`FileNotFoundError` (fail-closed — the cap-truncation backstop).
    Days before ``first_date`` are skipped (no data ingested for them). NB
    ``stk_limit`` delivers a superset (funds included) of the stock universe →
    completeness ~1.0; the point is to catch a silently-truncated (sub-5000) day.

    ``skip_keys`` = ``(endpoint, day)`` pairs whose coverage is already stored;
    those days are skipped BEFORE the snapshot is read, so an idempotent resume
    never re-parses the (very wide ``stk_factor_pro``) CSVs just to drop the write
    (codex P2-2).
    """
    skip = skip_keys or frozenset()
    manifests: list[CoverageManifest] = []
    for d in calendar:
        if first_date and d < first_date:
            continue
        if (endpoint, d) in skip:
            continue
        snapshot = store.latest(vendor=VENDOR, endpoint=endpoint, trade_date=d)
        if snapshot is None:
            raise FileNotFoundError(
                f"{endpoint} day {d} snapshot missing — "
                "cannot build coverage (fail-closed)"
            )
        frame = parse_csv_bytes(snapshot.raw_payload)
        delivered = {str(c).strip() for c in frame.get("ts_code", pd.Series(dtype=str))}
        requested = sorted(universe.tradable_asof(d))
        manifests.append(
            CoverageManifest(
                granularity="daily",
                endpoint=endpoint,
                params={"trade_date": d},
                session_start=d,
                session_end=d,
                requested_universe=tuple(requested),
                delivered_universe=tuple(sorted(delivered)),
            )
        )
    return manifests


def _existing_coverage_keys(coverage_store: CoverageStore) -> set[tuple[str, str]]:
    """One-pass read of the (endpoint, session_end) keys already stored.

    The per-day coverage build writes thousands of manifests; calling
    :func:`_put_coverage_idempotent` (a full-file scan per manifest) for each
    would be O(n²) over a large coverage file. Pre-loading the present keys once
    makes the resume-skip O(1) per manifest (the QGR daily manifests are
    deterministic, so a key-presence check is sufficient — no content re-compare).
    """
    return set(coverage_store.iter_keys())


def _build_qgr_coverage(
    store: SnapshotStore,
    coverage_store: CoverageStore,
    *,
    calendar: Sequence[str],
    asof: str,
    blocking: bool,
) -> tuple[list[EndpointResult], tuple[CoverageManifest, ...]]:
    """Build + persist per-day coverage for the full-market daily endpoints.

    Fail-closed, mirroring :func:`_build_coverage`: skipped on blocking full-market
    ingest failures; a missing snapshot or unbuildable survivorship universe (the
    round-2 stock_basic L/D rosters must exist at ``asof``) is a FAILED
    ``EndpointResult`` (never a silent pass). Resume-safe via a one-pass present-key
    preload (no duplicate rows, no O(n²) rescan). Returns ``(extra_results, cov)``.
    """
    if not calendar:
        return [], ()
    if blocking:
        log.warning("qgr_coverage_skipped", reason="blocking ingest failures")
        return [], ()
    # Pre-load already-stored coverage keys ONCE so a resume skips manifest
    # construction (the snapshot re-parse) for present days, not just the write.
    present = frozenset(_existing_coverage_keys(coverage_store))
    try:
        universe = load_survivorship(store, asof)
        manifests: list[CoverageManifest] = []
        for endpoint in QGR_FULLMARKET_DAILY:
            manifests.extend(
                build_daily_coverage_manifests(
                    store,
                    calendar,
                    universe,
                    endpoint=endpoint,
                    first_date=QGR_DAILY_FIRST_DATE.get(endpoint, ""),
                    skip_keys=present,
                )
            )
    except (FileNotFoundError, ValueError) as exc:
        log.error("qgr_coverage_failed", error=str(exc))
        return [
            EndpointResult(
                endpoint="coverage",
                key=asof,
                status=_STATUS_FAILED,
                rows=0,
                error=str(exc),
            )
        ], ()
    # Every manifest returned is for a not-yet-covered day (present keys were
    # skipped during construction) → write them all.
    for manifest in manifests:
        coverage_store.put(manifest)
    return [], tuple(manifests)


async def ingest_qgr(
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
    """QGR-1: short-horizon microstructure/chips/tech + earnings events + 主旋律.

    Full-market daily (stk_limit/cyq_perf/stk_factor_pro, paginated, coverage) +
    sparse daily (limit_list_d/suspend_d) over the calendar; earnings events
    (forecast_vip/express_vip) by ann_date month-range; theme catalogs
    (ths_index/index_classify) as-of. Per-day survivorship coverage is built for
    the 3 full-market daily endpoints ONLY (the others are sparse / not
    full-universe). Idempotent / resumable / rate-limited / byte-exact. OFFLINE
    batch; the real multi-thousand-call run is owner-gated. report_rc is ingested
    separately (``--phase round4``) and not repeated here.
    """
    results: list[EndpointResult] = []
    for endpoint in QGR_FULLMARKET_DAILY:
        results.extend(
            await ingest_fullmarket_daily(
                client,
                store,
                calendar,
                endpoint=endpoint,
                now=now,
                rate_limiter=rate_limiter,
                first_date=QGR_DAILY_FIRST_DATE.get(endpoint, ""),
            )
        )
    for endpoint in QGR_SPARSE_DAILY:
        results.extend(
            await ingest_sparse_daily(
                client,
                store,
                calendar,
                endpoint=endpoint,
                now=now,
                rate_limiter=rate_limiter,
                first_date=QGR_DAILY_FIRST_DATE.get(endpoint, ""),
            )
        )
    last_date = calendar[-1] if calendar else asof
    # Event streams keyed by ann_date month (NOT target period) so a forecast
    # already announced for a future target period is captured (codex P2-1).
    event_ranges = report_rc_month_ranges(first_year, last_date)
    for endpoint in QGR_EVENT_STREAM:
        results.extend(
            await ingest_event_stream(
                client,
                store,
                event_ranges,
                endpoint=endpoint,
                now=now,
                rate_limiter=rate_limiter,
                require_non_empty=EVENT_STREAM_REQUIRE_NON_EMPTY[endpoint],
                empty_columns=_EVENT_EMPTY_FIELDS.get(endpoint),
            )
        )
    results.extend(
        await ingest_theme_catalogs(
            client, store, asof, now=now, rate_limiter=rate_limiter
        )
    )
    blocking = any(
        r.status == _STATUS_FAILED and r.endpoint in QGR_FULLMARKET_DAILY
        for r in results
    )
    cov_results, coverage = _build_qgr_coverage(
        store, coverage_store, calendar=calendar, asof=asof, blocking=blocking
    )
    results.extend(cov_results)
    return Round2IngestReport(results=tuple(results), fina_coverage=coverage)


def _print_report(label: str, report: Round2IngestReport) -> None:
    """Print one phase's ingest tally + coverage summary + failures."""
    print(
        f"{label} ingest: ingested={report.ingested} skipped={report.skipped} "
        f"failed={report.failed}"
    )
    if report.fina_coverage:
        worst = min(report.fina_coverage, key=lambda m: m.completeness)
        n_incomplete = sum(1 for m in report.fina_coverage if not m.is_complete)
        print(
            f"{label} coverage: {len(report.fina_coverage)} period manifests, "
            f"{n_incomplete} incomplete; worst {worst.endpoint} {worst.session_end} "
            f"completeness={worst.completeness:.4f} "
            f"missing={len(worst.missing_symbols)}"
        )
    for fail in report.failures:
        print(f"  FAILED {fail.endpoint} {fail.key}: {fail.error}")


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
        "--phase",
        choices=("round2", "round3", "round3-restate", "round4", "qgr", "all"),
        default="round2",
        help="round2 = R2-1 (weights/fina/member/rosters); round3 = R3-1 "
        "(income/cashflow/balancesheet + namechange); round3-restate = re-pull "
        "the 3 statements paginated + version-bump truncated periods + rebuild "
        "coverage (no namechange); round4 = R4-2 (report_rc analyst forecasts, "
        "per-calendar-month paginated); qgr = QGR-1 (stk_limit/cyq_perf/"
        "stk_factor_pro full-market daily + limit_list_d/suspend_d sparse daily + "
        "forecast_vip/express_vip events + ths_index/index_classify theme "
        "catalogs); all = round2 + round3 (idempotent)",
    )
    parser.add_argument(
        "--namechange-first-year", type=int, default=NAMECHANGE_FIRST_YEAR
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
    today = datetime.now(UTC).strftime("%Y%m%d")
    last_date = calendar[-1] if calendar else today
    do_r2 = args.phase in ("round2", "all")
    do_r3 = args.phase in ("round3", "all")
    do_r3_restate = args.phase == "round3-restate"
    do_r4 = args.phase == "round4"
    do_qgr = args.phase == "qgr"
    # QGR keys its theme catalogs + per-day survivorship coverage on ``asof``;
    # default to the calendar's last trade date so coverage finds the round-2
    # stock_basic L/D rosters ingested at that date — wall-clock today would miss
    # them (the row-cap memo's asof-alignment gotcha).
    asof = args.asof or (last_date if do_qgr else today)

    if args.dry_run:
        periods = report_periods(args.first_year, last_date)
        pd_span = f"{periods[0]}..{periods[-1]}" if periods else "-"
        print(
            f"[dry-run] phase={args.phase} calendar {len(calendar)} td "
            f"({calendar[0] if calendar else '-'}..{last_date})"
        )
        if do_r2:
            month_ends = [
                d
                for d in month_end_trade_dates(calendar)
                if d[:6] >= CSI300_WEIGHT_FIRST_MONTH
            ]
            me_span = f"{month_ends[0]}..{month_ends[-1]}" if month_ends else "-"
            print(
                f"[dry-run] index_weight month snapshots: {len(month_ends)} ({me_span})"
            )
            print(f"[dry-run] fina_indicator periods: {len(periods)} ({pd_span})")
            print(f"[dry-run] index_member_all + stock_basic L/D as-of {asof}")
        if do_r3 or do_r3_restate:
            n_stmt = len(STATEMENT_ENDPOINTS) * len(periods)
            verb = "re-pull (paginated, version-bump)" if do_r3_restate else "pull"
            print(
                f"[dry-run] statements ({', '.join(STATEMENT_ENDPOINTS)}): "
                f"{verb} {n_stmt} period snapshots ({pd_span})"
            )
        if do_r3:
            pages = namechange_pages(args.namechange_first_year, asof)
            keys = [k for _, _, k in pages]
            yr_span = f"{keys[0]}..{keys[-1]}" if keys else "-"
            print(
                f"[dry-run] namechange year snapshots: {len(pages)} ({yr_span}; "
                "current year keyed by asof)"
            )
        if do_r4:
            ranges = report_rc_month_ranges(REPORT_RC_FIRST_YEAR, last_date)
            rr_span = f"{ranges[0][2]}..{ranges[-1][2]}" if ranges else "-"
            print(
                f"[dry-run] report_rc month snapshots: {len(ranges)} ({rr_span}; "
                "range-paginated, weekends incl)"
            )
        if do_qgr:
            est_calls = 0
            print(
                "[dry-run] QGR-1 full-market daily (paginated ~1-2 calls/day, "
                "+per-day survivorship coverage):"
            )
            for ep in QGR_FULLMARKET_DAILY:
                floor = QGR_DAILY_FIRST_DATE.get(ep, "")
                days = [d for d in calendar if not floor or d >= floor]
                span = f"{days[0]}..{days[-1]}" if days else "-"
                floor_note = f" (floor {floor})" if floor else ""
                print(
                    f"             {ep}: {len(days)} day snapshots ({span}){floor_note}"
                )
                est_calls += len(days) * 2  # upper bound: 2 pages/day
            print("[dry-run] QGR-1 sparse daily (single-call, empty day ok):")
            for ep in QGR_SPARSE_DAILY:
                floor = QGR_DAILY_FIRST_DATE.get(ep, "")
                days = [d for d in calendar if not floor or d >= floor]
                span = f"{days[0]}..{days[-1]}" if days else "-"
                floor_note = f" (floor {floor})" if floor else ""
                print(
                    f"             {ep}: {len(days)} day snapshots ({span}){floor_note}"
                )
                est_calls += len(days)
            event_ranges = report_rc_month_ranges(args.first_year, last_date)
            ev_span = (
                f"{event_ranges[0][2]}..{event_ranges[-1][2]}" if event_ranges else "-"
            )
            n_evt = len(QGR_EVENT_STREAM) * len(event_ranges)
            print(
                f"[dry-run] QGR-1 earnings events ({', '.join(QGR_EVENT_STREAM)}): "
                f"{n_evt} ann_date-month snapshots ({ev_span}; paginated)"
            )
            est_calls += n_evt * 2  # upper bound: 2 pages/month
            print(
                f"[dry-run] QGR-1 theme catalogs (ths_index, index_classify) "
                f"as-of {asof}"
            )
            est_calls += 2
            print(
                f"[dry-run] QGR-1 coverage: per-day survivorship manifests for "
                f"{', '.join(QGR_FULLMARKET_DAILY)} (needs round-2 stock_basic "
                f"rosters at asof {asof})"
            )
            print(f"[dry-run] QGR-1 estimated SDK calls (upper bound): ~{est_calls}")
        print("[dry-run] no network calls made.")
        return

    client = TushareClient()  # real, owner-gated heavy run
    store = SnapshotStore(args.snapshot_root)
    # Match the AE-001 coverage layout: snapshot_root/coverage/coverage.jsonl.
    coverage_store = CoverageStore(Path(args.snapshot_root) / "coverage")
    rate_limiter = RateLimiter(args.max_per_minute)
    failed = 0
    if do_r2:
        report = asyncio.run(
            ingest_round2(
                client,
                store,
                coverage_store,
                calendar=calendar,
                first_year=args.first_year,
                asof=asof,
                now=lambda: datetime.now(UTC),
                rate_limiter=rate_limiter,
            )
        )
        _print_report("round2", report)
        failed += report.failed
    if do_r3:
        report = asyncio.run(
            ingest_round3(
                client,
                store,
                coverage_store,
                calendar=calendar,
                first_year=args.first_year,
                asof=asof,
                now=lambda: datetime.now(UTC),
                rate_limiter=rate_limiter,
                namechange_first_year=args.namechange_first_year,
            )
        )
        _print_report("round3", report)
        failed += report.failed
    if do_r3_restate:
        report = asyncio.run(
            ingest_round3(
                client,
                store,
                coverage_store,
                calendar=calendar,
                first_year=args.first_year,
                asof=asof,
                now=lambda: datetime.now(UTC),
                rate_limiter=rate_limiter,
                namechange_first_year=args.namechange_first_year,
                restate_statements=True,
            )
        )
        _print_report("round3-restate", report)
        failed += report.failed
    if do_r4:
        report = asyncio.run(
            ingest_round4(
                client,
                store,
                calendar=calendar,
                now=lambda: datetime.now(UTC),
                rate_limiter=rate_limiter,
            )
        )
        _print_report("round4", report)
        failed += report.failed
    if do_qgr:
        report = asyncio.run(
            ingest_qgr(
                client,
                store,
                coverage_store,
                calendar=calendar,
                first_year=args.first_year,
                asof=asof,
                now=lambda: datetime.now(UTC),
                rate_limiter=rate_limiter,
            )
        )
        _print_report("qgr", report)
        failed += report.failed
    # Non-zero exit on any failure (fail-closed) so a resume re-run is obvious.
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()


__all__ = [
    "CSI300_CODE",
    "CYQ_PERF_FIRST_DATE",
    "EP_BALANCESHEET",
    "EP_CASHFLOW",
    "EP_CYQ_PERF",
    "EP_EXPRESS",
    "EP_FINA",
    "EP_FORECAST",
    "EP_INCOME",
    "EP_INDEX_CLASSIFY",
    "EP_INDEX_MEMBER",
    "EP_INDEX_WEIGHT",
    "EP_LIMIT_LIST_D",
    "EP_NAMECHANGE",
    "EP_REPORT_RC",
    "EP_STK_FACTOR_PRO",
    "EP_STK_LIMIT",
    "EP_STOCK_BASIC_D",
    "EP_STOCK_BASIC_L",
    "EP_SUSPEND_D",
    "EP_THS_INDEX",
    "EVENT_STREAM_REQUIRE_NON_EMPTY",
    "EXPRESS_VIP_FIELDS",
    "LIMIT_LIST_D_FIELDS",
    "LIMIT_LIST_D_FIRST_DATE",
    "NAMECHANGE_FIELDS",
    "NAMECHANGE_FIRST_YEAR",
    "QGR_DAILY_FIRST_DATE",
    "QGR_EVENT_STREAM",
    "QGR_FULLMARKET_DAILY",
    "QGR_SPARSE_DAILY",
    "REPORT_RC_FIRST_YEAR",
    "STATEMENT_ENDPOINTS",
    "SUSPEND_D_FIELDS",
    "EndpointResult",
    "Round2IngestReport",
    "build_daily_coverage_manifests",
    "build_fina_coverage_manifests",
    "build_statement_coverage_manifests",
    "ingest_event_stream",
    "ingest_fina_indicator",
    "ingest_fullmarket_daily",
    "ingest_index_member_all",
    "ingest_index_weight",
    "ingest_namechange",
    "ingest_report_rc",
    "ingest_round2",
    "ingest_round3",
    "ingest_round4",
    "ingest_qgr",
    "ingest_sparse_daily",
    "ingest_statement",
    "ingest_stock_basic",
    "ingest_theme_catalogs",
    "load_survivorship",
    "month_end_trade_dates",
    "namechange_pages",
    "namechange_years",
    "report_periods",
    "report_rc_month_ranges",
]
