"""WebSocket endpoint for real-time market data streaming.

Subscribes to Redis pub/sub channels and forwards messages to
all connected WebSocket clients.

Expected message format (sent to frontend):
    {"type": "index_update", "data": {...}}
    {"type": "news", "data": {...}}
    {"type": "signal", "data": "..."}
    {"type": "status", "data": {...}}
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from backend.data.publisher import CHANNEL_MARKET, CHANNEL_NEWS, CHANNEL_PORTFOLIO

log = structlog.get_logger(component="api_websocket")

router = APIRouter()

# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Manages active WebSocket connections.

    Thread-safe via asyncio (single-threaded event loop).
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        log.info("ws_client_connected", total=len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        log.info("ws_client_disconnected", total=len(self._connections))

    async def broadcast(self, message: str) -> None:
        """Send a message to all connected clients."""
        stale: list[WebSocket] = []
        for ws in self._connections:
            try:
                if ws.application_state == WebSocketState.CONNECTED:
                    await ws.send_text(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._connections.discard(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Redis pub/sub → WebSocket bridge
# ---------------------------------------------------------------------------


async def _subscribe_and_forward(redis_client: Any) -> None:
    """Subscribe to Redis pub/sub channels and forward to WebSocket clients.

    Runs as a background task for the lifetime of the application.
    """
    if redis_client is None:
        log.warning("ws_redis_unavailable")
        return

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(CHANNEL_MARKET, CHANNEL_NEWS, CHANNEL_PORTFOLIO)
    log.info(
        "ws_redis_subscribed",
        channels=[CHANNEL_MARKET, CHANNEL_NEWS, CHANNEL_PORTFOLIO],
    )

    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message is None:
                await asyncio.sleep(0.1)
                continue

            if message["type"] != "message":
                continue

            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            raw_data = message["data"]
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode()

            if manager.client_count == 0:
                continue

            ws_messages = _translate_redis_message(channel, raw_data)
            for ws_msg in ws_messages:
                await manager.broadcast(ws_msg)

    except asyncio.CancelledError:
        log.info("ws_redis_subscriber_cancelled")
    except Exception as exc:
        log.error("ws_redis_subscriber_error", error=str(exc))
    finally:
        await pubsub.unsubscribe(CHANNEL_MARKET, CHANNEL_NEWS, CHANNEL_PORTFOLIO)
        await pubsub.aclose()


def _translate_redis_message(
    channel: str, raw_data: str
) -> list[str]:
    """Convert a Redis pub/sub message into WebSocket message(s)."""
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        return []

    messages: list[str] = []

    if channel == CHANNEL_MARKET:
        # Market channel sends a list of quotes; emit one per quote
        if isinstance(data, list):
            for quote in data:
                messages.append(
                    json.dumps(
                        {"type": "index_update", "data": quote},
                        ensure_ascii=False,
                    )
                )
        else:
            messages.append(
                json.dumps(
                    {"type": "index_update", "data": data},
                    ensure_ascii=False,
                )
            )

    elif channel == CHANNEL_NEWS:
        # News channel sends a list of articles; emit one per article
        if isinstance(data, list):
            for article in data:
                messages.append(
                    json.dumps(
                        {"type": "news", "data": article},
                        ensure_ascii=False,
                    )
                )
        else:
            messages.append(
                json.dumps(
                    {"type": "news", "data": data},
                    ensure_ascii=False,
                )
            )

    elif channel == CHANNEL_PORTFOLIO:
        # Portfolio channel messages are already in {"type": ..., "data": ...}
        # format — forward as-is (single message, no list unwrapping).
        messages.append(json.dumps(data, ensure_ascii=False))

    return messages


# ---------------------------------------------------------------------------
# WebSocket route
# ---------------------------------------------------------------------------


@router.websocket("/ws/market")
async def websocket_market(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time market data.

    Clients connect here to receive index_update, news, signal,
    and status messages pushed from the backend.
    """
    await manager.connect(websocket)
    try:
        # Keep connection alive; listen for client messages (e.g., pings)
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(), timeout=60
                )
                # Client can send a ping; respond with pong
                if data == "ping":
                    await websocket.send_text(
                        json.dumps({"type": "pong"})
                    )
            except asyncio.TimeoutError:
                # Send a keepalive
                try:
                    await websocket.send_text(
                        json.dumps({"type": "heartbeat"})
                    )
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("ws_connection_error", error=str(exc))
    finally:
        manager.disconnect(websocket)
