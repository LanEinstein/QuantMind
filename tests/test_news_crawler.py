"""Tests for NewsCrawlerService (C-005: 5 sources × 3 domains)."""

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


def _cls_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "标题": "CLS 快讯",
                "内容": "央行降准消息扩散至 600519",
                "发布日期": "2026-03-22",
                "发布时间": "09:00:00",
                "链接": "https://www.cls.cn/detail/1",
            }
        ]
    )


def _global_em_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "标题": "Global EM 快讯",
                "摘要": "美联储利率决议召开",
                "发布时间": "2026-03-22 21:00:00",
                "链接": "https://global.eastmoney.com/1",
            }
        ]
    )


def _global_sina_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"时间": "2026-03-22 21:05:00", "内容": "Sina global flash content"}
        ]
    )


def _cctv_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "20260322",
                "title": "新闻联播头条",
                "content": "中央政策动向",
            }
        ]
    )


class TestFetchLatestNews:
    """Tests for fetch_latest_news (C-005 multi-domain fan-out)."""

    @pytest.mark.asyncio
    async def test_returns_articles_from_eastmoney(
        self, service: NewsCrawlerService
    ) -> None:
        with (
            patch(
                "backend.data.news_crawler._safe_fetch_news_eastmoney",
                return_value=_akshare_news_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cls",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_em",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_sina",
                return_value=pd.DataFrame(),
            ),
        ):
            result = await service.fetch_latest_news(limit=10)
        assert len(result) == 2
        assert all(isinstance(a, NewsArticle) for a in result)
        # Sorted by publish_time desc: 09:05 > 09:00
        assert result[0].title == "科技板块大涨"
        assert result[1].title == "央行宣布降准50个基点"
        assert all(a.source == "eastmoney" for a in result)
        assert all(a.domain == "financial" for a in result)

    @pytest.mark.asyncio
    async def test_multi_domain_fan_in(
        self, service: NewsCrawlerService
    ) -> None:
        with (
            patch(
                "backend.data.news_crawler._safe_fetch_news_eastmoney",
                return_value=_akshare_news_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cls",
                return_value=_cls_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_em",
                return_value=_global_em_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_sina",
                return_value=_global_sina_df(),
            ),
        ):
            result = await service.fetch_latest_news(limit=50)
        domains = {a.domain for a in result}
        # CCTV is not requested by default — 6h cadence handled elsewhere.
        assert domains == {"financial", "global"}
        sources = {a.source for a in result}
        assert sources == {"eastmoney", "cls", "global_em", "global_sina"}

    @pytest.mark.asyncio
    async def test_include_cctv_extends_domains(
        self, service: NewsCrawlerService
    ) -> None:
        with (
            patch(
                "backend.data.news_crawler._safe_fetch_news_eastmoney",
                return_value=_akshare_news_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cls",
                return_value=_cls_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_em",
                return_value=_global_em_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_sina",
                return_value=_global_sina_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cctv",
                return_value=_cctv_df(),
            ),
        ):
            result = await service.fetch_latest_news(
                limit=50, include_cctv=True
            )
        assert {a.domain for a in result} == {
            "financial",
            "political",
            "global",
        }

    @pytest.mark.asyncio
    async def test_one_source_failure_does_not_collapse_rest(
        self, service: NewsCrawlerService
    ) -> None:
        with (
            patch(
                "backend.data.news_crawler._safe_fetch_news_eastmoney",
                return_value=_akshare_news_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cls",
                side_effect=RuntimeError("cls outage"),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_em",
                return_value=_global_em_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_sina",
                return_value=_global_sina_df(),
            ),
        ):
            result = await service.fetch_latest_news(limit=50)
        # Eastmoney + global_em + global_sina survived; cls dropped silently.
        assert len(result) >= 4
        assert "cls" not in {a.source for a in result}

    @pytest.mark.asyncio
    async def test_within_domain_dedupe_collapses_duplicates(
        self, service: NewsCrawlerService
    ) -> None:
        dup_df = pd.concat(
            [_akshare_news_df(), _akshare_news_df()], ignore_index=True
        )
        with (
            patch(
                "backend.data.news_crawler._safe_fetch_news_eastmoney",
                return_value=dup_df,
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cls",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_em",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_sina",
                return_value=pd.DataFrame(),
            ),
        ):
            result = await service.fetch_latest_news(limit=50)
        urls = [a.url for a in result]
        assert len(urls) == len(set(urls))

    @pytest.mark.asyncio
    async def test_limit_respected(
        self, service: NewsCrawlerService
    ) -> None:
        with (
            patch(
                "backend.data.news_crawler._safe_fetch_news_eastmoney",
                return_value=_akshare_news_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cls",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_em",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_sina",
                return_value=pd.DataFrame(),
            ),
        ):
            result = await service.fetch_latest_news(limit=1)
        assert len(result) <= 1

    @pytest.mark.asyncio
    async def test_all_sources_failing_returns_empty(
        self, service: NewsCrawlerService
    ) -> None:
        with (
            patch(
                "backend.data.news_crawler._safe_fetch_news_eastmoney",
                side_effect=Exception("source down"),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cls",
                side_effect=Exception("source down"),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_em",
                side_effect=Exception("source down"),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_sina",
                side_effect=Exception("source down"),
            ),
        ):
            result = await service.fetch_latest_news(limit=50)
        assert result == []

    @pytest.mark.asyncio
    async def test_stock_codes_extraction(
        self, service: NewsCrawlerService
    ) -> None:
        with (
            patch(
                "backend.data.news_crawler._safe_fetch_news_eastmoney",
                return_value=_akshare_news_df(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_cls",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_em",
                return_value=pd.DataFrame(),
            ),
            patch(
                "backend.data.news_crawler._safe_fetch_news_global_sina",
                return_value=pd.DataFrame(),
            ),
        ):
            result = await service.fetch_latest_news(limit=50)
        article_with_code = next(a for a in result if "600519" in a.content)
        assert "600519" in article_with_code.stock_codes


class TestPerSourceParsers:
    def test_parse_cls_df_assigns_financial_domain(self) -> None:
        articles = news_crawler._parse_cls_df(_cls_df())
        assert len(articles) == 1
        assert articles[0].domain == "financial"
        assert articles[0].source == "cls"
        # stock_codes auto-extracted from content
        assert "600519" in articles[0].stock_codes

    def test_parse_global_em_df_assigns_global_domain(self) -> None:
        articles = news_crawler._parse_global_em_df(_global_em_df())
        assert articles[0].domain == "global"
        assert articles[0].source == "global_em"

    def test_parse_global_sina_df_synthesises_url_when_missing(self) -> None:
        articles = news_crawler._parse_global_sina_df(_global_sina_df())
        assert articles[0].url.startswith("global_sina://")
        assert articles[0].domain == "global"

    def test_parse_cctv_df_assigns_political_domain(self) -> None:
        articles = news_crawler._parse_cctv_df(_cctv_df())
        assert articles[0].domain == "political"
        assert articles[0].source == "cctv"
        assert articles[0].url.startswith("cctv://20260322-")

    def test_parse_cls_handles_missing_url(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "标题": "no url",
                    "内容": "x",
                    "发布日期": "2026-05-16",
                    "发布时间": "09:00:00",
                }
            ]
        )
        articles = news_crawler._parse_cls_df(df)
        assert articles[0].url.startswith("cls://")

    def test_parse_global_em_synthesises_url_when_missing(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "标题": "no url",
                    "摘要": "x",
                    "发布时间": "2026-05-16 09:00:00",
                }
            ]
        )
        articles = news_crawler._parse_global_em_df(df)
        assert articles[0].url.startswith("global_em://")


class TestFetchCctv:
    @pytest.mark.asyncio
    async def test_returns_cctv_articles(
        self, service: NewsCrawlerService
    ) -> None:
        with patch(
            "backend.data.news_crawler._safe_fetch_news_cctv",
            return_value=_cctv_df(),
        ):
            result = await service.fetch_cctv()
        assert len(result) == 1
        assert result[0].source == "cctv"
        assert result[0].domain == "political"

    @pytest.mark.asyncio
    async def test_cctv_failure_returns_empty(
        self, service: NewsCrawlerService
    ) -> None:
        with patch(
            "backend.data.news_crawler._safe_fetch_news_cctv",
            side_effect=RuntimeError("cctv outage"),
        ):
            # _safe_fetch_news_cctv swallows internally, but exercise the
            # service-level guard in case the wrapper ever changes.
            result = await service.fetch_cctv()
        assert result == []


class TestSafeFetchHelpers:
    """Each safe wrapper must always return a DataFrame, never raise."""

    def test_safe_cls_returns_empty_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            news_crawler,
            "_fetch_news_cls",
            lambda: (_ for _ in ()).throw(RuntimeError("net")),
        )
        df = news_crawler._safe_fetch_news_cls()
        assert df.empty
        assert list(df.columns) == news_crawler._EXPECTED_CLS_COLUMNS

    def test_safe_global_em_returns_empty_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            news_crawler,
            "_fetch_news_global_em",
            lambda: (_ for _ in ()).throw(RuntimeError("net")),
        )
        df = news_crawler._safe_fetch_news_global_em()
        assert df.empty
        assert list(df.columns) == news_crawler._EXPECTED_GLOBAL_EM_COLUMNS

    def test_safe_global_sina_returns_empty_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            news_crawler,
            "_fetch_news_global_sina",
            lambda: (_ for _ in ()).throw(RuntimeError("net")),
        )
        df = news_crawler._safe_fetch_news_global_sina()
        assert df.empty
        assert list(df.columns) == news_crawler._EXPECTED_GLOBAL_SINA_COLUMNS

    def test_safe_cctv_returns_empty_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            news_crawler,
            "_fetch_news_cctv",
            lambda date_str: (_ for _ in ()).throw(RuntimeError("net")),
        )
        df = news_crawler._safe_fetch_news_cctv("20260516")
        assert df.empty
        assert list(df.columns) == news_crawler._EXPECTED_CCTV_COLUMNS


class TestFetchStockNews:
    """Tests for fetch_stock_news (eastmoney per-code path, unchanged)."""

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
        assert result[0].source == "eastmoney"
        assert result[0].domain == "financial"

    @pytest.mark.asyncio
    async def test_empty_on_failure(
        self, service: NewsCrawlerService
    ) -> None:
        with patch(
            "backend.data.news_crawler._fetch_stock_news_akshare",
            side_effect=Exception("fail"),
        ):
            result = await service.fetch_stock_news("600519")
        assert result == []


class TestSafeFetchNewsEastmoney:
    """Tolerant wrapper around the akshare eastmoney empty-symbol call."""

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


class TestImportIsolation:
    """C-005 acceptance: ``news_crawler`` / ``news_dedupe`` do not pull LLM
    or agent layers in. Pure data pipeline only."""

    def test_news_crawler_module_has_no_llm_or_agent_imports(self) -> None:
        import ast
        from pathlib import Path

        for path in (
            "backend/data/news_crawler.py",
            "backend/data/news_dedupe.py",
        ):
            tree = ast.parse(Path(path).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    parts = mod.split(".")
                    if parts[:2] == ["backend", "llm"]:
                        pytest.fail(f"{path} imports {mod}")
                    if parts[:2] == ["backend", "agents"]:
                        pytest.fail(f"{path} imports {mod}")
                    if parts[:2] == ["backend", "mirofish"]:
                        pytest.fail(f"{path} imports {mod}")
                    if parts[:2] == ["backend", "risk"]:
                        pytest.fail(f"{path} imports {mod}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        parts = alias.name.split(".")
                        if parts[:2] in (
                            ["backend", "llm"],
                            ["backend", "agents"],
                            ["backend", "mirofish"],
                            ["backend", "risk"],
                        ):
                            pytest.fail(f"{path} imports {alias.name}")
