"""FiredTriggerStore — durable per-day dedup (intraday ops hardening §1.1).

The store is FAIL-OPEN by design (UX-layer guard, not safety-layer): a
corrupt or unreadable file degrades to an empty/partial key set with a
loud error log, never an exception — a broken store must not stop the
monitoring tick (the opposite trade-off from takeprofit_ledger).
"""

from __future__ import annotations

from pathlib import Path

from backend.orchestration.fired_trigger_store import FiredTriggerStore


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    store = FiredTriggerStore(tmp_path / "nope" / "fired.jsonl")
    assert store.load_fired("2026-06-04") == frozenset()


def test_record_and_load_roundtrip(tmp_path: Path) -> None:
    store = FiredTriggerStore(tmp_path / "fired.jsonl")
    store.record_fired("2026-06-04", "605020", "take_profit", signal_id="s1")
    store.record_fired("2026-06-04", "600011", "atr_trailing_stop", signal_id="s1")
    assert store.load_fired("2026-06-04") == frozenset(
        {("605020", "take_profit"), ("600011", "atr_trailing_stop")}
    )


def test_load_filters_by_trade_date(tmp_path: Path) -> None:
    store = FiredTriggerStore(tmp_path / "fired.jsonl")
    store.record_fired("2026-06-03", "605020", "take_profit", signal_id="s0")
    store.record_fired("2026-06-04", "600011", "drawdown_stop", signal_id="s1")
    assert store.load_fired("2026-06-04") == frozenset({("600011", "drawdown_stop")})
    assert store.load_fired("2026-06-03") == frozenset({("605020", "take_profit")})


def test_corrupt_row_fails_open_keeps_valid_rows(tmp_path: Path) -> None:
    path = tmp_path / "fired.jsonl"
    store = FiredTriggerStore(path)
    store.record_fired("2026-06-04", "605020", "take_profit", signal_id="s1")
    with path.open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write('{"trade_date": "2026-06-04"}\n')  # missing code/kind
    store.record_fired("2026-06-04", "600011", "drawdown_stop", signal_id="s2")
    # Both valid rows survive; the corrupt rows are skipped, not fatal.
    assert store.load_fired("2026-06-04") == frozenset(
        {("605020", "take_profit"), ("600011", "drawdown_stop")}
    )


def test_unreadable_path_fails_open(tmp_path: Path) -> None:
    # Point the store at a DIRECTORY — read_text raises OSError; the store
    # must degrade to an empty set, never raise.
    path = tmp_path / "fired.jsonl"
    path.mkdir()
    store = FiredTriggerStore(path)
    assert store.load_fired("2026-06-04") == frozenset()


def test_prune_before_drops_old_days_keeps_recent(tmp_path: Path) -> None:
    store = FiredTriggerStore(tmp_path / "fired.jsonl")
    store.record_fired("2026-05-20", "600011", "drawdown_stop", signal_id="a")
    store.record_fired("2026-06-03", "605020", "take_profit", signal_id="b")
    store.record_fired("2026-06-04", "600909", "take_profit", signal_id="c")
    store.prune_before("2026-06-01")
    assert store.load_fired("2026-05-20") == frozenset()
    assert store.load_fired("2026-06-03") == frozenset({("605020", "take_profit")})
    assert store.load_fired("2026-06-04") == frozenset({("600909", "take_profit")})


def test_prune_keeps_unparseable_rows(tmp_path: Path) -> None:
    path = tmp_path / "fired.jsonl"
    store = FiredTriggerStore(path)
    store.record_fired("2026-05-20", "600011", "drawdown_stop", signal_id="a")
    with path.open("a", encoding="utf-8") as f:
        f.write("garbage line\n")
    store.prune_before("2026-06-01")
    # The old row is gone; the unparseable row survives (pruning never
    # destroys what it cannot read).
    text = path.read_text(encoding="utf-8")
    assert "600011" not in text
    assert "garbage line" in text


def test_load_skips_empty_code_or_kind(tmp_path: Path) -> None:
    path = tmp_path / "fired.jsonl"
    store = FiredTriggerStore(path)
    with path.open("w", encoding="utf-8") as f:
        f.write('{"trade_date": "2026-06-04", "code": "", "kind": "x"}\n')
        f.write('{"trade_date": "2026-06-04", "code": "600011", "kind": " "}\n')
        f.write(
            '{"trade_date": "2026-06-04", "code": "605020", "kind": "take_profit"}\n'
        )
    assert store.load_fired("2026-06-04") == frozenset({("605020", "take_profit")})


def test_write_failure_fails_open(tmp_path: Path) -> None:
    path = tmp_path / "fired.jsonl"
    path.mkdir()  # open("a") on a directory → OSError
    store = FiredTriggerStore(path)
    # Must not raise (fail-open; the in-memory dedup still protects this
    # process — only restart durability is degraded, loudly logged).
    store.record_fired("2026-06-04", "605020", "take_profit", signal_id="s1")
