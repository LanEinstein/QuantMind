"""PositionEpisodeStore — entry dates for the entry-anchored chandelier (E1).

Fail-open by design: a broken store yields no entry date and the trigger
evaluator falls back to the v8 window stop — protection never disappears
(P0-7-amendment-2026-06-04-entry-anchored-chandelier §1.2).
"""

from __future__ import annotations

from pathlib import Path

from backend.orchestration.position_episode_store import PositionEpisodeStore


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    store = PositionEpisodeStore(tmp_path / "nope" / "episodes.jsonl")
    assert store.open_episodes() == {}


def test_sync_opens_and_closes_episodes(tmp_path: Path) -> None:
    store = PositionEpisodeStore(tmp_path / "episodes.jsonl")
    out = store.sync(frozenset({"600011", "605020"}), trade_date="2026-06-02")
    assert out == {"600011": "2026-06-02", "605020": "2026-06-02"}
    # Same membership next day → idempotent, opened dates unchanged.
    out = store.sync(frozenset({"600011", "605020"}), trade_date="2026-06-03")
    assert out == {"600011": "2026-06-02", "605020": "2026-06-02"}
    # 600011 exits; a new code enters.
    out = store.sync(frozenset({"605020", "600909"}), trade_date="2026-06-04")
    assert out == {"605020": "2026-06-02", "600909": "2026-06-04"}
    # A re-buy of 600011 starts a FRESH episode.
    out = store.sync(
        frozenset({"605020", "600909", "600011"}), trade_date="2026-06-05"
    )
    assert out["600011"] == "2026-06-05"


def test_seeded_opened_row_takes_precedence(tmp_path: Path) -> None:
    # The owner may seed a REAL entry date before boot (amendment §1.2);
    # a later sync must keep the seeded (first) date, never advance it.
    path = tmp_path / "episodes.jsonl"
    path.write_text(
        '{"code":"605111","event_type":"opened","trade_date":"2026-06-01"}\n',
        encoding="utf-8",
    )
    store = PositionEpisodeStore(path)
    out = store.sync(frozenset({"605111"}), trade_date="2026-06-10")
    assert out == {"605111": "2026-06-01"}


def test_corrupt_rows_fail_open(tmp_path: Path) -> None:
    path = tmp_path / "episodes.jsonl"
    store = PositionEpisodeStore(path)
    store.sync(frozenset({"600011"}), trade_date="2026-06-02")
    with path.open("a", encoding="utf-8") as f:
        f.write("garbage\n")
        f.write('{"code":"605020","event_type":"warp","trade_date":"x"}\n')
        f.write('{"code":"","event_type":"opened","trade_date":"2026-06-03"}\n')
    assert store.open_episodes() == {"600011": "2026-06-02"}


def test_unreadable_path_fails_open(tmp_path: Path) -> None:
    path = tmp_path / "episodes.jsonl"
    path.mkdir()  # read_text on a directory → OSError
    store = PositionEpisodeStore(path)
    assert store.open_episodes() == {}
