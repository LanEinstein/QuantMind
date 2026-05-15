"""Tests for the P1-5 §1.1 five-freeze-source system-status endpoint.

Locks the locked invariants of ``backend.api.system_status``:

* Exactly the five locked source names appear in ``data.sources``.
* No top-level ``frozen`` boolean is emitted (P1-5 §2 redline 4 forbids
  the aggregation).
* Each probe degrades to ``status="unavailable"`` when its backing
  state is not wired into ``app.state``.
* When every probe is wired the endpoint reports their individual
  ``active`` flags and the aggregate ``any_active`` is the *or* of them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.system_status import FREEZE_SOURCE_NAMES
from backend.broker.models import CircuitBreakerConfig
from backend.broker.scheduler import EodPipelineFreezeState
from backend.main import app
from backend.risk.circuit_breaker import CircuitBreaker
from backend.services.mode_router import ModeSwitchState

_STATE_KEYS = (
    "mode_router",
    "reconciliation_ticket_state",
    "circuit_breaker",
    "last_data_quality_state",
    "eod_pipeline_freeze_state",
    "broker_scheduler",
)


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    """Strip any freeze-source state left on ``app.state`` by other tests.

    Other test modules (test_api_trading.py, test_api_monitoring.py) wire
    ``circuit_breaker`` etc. onto ``app.state`` and never tear it down,
    so without this hygiene fixture our ``unavailable`` assertions would
    flake depending on test ordering.
    """
    for key in _STATE_KEYS:
        if hasattr(app.state, key):
            delattr(app.state, key)


@pytest.fixture()
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class _StubModeRouter:
    def __init__(self, state: ModeSwitchState) -> None:
        self.mode_state = state


class _StubReconciliationTicketState:
    def __init__(
        self,
        *,
        has_open: bool,
        ticket_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        self._has_open = has_open
        self._ticket_id = ticket_id
        self._reason = reason

    def has_open_ticket(self) -> bool:
        return self._has_open

    def open_ticket_id(self) -> str | None:
        return self._ticket_id

    def reason(self) -> str | None:
        return self._reason


class _StubDataQualityState:
    def __init__(self, *, acceptable: bool, code: str, reason: str | None) -> None:
        self._acceptable = acceptable
        self.stock_code = code
        self._reason = reason

    def is_acceptable_for_buy_sell(self) -> bool:
        return self._acceptable

    def degradation_reason(self) -> str | None:
        return self._reason


class TestUnavailableWhenStateMissing:
    @pytest.mark.asyncio
    async def test_returns_five_sources_all_unavailable(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/system-status/freeze-sources")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        data = body["data"]
        sources = data["sources"]
        assert [s["name"] for s in sources] == list(FREEZE_SOURCE_NAMES)
        for source in sources:
            assert source["status"] == "unavailable"
            assert source["active"] is False
        assert data["any_active"] is False
        assert data["any_unavailable"] is True
        assert "frozen" not in data, (
            "P1-5 §2 redline 4 forbids a top-level aggregated frozen flag"
        )

    @pytest.mark.asyncio
    async def test_response_envelope_is_locked(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/system-status/freeze-sources")
        body = resp.json()
        assert set(body.keys()) == {"status", "data", "error"}
        assert set(body["data"].keys()) == {
            "sources",
            "any_active",
            "any_unavailable",
            "timestamp",
        }


class TestModeSwitchActive:
    @pytest.mark.asyncio
    async def test_active_mode_switch_reports_active_with_context(
        self,
        client: AsyncClient,
    ) -> None:
        state = ModeSwitchState()
        state.activate(
            from_mode="simulation_auto",
            to_mode="feishu_interactive",
            reason="acceptance_pass",
            initiated_by="OPERATOR",
            when=dt.datetime(2026, 5, 15, 10, 0, tzinfo=dt.UTC),
        )
        app.state.mode_router = _StubModeRouter(state)

        resp = await client.get("/api/system-status/freeze-sources")
        body = resp.json()
        ms = next(
            s for s in body["data"]["sources"] if s["name"] == "mode_switch"
        )
        assert ms["status"] == "ok"
        assert ms["active"] is True
        assert ms["reason"] == "acceptance_pass"
        assert ms["context"]["from_mode"] == "simulation_auto"
        assert ms["context"]["to_mode"] == "feishu_interactive"
        assert body["data"]["any_active"] is True


class TestReconciliationTicketActive:
    @pytest.mark.asyncio
    async def test_open_ticket_reports_active(
        self,
        client: AsyncClient,
    ) -> None:
        app.state.reconciliation_ticket_state = _StubReconciliationTicketState(
            has_open=True,
            ticket_id="RECON-20260515-001",
            reason="cash_diff_above_threshold",
        )

        resp = await client.get("/api/system-status/freeze-sources")
        body = resp.json()
        rt = next(
            s for s in body["data"]["sources"] if s["name"] == "reconciliation_ticket"
        )
        assert rt["status"] == "ok"
        assert rt["active"] is True
        assert rt["ticket_id"] == "RECON-20260515-001"
        assert rt["reason"] == "cash_diff_above_threshold"


class TestCircuitBreakerActive:
    @pytest.mark.asyncio
    async def test_halted_breaker_reports_active(
        self,
        client: AsyncClient,
    ) -> None:
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                daily_loss_limit_pct=0.05,
                consecutive_loss_count=3,
                cooldown_minutes=60,
            )
        )
        # Use wall-clock now so the 60-minute cooldown has not expired by
        # the time the endpoint reads ``is_halted()``.
        breaker.record_trade_result(-0.06, now=dt.datetime.now(tz=dt.UTC))
        app.state.circuit_breaker = breaker

        resp = await client.get("/api/system-status/freeze-sources")
        body = resp.json()
        cb = next(
            s for s in body["data"]["sources"] if s["name"] == "circuit_breaker"
        )
        assert cb["status"] == "ok"
        assert cb["active"] is True
        assert cb["reason"] == "daily_loss_or_consecutive_losses"
        assert cb["halted_at"] is not None


class TestDataQualityActive:
    @pytest.mark.asyncio
    async def test_breach_reports_active(self, client: AsyncClient) -> None:
        app.state.last_data_quality_state = _StubDataQualityState(
            acceptable=False,
            code="600519",
            reason="primary_quote_stale_8s>5s",
        )

        resp = await client.get("/api/system-status/freeze-sources")
        body = resp.json()
        dq = next(
            s for s in body["data"]["sources"] if s["name"] == "data_quality"
        )
        assert dq["status"] == "ok"
        assert dq["active"] is True
        assert dq["code"] == "600519"
        assert dq["reason"] == "primary_quote_stale_8s>5s"


class TestEodPipelineActive:
    @pytest.mark.asyncio
    async def test_eod_freeze_reports_active(self, client: AsyncClient) -> None:
        freeze = EodPipelineFreezeState()
        freeze.record_failure(
            reason="checksum_mismatch",
            trade_date="20260515",
            when=dt.datetime(2026, 5, 15, 16, 5, tzinfo=dt.UTC),
        )
        app.state.eod_pipeline_freeze_state = freeze

        resp = await client.get("/api/system-status/freeze-sources")
        body = resp.json()
        eod = next(
            s for s in body["data"]["sources"] if s["name"] == "eod_pipeline"
        )
        assert eod["status"] == "ok"
        assert eod["active"] is True
        assert eod["reason"] == "checksum_mismatch"
        assert eod["trade_date"] == "20260515"
        assert eod["raised_at"] is not None


class TestMultipleSourcesIndependent:
    @pytest.mark.asyncio
    async def test_two_sources_active_both_surface_independently(
        self,
        client: AsyncClient,
    ) -> None:
        """P1-5 §2 redline 4 — every source stays independent."""
        # mode_switch active
        state = ModeSwitchState()
        state.activate(
            from_mode="simulation_auto",
            to_mode="feishu_interactive",
            reason="acceptance_pass",
            initiated_by="OPERATOR",
            when=dt.datetime(2026, 5, 15, 10, 0, tzinfo=dt.UTC),
        )
        app.state.mode_router = _StubModeRouter(state)
        # eod_pipeline active
        freeze = EodPipelineFreezeState()
        freeze.record_failure(
            reason="checksum_mismatch",
            trade_date="20260515",
            when=dt.datetime(2026, 5, 15, 16, 5, tzinfo=dt.UTC),
        )
        app.state.eod_pipeline_freeze_state = freeze

        resp = await client.get("/api/system-status/freeze-sources")
        body = resp.json()
        actives = {
            s["name"]: s["active"] for s in body["data"]["sources"]
        }
        assert actives["mode_switch"] is True
        assert actives["eod_pipeline"] is True
        assert actives["circuit_breaker"] is False
        assert body["data"]["any_active"] is True


class TestProbeFailureIsolation:
    @pytest.mark.asyncio
    async def test_a_failing_probe_does_not_crash_the_endpoint(
        self,
        client: AsyncClient,
    ) -> None:
        class _BrokenBreaker:
            def is_halted(self) -> bool:
                raise RuntimeError("intentional probe failure")

        app.state.circuit_breaker = _BrokenBreaker()
        resp = await client.get("/api/system-status/freeze-sources")
        assert resp.status_code == 200
        body = resp.json()
        cb = next(
            s for s in body["data"]["sources"] if s["name"] == "circuit_breaker"
        )
        assert cb["status"] == "unavailable"
        assert cb["active"] is False


class TestLockedSourceNames:
    def test_freeze_source_names_tuple_is_locked(self) -> None:
        assert FREEZE_SOURCE_NAMES == (
            "mode_switch",
            "reconciliation_ticket",
            "circuit_breaker",
            "data_quality",
            "eod_pipeline",
        )

    @pytest.mark.asyncio
    async def test_response_uses_exactly_those_names(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/system-status/freeze-sources")
        body = resp.json()
        names = [s["name"] for s in body["data"]["sources"]]
        assert names == list(FREEZE_SOURCE_NAMES)


def _resp_to_payload(body: dict[str, Any]) -> dict[str, Any]:
    return body["data"]
