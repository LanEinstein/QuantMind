"""Redis pub/sub helpers for real-time data distribution."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from backend.models.market import IndexQuote, NewsArticle, StockQuote

if TYPE_CHECKING:
    import redis.asyncio

log = structlog.get_logger(component="publisher")

QuoteType = IndexQuote | StockQuote

CHANNEL_MARKET = "market:realtime"
CHANNEL_NEWS = "news:latest"
CHANNEL_PORTFOLIO = "portfolio:updates"
CHANNEL_SYSTEM = "system:events"
"""G-009 — single channel for the 8 new WS message kinds.

Locked vocabulary (matches frontend ``WsMessage.type`` union):

* ``instruction_plan_update`` — InstructionPlan pool changes
* ``broker_event`` — append-only ledger ticks
* ``equity_point_update`` — 30s MTM snapshot
* ``data_quality_breach`` — DataQualityProvider raised a blocking breach
* ``freeze_source_update`` — any of the 5 freeze sources flipped
* ``ticket_update`` — reconciliation ticket OPEN / RESOLVED_* lifecycle
* ``acceptance_report_ready`` — daily 16:00:30 acceptance report
* ``feishu_message_received`` — inbound Feishu long-connection message

Forbidden kinds (P1-5 §2 红线 4 — removed by G-009):
* ``auth_mode_change`` — replaced by ``freeze_source_update`` source=switch
* ``approval_update`` — ApprovalQueue destructively removed in Phase A
"""

SYSTEM_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "instruction_plan_update",
        "broker_event",
        "equity_point_update",
        "data_quality_breach",
        "freeze_source_update",
        "ticket_update",
        "acceptance_report_ready",
        "feishu_message_received",
    }
)
"""Locked allowlist — :func:`publish_system_event` rejects anything else."""

FORBIDDEN_WS_TYPES: frozenset[str] = frozenset(
    {"auth_mode_change", "approval_update"}
)
"""Surfaces matching :func:`scripts/redline-check.sh` grep."""


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


async def publish_portfolio_event(
    redis_client: redis.asyncio.Redis | None,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Publish a portfolio event to Redis pub/sub.

    The event is wrapped as ``{"type": <event_type>, "data": <data>}``
    so the WebSocket bridge can forward it as-is.

    Args:
        redis_client: Async Redis client. If None, no-op.
        event_type: One of ``position_update``, ``circuit_breaker_update``.
        data: Event payload dict.
    """
    if redis_client is None:
        return

    try:
        payload = json.dumps(
            {"type": event_type, "data": data},
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_PORTFOLIO, payload)
    except Exception as exc:
        log.warning("publish_portfolio_failed", event_type=event_type, error=str(exc))


async def publish_system_event(
    redis_client: redis.asyncio.Redis | None,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """G-009 — publish a system-channel WS message to Redis.

    ``event_type`` must be one of :data:`SYSTEM_EVENT_TYPES` (the 8
    locked kinds for the G-009 WS upgrade). The event is wrapped as
    ``{"type": <event_type>, "data": <data>}`` so the WebSocket bridge
    can forward it as-is.

    Forbidden kinds (:data:`FORBIDDEN_WS_TYPES`) raise immediately so a
    typo or copy-paste from legacy code surfaces at the publisher rather
    than silently flowing through the bridge.
    """
    if event_type in FORBIDDEN_WS_TYPES:
        raise ValueError(
            f"WS event_type {event_type!r} was removed by G-009 "
            "(P1-5 §2 红线 4); use the replacement message kind."
        )
    if event_type not in SYSTEM_EVENT_TYPES:
        raise ValueError(
            f"unknown system event_type {event_type!r}; "
            f"allowed: {sorted(SYSTEM_EVENT_TYPES)}"
        )
    if redis_client is None:
        return
    try:
        payload = json.dumps(
            {"type": event_type, "data": data},
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_SYSTEM, payload)
    except Exception as exc:
        log.warning(
            "publish_system_failed",
            event_type=event_type,
            error=str(exc),
        )
