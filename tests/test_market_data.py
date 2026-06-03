"""Tests for MarketDataService (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pandas as pd
import pytest

from backend.data.config import DataSourcesConfig, load_data_sources_config
from backend.data.market_data import DataFetchError, MarketDataService
from backend.models.market import (
    CapitalFlowData,
    IndexQuote,
    SectorQuote,
    StockOrderbook,
    StockQuote,
    WatchlistMarketSnapshot,
)

VALID_CONFIG_YAML = """\
market_data:
  primary: adata
  fallback: akshare
  refresh_interval_seconds: 30
history_data:
  primary: adata
  fallback: baostock
  default_period: 1y
news:
  refresh_interval_seconds: 300
  max_articles_per_fetch: 50
  importance_threshold: 5
"""


@pytest.fixture()
def config(tmp_path: pytest.TempPathFactory) -> DataSourcesConfig:
    path = tmp_path / "ds.yaml"  # type: ignore[operator]
    path.write_text(VALID_CONFIG_YAML, encoding="utf-8")
    return load_data_sources_config(path)


@pytest.fixture()
def service(config: DataSourcesConfig) -> MarketDataService:
    return MarketDataService(config)


# -- Helpers: sample DataFrames matching adata output --


def _adata_index_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "index_code": "000001",
                "trade_time": "2026-03-22 10:30:00",
                "trade_date": "2026-03-22",
                "open": 3100.0,
                "high": 3160.0,
                "low": 3090.0,
                "price": 3150.5,
                "volume": 3_500_000_000,
                "amount": 450_000_000_000,
                "change": 26.5,
                "change_pct": 0.85,
            }
        ]
    )


def _adata_stock_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "stock_code": "600519",
                "short_name": "贵州茅台",
                "price": 1800.0,
                "change": 5.0,
                "change_pct": 0.28,
                "volume": 5_000_000,
                "amount": 9_000_000_000,
            }
        ]
    )


def _akshare_spot_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "最新价": 1800.0,
                "今开": 1790.0,
                "最高": 1810.0,
                "最低": 1785.0,
                "昨收": 1795.0,
                "涨跌幅": 0.28,
                "成交量": 5_000_000,
                "成交额": 9_000_000_000,
                "换手率": 0.63,
            }
        ]
    )


def _tushare_sina_df() -> pd.DataFrame:
    """Tushare ``ts.realtime_quote(src='sina')`` row shape (P0-8-amendment-
    2026-05-28). Used by :class:`TestGetStockRealtimeDual` — the fallback
    leg is now Tushare sina, not the eastmoney-throttled akshare batch.
    The price matches ``_adata_stock_df`` so the divergence check stays
    happy across both legs in the dual-source test."""
    return pd.DataFrame(
        [
            {
                "NAME": "贵州茅台",
                "TS_CODE": "600519.SH",
                "DATE": "20260528",
                "TIME": "10:13:57",
                "OPEN": 1790.0,
                "PRE_CLOSE": 1795.0,
                "PRICE": 1800.0,
                "HIGH": 1810.0,
                "LOW": 1785.0,
                "BID": 1799.99,
                "ASK": 1800.0,
                "VOLUME": 5_000_000,
                "AMOUNT": 9_000_000_000.0,
                "A1_P": 1800.0,
                "B1_P": 1799.99,
            }
        ]
    )


def _akshare_board_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "板块名称": "白酒",
                "涨跌幅": 2.15,
                "领涨股票代码": "600519",
                "领涨股票": "贵州茅台",
                "领涨涨跌幅": 3.50,
            }
        ]
    )


class TestGetIndexRealtime:
    """Tests for get_index_realtime."""

    @pytest.mark.asyncio
    async def test_primary_success(self, service: MarketDataService) -> None:
        with patch(
            "backend.data.market_data._fetch_index_adata",
            return_value=_adata_index_df(),
        ):
            result = await service.get_index_realtime(["000001"])
        assert len(result) == 1
        assert isinstance(result[0], IndexQuote)
        assert result[0].code == "000001"
        assert result[0].price == 3150.5

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(
        self, service: MarketDataService
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_index_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_index_akshare",
                return_value=_adata_index_df(),
            ),
        ):
            result = await service.get_index_realtime(["000001"])
        assert len(result) == 1
        assert result[0].code == "000001"

    @pytest.mark.asyncio
    async def test_both_fail_raises(self, service: MarketDataService) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_index_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_index_akshare",
                side_effect=Exception("akshare down"),
            ),
        ):
            with pytest.raises(DataFetchError):
                await service.get_index_realtime(["000001"])

    @pytest.mark.asyncio
    async def test_empty_dataframe(self, service: MarketDataService) -> None:
        with patch(
            "backend.data.market_data._fetch_index_adata",
            return_value=pd.DataFrame(),
        ):
            result = await service.get_index_realtime(["000001"])
        assert result == []


class TestGetStockRealtime:
    """Tests for get_stock_realtime."""

    @pytest.mark.asyncio
    async def test_primary_success(self, service: MarketDataService) -> None:
        with patch(
            "backend.data.market_data._fetch_stock_adata",
            return_value=_adata_stock_df(),
        ):
            result = await service.get_stock_realtime("600519")
        assert isinstance(result, StockQuote)
        assert result.code == "600519"
        assert result.name == "贵州茅台"

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self, service: MarketDataService) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_stock_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_stock_akshare",
                return_value=_akshare_spot_df(),
            ),
        ):
            result = await service.get_stock_realtime("600519")
        assert result.code == "600519"


class TestGetStockListRealtime:
    """Tests for get_stock_list_realtime."""

    @pytest.mark.asyncio
    async def test_returns_list(self, service: MarketDataService) -> None:
        two_stocks = pd.concat(
            [_adata_stock_df(), _adata_stock_df()], ignore_index=True
        )
        two_stocks.iloc[1, two_stocks.columns.get_loc("stock_code")] = "000001"
        two_stocks.iloc[1, two_stocks.columns.get_loc("short_name")] = "平安银行"
        with patch(
            "backend.data.market_data._fetch_stock_list_adata",
            return_value=two_stocks,
        ):
            result = await service.get_stock_list_realtime(["600519", "000001"])
        assert len(result) == 2
        assert all(isinstance(q, StockQuote) for q in result)


class TestGetWatchlistSnapshot:
    """C-003 watchlist 30s snapshot — Tushare-Sina primary, adata fallback
    (P0-8-amendment-2026-06-03; the dead eastmoney akshare batch leg removed)."""

    @pytest.fixture
    def snap_at(self) -> datetime:
        return datetime(2026, 5, 12, 6, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_empty_codes_short_circuits(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        with patch(
            "backend.data.market_data._fetch_stock_list_tushare_sina"
        ) as sina_mock:
            result = await service.get_watchlist_snapshot([], snap_at)
        assert result == []
        sina_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_primary_sina_tags_source(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        with patch(
            "backend.data.market_data._fetch_stock_list_tushare_sina",
            return_value=_tushare_sina_df(),
        ):
            result = await service.get_watchlist_snapshot(["600519"], snap_at)
        assert len(result) == 1
        assert isinstance(result[0], WatchlistMarketSnapshot)
        assert result[0].code == "600519"
        assert result[0].source == "tushare_sina"
        assert result[0].price == 1800.0
        # sina carries full OHLC/prev_close (adata batch only gives price)
        assert result[0].prev_close == 1795.0
        assert result[0].open == 1790.0
        assert result[0].snapshot_at == snap_at

    @pytest.mark.asyncio
    async def test_sina_exception_falls_back_to_adata(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_stock_list_tushare_sina",
                side_effect=RuntimeError("sina down"),
            ),
            patch(
                "backend.data.market_data._fetch_stock_list_adata",
                return_value=_adata_stock_df(),
            ),
        ):
            result = await service.get_watchlist_snapshot(["600519"], snap_at)
        assert len(result) == 1
        assert result[0].code == "600519"
        assert result[0].source == "adata"

    @pytest.mark.asyncio
    async def test_empty_sina_frame_falls_back_to_adata(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        """Empty primary frame during trading hours must fall back to adata."""
        with (
            patch(
                "backend.data.market_data._fetch_stock_list_tushare_sina",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.market_data._fetch_stock_list_adata",
                return_value=_adata_stock_df(),
            ),
        ):
            result = await service.get_watchlist_snapshot(["600519"], snap_at)
        assert len(result) == 1
        assert result[0].source == "adata"

    @pytest.mark.asyncio
    async def test_halted_row_skipped_not_batch_fatal(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        # A non-positive-PRICE sina row (halted symbol) is skipped per-row;
        # the healthy row still yields a snapshot (fail-closed for the one
        # code, not the whole batch). Primary stays tushare_sina.
        df = pd.DataFrame(
            [
                _tushare_sina_df().iloc[0].to_dict(),  # 600519 healthy
                {
                    "TS_CODE": "000001.SZ",
                    "NAME": "平安银行",
                    "PRICE": 0.0,  # halted / pre-open → skipped
                    "OPEN": 0.0,
                    "HIGH": 0.0,
                    "LOW": 0.0,
                    "PRE_CLOSE": 12.0,
                    "VOLUME": 0,
                    "AMOUNT": 0.0,
                },
            ]
        )
        with patch(
            "backend.data.market_data._fetch_stock_list_tushare_sina",
            return_value=df,
        ):
            result = await service.get_watchlist_snapshot(
                ["600519", "000001"], snap_at
            )
        assert {r.code for r in result} == {"600519"}
        assert result[0].source == "tushare_sina"

    @pytest.mark.asyncio
    async def test_both_legs_fail_raises(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_stock_list_tushare_sina",
                side_effect=RuntimeError("sina down"),
            ),
            patch(
                "backend.data.market_data._fetch_stock_list_adata",
                side_effect=RuntimeError("adata down"),
            ),
        ):
            with pytest.raises(DataFetchError):
                await service.get_watchlist_snapshot(["600519"], snap_at)

    @pytest.mark.asyncio
    async def test_both_legs_empty_returns_empty_no_raise(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_stock_list_tushare_sina",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.market_data._fetch_stock_list_adata",
                return_value=pd.DataFrame(),
            ),
        ):
            result = await service.get_watchlist_snapshot(["600519"], snap_at)
        assert result == []


class TestFetchStockListTushareSina:
    """P0-8-amendment-2026-06-03 — the batched sina primary fetch."""

    def test_batches_and_skips_unmappable_codes(self) -> None:
        # 688981 = STAR board → _to_tushare_ts_code raises ForbiddenCodeError;
        # it must be SKIPPED (not fail the batch), and the valid codes still
        # fetched with correct ts_code suffixes.
        seen: dict[str, str] = {}

        def _fake_rt(ts_code: str, src: str) -> pd.DataFrame:
            seen["ts_code"] = ts_code
            seen["src"] = src
            return _tushare_sina_df()

        with patch("tushare.realtime_quote", side_effect=_fake_rt):
            from backend.data.market_data import _fetch_stock_list_tushare_sina

            df = _fetch_stock_list_tushare_sina(["600519", "688981", "000001"])

        assert seen["src"] == "sina"
        # forbidden STAR code dropped; valid codes mapped to ts_codes
        assert "688981" not in seen["ts_code"]
        assert "600519.SH" in seen["ts_code"]
        assert "000001.SZ" in seen["ts_code"]
        assert not df.empty

    def test_all_unmappable_returns_empty_without_sdk_call(self) -> None:
        with patch("tushare.realtime_quote") as rt_mock:
            from backend.data.market_data import _fetch_stock_list_tushare_sina

            df = _fetch_stock_list_tushare_sina(["688981"])  # STAR → forbidden
        assert df.empty
        rt_mock.assert_not_called()


class TestGetSectorOverview:
    """Tests for get_sector_overview."""

    @pytest.mark.asyncio
    async def test_returns_sectors(self, service: MarketDataService) -> None:
        with patch(
            "backend.data.market_data._fetch_sectors_akshare",
            return_value=_akshare_board_df(),
        ):
            result = await service.get_sector_overview()
        assert len(result) == 1
        assert isinstance(result[0], SectorQuote)
        assert result[0].name == "白酒"


def _adata_five_df() -> pd.DataFrame:
    """adata get_market_five shape: s1..s5 (ask), b1..b5 (bid); no last."""
    return pd.DataFrame(
        [
            {
                "stock_code": "600519",
                "short_name": "贵州茅台",
                "s5": 1805.0,
                "sv5": 100,
                "s4": 1804.0,
                "sv4": 200,
                "s3": 1803.0,
                "sv3": 300,
                "s2": 1802.0,
                "sv2": 400,
                "s1": 1801.0,
                "sv1": 500,
                "b1": 1800.0,
                "bv1": 600,
                "b2": 1799.0,
                "bv2": 700,
                "b3": 1798.0,
                "bv3": 800,
                "b4": 1797.0,
                "bv4": 900,
                "b5": 1796.0,
                "bv5": 1000,
            }
        ]
    )


def _akshare_bidask_df() -> pd.DataFrame:
    """akshare stock_bid_ask_em long [item, value] shape."""
    return pd.DataFrame(
        [
            {"item": "sell_5", "value": 1805.0},
            {"item": "sell_1", "value": 1801.0},
            {"item": "buy_1", "value": 1800.0},
            {"item": "最新", "value": 1800.5},
        ]
    )


class TestGetStockOrderbook:
    """U-E2 (a): five-level orderbook fetch for the price-cage best_ask."""

    @pytest.mark.asyncio
    async def test_adata_primary_success(self, service: MarketDataService) -> None:
        with patch(
            "backend.data.market_data._fetch_orderbook_adata",
            return_value=_adata_five_df(),
        ):
            ob = await service.get_stock_orderbook("600519")
        assert isinstance(ob, StockOrderbook)
        assert ob.code == "600519"
        assert ob.best_ask == 1801.0  # s1
        assert ob.best_bid == 1800.0  # b1
        assert ob.last is None  # adata five has no last print
        assert ob.source == "adata"

    @pytest.mark.asyncio
    async def test_akshare_fallback_on_primary_failure(
        self, service: MarketDataService
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_orderbook_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_orderbook_akshare",
                return_value=_akshare_bidask_df(),
            ),
        ):
            ob = await service.get_stock_orderbook("600519")
        assert ob.best_ask == 1801.0  # sell_1
        assert ob.best_bid == 1800.0  # buy_1
        assert ob.last == 1800.5  # 最新
        assert ob.source == "akshare"

    @pytest.mark.asyncio
    async def test_empty_adata_falls_through_to_akshare(
        self, service: MarketDataService
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_orderbook_adata",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.market_data._fetch_orderbook_akshare",
                return_value=_akshare_bidask_df(),
            ),
        ):
            ob = await service.get_stock_orderbook("600519")
        assert ob.source == "akshare"
        assert ob.best_ask == 1801.0

    @pytest.mark.asyncio
    async def test_both_fail_raises(self, service: MarketDataService) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_orderbook_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_orderbook_akshare",
                side_effect=Exception("akshare down"),
            ),
        ):
            with pytest.raises(DataFetchError):
                await service.get_stock_orderbook("600519")

    @pytest.mark.asyncio
    async def test_inf_ask_falls_through_to_fallback(
        self, service: MarketDataService
    ) -> None:
        # A pandas inf cell for 卖一 must be treated as missing (→ fallback),
        # never surfaced as a price (it would suppress the fallback then crash
        # the cage). _positive_or_none maps inf → None → primary failure.
        inf_ask = _adata_five_df()
        inf_ask.iloc[0, inf_ask.columns.get_loc("s1")] = float("inf")
        with (
            patch(
                "backend.data.market_data._fetch_orderbook_adata",
                return_value=inf_ask,
            ),
            patch(
                "backend.data.market_data._fetch_orderbook_akshare",
                return_value=_akshare_bidask_df(),
            ),
        ):
            ob = await service.get_stock_orderbook("600519")
        assert ob.source == "akshare"
        assert ob.best_ask == 1801.0

    @pytest.mark.asyncio
    async def test_akshare_column_drift_raises_datafetch(
        self, service: MarketDataService
    ) -> None:
        # A non-empty akshare frame without item/value (vendor schema drift)
        # raises the documented DataFetchError, not a raw KeyError.
        drifted = pd.DataFrame([{"col_a": 1, "col_b": 2}])
        with (
            patch(
                "backend.data.market_data._fetch_orderbook_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_orderbook_akshare",
                return_value=drifted,
            ),
        ):
            with pytest.raises(DataFetchError):
                await service.get_stock_orderbook("600519")

    @pytest.mark.asyncio
    async def test_missing_ask_falls_through_to_fallback(
        self, service: MarketDataService
    ) -> None:
        # An adata book with a zero/blank 卖一 is a primary failure → akshare.
        no_ask = _adata_five_df()
        no_ask.iloc[0, no_ask.columns.get_loc("s1")] = 0.0
        with (
            patch(
                "backend.data.market_data._fetch_orderbook_adata",
                return_value=no_ask,
            ),
            patch(
                "backend.data.market_data._fetch_orderbook_akshare",
                return_value=_akshare_bidask_df(),
            ),
        ):
            ob = await service.get_stock_orderbook("600519")
        assert ob.source == "akshare"
        assert ob.best_ask == 1801.0


class TestGetStockRealtimeDual:
    """U-E2 (b) + P0-8-amendment-2026-05-28: dual-source last
    (adata primary + Tushare sina fallback) for divergence/staleness.

    Returns a positional ``(primary, fallback)`` tuple — primary is always
    the adata leg, fallback is now the **Tushare sina** leg (replaced the
    eastmoney-throttled akshare batch leg in 5-28 amendment). Each leg is
    ``None`` if it failed. The provider runs ``evaluate_divergence`` +
    staleness over the pair.
    """

    @pytest.mark.asyncio
    async def test_both_legs_returned(self, service: MarketDataService) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_stock_adata",
                return_value=_adata_stock_df(),
            ),
            patch(
                "backend.data.market_data._fetch_stock_tushare_sina",
                return_value=_tushare_sina_df(),
            ),
        ):
            primary, fallback = await service.get_stock_realtime_dual("600519")
        assert primary is not None and isinstance(primary, StockQuote)
        assert fallback is not None and isinstance(fallback, StockQuote)
        assert primary.price == 1800.0
        assert fallback.price == 1800.0

    @pytest.mark.asyncio
    async def test_fallback_leg_none_on_failure(
        self, service: MarketDataService
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_stock_adata",
                return_value=_adata_stock_df(),
            ),
            patch(
                "backend.data.market_data._fetch_stock_tushare_sina",
                side_effect=Exception("sina down"),
            ),
        ):
            primary, fallback = await service.get_stock_realtime_dual("600519")
        assert primary is not None
        assert fallback is None  # single-source view degraded upstream

    @pytest.mark.asyncio
    async def test_primary_leg_none_on_failure(
        self, service: MarketDataService
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_stock_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_stock_tushare_sina",
                return_value=_tushare_sina_df(),
            ),
        ):
            primary, fallback = await service.get_stock_realtime_dual("600519")
        assert primary is None
        assert fallback is not None


class TestGetCapitalFlow:
    """Tests for get_capital_flow."""

    @pytest.mark.asyncio
    async def test_returns_flow(self, service: MarketDataService) -> None:
        flow_df = pd.DataFrame(
            [{"trade_date": "2026-03-22", "north_money": 3_200_000_000}]
        )
        with patch(
            "backend.data.market_data._fetch_capital_flow_akshare",
            return_value=flow_df,
        ):
            result = await service.get_capital_flow()
        assert isinstance(result, CapitalFlowData)
        assert result.north_net_inflow == 3_200_000_000.0
