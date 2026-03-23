"""Tests for HistoryDataService (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from backend.data.config import DataSourcesConfig, load_data_sources_config
from backend.data.history_data import HistoryDataService
from backend.data.market_data import DataFetchError
from backend.models.market import FinancialData

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
def service(config: DataSourcesConfig) -> HistoryDataService:
    return HistoryDataService(config)


def _adata_kline_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "stock_code": "600519",
                "trade_time": "2026-03-20",
                "trade_date": "2026-03-20",
                "open": 1790.0,
                "close": 1800.0,
                "high": 1810.0,
                "low": 1785.0,
                "volume": 5_000_000,
                "amount": 9_000_000_000,
                "change_pct": 0.28,
                "change": 5.0,
                "turnover_ratio": 0.63,
                "pre_close": 1795.0,
            }
        ]
    )


def _baostock_kline_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-03-20",
                "open": "1790.0",
                "high": "1810.0",
                "low": "1785.0",
                "close": "1800.0",
                "volume": "5000000",
                "amount": "9000000000",
            }
        ]
    )


def _adata_finance_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "stock_code": "600519",
                "short_name": "贵州茅台",
                "report_date": "2025-12-31",
                "basic_eps": 45.8,
                "roe_wtd": 30.5,
                "total_rev_yoy_gr": 15.3,
            }
        ]
    )


class TestGetKline:
    """Tests for get_kline."""

    @pytest.mark.asyncio
    async def test_primary_success(self, service: HistoryDataService) -> None:
        with patch(
            "backend.data.history_data._fetch_kline_adata",
            return_value=_adata_kline_df(),
        ):
            df = await service.get_kline("600519")
        assert isinstance(df, pd.DataFrame)
        assert "date" in df.columns
        assert "open" in df.columns
        assert "close" in df.columns
        assert "high" in df.columns
        assert "low" in df.columns
        assert "volume" in df.columns
        assert len(df) == 1

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self, service: HistoryDataService) -> None:
        with (
            patch(
                "backend.data.history_data._fetch_kline_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.history_data._fetch_kline_baostock",
                return_value=_baostock_kline_df(),
            ),
        ):
            df = await service.get_kline("600519")
        assert len(df) == 1
        assert "date" in df.columns

    @pytest.mark.asyncio
    async def test_both_fail_raises(self, service: HistoryDataService) -> None:
        with (
            patch(
                "backend.data.history_data._fetch_kline_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.history_data._fetch_kline_baostock",
                side_effect=Exception("baostock down"),
            ),
        ):
            with pytest.raises(DataFetchError):
                await service.get_kline("600519")

    @pytest.mark.asyncio
    async def test_invalid_period(self, service: HistoryDataService) -> None:
        with pytest.raises(ValueError, match="period"):
            await service.get_kline("600519", period="hourly")

    @pytest.mark.asyncio
    async def test_invalid_adjust(self, service: HistoryDataService) -> None:
        with pytest.raises(ValueError, match="adjust"):
            await service.get_kline("600519", adjust="invalid")

    @pytest.mark.asyncio
    async def test_weekly_period(self, service: HistoryDataService) -> None:
        with patch(
            "backend.data.history_data._fetch_kline_adata",
            return_value=_adata_kline_df(),
        ):
            df = await service.get_kline("600519", period="weekly")
        assert len(df) == 1


class TestGetFinancialData:
    """Tests for get_financial_data."""

    @pytest.mark.asyncio
    async def test_primary_success(self, service: HistoryDataService) -> None:
        with patch(
            "backend.data.history_data._fetch_financial_adata",
            return_value=_adata_finance_df(),
        ):
            result = await service.get_financial_data("600519")
        assert isinstance(result, FinancialData)
        assert result.code == "600519"
        assert result.eps == 45.8

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self, service: HistoryDataService) -> None:
        akshare_df = pd.DataFrame(
            [
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "市盈率-动态": 32.5,
                    "市净率": 10.2,
                }
            ]
        )
        with (
            patch(
                "backend.data.history_data._fetch_financial_adata",
                side_effect=Exception("adata down"),
            ),
            patch(
                "backend.data.history_data._fetch_financial_akshare",
                return_value=akshare_df,
            ),
        ):
            result = await service.get_financial_data("600519")
        assert isinstance(result, FinancialData)
        assert result.pe_ratio == 32.5
