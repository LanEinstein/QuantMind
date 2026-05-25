"""Shared fixtures for Phase U orchestration tests (U-B2+).

Builds VALIDATED InstructionPlans + in-memory test doubles for the
outbound dispatch path (Feishu sender / decision-ledger / audit) so the
dispatcher + route coordinator can be exercised without a live broker,
Mongo, or Feishu credential.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.integrations.feishu.client import SendMessageResult
from backend.models.instruction import (
    DataSnapshot,
    InstructionPlan,
    InstructionSide,
    InstructionStatus,
    PositionSummary,
    RiskCheckSummary,
)
from backend.services.ledger import DecisionLedgerService, InMemoryLedgerRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")

_RISK_RULE_NAMES = (
    "code_validity", "price_reasonability", "volume_validity",
    "fund_sufficiency", "position_limit", "total_position_limit",
    "trading_time", "total_position_pct", "single_instruction_amount",
    "daily_new_instruction_count", "universe_whitelist",
    "limit_up_down_block", "daily_loss_halt", "consecutive_loss_halt",
)


def _risk_summary_14() -> tuple[RiskCheckSummary, ...]:
    return tuple(
        RiskCheckSummary(rule_name=n, passed=True, message="")
        for n in _RISK_RULE_NAMES
    )


def _snapshot(snap_at: dt.datetime) -> DataSnapshot:
    return DataSnapshot(
        snapshot_at=snap_at,
        quote_source="adata",
        quote_latency_ms=100,
        prev_close=100.0,
        is_trading_day=True,
        is_trading_hours=True,
    )


def make_plan(
    *,
    side: InstructionSide = InstructionSide.BUY,
    status: InstructionStatus = InstructionStatus.VALIDATED,
    signal_id: str = "sig-1",
    seq: str = "001",
    created: dt.datetime | None = None,
) -> InstructionPlan:
    """Build an InstructionPlan for dispatch tests.

    ``signal_id`` carries the ``LINE2-MON-`` prefix for Line-2 monitoring
    plans; ``seq`` keeps each instruction_id unique within one test.
    """
    created = created or dt.datetime(2026, 5, 15, 10, 0, 1, tzinfo=SHANGHAI)
    snap = created - dt.timedelta(seconds=2)
    code_side = side.value
    is_hold = side is InstructionSide.HOLD
    return InstructionPlan(
        instruction_id=f"QM-20260515-100001-600519-{code_side}-{seq}",
        created_at=created,
        valid_until=created + dt.timedelta(minutes=5),
        trade_date="2026-05-15",
        stock_code="600519",
        stock_name="贵州茅台",
        side=side,
        volume=None if is_hold else 100,
        limit_price=None if is_hold else 100.0,
        data_snapshot=_snapshot(snap),
        evidence_ids=("MARKET-600519-2026-05-15T10:00:00",),
        position_summary=(
            None
            if is_hold
            else PositionSummary(
                pre_position_pct=0.05, post_position_pct=0.06,
                pre_total_position_pct=0.30, post_total_position_pct=0.31,
                pre_cash=80_000.0, post_cash=69_950.0,
            )
        ),
        risk_summary=_risk_summary_14(),
        risk_validation_id="RV-1",
        signal_id=signal_id,
        analysis_record_id="run-1",
        debate_round_count=2,
        invalidation_summary="跌破 95",
        status=status,
    )


@dataclass
class FakeFeishuSender:
    """Records send_message calls and replays a queued/locked result."""

    ok: bool = True
    message_id: str = "om_test_1"
    calls: list[dict[str, object]] = field(default_factory=list)
    fail_first_n: int = 0

    async def send_message(
        self,
        chat_id: str,
        content: str,
        *,
        msg_type: str = "text",
        uuid: str | None = None,
    ) -> SendMessageResult:
        self.calls.append(
            {"chat_id": chat_id, "content": content, "uuid": uuid}
        )
        ok = self.ok and len(self.calls) > self.fail_first_n
        return SendMessageResult(
            ok=ok,
            code=0 if ok else 230002,
            msg="" if ok else "rejected",
            message_id=self.message_id if ok else None,
            log_id="log-1",
        )


@pytest.fixture()
def ledger() -> DecisionLedgerService:
    return DecisionLedgerService(InMemoryLedgerRepository())


@pytest.fixture()
def audit_store(tmp_path: Path) -> AuditStore:
    return AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl")
