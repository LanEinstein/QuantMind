"""AB-003 PromotionIntent ledger tests (mode gate + freeze + allowlist)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from backend.strategy_evolution.experiment_registry import ExperimentKind
from backend.strategy_evolution.promotion_intent import (
    IntentAction,
    IntentStatus,
    InvalidIntentTransitionError,
    MongoPromotionIntentLedger,
    PromotionModeError,
    build_promotion_intent,
)

NOW = dt.datetime(2026, 6, 12, 22, 0, tzinfo=dt.UTC)
HASH = "f" * 64


@pytest.fixture(autouse=True)
def _pure_sim_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "false")


def _intent(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "action": IntentAction.PROMOTE,
        "kind": ExperimentKind.THRESHOLD_PARAM,
        "family": "line2.drawdown_stop",
        "artifact_hash": HASH,
        "experiment_id": "e" * 64,
        "decision_digest": "d" * 64,
        "manifest_hash": "a" * 64,
        "previous_manifest_hash": None,
        "created_at": NOW,
        "decision_promoted": True,
    }
    base.update(overrides)
    return build_promotion_intent(**base)


class TestModeAndDecisionGate:
    def test_feishu_mode_refuses_intent_creation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Amendment §2 — the live domain keeps the human gate."""
        monkeypatch.setenv("FEISHU_INTERACTIVE_ENABLED", "true")
        with pytest.raises(PromotionModeError):
            _intent()

    def test_failed_decision_cannot_become_promote_intent(self) -> None:
        with pytest.raises(ValueError, match="promoted=True"):
            _intent(decision_promoted=False)

    def test_demote_intent_allowed_without_promoted_decision(self) -> None:
        intent = _intent(
            action=IntentAction.DEMOTE, decision_promoted=False
        )
        assert intent.action is IntentAction.DEMOTE


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
            if all(d.get(k) == v for k, v in query.items())
        ]
        return _FakeCursor(rows)


class _FakeDb:
    def __init__(self) -> None:
        self.coll = _FakeColl()

    def __getitem__(self, name: str) -> _FakeColl:
        assert name == MongoPromotionIntentLedger.COLLECTION
        return self.coll


class TestLedger:
    @pytest.mark.asyncio
    async def test_open_then_activate_round_trip(self) -> None:
        ledger = MongoPromotionIntentLedger(_FakeDb())
        intent = _intent()
        await ledger.open_intent(intent, reason="decision promoted")
        assert (
            await ledger.current_status(intent.intent_id)
        ) is IntentStatus.PENDING
        await ledger.record_status(
            intent.intent_id,
            IntentStatus.ACTIVATED,
            at=NOW + dt.timedelta(hours=10),
            reason="boot:applied",
        )
        assert (
            await ledger.current_status(intent.intent_id)
        ) is IntentStatus.ACTIVATED
        revived = await ledger.get_intent(intent.intent_id)
        assert revived is not None
        assert revived.manifest_hash == "a" * 64

    @pytest.mark.asyncio
    async def test_disallowed_transition_rejected(self) -> None:
        ledger = MongoPromotionIntentLedger(_FakeDb())
        intent = _intent()
        await ledger.open_intent(intent, reason="opened")
        await ledger.record_status(
            intent.intent_id,
            IntentStatus.CANCELLED,
            at=NOW,
            reason="superseded",
        )
        with pytest.raises(InvalidIntentTransitionError):
            await ledger.record_status(
                intent.intent_id,
                IntentStatus.ACTIVATED,
                at=NOW,
                reason="zombie activation",
            )

    @pytest.mark.asyncio
    async def test_freeze_all_pending_on_mode_switch(self) -> None:
        """Amendment §2 — mode switch freezes in-flight intents."""
        ledger = MongoPromotionIntentLedger(_FakeDb())
        a = _intent()
        b = _intent(family="prompt.fund_manager")
        await ledger.open_intent(a, reason="opened")
        await ledger.open_intent(b, reason="opened")
        await ledger.record_status(
            b.intent_id, IntentStatus.ACTIVATED, at=NOW, reason="boot"
        )
        frozen = await ledger.freeze_all_pending(
            at=NOW + dt.timedelta(minutes=1),
            reason="mode_switch:sim->feishu",
        )
        assert frozen == (a.intent_id,)
        assert (
            await ledger.current_status(a.intent_id)
        ) is IntentStatus.FROZEN
        # Activated intents are untouched.
        assert (
            await ledger.current_status(b.intent_id)
        ) is IntentStatus.ACTIVATED

    @pytest.mark.asyncio
    async def test_frozen_intent_can_only_cancel(self) -> None:
        ledger = MongoPromotionIntentLedger(_FakeDb())
        intent = _intent()
        await ledger.open_intent(intent, reason="opened")
        await ledger.record_status(
            intent.intent_id, IntentStatus.FROZEN, at=NOW, reason="switch"
        )
        with pytest.raises(InvalidIntentTransitionError):
            await ledger.record_status(
                intent.intent_id,
                IntentStatus.ACTIVATED,
                at=NOW,
                reason="thaw",
            )
        await ledger.record_status(
            intent.intent_id,
            IntentStatus.CANCELLED,
            at=NOW,
            reason="owner triage",
        )
