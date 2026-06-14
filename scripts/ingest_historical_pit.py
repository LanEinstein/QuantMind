#!/usr/bin/env python
"""Owner-gated CLI for the AE-001 bulk historical PIT ingestion.

Governing decision: ``P0-8-amendment-2026-06-14-bulk-historical-pit-ingestion``.

The full run pulls Tushare full-market ``daily`` / ``adj_factor`` /
``daily_basic`` / ``fund_daily`` for **2015-present** across the whole market
plus delisted codes — thousands of rate-limited calls, possibly hours. It is
therefore an **owner-gated** operation: run it deliberately. Each pull is
persisted byte-exact into the K-002 ``SnapshotStore`` (raw bytes + checksum),
and the job is idempotent/resumable — re-running after an interruption only
fetches the gap.

Examples
--------
Small dry-run (first 3 trading days, snapshot-only, no Mongo)::

    python scripts/ingest_historical_pit.py --dry-run --snapshot-root data/pit

Full run (snapshots + coverage + kline_daily rows)::

    python scripts/ingest_historical_pit.py \\
        --start 20150101 --end 20260613 \\
        --snapshot-root data/marketdata_pit \\
        --with-coverage --with-kline

``TUSHARE_TOKEN`` must be exported in the environment (~/.bashrc).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import structlog

from backend.data.historical_ingest.calendar_provider import TushareTradeCalendar
from backend.data.historical_ingest.job import HistoricalIngestJob, IngestReport
from backend.data.historical_ingest.rate_limiter import RateLimiter
from backend.data.historical_ingest.universe import SurvivorshipUniverse
from backend.data.tushare_client import TushareClient
from backend.marketdata_snapshot.coverage import CoverageStore
from backend.marketdata_snapshot.store import SnapshotStore

log = structlog.get_logger(component="scripts.ingest_historical_pit")


def _parse_args() -> argparse.Namespace:
    today = datetime.now().strftime("%Y%m%d")  # noqa: DTZ005 - local cutoff bound
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="20150101", help="YYYYMMDD (incl.)")
    parser.add_argument("--end", default=today, help="YYYYMMDD (incl.)")
    parser.add_argument(
        "--snapshot-root",
        default="data/marketdata_pit",
        help="Filesystem root for the byte-exact PIT SnapshotStore.",
    )
    parser.add_argument(
        "--max-per-minute",
        type=int,
        default=400,
        help="Tushare call throttle (0 disables).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ingest only the first --dry-run-days trading days (no kline).",
    )
    parser.add_argument("--dry-run-days", type=int, default=3)
    parser.add_argument(
        "--with-coverage",
        action="store_true",
        help="Build the survivorship universe + write coverage manifests.",
    )
    parser.add_argument(
        "--with-kline",
        action="store_true",
        help="Also write structured kline_daily rows (needs Mongo).",
    )
    parser.add_argument(
        "--mongo-uri", default="mongodb://127.0.0.1:27017"
    )
    parser.add_argument("--mongo-db", default="quantmind")
    return parser.parse_args()


async def _build_universe(client: TushareClient) -> SurvivorshipUniverse:
    """Fetch listed + delisted rosters and build the survivorship universe."""
    listed = await client.stock_basic(
        list_status="L", fields="ts_code,name,list_date,delist_date"
    )
    delisted = await client.stock_basic(
        list_status="D", fields="ts_code,name,list_date,delist_date"
    )
    return SurvivorshipUniverse.from_stock_basic(listed, delisted)


async def _run(args: argparse.Namespace) -> IngestReport:
    client = TushareClient()  # token from TUSHARE_TOKEN
    store = SnapshotStore(args.snapshot_root)
    calendar = TushareTradeCalendar(client)
    rate = RateLimiter(args.max_per_minute)

    coverage_store: CoverageStore | None = None
    universe: SurvivorshipUniverse | None = None
    if args.with_coverage:
        universe = await _build_universe(client)
        coverage_store = CoverageStore(args.snapshot_root + "/coverage")

    kline_writer = None
    mongo_client = None
    if args.with_kline:
        from motor.motor_asyncio import AsyncIOMotorClient

        from backend.data.database import MongoDBService

        mongo_client = AsyncIOMotorClient(
            args.mongo_uri, uuidRepresentation="standard"
        )
        kline_writer = MongoDBService(mongo_client[args.mongo_db])

    job = HistoricalIngestJob(
        client=client,
        snapshot_store=store,
        calendar=calendar,
        rate_limiter=rate,
        kline_writer=kline_writer,
        coverage_store=coverage_store,
        universe=universe,
    )

    try:
        if args.dry_run:
            days = await calendar.trading_days(args.start, args.end)
            sample = days[: max(0, args.dry_run_days)]
            log.info("dry_run_trading_days", count=len(sample), days=list(sample))
            results = []
            for trade_date in sample:
                results.extend(await job.ingest_trade_date(trade_date))
            return IngestReport(results=tuple(results))
        return await job.ingest_range(args.start, args.end)
    finally:
        if mongo_client is not None:
            mongo_client.close()


def main() -> None:
    args = _parse_args()
    report = asyncio.run(_run(args))
    print(
        f"ingested={report.ingested} skipped={report.skipped} "
        f"failed={report.failed}"
    )
    for failure in report.failures[:20]:
        print(f"  FAILED {failure.endpoint} {failure.trade_date}: {failure.error}")


if __name__ == "__main__":
    main()
