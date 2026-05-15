"""Tests for /api/acceptance/latest (G-007).

Lock the P0-6 §2 redline 5 contract that ``can_switch_to_feishu_on``
mirrors :meth:`AcceptanceService.can_switch_to_feishu_on` rather than
re-deriving it from the outcome enum, so a future bug that returns the
wrong outcome but the right boolean (or vice versa) is caught.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app
from backend.services.acceptance_report import (
    AcceptanceMetric,
    AcceptanceOutcome,
    AcceptanceReport,
    AcceptanceService,
    InMemoryAcceptanceRepository,
)


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    if hasattr(app.state, "acceptance_service"):
        delattr(app.state, "acceptance_service")


@pytest.fixture()
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _passing_metric(name: str = "instruction_completion_rate") -> AcceptanceMetric:
    return AcceptanceMetric(
        name=name,
        value=0.98,
        threshold=0.95,
        passed=True,
        direction="at_least",
    )


def _failing_metric(name: str = "instruction_completion_rate") -> AcceptanceMetric:
    return AcceptanceMetric(
        name=name,
        value=0.80,
        threshold=0.95,
        passed=False,
        direction="at_least",
    )


def _passing_report() -> AcceptanceReport:
    return AcceptanceReport(
        computed_at=dt.datetime(2026, 5, 15, 16, 0, 30, tzinfo=dt.UTC),
        trade_date="2026-05-15",
        window_start="2026-03-10",
        window_end="2026-05-15",
        trading_days_in_window=45,
        outcome=AcceptanceOutcome.PASS,
        metrics=tuple(
            _passing_metric(name)
            for name in (
                "instruction_completion_rate",
                "execution_report_accuracy_rate",
                "data_missing_rate",
                "llm_timeout_rate",
                "signal_generation_rate",
                "max_drawdown_pct",
                "pnl_cny",
                "csi300_excess_pct",
            )
        ),
    )


def _failing_report() -> AcceptanceReport:
    return AcceptanceReport(
        computed_at=dt.datetime(2026, 5, 15, 16, 0, 30, tzinfo=dt.UTC),
        trade_date="2026-05-15",
        window_start="2026-03-10",
        window_end="2026-05-15",
        trading_days_in_window=45,
        outcome=AcceptanceOutcome.FAIL,
        metrics=(_failing_metric(), _passing_metric("pnl_cny")),
    )


class TestUnwired:
    @pytest.mark.asyncio
    async def test_no_service_returns_unavailable(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/acceptance/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["service_status"] == "unavailable"
        assert body["data"]["report"] is None
        assert body["data"]["can_switch_to_feishu_on"] is False


class TestEmptyService:
    @pytest.mark.asyncio
    async def test_wired_service_with_no_rows_returns_null_report(
        self,
        client: AsyncClient,
    ) -> None:
        repo = InMemoryAcceptanceRepository()
        app.state.acceptance_service = AcceptanceService(repository=repo)
        resp = await client.get("/api/acceptance/latest")
        body = resp.json()
        assert body["data"]["service_status"] == "ok"
        assert body["data"]["report"] is None
        assert body["data"]["can_switch_to_feishu_on"] is False


class TestPassingReport:
    @pytest.mark.asyncio
    async def test_passing_report_surfaces_can_switch_true(
        self,
        client: AsyncClient,
    ) -> None:
        repo = InMemoryAcceptanceRepository()
        service = AcceptanceService(repository=repo)
        await service.upsert(_passing_report())
        app.state.acceptance_service = service

        resp = await client.get("/api/acceptance/latest")
        body = resp.json()
        data = body["data"]
        assert data["service_status"] == "ok"
        assert data["report"]["outcome"] == "PASS"
        assert data["report"]["trading_days_in_window"] == 45
        assert data["can_switch_to_feishu_on"] is True
        assert len(data["report"]["metrics"]) == 8
        for metric in data["report"]["metrics"]:
            assert metric["direction"] in ("at_least", "at_most")
            assert "passed" in metric


class TestFailingReport:
    @pytest.mark.asyncio
    async def test_failing_report_surfaces_can_switch_false(
        self,
        client: AsyncClient,
    ) -> None:
        repo = InMemoryAcceptanceRepository()
        service = AcceptanceService(repository=repo)
        await service.upsert(_failing_report())
        app.state.acceptance_service = service

        resp = await client.get("/api/acceptance/latest")
        body = resp.json()
        data = body["data"]
        assert data["report"]["outcome"] == "FAIL"
        assert data["can_switch_to_feishu_on"] is False


class TestProbeFailureIsolation:
    @pytest.mark.asyncio
    async def test_service_explosion_falls_back_to_unavailable(
        self,
        client: AsyncClient,
    ) -> None:
        class _Broken:
            async def latest(self) -> AcceptanceReport | None:
                raise RuntimeError("intentional failure")

            async def can_switch_to_feishu_on(self) -> bool:
                raise RuntimeError("intentional failure")

        # bypass isinstance check by setting raw protocol object
        app.state.acceptance_service = _Broken()
        resp = await client.get("/api/acceptance/latest")
        assert resp.status_code == 200
        # The isinstance gate keeps a non-AcceptanceService object out, so we
        # see ``unavailable`` (and crucially not a 500).
        assert resp.json()["data"]["service_status"] == "unavailable"
