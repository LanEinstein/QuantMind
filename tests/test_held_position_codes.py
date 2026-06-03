"""Tests for ``_read_held_position_codes`` — the late-bound broker-held
codes reader that the 30s collector unions into its snapshot universe.

P0-8-amendment-2026-06-03-collect-held-positions: a BUY fill does not add
its code to the configured watchlist, so without this reader the held
positions never get a fresh ``market_realtime`` row and intraday MTM falls
through to its red-line-banned cost-price fallback. The reader is **fail-open**
(infra glitch, not data corruption): any broker read error degrades to an
empty list so the collector falls back to watchlist-only rather than crashing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.main import _read_held_position_codes


def _pos(code: str, volume: int) -> SimpleNamespace:
    return SimpleNamespace(code=code, volume=volume)


def _app_with_registry(registry: object) -> SimpleNamespace:
    app = SimpleNamespace()
    app.state = SimpleNamespace(broker_registry=registry)
    return app


@pytest.mark.asyncio
async def test_returns_held_codes_with_positive_volume() -> None:
    broker = AsyncMock()
    broker.get_positions.return_value = (
        _pos("605111", 200),
        _pos("300433", 100),
    )
    registry = MagicMock()
    registry.get_broker.return_value = broker

    codes = await _read_held_position_codes(_app_with_registry(registry))

    assert codes == ["605111", "300433"]
    registry.get_broker.assert_called_once_with("default")


@pytest.mark.asyncio
async def test_zero_volume_excluded() -> None:
    # A fully-closed position (volume 0) must not be collected.
    broker = AsyncMock()
    broker.get_positions.return_value = (
        _pos("605111", 200),
        _pos("000001", 0),
    )
    registry = MagicMock()
    registry.get_broker.return_value = broker

    codes = await _read_held_position_codes(_app_with_registry(registry))

    assert codes == ["605111"]


@pytest.mark.asyncio
async def test_registry_absent_returns_empty() -> None:
    # broker_registry is attached only in _init_trading_layer (after the
    # data layer); early ticks see no registry → silent [] (not an error).
    app = SimpleNamespace()
    app.state = SimpleNamespace()

    codes = await _read_held_position_codes(app)

    assert codes == []


@pytest.mark.asyncio
async def test_broker_lookup_failure_fails_open() -> None:
    registry = MagicMock()
    registry.get_broker.side_effect = KeyError("default")

    codes = await _read_held_position_codes(_app_with_registry(registry))

    assert codes == []


@pytest.mark.asyncio
async def test_get_positions_failure_fails_open() -> None:
    broker = AsyncMock()
    broker.get_positions.side_effect = RuntimeError("mongo down")
    registry = MagicMock()
    registry.get_broker.return_value = broker

    codes = await _read_held_position_codes(_app_with_registry(registry))

    assert codes == []
