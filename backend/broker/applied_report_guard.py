"""Durable report-id idempotency guard for the ExecutionReportApplier (U-D4).

The :class:`backend.broker.appliers.ExecutionReportApplier` mutates the
MockBroker mirror once per parsed :class:`~backend.models.execution
.ExecutionReport`. The upstream Feishu dedupe keys on the Lark *envelope*
``event_id`` (not the report) and fails open when its store is
unavailable; the frontend ``POST /api/execution-reports`` path has no
dedupe at all. A second ``apply`` of the same ``report_id`` would
therefore double-mutate the broker — double cash deduction or a double
position delta — which the per-call BrokerEvent trail cannot undo.

This guard is the applier's last line of defence, keyed on ``report_id``.
It mirrors the U-B2 outbox claim/release pattern:

* :meth:`claim` is atomic — the first caller for a ``report_id`` wins and
  proceeds to mutate; a later caller gets ``False`` and the applier
  short-circuits to a no-op.
* :meth:`release` undoes a claim so a *failed* apply (broker raised) can
  be retried. The contract is at-most-once *successful* application, not
  at-most-once attempt.

Two implementations satisfy the :class:`AppliedReportGuard` Protocol:

* :class:`RedisAppliedReportGuard` — production. ``SET NX EX`` claims the
  key atomically across uvicorn workers and survives a process restart
  within the TTL window (same durability model as
  :class:`backend.integrations.feishu.dedupe.RedisEventDedupe`). Reports
  are same-day artifacts (intraday ``valid_until`` + 16:00 post-close
  cut-off), so a 24h TTL covers every redelivery window.
* :class:`InMemoryAppliedReportGuard` — tests / single-process dev. A
  bounded LRU with lazy TTL purge so a long session does not balloon.

LLM red line: this module never imports ``backend.{llm,agents,mirofish}``
— it is a pure-Python idempotency primitive over already-validated ids.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Protocol


class AppliedReportGuard(Protocol):
    """Claim/release idempotency primitive — atomic claim by contract."""

    async def claim(self, report_id: str) -> bool:
        """Return ``True`` iff this is the first claim of ``report_id``."""
        ...

    async def release(self, report_id: str) -> None:
        """Undo a claim so a failed apply can be retried."""
        ...


class _RedisLike(Protocol):
    """Minimal async Redis API used by :class:`RedisAppliedReportGuard`."""

    async def set(  # noqa: A003 — Redis idiom; literal command name
        self,
        name: str,
        value: str,
        *,
        ex: int | None = ...,
        nx: bool | None = ...,
    ) -> bool | None: ...

    async def delete(self, name: str) -> int: ...


class RedisAppliedReportGuard:
    """Atomic, restart-durable report-id guard using Redis ``SET NX EX``.

    Args:
        redis: async Redis client (``redis.asyncio.Redis`` or compatible).
        prefix: key prefix; ``broker:applied_report:`` keeps the
            namespace obvious in ``redis-cli KEYS``.
        ttl_seconds: TTL window. Reports are same-day; 24h absorbs every
            Feishu/frontend redelivery without leaking keys.
    """

    def __init__(
        self,
        redis: _RedisLike,
        *,
        prefix: str = "broker:applied_report:",
        ttl_seconds: int = 86_400,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._redis = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    async def claim(self, report_id: str) -> bool:
        if not report_id:
            raise ValueError("report_id must not be empty")
        # SET key value NX EX ttl — truthy when the key did not exist
        # (we claimed it), falsy when another caller already set it.
        result = await self._redis.set(
            self._prefix + report_id, "1", ex=self._ttl, nx=True
        )
        return bool(result)

    async def release(self, report_id: str) -> None:
        if not report_id:
            raise ValueError("report_id must not be empty")
        await self._redis.delete(self._prefix + report_id)


class InMemoryAppliedReportGuard:
    """LRU-backed in-memory guard for tests and single-process dev.

    Args:
        max_entries: number of report_ids retained; the LRU evicts the
            oldest once full so a long-running process does not grow
            unbounded.
        ttl_seconds: per-entry TTL; expired entries are reclaimed lazily
            on the next ``claim``.
    """

    def __init__(
        self,
        *,
        max_entries: int = 8_192,
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

    async def claim(self, report_id: str) -> bool:
        if not report_id:
            raise ValueError("report_id must not be empty")
        now = time.monotonic()
        async with self._lock:
            self._purge_expired(now)
            if report_id in self._entries:
                # Fixed-window TTL: a duplicate claim does NOT refresh the
                # entry's timestamp or position. Moving it to the end with
                # its stale timestamp would break ``_purge_expired``'s
                # ordering assumption (it stops at the first non-expired
                # entry), letting a stale claim outlive its TTL and suppress
                # a legitimate retry forever (Codex U-D4 P3).
                return False
            self._entries[report_id] = now
            if len(self._entries) > self._max:
                self._entries.popitem(last=False)
            return True

    async def release(self, report_id: str) -> None:
        if not report_id:
            raise ValueError("report_id must not be empty")
        async with self._lock:
            self._entries.pop(report_id, None)

    def _purge_expired(self, now: float) -> None:
        cutoff = now - self._ttl
        for key, ts in list(self._entries.items()):
            if ts < cutoff:
                del self._entries[key]
            else:
                break  # insertion order — first non-expired wins


__all__ = [
    "AppliedReportGuard",
    "InMemoryAppliedReportGuard",
    "RedisAppliedReportGuard",
]
