"""In-memory stream hub for live agent analysis jobs.

Each POST /api/analysis/jobs creates a Job. The background task then runs
the 9-agent pipeline and pushes SSE events into the Job's bounded replay
buffer. Subscribers (GET /api/analysis/stream/{job_id}) first receive the
replay buffer, then stream new events until a terminal event or the hub
is asked to close.

Process restart drops in-flight jobs by design — the full record is still
persisted to MongoDB by run_analysis(), so nothing gets lost.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

log = structlog.get_logger(component="analysis_stream")

TERMINAL_EVENTS = frozenset({"pipeline_completed", "error"})

DEFAULT_BUFFER_SIZE = 256
DEFAULT_JOB_RETENTION_MINUTES = 10
DEFAULT_SUBSCRIBER_QUEUE = 128
# Per-job subscriber cap. The frontend opens at most one EventSource at
# a time, so >5 subscribers on the same job is almost certainly a bug
# (or a malicious client trying to fan out a single expensive run into
# many open streams). Reject excess attempts at the API layer.
DEFAULT_MAX_SUBSCRIBERS_PER_JOB = 5
# Process-wide cap on concurrent live jobs. Each /jobs request kicks off
# the full 9-agent LangGraph pipeline (8-12 LLM calls, ~30-90s of wall
# time, billed against shell-env keys). A small default here means a
# bot or buggy client cannot trivially burn the daily LLM budget.
DEFAULT_MAX_ACTIVE_JOBS = 4


@dataclass
class Job:
    """State of a single live analysis run."""

    job_id: str
    stock_code: str
    max_debate_rounds: int
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    completed_at: datetime | None = None
    status: str = "running"
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(
        default_factory=list
    )
    terminated: bool = False
    record_id: str | None = None
    signal_id: str | None = None
    task: asyncio.Task | None = None

    def trim_buffer(self, max_size: int) -> None:
        if len(self.events) > max_size:
            # Drop oldest non-critical events but always keep the very
            # first agent_started and any terminal events to preserve the
            # shape of a replay.
            overflow = len(self.events) - max_size
            del self.events[1 : 1 + overflow]


class AnalysisStreamHub:
    """Registry of live analysis jobs.

    Not thread-safe; all access goes through the asyncio event loop.
    Use one hub per FastAPI application (attached to app.state).
    """

    def __init__(
        self,
        *,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        retention_minutes: int = DEFAULT_JOB_RETENTION_MINUTES,
        subscriber_queue_size: int = DEFAULT_SUBSCRIBER_QUEUE,
        max_subscribers_per_job: int = DEFAULT_MAX_SUBSCRIBERS_PER_JOB,
        max_active_jobs: int = DEFAULT_MAX_ACTIVE_JOBS,
    ) -> None:
        self._jobs: dict[str, Job] = {}
        self._buffer_size = buffer_size
        self._retention = timedelta(minutes=retention_minutes)
        self._subscriber_queue_size = subscriber_queue_size
        self._max_subscribers_per_job = max_subscribers_per_job
        self._max_active_jobs = max_active_jobs

    def active_job_count(self) -> int:
        """Number of jobs that are still running (no terminal yet)."""
        return sum(1 for j in self._jobs.values() if not j.terminated)

    def create_job(
        self, stock_code: str, max_debate_rounds: int
    ) -> Job:
        self._gc()
        job = Job(
            job_id=str(uuid.uuid4()),
            stock_code=stock_code,
            max_debate_rounds=max_debate_rounds,
        )
        self._jobs[job.job_id] = job
        log.info("stream_job_created", job_id=job.job_id, stock_code=stock_code)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def attach_task(self, job_id: str, task: asyncio.Task) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job.task = task

    async def push(self, job_id: str, event: dict[str, Any]) -> None:
        """Append an event to the job replay buffer and broadcast."""
        job = self._jobs.get(job_id)
        if job is None:
            log.warning("stream_push_missing_job", job_id=job_id)
            return

        job.events.append(event)
        job.trim_buffer(self._buffer_size)

        event_type = event.get("event_type")
        if event_type == "pipeline_completed":
            job.status = "completed"
            job.completed_at = datetime.now(tz=UTC)
            job.record_id = event.get("record_id")
            job.signal_id = event.get("signal_id")
        elif event_type == "error":
            job.status = "failed"
            job.completed_at = datetime.now(tz=UTC)

        # Broadcast. Drop to slow consumers rather than blocking the pipeline.
        # When dropping, drain the queue and post a None sentinel so the
        # subscriber's stream loop exits cleanly — otherwise it would
        # heartbeat forever waiting for a terminal event that never comes.
        dead: list[asyncio.Queue[dict[str, Any] | None]] = []
        for q in list(job.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning(
                    "stream_subscriber_dropped",
                    job_id=job_id,
                    event_type=event_type,
                )
                _drain_and_signal_close(q)
                dead.append(q)
        for q in dead:
            self._unsubscribe_queue(job, q)

        if event_type in TERMINAL_EVENTS:
            await self._finalize(job)

    async def _finalize(self, job: Job) -> None:
        """Signal all subscribers that the stream is done.

        If a subscriber's queue is completely full, drop one item to
        make room for the None sentinel. Without this, the consumer
        loop in `stream_analysis_job` would never see the terminal and
        would heartbeat forever after the real terminal event was
        already delivered (it breaks on None, not on terminal
        event_type).
        """
        if job.terminated:
            return
        job.terminated = True
        for q in list(job.subscribers):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover
                    pass
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:  # pragma: no cover
                    pass

    def _unsubscribe_queue(
        self, job: Job, q: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        try:
            job.subscribers.remove(q)
        except ValueError:
            pass

    def subscriber_count(self, job_id: str) -> int:
        """Number of live SSE subscribers attached to a job."""
        job = self._jobs.get(job_id)
        if job is None:
            return 0
        return len(job.subscribers)

    @property
    def max_subscribers_per_job(self) -> int:
        return self._max_subscribers_per_job

    @property
    def max_active_jobs(self) -> int:
        return self._max_active_jobs

    def subscribe(
        self, job_id: str
    ) -> tuple[
        Job,
        asyncio.Queue[dict[str, Any] | None] | None,
        list[dict[str, Any]],
    ] | None:
        """Register a new subscriber. Returns ``(job, queue, snapshot)``.

        - ``snapshot`` is an immutable list of events buffered at the
          moment of subscription. It is captured *before* the queue is
          attached to ``job.subscribers``, so the snapshot and the queue
          carry no overlapping events; new events arrive only on the
          queue.
        - ``queue`` is ``None`` when the job has already terminated. In
          that case the snapshot already contains the terminal event and
          there is nothing more to deliver.

        Returns ``None`` when the ``job_id`` is unknown.

        The whole body is synchronous (no await) so that snapshot
        capture and subscriber registration cannot be interleaved with
        ``push()`` — that race was what allowed late subscribers to miss
        the terminal event.
        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        snapshot = list(job.events)
        if job.terminated:
            return job, None, snapshot
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=self._subscriber_queue_size
        )
        job.subscribers.append(q)
        return job, q, snapshot

    def unsubscribe(
        self, job_id: str, q: asyncio.Queue[dict[str, Any] | None]
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        self._unsubscribe_queue(job, q)

    def replay(self, job_id: str) -> list[dict[str, Any]]:
        """Return a snapshot of buffered events for the given job."""
        job = self._jobs.get(job_id)
        if job is None:
            return []
        return list(job.events)

    def _gc(self) -> None:
        """Evict completed jobs older than retention window."""
        now = datetime.now(tz=UTC)
        stale_ids = [
            jid
            for jid, job in self._jobs.items()
            if job.completed_at is not None
            and (now - job.completed_at) > self._retention
        ]
        for jid in stale_ids:
            self._jobs.pop(jid, None)

    async def shutdown(self) -> None:
        """Cancel outstanding tasks, await them, then close subscribers.

        Awaiting the cancelled tasks via ``asyncio.gather`` ensures the
        background pipeline coroutines actually unwind before the
        application's lifespan tears down shared services (Mongo, Redis,
        LLM router); otherwise jobs could run against half-closed
        services and produce noisy crashes during shutdown.
        """
        pending: list[asyncio.Task] = []
        for job in list(self._jobs.values()):
            if job.task is not None and not job.task.done():
                job.task.cancel()
                pending.append(job.task)
            await self._finalize(job)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def _drain_and_signal_close(
    q: asyncio.Queue[dict[str, Any] | None],
) -> None:
    """Drain a slow-consumer queue and post a None sentinel.

    The subscriber's stream loop watches for ``None`` to exit. Without
    it the loop would idle on ``queue.get()`` until the request times
    out, since the dropped subscriber will never receive any further
    push (including the terminal event).
    """
    try:
        while True:
            q.get_nowait()
    except asyncio.QueueEmpty:
        pass
    try:
        q.put_nowait(None)
    except asyncio.QueueFull:  # pragma: no cover — shouldn't happen post-drain
        pass


__all__ = ["AnalysisStreamHub", "Job", "TERMINAL_EVENTS"]
