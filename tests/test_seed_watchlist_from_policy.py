"""Tests for ``_seed_watchlist_from_policy`` rotation-drift reconciliation.

Codex C-002 P1 (cycle 1) flagged that the previous implementation only
upserted policy codes and never soft-deleted Mongo rows that fell out
of the policy. After a rotation those stale rows stayed ``active=True``
and ``AnalysisScheduler`` would default-route them through
``assign_category`` — silently analysing codes the locked policy no
longer contains. This module verifies the reconciliation behaviour
that fixes the leak.

Post-2026-05-24 amendment the universe is full-market, so the seed only
reconciles *manually-pinned* codes (fast/slow default_codes + overrides);
``all_watchlist_codes()`` is empty unless the owner pins codes.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.main import _seed_watchlist_from_policy
from backend.services.universe_policy import (
    UniversePolicy,
    load_policy,
)

VALID_YAML = """
policy_version: 3
locked_decision: P0-9
last_updated: 2026-05-24

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

overrides: {}

universe:
  board_whitelist:
    - sh_main
    - sz_main
    - chuangye
    - etf
  forbidden_boards:
    - kechuang_688
    - beijiao_8
    - st
    - convertible_bond

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
def policy(tmp_path: Path) -> UniversePolicy:
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
async def test_seed_adds_missing_codes(policy: UniversePolicy) -> None:
    """Empty Mongo → every pinned code is upserted, no remove_stock call."""
    svc = _make_watchlist_service([])
    await _seed_watchlist_from_policy(svc, policy)

    upserted = {call.args[0] for call in svc.add_stock.await_args_list}
    assert upserted == {"600519", "000858", "510300"}
    svc.remove_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_uses_code_as_display_name(
    policy: UniversePolicy,
) -> None:
    """The required_etfs name SSoT was removed by the amendment; display
    names now fall back to the code itself until a stock_metadata
    registry is wired in (a later phase)."""
    svc = _make_watchlist_service([])
    await _seed_watchlist_from_policy(svc, policy)

    by_code = {c.args[0]: c.args[1] for c in svc.add_stock.await_args_list}
    assert by_code["510300"] == "510300"
    assert by_code["600519"] == "600519"
    assert by_code["000858"] == "000858"


@pytest.mark.asyncio
async def test_seed_soft_deletes_stale_codes(
    policy: UniversePolicy,
) -> None:
    """Rotation drift: codes that exist in Mongo but not pinned in the
    policy must be soft-deleted so the scheduler stops routing them."""
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
    policy: UniversePolicy,
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


@pytest.fixture()
def empty_pin_policy(tmp_path: Path) -> UniversePolicy:
    """A valid v3 policy with no manually-pinned codes (full-market default)."""
    text = VALID_YAML.replace('default_codes: ["600519"]', "default_codes: []")
    text = text.replace('    - "000858"\n    - "510300"\n', "")
    p = tmp_path / "empty.yaml"
    p.write_text(text, encoding="utf-8")
    return load_policy(p)


@pytest.mark.asyncio
async def test_seed_skips_when_no_pinned_codes(
    empty_pin_policy: UniversePolicy,
) -> None:
    """Full-market default (empty pin set) must NOT touch the watchlist:
    neither seed a fixed list nor destructively soft-delete pre-existing
    rows (codex L-001 P1). Screening drives the universe instead."""
    assert empty_pin_policy.all_watchlist_codes() == frozenset()
    active = [
        {"stock_code": "600519", "stock_name": "贵州茅台", "active": True},
        {"stock_code": "510300", "stock_name": "沪深300 ETF", "active": True},
    ]
    svc = _make_watchlist_service(active)
    await _seed_watchlist_from_policy(svc, empty_pin_policy)
    svc.add_stock.assert_not_awaited()
    svc.remove_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_idempotent_when_state_matches(
    policy: UniversePolicy,
) -> None:
    """If Mongo already matches the pinned policy codes, add_stock still
    runs (upsert is idempotent) but remove_stock must not be called."""
    canonical = sorted(policy.all_watchlist_codes())
    active = [
        {"stock_code": c, "stock_name": c, "active": True} for c in canonical
    ]
    svc = _make_watchlist_service(active)
    await _seed_watchlist_from_policy(svc, policy)

    svc.remove_stock.assert_not_awaited()
    assert svc.add_stock.await_count == len(canonical)
