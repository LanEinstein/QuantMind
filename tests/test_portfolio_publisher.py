"""Tests for publish_portfolio_event in the data publisher module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend.data.publisher import CHANNEL_PORTFOLIO, publish_portfolio_event


class TestPublishPortfolioEvent:
    @pytest.mark.asyncio
    async def test_publishes_position_update(self) -> None:
        redis_mock = AsyncMock()
        await publish_portfolio_event(
            redis_mock,
            "position_update",
            {"account_id": "default", "positions": [{"code": "600519"}]},
        )
        redis_mock.publish.assert_called_once()
        channel, payload = redis_mock.publish.call_args.args
        assert channel == CHANNEL_PORTFOLIO
        parsed = json.loads(payload)
        assert parsed["type"] == "position_update"
        assert parsed["data"]["account_id"] == "default"

    @pytest.mark.asyncio
    async def test_publishes_circuit_breaker_update(self) -> None:
        redis_mock = AsyncMock()
        await publish_portfolio_event(
            redis_mock,
            "circuit_breaker_update",
            {"halted": True, "daily_pnl_pct": -0.06},
        )
        payload = json.loads(redis_mock.publish.call_args.args[1])
        assert payload["type"] == "circuit_breaker_update"
        assert payload["data"]["halted"] is True

    @pytest.mark.asyncio
    async def test_noop_when_redis_is_none(self) -> None:
        # Should not raise
        await publish_portfolio_event(None, "position_update", {})

    @pytest.mark.asyncio
    async def test_swallows_redis_exception(self) -> None:
        redis_mock = AsyncMock()
        redis_mock.publish.side_effect = ConnectionError("Redis down")
        # Should not raise
        await publish_portfolio_event(
            redis_mock, "position_update", {"account_id": "default"}
        )
