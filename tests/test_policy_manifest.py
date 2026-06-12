"""AA-004 policy manifest hash + segment ledger tests."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.services.policy_manifest import (
    POLICY_CONFIG_FILES,
    POLICY_ENV_FLAGS,
    MongoPolicySegmentStore,
    PolicySegmentRecord,
    build_policy_components,
    compute_policy_hash,
    ensure_policy_segment,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = dt.datetime(2026, 6, 12, 9, 0, tzinfo=SHANGHAI)


@pytest.fixture(autouse=True)
def _clear_policy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in POLICY_ENV_FLAGS:
        monkeypatch.delenv(flag, raising=False)


def _seed_config(root: Path) -> None:
    (root / "config").mkdir(exist_ok=True)
    for rel in POLICY_CONFIG_FILES:
        (root / rel).write_text(f"content-of-{rel}\n")


class TestComputePolicyHash:
    def test_deterministic(self, tmp_path: Path) -> None:
        _seed_config(tmp_path)
        assert compute_policy_hash(repo_root=tmp_path) == (
            compute_policy_hash(repo_root=tmp_path)
        )

    def test_config_file_change_moves_hash(self, tmp_path: Path) -> None:
        _seed_config(tmp_path)
        before = compute_policy_hash(repo_root=tmp_path)
        (tmp_path / "config/risk.yaml").write_text("changed: true\n")
        assert compute_policy_hash(repo_root=tmp_path) != before

    def test_env_flag_moves_hash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(tmp_path)
        before = compute_policy_hash(repo_root=tmp_path)
        monkeypatch.setenv(POLICY_ENV_FLAGS[0], "1")
        assert compute_policy_hash(repo_root=tmp_path) != before

    def test_non_truthy_env_value_is_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_config(tmp_path)
        before = compute_policy_hash(repo_root=tmp_path)
        monkeypatch.setenv(POLICY_ENV_FLAGS[0], "false")
        assert compute_policy_hash(repo_root=tmp_path) == before

    def test_missing_file_encoded_as_absent(self, tmp_path: Path) -> None:
        _seed_config(tmp_path)
        (tmp_path / "config/risk.yaml").unlink()
        components = build_policy_components(repo_root=tmp_path)
        assert components["file:config/risk.yaml"] == "absent"

    def test_components_cover_code_versions(self, tmp_path: Path) -> None:
        _seed_config(tmp_path)
        components = build_policy_components(repo_root=tmp_path)
        assert "code:sell_stack" in components
        assert "code:screener" in components
        # The real version constants resolve in this repo.
        assert components["code:sell_stack"].startswith("monitoring.")


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int) -> _FakeCursor:
        self._docs = sorted(
            self._docs,
            key=lambda d: d.get(field),
            reverse=direction == -1,
        )
        return self

    def limit(self, n: int) -> _FakeCursor:
        self._docs = self._docs[:n]
        return self

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeColl:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.docs.append(dict(document))

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor(list(self.docs))


class _FakeDb:
    def __init__(self) -> None:
        self.coll = _FakeColl()

    def __getitem__(self, name: str) -> _FakeColl:
        assert name == MongoPolicySegmentStore.COLLECTION
        return self.coll


class TestEnsurePolicySegment:
    @pytest.mark.asyncio
    async def test_first_boot_opens_segment(self, tmp_path: Path) -> None:
        _seed_config(tmp_path)
        store = MongoPolicySegmentStore(_FakeDb())
        record = await ensure_policy_segment(
            store, now=NOW, trade_date="2026-06-12", repo_root=tmp_path
        )
        assert record.policy_hash == compute_policy_hash(
            repo_root=tmp_path
        )
        assert (await store.latest()) is not None

    @pytest.mark.asyncio
    async def test_unchanged_hash_does_not_append(
        self, tmp_path: Path
    ) -> None:
        _seed_config(tmp_path)
        db = _FakeDb()
        store = MongoPolicySegmentStore(db)
        first = await ensure_policy_segment(
            store, now=NOW, trade_date="2026-06-12", repo_root=tmp_path
        )
        second = await ensure_policy_segment(
            store,
            now=NOW + dt.timedelta(days=1),
            trade_date="2026-06-13",
            repo_root=tmp_path,
        )
        assert second.segment_id == first.segment_id
        assert len(db.coll.docs) == 1

    @pytest.mark.asyncio
    async def test_changed_hash_appends_transition_row(
        self, tmp_path: Path
    ) -> None:
        _seed_config(tmp_path)
        db = _FakeDb()
        store = MongoPolicySegmentStore(db)
        first = await ensure_policy_segment(
            store, now=NOW, trade_date="2026-06-12", repo_root=tmp_path
        )
        (tmp_path / "config/risk.yaml").write_text("changed: true\n")
        second = await ensure_policy_segment(
            store,
            now=NOW + dt.timedelta(days=1),
            trade_date="2026-06-13",
            repo_root=tmp_path,
        )
        assert second.policy_hash != first.policy_hash
        assert len(db.coll.docs) == 2
        # Append-only: the first row is untouched.
        rows = await store.list_all()
        assert [r.policy_hash for r in rows] == [
            first.policy_hash,
            second.policy_hash,
        ]

    @pytest.mark.asyncio
    async def test_store_has_no_update_or_delete_surface(self) -> None:
        forbidden = {"update", "update_one", "delete", "delete_one"}
        public = {
            n
            for n in dir(MongoPolicySegmentStore)
            if not n.startswith("_")
        }
        assert forbidden.isdisjoint(public)


class TestPolicySegmentRecord:
    def test_rejects_non_sha256_hash(self) -> None:
        with pytest.raises(Exception, match="policy_hash"):
            PolicySegmentRecord(
                policy_hash="not-a-hash",
                started_at=NOW,
                trade_date="2026-06-12",
            )
