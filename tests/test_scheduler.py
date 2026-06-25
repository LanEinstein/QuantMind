"""Tests for DataScheduler (TDD RED -> GREEN)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.data.market_meta_provider import _parse_redis_quote
from backend.data.scheduler import (
    QUOTE_CACHE_KEY_PREFIX,
    QUOTE_CACHE_TTL_SECONDS,
    DataScheduler,
)
from backend.models.market import WatchlistMarketSnapshot


def _sample_snapshot(code: str = "600519") -> WatchlistMarketSnapshot:
    return WatchlistMarketSnapshot(
        code=code,
        name="贵州茅台",
        price=1800.0,
        open=1790.0,
        high=1810.0,
        low=1785.0,
        prev_close=1795.0,
        change_pct=0.28,
        volume=5_000_000.0,
        amount=9_000_000_000.0,
        turnover_rate=0.63,
        source="adata",
        snapshot_at=datetime(2026, 5, 12, 6, 0, tzinfo=UTC),
    )


@pytest.fixture()
def mock_deps() -> dict[str, AsyncMock]:
    """Create mocked dependencies for the scheduler."""
    return {
        "market_data": AsyncMock(),
        "news_crawler": AsyncMock(),
        "mongodb": AsyncMock(),
        "redis_client": AsyncMock(),
        "watchlist": AsyncMock(),
    }


@pytest.fixture()
def scheduler(mock_deps: dict[str, AsyncMock]) -> DataScheduler:
    return DataScheduler(
        market_data=mock_deps["market_data"],
        news_crawler=mock_deps["news_crawler"],
        mongodb=mock_deps["mongodb"],
        redis_client=mock_deps["redis_client"],
        watchlist=mock_deps["watchlist"],
        market_interval_seconds=30,
        news_interval_seconds=300,
    )


class TestDataScheduler:
    """Tests for DataScheduler start/stop and job behavior."""

    @pytest.mark.asyncio
    async def test_start_creates_scheduler(self, scheduler: DataScheduler) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_shuts_down(self, scheduler: DataScheduler) -> None:
        await scheduler.start()
        await scheduler.stop()
        assert scheduler._scheduler is None or not scheduler._scheduler.running

    @pytest.mark.asyncio
    async def test_market_job_skips_outside_trading(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=False
        ):
            await scheduler._run_market_job()
        mock_deps["market_data"].get_index_realtime.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_job_runs_during_trading(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_realtime.return_value = []
        mock_deps["watchlist"].list_stocks.return_value = []
        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            await scheduler._run_market_job()
        mock_deps["market_data"].get_index_realtime.assert_called_once()

    @pytest.mark.asyncio
    async def test_market_job_full_watchlist_snapshot(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        """C-003: full watchlist tick persists + caches per-stock snapshots."""
        mock_deps["market_data"].get_index_realtime.return_value = []
        mock_deps["watchlist"].list_stocks.return_value = [
            {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
            {"stock_code": "510300", "stock_name": "沪深300 ETF", "active": True},
        ]
        sample = [_sample_snapshot("600519"), _sample_snapshot("510300")]
        mock_deps["market_data"].get_watchlist_snapshot = AsyncMock(
            return_value=sample
        )
        mock_deps["mongodb"].save_watchlist_snapshot = AsyncMock(return_value=2)

        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            await scheduler._run_market_job()

        mock_deps["market_data"].get_watchlist_snapshot.assert_called_once()
        call_args = mock_deps["market_data"].get_watchlist_snapshot.call_args
        assert call_args.args[0] == ["600519", "510300"]
        mock_deps["mongodb"].save_watchlist_snapshot.assert_called_once_with(
            sample
        )
        # Per-stock Redis cache with TTL=120s
        assert mock_deps["redis_client"].set.call_count == 2
        call0 = mock_deps["redis_client"].set.call_args_list[0]
        assert call0.args[0] == f"{QUOTE_CACHE_KEY_PREFIX}600519"
        assert call0.kwargs.get("ex") == QUOTE_CACHE_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_market_job_skips_when_watchlist_empty(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        """Empty active watchlist must not call get_watchlist_snapshot."""
        mock_deps["market_data"].get_index_realtime.return_value = []
        mock_deps["watchlist"].list_stocks.return_value = []
        mock_deps["market_data"].get_watchlist_snapshot = AsyncMock()

        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            await scheduler._run_market_job()

        mock_deps["market_data"].get_watchlist_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_job_swallows_watchlist_fetch_failure(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        """Vendor brown-out must NOT crash the scheduler loop."""
        mock_deps["market_data"].get_index_realtime.return_value = []
        mock_deps["watchlist"].list_stocks.return_value = [
            {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
        ]
        mock_deps["market_data"].get_watchlist_snapshot = AsyncMock(
            side_effect=RuntimeError("vendor outage")
        )
        mock_deps["mongodb"].save_watchlist_snapshot = AsyncMock()

        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            await scheduler._run_market_job()

        mock_deps["mongodb"].save_watchlist_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_job_no_redis_no_cache(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=None,
            watchlist=mock_deps["watchlist"],
        )
        mock_deps["market_data"].get_index_realtime.return_value = []
        mock_deps["watchlist"].list_stocks.return_value = [
            {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
        ]
        sample = [_sample_snapshot("600519")]
        mock_deps["market_data"].get_watchlist_snapshot = AsyncMock(
            return_value=sample
        )
        mock_deps["mongodb"].save_watchlist_snapshot = AsyncMock(return_value=1)

        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            await s._run_market_job()

        mock_deps["mongodb"].save_watchlist_snapshot.assert_called_once()
        # No Redis client → no caching call should have been attempted
        mock_deps["redis_client"].set.assert_not_called()

    @pytest.mark.asyncio
    async def test_market_job_no_watchlist_wired_skips_snapshot(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            watchlist=None,
        )
        mock_deps["market_data"].get_index_realtime.return_value = []
        mock_deps["market_data"].get_watchlist_snapshot = AsyncMock()

        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            await s._run_market_job()

        mock_deps["market_data"].get_watchlist_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_news_job_runs(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["news_crawler"].fetch_latest_news.return_value = []
        await scheduler._run_news_job()
        mock_deps["news_crawler"].fetch_latest_news.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_exception_does_not_crash(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["news_crawler"].fetch_latest_news.side_effect = Exception("boom")
        # Should not raise
        await scheduler._run_news_job()


class TestActiveWatchlistCodesUnion:
    """Held-position union for the 30s collector.

    P0-8-amendment-2026-06-03-collect-held-positions: the collector must
    price held positions, not just the configured watchlist, so intraday
    MTM / the equity curve can mark every open position. A BUY fill does
    not add its code to the configured watchlist, so ``_active_watchlist_codes``
    unions the watchlist with a fail-open ``held_codes_provider`` callback —
    de-duplicated, order-preserving (watchlist first). Held-code read errors
    degrade to watchlist-only (infra fail-open) rather than crashing the tick.
    """

    def _make(
        self,
        mock_deps: dict[str, AsyncMock],
        held_provider: AsyncMock | None,
    ) -> DataScheduler:
        return DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            watchlist=mock_deps["watchlist"],
            held_codes_provider=held_provider,
        )

    @pytest.mark.asyncio
    async def test_union_dedup_order_preserving(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["watchlist"].list_stocks.return_value = [
            {"stock_code": "600519"},
            {"stock_code": "510300"},
        ]
        held = AsyncMock(return_value=["510300", "605111"])  # 510300 dup
        s = self._make(mock_deps, held)

        codes = await s._active_watchlist_codes()

        assert codes == ["600519", "510300", "605111"]
        held.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_exception_falls_back_to_watchlist_only(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["watchlist"].list_stocks.return_value = [
            {"stock_code": "600519"},
        ]
        held = AsyncMock(side_effect=RuntimeError("broker read boom"))
        s = self._make(mock_deps, held)
        s._log = MagicMock()

        codes = await s._active_watchlist_codes()

        # Degraded to watchlist-only — the tick did not crash.
        assert codes == ["600519"]
        s._log.warning.assert_called_once()
        assert s._log.warning.call_args.args[0] == "held_codes_provider_failed"

    @pytest.mark.asyncio
    async def test_provider_none_is_watchlist_only(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["watchlist"].list_stocks.return_value = [
            {"stock_code": "600519"},
            {"stock_code": "510300"},
        ]
        s = self._make(mock_deps, None)

        codes = await s._active_watchlist_codes()

        assert codes == ["600519", "510300"]

    @pytest.mark.asyncio
    async def test_provider_non_str_codes_skipped(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["watchlist"].list_stocks.return_value = [
            {"stock_code": "600519"},
        ]
        held = AsyncMock(return_value=["605111", None, 123, "600011"])
        s = self._make(mock_deps, held)

        codes = await s._active_watchlist_codes()

        assert codes == ["600519", "605111", "600011"]

    @pytest.mark.asyncio
    async def test_held_codes_collected_when_watchlist_unwired(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        # watchlist=None but provider set → held codes still get priced.
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            watchlist=None,
            held_codes_provider=AsyncMock(return_value=["605111"]),
        )

        codes = await s._active_watchlist_codes()

        assert codes == ["605111"]


class TestRedisQuoteProviderContract:
    """Producer↔consumer contract for the 30s quote cache.

    P0-8-amendment-2026-06-03-collect-held-positions §1.4: unioning held
    codes into the collection set only prices them if the cached quote is
    actually readable by ``MongoBackedMarketMetaProvider`` — whose
    ``_parse_redis_quote`` requires ``price`` + ``timestamp``. Earlier tests
    only asserted the union, never that the consumer can parse the blob; this
    round-trips the producer's payload through the real provider parser.
    """

    @pytest.mark.asyncio
    async def test_cached_blob_carries_timestamp_and_is_parseable(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        snap = _sample_snapshot("605111")

        await scheduler._cache_quotes_to_redis([snap])

        mock_deps["redis_client"].set.assert_called_once()
        key, payload = mock_deps["redis_client"].set.call_args.args[:2]
        assert key == f"{QUOTE_CACHE_KEY_PREFIX}605111"

        blob = json.loads(payload)
        # The provider's required field is mirrored from snapshot_at, and
        # the native field is preserved for any OHLC reader.
        assert blob["timestamp"] == blob["snapshot_at"]
        assert blob["price"] == snap.price

        # The real provider parser must accept the producer's blob (age 0).
        price = _parse_redis_quote(payload, snap.snapshot_at, 60)
        assert price == snap.price


class TestIndexCronJob:
    """Tests for the 15:30 CSI300 index price collection cron."""

    @pytest.mark.asyncio
    async def test_start_registers_index_job(
        self, scheduler: DataScheduler
    ) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        job = scheduler._scheduler.get_job("index_price_job")
        assert job is not None
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_index_job_fetches_and_persists(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_history.return_value = pd.DataFrame([
            {"date": "2026-04-23", "open": 3800.0, "high": 3850.0,
             "low": 3780.0, "close": 3830.0, "volume": 1_000_000},
            {"date": "2026-04-24", "open": 3830.0, "high": 3870.0,
             "low": 3820.0, "close": 3865.0, "volume": 1_200_000},
        ])
        mock_deps["mongodb"].save_index_prices.return_value = 2

        await scheduler._run_index_job()

        mock_deps["market_data"].get_index_history.assert_called_once()
        mock_deps["mongodb"].save_index_prices.assert_called_once()
        args = mock_deps["mongodb"].save_index_prices.call_args
        assert args[0][0] == "000300"
        persisted = args[0][1]
        assert len(persisted) == 2
        assert persisted[0]["date"] == "2026-04-23"
        assert persisted[0]["close"] == 3830.0

    @pytest.mark.asyncio
    async def test_index_job_skips_empty_frame(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_history.return_value = pd.DataFrame()

        await scheduler._run_index_job()

        mock_deps["mongodb"].save_index_prices.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_job_exception_does_not_crash(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_history.side_effect = RuntimeError("boom")
        # Should not raise
        await scheduler._run_index_job()


class TestCostFlushCronJob:
    """Tests for the 23:00 LLM cost Redis→MongoDB flush cron."""

    @pytest.mark.asyncio
    async def test_start_registers_cost_flush_job(
        self, scheduler: DataScheduler
    ) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        job = scheduler._scheduler.get_job("cost_flush_job")
        assert job is not None
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_cost_flush_job_calls_flush(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        with patch(
            "backend.data.scheduler.flush_to_mongodb",
            new=AsyncMock(return_value=5),
        ) as flush_mock:
            await scheduler._run_cost_flush_job()

        flush_mock.assert_called_once()
        kwargs_or_args = flush_mock.call_args
        # Verify called with redis client and mongodb
        assert kwargs_or_args is not None

    @pytest.mark.asyncio
    async def test_cost_flush_skips_without_redis(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=None,
        )
        with patch(
            "backend.data.scheduler.flush_to_mongodb",
            new=AsyncMock(return_value=0),
        ) as flush_mock:
            await s._run_cost_flush_job()

        flush_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_cost_flush_exception_does_not_crash(
        self, scheduler: DataScheduler
    ) -> None:
        with patch(
            "backend.data.scheduler.flush_to_mongodb",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            # Should not raise
            await scheduler._run_cost_flush_job()


class TestCctvNewsCronJob:
    """C-005 (codex cycle 1 P1 + cycle 4 P3): CCTV cron at the locked
    Asia/Shanghai checkpoints 09:00 / 15:30 / 20:00."""

    @pytest.mark.asyncio
    async def test_cctv_cron_registered_at_all_three_checkpoints(
        self, scheduler: DataScheduler
    ) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        for suffix in ("0900", "1530", "2000"):
            assert (
                scheduler._scheduler.get_job(f"cctv_news_job_{suffix}")
                is not None
            )
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_cctv_job_fetches_and_persists(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["news_crawler"].fetch_cctv = AsyncMock(
            return_value=["dummy"]
        )
        mock_deps["mongodb"].save_news = AsyncMock(return_value=1)
        await scheduler._run_cctv_news_job()
        mock_deps["news_crawler"].fetch_cctv.assert_called_once()
        mock_deps["mongodb"].save_news.assert_called_once()

    @pytest.mark.asyncio
    async def test_cctv_job_exception_does_not_crash(
        self, scheduler: DataScheduler, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["news_crawler"].fetch_cctv = AsyncMock(
            side_effect=RuntimeError("net")
        )
        # Should not raise.
        await scheduler._run_cctv_news_job()


class TestMiroFishEodCronJob:
    """C-006: 17:00 mon-fri MiroFish EOD review cron wiring."""

    @pytest.mark.asyncio
    async def test_eod_cron_registered_when_writer_present(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        writer = AsyncMock()
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            watchlist=mock_deps["watchlist"],
            mirofish_writer=writer,
        )
        await s.start()
        assert s._scheduler is not None
        job = s._scheduler.get_job("mirofish_eod_review_job")
        assert job is not None
        await s.stop()

    @pytest.mark.asyncio
    async def test_eod_cron_not_registered_when_writer_absent(
        self, scheduler: DataScheduler
    ) -> None:
        await scheduler.start()
        assert scheduler._scheduler is not None
        # No writer was injected → cron must not be registered (dev-env
        # parity: scheduler still runs, EOD becomes a no-op).
        assert scheduler._scheduler.get_job("mirofish_eod_review_job") is None
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_eod_job_writes_evidence(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        writer = AsyncMock()
        writer.write.return_value = True
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            mirofish_writer=writer,
        )
        await s._run_mirofish_eod_job()
        writer.write.assert_called_once()
        evidence_arg = writer.write.call_args.args[0]
        assert evidence_arg.path == "eod_review"
        assert evidence_arg.evidence_id.startswith("MIROFISH-EOD-")

    @pytest.mark.asyncio
    async def test_eod_job_no_writer_is_noop(
        self, scheduler: DataScheduler
    ) -> None:
        # Should not raise even without a writer.
        await scheduler._run_mirofish_eod_job()

    @pytest.mark.asyncio
    async def test_eod_pipeline_supersedes_legacy_write(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        # O-002: with the full pipeline injected, the 17:00 job delegates
        # to it and the legacy minimal EOD write must NOT also run.
        writer = AsyncMock()
        pipeline = AsyncMock()
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            mirofish_writer=writer,
            eod_pipeline=pipeline,
        )
        await s._run_mirofish_eod_job()
        pipeline.assert_awaited_once()
        writer.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_eod_pipeline_failure_swallowed(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        pipeline = AsyncMock(side_effect=RuntimeError("pipeline down"))
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            eod_pipeline=pipeline,
        )
        # Must not raise — the cron job survives a pipeline crash.
        await s._run_mirofish_eod_job()

    @pytest.mark.asyncio
    async def test_eod_cron_registered_with_pipeline_only(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            eod_pipeline=AsyncMock(),
        )
        await s.start()
        assert s._scheduler is not None
        assert s._scheduler.get_job("mirofish_eod_review_job") is not None
        await s.stop()

    @pytest.mark.asyncio
    async def test_eod_job_swallows_writer_failure(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        writer = AsyncMock()
        writer.write.side_effect = RuntimeError("boom")
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            mirofish_writer=writer,
        )
        # Should not raise — failure is logged at warning level.
        await s._run_mirofish_eod_job()


class TestMarketJobFetchTimeout:
    """S2: a hung vendor socket must NOT wedge the 30s market job.

    Without ``asyncio.wait_for`` a blocking adata/akshare call pins the job
    forever and APScheduler's ``max_instances=1`` then drops every later tick.
    """

    @pytest.mark.asyncio
    async def test_hung_index_fetch_times_out_and_returns(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        async def _hang(*_a: object, **_k: object) -> list[object]:
            await asyncio.sleep(30)
            return []

        mock_deps["market_data"].get_index_realtime = _hang
        mock_deps["market_data"].get_watchlist_snapshot = AsyncMock(return_value=[])
        mock_deps["watchlist"].list_stocks = AsyncMock(return_value=[])
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            watchlist=mock_deps["watchlist"],
            tick_timeout_seconds=0.05,
        )
        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            # If the timeout regressed this awaits 30s and the 2s guard fires.
            await asyncio.wait_for(s._run_market_job(), timeout=2.0)
        # The index persist never happened (fetch timed out, returned early).
        mock_deps["mongodb"].save_market_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_hung_watchlist_fetch_times_out_and_returns(
        self, mock_deps: dict[str, AsyncMock]
    ) -> None:
        mock_deps["market_data"].get_index_realtime = AsyncMock(return_value=[])

        async def _hang(*_a: object, **_k: object) -> list[object]:
            await asyncio.sleep(30)
            return []

        mock_deps["market_data"].get_watchlist_snapshot = _hang
        mock_deps["watchlist"].list_stocks = AsyncMock(
            return_value=[{"stock_code": "600519"}]
        )
        s = DataScheduler(
            market_data=mock_deps["market_data"],
            news_crawler=mock_deps["news_crawler"],
            mongodb=mock_deps["mongodb"],
            redis_client=mock_deps["redis_client"],
            watchlist=mock_deps["watchlist"],
            tick_timeout_seconds=0.05,
        )
        with patch(
            "backend.data.scheduler.is_trading_hours", return_value=True
        ):
            await asyncio.wait_for(s._run_market_job(), timeout=2.0)
        mock_deps["mongodb"].save_watchlist_snapshot.assert_not_called()


class TestIntervalMisfireGrace:
    """S6: the 30s/news interval jobs carry explicit misfire grace.

    APScheduler's default ``misfire_grace_time=1`` silently drops any tick that
    is >1s late behind an event-loop stall.
    """

    @pytest.mark.asyncio
    async def test_interval_jobs_have_explicit_grace(
        self, scheduler: DataScheduler
    ) -> None:
        from backend.data.scheduler import (
            MARKET_MISFIRE_GRACE_SECONDS,
            NEWS_MISFIRE_GRACE_SECONDS,
        )

        await scheduler.start()
        try:
            market = scheduler._scheduler.get_job("market_data_job")
            news = scheduler._scheduler.get_job("news_job")
            assert market is not None
            assert news is not None
            assert market.misfire_grace_time == MARKET_MISFIRE_GRACE_SECONDS
            assert news.misfire_grace_time == NEWS_MISFIRE_GRACE_SECONDS
        finally:
            await scheduler.stop()
