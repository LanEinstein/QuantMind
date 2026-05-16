"""H-003 — backend.services.soft_degrade_manager tests.

Coverage:
- Kimi escalation block: SETNX idempotent, daily TTL, deactivated by reset_daily
- maybe_fire_monthly_milestone: SETNX per (ym, pct) firing exactly once
- Probe failures don't crash the manager (fail-open)
- DegradeFlags snapshot composes daily/monthly/kimi state correctly
- Module isolation — no backend.{llm,agents,mirofish,data} imports
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.services.cost_guard import (
    DailyBudgetState,
    KimiBudgetState,
    MonthlyBudgetState,
)
from backend.services.soft_degrade_manager import (
    MilestoneTransition,
    SoftDegradeManager,
)


class _FakeRedis:
    """In-memory Redis stub supporting get/set(ex,nx)/delete/scan/hgetall."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.fail_on_set = False
        self.fail_on_get = False
        self.fail_on_delete = False

    async def get(self, key: str) -> str | None:
        if self.fail_on_get:
            raise RuntimeError("redis down")
        return self.store.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool | None:
        if self.fail_on_set:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        # ex is recorded but not enforced — tests pass an explicit clock.
        return True

    async def delete(self, key: str) -> int:
        if self.fail_on_delete:
            raise RuntimeError("redis down")
        existed = key in self.store
        self.store.pop(key, None)
        return 1 if existed else 0


# ----------------------------------------------------------------------
# Kimi escalation block
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kimi_block_idempotent_setnx() -> None:
    redis = _FakeRedis()
    mgr = SoftDegradeManager(redis)
    assert await mgr.is_kimi_escalation_blocked() is False
    first = await mgr.activate_kimi_escalation_block(reason="soft_70pct")
    second = await mgr.activate_kimi_escalation_block(reason="soft_70pct")
    assert first is True
    assert second is False  # SETNX guarantees one fire per day
    assert await mgr.is_kimi_escalation_blocked() is True


@pytest.mark.asyncio
async def test_reset_daily_clears_flag() -> None:
    redis = _FakeRedis()
    mgr = SoftDegradeManager(redis)
    await mgr.activate_kimi_escalation_block(reason="soft_70pct")
    assert await mgr.is_kimi_escalation_blocked() is True
    await mgr.reset_daily()
    assert await mgr.is_kimi_escalation_blocked() is False


@pytest.mark.asyncio
async def test_kimi_block_fails_open_on_redis_error() -> None:
    redis = _FakeRedis()
    redis.fail_on_set = True
    mgr = SoftDegradeManager(redis)
    ok = await mgr.activate_kimi_escalation_block(reason="x")
    assert ok is False  # fail-open — don't crash the caller


@pytest.mark.asyncio
async def test_is_kimi_blocked_fails_open() -> None:
    redis = _FakeRedis()
    redis.fail_on_get = True
    mgr = SoftDegradeManager(redis)
    assert await mgr.is_kimi_escalation_blocked() is False


@pytest.mark.asyncio
async def test_is_kimi_blocked_treats_non_string_value_as_absent() -> None:
    """Codex cycle 2 P2 regression — defend against AsyncMock-returning redis stubs.

    A unit-test ``AsyncMock`` instance is truthy and not str/bytes; the
    pre-fix code returned True when ``redis.get`` produced such a value,
    spuriously veto-ing Kimi escalation in LLMRouter tests.
    """

    class _NonStringValueRedis:
        async def get(self, _key: str) -> object:
            return object()  # truthy but not str / bytes

    mgr = SoftDegradeManager(_NonStringValueRedis())  # type: ignore[arg-type]
    assert await mgr.is_kimi_escalation_blocked() is False


# ----------------------------------------------------------------------
# Monthly milestone SETNX
# ----------------------------------------------------------------------


def _monthly(status: str, *, pct: float | None) -> MonthlyBudgetState:
    return MonthlyBudgetState(
        monthly_budget=440.0,
        spent_month=440.0 * (pct or 0.0),
        fraction=pct or 0.0,
        threshold_reached=pct,
        status=status,
    )


@pytest.mark.asyncio
async def test_milestone_fires_once_per_month() -> None:
    redis = _FakeRedis()
    mgr = SoftDegradeManager(redis)
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    transition_1 = await mgr.maybe_fire_monthly_milestone(
        _monthly("threshold_50", pct=0.50), now=now
    )
    transition_2 = await mgr.maybe_fire_monthly_milestone(
        _monthly("threshold_50", pct=0.50), now=now
    )
    assert transition_1 is not None and transition_1.fired is True
    assert transition_2 is not None and transition_2.fired is False
    assert transition_2.alert_type == "monthly_budget_50pct_reached"


@pytest.mark.asyncio
async def test_milestone_distinct_per_pct() -> None:
    redis = _FakeRedis()
    mgr = SoftDegradeManager(redis)
    now = datetime(2026, 5, 16, tzinfo=UTC)
    t50 = await mgr.maybe_fire_monthly_milestone(
        _monthly("threshold_50", pct=0.50), now=now
    )
    t80 = await mgr.maybe_fire_monthly_milestone(
        _monthly("threshold_80", pct=0.80), now=now
    )
    t100 = await mgr.maybe_fire_monthly_milestone(
        _monthly("threshold_100", pct=1.00), now=now
    )
    assert t50.fired is True  # type: ignore[union-attr]
    assert t80.fired is True  # type: ignore[union-attr]
    assert t100.fired is True  # type: ignore[union-attr]
    assert t50.alert_type == "monthly_budget_50pct_reached"  # type: ignore[union-attr]
    assert t80.alert_type == "monthly_budget_80pct_reached"  # type: ignore[union-attr]
    assert t100.alert_type == "monthly_budget_100pct_reached"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_milestone_no_threshold_returns_none() -> None:
    redis = _FakeRedis()
    mgr = SoftDegradeManager(redis)
    out = await mgr.maybe_fire_monthly_milestone(
        _monthly("ok", pct=None),
        now=datetime(2026, 5, 16, tzinfo=UTC),
    )
    assert out is None


@pytest.mark.asyncio
async def test_milestone_resets_in_next_month() -> None:
    redis = _FakeRedis()
    mgr = SoftDegradeManager(redis)
    now_may = datetime(2026, 5, 16, tzinfo=UTC)
    now_jun = datetime(2026, 6, 1, tzinfo=UTC)
    t_may = await mgr.maybe_fire_monthly_milestone(
        _monthly("threshold_50", pct=0.50), now=now_may
    )
    t_jun = await mgr.maybe_fire_monthly_milestone(
        _monthly("threshold_50", pct=0.50), now=now_jun
    )
    assert t_may.fired is True  # type: ignore[union-attr]
    assert t_jun.fired is True  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_milestone_setnx_failure_is_fail_open() -> None:
    redis = _FakeRedis()
    redis.fail_on_set = True
    mgr = SoftDegradeManager(redis)
    out = await mgr.maybe_fire_monthly_milestone(
        _monthly("threshold_50", pct=0.50),
        now=datetime(2026, 5, 16, tzinfo=UTC),
    )
    assert out is not None
    assert out.fired is False
    assert isinstance(out, MilestoneTransition)


# ----------------------------------------------------------------------
# DegradeFlags snapshot
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_composes_states() -> None:
    redis = _FakeRedis()
    mgr = SoftDegradeManager(redis)
    daily = DailyBudgetState(
        daily_budget=20.0,
        spent_today=15.0,
        soft_ceiling=14.0,
        hard_ceiling=20.0,
        remaining=5.0,
        status="soft_breach",
    )
    kimi = KimiBudgetState(
        kimi_daily_cap=4.0,
        spent_today=4.0,
        remaining=0.0,
        status="hard_breach",
    )
    monthly = _monthly("threshold_50", pct=0.50)
    await mgr.activate_kimi_escalation_block(reason="soft_70pct")
    snapshot = await mgr.snapshot(daily=daily, monthly=monthly, kimi=kimi)
    assert snapshot.kimi_escalation_blocked is True
    assert snapshot.daily_status == "soft_breach"
    assert snapshot.kimi_status == "hard_breach"
    assert snapshot.monthly_status == "threshold_50"


# ----------------------------------------------------------------------
# Module isolation
# ----------------------------------------------------------------------


def test_soft_degrade_manager_has_no_forbidden_imports() -> None:
    forbidden = {"llm", "agents", "mirofish", "data"}
    tree = ast.parse(
        Path("backend/services/soft_degrade_manager.py").read_text(encoding="utf-8")
    )
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = mod.split(".")
            if parts[:1] == ["backend"] and len(parts) >= 2 and parts[1] in forbidden:
                bad.append(f"{node.lineno}: from {mod} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if (
                    parts[:1] == ["backend"]
                    and len(parts) >= 2
                    and parts[1] in forbidden
                ):
                    bad.append(f"{node.lineno}: import {alias.name}")
    assert not bad, f"soft_degrade_manager violates isolation: {bad}"
