"""Real LLM-backed agent nodes for the agents_team graph (Phase M-003).

Replaces the M-002 deterministic stubs with the four mandatory agents
(P0-10 §2.3) driven by real LLM calls through the injected router, plus a
single deterministic debate fan-in round (so the MVP stays at four LLM
calls per candidate ≈ ¥0.4/day; richer multi-round bull/bear debate +
≥2 trader personas are deferred to Phase T).

Invariant carried over from M-002: these nodes write ONLY free-text
report / reasoning fields + the ``direction`` proposal. ``fund_manager``
is the sole BUY/SELL/HOLD proposer (P0-10 §2.3) and also emits the
LLM-only :class:`FundManagerOutput` bridge via :func:`to_fund_manager_output`.
The deterministic tool nodes (``risk_gate`` / ``builder``, in
``nodes.py``) read only the numeric state, so no LLM edge writes the
decision path (R0 §4).

Fail-closed by construction: a missing ``ctx.llm_router`` or any call
failure / empty response yields an empty report, which the builder's
mandatory-agent gate turns into a HOLD — never a silent pass. The
fund_manager's JSON envelope is parsed deterministically; a parse failure
forces ``direction='HOLD'`` + ``fund_manager_parse_ok=False`` (P0-3 §2
redline 6).

agents_team NEVER constructs an :class:`InstructionPlan`; numeric order
fields are derived by ``instruction_plan_builder`` (single construction
point, M-004 / R0 §4 red line B). This module only produces the
LLM-writable bridge.
"""

from __future__ import annotations

import structlog

from backend.agents.base import extract_json_from_response  # noqa: TID251
from backend.agents.prompts import (  # noqa: TID251
    FUND_MANAGER_PROMPT,
    FUNDAMENTAL_ANALYST_PROMPT,
    RISK_OFFICER_PROMPT,
    TECHNICAL_ANALYST_PROMPT,
)
from backend.agents_team.state import LLMCompleter, TeamContext, TeamState
from backend.models.instruction import InstructionSide  # noqa: TID251
from backend.services.fund_manager_output import FundManagerOutput  # noqa: TID251

log = structlog.get_logger(component="agents_team.agents")

# Bounded to the FundManagerOutput.proposal_text Field(max_length=4096).
_MAX_PROPOSAL_TEXT = 4096

# Legacy Chinese action enum (FUND_MANAGER_PROMPT contract) → routable
# direction token consumed by ``risk_gate_node`` (_DIR_TO_ORDER in nodes.py).
_ACTION_TO_DIRECTION: dict[str, str] = {
    "买入": "BUY",
    "卖出": "SELL",
    "持有": "HOLD",
}

# Direction token → InstructionSide for the FundManagerOutput bridge.
_DIRECTION_TO_SIDE: dict[str, InstructionSide] = {
    "BUY": InstructionSide.BUY,
    "SELL": InstructionSide.SELL,
    "HOLD": InstructionSide.HOLD,
}

_PARSE_FAILURE_TEXT = "LLM response could not be parsed (forced HOLD)"


# ---------------------------------------------------------------------------
# LLM call helper — fail-closed (empty report on any failure)
# ---------------------------------------------------------------------------


async def _complete(
    ctx: TeamContext,
    agent_name: str,
    system_prompt: str,
    user_content: str,
) -> str:
    """Call the injected router for ``agent_name``; return content or ``""``.

    Fail-closed: a missing router, a provider exception, a malformed
    response shape, or an empty completion all yield ``""`` so the
    builder's mandatory-agent gate degrades to HOLD. Never raises.
    """
    router: LLMCompleter | None = ctx.llm_router
    if router is None:
        log.warning("agent_no_router", agent_name=agent_name)
        return ""
    try:
        response = await router.complete(
            agent_name,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as exc:  # noqa: BLE001 — fail-closed, log for operator
        log.warning("agent_call_failed", agent_name=agent_name, error=str(exc))
        return ""
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        log.warning("agent_bad_response_shape", agent_name=agent_name)
        return ""
    if not isinstance(content, str) or not content.strip():
        # Whitespace-only completions fail closed exactly like empty ones:
        # otherwise a "   " report would read truthy and let the
        # mandatory-agent gate reach BUILD_OK with no usable report
        # (codex M-003 P2).
        return ""
    return content


# ---------------------------------------------------------------------------
# Prompt context builders (deterministic; numeric fields are informational —
# the LLM can never change them, risk_gate reads them from state not text)
# ---------------------------------------------------------------------------


def _analyst_context(state: TeamState) -> str:
    """Minimal candidate context for the three analyst agents (MVP)."""
    return (
        f"目标标的: {state.get('candidate_code', '')} "
        f"{state.get('candidate_name', '')}\n"
        f"系统拟定交易参数(由确定性模块派生,非你决定): "
        f"数量={state.get('proposed_volume', 0)} "
        f"限价={state.get('proposed_limit_price', 0.0)}\n"
        "请基于你的专业领域给出分析报告。"
    )


def _fund_manager_context(state: TeamState) -> str:
    """Synthesise the analyst reports + debate record for the fund_manager."""
    return (
        f"目标标的: {state.get('candidate_code', '')} "
        f"{state.get('candidate_name', '')}\n\n"
        f"=== 基本面分析 ===\n{state.get('fundamental_report', '')}\n\n"
        f"=== 技术分析 ===\n{state.get('technical_report', '')}\n\n"
        f"=== 风控评估 ===\n{state.get('risk_officer_report', '')}\n\n"
        f"=== 单轮辩论记录 ===\n{state.get('debate_history', '')}"
    )


# ---------------------------------------------------------------------------
# Agent nodes (async; write only free text + the direction proposal)
# ---------------------------------------------------------------------------


async def fundamental_analyst_node(state: TeamState, ctx: TeamContext) -> dict:
    """Mandatory agent #1 — fundamental analysis report (P0-10 §2.3)."""
    report = await _complete(
        ctx, "fundamental_analyst", FUNDAMENTAL_ANALYST_PROMPT, _analyst_context(state)
    )
    return {"fundamental_report": report}


async def technical_analyst_node(state: TeamState, ctx: TeamContext) -> dict:
    """Mandatory agent #2 — technical analysis report (P0-10 §2.3)."""
    report = await _complete(
        ctx, "technical_analyst", TECHNICAL_ANALYST_PROMPT, _analyst_context(state)
    )
    return {"technical_report": report}


async def risk_officer_node(state: TeamState, ctx: TeamContext) -> dict:
    """Mandatory agent #3 — advisory risk-officer report (P0-10 §2.3).

    Advisory only: this LLM report never enters the RiskEngine 14-check
    (which runs as a pure tool node downstream); it is one input to the
    fund_manager's deliberation.
    """
    report = await _complete(
        ctx, "risk_officer", RISK_OFFICER_PROMPT, _analyst_context(state)
    )
    return {"risk_officer_report": report}


async def debate_node(state: TeamState, ctx: TeamContext) -> dict:
    """Single-round debate fan-in (deterministic, no LLM call).

    The three analyst reports converge into one debate record presented to
    the fund_manager; ``debate_round_count`` is set to 1 so the mandatory
    ``debate_round_count >= 1`` gate (P0-10 §2.3) is satisfied. Keeping the
    debate deterministic holds the MVP at four LLM calls per candidate;
    multi-round bull/bear debate is Phase T.
    """
    present = {
        "fundamental_analyst": bool(state.get("fundamental_report")),
        "technical_analyst": bool(state.get("technical_report")),
        "risk_officer": bool(state.get("risk_officer_report")),
    }
    history = (
        "[单轮辩论] 参与方报告就绪状态: "
        + ", ".join(f"{name}={present[name]}" for name in present)
    )
    return {"debate_history": history, "debate_round_count": 1}


async def fund_manager_node(state: TeamState, ctx: TeamContext) -> dict:
    """Mandatory agent #4 — sole BUY/SELL/HOLD proposer (P0-10 §2.3).

    Parses the JSON envelope (FUND_MANAGER_PROMPT contract) into a routable
    ``direction`` token + ``fund_manager_parse_ok``. A parse failure forces
    ``direction='HOLD'`` + ``parse_ok=False`` (P0-3 §2 redline 6). Writes
    only free text + the direction proposal — never a numeric order field.
    """
    raw = await _complete(
        ctx, "fund_manager", FUND_MANAGER_PROMPT, _fund_manager_context(state)
    )
    direction, reasoning, parse_ok = _parse_fund_manager(raw)
    return {
        "direction": direction,
        "fund_manager_reasoning": reasoning,
        "fund_manager_parse_ok": parse_ok,
    }


def _parse_fund_manager(raw: str) -> tuple[str, str, bool]:
    """Parse the fund_manager JSON envelope → (direction, reasoning, parse_ok).

    Returns ``("HOLD", <fallback text>, False)`` whenever the JSON is
    missing, the ``action`` is not one of 买入/卖出/持有, or the response
    is empty — the conservative fail-closed path (P0-3 §2 redline 6).
    """
    data = extract_json_from_response(raw)
    if data is not None:
        direction = _ACTION_TO_DIRECTION.get(str(data.get("action", "")))
        if direction is not None:
            reasoning = str(data.get("reasoning") or "").strip()
            if not reasoning:
                reasoning = raw.strip() or "(no reasoning supplied)"
            return direction, reasoning[:_MAX_PROPOSAL_TEXT], True
    fallback = raw.strip()[:_MAX_PROPOSAL_TEXT] if raw.strip() else _PARSE_FAILURE_TEXT
    return "HOLD", fallback, False


# ---------------------------------------------------------------------------
# LLM-only bridge — the single handoff to instruction_plan_builder
# ---------------------------------------------------------------------------


def to_fund_manager_output(state: TeamState) -> FundManagerOutput:
    """Build the LLM-only :class:`FundManagerOutput` bridge from team state.

    This is the ONLY value agents_team hands to the deterministic
    ``instruction_plan_builder``: the LLM-writable ``side`` + ``proposal_text``
    + ``parse_ok``. The builder derives ``volume`` / ``limit_price`` /
    ``status`` / ``risk_summary`` itself from non-LLM inputs (R0 §4 red
    line B). An unroutable / unset direction maps to HOLD so a malformed
    state can never smuggle a BUY/SELL downstream.
    """
    direction = state.get("direction", "")
    side = _DIRECTION_TO_SIDE.get(direction, InstructionSide.HOLD)
    reasoning = (state.get("fund_manager_reasoning") or "").strip()
    if not reasoning:
        reasoning = "(no reasoning supplied)"
    parse_ok = bool(state.get("fund_manager_parse_ok", False))
    return FundManagerOutput(
        side=side,
        proposal_text=reasoning[:_MAX_PROPOSAL_TEXT],
        parse_ok=parse_ok,
    )


__all__ = [
    "debate_node",
    "fund_manager_node",
    "fundamental_analyst_node",
    "risk_officer_node",
    "technical_analyst_node",
    "to_fund_manager_output",
]
