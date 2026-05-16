"""G-009 — WebSocket 14-kind contract tests.

Coverage:
- SYSTEM_EVENT_TYPES locked at 8 entries
- FORBIDDEN_WS_TYPES locked at 2 (auth_mode_change + approval_update)
- publish_system_event rejects forbidden + unknown kinds, accepts allowed ones
- _translate_redis_message drops payloads with forbidden / unknown type
- _translate_redis_message forwards every allowed kind exactly once
- redline grep: no live code references auth_mode_change / approval_update
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.api.websocket import _translate_redis_message
from backend.data.publisher import (
    CHANNEL_SYSTEM,
    FORBIDDEN_WS_TYPES,
    SYSTEM_EVENT_TYPES,
    publish_system_event,
)


def test_system_event_types_locked_at_eight() -> None:
    assert len(SYSTEM_EVENT_TYPES) == 8
    expected = {
        "instruction_plan_update",
        "broker_event",
        "equity_point_update",
        "data_quality_breach",
        "freeze_source_update",
        "ticket_update",
        "acceptance_report_ready",
        "feishu_message_received",
    }
    assert SYSTEM_EVENT_TYPES == expected


def test_forbidden_ws_types_locked() -> None:
    assert FORBIDDEN_WS_TYPES == frozenset(
        {"auth_mode_change", "approval_update"}
    )


def test_no_overlap_between_allowed_and_forbidden() -> None:
    assert SYSTEM_EVENT_TYPES.isdisjoint(FORBIDDEN_WS_TYPES)


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden_type", sorted(FORBIDDEN_WS_TYPES))
async def test_publish_system_event_rejects_forbidden(
    forbidden_type: str,
) -> None:
    redis = AsyncMock()
    with pytest.raises(ValueError, match="removed by G-009"):
        await publish_system_event(redis, forbidden_type, {"x": 1})
    redis.publish.assert_not_called()


@pytest.mark.asyncio
async def test_publish_system_event_rejects_unknown() -> None:
    redis = AsyncMock()
    with pytest.raises(ValueError, match="unknown system event_type"):
        await publish_system_event(redis, "some_garbage_kind", {})
    redis.publish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", sorted(SYSTEM_EVENT_TYPES))
async def test_publish_system_event_forwards_allowed(event_type: str) -> None:
    redis = AsyncMock()
    await publish_system_event(redis, event_type, {"foo": "bar"})
    redis.publish.assert_awaited_once()
    args = redis.publish.await_args.args
    assert args[0] == CHANNEL_SYSTEM
    payload = json.loads(args[1])
    assert payload == {"type": event_type, "data": {"foo": "bar"}}


@pytest.mark.asyncio
async def test_publish_system_event_no_redis_is_noop() -> None:
    # None redis must NOT raise — caller wires it conditionally.
    await publish_system_event(None, "ticket_update", {"x": 1})


def test_translate_drops_forbidden_kind_on_system_channel() -> None:
    raw = json.dumps({"type": "auth_mode_change", "data": {}})
    out = _translate_redis_message(CHANNEL_SYSTEM, raw)
    assert out == []


def test_translate_drops_unknown_kind_on_system_channel() -> None:
    raw = json.dumps({"type": "rogue_kind", "data": {}})
    out = _translate_redis_message(CHANNEL_SYSTEM, raw)
    assert out == []


@pytest.mark.parametrize("event_type", sorted(SYSTEM_EVENT_TYPES))
def test_translate_forwards_every_allowed_kind(event_type: str) -> None:
    raw = json.dumps({"type": event_type, "data": {"x": 1}})
    out = _translate_redis_message(CHANNEL_SYSTEM, raw)
    assert len(out) == 1
    parsed = json.loads(out[0])
    assert parsed == {"type": event_type, "data": {"x": 1}}


def test_translate_drops_non_dict_payload_on_system_channel() -> None:
    raw = json.dumps(["not", "an", "envelope"])
    out = _translate_redis_message(CHANNEL_SYSTEM, raw)
    assert out == []


def test_no_live_backend_references_to_forbidden_kinds() -> None:
    """Static grep — auth_mode_change / approval_update absent from .py
    files except the publisher's FORBIDDEN_WS_TYPES + this test file."""
    pattern = re.compile(r"auth_mode_change|approval_update")
    leak: list[str] = []
    backend = Path("backend")
    for path in backend.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            # Allowed location — the publisher declares the forbidden set
            # using the very strings being checked.
            if path == Path("backend/data/publisher.py"):
                continue
            leak.append(str(path))
    assert leak == [], leak


def test_no_live_frontend_references_to_forbidden_kinds() -> None:
    """Static grep — auth_mode_change / approval_update absent from
    .ts / .vue files except the locked FORBIDDEN_WS_MESSAGE_TYPES export
    in types/market.ts + this test file's pair."""
    pattern = re.compile(r"auth_mode_change|approval_update")
    leak: list[str] = []
    frontend = Path("frontend/src")
    for path in [*frontend.rglob("*.ts"), *frontend.rglob("*.vue")]:
        if "__tests__" in path.parts:
            # Snapshots / spec files declaring the forbidden list are OK.
            continue
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            if path == Path("frontend/src/types/market.ts"):
                continue
            leak.append(str(path))
    assert leak == [], leak
