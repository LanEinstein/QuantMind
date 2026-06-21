"""Tests for the round-2 PIT data ingest orchestrator (R2-1).

Cover the pure enumerators, the idempotent/fail-closed persistence, the
survivorship round-trip, and the coverage manifest — all with an injected fake
client so no token / network is needed.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import (
    canonical_csv_bytes,
    parse_csv_bytes,
)
from backend.marketdata_snapshot.coverage import CoverageStore
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.ingest_round2_data import (
    CYQ_PERF_FIRST_DATE,
    EP_BALANCESHEET,
    EP_CASHFLOW,
    EP_CYQ_PERF,
    EP_EXPRESS,
    EP_FINA,
    EP_FORECAST,
    EP_INCOME,
    EP_INDEX_CLASSIFY,
    EP_INDEX_MEMBER,
    EP_INDEX_WEIGHT,
    EP_LIMIT_LIST_D,
    EP_NAMECHANGE,
    EP_REPORT_RC,
    EP_STK_FACTOR_PRO,
    EP_STK_LIMIT,
    EP_STOCK_BASIC_D,
    EP_STOCK_BASIC_L,
    EP_SUSPEND_D,
    EP_THS_INDEX,
    EVENT_STREAM_REQUIRE_NON_EMPTY,
    EXPRESS_VIP_FIELDS,
    LIMIT_LIST_D_FIELDS,
    LIMIT_LIST_D_FIRST_DATE,
    STATEMENT_ENDPOINTS,
    SUSPEND_D_FIELDS,
    _build_coverage,
    build_daily_coverage_manifests,
    build_fina_coverage_manifests,
    ingest_event_stream,
    ingest_fina_indicator,
    ingest_fullmarket_daily,
    ingest_index_member_all,
    ingest_index_weight,
    ingest_namechange,
    ingest_qgr,
    ingest_report_rc,
    ingest_round2,
    ingest_round3,
    ingest_round4,
    ingest_sparse_daily,
    ingest_statement,
    ingest_stock_basic,
    ingest_theme_catalogs,
    load_survivorship,
    month_end_trade_dates,
    namechange_pages,
    namechange_years,
    report_periods,
    report_rc_month_ranges,
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
        statements: dict[tuple[str, str], pd.DataFrame] | None = None,
        empty_statements: set[tuple[str, str]] | None = None,
        namechange_by_year: dict[int, pd.DataFrame] | None = None,
        report_rc: dict[str, pd.DataFrame] | None = None,
        report_rc_empty: set[str] | None = None,
        qgr_frames: dict[tuple[str, str], pd.DataFrame] | None = None,
        qgr_empty: set[tuple[str, str]] | None = None,
    ) -> None:
        self._weight = weight
        self._fina = fina or {}
        self._member = member
        self._listed = listed
        self._delisted = delisted
        self._empty_periods = empty_periods or set()
        self._statements = statements or {}
        self._empty_statements = empty_statements or set()
        self._namechange_by_year = namechange_by_year or {}
        self._report_rc = report_rc or {}
        self._report_rc_empty = report_rc_empty or set()
        self._qgr_frames = qgr_frames or {}
        self._qgr_empty = qgr_empty or set()
        self.calls: list[tuple[str, str]] = []

    def _qgr_frame(self, endpoint: str, key: str) -> pd.DataFrame:
        if (endpoint, key) in self._qgr_empty:
            return pd.DataFrame()
        if (endpoint, key) in self._qgr_frames:
            return self._qgr_frames[(endpoint, key)]
        return pd.DataFrame(
            {"ts_code": ["600519.SH"], "trade_date": [key], "val": [1.0]}
        )

    async def stk_limit(
        self, trade_date: str, *, throttle: Any | None = None
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()  # one page → one token (mirrors the real client)
        self.calls.append(("stk_limit", trade_date))
        return self._qgr_frame("stk_limit", trade_date)

    async def cyq_perf(
        self, trade_date: str, *, throttle: Any | None = None
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()
        self.calls.append(("cyq_perf", trade_date))
        return self._qgr_frame("cyq_perf", trade_date)

    async def stk_factor_pro(
        self, trade_date: str, *, throttle: Any | None = None
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()
        self.calls.append(("stk_factor_pro", trade_date))
        return self._qgr_frame("stk_factor_pro", trade_date)

    async def limit_list_d(self, trade_date: str) -> pd.DataFrame:
        self.calls.append(("limit_list_d", trade_date))
        return self._qgr_frame("limit_list_d", trade_date)

    async def suspend_d(self, trade_date: str) -> pd.DataFrame:
        self.calls.append(("suspend_d", trade_date))
        return self._qgr_frame("suspend_d", trade_date)

    async def forecast_vip(
        self,
        period: str = "",
        *,
        start_date: str = "",
        end_date: str = "",
        throttle: Any | None = None,
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()
        key = end_date or period
        self.calls.append(("forecast_vip", key))
        return self._qgr_frame("forecast_vip", key)

    async def express_vip(
        self,
        period: str = "",
        *,
        start_date: str = "",
        end_date: str = "",
        throttle: Any | None = None,
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()
        key = end_date or period
        self.calls.append(("express_vip", key))
        return self._qgr_frame("express_vip", key)

    async def ths_index(self, *, index_type: str = "") -> pd.DataFrame:
        self.calls.append(("ths_index", index_type))
        return self._qgr_frame("ths_index", "asof")

    async def index_classify(
        self, *, level: str = "", src: str = "SW2021"
    ) -> pd.DataFrame:
        self.calls.append(("index_classify", src))
        return self._qgr_frame("index_classify", "asof")

    def _statement(self, endpoint: str, period: str) -> pd.DataFrame:
        self.calls.append((endpoint, period))
        if (endpoint, period) in self._empty_statements:
            return pd.DataFrame()
        return self._statements.get(
            (endpoint, period),
            pd.DataFrame(
                {
                    "ts_code": ["600519.SH"],
                    "end_date": [period],
                    "ann_date": [period],
                    "report_type": ["1"],
                }
            ),
        )

    async def income_vip(
        self, period: str, *, throttle: Any | None = None
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()  # one page → one token (mirrors the real client)
        return self._statement("income_vip", period)

    async def cashflow_vip(
        self, period: str, *, throttle: Any | None = None
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()
        return self._statement("cashflow_vip", period)

    async def balancesheet_vip(
        self, period: str, *, throttle: Any | None = None
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()
        return self._statement("balancesheet_vip", period)

    async def report_rc(
        self,
        *,
        start_date: str = "",
        end_date: str = "",
        throttle: Any | None = None,
    ) -> pd.DataFrame:
        if throttle is not None:
            await throttle()  # one page → one token (mirrors the real client)
        self.calls.append(("report_rc", end_date))
        if end_date in self._report_rc_empty:
            return pd.DataFrame()
        return self._report_rc.get(
            end_date,
            pd.DataFrame(
                {
                    "ts_code": ["600519.SH"],
                    "report_date": [end_date],
                    "org_name": ["中金"],
                    "quarter": ["2024Q4"],
                    "np": [10000.0],
                }
            ),
        )

    async def namechange(
        self, *, start_date: str = "", end_date: str = ""
    ) -> pd.DataFrame:
        self.calls.append(("namechange", end_date or start_date))
        year = int((start_date or end_date)[:4]) if (start_date or end_date) else 0
        # Default: only 2018 has a row; other years legitimately empty.
        if year in self._namechange_by_year:
            return self._namechange_by_year[year]
        if year == 2018:
            return pd.DataFrame(
                {
                    "ts_code": ["600519.SH"],
                    "name": ["*ST茅台"],
                    "start_date": ["20180115"],
                    "end_date": [""],
                    "change_reason": ["test"],
                }
            )
        return pd.DataFrame()

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
        # 3 complete months (the 4th, partial, dropped), all >= 2016 floor.
        calendar = ["20160105", "20160130", "20160227", "20160330", "20160402"]
        results = await ingest_index_weight(client, store, calendar, now=_now)
        assert [r.status for r in results] == ["ingested", "ingested", "ingested"]
        snap = store.latest(
            vendor="tushare", endpoint=EP_INDEX_WEIGHT, trade_date="20160130"
        )
        assert snap is not None
        assert snap.params == {
            "index_code": "000300.SH",
            "start_date": "20160101",
            "end_date": "20160130",
        }

    @pytest.mark.asyncio
    async def test_index_weight_skips_months_before_availability_floor(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        # 201511/201512 are below the 201601 floor → skipped (no snapshot, no fail).
        calendar = ["20151130", "20151231", "20160129", "20160229", "20160301"]
        results = await ingest_index_weight(client, store, calendar, now=_now)
        assert [r.key for r in results] == ["20160129", "20160229"]
        assert all(r.status == "ingested" for r in results)
        assert (
            store.latest(
                vendor="tushare", endpoint=EP_INDEX_WEIGHT, trade_date="20151130"
            )
            is None
        )

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
    async def test_load_survivorship_handles_float_inferred_delist_date(
        self, tmp_path: Path
    ) -> None:
        # A recently-delisted code still in the LISTED roster makes that roster's
        # delist_date column mostly-empty → a naive read_csv floatifies
        # '20260610' to '20260610.0' (which SurvivorshipUniverse rejects). The
        # dtype=str roster read must keep it a clean 8-digit string.
        store = SnapshotStore(tmp_path)
        listed = pd.DataFrame(
            {
                "ts_code": ["600519.SH", "688287.SH"],
                "name": ["贵州茅台", "退市观典"],
                "list_date": ["20010827", "20220525"],
                "delist_date": ["", "20260610"],
            }
        )
        client = _FakeRound2Client(listed=listed)
        await ingest_stock_basic(client, store, "20260618", now=_now)
        universe = load_survivorship(store, "20260618")  # must not raise
        assert "688287.SH" in universe.tradable_asof("20250101")
        assert "688287.SH" not in universe.tradable_asof("20260615")

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


# --- R3-1: financial statements + namechange --------------------------------


class TestRound3Statements:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", [EP_INCOME, EP_CASHFLOW, EP_BALANCESHEET])
    async def test_statement_persists_per_period(
        self, tmp_path: Path, endpoint: str
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        results = await ingest_statement(
            client, store, ["20240331", "20240630"], endpoint=endpoint, now=_now
        )
        assert [r.status for r in results] == ["ingested", "ingested"]
        assert (
            store.latest(vendor="tushare", endpoint=endpoint, trade_date="20240331")
            is not None
        )

    @pytest.mark.asyncio
    async def test_statement_byte_exact_checksum(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        frame = pd.DataFrame(
            {"ts_code": ["600519.SH"], "n_income": [1.0e9], "report_type": ["1"]}
        )
        client = _FakeRound2Client(statements={(EP_INCOME, "20240331"): frame})
        await ingest_statement(
            client, store, ["20240331"], endpoint=EP_INCOME, now=_now
        )
        snap = store.latest(vendor="tushare", endpoint=EP_INCOME, trade_date="20240331")
        assert snap is not None
        assert (
            snap.raw_payload_sha256
            == hashlib.sha256(canonical_csv_bytes(frame)).hexdigest()
        )

    @pytest.mark.asyncio
    async def test_statement_idempotent_resume(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_statement(
            client, store, ["20240331"], endpoint=EP_CASHFLOW, now=_now
        )
        n_calls = len(client.calls)
        second = await ingest_statement(
            client, store, ["20240331"], endpoint=EP_CASHFLOW, now=_now
        )
        assert second[0].status == "skipped"
        assert len(client.calls) == n_calls  # no re-fetch on resume

    @pytest.mark.asyncio
    async def test_statement_empty_required_fails_closed(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client(empty_statements={(EP_BALANCESHEET, "20240331")})
        results = await ingest_statement(
            client, store, ["20240331"], endpoint=EP_BALANCESHEET, now=_now
        )
        assert results[0].status == "failed"
        assert (
            store.latest(
                vendor="tushare", endpoint=EP_BALANCESHEET, trade_date="20240331"
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_reingest_version_bumps_on_changed_pull(self, tmp_path: Path) -> None:
        # The R3-1 truncation repair: a corrected (paginated) re-pull that returns
        # the dropped codes is appended as a NEW version; the truncated v1 bytes
        # are preserved (append-only) and store.latest returns the complete pull.
        store = SnapshotStore(tmp_path)
        truncated = pd.DataFrame(
            {"ts_code": ["1.SZ"], "end_date": ["20240331"], "report_type": ["1"]}
        )
        client = _FakeRound2Client(statements={(EP_CASHFLOW, "20240331"): truncated})
        first = await ingest_statement(
            client, store, ["20240331"], endpoint=EP_CASHFLOW, now=_now
        )
        assert first[0].status == "ingested"
        complete = pd.DataFrame(
            {
                "ts_code": ["1.SZ", "2.SZ"],
                "end_date": ["20240331", "20240331"],
                "report_type": ["1", "1"],
            }
        )
        client._statements[(EP_CASHFLOW, "20240331")] = complete
        second = await ingest_statement(
            client, store, ["20240331"], endpoint=EP_CASHFLOW, now=_now, reingest=True
        )
        assert second[0].status == "ingested"
        versions = store.versions(
            vendor="tushare", endpoint=EP_CASHFLOW, trade_date="20240331"
        )
        assert [v.version for v in versions] == [1, 2]  # v1 preserved, v2 appended
        latest = store.latest(
            vendor="tushare", endpoint=EP_CASHFLOW, trade_date="20240331"
        )
        assert latest is not None and latest.version == 2
        assert (
            latest.raw_payload_sha256
            == hashlib.sha256(canonical_csv_bytes(complete)).hexdigest()
        )

    @pytest.mark.asyncio
    async def test_reingest_unchanged_payload_is_skipped(self, tmp_path: Path) -> None:
        # An already-complete period whose re-pull is byte-identical is reported
        # SKIPPED — no spurious v2 churn.
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_statement(
            client, store, ["20240331"], endpoint=EP_INCOME, now=_now
        )
        second = await ingest_statement(
            client, store, ["20240331"], endpoint=EP_INCOME, now=_now, reingest=True
        )
        assert second[0].status == "skipped"
        versions = store.versions(
            vendor="tushare", endpoint=EP_INCOME, trade_date="20240331"
        )
        assert [v.version for v in versions] == [1]  # no restatement written

    @pytest.mark.asyncio
    async def test_round3_restate_skips_namechange_and_rebuilds_coverage(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)  # rosters
        calendar = ["20240105", "20240401"]  # spans the Q1 report-period end
        first = await ingest_round3(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20260618",
            now=_now,
        )
        assert first.failed == 0
        client.calls.clear()
        report = await ingest_round3(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20260618",
            now=_now,
            restate_statements=True,
        )
        assert report.failed == 0
        stmt_results = [
            r
            for r in report.results
            if r.endpoint in (EP_INCOME, EP_CASHFLOW, EP_BALANCESHEET)
        ]
        assert stmt_results and all(r.status == "skipped" for r in stmt_results)
        assert not any(c[0] == "namechange" for c in client.calls)  # skipped in restate

    @pytest.mark.asyncio
    async def test_statement_throttles_per_page_not_double(
        self, tmp_path: Path
    ) -> None:
        # The rate limiter is handed to the client (one token per real SDK page)
        # and _ingest_one does NOT also acquire — so a single-page period spends
        # exactly one token, never two (codex P2: no double / under throttling).
        class _CountingLimiter:
            def __init__(self) -> None:
                self.acquires = 0

            def acquire(self) -> None:
                self.acquires += 1

        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()  # fake spends one token per statement call
        limiter = _CountingLimiter()
        results = await ingest_statement(
            client,
            store,
            ["20240331", "20240630"],
            endpoint=EP_INCOME,
            now=_now,
            rate_limiter=limiter,
        )
        assert [r.status for r in results] == ["ingested", "ingested"]
        assert limiter.acquires == 2  # one per period; 4 would mean double-throttle


class TestNamechange:
    def test_namechange_years_inclusive_range(self) -> None:
        assert namechange_years(2015, "20180615") == [2015, 2016, 2017, 2018]

    def test_namechange_years_rejects_bad_asof(self) -> None:
        with pytest.raises(ValueError, match="YYYYMMDD"):
            namechange_years(2015, "2018")

    @pytest.mark.asyncio
    async def test_namechange_year_paged_empty_year_stored_not_failed(
        self, tmp_path: Path
    ) -> None:
        # 2017 returns empty (legit) → stored as a valid empty snapshot (not
        # FAILED); 2018 has a real row. Both keyed by the year-end.
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        results = await ingest_namechange(
            client, store, first_year=2017, asof="20181231", now=_now
        )
        assert [r.key for r in results] == ["20171231", "20181231"]
        assert all(r.status == "ingested" for r in results)  # empty year NOT failed
        snap2017 = store.latest(
            vendor="tushare", endpoint=EP_NAMECHANGE, trade_date="20171231"
        )
        assert snap2017 is not None and snap2017.metadata.get("rows") == 0
        snap2018 = store.latest(
            vendor="tushare", endpoint=EP_NAMECHANGE, trade_date="20181231"
        )
        assert snap2018 is not None and snap2018.metadata.get("rows") == 1

    @pytest.mark.asyncio
    async def test_namechange_idempotent_resume(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_namechange(
            client, store, first_year=2018, asof="20181231", now=_now
        )
        n_calls = len(client.calls)
        second = await ingest_namechange(
            client, store, first_year=2018, asof="20181231", now=_now
        )
        assert [r.status for r in second] == ["skipped"]
        assert len(client.calls) == n_calls

    def test_namechange_pages_current_year_keyed_by_asof(self) -> None:
        # codex P2: a mid-year asof must NOT key the current year YYYY1231 (that
        # stores a partial page under the final key and never refreshes). Past
        # years stay stable YYYY1231; the current year uses asof as end + key.
        pages = namechange_pages(2024, "20260618")
        assert pages == [
            ("20240101", "20241231", "20241231"),
            ("20250101", "20251231", "20251231"),
            ("20260101", "20260618", "20260618"),
        ]

    @pytest.mark.asyncio
    async def test_namechange_empty_year_snapshot_is_replayable(
        self, tmp_path: Path
    ) -> None:
        # codex P2: an empty no-change year must serialize WITH the namechange
        # header (not bare '\n'), so a PIT reader can replay it as a 0-row frame.
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()  # 2017 returns a zero-column empty frame
        await ingest_namechange(
            client, store, first_year=2017, asof="20171231", now=_now
        )
        snap = store.latest(
            vendor="tushare", endpoint=EP_NAMECHANGE, trade_date="20171231"
        )
        assert snap is not None
        frame = parse_csv_bytes(snap.raw_payload)  # must NOT raise EmptyDataError
        assert len(frame) == 0
        assert {"ts_code", "name", "start_date", "end_date"} <= set(frame.columns)


class TestRound3Orchestrator:
    @pytest.mark.asyncio
    async def test_full_run_builds_per_statement_coverage(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        # Rosters must exist first (statement coverage needs the survivorship set);
        # keyed by the SAME asof the round-3 run uses (real main passes one asof).
        await ingest_stock_basic(client, store, "20181231", now=_now)
        calendar = ["20240105", "20240131", "20240401"]  # spans Q1 end
        report = await ingest_round3(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20181231",  # namechange paged 2017..2018 (one real row in 2018)
            now=_now,
            namechange_first_year=2017,
        )
        assert report.failed == 0
        # 3 statements × 1 Q1 period + 2 namechange years (2017 empty, 2018 row).
        assert report.ingested == 3 + 2
        # One coverage manifest per (statement endpoint × period).
        assert len(report.fina_coverage) == len(STATEMENT_ENDPOINTS)
        for endpoint in STATEMENT_ENDPOINTS:
            assert (
                coverage_store.get(endpoint=endpoint, session_end="20240331")
                is not None
            )

    @pytest.mark.asyncio
    async def test_coverage_fails_closed_without_rosters(self, tmp_path: Path) -> None:
        # No L/D rosters ingested → survivorship universe unbuildable → the
        # coverage step must surface a FAILED result, not warn-and-pass.
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        calendar = ["20240105", "20240401"]
        report = await ingest_round3(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20181231",
            now=_now,
            namechange_first_year=2018,
        )
        assert report.failed == 1
        assert any(
            r.endpoint == "coverage" and r.status == "failed" for r in report.results
        )

    @pytest.mark.asyncio
    async def test_rerun_idempotent_no_coverage_duplication(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20181231", now=_now)
        calendar = ["20240105", "20240401"]
        kwargs = dict(
            calendar=calendar,
            first_year=2024,
            asof="20181231",
            now=_now,
            namechange_first_year=2018,
        )
        first = await ingest_round3(client, store, coverage_store, **kwargs)  # type: ignore[arg-type]
        assert first.failed == 0
        second = await ingest_round3(client, store, coverage_store, **kwargs)  # type: ignore[arg-type]
        assert second.ingested == 0  # all skipped on resume
        cov_lines = [
            ln
            for ln in (tmp_path / "coverage.jsonl").read_text().splitlines()
            if ln.strip()
        ]
        assert len(cov_lines) == len(STATEMENT_ENDPOINTS)  # no duplicate rows


# --- R4-2: report_rc analyst-forecast ingest --------------------------------


class TestRound4ReportRc:
    def test_month_ranges_weekend_inclusive_calendar_end(self) -> None:
        # Each month spans its full CALENDAR end (last day), not the last trade
        # date, so weekend-published reports are captured; key = end; the final
        # month is capped at last_date.
        ranges = report_rc_month_ranges(2024, "20240315")
        assert ranges == [
            ("20240101", "20240131", "20240131"),
            ("20240201", "20240229", "20240229"),  # leap-year Feb end
            ("20240301", "20240315", "20240315"),  # final month capped at last_date
        ]

    def test_month_ranges_first_year_floor_warms_window(self) -> None:
        # first_year is BEFORE the calendar/train_val start so the trailing window
        # is warm at the panel start.
        ranges = report_rc_month_ranges(2014, "20150115")
        assert ranges[0] == ("20140101", "20140131", "20140131")
        assert ranges[-1] == ("20150101", "20150115", "20150115")

    def test_month_ranges_rejects_bad_last_date(self) -> None:
        with pytest.raises(ValueError, match="YYYYMMDD"):
            report_rc_month_ranges(2024, "2024-03")

    @pytest.mark.asyncio
    async def test_report_rc_persists_per_month(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        results = await ingest_report_rc(
            client, store, first_year=2024, last_date="20240228", now=_now
        )
        assert [r.status for r in results] == ["ingested", "ingested"]
        snap = store.latest(
            vendor="tushare", endpoint=EP_REPORT_RC, trade_date="20240131"
        )
        assert snap is not None
        assert snap.params == {"start_date": "20240101", "end_date": "20240131"}

    @pytest.mark.asyncio
    async def test_report_rc_byte_exact_checksum(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        frame = pd.DataFrame(
            {"ts_code": ["600519.SH"], "np": [100.0], "report_date": ["20240131"]}
        )
        client = _FakeRound2Client(report_rc={"20240131": frame})
        await ingest_report_rc(
            client, store, first_year=2024, last_date="20240131", now=_now
        )
        snap = store.latest(
            vendor="tushare", endpoint=EP_REPORT_RC, trade_date="20240131"
        )
        assert snap is not None
        assert (
            snap.raw_payload_sha256
            == hashlib.sha256(canonical_csv_bytes(frame)).hexdigest()
        )

    @pytest.mark.asyncio
    async def test_report_rc_idempotent_resume(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_report_rc(
            client, store, first_year=2024, last_date="20240131", now=_now
        )
        n_calls = len(client.calls)
        second = await ingest_report_rc(
            client, store, first_year=2024, last_date="20240131", now=_now
        )
        assert second[0].status == "skipped"
        assert len(client.calls) == n_calls  # no re-fetch on resume

    @pytest.mark.asyncio
    async def test_report_rc_empty_month_fails_closed(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client(report_rc_empty={"20240131"})
        results = await ingest_report_rc(
            client, store, first_year=2024, last_date="20240131", now=_now
        )
        assert results[0].status == "failed"
        assert (
            store.latest(vendor="tushare", endpoint=EP_REPORT_RC, trade_date="20240131")
            is None
        )

    @pytest.mark.asyncio
    async def test_report_rc_throttles_per_month_not_double(
        self, tmp_path: Path
    ) -> None:
        class _CountingLimiter:
            def __init__(self) -> None:
                self.acquires = 0

            def acquire(self) -> None:
                self.acquires += 1

        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()  # fake spends one token per report_rc call
        limiter = _CountingLimiter()
        results = await ingest_report_rc(
            client,
            store,
            first_year=2024,
            last_date="20240228",
            now=_now,
            rate_limiter=limiter,
        )
        assert [r.status for r in results] == ["ingested", "ingested"]
        assert limiter.acquires == 2  # one per month; 4 would mean double-throttle

    @pytest.mark.asyncio
    async def test_ingest_round4_no_coverage_manifest(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        # calendar last = 20240301 → months 2024-01, 2024-02, 2024-03 (capped 0301).
        calendar = ["20240105", "20240131", "20240201", "20240228", "20240301"]
        report = await ingest_round4(
            client, store, calendar=calendar, now=_now, report_rc_first_year=2024
        )
        assert report.failed == 0
        assert report.fina_coverage == ()  # sparse stream → no coverage manifest
        assert report.ingested == 3

    @pytest.mark.asyncio
    async def test_ingest_round4_empty_calendar_rejected(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        with pytest.raises(ValueError, match="empty calendar"):
            await ingest_round4(client, store, calendar=[], now=_now)


# --- QGR-1: short-horizon microstructure / chips / tech / events / theme -----


class TestQgrFullMarketDaily:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", [EP_STK_LIMIT, EP_CYQ_PERF, EP_STK_FACTOR_PRO])
    async def test_persists_per_trade_date(self, tmp_path: Path, endpoint: str) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        results = await ingest_fullmarket_daily(
            client, store, ["20240105", "20240108"], endpoint=endpoint, now=_now
        )
        assert [r.status for r in results] == ["ingested", "ingested"]
        snap = store.latest(vendor="tushare", endpoint=endpoint, trade_date="20240105")
        assert snap is not None
        assert snap.params == {"trade_date": "20240105"}

    @pytest.mark.asyncio
    async def test_empty_required_fails_closed(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client(qgr_empty={(EP_STK_LIMIT, "20240105")})
        results = await ingest_fullmarket_daily(
            client, store, ["20240105"], endpoint=EP_STK_LIMIT, now=_now
        )
        assert results[0].status == "failed"
        assert (
            store.latest(vendor="tushare", endpoint=EP_STK_LIMIT, trade_date="20240105")
            is None
        )

    @pytest.mark.asyncio
    async def test_cyq_perf_skips_before_2018_floor(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        # 2017 day skipped (vendor floor 2018); 2018 day ingested.
        results = await ingest_fullmarket_daily(
            client,
            store,
            ["20170103", "20180102"],
            endpoint=EP_CYQ_PERF,
            now=_now,
            first_date=CYQ_PERF_FIRST_DATE,
        )
        assert [r.key for r in results] == ["20180102"]
        assert (
            store.latest(vendor="tushare", endpoint=EP_CYQ_PERF, trade_date="20170103")
            is None
        )

    @pytest.mark.asyncio
    async def test_throttle_one_token_per_day(self, tmp_path: Path) -> None:
        class _CountingLimiter:
            def __init__(self) -> None:
                self.acquires = 0

            def acquire(self) -> None:
                self.acquires += 1

        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()  # fake spends one token per (paginated) call
        limiter = _CountingLimiter()
        await ingest_fullmarket_daily(
            client,
            store,
            ["20240105", "20240108"],
            endpoint=EP_STK_LIMIT,
            now=_now,
            rate_limiter=limiter,
        )
        assert limiter.acquires == 2  # one per day; 4 would mean double-throttle

    @pytest.mark.asyncio
    async def test_idempotent_resume(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_fullmarket_daily(
            client, store, ["20240105"], endpoint=EP_STK_FACTOR_PRO, now=_now
        )
        n_calls = len(client.calls)
        second = await ingest_fullmarket_daily(
            client, store, ["20240105"], endpoint=EP_STK_FACTOR_PRO, now=_now
        )
        assert second[0].status == "skipped"
        assert len(client.calls) == n_calls  # no re-fetch on resume


class TestQgrSparseDaily:
    @pytest.mark.asyncio
    async def test_empty_day_stored_replayable_not_failed(self, tmp_path: Path) -> None:
        # A day with no suspend/resume events is legitimate → stored as an empty
        # frame with the canonical header (replayable), NOT failed.
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client(qgr_empty={(EP_SUSPEND_D, "20240105")})
        results = await ingest_sparse_daily(
            client, store, ["20240105"], endpoint=EP_SUSPEND_D, now=_now
        )
        assert results[0].status == "ingested"
        snap = store.latest(
            vendor="tushare", endpoint=EP_SUSPEND_D, trade_date="20240105"
        )
        assert snap is not None and snap.metadata.get("rows") == 0
        frame = parse_csv_bytes(snap.raw_payload)  # must NOT raise EmptyDataError
        assert len(frame) == 0
        assert set(SUSPEND_D_FIELDS) <= set(frame.columns)

    @pytest.mark.asyncio
    async def test_limit_list_d_empty_day_uses_its_own_header(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client(qgr_empty={(EP_LIMIT_LIST_D, "20240105")})
        await ingest_sparse_daily(
            client, store, ["20240105"], endpoint=EP_LIMIT_LIST_D, now=_now
        )
        snap = store.latest(
            vendor="tushare", endpoint=EP_LIMIT_LIST_D, trade_date="20240105"
        )
        assert snap is not None
        frame = parse_csv_bytes(snap.raw_payload)
        assert set(LIMIT_LIST_D_FIELDS) <= set(frame.columns)

    @pytest.mark.asyncio
    async def test_limit_list_d_skips_before_2020_floor(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        results = await ingest_sparse_daily(
            client,
            store,
            ["20190102", "20200102"],
            endpoint=EP_LIMIT_LIST_D,
            now=_now,
            first_date=LIMIT_LIST_D_FIRST_DATE,
        )
        assert [r.key for r in results] == ["20200102"]

    @pytest.mark.asyncio
    async def test_sparse_idempotent_resume(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_sparse_daily(
            client, store, ["20240105"], endpoint=EP_SUSPEND_D, now=_now
        )
        n_calls = len(client.calls)
        second = await ingest_sparse_daily(
            client, store, ["20240105"], endpoint=EP_SUSPEND_D, now=_now
        )
        assert second[0].status == "skipped"
        assert len(client.calls) == n_calls


class TestQgrEventStream:
    # ann_date month-range triples (start, end, key) like report_rc_month_ranges.
    _RANGES = [
        ("20240101", "20240131", "20240131"),
        ("20240201", "20240229", "20240229"),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", [EP_FORECAST, EP_EXPRESS])
    async def test_persists_per_ann_month_keyed_by_month_end(
        self, tmp_path: Path, endpoint: str
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        results = await ingest_event_stream(
            client,
            store,
            self._RANGES,
            endpoint=endpoint,
            now=_now,
            require_non_empty=EVENT_STREAM_REQUIRE_NON_EMPTY[endpoint],
            empty_columns=EXPRESS_VIP_FIELDS if endpoint == EP_EXPRESS else None,
        )
        assert [r.status for r in results] == ["ingested", "ingested"]
        snap = store.latest(vendor="tushare", endpoint=endpoint, trade_date="20240131")
        # Keyed by the ann_date month-end; params carry the ann_date window.
        assert snap is not None
        assert snap.params == {"start_date": "20240101", "end_date": "20240131"}

    @pytest.mark.asyncio
    async def test_forecast_empty_month_fails_closed(self, tmp_path: Path) -> None:
        # forecast_vip is never empty (require_non_empty=True) → an empty month is
        # corruption → FAILED, nothing stored.
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client(qgr_empty={(EP_FORECAST, "20240131")})
        results = await ingest_event_stream(
            client,
            store,
            self._RANGES[:1],
            endpoint=EP_FORECAST,
            now=_now,
            require_non_empty=True,
        )
        assert results[0].status == "failed"
        assert (
            store.latest(vendor="tushare", endpoint=EP_FORECAST, trade_date="20240131")
            is None
        )

    @pytest.mark.asyncio
    async def test_express_empty_month_stored_replayable(self, tmp_path: Path) -> None:
        # express_vip months are legitimately empty (require_non_empty=False) →
        # stored as a replayable empty frame with the canonical header.
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client(qgr_empty={(EP_EXPRESS, "20240131")})
        results = await ingest_event_stream(
            client,
            store,
            self._RANGES[:1],
            endpoint=EP_EXPRESS,
            now=_now,
            require_non_empty=False,
            empty_columns=EXPRESS_VIP_FIELDS,
        )
        assert results[0].status == "ingested"
        snap = store.latest(
            vendor="tushare", endpoint=EP_EXPRESS, trade_date="20240131"
        )
        assert snap is not None and snap.metadata.get("rows") == 0
        frame = parse_csv_bytes(snap.raw_payload)  # must NOT raise EmptyDataError
        assert len(frame) == 0
        assert set(EXPRESS_VIP_FIELDS) <= set(frame.columns)

    @pytest.mark.asyncio
    async def test_throttle_one_token_per_month(self, tmp_path: Path) -> None:
        class _CountingLimiter:
            def __init__(self) -> None:
                self.acquires = 0

            def acquire(self) -> None:
                self.acquires += 1

        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        limiter = _CountingLimiter()
        await ingest_event_stream(
            client,
            store,
            self._RANGES,
            endpoint=EP_FORECAST,
            now=_now,
            rate_limiter=limiter,
            require_non_empty=True,
        )
        assert limiter.acquires == 2


class TestQgrThemeCatalogs:
    @pytest.mark.asyncio
    async def test_ths_index_and_index_classify_keyed_by_asof(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        results = await ingest_theme_catalogs(client, store, "20260618", now=_now)
        assert [r.endpoint for r in results] == [EP_THS_INDEX, EP_INDEX_CLASSIFY]
        assert all(r.status == "ingested" for r in results)
        assert (
            store.latest(vendor="tushare", endpoint=EP_THS_INDEX, trade_date="20260618")
            is not None
        )
        classify = store.latest(
            vendor="tushare", endpoint=EP_INDEX_CLASSIFY, trade_date="20260618"
        )
        assert classify is not None and classify.params["src"] == "SW2021"


class TestQgrDailyCoverage:
    @pytest.mark.asyncio
    async def test_per_day_survivorship_coverage(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)
        await ingest_fullmarket_daily(
            client, store, ["20240105", "20240108"], endpoint=EP_STK_LIMIT, now=_now
        )
        universe = load_survivorship(store, "20260618")
        manifests = build_daily_coverage_manifests(
            store, ["20240105", "20240108"], universe, endpoint=EP_STK_LIMIT
        )
        assert len(manifests) == 2
        m = manifests[0]
        assert m.granularity == "daily"
        assert m.session_start == m.session_end == "20240105"
        # requested = tradable as-of the day; delivered = the snapshot ts_codes.
        assert "600519.SH" in m.requested_universe
        assert m.delivered_universe == ("600519.SH",)

    @pytest.mark.asyncio
    async def test_coverage_fails_closed_on_missing_snapshot(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)
        universe = load_survivorship(store, "20260618")
        with pytest.raises(FileNotFoundError, match="20240105"):
            build_daily_coverage_manifests(
                store, ["20240105"], universe, endpoint=EP_CYQ_PERF
            )

    @pytest.mark.asyncio
    async def test_coverage_skips_before_floor(self, tmp_path: Path) -> None:
        store = SnapshotStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)
        await ingest_fullmarket_daily(
            client,
            store,
            ["20180102"],
            endpoint=EP_CYQ_PERF,
            now=_now,
            first_date=CYQ_PERF_FIRST_DATE,
        )
        universe = load_survivorship(store, "20260618")
        # 2017 day below floor → not required (no snapshot, no FileNotFoundError).
        manifests = build_daily_coverage_manifests(
            store,
            ["20170103", "20180102"],
            universe,
            endpoint=EP_CYQ_PERF,
            first_date=CYQ_PERF_FIRST_DATE,
        )
        assert [m.session_end for m in manifests] == ["20180102"]


class TestQgrOrchestrator:
    @pytest.mark.asyncio
    async def test_full_run_builds_coverage_for_fullmarket_daily(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)  # rosters
        calendar = ["20240105", "20240131", "20240401"]  # spans Q1 end
        report = await ingest_qgr(
            client,
            store,
            coverage_store,
            calendar=calendar,
            first_year=2024,
            asof="20260618",
            now=_now,
        )
        assert report.failed == 0
        # 3 full-market daily × 3 days (9) + 2 sparse × 3 days (6) + 2 event
        # streams × 4 ann_date months (Jan/Feb/Mar/Apr-capped) (8) + 2 catalogs = 25.
        assert report.ingested == 25
        # Coverage = 3 full-market daily endpoints × 3 days.
        assert len(report.fina_coverage) == 9
        for endpoint in ("stk_limit", "cyq_perf", "stk_factor_pro"):
            assert (
                coverage_store.get(endpoint=endpoint, session_end="20240105")
                is not None
            )

    @pytest.mark.asyncio
    async def test_resume_idempotent_no_coverage_duplication(
        self, tmp_path: Path
    ) -> None:
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        await ingest_stock_basic(client, store, "20260618", now=_now)
        calendar = ["20240105", "20240401"]
        kwargs = dict(calendar=calendar, first_year=2024, asof="20260618", now=_now)
        first = await ingest_qgr(client, store, coverage_store, **kwargs)  # type: ignore[arg-type]
        assert first.failed == 0
        n_cov_first = len(
            [
                ln
                for ln in (tmp_path / "coverage.jsonl").read_text().splitlines()
                if ln.strip()
            ]
        )
        second = await ingest_qgr(client, store, coverage_store, **kwargs)  # type: ignore[arg-type]
        assert second.ingested == 0  # all skipped on resume
        n_cov_second = len(
            [
                ln
                for ln in (tmp_path / "coverage.jsonl").read_text().splitlines()
                if ln.strip()
            ]
        )
        assert n_cov_first == n_cov_second  # no duplicate coverage rows

    @pytest.mark.asyncio
    async def test_coverage_fails_closed_without_rosters(self, tmp_path: Path) -> None:
        # No L/D rosters → survivorship universe unbuildable → coverage surfaces a
        # FAILED result (never a silent pass).
        store = SnapshotStore(tmp_path)
        coverage_store = CoverageStore(tmp_path)
        client = _FakeRound2Client()
        report = await ingest_qgr(
            client,
            store,
            coverage_store,
            calendar=["20240105", "20240401"],
            first_year=2024,
            asof="20260618",
            now=_now,
        )
        assert report.failed == 1
        assert any(
            r.endpoint == "coverage" and r.status == "failed" for r in report.results
        )
