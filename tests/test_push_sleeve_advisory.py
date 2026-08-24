"""Unit tests for the SLV-1 advisory push script (pure parts; zero network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.push_sleeve_advisory import (
    already_sent,
    content_hash,
    decide,
    load_push_state,
    load_status,
    mark_sent,
    render_text,
    save_push_state,
)


def _status_payload() -> dict:
    return {
        "product": "defensive_sleeve_v1",
        "spec_hash": "c1d058c3" + "0" * 56,
        "status": "ACCRUING",
        "forward": {
            "complete_periods": 0,
            "schedule_rebalances": ["20260615", "20260714"],
        },
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


def _delivered_state(status: dict) -> dict:
    """State as if ``status`` was just delivered (the seeded steady state)."""
    return {
        "last_sent_status": str(status["status"]),
        "last_sent_hash": content_hash(status),
        "last_advised_rebalance": "20260615",
        "asof_trade_date": str(status["advisory"]["asof_trade_date"]),
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


def test_render_text_goes_through_renderer(tmp_path: Path) -> None:
    text = render_text(_status_payload(), mirror_path=tmp_path / "m.jsonl")
    assert "防御Sleeve目标持仓" in text
    assert "002271.SZ" in text
    assert "非交易指令" in text
    assert "QM-" not in text
    # Thresholds flow from the status JSON's pre-registered kill_switch block.
    assert "MDD>25%" in text and "连续6期落后基线" in text
    # MD-1 guardrail line rides on every advisory push.
    assert "不补仓亏损股" in text
    # Owner requirement 2026-08-24: plain-language selection logic always
    # present; without a declared capital, the sizing hint is shown instead
    # of share counts.
    assert "选股逻辑" in text
    assert "R线本金" in text and "建议持有" not in text


def test_render_text_with_capital_gives_share_counts(tmp_path: Path) -> None:
    from backend.portfolio.mirror_ledger import append_cash

    mirror = tmp_path / "m.jsonl"
    append_cash(mirror, amount=100_000.0, note="opening",
                recorded_at="2026-08-24T09:00:00+08:00")
    text = render_text(_status_payload(), mirror_path=mirror)
    # 100k × 8% / 11.5 = 695.6 → floored to 600 shares, a new entry.
    assert "建议持有 600 股" in text
    assert "新进买入 600 股" in text
    assert "买卖点" in text  # operations present → entry/exit logic footer


def test_render_text_exits_listed_for_dropped_mirror_names(
    tmp_path: Path,
) -> None:
    import datetime as dt

    from backend.models.manual_trade import (
        ExternalExecutionEvent,
        ManualTradeReason,
        ManualTradeSide,
    )
    from backend.portfolio.mirror_ledger import append_fill

    mirror = tmp_path / "m.jsonl"
    append_fill(
        mirror,
        ExternalExecutionEvent(
            external_trade_id="UT-20260820-100000-600519-BUY-001",
            code="600519",
            side=ManualTradeSide.BUY,
            volume=200,
            price=10.0,
            executed_at=dt.datetime(
                2026, 8, 20, 10, 0,
                tzinfo=dt.timezone(dt.timedelta(hours=8)),
            ),
            reason=ManualTradeReason.USER_OTHER,
        ),
        recorded_at="2026-08-20T10:00:00+08:00",
    )
    text = render_text(_status_payload(), mirror_path=mirror)
    assert "需清仓" in text
    assert "600519" in text and "现持 200 股 → 全部卖出" in text
    assert "买卖点" in text


def test_render_text_status_transition_line(tmp_path: Path) -> None:
    payload = _status_payload()
    payload["status"] = "KILLED"
    text = render_text(
        payload,
        status_changed_from="ACCRUING",
        mirror_path=tmp_path / "m.jsonl",
    )
    assert "前向状态变化: ACCRUING → KILLED" in text


# --------------------------------------------------------------------------- #
# Change-triggered decision (MI-1 §3.1)                                        #
# --------------------------------------------------------------------------- #


def test_content_hash_ignores_display_drift() -> None:
    a = _status_payload()
    b = _status_payload()
    b["advisory"]["asof_trade_date"] = "20260711"
    b["advisory"]["holdings"][0]["close"] = 99.9
    b["advisory"]["holdings"][0]["dv_ratio"] = 1.0
    assert content_hash(a) == content_hash(b)


def test_content_hash_changes_on_book_change() -> None:
    a = _status_payload()
    b = _status_payload()
    b["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
    c = _status_payload()
    c["advisory"]["cash_weight_pct"] = 60.0
    assert content_hash(a) != content_hash(b)
    assert content_hash(a) != content_hash(c)


def test_content_hash_is_order_insensitive() -> None:
    a = _status_payload()
    a["advisory"]["holdings"] = [
        {"ts_code": "000858.SZ", "target_weight_pct": 8.0},
        {"ts_code": "002271.SZ", "target_weight_pct": 8.0},
    ]
    b = _status_payload()
    b["advisory"]["holdings"] = list(reversed(a["advisory"]["holdings"]))
    assert content_hash(a) == content_hash(b)


def test_decide_silent_on_ordinary_day() -> None:
    status = _status_payload()
    state = _delivered_state(status)
    d = decide(status, state)
    assert d.event is None
    assert d.state_after_silent == state  # nothing to persist either


def test_decide_silent_despite_hash_drift_off_schedule() -> None:
    # The advisory book is recomputed daily (raw top-5, no buffer) — a
    # mid-period rank churn must NOT trigger a push.
    status = _status_payload()
    state = _delivered_state(status)
    status["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"  # drifted book
    d = decide(status, state)
    assert d.event is None
    assert d.state_after_silent == state


def test_decide_rebalance_with_diff_pushes() -> None:
    status = _status_payload()
    state = _delivered_state(status)
    status["advisory"]["asof_trade_date"] = "20260714"  # rebalance day
    status["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
    d = decide(status, state)
    assert d.event == "rebalance"
    assert d.status_changed_from is None
    assert d.state_after_send["last_advised_rebalance"] == "20260714"
    assert d.state_after_send["last_sent_hash"] == content_hash(status)


def test_decide_rebalance_without_diff_advances_pointer_silently() -> None:
    status = _status_payload()
    state = _delivered_state(status)
    status["advisory"]["asof_trade_date"] = "20260714"  # rebalance day, same book
    d = decide(status, state)
    assert d.event is None
    assert d.state_after_silent["last_advised_rebalance"] == "20260714"
    # ...so a later off-schedule drift stays silent:
    later = _status_payload()
    later["advisory"]["asof_trade_date"] = "20260716"
    later["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
    d2 = decide(later, d.state_after_silent)
    assert d2.event is None


def test_decide_missed_rebalance_day_self_heals() -> None:
    # Cron failed on 20260714; the next run (asof 20260715, off schedule)
    # must still announce the pending rebalance when the book differs.
    status = _status_payload()
    state = _delivered_state(status)
    status["advisory"]["asof_trade_date"] = "20260715"
    status["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
    d = decide(status, state)
    assert d.event == "rebalance"
    assert d.state_after_send["last_advised_rebalance"] == "20260714"


def test_decide_status_change_fires_any_day() -> None:
    status = _status_payload()
    state = _delivered_state(status)
    status["status"] = "KILLED"
    d = decide(status, state)
    assert d.event == "status_change"
    assert d.status_changed_from == "ACCRUING"
    assert d.state_after_send["last_sent_status"] == "KILLED"


def test_decide_first_run_announces() -> None:
    d = decide(_status_payload(), {})
    assert d.event == "status_change"
    assert d.status_changed_from is None  # nothing delivered before


def test_decide_delivered_killed_notice_not_repeated() -> None:
    # After a DELIVERED KILLED push, subsequent runs are silent again.
    status = _status_payload()
    status["status"] = "KILLED"
    state = decide(status, _delivered_state(_status_payload())).state_after_send
    d = decide(status, state)
    assert d.event is None


def test_decide_delivered_rebalance_sets_awaiting_report() -> None:
    status = _status_payload()
    state = _delivered_state(status)
    status["advisory"]["asof_trade_date"] = "20260714"
    status["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
    d = decide(status, state)
    assert d.event == "rebalance"
    assert d.state_after_send["awaiting_report"] == {
        "hash": content_hash(status),
        "delivered_asof": "20260714",
    }


def test_decide_awaiting_report_reminds_next_close() -> None:
    # No owner report by the next trading close → re-render + re-push.
    status = _status_payload()
    status["advisory"]["asof_trade_date"] = "20260714"
    status["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
    delivered = decide(status, _delivered_state(_status_payload())).state_after_send
    same_day = decide(status, delivered)
    assert same_day.event is None  # delivered today — no reminder yet
    status["advisory"]["asof_trade_date"] = "20260715"
    d = decide(status, delivered)
    assert d.event == "rebalance_reminder"
    # A delivered reminder re-arms for the day after.
    assert d.state_after_send["awaiting_report"]["delivered_asof"] == "20260715"


def test_decide_cleared_awaiting_stays_silent() -> None:
    # The reconciliation loop clears awaiting_report after a booked fill /
    # explicit no-action — the reminder must then stop.
    status = _status_payload()
    status["advisory"]["asof_trade_date"] = "20260714"
    status["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
    delivered = decide(status, _delivered_state(_status_payload())).state_after_send
    cleared = {k: v for k, v in delivered.items() if k != "awaiting_report"}
    status["advisory"]["asof_trade_date"] = "20260715"
    assert decide(status, cleared).event is None


def test_decide_killed_suppresses_rebalance_and_reminder() -> None:
    # codex P1: after the delivered KILLED notice, a killed sleeve must
    # never resume action-bearing pushes — no reminder, no new rebalance.
    status = _status_payload()
    status["status"] = "KILLED"
    delivered_killed = decide(status, _delivered_state(_status_payload()))
    assert delivered_killed.event == "status_change"
    assert "awaiting_report" not in delivered_killed.state_after_send
    after = delivered_killed.state_after_send
    # A pending reminder from before the kill must not fire...
    stale = {
        **after,
        "awaiting_report": {"hash": "old", "delivered_asof": "20260709"},
    }
    status["advisory"]["asof_trade_date"] = "20260711"
    d = decide(status, stale)
    assert d.event is None
    assert "awaiting_report" not in d.state_after_silent  # ...and is purged
    # ...and a later scheduled rebalance with a changed book stays silent.
    status["advisory"]["asof_trade_date"] = "20260714"
    status["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
    assert decide(status, after).event is None


def test_advisory_history_records_exits_once(tmp_path: Path) -> None:
    from unittest.mock import patch

    from scripts.push_sleeve_advisory import (
        append_advisory_history,
        load_advisory_history,
    )

    history = tmp_path / "history.jsonl"
    first = _status_payload()
    with patch(
        "scripts.push_sleeve_advisory._pit_closes",
        side_effect=lambda asof, codes: {c: 9.99 for c in codes},
    ):
        append_advisory_history(
            history, first, event="rebalance", delivered_at="t0"
        )
        second = _status_payload()
        second["advisory"]["asof_trade_date"] = "20260714"
        second["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
        append_advisory_history(
            history, second, event="rebalance", delivered_at="t1"
        )
        third = _status_payload()
        third["advisory"]["asof_trade_date"] = "20260715"
        third["advisory"]["holdings"][0]["ts_code"] = "000858.SZ"
        append_advisory_history(
            history, third, event="rebalance_reminder", delivered_at="t2"
        )
    rows = load_advisory_history(history)
    assert rows[0]["exits"] == []
    # 002271.SZ dropped at the 20260714 delivery → exit close recorded there,
    assert rows[1]["exits"] == [{"ts_code": "002271.SZ", "close": 9.99}]
    # ...and ONCE only (the research sell assumption is the exit-day close).
    assert rows[2]["exits"] == []


def test_clear_awaiting_report_roundtrip(tmp_path: Path) -> None:
    from backend.portfolio.sleeve_push_state import clear_awaiting_report

    p = tmp_path / "state.json"
    save_push_state(
        p,
        {
            "last_sent_status": "ACCRUING",
            "awaiting_report": {"hash": "h", "delivered_asof": "20260714"},
        },
    )
    assert clear_awaiting_report(p)
    assert "awaiting_report" not in load_push_state(p)
    assert load_push_state(p)["last_sent_status"] == "ACCRUING"
    assert not clear_awaiting_report(p)  # idempotent


def test_render_text_reminder_line(tmp_path: Path) -> None:
    text = render_text(
        _status_payload(), reminder=True, mirror_path=tmp_path / "m.jsonl"
    )
    assert "尚未收到执行回报" in text


def test_push_state_roundtrip_and_corruption(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    assert load_push_state(p) == {}
    save_push_state(p, {"last_sent_status": "ACCRUING"})
    assert load_push_state(p)["last_sent_status"] == "ACCRUING"
    p.write_text("{not json", encoding="utf-8")
    assert load_push_state(p) == {}  # corrupt → first-run behavior (dedupe only)


# --------------------------------------------------------------------------- #
# Legacy per-as-of marker (still used by push_ipo_reminder)                    #
# --------------------------------------------------------------------------- #


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
