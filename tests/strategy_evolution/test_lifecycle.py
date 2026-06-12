"""R-002 strategy lifecycle state machine + ledger tests."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from backend.strategy_evolution.lifecycle import (
    ALLOWED_LIFECYCLE_TRANSITIONS,
    InvalidLifecycleTransitionError,
    MongoLifecycleLedger,
    StrategyLifecycleRecord,
    StrategyLifecycleState,
    StrategyRetiredError,
    UnapprovedStrategyError,
    transition_lifecycle,
)
from backend.strategy_evolution.live_artifact_registry import (
    LiveArtifactRegistry,
)

NOW = dt.datetime(2026, 6, 12, 22, 0, tzinfo=dt.UTC)
HASH = "a" * 64
OTHER_HASH = "b" * 64
_S = StrategyLifecycleState


def _record(
    state: StrategyLifecycleState = _S.CANDIDATE,
    *,
    strategy_hash: str = HASH,
) -> StrategyLifecycleRecord:
    return StrategyLifecycleRecord(
        strategy_hash=strategy_hash,
        state=state,
        entered_at=NOW,
        reason="test",
    )


def _registry(tmp_path: Path, *pinned: str) -> LiveArtifactRegistry:
    lock = tmp_path / "live_artifacts.lock.json"
    lock.write_text(
        json.dumps(
            {
                "version": "1.0",
                "updated_at": "2026-06-12T00:00:00+08:00",
                "approved": {
                    "strategy_code": list(pinned),
                    "feature_def": [],
                    "prompt_version": [],
                    "anomaly_model": [],
                    "rag_index": [],
                },
            }
        )
    )
    return LiveArtifactRegistry.from_lockfile(lock)


class TestTransitionAllowlist:
    def test_happy_path_chain(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path, HASH)
        record = _record(_S.CANDIDATE)
        for target in (_S.SHADOW, _S.ACTIVE, _S.DECAYING, _S.RETIRED):
            record = transition_lifecycle(
                record,
                target,
                at=NOW + dt.timedelta(days=1),
                reason="advance",
                registry=registry,
            )
        assert record.state is _S.RETIRED

    def test_retired_is_terminal(self) -> None:
        assert not any(
            src is _S.RETIRED for src, _ in ALLOWED_LIFECYCLE_TRANSITIONS
        )
        with pytest.raises(InvalidLifecycleTransitionError):
            transition_lifecycle(
                _record(_S.RETIRED), _S.CANDIDATE, at=NOW, reason="revive"
            )

    def test_candidate_cannot_jump_to_active(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidLifecycleTransitionError):
            transition_lifecycle(
                _record(_S.CANDIDATE),
                _S.ACTIVE,
                at=NOW,
                reason="skip shadow",
                registry=_registry(tmp_path, HASH),
            )

    def test_decaying_can_recover(self, tmp_path: Path) -> None:
        moved = transition_lifecycle(
            _record(_S.DECAYING),
            _S.ACTIVE,
            at=NOW,
            reason="performance recovered",
            registry=_registry(tmp_path, HASH),
        )
        assert moved.state is _S.ACTIVE

    def test_time_must_not_rewind(self) -> None:
        with pytest.raises(ValueError, match="entered_at"):
            transition_lifecycle(
                _record(_S.CANDIDATE),
                _S.SHADOW,
                at=NOW - dt.timedelta(days=1),
                reason="back in time",
            )


class TestRegistryGatedActivation:
    """R-001 tie-in: ACTIVE requires a LiveArtifactRegistry pin."""

    def test_unpinned_hash_rejected(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)  # nothing pinned
        with pytest.raises(UnapprovedStrategyError):
            transition_lifecycle(
                _record(_S.SHADOW),
                _S.ACTIVE,
                at=NOW,
                reason="promote",
                registry=registry,
            )

    def test_valid_but_other_kind_pin_rejected(
        self, tmp_path: Path
    ) -> None:
        # OTHER_HASH pinned, HASH not — a valid-looking hash without ITS
        # pin must still be rejected.
        registry = _registry(tmp_path, OTHER_HASH)
        with pytest.raises(UnapprovedStrategyError):
            transition_lifecycle(
                _record(_S.SHADOW),
                _S.ACTIVE,
                at=NOW,
                reason="promote",
                registry=registry,
            )

    def test_missing_registry_rejected(self) -> None:
        with pytest.raises(UnapprovedStrategyError):
            transition_lifecycle(
                _record(_S.SHADOW), _S.ACTIVE, at=NOW, reason="promote"
            )

    def test_pinned_hash_promotes(self, tmp_path: Path) -> None:
        moved = transition_lifecycle(
            _record(_S.SHADOW),
            _S.ACTIVE,
            at=NOW,
            reason="promote",
            registry=_registry(tmp_path, HASH),
        )
        assert moved.state is _S.ACTIVE

    def test_non_active_targets_need_no_registry(self) -> None:
        moved = transition_lifecycle(
            _record(_S.CANDIDATE), _S.SHADOW, at=NOW, reason="screened"
        )
        assert moved.state is _S.SHADOW


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int) -> _FakeCursor:
        self._docs = sorted(
            self._docs, key=lambda d: d.get(field), reverse=direction == -1
        )
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
        rows = [
            d
            for d in self.docs
            if d.get("strategy_hash") == query.get("strategy_hash")
        ]
        return _FakeCursor(rows)


class _FakeDb:
    def __init__(self) -> None:
        self.coll = _FakeColl()

    def __getitem__(self, name: str) -> _FakeColl:
        assert name == MongoLifecycleLedger.COLLECTION
        return self.coll


class TestLedger:
    @pytest.mark.asyncio
    async def test_open_then_fold_current_state(self) -> None:
        ledger = MongoLifecycleLedger(_FakeDb())
        record = await ledger.open_candidate(
            HASH, at=NOW, reason="discovered by weekly experiment"
        )
        assert record.state is _S.CANDIDATE
        current = await ledger.current_state(HASH)
        assert current is not None
        assert current.state is _S.CANDIDATE

    @pytest.mark.asyncio
    async def test_transition_appends_and_folds(
        self, tmp_path: Path
    ) -> None:
        db = _FakeDb()
        ledger = MongoLifecycleLedger(db)
        record = await ledger.open_candidate(HASH, at=NOW, reason="found")
        record = await ledger.record_transition(
            record,
            _S.SHADOW,
            at=NOW + dt.timedelta(days=1),
            reason="screening passed",
        )
        current = await ledger.current_state(HASH)
        assert current is not None
        assert current.state is _S.SHADOW
        assert len(db.coll.docs) == 2  # append-only, both events kept

    @pytest.mark.asyncio
    async def test_retired_hash_cannot_be_reproposed(self) -> None:
        ledger = MongoLifecycleLedger(_FakeDb())
        record = await ledger.open_candidate(HASH, at=NOW, reason="found")
        await ledger.record_transition(
            record,
            _S.RETIRED,
            at=NOW + dt.timedelta(days=1),
            reason="failed screening",
        )
        with pytest.raises(StrategyRetiredError):
            await ledger.open_candidate(
                HASH,
                at=NOW + dt.timedelta(days=2),
                reason="rediscovered",
            )

    @pytest.mark.asyncio
    async def test_duplicate_open_rejected(self) -> None:
        ledger = MongoLifecycleLedger(_FakeDb())
        await ledger.open_candidate(HASH, at=NOW, reason="found")
        with pytest.raises(ValueError, match="already has a lifecycle"):
            await ledger.open_candidate(HASH, at=NOW, reason="again")

    @pytest.mark.asyncio
    async def test_unknown_hash_has_no_state(self) -> None:
        ledger = MongoLifecycleLedger(_FakeDb())
        assert await ledger.current_state(OTHER_HASH) is None

    @pytest.mark.asyncio
    async def test_ledger_has_no_update_or_delete_surface(self) -> None:
        forbidden = {"update", "update_one", "delete", "delete_one"}
        public = {
            n for n in dir(MongoLifecycleLedger) if not n.startswith("_")
        }
        assert forbidden.isdisjoint(public)


class TestStaleRecordGuard:
    """Codex R-002 P1: a stale caller view must not rewind the ledger."""

    @pytest.mark.asyncio
    async def test_stale_record_after_retire_is_rejected(self) -> None:
        from backend.strategy_evolution.lifecycle import (
            StaleLifecycleRecordError,
        )

        ledger = MongoLifecycleLedger(_FakeDb())
        record = await ledger.open_candidate(HASH, at=NOW, reason="found")
        await ledger.record_transition(
            record,
            _S.RETIRED,
            at=NOW + dt.timedelta(days=1),
            reason="failed screening",
        )
        # The caller retries with its stale CANDIDATE view — must NOT
        # append a candidate→shadow event after the terminal RETIRED.
        with pytest.raises(StaleLifecycleRecordError):
            await ledger.record_transition(
                record,
                _S.SHADOW,
                at=NOW + dt.timedelta(days=2),
                reason="stale retry",
            )
        current = await ledger.current_state(HASH)
        assert current is not None
        assert current.state is _S.RETIRED

    @pytest.mark.asyncio
    async def test_transition_without_lifecycle_rejected(self) -> None:
        from backend.strategy_evolution.lifecycle import (
            StaleLifecycleRecordError,
        )

        ledger = MongoLifecycleLedger(_FakeDb())
        with pytest.raises(StaleLifecycleRecordError):
            await ledger.record_transition(
                _record(_S.CANDIDATE),
                _S.SHADOW,
                at=NOW,
                reason="no ledger entry",
            )
