"""HistoricalIngestJob — offline bulk PIT ingestion orchestrator (AE-001).

Pulls the Tushare full-market endpoints once per real trading day and
persists each pull **byte-exact** into the K-002 ``SnapshotStore`` (raw bytes +
checksum — R0 §3). The job is:

* **idempotent / resumable** — before fetching it checks whether the snapshot
  for ``(vendor, endpoint, trade_date)`` already exists and skips it, so a
  re-run after an interruption only fetches the gap (断点续传);
* **rate-limited** — an injectable :class:`RateLimiter` throttles to Tushare's
  per-minute ceiling;
* **fail-closed for data** — a fetch failure (or an empty ``daily`` frame on a
  trading day) records a *failed* result and writes **nothing**; it never
  stores a partial/empty payload that would poison the PIT history. The re-run
  retries it.

It is **offline batch**: never wired into the 13 runtime crons, never on the
realtime path (asserted by ``test_module_contract``). The real multi-thousand
-call run is owner-gated; ``scripts/ingest_historical_pit.py`` is the entry
point with a small dry-run mode. It is designed for a **single sequential
runner** — the skip/backfill and coverage de-dupe guards are serial, not
atomic across concurrent processes; do not run two ingests against the same
snapshot root at once (a later sequential re-run repairs any racing gap).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import uuid4

import pandas as pd
import structlog

from backend.data.historical_ingest.calendar_provider import TradeCalendarProvider
from backend.data.historical_ingest.rate_limiter import RateLimiter
from backend.data.historical_ingest.serialization import (
    canonical_csv_bytes,
    parse_csv_bytes,
)
from backend.data.historical_ingest.universe import SurvivorshipUniverse
from backend.marketdata_snapshot.coverage import CoverageManifest, CoverageStore
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import (
    SnapshotOverwriteError,
    SnapshotStore,
)

log = structlog.get_logger(component="data.historical_ingest.job")

VENDOR = "tushare"
"""Snapshot vendor tag for every payload this job persists."""

DEFAULT_ENDPOINTS = ("daily", "adj_factor", "daily_basic", "fund_daily")
"""Full-market, one-call-per-trade-date endpoints persisted byte-exact."""

# Endpoints whose empty frame on a real trading day is a vendor error, not a
# legitimate "nothing to report" — never stored (fail-closed). All four
# full-market endpoints return rows on every trading day (every listed stock
# has a daily / adj_factor / daily_basic row; ETFs have a fund_daily row), and
# adj_factor in particular is a *required* PIT pin for the as-of adjusted-close
# reconstruction — storing an empty success would leave a permanent gap that a
# resumable re-run would then skip (codex AE-001 P2).
_REQUIRE_NON_EMPTY = frozenset(DEFAULT_ENDPOINTS)

_STATUS_INGESTED = "ingested"
_STATUS_SKIPPED = "skipped"
_STATUS_FAILED = "failed"


@runtime_checkable
class KlineRowWriter(Protocol):
    """Optional secondary writer for the structured ``kline_daily`` rows.

    The authoritative PIT layer is the byte-exact ``SnapshotStore``; this
    derived row store (amendment §2.2) is a query convenience. Satisfied by
    :class:`backend.data.database.MongoDBService` (``save_daily_frame``).
    """

    async def save_daily_frame(self, trade_date: str, df: pd.DataFrame) -> int: ...


@runtime_checkable
class _FullMarketClient(Protocol):
    """Duck type for the Tushare per-trade-date full-market endpoints."""

    async def daily(self, trade_date: str) -> pd.DataFrame: ...
    async def adj_factor(self, trade_date: str) -> pd.DataFrame: ...
    async def daily_basic(self, trade_date: str) -> pd.DataFrame: ...
    async def fund_daily(self, trade_date: str) -> pd.DataFrame: ...


@dataclass(frozen=True)
class EndpointResult:
    """Outcome of one (endpoint, trade_date) pull (immutable)."""

    endpoint: str
    trade_date: str
    status: str
    rows: int
    sha256: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class IngestReport:
    """Immutable summary of an ingest run."""

    results: tuple[EndpointResult, ...]

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


class HistoricalIngestJob:
    """Offline bulk historical PIT ingestion orchestrator.

    Args:
        client: A :class:`TushareClient` (or any ``_FullMarketClient``).
        snapshot_store: The K-002 byte-exact PIT store.
        calendar: Trade-day enumeration provider.
        rate_limiter: Throttle; defaults to disabled (tests / explicit days).
        kline_writer: Optional secondary structured-row writer (``daily``).
        coverage_store: Optional coverage manifest store (paired with
            ``universe`` to record requested-vs-delivered for ``daily``).
        universe: Optional survivorship universe — when given with
            ``coverage_store``, each ``daily`` pull's delivered set is checked
            against ``tradable_asof(trade_date)``.
        now_utc: Injected UTC clock for ``fetch_time_utc`` (tests).
        endpoints: Endpoints to ingest (default :data:`DEFAULT_ENDPOINTS`).
    """

    def __init__(
        self,
        *,
        client: _FullMarketClient,
        snapshot_store: SnapshotStore,
        calendar: TradeCalendarProvider,
        rate_limiter: RateLimiter | None = None,
        kline_writer: KlineRowWriter | None = None,
        coverage_store: CoverageStore | None = None,
        universe: SurvivorshipUniverse | None = None,
        now_utc: Callable[[], datetime] | None = None,
        endpoints: Sequence[str] = DEFAULT_ENDPOINTS,
    ) -> None:
        self._client = client
        self._store = snapshot_store
        self._calendar = calendar
        self._rate = rate_limiter or RateLimiter(0)
        self._kline_writer = kline_writer
        self._coverage_store = coverage_store
        self._universe = universe
        self._now_utc = now_utc or (lambda: datetime.now(UTC))
        self._endpoints = tuple(endpoints)

    # -- public --------------------------------------------------------

    async def ingest_range(
        self, start_date: str, end_date: str, *, run_id: str | None = None
    ) -> IngestReport:
        """Ingest every endpoint for every trading day in ``[start, end]``."""
        rid = run_id or uuid4().hex
        days = await self._calendar.trading_days(start_date, end_date)
        log.info(
            "historical_ingest_start",
            run_id=rid,
            start_date=start_date,
            end_date=end_date,
            trading_days=len(days),
            endpoints=list(self._endpoints),
        )
        results: list[EndpointResult] = []
        for trade_date in days:
            results.extend(await self.ingest_trade_date(trade_date, run_id=rid))
        report = IngestReport(results=tuple(results))
        log.info(
            "historical_ingest_done",
            run_id=rid,
            ingested=report.ingested,
            skipped=report.skipped,
            failed=report.failed,
        )
        return report

    async def ingest_trade_date(
        self, trade_date: str, *, run_id: str | None = None
    ) -> list[EndpointResult]:
        """Ingest all configured endpoints for a single trade date."""
        rid = run_id or uuid4().hex
        out: list[EndpointResult] = []
        for endpoint in self._endpoints:
            out.append(await self._ingest_one(endpoint, trade_date, rid))
        return out

    # -- internal ------------------------------------------------------

    async def _ingest_one(
        self, endpoint: str, trade_date: str, run_id: str
    ) -> EndpointResult:
        existing = self._store.latest(
            vendor=VENDOR, endpoint=endpoint, trade_date=trade_date
        )
        if existing is not None:
            rows = int(existing.metadata.get("rows", 0))
            # Resume promise: a snapshot-only first run (or one whose
            # best-effort secondary write failed) must still backfill the
            # derived kline / coverage artifacts from the *verified* stored
            # bytes — otherwise enabling --with-kline/--with-coverage on a
            # re-run would skip the day forever (codex AE-001 P2).
            if endpoint == "daily":
                await self._backfill_secondary(trade_date, existing)
            return EndpointResult(
                endpoint=endpoint,
                trade_date=trade_date,
                status=_STATUS_SKIPPED,
                rows=rows,
                sha256=existing.raw_payload_sha256,
            )

        try:
            frame = await self._fetch(endpoint, trade_date)
        except Exception as exc:  # noqa: BLE001 - record + continue (resume retries)
            log.warning(
                "historical_ingest_fetch_failed",
                endpoint=endpoint,
                trade_date=trade_date,
                error=str(exc),
            )
            return EndpointResult(
                endpoint=endpoint,
                trade_date=trade_date,
                status=_STATUS_FAILED,
                rows=0,
                error=str(exc),
            )

        if endpoint in _REQUIRE_NON_EMPTY and (frame is None or frame.empty):
            msg = "empty frame on a trading day"
            log.error(
                "historical_ingest_empty_required",
                endpoint=endpoint,
                trade_date=trade_date,
            )
            return EndpointResult(
                endpoint=endpoint,
                trade_date=trade_date,
                status=_STATUS_FAILED,
                rows=0,
                error=msg,
            )

        frame = frame if frame is not None else pd.DataFrame()
        raw = canonical_csv_bytes(frame)
        snapshot = MarketDataSnapshot.create(
            vendor=VENDOR,
            endpoint=endpoint,
            params={"trade_date": trade_date},
            trade_date=trade_date,
            raw_payload=raw,
            encoding="csv",
            compression="none",
            fetch_time_utc=self._now_utc(),
            metadata={"rows": int(len(frame)), "run_id": run_id},
        )
        try:
            self._store.put(snapshot)
        except SnapshotOverwriteError:
            # Raced with another writer / already present — idempotent skip.
            return EndpointResult(
                endpoint=endpoint,
                trade_date=trade_date,
                status=_STATUS_SKIPPED,
                rows=int(len(frame)),
                sha256=snapshot.raw_payload_sha256,
            )

        if endpoint == "daily":
            await self._write_secondary(trade_date, frame)

        return EndpointResult(
            endpoint=endpoint,
            trade_date=trade_date,
            status=_STATUS_INGESTED,
            rows=int(len(frame)),
            sha256=snapshot.raw_payload_sha256,
        )

    async def _fetch(self, endpoint: str, trade_date: str) -> pd.DataFrame:
        # Throttle off the event loop (offline batch; sleep may be ~60s).
        await asyncio.to_thread(self._rate.acquire)
        dispatch = {
            "daily": self._client.daily,
            "adj_factor": self._client.adj_factor,
            "daily_basic": self._client.daily_basic,
            "fund_daily": self._client.fund_daily,
        }
        try:
            method = dispatch[endpoint]
        except KeyError:
            raise ValueError(f"unsupported endpoint {endpoint!r}") from None
        return await method(trade_date)

    def _has_secondary(self) -> bool:
        return self._kline_writer is not None or (
            self._coverage_store is not None and self._universe is not None
        )

    async def _backfill_secondary(
        self, trade_date: str, snapshot: MarketDataSnapshot
    ) -> None:
        """Rebuild derived artifacts from an already-stored daily snapshot.

        Reads the *verified* (checksum-checked) stored bytes — never re-fetches
        — so a re-run can populate kline / coverage that an earlier
        snapshot-only run left out.
        """
        if not self._has_secondary():
            return
        frame = parse_csv_bytes(snapshot.raw_payload)
        await self._write_secondary(trade_date, frame)

    async def _write_secondary(self, trade_date: str, frame: pd.DataFrame) -> None:
        """Best-effort structured-row + coverage writes (derived from PIT)."""
        if self._kline_writer is not None:
            try:
                await self._kline_writer.save_daily_frame(trade_date, frame)
            except Exception as exc:  # noqa: BLE001 - secondary store is derived
                log.warning(
                    "historical_ingest_kline_write_failed",
                    trade_date=trade_date,
                    error=str(exc),
                )
        if self._coverage_store is not None and self._universe is not None:
            # The coverage store is append-only — don't duplicate a manifest
            # that a prior run already wrote (the backfill path re-enters here).
            if (
                self._coverage_store.get(endpoint="daily", session_end=trade_date)
                is not None
            ):
                return
            requested = sorted(self._universe.tradable_asof(trade_date))
            delivered = sorted(
                {str(c).strip() for c in frame.get("ts_code", pd.Series(dtype=str))}
            )
            manifest = CoverageManifest(
                granularity="daily",
                endpoint="daily",
                params={"trade_date": trade_date, "vendor": VENDOR},
                session_start=trade_date,
                session_end=trade_date,
                requested_universe=tuple(requested),
                delivered_universe=tuple(delivered),
            )
            self._coverage_store.put(manifest)


__all__ = [
    "DEFAULT_ENDPOINTS",
    "VENDOR",
    "EndpointResult",
    "HistoricalIngestJob",
    "IngestReport",
    "KlineRowWriter",
]
