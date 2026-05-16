"""Tests for backend.services.cost_guard daily LLM budget enforcement."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.services.cost_guard import (
    BudgetState,
    DailyBudgetExceededError,
    _classify,
    _read_env_float,
    assert_budget_allows,
    get_budget_state,
)


def _summary(total_today: float | None) -> float:
    """Return a stub spent-today float for the get_daily_spent patch."""
    return 0.0 if total_today is None else total_today


@pytest.fixture()
def patch_aggregate(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Replace get_daily_spent with a settable AsyncMock.

    Post-H-003 cost_guard reads via :func:`backend.services.cost_probe.
    get_daily_spent` (Redis-only). The legacy patch helper is renamed
    only for backwards compatibility with the existing test bodies.
    """

    mock = AsyncMock()
    monkeypatch.setattr(
        "backend.services.cost_guard.get_daily_spent", mock
    )
    return mock


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestClassify:
    """Status state machine: spent vs (soft, hard) thresholds."""

    @pytest.mark.parametrize(
        ("spent", "expected"),
        [
            (0.0, "ok"),
            (5.0, "ok"),
            (13.99, "ok"),
            (14.0, "soft_breach"),
            (19.99, "soft_breach"),
            (20.0, "hard_breach"),
            (25.0, "hard_breach"),
        ],
    )
    def test_state_machine(self, spent: float, expected: str) -> None:
        assert _classify(spent, soft=14.0, hard=20.0) == expected

    def test_zero_thresholds_treats_any_spend_as_hard_breach(self) -> None:
        # Edge case: budget=0 means everything is a hard breach.
        assert _classify(0.0, soft=0.0, hard=0.0) == "hard_breach"


class TestReadEnvFloat:
    """Tolerant env var parser used by get_budget_state."""

    def test_returns_default_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("X_TEST_BUDGET", raising=False)
        assert _read_env_float("X_TEST_BUDGET", default=20.0) == 20.0

    def test_parses_numeric_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("X_TEST_BUDGET", "12.5")
        assert _read_env_float("X_TEST_BUDGET", default=20.0) == 12.5

    def test_default_on_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("X_TEST_BUDGET", "not-a-number")
        assert _read_env_float("X_TEST_BUDGET", default=20.0) == 20.0

    def test_clamps_below_minimum(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("X_TEST_PCT", "-0.5")
        assert _read_env_float("X_TEST_PCT", default=0.7, minimum=0.0) == 0.0

    def test_empty_string_uses_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("X_TEST_BUDGET", "")
        assert _read_env_float("X_TEST_BUDGET", default=20.0) == 20.0

    @pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
    def test_rejects_non_finite_to_protect_hard_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
        raw: str,
    ) -> None:
        """NaN/Inf must NOT silently disable the budget guard.

        Without this check, ``QUANTMIND_DAILY_BUDGET=inf`` would make
        every spend below the (infinite) cap, effectively turning off
        enforcement — the worst possible regression for a guard rail.
        """
        monkeypatch.setenv("X_TEST_BUDGET", raw)
        assert _read_env_float("X_TEST_BUDGET", default=20.0) == 20.0


# ---------------------------------------------------------------------------
# get_budget_state
# ---------------------------------------------------------------------------


class TestGetBudgetState:
    """Snapshot semantics: read-only, derived from aggregate_costs."""

    @pytest.mark.asyncio
    async def test_ok_when_below_soft(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
    ) -> None:
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
        monkeypatch.setenv("QUANTMIND_SOFT_CEIL_PCT", "0.7")
        patch_aggregate.return_value = _summary(3.0)
        state = await get_budget_state(redis_client=AsyncMock())
        assert state.status == "ok"
        assert state.spent_today == 3.0
        assert state.soft_ceiling == 14.0
        assert state.hard_ceiling == 20.0
        assert state.remaining == 17.0

    @pytest.mark.asyncio
    async def test_soft_breach(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
    ) -> None:
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
        monkeypatch.setenv("QUANTMIND_SOFT_CEIL_PCT", "0.7")
        patch_aggregate.return_value = _summary(15.0)
        state = await get_budget_state(redis_client=AsyncMock())
        assert state.status == "soft_breach"
        assert state.remaining == 5.0

    @pytest.mark.asyncio
    async def test_hard_breach(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
    ) -> None:
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "10.0")
        patch_aggregate.return_value = _summary(12.0)
        state = await get_budget_state(redis_client=AsyncMock())
        assert state.status == "hard_breach"
        assert state.remaining == 0.0

    @pytest.mark.asyncio
    async def test_no_spend_today_yields_zero(
        self, patch_aggregate: AsyncMock
    ) -> None:
        patch_aggregate.return_value = _summary(None)
        state = await get_budget_state(redis_client=AsyncMock())
        assert state.spent_today == 0.0
        assert state.status == "ok"

    @pytest.mark.asyncio
    async def test_soft_pct_clamped_to_one(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
    ) -> None:
        # Misconfigured > 1.0 must not silently disable the warning.
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
        monkeypatch.setenv("QUANTMIND_SOFT_CEIL_PCT", "1.5")
        patch_aggregate.return_value = _summary(19.99)
        state = await get_budget_state(redis_client=AsyncMock())
        # soft_ceiling clamped to 20.0 (= hard_ceiling); 19.99 is below soft
        assert state.soft_ceiling == 20.0
        assert state.status == "ok"

    @pytest.mark.asyncio
    async def test_uses_defaults_when_env_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
    ) -> None:
        monkeypatch.delenv("QUANTMIND_DAILY_BUDGET", raising=False)
        monkeypatch.delenv("QUANTMIND_SOFT_CEIL_PCT", raising=False)
        patch_aggregate.return_value = _summary(0.0)
        state = await get_budget_state(redis_client=AsyncMock())
        assert state.daily_budget == 20.0
        assert state.soft_ceiling == 14.0  # 20 * 0.7

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raw_spent",
        [float("nan"), float("inf"), float("-inf"), -0.01, -1000.0],
    )
    async def test_invalid_spent_fails_closed_as_hard_breach(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
        raw_spent: float,
    ) -> None:
        """Corrupt cost_rmb in Redis must NOT bypass the hard cap.

        Without the fail-closed branch, ``cost_rmb=nan`` would cause
        ``_classify()`` to see "ok" forever — the same guardrail
        failure mode the env-var validation closes off.
        """
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
        patch_aggregate.return_value = _summary(raw_spent)
        state = await get_budget_state(redis_client=AsyncMock())
        assert state.status == "hard_breach"
        # Sentinel spent must exceed hard ceiling so any future _classify
        # call also flags hard_breach.
        assert state.spent_today > state.hard_ceiling
        assert state.remaining == 0.0


# ---------------------------------------------------------------------------
# assert_budget_allows
# ---------------------------------------------------------------------------


class TestAssertBudgetAllows:
    """Side-effects: raise on hard_breach, return on ok/soft_breach."""

    @pytest.mark.asyncio
    async def test_raises_on_hard_breach(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
    ) -> None:
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "10.0")
        patch_aggregate.return_value = _summary(12.0)
        with pytest.raises(DailyBudgetExceededError) as ctx:
            await assert_budget_allows(
                redis_client=AsyncMock(), agent_name="pipeline"
            )
        # Error message must include both budget and spent for the
        # operator to triage from logs alone.
        assert "10.00" in str(ctx.value)
        assert "12.00" in str(ctx.value)
        assert "pipeline" in str(ctx.value)

    @pytest.mark.asyncio
    async def test_returns_state_on_soft_breach(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
    ) -> None:
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
        patch_aggregate.return_value = _summary(15.0)
        state = await assert_budget_allows(
            redis_client=AsyncMock(), agent_name="pipeline"
        )
        assert isinstance(state, BudgetState)
        assert state.status == "soft_breach"

    @pytest.mark.asyncio
    async def test_returns_state_when_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_aggregate: AsyncMock,
    ) -> None:
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
        patch_aggregate.return_value = _summary(1.0)
        state = await assert_budget_allows(
            redis_client=AsyncMock(), agent_name="pipeline"
        )
        assert state.status == "ok"
