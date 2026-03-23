"""Redis pub/sub helpers for real-time data distribution."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

from backend.models.market import IndexQuote, NewsArticle, StockQuote

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="publisher")

QuoteType = IndexQuote | StockQuote

CHANNEL_MARKET = "market:realtime"
CHANNEL_NEWS = "news:latest"


async def publish_market_update(
    redis_client: redis.asyncio.Redis | None,
    quotes: list[QuoteType],
) -> None:
    """Publish market data updates to Redis pub/sub.

    Args:
        redis_client: Async Redis client. If None, no-op.
        quotes: List of index or stock quotes to publish.
    """
    if redis_client is None:
        return

    try:
        payload = json.dumps(
            [q.model_dump(mode="json") for q in quotes],
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_MARKET, payload)
    except Exception as exc:
        log.warning("publish_market_failed", error=str(exc))


async def publish_news(
    redis_client: redis.asyncio.Redis | None,
    articles: list[NewsArticle],
) -> None:
    """Publish news articles to Redis pub/sub.

    Args:
        redis_client: Async Redis client. If None, no-op.
        articles: List of news articles to publish.
    """
    if redis_client is None:
        return

    try:
        payload = json.dumps(
            [a.model_dump(mode="json") for a in articles],
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_NEWS, payload)
    except Exception as exc:
        log.warning("publish_news_failed", error=str(exc))
