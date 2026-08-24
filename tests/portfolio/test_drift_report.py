"""Tests for the monthly mirror-vs-research execution-drift disclosure."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from backend.models.manual_trade import (
    ExternalExecutionEvent,
    ManualTradeReason,
    ManualTradeSide,
)
from backend.portfolio.mirror_ledger import append_fill
from scripts.mirror_drift_report import monthly_drift, reference_close

SHANGHAI = dt.timezone(dt.timedelta(hours=8))
_NOW = "2026-08-24T18:00:00+08:00"


def _history(tmp_path: Path) -> Path:
    p = tmp_path / "history.jsonl"
    rows = [
        {
            "asof": "20260821",
            "event": "rebalance",
            "holdings": [
                {"ts_code": "002271.SZ", "close": 11.14, "target_weight_pct": 8.0}
            ],
            "cash_weight_pct": 60.0,
        },
        {
            "asof": "20260910",
            "event": "rebalance",
            "holdings": [
                {"ts_code": "002271.SZ", "close": 12.00, "target_weight_pct": 8.0}
            ],
            "cash_weight_pct": 60.0,
        },
    ]
    p.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return p


def _fill(path: Path, *, price: float, side=ManualTradeSide.BUY) -> None:
    append_fill(
        path,
        ExternalExecutionEvent(
            external_trade_id=f"UT-20260824-101200-002271-{side.value}-001",
            code="002271",
            side=side,
            volume=5000,
            price=price,
            executed_at=dt.datetime(2026, 8, 24, 10, 12, tzinfo=SHANGHAI),
            reason=ManualTradeReason.USER_OTHER,
        ),
        recorded_at=_NOW,
    )


def test_reference_close_picks_latest_delivery_before_fill(
    tmp_path: Path,
) -> None:
    history_rows = json.loads(
        "[" + ",".join(_history(tmp_path).read_text().strip().splitlines()) + "]"
    )
    assert reference_close(
        history_rows, code="002271", fill_date="20260824"
    ) == 11.14
    assert reference_close(
        history_rows, code="002271", fill_date="20260911"
    ) == 12.00
    assert reference_close(
        history_rows, code="000858", fill_date="20260824"
    ) is None


def test_monthly_drift_buy_above_close_is_positive(tmp_path: Path) -> None:
    history = _history(tmp_path)
    mirror = tmp_path / "mirror.jsonl"
    _fill(mirror, price=11.24)  # paid 0.10 above the 11.14 reference
    report = monthly_drift(mirror, history)
    assert len(report) == 1
    r = report[0]
    assert r["month"] == "202608"
    assert r["comparable_fills"] == 1 and r["uncovered_fills"] == 0
    assert r["drift_yuan"] == 500.0  # 0.10 × 5000 — real execution worse


def test_reference_close_uses_exit_price_for_exited_name(
    tmp_path: Path,
) -> None:
    # codex P1: a name dropped at a rebalance must be referenced at the
    # EXIT delivery's close, not the weeks-old close from when it was held.
    history_rows = [
        {
            "asof": "20260821",
            "holdings": [{"ts_code": "002271.SZ", "close": 11.14}],
            "exits": [],
        },
        {
            "asof": "20260910",
            "holdings": [{"ts_code": "000858.SZ", "close": 71.19}],
            "exits": [{"ts_code": "002271.SZ", "close": 12.50}],
        },
    ]
    assert reference_close(
        history_rows, code="002271", fill_date="20260911"
    ) == 12.50
    # An exit whose PIT lookup failed stays honest: uncovered, not stale.
    history_rows[1]["exits"] = [{"ts_code": "002271.SZ", "close": None}]
    assert reference_close(
        history_rows, code="002271", fill_date="20260911"
    ) is None


def test_monthly_drift_uncovered_fill_disclosed(tmp_path: Path) -> None:
    history = tmp_path / "empty.jsonl"
    mirror = tmp_path / "mirror.jsonl"
    _fill(mirror, price=11.24)
    report = monthly_drift(mirror, history)
    assert report[0]["comparable_fills"] == 0
    assert report[0]["uncovered_fills"] == 1
