"""Post-close attribution review (Phase AA / P1-2.A-amendment-2026-06-12).

Deterministic, facts-first daily attribution: per-trade execution facts
(price vs day VWAP, slippage, fees, holding-period return), pre-registered
counterfactual signals (HOLD plans / rejected orders), and violation
counts — persisted append-only as the objective-evidence substrate for
the Phase AB promotion engine.

Red lines: the module is pure-deterministic — no LLM import, no
``backend.{llm,agents,mirofish}``; LLM review prose (if any) lives in
the orchestration layer and is evidence-only. Hindsight counterfactuals
are non-promotable by construction (only pre-registered signals may
carry ``promotable=True``).
"""

from backend.review.attribution import build_daily_review, build_trade_fact
from backend.review.models import (
    ATTRIBUTION_CODE_VERSION,
    REVIEW_SCHEMA_VERSION,
    CounterfactualEntry,
    CounterfactualKind,
    DailyReviewRecord,
    TradeFact,
    TradeSide,
    VwapQuality,
)
from backend.review.store import MongoReviewRecordStore

__all__ = [
    "ATTRIBUTION_CODE_VERSION",
    "REVIEW_SCHEMA_VERSION",
    "CounterfactualEntry",
    "CounterfactualKind",
    "DailyReviewRecord",
    "MongoReviewRecordStore",
    "TradeFact",
    "TradeSide",
    "VwapQuality",
    "build_daily_review",
    "build_trade_fact",
]
