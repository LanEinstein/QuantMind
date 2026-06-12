"""AB-001 ExperimentRegistry tests (append-only, content-addressed)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from backend.strategy_evolution.experiment_registry import (
    ExperimentKind,
    ExperimentRecord,
    MongoExperimentRegistry,
    bonferroni_alpha,
    compute_experiment_id,
)

NOW = dt.datetime(2026, 6, 12, 22, 0, tzinfo=dt.UTC)
HASH = "c" * 64


def _design_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": ExperimentKind.THRESHOLD_PARAM,
        "family": "line2.drawdown_stop",
        "hypothesis": "wider drawdown quantile improves net PnL",
        "artifact_hash": HASH,
        "param_space": {"line2.drawdown_quantile": "0.92"},
        "window_start": "2026-05-01",
        "window_end": "2026-05-31",
    }
    base.update(overrides)
    return base


def _record(*, success: bool = False, **overrides: Any) -> ExperimentRecord:
    design = _design_kwargs(**overrides)
    return ExperimentRecord(
        experiment_id=compute_experiment_id(**design),
        trading_days=20,
        sample_count=42,
        metrics={"excess_pnl_cny": -120.0},
        ci_low=-1.2,
        ci_high=0.4,
        success=success,
        registered_at=NOW,
        **design,
    )


class TestContentAddressing:
    def test_design_identity_is_stable(self) -> None:
        a = compute_experiment_id(**_design_kwargs())
        b = compute_experiment_id(**_design_kwargs())
        assert a == b

    def test_param_change_moves_id(self) -> None:
        a = compute_experiment_id(**_design_kwargs())
        b = compute_experiment_id(
            **_design_kwargs(
                param_space={"line2.drawdown_quantile": "0.85"}
            )
        )
        assert a != b

    def test_outcome_does_not_move_id(self) -> None:
        """The id addresses the DESIGN — outcome laundering impossible."""
        failed = _record(success=False)
        succeeded = _record(success=True)
        assert failed.experiment_id == succeeded.experiment_id


class TestBonferroni:
    def test_monotone_tightening(self) -> None:
        alphas = [bonferroni_alpha(0.05, n) for n in (1, 5, 50, 500)]
        assert alphas == sorted(alphas, reverse=True)
        assert alphas[0] == 0.05
        assert alphas[-1] == pytest.approx(0.0001)

    def test_zero_trials_keeps_base(self) -> None:
        assert bonferroni_alpha(0.05, 0) == 0.05

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(ValueError):
            bonferroni_alpha(0.0, 5)
        with pytest.raises(ValueError):
            bonferroni_alpha(0.05, -1)


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str, direction: int) -> _FakeCursor:
        self._docs = sorted(
            self._docs, key=lambda d: d.get(field), reverse=direction == -1
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

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if doc.get("experiment_id") == query.get("experiment_id"):
                return dict(doc)
        return None

    async def count_documents(self, query: dict[str, Any]) -> int:
        if not query:
            return len(self.docs)
        return sum(
            1
            for d in self.docs
            if all(d.get(k) == v for k, v in query.items())
        )

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        rows = [
            d
            for d in self.docs
            if all(d.get(k) == v for k, v in query.items())
        ]
        return _FakeCursor(rows)


class _FakeDb:
    def __init__(self) -> None:
        self.coll = _FakeColl()

    def __getitem__(self, name: str) -> _FakeColl:
        assert name == MongoExperimentRegistry.COLLECTION
        return self.coll


class TestRegistry:
    @pytest.mark.asyncio
    async def test_register_round_trip_including_failure(self) -> None:
        registry = MongoExperimentRegistry(_FakeDb())
        record = _record(success=False)
        assert await registry.register(record) is True
        revived = await registry.get(record.experiment_id)
        assert revived is not None
        assert revived.success is False
        assert revived.metrics["excess_pnl_cny"] == -120.0

    @pytest.mark.asyncio
    async def test_duplicate_design_is_idempotent_skip(self) -> None:
        db = _FakeDb()
        registry = MongoExperimentRegistry(db)
        assert await registry.register(_record(success=False)) is True
        # Re-registering the same design (even with a "better" outcome)
        # cannot rewrite the row.
        assert await registry.register(_record(success=True)) is False
        assert len(db.coll.docs) == 1
        revived = await registry.get(_record().experiment_id)
        assert revived is not None
        assert revived.success is False

    @pytest.mark.asyncio
    async def test_count_trials_includes_failures(self) -> None:
        registry = MongoExperimentRegistry(_FakeDb())
        await registry.register(_record(success=False))
        await registry.register(
            _record(
                success=True,
                param_space={"line2.drawdown_quantile": "0.85"},
            )
        )
        await registry.register(
            _record(success=False, family="prompt.fund_manager")
        )
        assert await registry.count_trials("line2.drawdown_stop") == 2
        assert await registry.count_trials() == 3

    @pytest.mark.asyncio
    async def test_no_update_or_delete_surface(self) -> None:
        forbidden = {"update", "update_one", "delete", "delete_one"}
        public = {
            n
            for n in dir(MongoExperimentRegistry)
            if not n.startswith("_")
        }
        assert forbidden.isdisjoint(public)
