"""Tests for ``_seed_watchlist_from_policy`` rotation-drift reconciliation.

Codex C-002 P1 (cycle 1) flagged that the previous implementation only
upserted policy codes and never soft-deleted Mongo rows that fell out
of the policy. After a rotation those stale rows stayed ``active=True``
and ``AnalysisScheduler`` would default-route them through
``assign_category`` — silently analysing codes the locked policy no
longer contains. This module verifies the reconciliation behaviour
that fixes the leak.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.main import _seed_watchlist_from_policy
from backend.services.watchlist_policy import (
    WatchlistPolicy,
    load_policy,
)

VALID_YAML = """
policy_version: 2
locked_decision: P0-9
last_updated: 2026-05-12

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
  default_codes:
    - "000858"
    - "510300"
    - "510500"
    - "159949"

overrides: {}

watchlist:
  total_codes: 13
  composition:
    sh_main: 4
    sz_main: 3
    chuangye: 3
    etf: 3
  default_category: slow

required_etfs:
  - code: "510300"
    name: "沪深300 ETF"
    tracking: "沪深300指数"
  - code: "510500"
    name: "中证500 ETF"
    tracking: "中证500指数"
  - code: "159949"
    name: "创业板50 ETF"
    tracking: "创业板50指数"

exclusion_rules:
  ipo_min_trading_days: 30
  sub_new_min_trading_days: 180
  min_avg_amount_20d_yuan: 200000000
  max_unit_price_yuan: 500.0

cap_allocation:
  total_daily_cap: 5
  traditional_path_default_cap: 4
  event_path_reserved_cap: 1
  reserved_cap_release_time: "14:30"

direction_policy:
  long_only: true
  forbidden_sides:
    - SHORT
    - COVER
    - MARGIN_BUY
    - REVERSE_REPO
    - ETF_SUBSCRIBE
    - ETF_REDEEM
  etf_arbitrage_enabled: false
"""


@pytest.fixture()
def policy(tmp_path: Path) -> WatchlistPolicy:
    p = tmp_path / "policy.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    return load_policy(p)


def _make_watchlist_service(active_rows: list[dict]) -> AsyncMock:
    svc = AsyncMock()
    svc.list_stocks = AsyncMock(return_value=active_rows)
    svc.add_stock = AsyncMock()
    svc.remove_stock = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_seed_adds_missing_codes(policy: WatchlistPolicy) -> None:
    """Empty Mongo → every policy code is upserted, no remove_stock call."""
    svc = _make_watchlist_service([])
    await _seed_watchlist_from_policy(svc, policy)

    upserted = {call.args[0] for call in svc.add_stock.await_args_list}
    assert upserted == {"600519", "000858", "510300", "510500", "159949"}
    svc.remove_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_uses_required_etf_display_names(
    policy: WatchlistPolicy,
) -> None:
    """ETF display names come from policy.required_etfs (P0-9 §1.2 SSoT)."""
    svc = _make_watchlist_service([])
    await _seed_watchlist_from_policy(svc, policy)

    by_code = {c.args[0]: c.args[1] for c in svc.add_stock.await_args_list}
    assert by_code["510300"] == "沪深300 ETF"
    assert by_code["510500"] == "中证500 ETF"
    assert by_code["159949"] == "创业板50 ETF"
    # Non-ETF codes fall back to the code itself.
    assert by_code["600519"] == "600519"


@pytest.mark.asyncio
async def test_seed_soft_deletes_stale_codes(
    policy: WatchlistPolicy,
) -> None:
    """Rotation drift: codes that exist in Mongo but not in the policy
    must be soft-deleted so the scheduler stops routing them."""
    active = [
        {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
        {"stock_code": "300750", "stock_name": "宁德时代", "active": True},  # stale
        {"stock_code": "601318", "stock_name": "中国平安", "active": True},  # stale
        {"stock_code": "510300", "stock_name": "沪深300 ETF", "active": True},
    ]
    svc = _make_watchlist_service(active)
    await _seed_watchlist_from_policy(svc, policy)

    removed = {call.args[0] for call in svc.remove_stock.await_args_list}
    assert removed == {"300750", "601318"}


@pytest.mark.asyncio
async def test_seed_handles_malformed_active_row(
    policy: WatchlistPolicy,
) -> None:
    """A Mongo row missing stock_code must not crash the seed."""
    active = [
        {"stock_name": "broken", "active": True},
        {"stock_code": 12345, "active": True},  # non-string
        {"stock_code": "300750", "active": True},  # stale
    ]
    svc = _make_watchlist_service(active)
    await _seed_watchlist_from_policy(svc, policy)

    removed = {call.args[0] for call in svc.remove_stock.await_args_list}
    assert removed == {"300750"}


@pytest.mark.asyncio
async def test_seed_idempotent_when_state_matches(
    policy: WatchlistPolicy,
) -> None:
    """If Mongo already matches the policy, add_stock still runs (upsert
    is idempotent) but remove_stock must not be called."""
    canonical = sorted(policy.all_watchlist_codes())
    active = [
        {"stock_code": c, "stock_name": c, "active": True} for c in canonical
    ]
    svc = _make_watchlist_service(active)
    await _seed_watchlist_from_policy(svc, policy)

    svc.remove_stock.assert_not_awaited()
    assert svc.add_stock.await_count == len(canonical)
