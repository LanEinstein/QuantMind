"""Unit tests for the WebSocket market stream endpoint."""

from __future__ import annotations

import json

import pytest

from backend.api.websocket import (
    ConnectionManager,
    _translate_redis_message,
    manager,
)
from backend.data.publisher import CHANNEL_MARKET, CHANNEL_NEWS


# ---------------------------------------------------------------------------
# ConnectionManager tests
# ---------------------------------------------------------------------------


class TestConnectionManager:
    def test_initial_state(self) -> None:
        mgr = ConnectionManager()
        assert mgr.client_count == 0


# ---------------------------------------------------------------------------
# Message translation tests
# ---------------------------------------------------------------------------


class TestTranslateRedisMessage:
    def test_market_single_quote(self) -> None:
        quote = {"code": "000001", "name": "上证指数", "price": 3200.5}
        raw = json.dumps(quote)
        messages = _translate_redis_message(CHANNEL_MARKET, raw)
        assert len(messages) == 1
        parsed = json.loads(messages[0])
        assert parsed["type"] == "index_update"
        assert parsed["data"]["code"] == "000001"

    def test_market_multiple_quotes(self) -> None:
        quotes = [
            {"code": "000001", "name": "上证指数", "price": 3200.5},
            {"code": "399001", "name": "深证成指", "price": 10500.0},
        ]
        raw = json.dumps(quotes)
        messages = _translate_redis_message(CHANNEL_MARKET, raw)
        assert len(messages) == 2
        types = {json.loads(m)["type"] for m in messages}
        assert types == {"index_update"}

    def test_news_single_article(self) -> None:
        article = {
            "title": "央行降准",
            "source": "eastmoney",
            "importance_score": 9,
        }
        raw = json.dumps(article)
        messages = _translate_redis_message(CHANNEL_NEWS, raw)
        assert len(messages) == 1
        parsed = json.loads(messages[0])
        assert parsed["type"] == "news"
        assert parsed["data"]["title"] == "央行降准"

    def test_news_multiple_articles(self) -> None:
        articles = [
            {"title": "A", "importance_score": 5},
            {"title": "B", "importance_score": 8},
        ]
        raw = json.dumps(articles)
        messages = _translate_redis_message(CHANNEL_NEWS, raw)
        assert len(messages) == 2

    def test_invalid_json(self) -> None:
        messages = _translate_redis_message(CHANNEL_MARKET, "not-json{{{")
        assert messages == []

    def test_unknown_channel(self) -> None:
        messages = _translate_redis_message(
            "unknown:channel", json.dumps({"foo": 1})
        )
        assert messages == []
