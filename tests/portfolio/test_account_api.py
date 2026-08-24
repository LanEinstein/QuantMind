"""Tests for the standalone read-only account-lines API (post-MI-1 panel)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.models.manual_trade import (
    ExternalExecutionEvent,
    ManualTradeReason,
    ManualTradeSide,
)
from backend.portfolio.mirror_ledger import append_adjust, append_cash, append_fill
from backend.portfolio.z_ledger_io import append_record, make_record
from scripts.account_api import create_app

SHANGHAI = dt.timezone(dt.timedelta(hours=8))
_NOW = "2026-08-24T18:00:00+08:00"


def _fill(trade_id: str, *, side: ManualTradeSide, volume: int, price: float):
    return ExternalExecutionEvent(
        external_trade_id=trade_id,
        code="002271",
        side=side,
        volume=volume,
        price=price,
        executed_at=dt.datetime(2026, 8, 24, 10, 12, tzinfo=SHANGHAI),
        reason=ManualTradeReason.USER_OTHER,
    )


def _seed(tmp_path: Path) -> dict[str, Path]:
    mirror = tmp_path / "mirror.jsonl"
    z = tmp_path / "z.jsonl"
    history = tmp_path / "history.jsonl"
    append_cash(mirror, amount=150_000.0, note="opening", recorded_at=_NOW)
    append_fill(
        mirror,
        _fill("UT-20260824-101200-002271-BUY-001",
              side=ManualTradeSide.BUY, volume=5000, price=12.30),
        recorded_at=_NOW,
    )
    append_adjust(
        mirror, code="002271", volume_delta=-100, note="owner corrected",
        recorded_at=_NOW, effective_at="2026-08-24T18:00:01+08:00",
    )
    append_record(
        z, make_record(type="ipo_sell", code="301689.SZ", name="电科思仪",
                       amount=21850.0)
    )
    history.write_text(
        json.dumps({"asof": "20260821",
                    "holdings": [{"ts_code": "002271.SZ", "close": 12.0}],
                    "exits": []}) + "\n",
        encoding="utf-8",
    )
    return {"mirror": mirror, "z": z, "history": history}


def _client(paths: dict[str, Path], **kw) -> TestClient:
    return TestClient(create_app(
        mirror_path=paths["mirror"], z_path=paths["z"],
        history_path=paths["history"], **kw,
    ))


def test_lines_endpoint_returns_envelope_with_account_shape(tmp_path: Path):
    body = _client(_seed(tmp_path)).get("/api/portfolio/lines").json()
    assert body["status"] == "ok" and body["error"] is None
    data = body["data"]
    assert data["r_line"]["cash"] < 150_000.0
    assert data["r_line"]["opening_declared"] is True
    assert data["r_line"]["fill_count"] == 1
    assert data["r_line"]["positions"] == [
        {"code": "002271", "volume": 4900,
         "avg_cost": data["r_line"]["positions"][0]["avg_cost"]}
    ]
    assert data["r_line"]["cost_value"] == round(
        4900 * data["r_line"]["positions"][0]["avg_cost"], 2)
    assert data["z_line"]["realized_pnl"] == 21850.0
    assert data["z_line"]["records"] == 1
    assert data["generated_at"]


def test_recent_rows_are_newest_first_and_keep_kind_specific_fields(tmp_path):
    data = _client(_seed(tmp_path)).get("/api/portfolio/lines").json()["data"]
    rows = data["recent_ledger_rows"]
    assert [r["kind"] for r in rows] == ["adjust", "fill", "cash"]
    adjust, fill, cash = rows
    assert adjust["volume_delta"] == -100 and adjust["effective_at"]
    assert fill["side"] == "BUY" and fill["commission"] > 0 and fill["net"] > 0
    assert "external_trade_id" not in fill
    assert cash["amount"] == 150_000.0


def test_recent_rows_limit_takes_the_tail(tmp_path: Path):
    data = _client(_seed(tmp_path), recent_limit=2).get(
        "/api/portfolio/lines").json()["data"]
    assert [r["kind"] for r in data["recent_ledger_rows"]] == ["adjust", "fill"]


def test_monthly_drift_included(tmp_path: Path):
    data = _client(_seed(tmp_path)).get("/api/portfolio/lines").json()["data"]
    assert data["monthly_drift"] == [{
        "month": "202608", "comparable_fills": 1, "uncovered_fills": 0,
        "drift_yuan": 1500.0, "drift_pct": 2.5,
    }]


def test_missing_ledgers_yield_empty_view(tmp_path: Path):
    paths = {"mirror": tmp_path / "m.jsonl", "z": tmp_path / "z.jsonl",
             "history": tmp_path / "h.jsonl"}
    body = _client(paths).get("/api/portfolio/lines").json()
    assert body["status"] == "ok"
    data = body["data"]
    assert data["r_line"] == {"positions": [], "cash": 0.0,
                              "opening_declared": False, "fill_count": 0,
                              "cost_value": 0.0}
    assert data["recent_ledger_rows"] == [] and data["monthly_drift"] == []


def test_broken_ledger_reported_in_envelope(tmp_path: Path):
    paths = _seed(tmp_path)
    paths["mirror"].write_text('{"kind": "bogus"}\n', encoding="utf-8")
    resp = _client(paths).get("/api/portfolio/lines")
    body = resp.json()
    assert resp.status_code == 200
    assert body["status"] == "error" and body["data"] is None
    assert "unknown kind" in body["error"]
