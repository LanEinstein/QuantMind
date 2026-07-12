"""Unit tests for the SLV-1 advisory push script (pure parts; zero network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.push_sleeve_advisory import (
    already_sent,
    load_status,
    mark_sent,
    render_text,
)


def _status_payload() -> dict:
    return {
        "product": "defensive_sleeve_v1",
        "spec_hash": "c1d058c3" + "0" * 56,
        "status": "ACCRUING",
        "forward": {"complete_periods": 0},
        "kill_switch": {
            "min_forward_periods": 8,
            "mdd_kill": 0.25,
            "bear_cum_kill": -0.05,
            "baseline_underperf_periods": 6,
        },
        "advisory": {
            "asof_trade_date": "20260710",
            "universe_size": 463,
            "holdings": [
                {
                    "ts_code": "002271.SZ",
                    "name": "东方雨虹",
                    "dv_ratio": 16.09,
                    "close": 11.5,
                    "target_weight_pct": 8.0,
                }
            ],
            "cash_weight_pct": 92.0,
        },
    }


def test_load_status_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "status.json"
    p.write_text(json.dumps(_status_payload()), encoding="utf-8")
    loaded = load_status(p)
    assert loaded["status"] == "ACCRUING"


def test_load_status_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_status(tmp_path / "absent.json")


def test_load_status_empty_holdings_fails(tmp_path: Path) -> None:
    payload = _status_payload()
    payload["advisory"]["holdings"] = []
    p = tmp_path / "status.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="nothing to push"):
        load_status(p)


def test_load_status_missing_section_fails(tmp_path: Path) -> None:
    payload = _status_payload()
    del payload["kill_switch"]
    p = tmp_path / "status.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="kill_switch"):
        load_status(p)


def test_render_text_goes_through_renderer() -> None:
    text = render_text(_status_payload())
    assert "防御Sleeve目标持仓" in text
    assert "002271.SZ" in text
    assert "非交易指令" in text
    assert "QM-" not in text
    # Thresholds flow from the status JSON's pre-registered kill_switch block.
    assert "MDD>25%" in text and "连续6期落后基线" in text


def test_sent_marker_dedupes_per_asof(tmp_path: Path) -> None:
    marker = tmp_path / "sent.json"
    assert not already_sent(marker, "20260710")
    mark_sent(marker, "20260710", sent_at="2026-07-12T12:00:00+00:00")
    assert already_sent(marker, "20260710")
    assert not already_sent(marker, "20260713")  # a new as-of date still sends
    mark_sent(marker, "20260713", sent_at="2026-07-13T10:00:00+00:00")
    sent = json.loads(marker.read_text(encoding="utf-8"))
    assert set(sent) == {"20260710", "20260713"}  # merge-write keeps history


def test_corrupt_marker_treated_as_never_sent(tmp_path: Path) -> None:
    marker = tmp_path / "sent.json"
    marker.write_text("{not json", encoding="utf-8")
    assert not already_sent(marker, "20260710")
