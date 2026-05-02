"""Tests for the Phase 5B-T02 watchlist category API endpoints."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.main import app
from backend.services.watchlist_policy import (
    WatchlistPolicy,
    load_policy,
)

YAML_TEMPLATE = """
fast:
  cron: "0 9,11,13,15 * * mon-fri"
  pipeline: fast_pipeline
  max_debate_rounds: 1
  pipeline_timeout_seconds: 480
  default_codes: ["600519"]
slow:
  cron: "0 9 * * mon-fri"
  pipeline: slow_pipeline
  max_debate_rounds: 2
  pipeline_timeout_seconds: 900
  default_codes: ["000858"]
overrides:
  "300750": slow
default_category: slow
policy_version: 1
"""


@pytest.fixture()
def policy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "policy.yaml"
    p.write_text(YAML_TEMPLATE, encoding="utf-8")
    monkeypatch.setenv("QUANTMIND_WATCHLIST_POLICY_PATH", str(p))
    return p


@pytest.fixture()
def loaded_policy(policy_file: Path) -> WatchlistPolicy:
    return load_policy(policy_file)


@pytest.fixture()
def app_state_with_policy(loaded_policy: WatchlistPolicy):
    """Wire a watchlist + scheduler stub into app.state for API tests.

    Snapshot/restore the relevant ``app.state`` keys so a failed test
    in this file cannot poison subsequent tests in the suite (Codex
    R3 MEDIUM #6 — fixture pollution risk).
    """
    keys = ("watchlist", "watchlist_policy", "analysis_scheduler")
    snapshot = {k: getattr(app.state, k, None) for k in keys}

    watchlist = AsyncMock()
    watchlist.list_stocks = AsyncMock(
        return_value=[
            {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
            {"stock_code": "000858", "stock_name": "五粮液", "active": True},
            {"stock_code": "300750", "stock_name": "宁德时代", "active": True},
        ]
    )
    scheduler = MagicMock()
    scheduler.update_policy = MagicMock()
    app.state.watchlist = watchlist
    app.state.watchlist_policy = loaded_policy
    app.state.analysis_scheduler = scheduler

    yield

    for k, v in snapshot.items():
        if v is None:
            if hasattr(app.state, k):
                delattr(app.state, k)
        else:
            setattr(app.state, k, v)


@pytest.fixture()
async def client(app_state_with_policy: None) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestGetWatchlistPolicy:
    @pytest.mark.asyncio
    async def test_returns_policy_and_assignments(
        self, client: AsyncClient
    ) -> None:
        r = await client.get("/api/watchlist/policy")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        data = body["data"]
        assert data["policy"]["fast"]["max_debate_rounds"] == 1
        assert data["policy"]["slow"]["pipeline_timeout_seconds"] == 900
        assert data["assignments"] == {
            "600519": "fast",
            "000858": "slow",
            "300750": "slow",
        }

    @pytest.mark.asyncio
    async def test_503_when_policy_not_loaded(
        self, app_state_with_policy: None, client: AsyncClient
    ) -> None:
        app.state.watchlist_policy = None
        r = await client.get("/api/watchlist/policy")
        assert r.status_code == 503


class TestSetWatchlistCategory:
    @pytest.mark.asyncio
    async def test_pin_to_fast_persists_and_updates_scheduler(
        self,
        client: AsyncClient,
        policy_file: Path,
    ) -> None:
        r = await client.post(
            "/api/watchlist/000858/category",
            json={"category": "fast"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["data"]["category"] == "fast"
        assert body["data"]["override"] == "fast"

        # Persisted file picks up the override
        reloaded = load_policy(policy_file)
        assert reloaded.overrides["000858"] == "fast"

        # Scheduler.update_policy was called with the new policy
        scheduler = app.state.analysis_scheduler
        scheduler.update_policy.assert_called_once()
        passed = scheduler.update_policy.call_args[0][0]
        assert passed.overrides["000858"] == "fast"

        # In-memory policy on app.state is the new one
        assert app.state.watchlist_policy.overrides["000858"] == "fast"

    @pytest.mark.asyncio
    async def test_clear_override(
        self, client: AsyncClient, policy_file: Path
    ) -> None:
        r = await client.post(
            "/api/watchlist/300750/category",
            json={"category": None},
        )
        assert r.status_code == 200
        # 300750 was an override; now removed → falls back to default (slow)
        assert r.json()["data"]["category"] == "slow"
        assert r.json()["data"]["override"] is None

        reloaded = load_policy(policy_file)
        assert "300750" not in reloaded.overrides

    @pytest.mark.asyncio
    async def test_invalid_code_rejected(
        self, client: AsyncClient
    ) -> None:
        r = await client.post(
            "/api/watchlist/abc/category",
            json={"category": "fast"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_category_returns_envelope_422(
        self, client: AsyncClient
    ) -> None:
        """R2 MEDIUM: error must use the project status/data/error envelope."""
        r = await client.post(
            "/api/watchlist/600519/category",
            json={"category": "medium"},
        )
        assert r.status_code == 422
        body = r.json()
        # FastAPI HTTPException unpacks our _err detail dict directly into "detail"
        assert body["detail"]["status"] == "error"
        assert "fast" in body["detail"]["error"]
        assert "slow" in body["detail"]["error"]

    @pytest.mark.asyncio
    async def test_save_failure_returns_500_and_keeps_old_policy(
        self, client: AsyncClient, loaded_policy: WatchlistPolicy
    ) -> None:
        """R3 MEDIUM #9: an OSError on the YAML write must NOT mutate
        the in-memory policy or call scheduler.update_policy."""
        from unittest.mock import patch as _patch

        with _patch(
            "backend.api.watchlist.save_policy",
            side_effect=OSError("disk full"),
        ):
            r = await client.post(
                "/api/watchlist/600519/category",
                json={"category": "slow"},
            )
        assert r.status_code == 500
        # Generic message — no path leaked (Codex R5 LOW)
        assert r.json()["detail"]["error"] == (
            "Failed to persist watchlist policy"
        )
        # Old policy untouched
        assert app.state.watchlist_policy is loaded_policy
        # Scheduler not notified
        scheduler = app.state.analysis_scheduler
        scheduler.update_policy.assert_not_called()

    @pytest.mark.asyncio
    async def test_extra_field_rejected(self, client: AsyncClient) -> None:
        """R5 LOW: SetCategoryRequest has extra='forbid' so a typo'd
        client cannot silently send ignored fields."""
        r = await client.post(
            "/api/watchlist/600519/category",
            json={"category": "fast", "duration": 99},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_body_rejected(self, client: AsyncClient) -> None:
        """R6 MEDIUM #4: empty `{}` body must NOT silently clear an
        override — operator has to explicitly send `null`."""
        r = await client.post(
            "/api/watchlist/600519/category",
            json={},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_code_returns_404(
        self, client: AsyncClient
    ) -> None:
        """R2 MEDIUM: unknown but well-formed codes must NOT be silently
        added as overrides — operators need an actionable 404."""
        r = await client.post(
            "/api/watchlist/999999/category",
            json={"category": "fast"},
        )
        assert r.status_code == 404
        assert "active watchlist" in r.json()["detail"]["error"]
