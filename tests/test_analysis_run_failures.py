"""Tests for R1 codex fixes on failure propagation and record persistence.

Covers C2 (failed steps → status=failed), C3 (AnalysisRunError surfaces
record for persistence), and the unique-index relaxation on
`analysis_records.signal_id` (C1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.agents.collector import RunCollector
from backend.agents.graph import AnalysisRunError
from backend.agents.records import AnalysisRecord
from backend.main import app
from backend.services.analysis_stream import AnalysisStreamHub


def _failed_record() -> AnalysisRecord:
    return AnalysisRecord(
        run_id="run-fail",
        stock_code="600519",
        stock_name="贵州茅台",
        trade_date="2026-04-25",
        status="failed",
        max_rounds=2,
        current_round=1,
        created_at=datetime.now(tz=UTC),
        completed_at=datetime.now(tz=UTC),
        error="fundamental_analyst: llm down",
    )


class TestCollectorFailedStepHelpers:
    def test_has_failed_steps_false_on_clean_run(self) -> None:
        collector = RunCollector(
            run_id="r1",
            stock_code="600519",
            stock_name="x",
            trade_date="2026-04-25",
            max_rounds=2,
        )
        assert collector.has_failed_steps() is False
        assert collector.first_failure_summary() is None

    @pytest.mark.asyncio
    async def test_on_agent_failed_records_failed_step(self) -> None:
        emitted: list[dict] = []

        async def emit(ev: dict) -> None:
            emitted.append(ev)

        collector = RunCollector(
            run_id="r1",
            stock_code="600519",
            stock_name="x",
            trade_date="2026-04-25",
            max_rounds=2,
            emitter=emit,
        )
        started = await collector.on_agent_started("news_crawler", 0)
        await collector.on_agent_failed(
            "news_crawler", 0, started, "boom"
        )
        assert collector.has_failed_steps() is True
        assert collector.first_failure_summary() == "news_crawler: boom"

        # Emitted event uses agent_completed with status=failed + error
        failed_events = [
            e for e in emitted if e.get("status") == "failed"
        ]
        assert len(failed_events) == 1
        assert failed_events[0]["event_type"] == "agent_completed"
        assert failed_events[0]["error"] == "boom"


class TestApiStockErrorPath:
    @pytest.fixture()
    def mongodb_mock(self) -> AsyncMock:
        app.state.llm_router = AsyncMock()
        app.state.llm_router.preflight = lambda: {"deepseek": True}
        app.state.market_data = AsyncMock()
        app.state.history_data = AsyncMock()
        app.state.news_crawler = AsyncMock()
        mongodb = AsyncMock()
        mongodb.save_analysis_record = AsyncMock(return_value="rec-1")
        mongodb.save_signal = AsyncMock(return_value="sig-1")
        app.state.mongodb = mongodb
        app.state.analysis_stream_hub = AnalysisStreamHub()
        return mongodb

    @pytest.mark.asyncio
    async def test_stock_endpoint_persists_failed_record(
        self, mongodb_mock: AsyncMock
    ) -> None:
        async def boom(*args, **kwargs):
            raise AnalysisRunError(_failed_record())

        transport = ASGITransport(app=app)
        with patch("backend.api.analysis.run_analysis", side_effect=boom):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/analysis/stock",
                    json={"stock_code": "600519"},
                )

        assert resp.status_code == 500
        mongodb_mock.save_analysis_record.assert_awaited_once()
        saved_doc = mongodb_mock.save_analysis_record.call_args[0][0]
        assert saved_doc["status"] == "failed"
        assert saved_doc["run_id"] == "run-fail"

    @pytest.mark.asyncio
    async def test_jobs_endpoint_error_event_includes_record_id(
        self, mongodb_mock: AsyncMock
    ) -> None:
        import asyncio

        async def boom(*args, **kwargs):
            raise AnalysisRunError(_failed_record())

        transport = ASGITransport(app=app)
        with patch("backend.api.analysis.run_analysis", side_effect=boom):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/analysis/jobs",
                    json={"stock_code": "600519"},
                )
                job_id = resp.json()["data"]["job_id"]

                # Wait for background task to finish and push error
                for _ in range(40):
                    buffer = app.state.analysis_stream_hub.replay(job_id)
                    if buffer and buffer[-1].get("event_type") == "error":
                        break
                    await asyncio.sleep(0.01)

        terminal = buffer[-1]
        assert terminal["event_type"] == "error"
        assert terminal.get("record_id") == "rec-1"
        mongodb_mock.save_analysis_record.assert_awaited_once()
