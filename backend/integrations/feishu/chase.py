"""Chase / expiry scheduler for dispatched InstructionPlans (F-004).

P0-4 §1.1.5 lock — when an operator hasn't acknowledged a dispatched
plan within 30 minutes, the system politely re-pings. When the plan
crosses its ``valid_until`` (≤ 14:55 Asia/Shanghai per P0-3 §1.4) the
state machine transitions to ``EXPIRED`` automatically.

This module owns the per-plan timer machinery. The actual state
transition and Feishu reminder text are delegated to injected
callbacks so the orchestrator (F-005 reconciliation, BrokerScheduler)
can wire whichever side effects it needs.

Red lines:

* Cancelling a chase is idempotent (an inbound execution report
  before the 30-min mark must always cancel cleanly).
* No background tasks survive ``stop()`` — every scheduled callback is
  cancelled and awaited so a process restart starts cleanly.
* This module imports zero ``backend.llm`` / ``backend.agents`` /
  ``backend.mirofish`` — chase reminder text is composed by
  :class:`MessageRenderer` (F-002) at callback time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

log = logging.getLogger("backend.integrations.feishu.chase")


ChaseCallback = Callable[[str], Awaitable[None]]
"""Async callable invoked with ``instruction_id`` when the chase fires."""

ExpireCallback = Callable[[str], Awaitable[None]]
"""Async callable invoked with ``instruction_id`` when ``valid_until``
elapses without an execution report."""


# Defaults match P0-4 §1.1.5 + P0-3 §1.4.
DEFAULT_CHASE_AFTER = timedelta(minutes=30)


class ChaseScheduler:
    """Schedule per-plan chase reminders + valid_until expirations.

    Stateful by design — a process-wide instance tracks every
    dispatched plan that has yet to receive an execution report.

    Args:
        chase_after: silence window before the chase reminder fires.
        on_chase: async callback when the chase reminder is due.
        on_expire: async callback when the plan reaches its
            ``valid_until``. Both callbacks may execute concurrently
            and must be re-entrancy safe.
        clock: optional clock override (returns timezone-aware UTC).
            Tests pass a controllable clock to assert timing.
    """

    def __init__(
        self,
        *,
        on_chase: ChaseCallback,
        on_expire: ExpireCallback,
        chase_after: timedelta = DEFAULT_CHASE_AFTER,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if chase_after.total_seconds() <= 0:
            raise ValueError("chase_after must be positive")
        self._on_chase = on_chase
        self._on_expire = on_expire
        self._chase_after = chase_after
        self._clock = clock or _utc_now
        self._chase_tasks: dict[str, asyncio.Task[None]] = {}
        self._expire_tasks: dict[str, asyncio.Task[None]] = {}
        # P2-3: tracks every live task created by the scheduler,
        # including those mid-callback (after the per-instruction
        # entry has been popped from the chase / expire dicts). Lets
        # ``stop()`` await in-flight callbacks for clean shutdown.
        self._all_tasks: set[asyncio.Task[None]] = set()
        self._lock = asyncio.Lock()

    # -- Lifecycle ----------------------------------------------------

    async def schedule(
        self, instruction_id: str, valid_until: datetime
    ) -> None:
        """Schedule chase + expire for a freshly dispatched plan.

        Re-scheduling the same ``instruction_id`` cancels the prior
        timers so a second dispatch (e.g. operator re-pushes) does
        not double-fire.
        """
        if not instruction_id:
            raise ValueError("instruction_id must not be empty")
        already_expired = False
        async with self._lock:
            self._cancel_locked(instruction_id)
            now = self._clock()
            chase_at = now + self._chase_after
            chase_delay = max((chase_at - now).total_seconds(), 0.0)
            expire_delay = max((valid_until - now).total_seconds(), 0.0)

            if expire_delay <= 0:
                # Don't fire the expire callback while holding the lock —
                # _fire_expire re-acquires it and would deadlock. Defer
                # to after the ``async with`` block exits.
                already_expired = True
            else:
                # Only schedule the chase if it would fire before expiry —
                # otherwise the expire callback supersedes it.
                if chase_delay < expire_delay:
                    chase_task = asyncio.create_task(
                        self._run_chase(instruction_id, chase_delay),
                        name=f"chase:{instruction_id}",
                    )
                    self._chase_tasks[instruction_id] = chase_task
                    self._all_tasks.add(chase_task)
                    chase_task.add_done_callback(self._all_tasks.discard)
                expire_task = asyncio.create_task(
                    self._run_expire(instruction_id, expire_delay),
                    name=f"expire:{instruction_id}",
                )
                self._expire_tasks[instruction_id] = expire_task
                self._all_tasks.add(expire_task)
                expire_task.add_done_callback(self._all_tasks.discard)

        if already_expired:
            log.info(
                "chase_schedule_already_expired instruction_id=%s",
                instruction_id,
            )
            await self._fire_expire(instruction_id)

    async def cancel(self, instruction_id: str) -> None:
        """Cancel both chase + expire for a plan that just got a report."""
        async with self._lock:
            self._cancel_locked(instruction_id)

    async def stop(self) -> None:
        """Cancel everything; safe to call multiple times.

        P2-3 fix: tasks may have already popped themselves from
        ``_chase_tasks`` / ``_expire_tasks`` (inside ``_fire_chase`` /
        ``_fire_expire``) but still be awaiting the user callback. We
        snapshot ``_all_tasks`` so even mid-callback tasks are
        cancelled + awaited before ``stop()`` returns.
        """
        async with self._lock:
            self._chase_tasks.clear()
            self._expire_tasks.clear()
            tasks_to_drain = list(self._all_tasks)
        for task in tasks_to_drain:
            task.cancel()
        if tasks_to_drain:
            await asyncio.gather(
                *tasks_to_drain, return_exceptions=True
            )
        async with self._lock:
            self._all_tasks.clear()

    # -- Introspection (used by /api/system-status + tests) -----------

    @property
    def pending_count(self) -> int:
        return len({*self._chase_tasks, *self._expire_tasks})

    def is_tracking(self, instruction_id: str) -> bool:
        return (
            instruction_id in self._chase_tasks
            or instruction_id in self._expire_tasks
        )

    # -- Internals ----------------------------------------------------

    def _cancel_locked(self, instruction_id: str) -> None:
        chase = self._chase_tasks.pop(instruction_id, None)
        expire = self._expire_tasks.pop(instruction_id, None)
        if chase is not None:
            chase.cancel()
        if expire is not None:
            expire.cancel()

    async def _run_chase(
        self, instruction_id: str, delay: float
    ) -> None:
        try:
            await asyncio.sleep(delay)
            await self._fire_chase(instruction_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — isolate handler errors
            log.warning(
                "chase_handler_error instruction_id=%s error_class=%s",
                instruction_id,
                exc.__class__.__name__,
            )

    async def _run_expire(
        self, instruction_id: str, delay: float
    ) -> None:
        try:
            await asyncio.sleep(delay)
            await self._fire_expire(instruction_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "expire_handler_error instruction_id=%s error_class=%s",
                instruction_id,
                exc.__class__.__name__,
            )

    async def _fire_chase(self, instruction_id: str) -> None:
        async with self._lock:
            self._chase_tasks.pop(instruction_id, None)
        log.info("chase_fired instruction_id=%s", instruction_id)
        await self._on_chase(instruction_id)

    async def _fire_expire(self, instruction_id: str) -> None:
        async with self._lock:
            self._chase_tasks.pop(instruction_id, None)
            self._expire_tasks.pop(instruction_id, None)
        log.info("expire_fired instruction_id=%s", instruction_id)
        await self._on_expire(instruction_id)


def _utc_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


__all__ = [
    "DEFAULT_CHASE_AFTER",
    "ChaseScheduler",
]
