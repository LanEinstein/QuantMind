"""P1-5 §1.1 MVP page 3 — InstructionPlan 池 + 3-tab reason drawer.

Surfaces the executable plan timeline to operators with three separate
reason namespaces (P1-5 §1.5):

* ``builder_early_return`` — the five Builder gates (mode_switch /
  reconciliation_ticket_open / circuit_breaker_cooldown /
  data_quality_breach / watchlist_exclusion).
* ``risk_engine_check`` — the 14 ``RiskCheckSummary`` rows on the plan
  itself.
* ``broker_at_fill`` — the MockBroker terminal outcome, including the
  ``price_limit_violation_at_fill`` namespace that is distinct from
  the engine's ``limit_up_block`` / ``limit_down_block`` reasons.

The three tabs are returned as *independent* arrays — never merged into
one flat list — so the front-end drawer can render three tabs without
re-classifying the data path.

GET-only endpoint (P1-5 §2 redline 1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog
from fastapi import APIRouter, HTTPException, Request

from backend.models.instruction import InstructionPlan

log = structlog.get_logger(component="api_instruction_plans")

router = APIRouter(tags=["instruction-plans"])

# Namespace tag tuple — locked so the front-end drawer + redline-check
# can keep the three reason scopes independent. The locked invariant:
# ``price_limit_violation_at_fill`` only appears under
# ``broker_at_fill`` (MockBroker), never under ``risk_engine_check``
# whose limit-up/down scope uses ``limit_up_block`` /
# ``limit_down_block`` (D-001 14-check naming).
REASON_NAMESPACES: tuple[str, ...] = (
    "builder_early_return",
    "risk_engine_check",
    "broker_at_fill",
)


@runtime_checkable
class InstructionPlanReadRepository(Protocol):
    """Read-only contract for the in-memory or Mongo-backed plan store.

    Phase F will land the real persistence; this protocol lets the
    endpoint hydrate from any populated state on ``app.state``.
    """

    async def list_recent(
        self,
        *,
        limit: int,
        status: str | None,
        trade_date: str | None,
    ) -> list[InstructionPlan]: ...

    async def get_by_id(
        self, instruction_id: str
    ) -> InstructionPlan | None: ...

    async def builder_early_returns(
        self, instruction_id: str
    ) -> list[dict[str, Any]]: ...

    async def broker_at_fill(
        self, instruction_id: str
    ) -> dict[str, Any] | None: ...


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


def _plan_summary(plan: InstructionPlan) -> dict[str, Any]:
    side = plan.side.value if hasattr(plan.side, "value") else str(plan.side)
    status = (
        plan.status.value if hasattr(plan.status, "value") else str(plan.status)
    )
    return {
        "instruction_id": plan.instruction_id,
        "trade_date": plan.trade_date,
        "stock_code": plan.stock_code,
        "stock_name": plan.stock_name,
        "side": side,
        "status": status,
        "volume": plan.volume,
        "limit_price": plan.limit_price,
        "valid_until": plan.valid_until.isoformat(),
        "created_at": plan.created_at.isoformat(),
        "rejection_reason": plan.rejection_reason,
    }


def _risk_engine_rows(plan: InstructionPlan) -> list[dict[str, Any]]:
    """Project ``plan.risk_summary`` into the drawer's 14-row shape.

    Each row carries ``check_id`` (1..14), ``rule_name``, ``passed``
    (tri-state per P0-7 amendment), and the locked reason / threshold /
    actual fields. Rows stay in source order so the drawer renders
    check 1 first.
    """
    rows: list[dict[str, Any]] = []
    for idx, summary in enumerate(plan.risk_summary, start=1):
        rows.append(
            {
                "check_id": idx,
                "rule_name": summary.rule_name,
                "passed": summary.passed,
                "threshold": summary.threshold,
                "actual": summary.actual,
                "message": summary.message,
            }
        )
    return rows


def _get_repo(request: Request) -> InstructionPlanReadRepository | None:
    repo = getattr(request.app.state, "instruction_plan_repository", None)
    if repo is None:
        return None
    if not isinstance(repo, InstructionPlanReadRepository):
        log.warning(
            "instruction_plan_repository_protocol_mismatch",
            type=type(repo).__name__,
        )
        return None
    return repo


@router.get("/api/instruction-plans")
async def list_instruction_plans(
    request: Request,
    limit: int = 50,
    status: str | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Return a recent-first list of plan summaries.

    The endpoint stays read-only and degrades to an empty list when no
    repository is wired (Phase F wires the real persistence). Operators
    see a clear "no data yet" state instead of a misleading 500.
    """
    if limit < 1 or limit > 500:
        _err("limit must be 1..500", status_code=400)

    repo = _get_repo(request)
    if repo is None:
        return _ok(
            {
                "plans": [],
                "total": 0,
                "repository_status": "unavailable",
            }
        )

    try:
        plans = await repo.list_recent(
            limit=limit, status=status, trade_date=trade_date
        )
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning("instruction_plan_list_failed", error=str(exc))
        return _ok(
            {
                "plans": [],
                "total": 0,
                "repository_status": "unavailable",
            }
        )

    return _ok(
        {
            "plans": [_plan_summary(p) for p in plans],
            "total": len(plans),
            "repository_status": "ok",
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
    )


@router.get("/api/instruction-plans/{instruction_id}")
async def get_instruction_plan_detail(
    instruction_id: str,
    request: Request,
) -> dict[str, Any]:
    """Return the plan + three independent reason-tab arrays.

    Locked invariants:

    * Three keys appear under ``reason_tabs``: ``builder_early_return``,
      ``risk_engine_check``, ``broker_at_fill`` — operator UI's drawer
      uses these as tab keys.
    * ``broker_at_fill.reason`` may carry
      ``price_limit_violation_at_fill`` but the engine tab uses
      ``limit_up_block`` / ``limit_down_block``; the two namespaces
      never collide.
    """
    repo = _get_repo(request)
    if repo is None:
        _err("instruction_plan_repository not wired", status_code=503)
    assert repo is not None  # for type-checker

    try:
        plan = await repo.get_by_id(instruction_id)
    except Exception as exc:  # pragma: no cover — defensive only
        log.warning(
            "instruction_plan_detail_failed",
            error=str(exc),
            instruction_id=instruction_id,
        )
        _err("instruction_plan lookup failed", status_code=503)
    if plan is None:
        _err(f"instruction_plan {instruction_id!r} not found", status_code=404)
    assert plan is not None

    try:
        builder = await repo.builder_early_returns(instruction_id)
    except Exception:
        builder = []
    try:
        broker = await repo.broker_at_fill(instruction_id)
    except Exception:
        broker = None

    risk_rows = _risk_engine_rows(plan)

    return _ok(
        {
            "plan": _plan_summary(plan),
            "evidence_ids": list(plan.evidence_ids),
            "debate_round_count": plan.debate_round_count,
            "invalidation_summary": plan.invalidation_summary,
            "reason_tabs": {
                "builder_early_return": builder,
                "risk_engine_check": risk_rows,
                "broker_at_fill": broker,
            },
        }
    )
