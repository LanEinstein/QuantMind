"""agents_team graph nodes (Phase M-002).

Two node families:

* **Agent stubs** (``fundamental_analyst_node`` / ``technical_analyst_node`` /
  ``risk_officer_node`` / ``debate_node`` / ``fund_manager_node``) — async,
  deterministic placeholders that write ONLY free-text report / reasoning fields
  and the ``direction`` proposal. M-003 replaces their bodies with real LLM
  calls; the graph topology + the no-LLM-edge invariant stay identical.

* **Deterministic tool nodes** (``risk_gate_node`` / ``builder_node``) — sync,
  pure. ``risk_gate_node`` builds the :class:`Order` from the *numeric* state
  fields (never from any agent's text) and runs the pure 14-check RiskEngine.
  ``builder_node`` applies the mandatory-agent / debate / direction / risk gates
  and emits a terminal decision. It deliberately does NOT construct an
  InstructionPlan — that stays the sole job of ``instruction_plan_builder``
  (R0 §4 red line B), wired in M-003/M-004.

The key invariant proven here: the tool nodes' numeric/decision output depends
only on the deterministic numeric inputs + the fund_manager's direction
proposal — feeding different agent *text* never changes the order or the risk
result.
"""

from __future__ import annotations

import datetime as dt

import structlog

from backend.agents_team.state import (
    DECISION_BUILD_OK,
    DECISION_HOLD,
    DECISION_REJECTED,
    MANDATORY_AGENTS,
    TeamContext,
    TeamState,
)
from backend.broker.models import (  # noqa: TID251
    Order,
    OrderDirection,
    OrderType,
)

log = structlog.get_logger(component="agents_team.nodes")

_DIR_TO_ORDER = {"BUY": OrderDirection.BUY, "SELL": OrderDirection.SELL}


# ---------------------------------------------------------------------------
# Agent stubs (M-003 replaces bodies with real LLM calls; topology unchanged)
# ---------------------------------------------------------------------------


async def fundamental_analyst_node(state: TeamState, ctx: TeamContext) -> dict:
    """STUB: deterministic fundamental report. M-003 → LLM fundamental_analyst."""
    code = state.get("candidate_code", "")
    return {"fundamental_report": f"[stub fundamental] {code}"}


async def technical_analyst_node(state: TeamState, ctx: TeamContext) -> dict:
    """STUB: deterministic technical report. M-003 → LLM technical_analyst."""
    code = state.get("candidate_code", "")
    return {"technical_report": f"[stub technical] {code}"}


async def risk_officer_node(state: TeamState, ctx: TeamContext) -> dict:
    """STUB: deterministic risk-officer note. M-003 → LLM risk_officer."""
    code = state.get("candidate_code", "")
    return {"risk_officer_report": f"[stub risk_officer] {code}"}


async def debate_node(state: TeamState, ctx: TeamContext) -> dict:
    """STUB: single-round debate. Sets debate_round_count ≥ 1 (P0-10 §2.3).

    M-003 replaces this with the real single-round 4-agent debate.
    """
    history = (
        f"[stub debate] f={bool(state.get('fundamental_report'))} "
        f"t={bool(state.get('technical_report'))} "
        f"r={bool(state.get('risk_officer_report'))}"
    )
    return {"debate_history": history, "debate_round_count": 1}


async def fund_manager_node(state: TeamState, ctx: TeamContext) -> dict:
    """STUB: fund_manager is the sole BUY/SELL/HOLD proposer (P0-10 §2.3).

    The skeleton echoes ``ctx.stub_direction`` as the proposal + writes free-text
    reasoning. M-003 replaces this with the real LLM fund_manager that derives
    the direction proposal; the numeric order fields are never set here.
    """
    return {
        "direction": ctx.stub_direction,
        "fund_manager_reasoning": (
            f"[stub fund_manager] proposes {ctx.stub_direction}"
        ),
    }


# ---------------------------------------------------------------------------
# Deterministic tool nodes (sync, pure — no LLM edge writes them)
# ---------------------------------------------------------------------------


def _derive_order(state: TeamState, ctx: TeamContext, direction: str) -> Order:
    """Build the risk-check Order from numeric state fields only (non-LLM).

    Deterministic: depends solely on the candidate code, the position-sizer's
    ``proposed_volume`` / ``proposed_limit_price``, and the fund_manager's
    direction proposal — never on any agent's free text.
    """
    now = ctx.now or dt.datetime.now(tz=dt.UTC)
    return Order(
        order_id=f"RISK-PRECHECK-{state.get('candidate_code', '')}",
        code=state.get("candidate_code", ""),
        price=float(state.get("proposed_limit_price", 0.0)),
        volume=int(state.get("proposed_volume", 0)),
        direction=_DIR_TO_ORDER[direction],
        order_type=OrderType.LIMIT,
        created_at=now,
        updated_at=now,
    )


def risk_gate_node(state: TeamState, ctx: TeamContext) -> dict:
    """Pure 14-check RiskEngine gate over a deterministically-built order.

    HOLD never routes (P0-3) so no order is built and the engine is not run.
    For BUY/SELL the order is derived from numeric state only; the result
    depends on no agent text (the determinism invariant).
    """
    direction = state.get("direction", "")
    if direction not in _DIR_TO_ORDER:
        # HOLD or unset → no routing; builder turns this into a HOLD decision.
        return {
            "risk_passed": False,
            "risk_rule": "not_routed",
            "risk_message": f"direction={direction!r} is not routable",
        }
    # Fail closed if the full 14-check context is incomplete: with BOTH
    # daily_state and stock_meta None, RiskEngine.validate_order silently
    # degrades to the legacy 7-check mode and skips checks 8-14
    # (single-instruction amount, universe whitelist, limit-up/down, halts).
    # This decision path is contracted to the full 14-check, so a missing
    # context is a fail-closed reject, never a 7-check pass (codex M-002 P1).
    if ctx.daily_state is None or ctx.stock_meta is None:
        log.warning(
            "risk_context_incomplete",
            code=state.get("candidate_code", ""),
            has_daily_state=ctx.daily_state is not None,
            has_stock_meta=ctx.stock_meta is not None,
        )
        return {
            "risk_passed": False,
            "risk_rule": "risk_context_incomplete",
            "risk_message": (
                "daily_state + stock_meta required for the full 14-check gate"
            ),
        }
    order = _derive_order(state, ctx, direction)
    result = ctx.risk_engine.validate_order(
        order,
        ctx.account,
        ctx.positions,
        prev_close=ctx.prev_close,
        now=ctx.now,
        daily_state=ctx.daily_state,
        stock_meta=ctx.stock_meta,
        concentration_exception=ctx.concentration_exception,
    )
    log.info(
        "risk_gate",
        code=order.code,
        direction=direction,
        passed=result.passed,
        rule=result.rule_name,
    )
    return {
        "risk_passed": result.passed,
        "risk_rule": result.rule_name,
        "risk_message": result.message,
    }


def _missing_mandatory(state: TeamState) -> list[str]:
    """Mandatory agents that produced no output (→ degrade HOLD)."""
    produced = {
        "fundamental_analyst": bool(state.get("fundamental_report")),
        "technical_analyst": bool(state.get("technical_report")),
        "risk_officer": bool(state.get("risk_officer_report")),
        "fund_manager": bool(state.get("fund_manager_reasoning")),
    }
    return [a for a in MANDATORY_AGENTS if not produced[a]]


def builder_node(state: TeamState, ctx: TeamContext) -> dict:
    """Apply the gates and emit a terminal decision (skeleton, no plan built).

    Gate order (fail toward HOLD):
      1. Any mandatory agent missing → HOLD (P0-10 §2.3 degrade).
      2. ``debate_round_count < 1`` → HOLD (P0-10 §2.3).
      3. fund_manager proposed HOLD (or unroutable) → HOLD (P0-3).
      4. RiskEngine rejected → REJECTED (rule name carried).
      5. Otherwise → BUILD_OK.

    M-003/M-004 replace step 5's BUILD_OK signal with a delegated
    ``instruction_plan_builder.assemble_plan`` call — agents_team never
    constructs the InstructionPlan itself (single construction point).
    """
    missing = _missing_mandatory(state)
    if missing:
        return _decide(DECISION_HOLD, f"mandatory_agent_missing:{','.join(missing)}")
    if state.get("debate_round_count", 0) < 1:
        return _decide(DECISION_HOLD, "debate_round_count_lt_1")
    direction = state.get("direction", "")
    if direction not in _DIR_TO_ORDER:
        return _decide(DECISION_HOLD, f"fund_manager_hold:{direction}")
    if not state.get("risk_passed", False):
        return _decide(
            DECISION_REJECTED, f"risk:{state.get('risk_rule', 'unknown')}"
        )
    return _decide(DECISION_BUILD_OK, "all_gates_passed")


def _decide(decision: str, reason: str) -> dict:
    log.info("builder_decision", decision=decision, reason=reason)
    return {"decision": decision, "decision_reason": reason}


__all__ = [
    "builder_node",
    "debate_node",
    "fundamental_analyst_node",
    "fund_manager_node",
    "risk_gate_node",
    "risk_officer_node",
    "technical_analyst_node",
]
