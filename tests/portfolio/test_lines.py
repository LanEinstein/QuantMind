"""Tests for the read-side line aggregation (R / Z / cash view)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from backend.models.manual_trade import (
    ExternalExecutionEvent,
    ManualTradeReason,
    ManualTradeSide,
)
from backend.portfolio.lines import build_account_view, render_account_lines
from backend.portfolio.mirror_ledger import append_cash, append_fill
from backend.portfolio.z_ledger_io import append_record, make_record

SHANGHAI = dt.timezone(dt.timedelta(hours=8))
_NOW = "2026-08-24T18:00:00+08:00"


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    mirror = tmp_path / "mirror.jsonl"
    z = tmp_path / "z.jsonl"
    append_cash(mirror, amount=100_000.0, note="opening", recorded_at=_NOW)
    append_fill(
        mirror,
        ExternalExecutionEvent(
            external_trade_id="UT-20260824-101200-002271-BUY-001",
            code="002271",
            side=ManualTradeSide.BUY,
            volume=5000,
            price=12.30,
            executed_at=dt.datetime(2026, 8, 24, 10, 12, tzinfo=SHANGHAI),
            reason=ManualTradeReason.USER_OTHER,
        ),
        recorded_at=_NOW,
    )
    append_record(
        z, make_record(type="ipo_sell", code="301689.SZ", name="电科思仪",
                       amount=21850.0)
    )
    return mirror, z


def test_view_merges_r_and_z(tmp_path: Path) -> None:
    mirror, z = _seed(tmp_path)
    view = build_account_view(mirror, z)
    assert view.r_book.position_for("002271").volume == 5000
    assert view.r_book.opening_declared
    assert view.z_summary["realized_pnl"] == 21850.0
    assert view.r_cost_value > 61_500.0  # fee-inclusive cost

    text = render_account_lines(view)
    assert "R 线" in text and "Z 线" in text
    assert "002271" in text and "21,850.00" in text
    assert "本金未申报" not in text


def test_view_discloses_undeclared_opening(tmp_path: Path) -> None:
    mirror = tmp_path / "mirror.jsonl"
    append_fill(
        mirror,
        ExternalExecutionEvent(
            external_trade_id="UT-20260824-101200-002271-BUY-001",
            code="002271",
            side=ManualTradeSide.BUY,
            volume=100,
            price=10.0,
            executed_at=dt.datetime(2026, 8, 24, 10, 12, tzinfo=SHANGHAI),
            reason=ManualTradeReason.USER_OTHER,
        ),
        recorded_at=_NOW,
    )
    view = build_account_view(mirror, tmp_path / "absent_z.jsonl")
    assert "本金未申报" in render_account_lines(view)
    assert view.z_summary["records"] == 0
