"""PILOT manifest sign-off evidence (U-E5 — all 6 ledger conds signed).

``config/pilot_readiness.yaml`` is an auditable ledger: a flag flips to
``true`` ONLY after its named task lands AND the evidence is signed off
(``backend/services/pilot_readiness.py`` header). This module is the
single place that ties the **six signed conds** to the evidence that
proves them, so the manifest state is reviewable end-to-end. cond3 was
flipped 2026-05-28 (open-market dry-run owner-reviewed); cond4 was
flipped 2026-05-29 (real Feishu smoke ping sent to decision chat,
owner acknowledged the round-trip). PILOT gate now depends solely on
the 5 live-probe conditions (cond1 active broker / cond2 owner auth /
cond8 reconciliation clear / cond9 data-quality / cond10 LLM-timeout +
cost-guard).

Evidence map (all 6 manifest conds signed; live-probe conds are separate):

* **cond5  outbox_restart_idempotent** — the durable outbox is the
  at-most-once gate; a second dispatch after a "restart" (same repo) is
  ``skipped_duplicate``. Proven by
  ``tests/orchestration/test_instruction_dispatcher.py``
  (``test_second_dispatch_after_restart_does_not_resend`` +
  ``test_pre_existing_pending_claim_is_not_resent`` +
  ``test_sent_without_ledger_recovers_bookkeeping``).
* **cond6  no_double_execution_invariant** — every VALIDATED plan routes
  down exactly ONE mutually-exclusive mode path (sim auto-fill XOR Feishu
  dispatch). Proven by ``tests/orchestration/test_route_coordinator.py``
  (``test_routes_to_executor_not_feishu`` +
  ``test_dispatches_feishu_and_never_auto_fills``) +
  ``tests/orchestration/test_no_stray_route_callers.py``.
* **cond7  all_report_templates_parse_apply** — every report template
  parses (``tests/test_execution_report_parser.py::test_keys_locked`` +
  the per-template cases) and every clarification branch leaves the
  applier untouched / AMBIGUOUS never mutates the mirror
  (``tests/test_feishu_parser_orchestrator.py`` — ``applier.calls == []``
  on every clarify branch).
* **cond11 rollback_simulation_only_ready** — the new drill below.

The drill (cond11) lives here because no test previously exercised the
**reverse** mode switch (feishu_interactive → simulation_auto). It proves
the one-click rollback is a clean account-lifecycle reset that needs NO
acceptance gate.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import AuditEventType
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.store import BrokerEventStore
from backend.models.reconciliation import ReportedPosition
from backend.services.mode_router import (
    FEISHU_INTERACTIVE,
    SIMULATION_AUTO,
    ModeRouter,
)
from backend.services.pilot_readiness import read_manifest_flags

SHANGHAI = ZoneInfo("Asia/Shanghai")


# -- minimal Mongo-session fakes (mirror test_simulation_executor) ---------


class _FakeSession:
    @asynccontextmanager
    async def start_transaction(self) -> AsyncIterator[None]:
        yield

    async def commit_transaction(self) -> None:
        return None

    async def abort_transaction(self) -> None:
        return None

    async def end_session(self) -> None:
        return None


@dataclass
class _FakeClient:
    async def start_session(self) -> _FakeSession:
        return _FakeSession()


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field_name: str, direction: int) -> _FakeCursor:
        self._docs = sorted(
            self._docs, key=lambda d: d.get(field_name, 0), reverse=direction == -1
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


@dataclass
class _FakeCollection:
    docs: list[dict[str, Any]] = field(default_factory=list)

    async def insert_one(self, document: dict[str, Any], session: Any = None) -> None:
        self.docs.append(dict(document))

    def find(self, filter: Any = None, projection: Any = None) -> _FakeCursor:  # noqa: A002
        return _FakeCursor(list(self.docs))


# -- cond11 — one-click rollback to simulation-only drill ------------------


@pytest.mark.asyncio
async def test_cond11_rollback_to_simulation_only_resets_account(
    tmp_path: Path,
) -> None:
    """feishu_interactive → simulation_auto archives state + resets broker.

    Cond 11 evidence: the rollback is a clean account-lifecycle reset
    (MODE_SWITCH_RESET archive + broker reset to initial capital + audit
    INITIATED/MOCKBROKER_RESET/COMPLETED) and — critically — needs NO
    acceptance gate (acceptance_gate=None), because rolling BACK to the
    always-safe simulation-only baseline is never gated.
    """
    broker = MockBroker(
        config=BrokerConfig(initial_capital=1_000_000.0),
        now_func=lambda: dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
    )
    event_coll = _FakeCollection()
    event_store = BrokerEventStore(_FakeClient(), event_coll)
    audit_coll = InMemoryAuditCollection()
    audit_store = AuditStore(audit_coll, jsonl_path=tmp_path / "audit.jsonl")
    # Start ALREADY in feishu_interactive; no acceptance gate wired — the
    # rollback path must not consult it.
    router = ModeRouter(
        broker=broker,
        event_store=event_store,
        audit_store=audit_store,
        initial_mode=FEISHU_INTERACTIVE,
        acceptance_gate=None,
    )

    # Seed some live state so we can prove the reset clears it.
    await broker.reset_to_snapshot(
        cash=850_000.0,
        positions=(ReportedPosition(code="600519", volume=200, cost_price=1_700.0),),
        reset_at=dt.datetime(2026, 5, 15, 9, 30, tzinfo=SHANGHAI),
        reason="seed_interactive",
    )

    result = await router.switch_mode(
        to_mode=SIMULATION_AUTO,
        reason="one_click_rollback_drill",
        initiated_by="cli",
        when=dt.datetime(2026, 5, 15, 16, 30, tzinfo=SHANGHAI),
    )

    assert result.from_mode == FEISHU_INTERACTIVE
    assert result.to_mode == SIMULATION_AUTO
    assert router.current_mode == SIMULATION_AUTO
    # Flag always cleared (finally:) so trading is never left frozen.
    assert router.mode_state.is_active() is False

    # Archive event recorded with the rollback payload.
    reset_events = [
        d
        for d in event_coll.docs
        if d["event_type"] == BrokerEventType.MODE_SWITCH_RESET.value
    ]
    assert len(reset_events) == 1
    payload = reset_events[0]["payload"]
    assert payload["from_mode"] == FEISHU_INTERACTIVE
    assert payload["to_mode"] == SIMULATION_AUTO
    # Rollback to simulation_auto carries no go-live tier.
    assert payload["go_live_tier"] is None

    # Audit lifecycle trio present.
    types = {d["event_type"] for d in audit_coll.documents}
    assert AuditEventType.MODE_SWITCH_INITIATED.value in types
    assert AuditEventType.MOCKBROKER_RESET.value in types
    assert AuditEventType.MODE_SWITCH_COMPLETED.value in types

    # Broker mirror reset to the initial-capital baseline, positions cleared.
    account = await broker.get_account()
    assert account.available_cash == pytest.approx(1_000_000.0)
    assert (await broker.get_positions()) == ()


# -- manifest ledger state (the four test sign-offs flipped in U-E5) -------


def test_committed_manifest_signs_off_all_six_conds() -> None:
    """All 6 manifest conds are signed off; PILOT gate depends only on live-probe.

    U-E5 (A) landed cond5/6/7 (existing dispatcher/route/parser tests) +
    cond11 (the drill above). U-E5 (B) prerequisite landed cond3 on
    2026-05-28 (open-market dry-run rendered 3 real BUYs with full
    quant + analyst-reasoning rationale + cage-derived limits, owner-
    reviewed) and cond4 on 2026-05-29 (real Feishu smoke ping sent to the
    decision chat oc_77e23..., owner-acknowledged the round-trip;
    message_id=om_x100b6e49fbd98c98c327a5e3b29f142, cost ¥0.20). This
    locks the ledger so a regression flipping any signed cond back is
    caught; the PILOT acceptance gate now depends solely on the 5
    live-probe conditions (cond1/2/8/9/10).
    """
    flags = read_manifest_flags(Path("config/pilot_readiness.yaml"))
    assert flags == {
        "dry_run_double_line_pass": True,  # cond3 — owner-reviewed 2026-05-28
        "feishu_send_recv_smoke_pass": True,  # cond4 — owner-acknowledged 2026-05-29
        "outbox_restart_idempotent": True,  # cond5 — test sign-off
        "no_double_execution_invariant": True,  # cond6 — test sign-off
        "all_report_templates_parse_apply": True,  # cond7 — test sign-off
        "rollback_simulation_only_ready": True,  # cond11 — drill above
    }
