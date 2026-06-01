"""V-004 — first-seen entry-rank ledger (open/close lifecycle, replayable)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.slot_portfolio.entry_rank import EntryRankError, EntryRankStore


def _store(tmp_path: Path) -> EntryRankStore:
    return EntryRankStore(tmp_path / "entry.jsonl")


class TestFirstSeen:
    def test_records_baseline_on_first_seen(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        opened = store.sync_holdings(
            frozenset({"600001"}), trade_date="20260601",
            percentile_by_code={"600001": 0.70}, score_by_code={"600001": 0.65},
        )
        assert opened == ("600001",)
        assert store.entry_percentile_for("600001") == 0.70
        entry = store.open_entries()["600001"]
        assert entry.first_seen_trade_date == "20260601"
        assert entry.entry_score == 0.65

    def test_baseline_is_sticky_not_overwritten(self, tmp_path: Path) -> None:
        # A second sync with a different percentile must NOT move the baseline.
        store = _store(tmp_path)
        store.sync_holdings(
            frozenset({"600001"}), trade_date="20260601",
            percentile_by_code={"600001": 0.70}, score_by_code={"600001": 0.7},
        )
        opened = store.sync_holdings(
            frozenset({"600001"}), trade_date="20260602",
            percentile_by_code={"600001": 0.30}, score_by_code={"600001": 0.3},
        )
        assert opened == ()
        assert store.entry_percentile_for("600001") == 0.70  # unchanged

    def test_unknown_code_has_no_baseline(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        assert store.entry_percentile_for("999999") is None

    def test_held_without_percentile_today_not_recorded(self, tmp_path: Path) -> None:
        # A holding that fell out of the universe (no percentile) gets no baseline
        # yet — condition 5 stays fail-closed until one exists.
        store = _store(tmp_path)
        opened = store.sync_holdings(
            frozenset({"600001"}), trade_date="20260601",
            percentile_by_code={}, score_by_code={},
        )
        assert opened == ()
        assert store.entry_percentile_for("600001") is None


class TestLifecycle:
    def test_exit_closes_baseline(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.sync_holdings(
            frozenset({"600001"}), trade_date="20260601",
            percentile_by_code={"600001": 0.70}, score_by_code={"600001": 0.7},
        )
        # Next day the code is no longer held → baseline retired.
        store.sync_holdings(
            frozenset(), trade_date="20260602",
            percentile_by_code={}, score_by_code={},
        )
        assert store.entry_percentile_for("600001") is None

    def test_rebuy_gets_fresh_baseline(self, tmp_path: Path) -> None:
        # Sold then re-bought → a NEW baseline (never the stale one).
        store = _store(tmp_path)
        store.sync_holdings(
            frozenset({"600001"}), trade_date="20260601",
            percentile_by_code={"600001": 0.70}, score_by_code={"600001": 0.7},
        )
        store.sync_holdings(
            frozenset(), trade_date="20260602",
            percentile_by_code={}, score_by_code={},
        )
        opened = store.sync_holdings(
            frozenset({"600001"}), trade_date="20260610",
            percentile_by_code={"600001": 0.55}, score_by_code={"600001": 0.5},
        )
        assert opened == ("600001",)
        assert store.entry_percentile_for("600001") == 0.55  # fresh, not 0.70
        assert store.open_entries()["600001"].first_seen_trade_date == "20260610"


class TestPersistence:
    def test_survives_reload(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.sync_holdings(
            frozenset({"600001", "510300"}), trade_date="20260601",
            percentile_by_code={"600001": 0.7, "510300": 0.5},
            score_by_code={"600001": 0.7, "510300": 0.5},
        )
        reloaded = EntryRankStore(tmp_path / "entry.jsonl")
        assert reloaded.entry_percentile_for("600001") == 0.7
        assert reloaded.entry_percentile_for("510300") == 0.5

    def test_corrupt_row_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "entry.jsonl"
        path.write_text("{bad}\n", encoding="utf-8")
        with pytest.raises(EntryRankError, match="corrupt"):
            EntryRankStore(path).open_entries()

    def test_blank_line_skipped(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.sync_holdings(
            frozenset({"600001"}), trade_date="20260601",
            percentile_by_code={"600001": 0.7}, score_by_code={"600001": 0.7},
        )
        with (tmp_path / "entry.jsonl").open("a", encoding="utf-8") as f:
            f.write("\n")
        assert store.entry_percentile_for("600001") == 0.7

    def test_store_path_property(self, tmp_path: Path) -> None:
        path = tmp_path / "entry.jsonl"
        assert EntryRankStore(path).path == path
