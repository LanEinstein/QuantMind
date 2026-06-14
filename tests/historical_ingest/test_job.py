"""Tests for the AE-001 HistoricalIngestJob orchestrator.

Red lines exercised: byte-exact PIT storage, idempotent/resumable re-runs,
fail-closed on fetch failure / empty-daily, secondary kline writer + coverage,
verify-before-adopt on read (checksum).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from backend.data.historical_ingest.calendar_provider import StaticTradeCalendar
from backend.data.historical_ingest.job import VENDOR, HistoricalIngestJob
from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.data.historical_ingest.universe import SurvivorshipUniverse
from backend.marketdata_snapshot.coverage import CoverageStore
from backend.marketdata_snapshot.store import (
    ChecksumMismatchError,
    SnapshotStore,
)

_FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _daily(codes: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"ts_code": codes, "close": closes})


def _adj(codes: list[str], factors: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"ts_code": codes, "adj_factor": factors})


class _FakeClient:
    """Per-(trade_date, endpoint) canned frames; optional raising endpoints."""

    def __init__(
        self,
        frames: dict[str, dict[str, pd.DataFrame]],
        *,
        raise_endpoints: set[str] | None = None,
    ) -> None:
        self._frames = frames
        self._raise = raise_endpoints or set()
        self.calls: list[tuple[str, str]] = []

    async def _get(self, endpoint: str, trade_date: str) -> pd.DataFrame:
        self.calls.append((endpoint, trade_date))
        if endpoint in self._raise:
            raise RuntimeError(f"vendor down: {endpoint}")
        return self._frames.get(trade_date, {}).get(endpoint, pd.DataFrame())

    async def daily(self, trade_date: str) -> pd.DataFrame:
        return await self._get("daily", trade_date)

    async def adj_factor(self, trade_date: str) -> pd.DataFrame:
        return await self._get("adj_factor", trade_date)

    async def daily_basic(self, trade_date: str) -> pd.DataFrame:
        return await self._get("daily_basic", trade_date)

    async def fund_daily(self, trade_date: str) -> pd.DataFrame:
        return await self._get("fund_daily", trade_date)


class _FakeKlineWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def save_daily_frame(self, trade_date: str, df: pd.DataFrame) -> int:
        self.calls.append((trade_date, len(df)))
        return len(df)


def _job(
    tmp_path,
    client: _FakeClient,
    *,
    endpoints=("daily", "adj_factor"),
    **kwargs,
) -> HistoricalIngestJob:
    return HistoricalIngestJob(
        client=client,
        snapshot_store=SnapshotStore(tmp_path / "snap"),
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=endpoints,
        **kwargs,
    )


async def test_ingests_byte_exact_pit(tmp_path) -> None:
    frame = _daily(["600519.SH", "000001.SZ"], [1700.0, 10.5])
    client = _FakeClient(
        {"20180102": {"daily": frame, "adj_factor": _adj(["x"], [1.0])}}
    )
    store = SnapshotStore(tmp_path / "snap")
    job = HistoricalIngestJob(
        client=client,
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily", "adj_factor"),
    )
    report = await job.ingest_range("20180101", "20180103")
    assert report.ingested == 2 and report.failed == 0

    snap = store.latest(vendor=VENDOR, endpoint="daily", trade_date="20180102")
    assert snap is not None
    # The stored bytes are the canonical serialization (idempotency anchor).
    assert snap.raw_payload == canonical_csv_bytes(frame)
    assert snap.metadata["rows"] == 2


async def test_idempotent_rerun_skips(tmp_path) -> None:
    frame = _daily(["600519.SH"], [1700.0])
    client = _FakeClient(
        {"20180102": {"daily": frame, "adj_factor": _adj(["x"], [1.0])}}
    )
    store = SnapshotStore(tmp_path / "snap")

    def make_job() -> HistoricalIngestJob:
        return HistoricalIngestJob(
            client=client,
            snapshot_store=store,
            calendar=StaticTradeCalendar(["20180102"]),
            now_utc=lambda: _FIXED_NOW,
            endpoints=("daily", "adj_factor"),
        )

    first = await make_job().ingest_range("20180101", "20180103")
    assert first.ingested == 2
    second = await make_job().ingest_range("20180101", "20180103")
    assert second.ingested == 0 and second.skipped == 2
    # No spurious second version was written.
    versions = store.versions(
        vendor=VENDOR, endpoint="daily", trade_date="20180102"
    )
    assert len(versions) == 1


async def test_resume_skips_already_stored(tmp_path) -> None:
    frame = _daily(["600519.SH"], [1700.0])
    client = _FakeClient(
        {"20180102": {"daily": frame, "adj_factor": _adj(["x"], [1.0])}}
    )
    store = SnapshotStore(tmp_path / "snap")
    # Pre-store the daily snapshot so only adj_factor remains.
    from backend.marketdata_snapshot.snapshot import MarketDataSnapshot

    store.put(
        MarketDataSnapshot.create(
            vendor=VENDOR,
            endpoint="daily",
            params={"trade_date": "20180102"},
            trade_date="20180102",
            raw_payload=canonical_csv_bytes(frame),
            encoding="csv",
            compression="none",
            fetch_time_utc=_FIXED_NOW,
            metadata={"rows": 1},
        )
    )
    job = HistoricalIngestJob(
        client=client,
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily", "adj_factor"),
    )
    report = await job.ingest_range("20180101", "20180103")
    assert report.skipped == 1 and report.ingested == 1
    assert ("daily", "20180102") not in client.calls  # daily was not re-fetched


async def test_fetch_failure_is_fail_closed_then_resumable(tmp_path) -> None:
    frame = _daily(["600519.SH"], [1700.0])
    store = SnapshotStore(tmp_path / "snap")
    raising = _FakeClient(
        {"20180102": {"daily": frame, "adj_factor": _adj(["x"], [1.0])}},
        raise_endpoints={"adj_factor"},
    )
    job1 = HistoricalIngestJob(
        client=raising,
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily", "adj_factor"),
    )
    r1 = await job1.ingest_range("20180101", "20180103")
    assert r1.ingested == 1 and r1.failed == 1
    # Nothing was stored for the failed endpoint (no partial/empty payload).
    assert (
        store.latest(vendor=VENDOR, endpoint="adj_factor", trade_date="20180102")
        is None
    )

    # Re-run with a healthy client picks up exactly the gap.
    healthy = _FakeClient(
        {"20180102": {"daily": frame, "adj_factor": _adj(["x"], [1.0])}}
    )
    job2 = HistoricalIngestJob(
        client=healthy,
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily", "adj_factor"),
    )
    r2 = await job2.ingest_range("20180101", "20180103")
    assert r2.ingested == 1 and r2.skipped == 1


async def test_empty_daily_on_trading_day_fails(tmp_path) -> None:
    client = _FakeClient({"20180102": {"daily": pd.DataFrame()}})
    job = _job(tmp_path, client, endpoints=("daily",))
    report = await job.ingest_range("20180101", "20180103")
    assert report.failed == 1 and report.ingested == 0
    assert report.failures[0].error == "empty frame on a trading day"


async def test_empty_adj_factor_on_trading_day_fails(tmp_path) -> None:
    # adj_factor is a required PIT pin — an empty trading-day pull must
    # fail-closed (not be stored as a successful empty snapshot). codex P2.
    frame = _daily(["600519.SH"], [1700.0])
    client = _FakeClient({"20180102": {"daily": frame, "adj_factor": pd.DataFrame()}})
    job = _job(tmp_path, client, endpoints=("daily", "adj_factor"))
    report = await job.ingest_range("20180101", "20180103")
    assert report.ingested == 1  # daily ok
    assert report.failed == 1
    assert report.failures[0].endpoint == "adj_factor"


def _listed_two() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["600519.SH", "000001.SZ"],
            "name": ["a", "b"],
            "list_date": ["20010827", "19910403"],
            "delist_date": [None, None],
            "list_status": ["L", "L"],
        }
    )


async def test_backfill_secondary_from_stored_snapshot_on_skip(tmp_path) -> None:
    frame = _daily(["600519.SH"], [1700.0])
    store = SnapshotStore(tmp_path / "snap")
    # First run: snapshot-only (no kline / coverage writers).
    job1 = HistoricalIngestJob(
        client=_FakeClient({"20180102": {"daily": frame}}),
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily",),
    )
    await job1.ingest_range("20180101", "20180103")

    # Second run on the SAME store, now with secondary writers enabled. The
    # daily snapshot already exists → skipped, but the derived artifacts must
    # be backfilled from the verified stored bytes (no re-fetch).
    writer = _FakeKlineWriter()
    universe = SurvivorshipUniverse.from_stock_basic(_listed_two(), pd.DataFrame())
    cov = CoverageStore(tmp_path / "cov")
    job2 = HistoricalIngestJob(
        client=_FakeClient({}),  # empty: a re-fetch would KeyError/return empty
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily",),
        kline_writer=writer,
        coverage_store=cov,
        universe=universe,
    )
    report = await job2.ingest_range("20180101", "20180103")
    assert report.skipped == 1 and report.ingested == 0
    assert writer.calls == [("20180102", 1)]  # backfilled from stored payload
    manifest = cov.get(endpoint="daily", session_end="20180102")
    assert manifest is not None and "000001.SZ" in manifest.missing_symbols

    # Third run must NOT duplicate the append-only coverage manifest.
    job3 = HistoricalIngestJob(
        client=_FakeClient({}),
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily",),
        kline_writer=_FakeKlineWriter(),
        coverage_store=cov,
        universe=universe,
    )
    await job3.ingest_range("20180101", "20180103")
    rows = (tmp_path / "cov" / "coverage.jsonl").read_text().strip().splitlines()
    assert len(rows) == 1  # still one manifest, not duplicated


async def test_kline_writer_called_for_daily_only(tmp_path) -> None:
    frame = _daily(["600519.SH", "000001.SZ"], [1700.0, 10.5])
    client = _FakeClient(
        {"20180102": {"daily": frame, "adj_factor": _adj(["x"], [1.0])}}
    )
    writer = _FakeKlineWriter()
    store = SnapshotStore(tmp_path / "snap")
    job = HistoricalIngestJob(
        client=client,
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily", "adj_factor"),
        kline_writer=writer,
    )
    await job.ingest_range("20180101", "20180103")
    assert writer.calls == [("20180102", 2)]  # daily only, 2 rows


async def test_coverage_manifest_flags_missing(tmp_path) -> None:
    # Universe expects two codes; vendor delivers only one → completeness < 1.
    listed = pd.DataFrame(
        {
            "ts_code": ["600519.SH", "000001.SZ"],
            "name": ["a", "b"],
            "list_date": ["20010827", "19910403"],
            "delist_date": [None, None],
            "list_status": ["L", "L"],
        }
    )
    universe = SurvivorshipUniverse.from_stock_basic(listed, pd.DataFrame())
    frame = _daily(["600519.SH"], [1700.0])  # 000001.SZ missing
    client = _FakeClient({"20180102": {"daily": frame}})
    cov = CoverageStore(tmp_path / "cov")
    store = SnapshotStore(tmp_path / "snap")
    job = HistoricalIngestJob(
        client=client,
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily",),
        coverage_store=cov,
        universe=universe,
    )
    await job.ingest_range("20180101", "20180103")
    manifest = cov.get(endpoint="daily", session_end="20180102")
    assert manifest is not None
    assert "000001.SZ" in manifest.missing_symbols
    assert manifest.completeness < 1.0


async def test_stored_payload_is_verified_on_read(tmp_path) -> None:
    frame = _daily(["600519.SH"], [1700.0])
    client = _FakeClient({"20180102": {"daily": frame}})
    store = SnapshotStore(tmp_path / "snap")
    job = HistoricalIngestJob(
        client=client,
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
        endpoints=("daily",),
    )
    await job.ingest_range("20180101", "20180103")
    snap = store.latest(vendor=VENDOR, endpoint="daily", trade_date="20180102")
    assert snap is not None
    # Tamper the on-disk payload → verify-before-adopt must reject it.
    sha = snap.raw_payload_sha256
    payload_path = (tmp_path / "snap" / "payloads" / sha[:2] / f"{sha}.bin")
    payload_path.write_bytes(b"corrupted")
    with pytest.raises(ChecksumMismatchError):
        store.get(snap.snapshot_id)


async def test_full_default_endpoints_e2e(tmp_path) -> None:
    frame_d = _daily(["600519.SH"], [1700.0])
    frames = {
        "20180102": {
            "daily": frame_d,
            "adj_factor": _adj(["600519.SH"], [1.23]),
            "daily_basic": pd.DataFrame({"ts_code": ["600519.SH"], "pe": [30.0]}),
            "fund_daily": pd.DataFrame({"ts_code": ["510300.SH"], "close": [4.0]}),
        }
    }
    client = _FakeClient(frames)
    store = SnapshotStore(tmp_path / "snap")
    job = HistoricalIngestJob(
        client=client,
        snapshot_store=store,
        calendar=StaticTradeCalendar(["20180102"]),
        now_utc=lambda: _FIXED_NOW,
    )
    report = await job.ingest_range("20180101", "20180103")
    assert report.ingested == 4  # all four default endpoints
    for endpoint in ("daily", "adj_factor", "daily_basic", "fund_daily"):
        assert (
            store.latest(vendor=VENDOR, endpoint=endpoint, trade_date="20180102")
            is not None
        )
