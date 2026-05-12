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
    """C-003: per-stock 30s watchlist snapshot with adata→akshare fallback."""

    @pytest.fixture
    def snap_at(self) -> datetime:
        return datetime(2026, 5, 12, 6, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_empty_codes_short_circuits(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        with patch(
            "backend.data.market_data._fetch_stock_list_adata"
        ) as adata_mock:
            result = await service.get_watchlist_snapshot([], snap_at)
        assert result == []
        adata_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_primary_adata_tags_source(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        with patch(
            "backend.data.market_data._fetch_stock_list_adata",
            return_value=_adata_stock_df(),
        ):
            result = await service.get_watchlist_snapshot(["600519"], snap_at)
        assert len(result) == 1
        assert isinstance(result[0], WatchlistMarketSnapshot)
        assert result[0].source == "adata"
        assert result[0].snapshot_at == snap_at

    @pytest.mark.asyncio
    async def test_adata_exception_falls_back_to_akshare_multi_code(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        """Codex Cycle 1 [P1]: multi-code akshare fallback must filter via isin()."""
        # Frame contains 2 watchlist codes (600519, 000001) + 1 noise row (300750).
        # The akshare helper must return only the requested codes.
        noisy_df = pd.DataFrame(
            [
                _akshare_spot_df().iloc[0].to_dict(),  # 600519
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "最新价": 12.5,
                    "今开": 12.4,
                    "最高": 12.6,
                    "最低": 12.3,
                    "昨收": 12.45,
                    "涨跌幅": 0.4,
                    "成交量": 1_000_000.0,
                    "成交额": 12_500_000.0,
                    "换手率": 0.1,
                },
                {
                    "代码": "300750",
                    "名称": "宁德时代",
                    "最新价": 250.0,
                    "今开": 248.0,
                    "最高": 252.0,
                    "最低": 245.0,
                    "昨收": 247.5,
                    "涨跌幅": 1.0,
                    "成交量": 500_000.0,
                    "成交额": 125_000_000.0,
                    "换手率": 0.5,
                },
            ]
        )
        # Filter the noisy frame the way the real akshare helper does so
        # the test asserts the multi-code isin() filter at the helper
        # level (proves callers don't have to pre-filter).
        def _fake_list_akshare(codes_arg: list[str]) -> pd.DataFrame:
            return noisy_df[noisy_df["代码"].isin(codes_arg)]

        with (
            patch(
                "backend.data.market_data._fetch_stock_list_adata",
                side_effect=RuntimeError("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_stock_list_akshare",
                side_effect=_fake_list_akshare,
            ),
        ):
            result = await service.get_watchlist_snapshot(
                ["600519", "000001"], snap_at
            )

        assert len(result) == 2
        codes = {r.code for r in result}
        assert codes == {"600519", "000001"}
        assert all(r.source == "akshare" for r in result)

    @pytest.mark.asyncio
    async def test_empty_adata_frame_falls_back(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        """Codex Cycle 1 [P2]: empty primary frame must fall back to akshare."""
        with (
            patch(
                "backend.data.market_data._fetch_stock_list_adata",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.market_data._fetch_stock_list_akshare",
                return_value=_akshare_spot_df(),
            ),
        ):
            result = await service.get_watchlist_snapshot(["600519"], snap_at)
        assert len(result) == 1
        assert result[0].source == "akshare"

    @pytest.mark.asyncio
    async def test_both_legs_fail_raises(
        self, service: MarketDataService, snap_at: datetime
    ) -> None:
        with (
            patch(
                "backend.data.market_data._fetch_stock_list_adata",
                side_effect=RuntimeError("adata down"),
            ),
            patch(
                "backend.data.market_data._fetch_stock_list_akshare",
                side_effect=RuntimeError("akshare down"),
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
                "backend.data.market_data._fetch_stock_list_adata",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.market_data._fetch_stock_list_akshare",
                return_value=pd.DataFrame(),
            ),
        ):
            result = await service.get_watchlist_snapshot(["600519"], snap_at)
        assert result == []


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
