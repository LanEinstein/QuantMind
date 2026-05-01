"""Tests for live analysis jobs + SSE stream API (Session A2)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.agents.records import AnalysisRecord, AnalysisRunResult
from backend.main import app
from backend.services.analysis_stream import AnalysisStreamHub


def _sample_record() -> AnalysisRecord:
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC)
    return AnalysisRecord(
        run_id="run-stream",
        stock_code="600519",
        stock_name="贵州茅台",
        trade_date="2026-04-24",
        status="completed",
        max_rounds=2,
        current_round=2,
        created_at=now,
        completed_at=now,
    )


def _sample_signal():
    from backend.agents.models import TradingSignal

    return TradingSignal(
        action="买入",
        target_price=1900.0,
        confidence=0.8,
        risk_score=0.3,
        reasoning="分析完成",
        stock_code="600519",
        stock_name="贵州茅台",
        trade_date="2026-04-24",
    )


@pytest.fixture()
async def mock_state() -> AsyncMock:
    """Fresh app.state + stream hub per test.

    Teardown: shutdown the hub so any in-flight background task from a
    POST /jobs is cancelled and awaited before the next test starts —
    otherwise the patched `run_analysis` would leak across tests.

    ``llm_router.preflight`` is a MagicMock (not AsyncMock) because the
    API calls it synchronously; an AsyncMock returns a coroutine that
    silently bypasses the 503 guard.
    """
    from unittest.mock import MagicMock

    router = AsyncMock()
    router.preflight = MagicMock(return_value={"deepseek": True})
    app.state.llm_router = router
    app.state.market_data = AsyncMock()
    app.state.history_data = AsyncMock()
    app.state.news_crawler = AsyncMock()
    mongodb = AsyncMock()
    mongodb.save_signal = AsyncMock(return_value="signal-xyz")
    mongodb.save_analysis_record = AsyncMock(return_value="record-xyz")
    app.state.mongodb = mongodb
    hub = AnalysisStreamHub()
    app.state.analysis_stream_hub = hub
    try:
        yield mongodb
    finally:
        await hub.shutdown()


@pytest.fixture()
async def client(mock_state: AsyncMock) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _fake_run_analysis_factory():
    """Returns a fake run_analysis that pushes events via emitter then returns."""

    async def fake_run_analysis(
        stock_code, services, *, run_id=None, emitter=None
    ):
        for agent in ("news_crawler", "sentiment_analyst"):
            if emitter is not None:
                await emitter(
                    {
                        "event_type": "agent_started",
                        "agent": agent,
                        "round": 0,
                        "timestamp": "t",
                        "run_id": run_id,
                    }
                )
                await emitter(
                    {
                        "event_type": "agent_completed",
                        "agent": agent,
                        "round": 0,
                        "content": f"{agent} done",
                        "model_label": "",
                        "model_id": "",
                        "status": "completed",
                        "error": None,
                        "timestamp": "t",
                        "run_id": run_id,
                    }
                )
        return AnalysisRunResult(signal=_sample_signal(), record=_sample_record())

    return fake_run_analysis


class TestJobsCreation:
    @pytest.mark.asyncio
    async def test_post_jobs_returns_job_id(
        self, client: AsyncClient
    ) -> None:
        with patch(
            "backend.api.analysis.run_analysis",
            side_effect=_fake_run_analysis_factory(),
        ):
            resp = await client.post(
                "/api/analysis/jobs",
                json={"stock_code": "600519"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "job_id" in body["data"]
        assert body["data"]["status"] == "running"

    @pytest.mark.asyncio
    async def test_post_jobs_invalid_code_422(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/api/analysis/jobs", json={"stock_code": "xyz"}
        )
        assert resp.status_code == 422


class TestStreamSubscription:
    @pytest.mark.asyncio
    async def test_stream_unknown_job_404(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get("/api/analysis/stream/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_emits_agent_and_terminal_events(
        self, client: AsyncClient, mock_state: AsyncMock
    ) -> None:
        with patch(
            "backend.api.analysis.run_analysis",
            side_effect=_fake_run_analysis_factory(),
        ):
            resp = await client.post(
                "/api/analysis/jobs",
                json={"stock_code": "600519"},
            )
            job_id = resp.json()["data"]["job_id"]

            # Give the background task time to push events
            for _ in range(20):
                buffer = app.state.analysis_stream_hub.replay(job_id)
                if buffer and buffer[-1].get("event_type") == "pipeline_completed":
                    break
                await asyncio.sleep(0.01)

            buffer_types = [e["event_type"] for e in buffer]
            assert "agent_started" in buffer_types
            assert "agent_completed" in buffer_types
            assert "pipeline_completed" in buffer_types

            async with client.stream(
                "GET", f"/api/analysis/stream/{job_id}"
            ) as stream:
                assert stream.status_code == 200
                assert "text/event-stream" in stream.headers["content-type"]
                collected: list[dict] = []
                async for chunk in stream.aiter_bytes():
                    for line in chunk.decode().splitlines():
                        if line.startswith("data: "):
                            collected.append(json.loads(line[6:]))
                    if collected and collected[-1]["event_type"] == "pipeline_completed":
                        break

        seen_types = {e["event_type"] for e in collected}
        assert "pipeline_completed" in seen_types
        assert "agent_completed" in seen_types

    @pytest.mark.asyncio
    async def test_error_path_emits_error_event(
        self, client: AsyncClient
    ) -> None:
        async def boom(*args, **kwargs):
            raise RuntimeError("llm down")

        with patch(
            "backend.api.analysis.run_analysis", side_effect=boom
        ):
            resp = await client.post(
                "/api/analysis/jobs",
                json={"stock_code": "600519"},
            )
            job_id = resp.json()["data"]["job_id"]

            for _ in range(20):
                buffer = app.state.analysis_stream_hub.replay(job_id)
                if buffer and buffer[-1].get("event_type") == "error":
                    break
                await asyncio.sleep(0.01)

        types = [e["event_type"] for e in buffer]
        assert types[-1] == "error"
        assert "llm down" in buffer[-1]["message"]

    @pytest.mark.asyncio
    async def test_late_subscriber_receives_replay(
        self, client: AsyncClient
    ) -> None:
        """Subscribers connecting after terminal still see full history."""
        with patch(
            "backend.api.analysis.run_analysis",
            side_effect=_fake_run_analysis_factory(),
        ):
            resp = await client.post(
                "/api/analysis/jobs",
                json={"stock_code": "600519"},
            )
            job_id = resp.json()["data"]["job_id"]

            for _ in range(30):
                buffer = app.state.analysis_stream_hub.replay(job_id)
                if buffer and buffer[-1].get("event_type") == "pipeline_completed":
                    break
                await asyncio.sleep(0.01)

            async with client.stream(
                "GET", f"/api/analysis/stream/{job_id}"
            ) as stream:
                body = b""
                async for chunk in stream.aiter_bytes():
                    body += chunk
                    if b"pipeline_completed" in body:
                        break

        # Replay must include agent events, not only terminal
        assert b"agent_started" in body
        assert b"pipeline_completed" in body


class TestJobsToHistoryIntegration:
    """End-to-end: POST /jobs → SSE finishes → record persisted → /history."""

    @pytest.mark.asyncio
    async def test_completed_job_persists_and_shows_in_history(
        self, client: AsyncClient, mock_state: AsyncMock
    ) -> None:
        """R3 CRITICAL #2: prove the full /jobs → /history contract.

        Asserts that (a) a completed live job calls save_signal +
        save_analysis_record, (b) the terminal SSE event carries a
        non-null record_id, (c) the subsequent /history query returns
        the new run's summary.
        """
        # Make /history respond from the same AsyncMock with one entry.
        mock_state.query_analysis_records = AsyncMock(
            return_value=[
                {
                    "_id": "record-xyz",
                    "run_id": "run-stream",
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                    "trade_date": "2026-04-24",
                    "status": "completed",
                    "signal_id": "signal-xyz",
                    "decision": {
                        "action": "买入",
                        "confidence": 0.8,
                        "risk_score": 0.3,
                    },
                }
            ]
        )

        with patch(
            "backend.api.analysis.run_analysis",
            side_effect=_fake_run_analysis_factory(),
        ):
            resp = await client.post(
                "/api/analysis/jobs",
                json={"stock_code": "600519"},
            )
            job_id = resp.json()["data"]["job_id"]

            # Wait until the background task finalizes the hub.
            for _ in range(50):
                buffer = app.state.analysis_stream_hub.replay(job_id)
                if buffer and buffer[-1].get("event_type") == "pipeline_completed":
                    break
                await asyncio.sleep(0.01)

            # (a) + (b): persistence called, terminal has record_id
            mock_state.save_signal.assert_awaited()
            mock_state.save_analysis_record.assert_awaited()
            terminal = buffer[-1]
            assert terminal["event_type"] == "pipeline_completed"
            assert terminal["record_id"] == "record-xyz"
            assert terminal["signal_id"] == "signal-xyz"

            # (c): /history reflects the new run
            hist_resp = await client.get("/api/analysis/history?limit=5")

        assert hist_resp.status_code == 200
        hist_body = hist_resp.json()
        assert hist_body["status"] == "ok"
        assert len(hist_body["data"]) == 1
        assert hist_body["data"][0]["run_id"] == "run-stream"
        assert hist_body["data"][0]["signal_id"] == "signal-xyz"


class TestAtomicSubscribeRace:
    """R3 HIGH #3: reverting subscribe to separate replay/subscribe must fail.

    Reproduces the scenario where the terminal event is pushed between
    snapshot capture and queue registration. The atomic subscribe means
    the terminal is ALWAYS in the snapshot; the stream yields it and
    then exits cleanly. A regression that re-splits these steps would
    let the terminal fall through the crack and the stream would hang.
    """

    @pytest.mark.asyncio
    async def test_max_active_jobs_admission_cap_returns_429(
        self, client: AsyncClient
    ) -> None:
        """R5 admission cap: /jobs returns 429 when too many live jobs.

        The hub default `max_active_jobs` keeps a bot from kicking off
        an unbounded number of expensive 9-agent runs. We seed the hub
        beyond capacity directly (bypassing the slow real pipeline) so
        the test runs fast.
        """
        hub: AnalysisStreamHub = app.state.analysis_stream_hub
        for i in range(hub.max_active_jobs):
            hub.create_job(stock_code="600519", max_debate_rounds=2)

        with patch(
            "backend.api.analysis.run_analysis",
            side_effect=_fake_run_analysis_factory(),
        ):
            resp = await client.post(
                "/api/analysis/jobs",
                json={"stock_code": "600519"},
            )
        assert resp.status_code == 429
        assert "Too many" in resp.json()["detail"]["error"]

    @pytest.mark.asyncio
    async def test_max_subscribers_per_job_returns_429(
        self, client: AsyncClient
    ) -> None:
        """R5 fan-out cap: GET /stream returns 429 past per-job cap."""
        hub: AnalysisStreamHub = app.state.analysis_stream_hub
        job = hub.create_job(stock_code="600519", max_debate_rounds=2)

        # Saturate subscribers via direct hub.subscribe.
        for _ in range(hub.max_subscribers_per_job):
            hub.subscribe(job.job_id)

        resp = await client.get(f"/api/analysis/stream/{job.job_id}")
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_terminal_event_after_buffered_events_still_reaches_subscriber(
        self, client: AsyncClient
    ) -> None:
        hub: AnalysisStreamHub = app.state.analysis_stream_hub
        job = hub.create_job(stock_code="600519", max_debate_rounds=2)

        # Pre-populate the buffer with some non-terminal events, then
        # push a terminal event — this simulates a job that finished
        # before a subscriber connects.
        await hub.push(
            job.job_id,
            {
                "event_type": "agent_started",
                "agent": "news_crawler",
                "round": 0,
                "timestamp": "t0",
                "run_id": job.job_id,
            },
        )
        await hub.push(
            job.job_id,
            {
                "event_type": "agent_completed",
                "agent": "news_crawler",
                "round": 0,
                "content": "done",
                "status": "completed",
                "error": None,
                "timestamp": "t1",
                "run_id": job.job_id,
            },
        )
        await hub.push(
            job.job_id,
            {
                "event_type": "pipeline_completed",
                "run_id": job.job_id,
                "record_id": "rec-late",
                "signal_id": "sig-late",
                "timestamp": "t2",
            },
        )

        # Now the job is terminated; subscribing must return snapshot
        # with the terminal event already included, and the HTTP stream
        # must terminate promptly without hanging on the queue.
        async with client.stream(
            "GET", f"/api/analysis/stream/{job.job_id}"
        ) as stream:
            body = b""
            async for chunk in stream.aiter_bytes():
                body += chunk

        assert b"pipeline_completed" in body
        assert b"agent_completed" in body
        # record_id flows through so the client can fetch the record
        assert b"rec-late" in body
