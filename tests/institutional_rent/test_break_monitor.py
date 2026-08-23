"""Unit tests for the rolling break-issue monitor (zero network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.institutional_rent.break_monitor import (
    cb_break_stats,
    latch_kill_state,
    load_cache,
    load_kill_state,
    mark_notice_delivered,
    save_cache,
    stock_break_stats,
)
from tests.institutional_rent.test_calendars import FakeFrame


def _query(frames: dict[str, Any]):
    calls: list[tuple[str, dict[str, Any]]] = []

    def query(endpoint: str, **kwargs: Any) -> FakeFrame:
        calls.append((endpoint, kwargs))
        value = frames[endpoint]
        return value(kwargs) if callable(value) else value

    query.calls = calls  # type: ignore[attr-defined]
    return query


def _new_share_rows() -> list[dict[str, Any]]:
    return [
        {"ts_code": "301001.SZ", "issue_date": "20260810", "price": 10.0},
        {"ts_code": "301002.SZ", "issue_date": "20260812", "price": 20.0},
        {"ts_code": "920001.BJ", "issue_date": "20260812", "price": 5.0},
        {"ts_code": "301003.SZ", "issue_date": None, "price": 30.0},
        {"ts_code": "301004.SZ", "issue_date": "20260901", "price": 30.0},
    ]


def test_stock_break_stats_counts_and_caches() -> None:
    closes = {"301001.SZ": 9.0, "301002.SZ": 25.0}  # one break, one gain

    def daily(kwargs: dict[str, Any]) -> FakeFrame:
        return FakeFrame([{"close": closes[kwargs["ts_code"]]}])

    query = _query({"new_share": FakeFrame(_new_share_rows()), "daily": daily})
    stats, cache = stock_break_stats(query, "20260823", {"stocks": {}, "cbs": {}})
    assert (stats.evaluated, stats.broken) == (2, 1)
    assert not stats.killed
    assert set(cache["stocks"]) == {"301001.SZ", "301002.SZ"}

    # second run: cached — no further daily queries
    query2 = _query({"new_share": FakeFrame(_new_share_rows())})
    stats2, _ = stock_break_stats(query2, "20260823", cache)
    assert (stats2.evaluated, stats2.broken) == (2, 1)
    assert all(ep == "new_share" for ep, _ in query2.calls)  # type: ignore[attr-defined]


def test_stock_break_stats_missing_bar_skipped_not_cached() -> None:
    query = _query(
        {"new_share": FakeFrame(_new_share_rows()), "daily": lambda k: FakeFrame([])}
    )
    stats, cache = stock_break_stats(query, "20260823", {"stocks": {}, "cbs": {}})
    assert (stats.evaluated, stats.broken) == (0, 0)
    assert cache["stocks"] == {}  # retried on the next run


def test_cb_break_stats_par_threshold() -> None:
    basic = FakeFrame(
        [
            {"ts_code": "123001.SZ", "list_date": "20260801"},
            {"ts_code": "123002.SZ", "list_date": "20260805"},
            {"ts_code": "123003.SZ", "list_date": None},
        ]
    )
    closes = {"123001.SZ": 95.0, "123002.SZ": 130.0}

    def cb_daily(kwargs: dict[str, Any]) -> FakeFrame:
        return FakeFrame([{"close": closes[kwargs["ts_code"]]}])

    query = _query({"cb_basic": basic, "cb_daily": cb_daily})
    stats, cache = cb_break_stats(query, "20260823", {"stocks": {}, "cbs": {}})
    assert (stats.evaluated, stats.broken) == (2, 1)
    assert set(cache["cbs"]) == {"123001.SZ", "123002.SZ"}


def test_kill_threshold_at_four() -> None:
    rows = [
        {
            "ts_code": f"3010{i:02d}.SZ",
            "issue_date": f"202608{i + 1:02d}",
            "price": 10.0,
        }
        for i in range(4)
    ]

    def daily(kwargs: dict[str, Any]) -> FakeFrame:
        return FakeFrame([{"close": 8.0}])  # every one breaks

    query = _query({"new_share": FakeFrame(rows), "daily": daily})
    stats, _ = stock_break_stats(query, "20260823", {"stocks": {}, "cbs": {}})
    assert stats.broken == 4
    assert stats.killed


def test_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    assert load_cache(path) == {"stocks": {}, "cbs": {}}
    save_cache(path, {"stocks": {"a": {"price": 1.0}}, "cbs": {}})
    assert load_cache(path)["stocks"]["a"]["price"] == 1.0


def test_window_substitutes_unevaluable_listing_with_older_one() -> None:
    # 21 listings; the NEWEST has no closing bar yet. The window must still
    # evaluate 20 by falling through to the oldest (codex P1 — the 4th
    # broken issue squeezed out of a shrunken window would miss the kill).
    rows = [
        {
            "ts_code": f"3011{i:02d}.SZ",
            "issue_date": f"202607{i + 1:02d}",
            "price": 10.0,
        }
        for i in range(21)
    ]
    newest = max(r["ts_code"] for r in rows)
    closes = {r["ts_code"]: 12.0 for r in rows}
    closes[min(r["ts_code"] for r in rows)] = 8.0  # only the OLDEST breaks

    def daily(kwargs: dict[str, Any]) -> FakeFrame:
        if kwargs["ts_code"] == newest:
            return FakeFrame([])  # listing morning: no bar yet
        return FakeFrame([{"close": closes[kwargs["ts_code"]]}])

    query = _query({"new_share": FakeFrame(rows), "daily": daily})
    stats, _ = stock_break_stats(query, "20260823", {"stocks": {}, "cbs": {}})
    assert stats.evaluated == 20
    assert stats.broken == 1  # the oldest was substituted in, not dropped


def test_kill_state_latches_and_separates_notification(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    first = latch_kill_state(path, stock_killed=True, cb_killed=False)
    assert first["stock"] and not first["stock_notified"]
    # recovery is NOT automatic: stats back below threshold keeps the latch
    second = latch_kill_state(path, stock_killed=False, cb_killed=False)
    assert second["stock"] and not second["stock_notified"]
    mark_notice_delivered(path, ("stock",))
    assert load_kill_state(path) == {
        "stock": True,
        "cb": False,
        "stock_notified": True,
        "cb_notified": False,
    }


def test_kill_state_dry_run_does_not_persist(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    latch_kill_state(path, stock_killed=True, cb_killed=False, persist=False)
    assert not path.exists()
