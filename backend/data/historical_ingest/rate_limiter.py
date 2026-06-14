"""Injectable sliding-window rate limiter for the offline ingest (AE-001).

Tushare Pro enforces a per-minute call frequency limit. The bulk historical
job makes thousands of full-market calls, so it must throttle. The limiter is
a sliding-window counter with **injectable** ``monotonic`` and ``sleep`` so
unit tests drive it with a fake clock (no real sleeping, deterministic). It is
the *断点续传 / 限速* half of the amendment's "rate-limit + resume + idempotent"
requirement (resume is handled by the job skipping already-stored snapshots).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

_WINDOW_SECONDS = 60.0


class RateLimiter:
    """Allow at most ``max_per_minute`` ``acquire()`` calls per rolling minute.

    Args:
        max_per_minute: Maximum calls per ``window_seconds``. ``<= 0`` disables
            throttling (every ``acquire`` returns immediately).
        monotonic: Returns a monotonically increasing seconds clock. Injected
            for tests; defaults to :func:`time.monotonic`.
        sleep: Blocks for the given seconds. Injected for tests; defaults to
            :func:`time.sleep`.
        window_seconds: Width of the sliding window (default 60s).
    """

    def __init__(
        self,
        max_per_minute: int,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        window_seconds: float = _WINDOW_SECONDS,
    ) -> None:
        self._max = max_per_minute
        self._monotonic = monotonic
        self._sleep = sleep
        self._window = window_seconds
        self._calls: deque[float] = deque()

    def _evict(self, now: float) -> None:
        """Drop call timestamps that have fallen out of the window."""
        cutoff = now - self._window
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    def acquire(self) -> None:
        """Block (via the injected ``sleep``) until a call slot is free."""
        if self._max <= 0:
            return
        now = self._monotonic()
        self._evict(now)
        if len(self._calls) >= self._max:
            # Wait until the oldest in-window call ages out.
            wait = self._window - (now - self._calls[0])
            if wait > 0:
                self._sleep(wait)
            now = self._monotonic()
            self._evict(now)
        self._calls.append(self._monotonic())


__all__ = ["RateLimiter"]
