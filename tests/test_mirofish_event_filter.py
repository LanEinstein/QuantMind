"""Tests for MiroFish event filter."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.mirofish.event_filter import extract_key_events


def _make_completion(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
    return resp


def _valid_events_json() -> str:
    return json.dumps({
        "events": [
            {
                "title": "央行降准50个基点",
                "content": "中国人民银行宣布降准",
                "importance_score": 9,
                "sectors": ["银行", "房地产"],
                "stocks": ["601398"],
            },
            {
                "title": "日常数据发布",
                "content": "PMI数据公布",
                "importance_score": 5,
                "sectors": ["制造业"],
                "stocks": [],
            },
        ]
    })


class TestExtractKeyEvents:
    @pytest.mark.asyncio
    async def test_valid_news_extracts_events(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion(_valid_events_json())
        )
        events = await extract_key_events(
            router, "新闻报告内容", "600519", "贵州茅台"
        )
        assert len(events) == 2
        assert events[0].title == "央行降准50个基点"
        assert events[0].importance_score == 9
        assert events[1].importance_score == 5

    @pytest.mark.asyncio
    async def test_empty_news_returns_empty(self) -> None:
        router = AsyncMock()
        events = await extract_key_events(router, "", "600519", "贵州茅台")
        assert events == ()
        router.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_news_returns_empty(self) -> None:
        router = AsyncMock()
        events = await extract_key_events(
            router, "[news_crawler error: timeout]", "600519", "贵州茅台"
        )
        assert events == ()

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(side_effect=Exception("API down"))
        events = await extract_key_events(
            router, "有新闻内容", "600519", "贵州茅台"
        )
        assert events == ()

    @pytest.mark.asyncio
    async def test_garbage_response_returns_empty(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion("这不是JSON")
        )
        events = await extract_key_events(
            router, "有新闻内容", "600519", "贵州茅台"
        )
        assert events == ()

    @pytest.mark.asyncio
    async def test_partial_valid_events(self) -> None:
        data = json.dumps({
            "events": [
                {
                    "title": "有效事件",
                    "content": "内容",
                    "importance_score": 8,
                    "sectors": [],
                    "stocks": [],
                },
                {
                    "title": "无效事件",
                    "content": "内容",
                    "importance_score": 15,  # Invalid: > 10
                    "sectors": [],
                    "stocks": [],
                },
            ]
        })
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion(data)
        )
        events = await extract_key_events(
            router, "有新闻内容", "600519", "贵州茅台"
        )
        assert len(events) == 1
        assert events[0].importance_score == 8

    @pytest.mark.asyncio
    async def test_uses_news_crawler_agent(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(
            return_value=_make_completion(_valid_events_json())
        )
        await extract_key_events(router, "内容", "600519", "贵州茅台")
        call_args = router.complete.call_args
        assert call_args[0][0] == "news_crawler"
