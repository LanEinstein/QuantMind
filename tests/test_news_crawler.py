"""Tests for NewsCrawlerService (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from backend.data import news_crawler
from backend.data.config import DataSourcesConfig, load_data_sources_config
from backend.data.news_crawler import NewsCrawlerService
from backend.models.market import NewsArticle

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
def service(config: DataSourcesConfig) -> NewsCrawlerService:
    return NewsCrawlerService(config)


def _akshare_news_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "发布时间": "2026-03-22 09:00:00",
                "新闻标题": "央行宣布降准50个基点",
                "新闻内容": "中国人民银行今日宣布降准，600519贵州茅台受益",
                "新闻链接": "https://finance.eastmoney.com/news/1",
            },
            {
                "发布时间": "2026-03-22 09:05:00",
                "新闻标题": "科技板块大涨",
                "新闻内容": "科技板块多只个股涨停",
                "新闻链接": "https://finance.eastmoney.com/news/2",
            },
        ]
    )


class TestFetchLatestNews:
    """Tests for fetch_latest_news."""

    @pytest.mark.asyncio
    async def test_returns_articles(self, service: NewsCrawlerService) -> None:
        with patch(
            "backend.data.news_crawler._fetch_news_eastmoney",
            return_value=_akshare_news_df(),
        ):
            result = await service.fetch_latest_news(limit=10)
        assert len(result) == 2
        assert all(isinstance(a, NewsArticle) for a in result)
        # Sorted by publish_time desc: 09:05 > 09:00
        assert result[0].title == "科技板块大涨"
        assert result[1].title == "央行宣布降准50个基点"
        assert result[0].source == "eastmoney"

    @pytest.mark.asyncio
    async def test_deduplication_by_url(self, service: NewsCrawlerService) -> None:
        dup_df = pd.concat([_akshare_news_df(), _akshare_news_df()], ignore_index=True)
        with patch(
            "backend.data.news_crawler._fetch_news_eastmoney",
            return_value=dup_df,
        ):
            result = await service.fetch_latest_news(limit=50)
        urls = [a.url for a in result]
        assert len(urls) == len(set(urls))

    @pytest.mark.asyncio
    async def test_limit_respected(self, service: NewsCrawlerService) -> None:
        with patch(
            "backend.data.news_crawler._fetch_news_eastmoney",
            return_value=_akshare_news_df(),
        ):
            result = await service.fetch_latest_news(limit=1)
        assert len(result) <= 1

    @pytest.mark.asyncio
    async def test_source_failure_graceful(self, service: NewsCrawlerService) -> None:
        with patch(
            "backend.data.news_crawler._fetch_news_eastmoney",
            side_effect=Exception("source down"),
        ):
            result = await service.fetch_latest_news(limit=50)
        assert result == []

    @pytest.mark.asyncio
    async def test_stock_codes_extraction(self, service: NewsCrawlerService) -> None:
        with patch(
            "backend.data.news_crawler._fetch_news_eastmoney",
            return_value=_akshare_news_df(),
        ):
            result = await service.fetch_latest_news(limit=50)
        # The article mentioning 600519 is at index 1 (sorted by time desc)
        article_with_code = result[1]
        assert "600519" in article_with_code.stock_codes


class TestFetchStockNews:
    """Tests for fetch_stock_news."""

    @pytest.mark.asyncio
    async def test_returns_stock_specific_news(
        self, service: NewsCrawlerService
    ) -> None:
        stock_df = pd.DataFrame(
            [
                {
                    "发布时间": "2026-03-22 09:00:00",
                    "新闻标题": "贵州茅台发布年报",
                    "新闻内容": "600519贵州茅台2025年报发布",
                    "新闻链接": "https://finance.eastmoney.com/news/3",
                }
            ]
        )
        with patch(
            "backend.data.news_crawler._fetch_stock_news_akshare",
            return_value=stock_df,
        ):
            result = await service.fetch_stock_news("600519", limit=10)
        assert len(result) == 1
        assert isinstance(result[0], NewsArticle)

    @pytest.mark.asyncio
    async def test_empty_on_failure(self, service: NewsCrawlerService) -> None:
        with patch(
            "backend.data.news_crawler._fetch_stock_news_akshare",
            side_effect=Exception("fail"),
        ):
            result = await service.fetch_stock_news("600519")
        assert result == []


class TestSafeFetchNewsEastmoney:
    """Tolerant wrapper around the akshare eastmoney empty-symbol call.

    Pinned to the production regression where upstream raises
    ``KeyError('result')`` every 5 minutes. The wrapper must:
    1. Suppress that exact KeyError as "empty payload" info,
    2. Re-raise any other KeyError so real schema bugs stay loud,
    3. Downgrade unrelated exceptions to a warning + empty DataFrame
       so the scheduler keeps running.
    """

    def test_returns_empty_on_keyerror_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake() -> pd.DataFrame:
            raise KeyError("result")

        monkeypatch.setattr(news_crawler, "_fetch_news_eastmoney", fake)
        df = news_crawler._safe_fetch_news_eastmoney()
        assert df.empty
        assert list(df.columns) == news_crawler._EXPECTED_NEWS_COLUMNS

    def test_propagates_unrelated_keyerror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake() -> pd.DataFrame:
            raise KeyError("some_other_field")

        monkeypatch.setattr(news_crawler, "_fetch_news_eastmoney", fake)
        with pytest.raises(KeyError):
            news_crawler._safe_fetch_news_eastmoney()

    def test_swallows_general_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake() -> pd.DataFrame:
            raise RuntimeError("network down")

        monkeypatch.setattr(news_crawler, "_fetch_news_eastmoney", fake)
        df = news_crawler._safe_fetch_news_eastmoney()
        assert df.empty
        assert list(df.columns) == news_crawler._EXPECTED_NEWS_COLUMNS

    def test_returns_payload_when_no_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        df_in = _akshare_news_df()
        monkeypatch.setattr(
            news_crawler, "_fetch_news_eastmoney", lambda: df_in
        )
        df_out = news_crawler._safe_fetch_news_eastmoney()
        pd.testing.assert_frame_equal(df_out, df_in)

    @pytest.mark.asyncio
    async def test_service_path_keyerror_result_yields_empty(
        self,
        service: NewsCrawlerService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Integration: full ``fetch_latest_news`` survives KeyError('result')."""

        def fake() -> pd.DataFrame:
            raise KeyError("result")

        monkeypatch.setattr(news_crawler, "_fetch_news_eastmoney", fake)
        result = await service.fetch_latest_news(limit=50)
        assert result == []
