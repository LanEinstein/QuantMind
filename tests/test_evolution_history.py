"""AD-003 — GET /api/evolution/history endpoint (read-only, fail-open)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.api.evolution import get_evolution_history


class _State:
    def __init__(self, lock_path: Path | None) -> None:
        self.mongodb = None
        if lock_path is not None:
            self.live_artifacts_lock_path = str(lock_path)


class _App:
    def __init__(self, lock_path: Path | None) -> None:
        self.state = _State(lock_path)


class _Req:
    def __init__(self, lock_path: Path | None = None) -> None:
        self.app = _App(lock_path)


class TestEvolutionHistory:
    @pytest.mark.asyncio
    async def test_unwired_returns_empty_200(self, tmp_path: Path) -> None:
        # Point the manifest override at a nonexistent file so the
        # repo's real lockfile doesn't leak into the "no data" assertion.
        missing = tmp_path / "nope.lock.json"
        resp = await get_evolution_history(
            _Req(missing), limit=50  # type: ignore[arg-type]
        )
        assert resp["status"] == "ok"
        data = resp["data"]
        assert data["source"] == "unavailable"
        assert data["experiments"] == []
        assert data["intents"] == []
        assert data["current_manifest"] is None

    @pytest.mark.asyncio
    async def test_reads_current_manifest(self, tmp_path: Path) -> None:
        lock = tmp_path / "live_artifacts.lock.json"
        lock.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "updated_at": "2026-06-11T00:00:00+00:00",
                    "approved": {
                        "prompt_version": ["a" * 64],
                        "strategy_code": [],
                    },
                }
            ),
            encoding="utf-8",
        )
        resp = await get_evolution_history(_Req(lock), limit=50)  # type: ignore[arg-type]
        manifest = resp["data"]["current_manifest"]
        assert manifest is not None
        assert manifest["version"] == "1.0"
        assert manifest["approved"]["prompt_version"] == ["a" * 64]

    @pytest.mark.asyncio
    async def test_malformed_lockfile_fail_open(self, tmp_path: Path) -> None:
        lock = tmp_path / "bad.lock.json"
        lock.write_text("{not json", encoding="utf-8")
        resp = await get_evolution_history(_Req(lock), limit=50)  # type: ignore[arg-type]
        assert resp["data"]["current_manifest"] is None
