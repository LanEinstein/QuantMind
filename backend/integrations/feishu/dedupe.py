"""Feishu event-id deduplication (P0-2 §2.4 / F-003).

Lark guarantees ``event_id`` is unique per dispatched envelope. The SDK
will redeliver an event on reconnect / ack loss, so the receiver must
short-circuit duplicates before the consumer (F-004 parser) runs to
prevent doubled MockBroker writes.

Two implementations:

* :class:`RedisEventDedupe` — production. Stores
  ``feishu:dedupe:{event_id}`` with ``SET NX EX <ttl>`` so the first
  writer wins atomically across uvicorn workers.
* :class:`InMemoryEventDedupe` — tests and dev. Bounded LRU so a long
  session does not balloon memory.

Both satisfy the :class:`EventDedupe` Protocol so the receiver can
swap them without branching.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Protocol


class EventDedupe(Protocol):
    """Single check-and-claim primitive — atomic by contract."""

    async def claim(self, event_id: str) -> bool:
        """Return ``True`` iff this is the first time ``event_id`` is seen."""
        ...


class _RedisLike(Protocol):
    """Minimal async Redis API used by :class:`RedisEventDedupe`."""

    async def set(  # noqa: A003 — Redis idiom; this is the literal command name
        self,
        name: str,
        value: str,
        *,
        ex: int | None = ...,
        nx: bool | None = ...,
    ) -> bool | None: ...


class RedisEventDedupe:
    """Atomic dedupe using Redis ``SET NX EX``.

    Args:
        redis: async Redis client (``redis.asyncio.Redis`` or compatible).
        prefix: key prefix; ``feishu:dedupe:`` keeps the namespace
            obvious in `redis-cli KEYS`.
        ttl_seconds: TTL window. Lark redelivers within minutes; 24h
            absorbs even long disconnects without leaking memory.
    """

    def __init__(
        self,
        redis: _RedisLike,
        *,
        prefix: str = "feishu:dedupe:",
        ttl_seconds: int = 86_400,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    async def claim(self, event_id: str) -> bool:
        if not event_id:
            raise ValueError("event_id must not be empty")
        key = self._prefix + event_id
        # SET key value NX EX ttl — returns truthy when the key did not
        # exist (i.e. we claimed it), falsy when another worker already
        # set it. The same call wins atomically across processes.
        result = await self._redis.set(key, "1", ex=self._ttl, nx=True)
        return bool(result)


class InMemoryEventDedupe:
    """LRU-backed in-memory dedupe used by tests and single-process dev.

    Args:
        max_entries: number of event_ids retained. The LRU evicts the
            oldest entry once full so a long-running test does not
            grow unbounded.
        ttl_seconds: per-entry TTL; expired entries are reclaimed lazily
            on the next ``claim`` call.
    """

    def __init__(
        self,
        *,
        max_entries: int = 4_096,
        ttl_seconds: int = 86_400,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._max = max_entries
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def claim(self, event_id: str) -> bool:
        if not event_id:
            raise ValueError("event_id must not be empty")
        now = time.monotonic()
        async with self._lock:
            # Purge expired keys before lookup so a stale entry does
            # not block a re-occurrence after the TTL window.
            cutoff = now - self._ttl
            for key, ts in list(self._entries.items()):
                if ts < cutoff:
                    del self._entries[key]
                else:
                    break  # OrderedDict insertion order — first valid wins
            if event_id in self._entries:
                # Refresh recency — Lark may redeliver during the TTL
                # window and we want to keep recognising it as dupe.
                self._entries.move_to_end(event_id)
                return False
            self._entries[event_id] = now
            if len(self._entries) > self._max:
                self._entries.popitem(last=False)
            return True


__all__ = [
    "EventDedupe",
    "InMemoryEventDedupe",
    "RedisEventDedupe",
]
