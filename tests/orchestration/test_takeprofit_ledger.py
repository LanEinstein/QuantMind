"""D1-d take-profit tier ledger (P0-10-amendment-line2-2026-06-04).

Append-only JSONL + fold (the V-003 / entry_rank pattern): TIER_TAKEN
accumulates per open episode; EPISODE_CLOSED resets; corrupt rows fail
closed (raise) — tiers are never guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.orchestration.takeprofit_ledger import (
    TakeProfitLedgerError,
    TakeProfitLedgerStore,
)


@pytest.fixture()
def store(tmp_path: Path) -> TakeProfitLedgerStore:
    return TakeProfitLedgerStore(tmp_path / "episodes.jsonl")


def test_empty_ledger_has_no_tiers(store: TakeProfitLedgerStore) -> None:
    assert store.tiers_taken() == {}


def test_tier_taken_accumulates(store: TakeProfitLedgerStore) -> None:
    store.record_tier(
        "600519", tier=1, trade_date="2026-06-04", signal_id="LINE2-MON-x"
    )
    assert store.tiers_taken() == {"600519": 1}
    store.record_tier(
        "600519", tier=2, trade_date="2026-06-05", signal_id="LINE2-MON-y"
    )
    assert store.tiers_taken() == {"600519": 2}


def test_episode_close_resets_count(store: TakeProfitLedgerStore) -> None:
    store.record_tier(
        "600519", tier=1, trade_date="2026-06-04", signal_id="LINE2-MON-x"
    )
    closed = store.sync_episodes(frozenset(), trade_date="2026-06-05")
    assert closed == ("600519",)
    assert store.tiers_taken() == {}
    # A re-buy starts a FRESH episode from tier 1.
    store.record_tier(
        "600519", tier=1, trade_date="2026-06-06", signal_id="LINE2-MON-z"
    )
    assert store.tiers_taken() == {"600519": 1}


def test_sync_keeps_open_episodes_for_held_codes(
    store: TakeProfitLedgerStore,
) -> None:
    store.record_tier(
        "600519", tier=1, trade_date="2026-06-04", signal_id="LINE2-MON-x"
    )
    closed = store.sync_episodes(
        frozenset({"600519"}), trade_date="2026-06-05"
    )
    assert closed == ()
    assert store.tiers_taken() == {"600519": 1}


def test_sync_is_idempotent_when_nothing_open(
    store: TakeProfitLedgerStore,
) -> None:
    assert store.sync_episodes(frozenset(), trade_date="2026-06-04") == ()
    assert store.sync_episodes(frozenset(), trade_date="2026-06-04") == ()
    assert store.tiers_taken() == {}


def test_corrupt_row_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "episodes.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    store = TakeProfitLedgerStore(path)
    with pytest.raises(TakeProfitLedgerError, match="corrupt"):
        store.tiers_taken()


def test_malformed_event_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "episodes.jsonl"
    path.write_text('{"event_type":"nope","code":"600519"}\n', encoding="utf-8")
    store = TakeProfitLedgerStore(path)
    with pytest.raises(TakeProfitLedgerError):
        store.tiers_taken()


def test_fold_is_replayable_bit_exact(store: TakeProfitLedgerStore) -> None:
    store.record_tier(
        "600519", tier=1, trade_date="2026-06-04", signal_id="LINE2-MON-x"
    )
    store.record_tier(
        "510300", tier=1, trade_date="2026-06-04", signal_id="LINE2-MON-x"
    )
    store.sync_episodes(frozenset({"600519"}), trade_date="2026-06-05")
    first = store.tiers_taken()
    # A second store over the same file folds to the identical state.
    again = TakeProfitLedgerStore(store.path).tiers_taken()
    assert first == again == {"600519": 1}


def test_tier_taken_row_without_tier_fails_closed(tmp_path: Path) -> None:
    # codex P2: a syntactically-valid TIER_TAKEN row with no positive tier
    # must raise, never silently advance/suppress the ladder.
    path = tmp_path / "episodes.jsonl"
    path.write_text(
        '{"event_type":"tier_taken","code":"600519",'
        '"trade_date":"2026-06-04"}\n',
        encoding="utf-8",
    )
    with pytest.raises(TakeProfitLedgerError, match="positive tier"):
        TakeProfitLedgerStore(path).tiers_taken()
