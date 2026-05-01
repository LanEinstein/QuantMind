"""Analysis record data models for full multi-agent run persistence.

Separate from TradingSignal to avoid polluting the terminal decision model.
Used by graph.run_analysis() instrumentation and the analysis history API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.models import TradingSignal

AgentName = Literal[
    "news_crawler",
    "sentiment_analyst",
    "fundamental_analyst",
    "technical_analyst",
    "intelligence_officer",
    "bull_researcher",
    "bear_researcher",
    "risk_officer",
    "fund_manager",
]

AgentStepStatus = Literal["running", "completed", "failed"]

AnalysisRunStatus = Literal["running", "completed", "failed"]


class EvidenceItem(BaseModel):
    """Evidence citation attached to an agent step."""

    model_config = ConfigDict(frozen=True)

    source: str
    snippet: str = ""
    sentiment: Literal["positive", "mixed", "negative"] = "mixed"


class AgentStepRecord(BaseModel):
    """Single agent invocation outcome in a run timeline.

    Tokens and cost default to 0 when the LLM SDK does not expose usage
    data; they must never be fabricated. Aggregate cost is tracked via
    cost_tracking collection, not here.
    """

    model_config = ConfigDict(frozen=True)

    agent: AgentName
    round: int = 0
    content: str = ""
    model_label: str = ""
    model_id: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    cost_cny: float = 0.0
    evidence: list[EvidenceItem] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    status: AgentStepStatus = "completed"
    error: str | None = None


class DebateRoundRecord(BaseModel):
    """One debate round — bull then bear (either may be missing if the
    debate terminated mid-round, or if a round only had one side)."""

    model_config = ConfigDict(frozen=True)

    round: int
    bull: AgentStepRecord | None = None
    bear: AgentStepRecord | None = None


class RiskAssessmentRecord(BaseModel):
    """Risk officer structured output.

    `checks` may legitimately be empty — do not fabricate pass items.
    """

    model_config = ConfigDict(frozen=True)

    content: str = ""
    checks: list[dict] = Field(default_factory=list)
    step: AgentStepRecord


class FundManagerRecord(BaseModel):
    """Fund manager final decision, mirrored from TradingSignal."""

    model_config = ConfigDict(frozen=True)

    action: Literal["买入", "持有", "卖出"]
    target_price: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    risk_score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    step: AgentStepRecord


class AnalysisRecord(BaseModel):
    """Complete multi-agent analysis run record.

    One per run. Persisted in `analysis_records` MongoDB collection keyed
    by `run_id`. History view and detail view both read from here.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    stock_code: str
    stock_name: str
    trade_date: str
    status: AnalysisRunStatus = "running"
    max_rounds: int = 2
    current_round: int = 0

    steps: list[AgentStepRecord] = Field(default_factory=list)
    analysts: list[AgentStepRecord] = Field(default_factory=list)
    intelligence_officer: AgentStepRecord | None = None
    debates: list[DebateRoundRecord] = Field(default_factory=list)
    risk_assessment: RiskAssessmentRecord | None = None
    decision: FundManagerRecord | None = None

    signal_id: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC)
    )
    completed_at: datetime | None = None
    error: str | None = None


class AnalysisRunResult(BaseModel):
    """Bundle returned by run_analysis(): terminal signal + full record."""

    model_config = ConfigDict(frozen=True)

    signal: TradingSignal
    record: AnalysisRecord


class AnalysisSummary(BaseModel):
    """Compact row for the history list endpoint."""

    model_config = ConfigDict(frozen=True)

    id: str
    run_id: str
    stock_code: str
    stock_name: str
    trade_date: str
    status: AnalysisRunStatus
    action: Literal["买入", "持有", "卖出"] | None = None
    confidence: float | None = None
    risk_score: float | None = None
    signal_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
