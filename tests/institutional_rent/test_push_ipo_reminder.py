"""Unit tests for the reminder assembly (pure parts; zero network)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.institutional_rent.break_monitor import mark_notice_delivered
from scripts.push_ipo_reminder import build_reminder
from tests.institutional_rent.test_calendars import FakeFrame


def _query(frames: dict[str, Any]):
    def query(endpoint: str, **kwargs: Any) -> FakeFrame:
        value = frames[endpoint]
        return value(kwargs) if callable(value) else value

    return query


def _quiet_frames() -> dict[str, Any]:
    """No subscriptions today; two healthy listed IPOs; no listed CBs."""
    return {
        "new_share": FakeFrame(
            [
                {"ts_code": "301001.SZ", "sub_code": "301001", "name": "甲",
                 "ipo_date": "20260810", "issue_date": "20260818", "price": 10.0},
                {"ts_code": "301002.SZ", "sub_code": "301002", "name": "乙",
                 "ipo_date": "20260812", "issue_date": "20260820", "price": 20.0},
            ]
        ),
        "cb_issue": FakeFrame([]),
        "cb_basic": FakeFrame([]),
        "daily": lambda k: FakeFrame([{"close": 15.0}]),
    }


def test_silent_when_nothing_subscribable(tmp_path: Path) -> None:
    build = build_reminder(_query(_quiet_frames()), "20260823", tmp_path)
    assert build.text is None
    assert build.pending_notices == ()
    # break cache persisted even on silent days (monitor keeps accruing)
    assert (tmp_path / "break_cache.json").exists()


def test_reminder_lists_stock_and_cb(tmp_path: Path) -> None:
    frames = _quiet_frames()
    frames["new_share"] = FakeFrame(
        [
            *frames["new_share"].to_dict("records"),
            {"ts_code": "301689.SZ", "sub_code": "301689", "name": "电科思仪",
             "ipo_date": "20260823", "issue_date": None, "price": 0.0},
        ]
    )
    frames["cb_issue"] = FakeFrame(
        [{"ts_code": "123284.SZ", "onl_code": "371628", "onl_name": "强达发债",
          "onl_date": "2026-08-23"}]
    )
    build = build_reminder(_query(frames), "20260823", tmp_path)
    assert build.text is not None
    assert "电科思仪" in build.text
    assert "发行价 未公布" in build.text
    assert "强达发债" in build.text
    assert "非交易指令" in build.text
    assert "QM-" not in build.text


def test_kill_notice_retries_until_delivered_then_goes_silent(tmp_path: Path) -> None:
    frames = _quiet_frames()
    frames["new_share"] = FakeFrame(
        [
            {"ts_code": f"3010{i:02d}.SZ", "sub_code": f"3010{i:02d}", "name": f"股{i}",
             "ipo_date": "20260801", "issue_date": f"202608{i + 1:02d}", "price": 10.0}
            for i in range(4)
        ]
    )
    frames["daily"] = lambda k: FakeFrame([{"close": 8.0}])  # all four break

    first = build_reminder(_query(frames), "20260823", tmp_path)
    assert first.text is not None and "停发" in first.text
    assert first.pending_notices == ("stock",)

    # send FAILED (delivery never confirmed) → the notice must come back
    # on the next run instead of being consumed (codex P1 regression).
    second = build_reminder(_query(frames), "20260824", tmp_path)
    assert second.text is not None and "停发" in second.text
    assert second.pending_notices == ("stock",)

    mark_notice_delivered(tmp_path / "break_kill_state.json", ("stock",))
    third = build_reminder(_query(frames), "20260825", tmp_path)
    assert third.text is None  # delivered: no repeat notice, no listing

    state = json.loads((tmp_path / "break_kill_state.json").read_text(encoding="utf-8"))
    assert state == {
        "stock": True,
        "cb": False,
        "stock_notified": True,
        "cb_notified": False,
    }


def test_killed_stock_category_does_not_list_subscriptions(tmp_path: Path) -> None:
    (tmp_path / "break_kill_state.json").write_text(
        json.dumps(
            {"stock": True, "cb": False, "stock_notified": True, "cb_notified": False}
        ),
        encoding="utf-8",
    )
    frames = _quiet_frames()
    frames["new_share"] = FakeFrame(
        [{"ts_code": "301777.SZ", "sub_code": "301777", "name": "丙",
          "ipo_date": "20260823", "issue_date": None, "price": 12.0}]
    )
    frames["cb_issue"] = FakeFrame(
        [{"ts_code": "123284.SZ", "onl_code": "371628", "onl_name": "强达发债",
          "onl_date": "2026-08-23"}]
    )
    build = build_reminder(_query(frames), "20260823", tmp_path)
    assert build.text is not None
    assert "丙" not in build.text  # stock category suppressed
    assert "强达发债" in build.text  # CB category still lives
    assert build.pending_notices == ()  # notice already delivered earlier


def test_dry_run_persists_nothing(tmp_path: Path) -> None:
    build_reminder(_query(_quiet_frames()), "20260823", tmp_path, persist=False)
    assert not (tmp_path / "break_cache.json").exists()
    assert not (tmp_path / "break_kill_state.json").exists()
