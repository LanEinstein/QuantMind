"""O-003 PIT-pinned industry map store tests."""

from __future__ import annotations

from pathlib import Path

from backend.data.industry_map_store import IndustryMapStore


class TestIndustryMapStore:
    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        store = IndustryMapStore(tmp_path)
        store.save("2026-06-11", {"600001.SH": "半导体", "600002.SH": "银行"})
        loaded = store.load("2026-06-11")
        assert loaded == {"600001.SH": "半导体", "600002.SH": "银行"}

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        store = IndustryMapStore(tmp_path)
        assert store.load("2026-06-11") == {}

    def test_dated_isolation(self, tmp_path: Path) -> None:
        store = IndustryMapStore(tmp_path)
        store.save("2026-06-10", {"a": "x"})
        store.save("2026-06-11", {"b": "y"})
        assert store.load("2026-06-10") == {"a": "x"}
        assert store.load("2026-06-11") == {"b": "y"}

    def test_deterministic_file_bytes(self, tmp_path: Path) -> None:
        store = IndustryMapStore(tmp_path)
        store.save("2026-06-11", {"b": "y", "a": "x"})
        first = (tmp_path / "2026-06-11.json").read_bytes()
        store.save("2026-06-11", {"a": "x", "b": "y"})  # different insert order
        second = (tmp_path / "2026-06-11.json").read_bytes()
        assert first == second  # key-sorted → byte-identical

    def test_corrupt_file_fails_open(self, tmp_path: Path) -> None:
        (tmp_path / "2026-06-11.json").write_text("{not json", encoding="utf-8")
        store = IndustryMapStore(tmp_path)
        assert store.load("2026-06-11") == {}

    def test_non_dict_payload_fails_open(self, tmp_path: Path) -> None:
        (tmp_path / "2026-06-11.json").write_text("[1, 2, 3]", encoding="utf-8")
        store = IndustryMapStore(tmp_path)
        assert store.load("2026-06-11") == {}

    def test_path_traversal_neutralized(self, tmp_path: Path) -> None:
        store = IndustryMapStore(tmp_path)
        # Separators stripped so a crafted date can't escape the root.
        store.save("../evil", {"a": "x"})
        assert not (tmp_path.parent / "evil.json").exists()
