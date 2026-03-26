"""Pydantic models and TypedDict state for the multi-agent analysis pipeline."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class DebateState(TypedDict):
    """State tracking for Bull/Bear debate rounds."""

    history: str
    bull_history: str
    bear_history: str
    current_response: str
    count: int


class AnalysisState(TypedDict):
    """LangGraph state passed through the analysis pipeline."""

    stock_code: str
    stock_name: str
    trade_date: str
    # Stage 1: analysis reports
    news_report: str
    sentiment_report: str
    fundamental_report: str
    technical_report: str
    intelligence_report: str
    # Stage 2: debate
    debate_state: DebateState
    # Stage 3: decision
    risk_assessment: str
    trading_signal: dict[str, Any]


class TradingSignal(BaseModel):
    """Final output of the multi-agent analysis pipeline."""

    model_config = ConfigDict(frozen=True)

    action: Literal["买入", "持有", "卖出"]
    target_price: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    risk_score: float = Field(ge=0.0, le=1.0)
    reasoning: str
    stock_code: str
    stock_name: str
    trade_date: str


class PipelineConfig(BaseModel):
    """Configuration for the analysis pipeline."""

    model_config = ConfigDict(frozen=True)

    max_debate_rounds: int = 2
    analysis_timeout_seconds: int = 300


class AnalysisServices(BaseModel):
    """Bundle of services injected into agent nodes."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    llm_router: Any  # LLMRouter
    market_data: Any  # MarketDataService
    history_data: Any  # HistoryDataService
    news_crawler: Any  # NewsCrawlerService
    mirofish_simulator: Any = None  # MiroFishSimulator (optional)
    mongodb: Any = None  # MongoDBService (optional, for simulation persistence)
    pipeline_config: PipelineConfig = PipelineConfig()
