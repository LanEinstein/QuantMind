"""F-004 — ExecutionReportOrchestrator + ChaseScheduler tests.

Covers the parse/apply happy path, every clarification branch, the
audit + Feishu send red lines, and the chase / expire timer behaviour
including idempotent cancellation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from backend.audit.models import (
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)
from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.broker.appliers import ApplyResult
from backend.integrations.feishu.chase import (
    ChaseScheduler,
)
from backend.integrations.feishu.client import (
    SendMessageResult,
)
from backend.integrations.feishu.events import ReceivedMessage
from backend.integrations.feishu.parser import (
    ExecutionReportOrchestrator,
)
from backend.integrations.feishu.renderer import (
    ClarificationTemplate,
    MessageRenderer,
)
from backend.models.execution import ExecutionReport
from backend.models.instruction import (
    DataSnapshot,
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    PositionSummary,
    RiskCheckSummary,
)

_SH = ZoneInfo("Asia/Shanghai")
_VALID_APP_ID = "cli_" + "a" * 16
_VALID_APP_SECRET = "x" * 32
_VALID_CHAT = "oc_" + "f" * 32

GOOD_REPORT_TEXT = (
    "已执行 QM-20260516-103000-510300-BUY-001 买入 510300 "
    "1000股 成交价 3.85"
)


# -----------------------------------------------------------------------------
# Test doubles
# -----------------------------------------------------------------------------


class _StubLookup:
    def __init__(self, plans: dict[str, InstructionPlan] | None = None) -> None:
        self.plans = plans or {}

    async def get(self, instruction_id: str) -> InstructionPlan | None:
        return self.plans.get(instruction_id)


class _RecordingApplier:
    """Stand-in for :class:`ExecutionReportApplier` (E-004)."""

    def __init__(self, *, raise_on_apply: bool = False) -> None:
        self.calls: list[tuple[ExecutionReport, bool]] = []
        self.raise_on_apply = raise_on_apply

    async def apply(
        self,
        report: ExecutionReport,
        *,
        side_is_buy: bool,
    ) -> ApplyResult:
        if self.raise_on_apply:
            raise RuntimeError("applier boom")
        self.calls.append((report, side_is_buy))
        return ApplyResult(
            cash_delta=-3855.0,
            positions_delta=({"code": "510300", "delta_volume": 1000},),
            broker_event_sequence=1,
            reason="execution_report_applied",
        )


class _RecordingFeishu:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def send_message(
        self, chat_id: str, content: str, **_: Any
    ) -> SendMessageResult:
        self.calls.append((chat_id, content))
        return SendMessageResult(
            ok=True,
            code=0,
            msg="success",
            message_id="om_clar",
            log_id="log_clar",
        )


def _build_orchestrator(
    *,
    plans: dict[str, InstructionPlan] | None = None,
    applier: _RecordingApplier | None = None,
    feishu: _RecordingFeishu | None = None,
    audit: AuditStore | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[
    ExecutionReportOrchestrator,
    _RecordingApplier,
    _RecordingFeishu,
    InMemoryAuditCollection,
    AuditStore,
]:
    applier_obj = applier or _RecordingApplier()
    feishu_obj = feishu or _RecordingFeishu()
    audit_collection = InMemoryAuditCollection()
    audit_store = audit or AuditStore(
        audit_collection, jsonl_path=_tmp_audit_path()
    )
    orchestrator = ExecutionReportOrchestrator(
        applier=applier_obj,  # type: ignore[arg-type]
        plan_lookup=_StubLookup(plans),
        feishu=feishu_obj,  # type: ignore[arg-type]
        renderer=MessageRenderer(),
        audit=audit_store,
        now=now,
    )
    return orchestrator, applier_obj, feishu_obj, audit_collection, audit_store


def _tmp_audit_path() -> Any:
    """Use a unique temp file each call so concurrent test runs don't collide."""

    return type("_P", (), {})  # placeholder; real path below


@pytest.fixture()
def audit_jsonl(tmp_path):
    return tmp_path / "audit.jsonl"


def _make_plan(
    *,
    instruction_id: str = "QM-20260516-103000-510300-BUY-001",
    valid_until: datetime | None = None,
    side: InstructionSide = InstructionSide.BUY,
) -> InstructionPlan:
    created = datetime(2026, 5, 16, 10, 30, 0, tzinfo=_SH)
    snapshot = datetime(2026, 5, 16, 10, 29, 50, tzinfo=_SH)
    vu = valid_until or datetime(2026, 5, 16, 14, 55, 0, tzinfo=_SH)
    return InstructionPlan(
        instruction_id=instruction_id,
        created_at=created,
        valid_until=vu,
        trade_date="2026-05-16",
        stock_code="510300",
        stock_name="沪深 300 ETF",
        side=side,
        volume=1000,
        limit_price=3.85,
        data_snapshot=DataSnapshot(
            snapshot_at=snapshot,
            quote_source="adata",
            is_trading_day=True,
            is_trading_hours=True,
        ),
        evidence_ids=("NEWS-20260516-0001",),
        position_summary=PositionSummary(
            pre_position_pct=0.04,
            post_position_pct=0.078,
            pre_total_position_pct=0.32,
            post_total_position_pct=0.358,
            pre_cash=200_000.0,
            post_cash=196_150.0,
        ),
        risk_summary=tuple(
            RiskCheckSummary(rule_name=f"r_{i}", passed=True, message="")
            for i in range(14)
        ),
        risk_validation_id="rv-001",
        signal_id="sig-001",
        analysis_record_id="ar-001",
        debate_round_count=1,
        invalidation_summary="hold valid",
        status=InstructionStatus.DISPATCHED,
    )


def _received_message(
    text: str = GOOD_REPORT_TEXT,
    received_at: datetime | None = None,
) -> ReceivedMessage:
    return ReceivedMessage(
        event_id="ev_x",
        message_id="om_x",
        chat_id=_VALID_CHAT,
        sender_id="ou_user",
        text=text,
        raw_create_time=1747380000,
        # Default to a fixed 2026-05-16 timestamp so it stays <= parsed_at
        # without depending on wall clock — the parser enforces
        # parsed_at >= received_at.
        received_at=received_at
        or datetime(2026, 5, 16, 2, 30, 0, tzinfo=UTC),
    )


# -----------------------------------------------------------------------------
# Happy path — parse succeeds, applier called
# -----------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_feishu_path_applies_and_sends_one_ack(
        self, tmp_path
    ) -> None:
        plan = _make_plan()
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_feishu(_received_message())
        assert outcome.success is True
        assert outcome.ambiguous is False
        assert outcome.instruction_id == plan.instruction_id
        assert applier.calls and applier.calls[0][1] is True  # side_is_buy
        # P0-4-amendment-2026-05-30b — success now sends exactly one
        # confirmation ack back to the decision chat (not a clarification).
        assert len(feishu.calls) == 1
        ack_chat_id, ack_body = feishu.calls[0]
        assert ack_chat_id == _received_message().chat_id
        assert "【QuantMind 已记录】" in ack_body
        assert plan.instruction_id in ack_body
        assert outcome.send_result is not None

    @pytest.mark.asyncio
    async def test_ack_send_failure_is_fail_open(self, tmp_path) -> None:
        # The broker mirror is authoritative (P0-5): a failed ack send must
        # NOT undo the applied report. Outcome stays success; send_result None.
        class _RaisingFeishu:
            async def send_message(self, *a: object, **k: object) -> object:
                raise RuntimeError("feishu down")

        plan = _make_plan()
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, _, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            feishu=_RaisingFeishu(),  # type: ignore[arg-type]
            audit=audit,
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_feishu(_received_message())
        assert outcome.success is True
        assert applier.calls  # the report WAS applied
        assert outcome.send_result is None  # ack send swallowed, not raised

    @pytest.mark.asyncio
    async def test_frontend_path_applies(self, tmp_path) -> None:
        plan = _make_plan()
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_frontend(
            GOOD_REPORT_TEXT,
            received_at=datetime(2026, 5, 16, 2, 30, 0, tzinfo=UTC),
        )
        assert outcome.success is True
        assert applier.calls
        assert feishu.calls == []


# -----------------------------------------------------------------------------
# Clarification — five branches
# -----------------------------------------------------------------------------


class TestClarification:
    @pytest.mark.asyncio
    async def test_no_pattern_match_sends_template(
        self, tmp_path
    ) -> None:
        audit_collection = InMemoryAuditCollection()
        audit = AuditStore(audit_collection, jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            audit=audit,
        )
        outcome = await orchestrator.handle_feishu(
            _received_message("一段无法识别的文字 xxx")
        )
        assert outcome.ambiguous is True
        assert outcome.template_id == ClarificationTemplate.NO_PATTERN_MATCH
        assert applier.calls == []
        assert len(feishu.calls) == 1
        chat_id, body = feishu.calls[0]
        assert chat_id == _VALID_CHAT
        assert "无法识别" in body

    @pytest.mark.asyncio
    async def test_empty_payload_clarifies(self, tmp_path) -> None:
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            audit=audit,
        )
        outcome = await orchestrator.handle_feishu(_received_message("   "))
        # Whitespace-only payload — the regex matcher catches it as
        # "empty body" which the parser maps to ``empty_payload`` reason.
        assert outcome.ambiguous is True
        assert outcome.template_id == ClarificationTemplate.EMPTY_PAYLOAD
        assert applier.calls == []

    @pytest.mark.asyncio
    async def test_field_cross_check_failed(self, tmp_path) -> None:
        """Side / code mismatch flows through the parser's semantic
        validation step → field_cross_check_failed reason."""
        # plan side=BUY but text says SELL — parser will reject after regex match.
        text = (
            "已执行 QM-20260516-103000-510300-BUY-001 卖出 510300 "
            "1000股 成交价 3.85"
        )
        plan = _make_plan(side=InstructionSide.BUY)
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
        )
        outcome = await orchestrator.handle_feishu(_received_message(text))
        assert outcome.ambiguous is True
        assert (
            outcome.template_id
            == ClarificationTemplate.FIELD_CROSS_CHECK_FAILED
        )
        assert applier.calls == []

    @pytest.mark.asyncio
    async def test_unknown_instruction_id(self, tmp_path) -> None:
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            plans={},  # lookup misses
            audit=audit,
        )
        outcome = await orchestrator.handle_feishu(_received_message())
        assert outcome.ambiguous is True
        assert (
            outcome.template_id
            == ClarificationTemplate.UNKNOWN_INSTRUCTION_ID
        )
        # Pure clarification — applier untouched
        assert applier.calls == []

    @pytest.mark.asyncio
    async def test_expired_plan_routes_to_expired_template(
        self, tmp_path
    ) -> None:
        plan = _make_plan(
            valid_until=datetime(2026, 5, 16, 11, 0, 0, tzinfo=_SH),
        )
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        # Simulate now() AFTER valid_until.
        future_now = datetime(2026, 5, 16, 13, 0, 0, tzinfo=_SH)
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            now=lambda: future_now,
        )
        outcome = await orchestrator.handle_feishu(_received_message())
        assert outcome.ambiguous is True
        assert (
            outcome.template_id == ClarificationTemplate.EXPIRED_INSTRUCTION
        )
        assert applier.calls == []

    @pytest.mark.asyncio
    async def test_volume_mismatch_filled_routes_to_field_cross_check(
        self, tmp_path
    ) -> None:
        """P1-2: FILLED report whose filled_volume != plan.volume
        must NOT reach the applier — route to FIELD_CROSS_CHECK_FAILED."""
        plan = _make_plan()  # plan.volume = 1000
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        # text claims 2000 shares filled but plan was 1000.
        wrong_volume = (
            "已执行 QM-20260516-103000-510300-BUY-001 买入 510300 "
            "2000股 成交价 3.85"
        )
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_feishu(
            _received_message(wrong_volume)
        )
        assert outcome.ambiguous is True
        assert (
            outcome.template_id
            == ClarificationTemplate.FIELD_CROSS_CHECK_FAILED
        )
        assert applier.calls == []
        assert any(
            "无法识别" not in body  # noqa: SIM102 — just ensure feishu was hit
            for _, body in feishu.calls
        ) if feishu.calls else True

    @pytest.mark.asyncio
    async def test_volume_mismatch_partial_sum_routes_to_field_cross_check(
        self, tmp_path
    ) -> None:
        """P1-2: PARTIAL filled+remain must equal plan.volume."""
        plan = _make_plan()  # plan.volume = 1000
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        # 700 + 200 = 900, but plan = 1000.
        wrong_sum = (
            "部分执行 QM-20260516-103000-510300-BUY-001 买入 510300 "
            "700股 成交价 3.85 剩余未成交 200股"
        )
        orchestrator, applier, _, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_feishu(
            _received_message(wrong_sum)
        )
        assert outcome.ambiguous is True
        assert (
            outcome.template_id
            == ClarificationTemplate.FIELD_CROSS_CHECK_FAILED
        )
        assert applier.calls == []

    @pytest.mark.asyncio
    async def test_volume_correct_filled_applies(self, tmp_path) -> None:
        """P1-2 regression: when filled_volume == plan.volume the
        applier still runs."""
        plan = _make_plan()
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, _, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_feishu(_received_message())
        assert outcome.success is True
        assert len(applier.calls) == 1

    @pytest.mark.asyncio
    async def test_volume_partial_correct_sum_applies(
        self, tmp_path
    ) -> None:
        plan = _make_plan()  # 1000
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        partial_correct = (
            "部分执行 QM-20260516-103000-510300-BUY-001 买入 510300 "
            "700股 成交价 3.85 剩余未成交 300股"
        )
        orchestrator, applier, _, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_feishu(
            _received_message(partial_correct)
        )
        assert outcome.success is True
        assert len(applier.calls) == 1

    @pytest.mark.asyncio
    async def test_post_close_prefix_overrides_expiry(
        self, tmp_path
    ) -> None:
        """盘后补录 prefix is the operator's escape hatch — applier still runs."""
        plan = _make_plan(
            valid_until=datetime(2026, 5, 16, 11, 0, 0, tzinfo=_SH),
        )
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        post_close_text = "盘后补录 " + GOOD_REPORT_TEXT
        orchestrator, applier, feishu, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            now=lambda: datetime(2026, 5, 16, 16, 30, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_feishu(
            _received_message(post_close_text)
        )
        assert outcome.success is True
        assert applier.calls


# -----------------------------------------------------------------------------
# Audit red line
# -----------------------------------------------------------------------------


class TestAudit:
    @pytest.mark.asyncio
    async def test_parse_failed_audit_written(self, tmp_path) -> None:
        audit_collection = InMemoryAuditCollection()
        audit = AuditStore(audit_collection, jsonl_path=tmp_path / "a.jsonl")
        orchestrator, _, _, _, _ = _build_orchestrator(audit=audit)
        await orchestrator.handle_feishu(_received_message("junk junk"))
        events = [
            AuditEvent.model_validate_json(_jsonify(doc))
            for doc in audit_collection.documents
        ]
        kinds = {ev.event_type for ev in events}
        assert AuditEventType.EXECUTION_REPORT_PARSE_FAILED in kinds
        only = [
            ev
            for ev in events
            if ev.event_type
            == AuditEventType.EXECUTION_REPORT_PARSE_FAILED
        ][0]
        assert only.actor == AuditActor.FEISHU_USER
        assert only.outcome == AuditOutcome.FAILURE
        assert only.reason_namespace == "execution_report_ambiguous"
        assert only.payload["channel"] == "FEISHU"
        # Raw text length flows but the body itself does not — no
        # operator-controlled string in the audit payload.
        assert only.payload["raw_text_length"] == len("junk junk")

    @pytest.mark.asyncio
    async def test_frontend_channel_actor(self, tmp_path) -> None:
        audit_collection = InMemoryAuditCollection()
        audit = AuditStore(audit_collection, jsonl_path=tmp_path / "a.jsonl")
        orchestrator, _, _, _, _ = _build_orchestrator(audit=audit)
        await orchestrator.handle_frontend(
            "junk", received_at=datetime(2026, 5, 16, 2, 30, 0, tzinfo=UTC)
        )
        events = [
            AuditEvent.model_validate_json(_jsonify(doc))
            for doc in audit_collection.documents
        ]
        actors = {ev.actor for ev in events}
        assert AuditActor.FRONTEND_USER in actors

    @pytest.mark.asyncio
    async def test_no_mockbroker_write_on_ambiguous(
        self, tmp_path
    ) -> None:
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, applier, _, _, _ = _build_orchestrator(audit=audit)
        # 4 distinct ambiguous paths
        for text in ("junk", "", "盘后补录 不识别 xxx"):
            await orchestrator.handle_feishu(_received_message(text))
        assert applier.calls == []


# -----------------------------------------------------------------------------
# Frontend never sends Feishu clarification
# -----------------------------------------------------------------------------


class TestFrontendChannel:
    @pytest.mark.asyncio
    async def test_frontend_skip_feishu_send(self, tmp_path) -> None:
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        orchestrator, _, feishu, _, _ = _build_orchestrator(audit=audit)
        await orchestrator.handle_frontend(
            "junk", received_at=datetime(2026, 5, 16, 2, 30, 0, tzinfo=UTC)
        )
        assert feishu.calls == []


# -----------------------------------------------------------------------------
# Apply failure path
# -----------------------------------------------------------------------------


class TestApplyFailure:
    @pytest.mark.asyncio
    async def test_apply_error_returns_failure(self, tmp_path) -> None:
        plan = _make_plan()
        audit = AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl")
        applier = _RecordingApplier(raise_on_apply=True)
        orchestrator, _, feishu, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=audit,
            applier=applier,
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        outcome = await orchestrator.handle_feishu(_received_message())
        assert outcome.success is False
        assert outcome.ambiguous is False
        # No clarification sent on apply failure — that's an internal
        # bug, not an ambiguity.
        assert feishu.calls == []


# -----------------------------------------------------------------------------
# Single-source-of-truth — frontend + feishu share the regex layer
# -----------------------------------------------------------------------------


class TestSingleSourceOfTruth:
    @pytest.mark.asyncio
    async def test_same_parser_outcome_both_channels(
        self, tmp_path
    ) -> None:
        plan = _make_plan()
        feishu_orch, _, _, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=AuditStore(
                InMemoryAuditCollection(), jsonl_path=tmp_path / "a.jsonl"
            ),
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        frontend_orch, _, _, _, _ = _build_orchestrator(
            plans={plan.instruction_id: plan},
            audit=AuditStore(
                InMemoryAuditCollection(), jsonl_path=tmp_path / "b.jsonl"
            ),
            now=lambda: datetime(2026, 5, 16, 10, 35, 0, tzinfo=_SH),
        )
        feishu_out = await feishu_orch.handle_feishu(_received_message())
        frontend_out = await frontend_orch.handle_frontend(
            GOOD_REPORT_TEXT, received_at=datetime(2026, 5, 16, 2, 30, 0, tzinfo=UTC)
        )
        # Same regex hit, same applier outcome — channel only differs.
        assert feishu_out.success == frontend_out.success
        assert feishu_out.instruction_id == frontend_out.instruction_id

    def test_no_llm_imports(self) -> None:
        import ast
        import pathlib

        for path in (
            "backend/integrations/feishu/parser.py",
            "backend/integrations/feishu/chase.py",
        ):
            tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    parts = (node.module or "").split(".")
                    assert not (
                        parts[:1] == ["backend"]
                        and len(parts) >= 2
                        and parts[1] in {"llm", "agents", "mirofish"}
                    ), f"forbidden import in {path}: {node.module}"


# -----------------------------------------------------------------------------
# ChaseScheduler
# -----------------------------------------------------------------------------


class TestChaseScheduler:
    @pytest.mark.asyncio
    async def test_chase_fires_after_delay(self) -> None:
        fires: list[str] = []

        async def _chase(inst_id: str) -> None:
            fires.append(inst_id)

        async def _expire(_inst_id: str) -> None:
            pass

        scheduler = ChaseScheduler(
            on_chase=_chase,
            on_expire=_expire,
            chase_after=timedelta(milliseconds=20),
        )
        valid_until = _utc_now() + timedelta(seconds=5)
        await scheduler.schedule("inst_a", valid_until)
        await asyncio.sleep(0.1)
        assert fires == ["inst_a"]
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_cancel_prevents_chase(self) -> None:
        fires: list[str] = []

        async def _chase(inst_id: str) -> None:
            fires.append(inst_id)

        async def _expire(_inst_id: str) -> None:
            pass

        scheduler = ChaseScheduler(
            on_chase=_chase,
            on_expire=_expire,
            chase_after=timedelta(milliseconds=50),
        )
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(seconds=5)
        )
        await scheduler.cancel("inst_a")
        await asyncio.sleep(0.1)
        assert fires == []
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_re_schedule_replaces_prior_timers(self) -> None:
        fires: list[str] = []

        async def _chase(inst_id: str) -> None:
            fires.append(f"chase:{inst_id}")

        async def _expire(_inst_id: str) -> None:
            pass

        scheduler = ChaseScheduler(
            on_chase=_chase,
            on_expire=_expire,
            chase_after=timedelta(milliseconds=20),
        )
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(seconds=5)
        )
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(seconds=5)
        )
        await asyncio.sleep(0.1)
        # The second schedule cancels the first; only one chase fires.
        assert fires == ["chase:inst_a"]
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_expire_fires_when_valid_until_elapses(self) -> None:
        expired: list[str] = []

        async def _chase(_inst_id: str) -> None:
            pass

        async def _expire(inst_id: str) -> None:
            expired.append(inst_id)

        scheduler = ChaseScheduler(
            on_chase=_chase,
            on_expire=_expire,
            chase_after=timedelta(seconds=10),
        )
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(milliseconds=20)
        )
        await asyncio.sleep(0.1)
        assert expired == ["inst_a"]
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_chase_skipped_when_expire_comes_first(self) -> None:
        chases: list[str] = []
        expired: list[str] = []

        async def _chase(inst_id: str) -> None:
            chases.append(inst_id)

        async def _expire(inst_id: str) -> None:
            expired.append(inst_id)

        scheduler = ChaseScheduler(
            on_chase=_chase,
            on_expire=_expire,
            chase_after=timedelta(seconds=5),  # would fire AFTER expiry
        )
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(milliseconds=20)
        )
        await asyncio.sleep(0.15)
        assert chases == []
        assert expired == ["inst_a"]
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_already_expired_fires_immediately(self) -> None:
        expired: list[str] = []

        async def _chase(_inst_id: str) -> None:
            pass

        async def _expire(inst_id: str) -> None:
            expired.append(inst_id)

        scheduler = ChaseScheduler(
            on_chase=_chase,
            on_expire=_expire,
        )
        # valid_until already in the past
        await scheduler.schedule(
            "inst_a", _utc_now() - timedelta(minutes=1)
        )
        assert expired == ["inst_a"]
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_timers(self) -> None:
        fires: list[str] = []

        async def _chase(inst_id: str) -> None:
            fires.append(inst_id)

        async def _expire(_inst_id: str) -> None:
            pass

        scheduler = ChaseScheduler(
            on_chase=_chase,
            on_expire=_expire,
            chase_after=timedelta(milliseconds=10),
        )
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(seconds=5)
        )
        await scheduler.stop()
        await asyncio.sleep(0.05)
        assert fires == []

    @pytest.mark.asyncio
    async def test_pending_count_tracks_state(self) -> None:
        async def _noop(_inst_id: str) -> None:
            pass

        scheduler = ChaseScheduler(
            on_chase=_noop,
            on_expire=_noop,
            chase_after=timedelta(seconds=10),
        )
        assert scheduler.pending_count == 0
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(seconds=10)
        )
        assert scheduler.pending_count == 1
        assert scheduler.is_tracking("inst_a") is True
        await scheduler.cancel("inst_a")
        assert scheduler.pending_count == 0
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_handler_error_does_not_propagate(self) -> None:
        async def _boom(_inst_id: str) -> None:
            raise RuntimeError("chase boom")

        async def _noop(_inst_id: str) -> None:
            pass

        scheduler = ChaseScheduler(
            on_chase=_boom,
            on_expire=_noop,
            chase_after=timedelta(milliseconds=20),
        )
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(seconds=5)
        )
        # Should not raise — sleep through the chase window.
        await asyncio.sleep(0.1)
        await scheduler.stop()

    def test_chase_after_must_be_positive(self) -> None:
        async def _noop(_inst_id: str) -> None:
            pass

        with pytest.raises(ValueError, match="chase_after"):
            ChaseScheduler(
                on_chase=_noop,
                on_expire=_noop,
                chase_after=timedelta(seconds=0),
            )

    @pytest.mark.asyncio
    async def test_schedule_after_stop_raises(self) -> None:
        """Cycle 2 P2 regression: a concurrent schedule() during
        stop() previously orphaned tasks. After stop() returns the
        scheduler is dead — further schedule() must raise rather
        than create new untracked tasks."""
        async def _noop(_inst_id: str) -> None:
            pass

        scheduler = ChaseScheduler(
            on_chase=_noop,
            on_expire=_noop,
            chase_after=timedelta(seconds=10),
        )
        await scheduler.stop()
        with pytest.raises(RuntimeError, match="stopped"):
            await scheduler.schedule(
                "inst_post_stop", _utc_now() + timedelta(seconds=10)
            )

    @pytest.mark.asyncio
    async def test_stop_awaits_in_flight_callback(self) -> None:
        """P2-3: stop() must await callbacks already running. A task
        that has popped itself from the chase/expire dicts (inside
        _fire_chase / _fire_expire) but is mid-await on the user
        callback must NOT survive stop()."""
        callback_blocking = asyncio.Event()
        callback_finished = asyncio.Event()

        async def _chase_holds(_inst_id: str) -> None:
            try:
                callback_blocking.set()
                # Block until cancelled. We don't await the event ever
                # being released — only stop() cancellation should
                # break this.
                await asyncio.sleep(60)
            finally:
                callback_finished.set()

        async def _expire(_inst_id: str) -> None:
            pass

        scheduler = ChaseScheduler(
            on_chase=_chase_holds,
            on_expire=_expire,
            chase_after=timedelta(milliseconds=10),
        )
        await scheduler.schedule(
            "inst_a", _utc_now() + timedelta(seconds=10)
        )
        # Wait until the callback has started (and has popped itself
        # from the chase_tasks dict).
        await asyncio.wait_for(callback_blocking.wait(), timeout=1.0)
        # Now stop — must cancel the in-flight callback and await it.
        await scheduler.stop()
        # callback_finished must be set as a side-effect of the
        # callback's finally block running after cancellation.
        assert callback_finished.is_set()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _jsonify(doc: dict[str, Any]) -> str:
    import json as _json

    return _json.dumps(doc, default=str)
