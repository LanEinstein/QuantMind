"""Tests for the round-2 PIT data ingest orchestrator (R2-1).

Cover the pure enumerators, the idempotent/fail-closed persistence, the
survivorship round-trip, and the coverage manifest — all with an injected fake
client so no token / network is needed.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.coverage import CoverageStore
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.ingest_round2_data import (
    EP_FINA,
    EP_INDEX_MEMBER,
    EP_INDEX_WEIGHT,
    EP_STOCK_BASIC_D,
    EP_STOCK_BASIC_L,
    _build_coverage,
    build_fina_coverage_manifests,
    ingest_fina_indicator,
    ingest_index_member_all,
    ingest_index_weight,
    ingest_round2,
    ingest_stock_basic,
    load_survivorship,
    month_end_trade_dates,
    report_periods,
)

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _now() -> datetime:
    return FIXED_NOW


class _FakeRound2Client:
    """Records calls; returns canned (or per-key) frames; can fail periods."""

    def __init__(
        self,
        *,
        weight: pd.DataFrame | None = None,
        fina: dict[str, pd.DataFrame] | None = None,
        member: pd.DataFrame | None = None,
        listed: pd.DataFrame | None = None,
        delisted: pd.DataFrame | None = None,
        empty_periods: set[str] | None = None,
    ) -> None:
        self._weight = weight
        self._fina = fina or {}
        self._member = member
        self._listed = listed
        self._delisted = delisted
        self._empty_periods = empty_periods or set()
        self.calls: list[tuple[str, str]] = []

    async def index_weight(
        self,
        index_code: str,
        *,
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> pd.DataFrame:
        self.calls.append(("index_weight", end_date or trade_date))
        if self._weight is not None:
            return self._weight
        return pd.DataFrame(
            {"con_code": ["600519.SH"], "trade_date": [end_date], "weight": [5.0]}
        )

    async def fina_indicator_vip(self, period: str) -> pd.DataFrame:
        self.calls.append(("fina_indicator_vip", period))
        if period in self._empty_periods:
            return pd.DataFrame()
        return self._fina.get(
            period,
            pd.DataFrame(
                {"ts_code": ["600519.SH"], "end_date": [period], "roe": [20.0]}
            ),
        )

    async def index_member_all(self) -> pd.DataFrame:
        self.calls.append(("index_member_all", ""))
        if self._member is not None:
            return self._member
        return pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "l1_name": ["食品饮料"],
                "in_date": ["20150101"],
                "out_date": [""],
            }
        )

    async def stock_basic(self, *, list_status: str, fields: str) -> pd.DataFrame:
        self.calls.append(("stock_basic", list_status))
        if list_status == "L":
            if self._listed is not None:
                return self._listed
            return pd.DataFrame(
                {
                    "ts_code": ["600519.SH", "000001.SZ"],
                    "name": ["贵州茅台", "平安银行"],
                    "list_date": ["20010827", "19910403"],
                    "delist_date": ["", ""],
                }
            )
        if self._delisted is not None:
            return self._delisted
        return pd.DataFrame(
            {
                "ts_code": ["600001.SH"],
                "name": ["邯郸钢铁"],
                "list_date": ["19980122"],
                "delist_date": ["20100825"],
            }
        )


# --- pure enumerators --------------------------------------------------------


class TestEnumerators:
    def test_report_periods_quarter_ends(self) -> None:
        assert report_periods(2015, "20160630") == [
            "20150331",
            "20150630",
            "20150930",
            "20151231",
            "20160331",
            "20160630",
        ]

    def test_report_periods_excludes_future_periods(self) -> None:
        # last_date mid-Q2 → only 20240331 <= 20240615 (0630 excluded).
        assert report_periods(2024, "20240615") == ["20240331"]

    def test_report_periods_rejects_bad_last_date(self) -> None:
        with pytest.raises(ValueError, match="YYYYMMDD"):
            report_periods(2015, "2016-06")

    def test_month_end_trade_dates_drops_partial_last_month(self) -> None:
        # 201503 is the highest (possibly-incomplete) month → dropped by default.
        calendar = ["20150105", "20150130", "20150202", "20150227", "20150302"]
        assert month_end_trade_dates(calendar) == ["20150130", "20150227"]

    def test_month_end_trade_dates_include_partial_last(self) -> None:
        calendar = ["20150105", "20150130", "20150202", "20150227", "20150302"]
        assert month_end_trade_dates(calendar, include_partial_last=True) == [
            "20150130",
            "20150227",
            "20150302",
        ]


# --- persistence + idempotency ----------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio
    async def test_index_weight_persists_per_month_end(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        # 3 complete months (the 4th, partial, would be dropped) → 3 snapshots.
        calendar = ["20150105", "20150130", "20150227", "20150330", "20150402"]
        results = await ingest_index_weight(client, store, calendar, now=_now)
        assert [r.status for r in results] == ["ingested", "ingested", "ingested"]
        snap = store.latest(
            vendor="tushare", endpoint=EP_INDEX_WEIGHT, trade_date="20150130"
        )
        assert snap is not None
        assert snap.params == {
            "index_code": "000300.SH",
            "start_date": "20150101",
            "end_date": "20150130",
        }

    @pytest.mark.asyncio
    async def test_byte_exact_checksum(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        frame = pd.DataFrame({"ts_code": ["600519.SH"], "roe": [20.0]})
        client = _FakeRound2Client(fina={"20240331": frame})
        await ingest_fina_indicator(client, store, ["20240331"], now=_now)
        snap = store.latest(vendor="tushare", endpoint=EP_FINA, trade_date="20240331")
        assert snap is not None
        assert (
            snap.raw_payload_sha256
            == hashlib.sha256(canonical_csv_bytes(frame)).hexdigest()
        )

    @pytest.mark.asyncio
    async def test_idempotent_resume_skips_without_refetch(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        first = await ingest_fina_indicator(client, store, ["20240331"], now=_now)
        assert first[0].status == "ingested"
        n_calls = len(client.calls)
        second = await ingest_fina_indicator(client, store, ["20240331"], now=_now)
        assert second[0].status == "skipped"
        # No re-fetch on the resume run.
        assert len(client.calls) == n_calls

    @pytest.mark.asyncio
    async def test_empty_required_frame_fails_closed(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client(empty_periods={"20240331"})
        results = await ingest_fina_indicator(client, store, ["20240331"], now=_now)
        assert results[0].status == "failed"
        # Nothing stored — a re-run retries it.
        assert (
            store.latest(vendor="tushare", endpoint=EP_FINA, trade_date="20240331")
            is None
        )

    @pytest.mark.asyncio
    async def test_index_member_all_single_asof_snapshot(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        result = await ingest_index_member_all(client, store, "20260618", now=_now)
        assert result.status == "ingested"
        assert (
            store.latest(
                vendor="tushare", endpoint=EP_INDEX_MEMBER, trade_date="20260618"
            )
            is not None
        )


# --- survivorship + coverage -------------------------------------------------


class TestSurvivorshipCoverage:
    @pytest.mark.asyncio
    async def test_stock_basic_round_trips_to_survivorship(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        results = await ingest_stock_basic(client, store, "20260618", now=_now)
        assert [r.endpoint for r in results] == [EP_STOCK_BASIC_L, EP_STOCK_BASIC_D]
        universe = load_survivorship(store, "20260618")
        assert universe.all_codes() == frozenset(
            {"600519.SH", "000001.SZ", "600001.SH"}
        )
        # PIT tradability: the delisted name is absent after its delist date.
        assert "600001.SH" in universe.tradable_asof("20050101")
        assert "600001.SH" not in universe.tradable_asof("20200101")

    @pytest.mark.asyncio
    async def test_per_period_coverage_flags_listed_code_without_report(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)
        # Fundamentals only return 600519.SH for the period.
        await ingest_fina_indicator(client, store, ["20240331"], now=_now)
        universe = load_survivorship(store, "20260618")
        manifests = build_fina_coverage_manifests(store, ["20240331"], universe)
        assert len(manifests) == 1
        manifest = manifests[0]
        assert manifest.session_start == manifest.session_end == "20240331"
        # requested = tradable AS-OF the period: 600001.SH (delisted 2010) excluded;
        # 000001.SZ is listed but filed no report → correctly flagged missing.
        assert set(manifest.requested_universe) == {"600519.SH", "000001.SZ"}
        assert manifest.delivered_universe == ("600519.SH",)
        assert set(manifest.missing_symbols) == {"000001.SZ"}
        assert manifest.completeness == pytest.approx(1 / 2)

    @pytest.mark.asyncio
    async def test_coverage_fails_closed_on_missing_period(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)
        universe = load_survivorship(store, "20260618")
        # No fina snapshot stored for 20240331 → fail-closed, never a silent skip.
        with pytest.raises(FileNotFoundError, match="20240331"):
            build_fina_coverage_manifests(store, ["20240331"], universe)


# --- orchestrator integration ------------------------------------------------


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_full_run_no_complete_quarter_skips_coverage(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        # last_date 20240228 → no quarter end ≤ it; 202402 month is partial → dropped.
        calendar = ["20240105", "20240131", "20240228"]
        report = await ingest_round2(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20260618",
            now=_now,
        )
        assert report.failed == 0
        # L, D, member, 1 complete-month weight (202401); no fina periods.
        assert report.ingested == 4
        assert report.fina_coverage == ()

    @pytest.mark.asyncio
    async def test_full_run_with_periods_builds_per_period_coverage(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        calendar = ["20240105", "20240131", "20240401"]  # spans Q1 end
        report = await ingest_round2(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20260618",
            now=_now,
        )
        assert report.failed == 0
        assert report.ingested == 5  # L, D, member, 1 weight (202401), 1 fina (Q1)
        assert len(report.fina_coverage) == 1
        assert coverage_store.get(endpoint=EP_FINA, session_end="20240331") is not None

    @pytest.mark.asyncio
    async def test_rerun_idempotent_no_coverage_duplication(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        calendar = ["20240105", "20240131", "20240401"]
        first = await ingest_round2(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20260618",
            now=_now,
        )
        assert first.ingested == 5 and first.failed == 0
        second = await ingest_round2(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20260618",
            now=_now,
        )
        # Resume: every endpoint skipped, no new coverage row appended.
        assert second.ingested == 0
        assert second.skipped == 5
        cov_lines = [
            ln
            for ln in (tmp_path / "coverage.jsonl").read_text().splitlines()
            if ln.strip()
        ]
        assert len(cov_lines) == 1

    @pytest.mark.asyncio
    async def test_coverage_section_fails_closed_on_missing_period(
        self, tmp_path: Path
    ) -> None:
        # No fina snapshot for the period (no blocking ingest failure recorded)
        # → coverage must surface a FAILED result, not warn-and-succeed.
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)
        cov_results, coverage = _build_coverage(
            store,
            coverage_store,
            periods=["20240331"],
            asof="20260618",
            blocking=False,
        )
        assert coverage == ()
        assert len(cov_results) == 1
        assert cov_results[0].status == "failed"
        assert cov_results[0].endpoint == "coverage"
