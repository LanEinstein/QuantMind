"""Run collector: accumulates per-agent steps from the LangGraph pipeline.

Used by run_analysis() to build an AnalysisRecord alongside the terminal
TradingSignal, and by the live SSE stream API (Session A2) to push events
to subscribers as agents complete.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog

from backend.agents.models import TradingSignal
from backend.agents.records import (
    AgentStepRecord,
    AnalysisRecord,
    DebateRoundRecord,
    FundManagerRecord,
    RiskAssessmentRecord,
)

log = structlog.get_logger(component="run_collector")

EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]

ANALYST_AGENTS = (
    "news_crawler",
    "sentiment_analyst",
    "fundamental_analyst",
    "technical_analyst",
)


def extract_content(node_name: str, delta: dict[str, Any]) -> str:
    """Extract the human-visible content a node produced into state.

    Uses exact state keys from backend/agents/*.py return values.
    Strips Bull:/Bear: prefix from debate current_response per plan §5.1.
    """
    if node_name == "news_crawler":
        return delta.get("news_report", "") or ""
    if node_name == "sentiment_analyst":
        return delta.get("sentiment_report", "") or ""
    if node_name == "fundamental_analyst":
        return delta.get("fundamental_report", "") or ""
    if node_name == "technical_analyst":
        return delta.get("technical_report", "") or ""
    if node_name == "intelligence_officer":
        return delta.get("intelligence_report", "") or ""
    if node_name in ("bull_researcher", "bear_researcher"):
        debate = delta.get("debate_state") or {}
        current = debate.get("current_response", "") or ""
        for prefix in ("Bull: ", "Bear: "):
            if current.startswith(prefix):
                return current[len(prefix) :]
        return current
    if node_name == "risk_officer":
        return delta.get("risk_assessment", "") or ""
    if node_name == "fund_manager":
        signal = delta.get("trading_signal") or {}
        return signal.get("reasoning", "") or ""
    return ""


def classify_status(
    agent: str, content: str
) -> tuple[str, str | None]:
    """Detect call_agent() graceful-error string and mark step failed."""
    err_prefix = f"[{agent} error:"
    if content.startswith(err_prefix):
        return ("failed", content)
    return ("completed", None)


class RunCollector:
    """Accumulates per-agent step records for one analysis run.

    Thread-safety: all mutations happen on the asyncio event loop; appends
    to list under GIL are atomic. Do not share across loops.
    """

    def __init__(
        self,
        *,
        run_id: str,
        stock_code: str,
        stock_name: str,
        trade_date: str,
        max_rounds: int,
        emitter: EventEmitter | None = None,
    ) -> None:
        self._run_id = run_id
        self._stock_code = stock_code
        self._stock_name = stock_name
        self._trade_date = trade_date
        self._max_rounds = max_rounds
        self._emitter = emitter
        self._steps: list[AgentStepRecord] = []
        self._created_at = datetime.now(tz=UTC)

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def steps(self) -> list[AgentStepRecord]:
        return list(self._steps)

    async def on_agent_started(self, agent: str, round_: int) -> datetime:
        """Emit agent_started event and return started_at timestamp."""
        started_at = datetime.now(tz=UTC)
        await self._emit(
            {
                "event_type": "agent_started",
                "agent": agent,
                "round": round_,
                "timestamp": started_at.isoformat(),
                "run_id": self._run_id,
            }
        )
        return started_at

    async def on_agent_completed(
        self,
        agent: str,
        round_: int,
        started_at: datetime,
        delta: dict[str, Any],
    ) -> AgentStepRecord:
        """Extract content from node delta, record step, emit event."""
        content = extract_content(agent, delta)
        status, error = classify_status(agent, content)
        completed_at = datetime.now(tz=UTC)
        step = AgentStepRecord(
            agent=agent,
            round=round_,
            content=content,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            error=error,
        )
        self._steps.append(step)
        await self._emit(
            {
                "event_type": "agent_completed",
                "agent": agent,
                "round": round_,
                "content": content,
                "model_label": step.model_label,
                "model_id": step.model_id,
                "status": status,
                "error": error,
                "timestamp": completed_at.isoformat(),
                "run_id": self._run_id,
            }
        )
        return step

    async def on_agent_failed(
        self,
        agent: str,
        round_: int,
        started_at: datetime,
        error: str,
    ) -> AgentStepRecord:
        """Record a hard failure (raised exception) as a failed step.

        Distinct from `on_agent_completed` with a graceful-error string:
        here the node fn never returned, so there is no state delta and
        content stays empty. The emitted event still uses
        ``agent_completed`` so the frontend's discriminated union does
        not need a third case, but ``status="failed"`` carries the signal.
        """
        completed_at = datetime.now(tz=UTC)
        step = AgentStepRecord(
            agent=agent,  # type: ignore[arg-type]
            round=round_,
            content="",
            started_at=started_at,
            completed_at=completed_at,
            status="failed",
            error=error,
        )
        self._steps.append(step)
        await self._emit(
            {
                "event_type": "agent_completed",
                "agent": agent,
                "round": round_,
                "content": "",
                "model_label": step.model_label,
                "model_id": step.model_id,
                "status": "failed",
                "error": error,
                "timestamp": completed_at.isoformat(),
                "run_id": self._run_id,
            }
        )
        return step

    def has_failed_steps(self) -> bool:
        """True when any recorded step finalized with status='failed'."""
        return any(s.status == "failed" for s in self._steps)

    def first_failure_summary(self) -> str | None:
        """Compact human-readable summary of the first failed step, or None."""
        for s in self._steps:
            if s.status == "failed":
                err = s.error or "agent failed"
                return f"{s.agent}: {err}"
        return None

    async def on_pipeline_completed(
        self, *, record_id: str | None, signal_id: str | None
    ) -> None:
        await self._emit(
            {
                "event_type": "pipeline_completed",
                "run_id": self._run_id,
                "record_id": record_id,
                "signal_id": signal_id,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )

    async def on_error(self, message: str) -> None:
        await self._emit(
            {
                "event_type": "error",
                "message": message,
                "run_id": self._run_id,
                "timestamp": datetime.now(tz=UTC).isoformat(),
            }
        )

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._emitter is None:
            return
        try:
            await self._emitter(event)
        except Exception as exc:
            log.warning(
                "collector_emit_failed",
                run_id=self._run_id,
                error=str(exc),
                event_type=event.get("event_type"),
            )

    def finalize(
        self,
        *,
        status: str,
        signal: TradingSignal | None,
        error: str | None = None,
    ) -> AnalysisRecord:
        """Build the terminal AnalysisRecord from accumulated steps."""
        analysts = [s for s in self._steps if s.agent in ANALYST_AGENTS]

        intelligence = next(
            (s for s in self._steps if s.agent == "intelligence_officer"),
            None,
        )

        debates = self._build_debate_rounds()

        risk_step = next(
            (s for s in self._steps if s.agent == "risk_officer"), None
        )
        risk = (
            RiskAssessmentRecord(content=risk_step.content, step=risk_step)
            if risk_step is not None
            else None
        )

        fund_step = next(
            (s for s in self._steps if s.agent == "fund_manager"), None
        )
        decision = None
        if fund_step is not None and signal is not None:
            decision = FundManagerRecord(
                action=signal.action,
                target_price=signal.target_price,
                confidence=signal.confidence,
                risk_score=signal.risk_score,
                reasoning=signal.reasoning,
                step=fund_step,
            )

        current_round = max(
            (s.round for s in self._steps if s.round > 0),
            default=0,
        )

        completed_at = datetime.now(tz=UTC) if status != "running" else None

        return AnalysisRecord(
            run_id=self._run_id,
            stock_code=self._stock_code,
            stock_name=self._stock_name,
            trade_date=self._trade_date,
            status=status,  # type: ignore[arg-type]
            max_rounds=self._max_rounds,
            current_round=current_round,
            steps=list(self._steps),
            analysts=analysts,
            intelligence_officer=intelligence,
            debates=debates,
            risk_assessment=risk,
            decision=decision,
            signal_id=None,
            created_at=self._created_at,
            completed_at=completed_at,
            error=error,
        )

    def _build_debate_rounds(self) -> list[DebateRoundRecord]:
        """Group bull/bear steps by round number."""
        by_round: dict[int, dict[str, AgentStepRecord]] = {}
        for step in self._steps:
            if step.agent not in ("bull_researcher", "bear_researcher"):
                continue
            if step.round <= 0:
                continue
            bucket = by_round.setdefault(step.round, {})
            side = "bull" if step.agent == "bull_researcher" else "bear"
            bucket[side] = step
        rounds: list[DebateRoundRecord] = []
        for r in sorted(by_round.keys()):
            b = by_round[r]
            rounds.append(
                DebateRoundRecord(
                    round=r,
                    bull=b.get("bull"),
                    bear=b.get("bear"),
                )
            )
        return rounds


__all__ = [
    "ANALYST_AGENTS",
    "EventEmitter",
    "RunCollector",
    "classify_status",
    "extract_content",
]
