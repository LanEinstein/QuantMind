"""News crawler service for Chinese financial news sources."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

import pandas as pd
import structlog

from backend.data.config import DataSourcesConfig
from backend.models.market import NewsArticle

log = structlog.get_logger(component="news_crawler")

# Regex for 6-digit A-share stock codes in content
_STOCK_CODE_RE = re.compile(r"(?<!\d)([036]\d{5})(?!\d)")


# ---------------------------------------------------------------------------
# Low-level fetchers
# ---------------------------------------------------------------------------


def _fetch_news_eastmoney() -> pd.DataFrame:
    """Fetch latest financial news via akshare's eastmoney interface (sync)."""
    import akshare

    return akshare.stock_news_em(symbol="")


def _fetch_stock_news_akshare(code: str) -> pd.DataFrame:
    """Fetch news for a specific stock via akshare (sync)."""
    import akshare

    return akshare.stock_news_em(symbol=code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_stock_codes(text: str) -> tuple[str, ...]:
    """Extract A-share stock codes from text content."""
    matches = _STOCK_CODE_RE.findall(text)
    return tuple(sorted(set(matches)))


def _parse_news_df(
    df: pd.DataFrame, source: str = "eastmoney"
) -> list[NewsArticle]:
    """Convert a news DataFrame to list of NewsArticle models."""
    articles: list[NewsArticle] = []
    seen_urls: set[str] = set()

    for _, row in df.iterrows():
        url = str(row.get("新闻链接", row.get("url", "")))
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = str(row.get("新闻标题", row.get("title", "")))
        content = str(row.get("新闻内容", row.get("content", "")))
        pub_str = str(row.get("发布时间", row.get("publish_time", "")))

        try:
            publish_time = datetime.fromisoformat(pub_str)
            if publish_time.tzinfo is None:
                publish_time = publish_time.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            publish_time = datetime.now(tz=UTC)

        stock_codes = _extract_stock_codes(f"{title} {content}")

        articles.append(
            NewsArticle(
                title=title,
                content=content,
                source=source,
                url=url,
                publish_time=publish_time,
                stock_codes=stock_codes,
                importance_score=0,
            )
        )

    return articles


# ---------------------------------------------------------------------------
# NewsCrawlerService
# ---------------------------------------------------------------------------


class NewsCrawlerService:
    """Async service for fetching Chinese financial news.

    Aggregates from multiple sources (eastmoney via akshare, etc.)
    with graceful degradation when individual sources fail.
    """

    def __init__(self, config: DataSourcesConfig) -> None:
        self._config = config
        self._log = log

    async def fetch_latest_news(
        self, limit: int = 50
    ) -> list[NewsArticle]:
        """Fetch latest financial news from configured sources.

        Args:
            limit: Maximum number of articles to return.

        Returns:
            Deduplicated list of NewsArticle sorted by publish_time desc.
        """
        all_articles: list[NewsArticle] = []

        # Source 1: eastmoney via akshare
        try:
            df = await asyncio.to_thread(_fetch_news_eastmoney)
            all_articles.extend(_parse_news_df(df, source="eastmoney"))
        except Exception as exc:
            self._log.warning("eastmoney_news_failed", error=str(exc))

        # Deduplicate by URL
        seen: set[str] = set()
        unique: list[NewsArticle] = []
        for article in all_articles:
            if article.url not in seen:
                seen.add(article.url)
                unique.append(article)

        # Sort by publish_time descending
        unique.sort(key=lambda a: a.publish_time, reverse=True)

        return unique[:limit]

    async def fetch_stock_news(
        self, code: str, limit: int = 20
    ) -> list[NewsArticle]:
        """Fetch news related to a specific stock.

        Args:
            code: Stock code (e.g. "600519").
            limit: Maximum number of articles.

        Returns:
            List of NewsArticle for the given stock.
        """
        try:
            df = await asyncio.to_thread(_fetch_stock_news_akshare, code)
            articles = _parse_news_df(df, source="eastmoney")
            articles.sort(key=lambda a: a.publish_time, reverse=True)
            return articles[:limit]
        except Exception as exc:
            self._log.warning(
                "stock_news_failed", code=code, error=str(exc)
            )
            return []
