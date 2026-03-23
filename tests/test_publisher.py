"""Tests for Redis pub/sub publisher (TDD RED -> GREEN)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from backend.data.publisher import publish_market_update, publish_news
from backend.models.market import IndexQuote, NewsArticle


def _sample_index_quote() -> IndexQuote:
    return IndexQuote(
        code="000001",
        name="上证指数",
        price=3150.5,
        change_pct=0.85,
        volume=3_500_000_000.0,
        amount=450_000_000_000.0,
        timestamp=datetime(2026, 3, 22, 10, 30, tzinfo=UTC),
    )


def _sample_news() -> NewsArticle:
    return NewsArticle(
        title="Test News",
        content="Test content",
        source="eastmoney",
        url="https://example.com/1",
        publish_time=datetime(2026, 3, 22, 9, 0, tzinfo=UTC),
    )


class TestPublishMarketUpdate:
    """Tests for publish_market_update."""

    @pytest.mark.asyncio
    async def test_publishes_to_channel(self) -> None:
        redis_mock = AsyncMock()
        await publish_market_update(redis_mock, [_sample_index_quote()])
        redis_mock.publish.assert_called_once()
        call_args = redis_mock.publish.call_args
        assert call_args[0][0] == "market:realtime"

    @pytest.mark.asyncio
    async def test_none_redis_no_error(self) -> None:
        await publish_market_update(None, [_sample_index_quote()])

    @pytest.mark.asyncio
    async def test_redis_failure_logged_not_raised(self) -> None:
        redis_mock = AsyncMock()
        redis_mock.publish.side_effect = Exception("Redis down")
        # Should not raise
        await publish_market_update(redis_mock, [_sample_index_quote()])


class TestPublishNews:
    """Tests for publish_news."""

    @pytest.mark.asyncio
    async def test_publishes_to_channel(self) -> None:
        redis_mock = AsyncMock()
        await publish_news(redis_mock, [_sample_news()])
        redis_mock.publish.assert_called_once()
        call_args = redis_mock.publish.call_args
        assert call_args[0][0] == "news:latest"

    @pytest.mark.asyncio
    async def test_none_redis_no_error(self) -> None:
        await publish_news(None, [_sample_news()])
