"""H-003 — backend.services.cost_probe tests.

Coverage:
- scan_costs aggregates daily / by_provider / by_provider_daily
- get_daily_spent shorthand
- get_daily_spent_for_provider filters by provider
- get_month_spent sums only the current calendar month
- corrupt cost_rmb (negative / NaN / Inf) entries are dropped
- empty / missing keys handled gracefully
"""

from __future__ import annotations

import datetime
import math

import pytest

from backend.services.cost_probe import (
    CostProbeEntry,
    get_daily_spent,
    get_daily_spent_for_provider,
    get_month_spent,
    scan_costs,
)


class _FakeRedis:
    """Minimal Redis stub supporting scan + hgetall used by cost_probe."""

    def __init__(self, hashes: dict[str, dict[str, str]]) -> None:
        # key: "llm:usage:{date}:{agent}:{provider}" -> hash dict
        self._hashes = hashes

    async def scan(
        self, cursor: int, match: str, count: int = 100
    ) -> tuple[int, list[str]]:
        prefix = match.rstrip("*")
        keys = [k for k in self._hashes if k.startswith(prefix)]
        return 0, keys

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))


def _key(date: str, agent: str, provider: str) -> str:
    return f"llm:usage:{date}:{agent}:{provider}"


@pytest.mark.asyncio
async def test_scan_costs_aggregates_by_provider_and_day() -> None:
    today = datetime.date(2026, 5, 16)
    yesterday = today - datetime.timedelta(days=1)
    hashes = {
        _key(today.isoformat(), "fundamental", "deepseek"): {"cost_rmb": "1.5"},
        _key(today.isoformat(), "technical", "kimi"): {"cost_rmb": "0.8"},
        _key(yesterday.isoformat(), "fund_manager", "qwen"): {"cost_rmb": "2.2"},
    }
    redis = _FakeRedis(hashes)
    summary = await scan_costs(redis, days=2, today=today)
    assert summary.total_cost_rmb == pytest.approx(4.5)
    assert summary.daily_totals[today.isoformat()] == pytest.approx(2.3)
    assert summary.daily_totals[yesterday.isoformat()] == pytest.approx(2.2)
    assert summary.by_provider["deepseek"] == pytest.approx(1.5)
    assert summary.by_provider["kimi"] == pytest.approx(0.8)
    assert summary.by_provider["qwen"] == pytest.approx(2.2)
    assert summary.by_provider_daily["deepseek"][today.isoformat()] == pytest.approx(
        1.5
    )


@pytest.mark.asyncio
async def test_get_daily_spent_returns_today_only() -> None:
    today = datetime.date(2026, 5, 16)
    hashes = {
        _key(today.isoformat(), "ag", "deepseek"): {"cost_rmb": "5.5"},
        _key(
            (today - datetime.timedelta(days=1)).isoformat(), "ag", "deepseek"
        ): {"cost_rmb": "9.9"},
    }
    redis = _FakeRedis(hashes)
    spent = await get_daily_spent(redis, today=today)
    assert spent == pytest.approx(5.5)


@pytest.mark.asyncio
async def test_get_daily_spent_for_provider_filters() -> None:
    today = datetime.date(2026, 5, 16)
    hashes = {
        _key(today.isoformat(), "ag", "kimi"): {"cost_rmb": "2.0"},
        _key(today.isoformat(), "ag", "deepseek"): {"cost_rmb": "9.0"},
    }
    redis = _FakeRedis(hashes)
    assert await get_daily_spent_for_provider(
        redis, provider="kimi", today=today
    ) == pytest.approx(2.0)
    assert await get_daily_spent_for_provider(
        redis, provider="deepseek", today=today
    ) == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_get_daily_spent_for_provider_missing_returns_zero() -> None:
    today = datetime.date(2026, 5, 16)
    hashes = {_key(today.isoformat(), "ag", "deepseek"): {"cost_rmb": "1.0"}}
    redis = _FakeRedis(hashes)
    assert await get_daily_spent_for_provider(
        redis, provider="kimi", today=today
    ) == 0.0


@pytest.mark.asyncio
async def test_get_month_spent_sums_only_current_month() -> None:
    today = datetime.date(2026, 5, 16)
    last_month = datetime.date(2026, 4, 30)
    hashes = {
        _key(today.isoformat(), "ag", "deepseek"): {"cost_rmb": "3.0"},
        _key(
            (today - datetime.timedelta(days=5)).isoformat(),
            "ag",
            "kimi",
        ): {"cost_rmb": "1.0"},
        # Across the month boundary — must NOT count.
        _key(last_month.isoformat(), "ag", "qwen"): {"cost_rmb": "99.0"},
    }
    redis = _FakeRedis(hashes)
    month = await get_month_spent(redis, today=today)
    # 3.0 + 1.0 + 0 (last month dropped)
    assert month == pytest.approx(4.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["nan", "-1.0", "-100", "inf", "-inf"])
async def test_corrupt_cost_dropped(bad: str) -> None:
    today = datetime.date(2026, 5, 16)
    hashes = {
        _key(today.isoformat(), "ag", "deepseek"): {"cost_rmb": "1.0"},
        _key(today.isoformat(), "ag2", "kimi"): {"cost_rmb": bad},
    }
    redis = _FakeRedis(hashes)
    spent = await get_daily_spent(redis, today=today)
    # Only the clean ¥1.0 row counts; bad row dropped.
    assert spent == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_empty_hash_dropped() -> None:
    today = datetime.date(2026, 5, 16)
    hashes = {_key(today.isoformat(), "ag", "deepseek"): {}}
    redis = _FakeRedis(hashes)
    spent = await get_daily_spent(redis, today=today)
    assert spent == 0.0


@pytest.mark.asyncio
async def test_scan_costs_rejects_zero_days() -> None:
    redis = _FakeRedis({})
    with pytest.raises(ValueError):
        await scan_costs(redis, days=0)


def test_cost_probe_entry_is_immutable() -> None:
    entry = CostProbeEntry(
        date="2026-05-16", agent_name="ag", provider="kimi", cost_rmb=1.0
    )
    with pytest.raises(Exception):  # noqa: BLE001 — dataclass frozen=True
        entry.cost_rmb = 99.0  # type: ignore[misc]
    assert math.isfinite(entry.cost_rmb)
