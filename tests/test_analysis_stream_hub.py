"""Tests for AnalysisStreamHub lifecycle guarantees (R1 codex fixes).

Covers C4 (atomic subscribe+snapshot), W2 (slow consumer drop sentinel),
and W3 (shutdown awaits cancelled tasks).
"""

from __future__ import annotations

import asyncio

import pytest

from backend.services.analysis_stream import AnalysisStreamHub, Job


def _fake_event(event_type: str = "agent_completed") -> dict:
    return {
        "event_type": event_type,
        "agent": "news_crawler",
        "round": 0,
        "content": "x",
        "timestamp": "t",
    }


class TestSubscribeReturnsSnapshot:
    @pytest.mark.asyncio
    async def test_returns_job_queue_snapshot_tuple(self) -> None:
        hub = AnalysisStreamHub()
        job = hub.create_job(stock_code="600519", max_debate_rounds=2)
        await hub.push(job.job_id, _fake_event())
        await hub.push(job.job_id, _fake_event("agent_started"))

        result = hub.subscribe(job.job_id)
        assert result is not None
        job_ref, queue, snapshot = result
        assert job_ref.job_id == job.job_id
        assert queue is not None
        assert len(snapshot) == 2
        assert snapshot[0]["event_type"] == "agent_completed"
        assert snapshot[1]["event_type"] == "agent_started"

    @pytest.mark.asyncio
    async def test_snapshot_does_not_receive_future_events(self) -> None:
        """New events after subscribe go to queue only, not snapshot."""
        hub = AnalysisStreamHub()
        job = hub.create_job(stock_code="600519", max_debate_rounds=2)
        await hub.push(job.job_id, _fake_event("agent_started"))

        _, queue, snapshot = hub.subscribe(job.job_id)  # type: ignore[misc]
        snapshot_len_at_subscribe = len(snapshot)

        await hub.push(job.job_id, _fake_event("agent_completed"))

        # Snapshot is frozen; queue carries only the new event.
        assert len(snapshot) == snapshot_len_at_subscribe
        new_item = queue.get_nowait()
        assert new_item is not None
        assert new_item["event_type"] == "agent_completed"

    @pytest.mark.asyncio
    async def test_terminated_job_returns_none_queue(self) -> None:
        hub = AnalysisStreamHub()
        job = hub.create_job(stock_code="600519", max_debate_rounds=2)
        await hub.push(job.job_id, _fake_event("pipeline_completed"))
        # Hub finalizes automatically on terminal push.
        assert job.terminated

        _, queue, snapshot = hub.subscribe(job.job_id)  # type: ignore[misc]
        assert queue is None
        assert snapshot[-1]["event_type"] == "pipeline_completed"

    @pytest.mark.asyncio
    async def test_subscribe_unknown_job(self) -> None:
        hub = AnalysisStreamHub()
        assert hub.subscribe("nope") is None


class TestSlowConsumerDropSentinel:
    @pytest.mark.asyncio
    async def test_full_queue_subscriber_gets_none_sentinel(self) -> None:
        """On QueueFull, hub drains + posts None so stream loop exits."""
        hub = AnalysisStreamHub(subscriber_queue_size=2)
        job = hub.create_job(stock_code="600519", max_debate_rounds=2)

        _, queue, _ = hub.subscribe(job.job_id)  # type: ignore[misc]
        assert queue is not None

        # Fill the queue to capacity, then push one more — that triggers drop.
        await hub.push(job.job_id, _fake_event())
        await hub.push(job.job_id, _fake_event())
        # Third push: queue is full → consumer dropped, drained, None posted.
        await hub.push(job.job_id, _fake_event())

        # After drop, the queue should contain exactly the None sentinel.
        item = queue.get_nowait()
        assert item is None
        assert queue.empty()

        # Subscriber removed from job.subscribers
        assert queue not in job.subscribers


class TestShutdownAwaitsTasks:
    @pytest.mark.asyncio
    async def test_shutdown_gathers_cancelled_tasks(self) -> None:
        hub = AnalysisStreamHub()
        job = hub.create_job(stock_code="600519", max_debate_rounds=2)

        started = asyncio.Event()
        cancelled_observed = asyncio.Event()

        async def long_running() -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled_observed.set()
                raise

        task = asyncio.create_task(long_running())
        hub.attach_task(job.job_id, task)
        await started.wait()

        await hub.shutdown()

        # After shutdown returns, the background task must have unwound.
        assert task.done()
        assert cancelled_observed.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_swallows_task_exceptions(self) -> None:
        """A task that dies during shutdown must not bubble up."""
        hub = AnalysisStreamHub()
        job = hub.create_job(stock_code="600519", max_debate_rounds=2)

        async def kamikaze() -> None:
            raise RuntimeError("boom")

        task = asyncio.create_task(kamikaze())
        hub.attach_task(job.job_id, task)
        # Let the task actually run and raise before shutdown.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Must not raise.
        await hub.shutdown()
        assert task.done()
