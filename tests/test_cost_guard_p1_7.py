"""H-003 — P1-7 cost_guard extensions: monthly + Kimi + isolation.

Coverage:
- get_monthly_budget_state classifies 50/80/100 milestones
- get_kimi_budget_state classifies ¥4 cap as the ONLY Kimi gate
- assert_kimi_budget_allows raises KimiDailyCapExceededError on breach
- Daily hard breach remains the ONLY full-LLM circuit breaker
- Monthly 100% does NOT raise (status only)
- get_full_budget_state composites the three states
- Module isolation — backend.services.cost_guard does NOT import
  backend.{llm,agents,mirofish,data}
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.services.cost_guard import (
    DailyBudgetExceededError,
    FullBudgetState,
    KimiBudgetState,
    KimiDailyCapExceededError,
    MonthlyBudgetState,
    _classify_kimi,
    _classify_monthly,
    assert_budget_allows,
    assert_kimi_budget_allows,
    get_full_budget_state,
    get_kimi_budget_state,
    get_monthly_budget_state,
)


@pytest.fixture()
def patch_probe(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    """Patch the three cost_probe helpers cost_guard uses."""
    mocks = {
        "get_daily_spent": AsyncMock(return_value=0.0),
        "get_daily_spent_for_provider": AsyncMock(return_value=0.0),
        "get_month_spent": AsyncMock(return_value=0.0),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(f"backend.services.cost_guard.{name}", mock)
    return mocks


# ----------------------------------------------------------------------
# Pure classifiers
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fraction", "status", "milestone"),
    [
        (0.00, "ok", None),
        (0.49, "ok", None),
        (0.50, "threshold_50", 0.50),
        (0.79, "threshold_50", 0.50),
        (0.80, "threshold_80", 0.80),
        (0.99, "threshold_80", 0.80),
        (1.00, "threshold_100", 1.00),
        (1.50, "threshold_100", 1.00),
    ],
)
def test_classify_monthly(
    fraction: float, status: str, milestone: float | None
) -> None:
    assert _classify_monthly(fraction) == (status, milestone)


@pytest.mark.parametrize(
    ("spent", "cap", "expected"),
    [
        (0.0, 4.0, "ok"),
        (3.99, 4.0, "ok"),
        (4.0, 4.0, "hard_breach"),
        (10.0, 4.0, "hard_breach"),
    ],
)
def test_classify_kimi(spent: float, cap: float, expected: str) -> None:
    assert _classify_kimi(spent, cap) == expected


# ----------------------------------------------------------------------
# get_monthly_budget_state
# ----------------------------------------------------------------------


class TestMonthlyBudget:
    @pytest.mark.asyncio
    async def test_ok_at_low_spend(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        monkeypatch.setenv("QUANTMIND_MONTHLY_BUDGET", "440.0")
        patch_probe["get_month_spent"].return_value = 100.0
        state = await get_monthly_budget_state(AsyncMock())
        assert state.status == "ok"
        assert state.threshold_reached is None
        assert state.fraction == pytest.approx(100.0 / 440.0, abs=0.001)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("spent", "expected"),
        [
            (220.0, "threshold_50"),
            (352.0, "threshold_80"),
            (440.0, "threshold_100"),
            (500.0, "threshold_100"),
        ],
    )
    async def test_milestone_states(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
        spent: float,
        expected: str,
    ) -> None:
        monkeypatch.setenv("QUANTMIND_MONTHLY_BUDGET", "440.0")
        patch_probe["get_month_spent"].return_value = spent
        state = await get_monthly_budget_state(AsyncMock())
        assert state.status == expected

    @pytest.mark.asyncio
    async def test_invalid_spend_fails_closed(
        self,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        patch_probe["get_month_spent"].return_value = float("nan")
        state = await get_monthly_budget_state(AsyncMock())
        # NaN spend ⇒ fail-closed sentinel that lands at threshold_100.
        assert state.status == "threshold_100"

    @pytest.mark.asyncio
    async def test_zero_budget_with_spend_yields_finite_fraction(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        """Codex cycle 1 P2 regression.

        Misconfigured ``QUANTMIND_MONTHLY_BUDGET=0`` with positive spend
        must NOT produce ``float('inf')`` for ``MonthlyBudgetState.fraction``
        — Starlette / FastAPI rejects non-finite floats and would 500
        the ``/api/cost/budget`` endpoint. Cap to the threshold_100
        sentinel so JSON serialization stays safe.
        """
        import math

        monkeypatch.setenv("QUANTMIND_MONTHLY_BUDGET", "0")
        patch_probe["get_month_spent"].return_value = 5.0
        state = await get_monthly_budget_state(AsyncMock())
        assert math.isfinite(state.fraction)
        assert state.fraction <= 1.0
        assert state.status == "threshold_100"

    @pytest.mark.asyncio
    async def test_zero_budget_zero_spend_is_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        """Zero / zero degenerate case stays in 'ok' status."""
        monkeypatch.setenv("QUANTMIND_MONTHLY_BUDGET", "0")
        patch_probe["get_month_spent"].return_value = 0.0
        state = await get_monthly_budget_state(AsyncMock())
        assert state.fraction == 0.0
        assert state.status == "ok"


# ----------------------------------------------------------------------
# get_kimi_budget_state
# ----------------------------------------------------------------------


class TestKimiBudget:
    @pytest.mark.asyncio
    async def test_ok_below_cap(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        monkeypatch.setenv("QUANTMIND_KIMI_DAILY_CAP", "4.0")
        patch_probe["get_daily_spent_for_provider"].return_value = 2.5
        state = await get_kimi_budget_state(AsyncMock())
        assert state.status == "ok"
        assert state.spent_today == 2.5
        assert state.remaining == 1.5

    @pytest.mark.asyncio
    async def test_at_cap_is_hard_breach(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        monkeypatch.setenv("QUANTMIND_KIMI_DAILY_CAP", "4.0")
        patch_probe["get_daily_spent_for_provider"].return_value = 4.0
        state = await get_kimi_budget_state(AsyncMock())
        assert state.status == "hard_breach"
        assert state.remaining == 0.0

    @pytest.mark.asyncio
    async def test_invalid_spend_fails_closed(
        self,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        patch_probe["get_daily_spent_for_provider"].return_value = float("-inf")
        state = await get_kimi_budget_state(AsyncMock())
        assert state.status == "hard_breach"


# ----------------------------------------------------------------------
# assert_kimi_budget_allows
# ----------------------------------------------------------------------


class TestAssertKimi:
    @pytest.mark.asyncio
    async def test_raises_on_breach(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        monkeypatch.setenv("QUANTMIND_KIMI_DAILY_CAP", "4.0")
        patch_probe["get_daily_spent_for_provider"].return_value = 5.0
        with pytest.raises(KimiDailyCapExceededError) as ctx:
            await assert_kimi_budget_allows(AsyncMock(), agent_name="risk_officer")
        assert "Kimi" in str(ctx.value)
        assert "risk_officer" in str(ctx.value)

    @pytest.mark.asyncio
    async def test_returns_state_when_ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        monkeypatch.setenv("QUANTMIND_KIMI_DAILY_CAP", "4.0")
        patch_probe["get_daily_spent_for_provider"].return_value = 1.0
        state = await assert_kimi_budget_allows(AsyncMock(), agent_name="x")
        assert state.status == "ok"


# ----------------------------------------------------------------------
# Daily hard cap is the ONLY full-LLM circuit breaker (red-line check)
# ----------------------------------------------------------------------


class TestDailyHardCapIsOnlyFullLlmBreaker:
    @pytest.mark.asyncio
    async def test_monthly_100pct_does_not_raise_in_assert_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        """Monthly 100% must NOT stop LLM (status-only milestone)."""
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
        monkeypatch.setenv("QUANTMIND_MONTHLY_BUDGET", "440.0")
        # Daily within bounds, monthly at 100%.
        patch_probe["get_daily_spent"].return_value = 5.0
        patch_probe["get_month_spent"].return_value = 500.0
        state = await assert_budget_allows(AsyncMock(), agent_name="x")
        assert state.status == "ok"

    @pytest.mark.asyncio
    async def test_kimi_breach_does_not_raise_in_assert_budget(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        """Kimi cap blocks Kimi only — daily LLM stays alive."""
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
        patch_probe["get_daily_spent"].return_value = 1.0
        # Kimi at cap should be irrelevant to the full daily gate.
        state = await assert_budget_allows(AsyncMock(), agent_name="x")
        assert state.status == "ok"

    @pytest.mark.asyncio
    async def test_daily_hard_breach_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_probe: dict[str, AsyncMock],
    ) -> None:
        monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "10.0")
        patch_probe["get_daily_spent"].return_value = 12.0
        with pytest.raises(DailyBudgetExceededError):
            await assert_budget_allows(AsyncMock(), agent_name="x")


# ----------------------------------------------------------------------
# get_full_budget_state composes the three states
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_budget_state_composites(
    monkeypatch: pytest.MonkeyPatch,
    patch_probe: dict[str, AsyncMock],
) -> None:
    monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
    monkeypatch.setenv("QUANTMIND_MONTHLY_BUDGET", "440.0")
    monkeypatch.setenv("QUANTMIND_KIMI_DAILY_CAP", "4.0")
    patch_probe["get_daily_spent"].return_value = 7.0
    patch_probe["get_daily_spent_for_provider"].return_value = 0.5
    patch_probe["get_month_spent"].return_value = 230.0
    state = await get_full_budget_state(AsyncMock())
    assert isinstance(state, FullBudgetState)
    assert state.daily.spent_today == 7.0
    assert state.monthly.status == "threshold_50"
    assert isinstance(state.monthly, MonthlyBudgetState)
    assert state.kimi.status == "ok"
    assert isinstance(state.kimi, KimiBudgetState)


# ----------------------------------------------------------------------
# Isolation — backend.{llm,agents,mirofish,data} imports forbidden
# (CLAUDE.md §2.10 / H-003 redline). This static AST check mirrors the
# scripts/redline-check.sh sub-check so a future regression catches at
# unit-test time too.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    [
        "backend/services/cost_guard.py",
        "backend/services/soft_degrade_manager.py",
        "backend/services/cost_probe.py",
    ],
)
def test_module_does_not_import_forbidden_layers(module_path: str) -> None:
    forbidden = {"llm", "agents", "mirofish", "data"}
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            parts = mod.split(".")
            if parts[:1] == ["backend"] and len(parts) >= 2 and parts[1] in forbidden:
                bad.append(f"{module_path}:{node.lineno} from {mod} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if (
                    parts[:1] == ["backend"]
                    and len(parts) >= 2
                    and parts[1] in forbidden
                ):
                    bad.append(f"{module_path}:{node.lineno} import {alias.name}")
    assert not bad, f"isolation violated in {module_path}: {bad}"
