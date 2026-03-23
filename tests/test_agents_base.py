"""Tests for agent base helpers (TDD RED -> GREEN)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agents.base import call_agent, extract_json_from_response


def _make_completion(content: str = "test response") -> MagicMock:
    """Create a mock ChatCompletion."""
    choice = MagicMock()
    choice.message.content = content
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 20
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


class TestCallAgent:
    """Tests for call_agent helper."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(return_value=_make_completion("分析报告内容"))
        result = await call_agent(
            router, "news_crawler", "你是新闻分析师", "分析以下新闻"
        )
        assert result == "分析报告内容"
        router.complete.assert_called_once_with(
            "news_crawler",
            [
                {"role": "system", "content": "你是新闻分析师"},
                {"role": "user", "content": "分析以下新闻"},
            ],
        )

    @pytest.mark.asyncio
    async def test_error_returns_fallback(self) -> None:
        router = AsyncMock()
        router.complete = AsyncMock(side_effect=Exception("API down"))
        result = await call_agent(router, "news_crawler", "prompt", "content")
        assert "news_crawler" in result
        assert "error" in result.lower() or "API down" in result

    @pytest.mark.asyncio
    async def test_empty_choices(self) -> None:
        router = AsyncMock()
        resp = MagicMock()
        resp.choices = []
        resp.usage = None
        router.complete = AsyncMock(return_value=resp)
        result = await call_agent(router, "test_agent", "prompt", "content")
        assert "error" in result.lower() or result == ""


class TestExtractJsonFromResponse:
    """Tests for extract_json_from_response."""

    def test_valid_json(self) -> None:
        text = '分析结果如下：\n{"action": "买入", "confidence": 0.8}\n以上。'
        result = extract_json_from_response(text)
        assert result is not None
        assert result["action"] == "买入"

    def test_no_json(self) -> None:
        result = extract_json_from_response("这是一段纯文本")
        assert result is None

    def test_malformed_json(self) -> None:
        result = extract_json_from_response('{"broken": true,}')
        assert result is None

    def test_nested_json(self) -> None:
        text = '```json\n{"action": "卖出", "reasoning": "风险高"}\n```'
        result = extract_json_from_response(text)
        assert result is not None
        assert result["action"] == "卖出"
