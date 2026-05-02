"""Shadow decision recording for Phase 5B exit verification.

This module is the data-layer half of the shadow-test harness. It defines
the immutable ``ShadowDecisionEntry`` schema and the read/write API
against the ``shadow_decisions`` MongoDB collection. The companion CLI
``scripts/shadow_compare.py`` consumes these documents to produce the
action-consistency / confidence-deviation report Phase 5B exit gates on.

Design notes
------------

* **Pure data-layer.** This module is intentionally NOT wired into the
  live LangGraph pipeline. Doubling LLM calls in production would
  invalidate the cost-savings story P5B-T03 was built to tell. Operators
  wire the recorder through a separate scheduled job once deployment
  starts (Phase 5C deployment task). Tests therefore drive it directly.
* **Immutable entries.** Every field is frozen so a record cannot drift
  between the moment it is built and the moment it lands in Mongo —
  protects against subtle aliasing bugs in async pipelines.
* **UTC clock.** Matches the convention pinned by
  ``backend.llm.fallback._utc_date_str()`` so daily rollups elsewhere in
  the system line up; do NOT switch to ``datetime.now()`` (no tz). See
  P5B-T03 codex R6 for the timezone-drift bug this convention prevents.
* **Fail-soft writes.** The recorder swallows Mongo errors and logs a
  structured warning. Shadow recording is observability — a Mongo blip
  must not crash the calling job.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from backend.data.database import MongoDBService

log = structlog.get_logger(component="shadow_recorder")

SHADOW_COLLECTION = "shadow_decisions"
_TTL_DAYS_DEFAULT = 30
_VALID_ACTIONS: frozenset[str] = frozenset({"买入", "持有", "卖出"})


@dataclass(frozen=True)
class ShadowDecisionLeg:
    """One side (baseline or routed) of a shadow comparison.

    ``parse_ok`` records whether the LLM response was JSON-parseable.
    The harness keeps unparseable runs because they are themselves a
    quality signal — a routing change that drives parse-failure rate up
    is a regression even if the surviving runs still match.

    ``escalated`` is meaningful only for the routed leg; the baseline leg
    sets it to ``False`` by convention. Storing both keeps the document
    schema-symmetric and the consumer code branch-free.
    """

    action: str
    confidence: float
    model: str
    latency_ms: float
    escalated: bool
    parse_ok: bool

    def __post_init__(self) -> None:
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(_VALID_ACTIONS)}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.confidence, (int, float)) or isinstance(
            self.confidence, bool
        ):
            raise ValueError(
                f"confidence must be a finite float in [0,1], got "
                f"{self.confidence!r}"
            )
        conf = float(self.confidence)
        if not math.isfinite(conf) or conf < 0.0 or conf > 1.0:
            raise ValueError(
                f"confidence must be a finite float in [0,1], got {conf!r}"
            )
        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError(
                f"latency_ms must be a finite, non-negative float, got "
                f"{self.latency_ms!r}"
            )


@dataclass(frozen=True)
class ShadowDecisionEntry:
    """A baseline-vs-routed pair of fund_manager decisions for one run.

    The pair shares ``run_id`` so each entry carries both decisions
    side-by-side and the consumer never has to join two collections.
    """

    run_id: str
    stock_code: str
    trade_date: str
    created_at: datetime.datetime
    baseline: ShadowDecisionLeg
    routed: ShadowDecisionLeg

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be a non-empty string")
        if not self.stock_code:
            raise ValueError("stock_code must be a non-empty string")
        if not self.trade_date:
            raise ValueError("trade_date must be a non-empty string")
        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware (UTC); naive datetimes "
                "drift across daylight-saving boundaries"
            )

    def to_document(self) -> dict[str, Any]:
        """Serialise to a Mongo-friendly dict.

        Keeps ``created_at`` as a real ``datetime`` (Mongo encodes it as
        BSON Date) so range queries work; everything else is plain JSON.
        """
        doc: dict[str, Any] = {
            "run_id": self.run_id,
            "stock_code": self.stock_code,
            "trade_date": self.trade_date,
            "created_at": self.created_at,
            "baseline": asdict(self.baseline),
            "routed": asdict(self.routed),
        }
        return doc


async def record_shadow_decision(
    mongodb: MongoDBService,
    entry: ShadowDecisionEntry,
) -> bool:
    """Upsert a shadow comparison entry into the ``shadow_decisions`` collection.

    Upsert key is ``run_id`` so re-runs (e.g. operator replays) overwrite
    rather than accumulate noise. Returns True on success, False on Mongo
    error — the caller logs but does not raise. Shadow tracking is
    observability and must never propagate a failure into a real trading
    run.
    """
    try:
        coll = mongodb._db[SHADOW_COLLECTION]  # noqa: SLF001
        await coll.update_one(
            {"run_id": entry.run_id},
            {"$set": entry.to_document()},
            upsert=True,
        )
        return True
    except Exception as exc:
        log.warning(
            "shadow_record_failed",
            run_id=entry.run_id,
            stock_code=entry.stock_code,
            error=str(exc),
        )
        return False


async def query_shadow_decisions(
    mongodb: MongoDBService,
    *,
    days: int = 7,
    now: datetime.datetime | None = None,
) -> list[dict[str, Any]]:
    """Return shadow_decisions documents for the last ``days`` days.

    ``now`` is injectable so tests can pin the clock without monkey-
    patching ``datetime.datetime``. The cutoff is computed in UTC to
    match the writer convention.

    Empty result is normal (no shadow data collected yet) and is
    returned as ``[]`` — never ``None`` — so consumers can iterate
    without a None check.
    """
    if days <= 0:
        raise ValueError(f"days must be positive, got {days}")
    cutoff = (
        now.astimezone(datetime.UTC)
        if now is not None
        else datetime.datetime.now(tz=datetime.UTC)
    ) - datetime.timedelta(days=days)

    try:
        coll = mongodb._db[SHADOW_COLLECTION]  # noqa: SLF001
        cursor = coll.find({"created_at": {"$gte": cutoff}})
        # Drop the Mongo ObjectId so consumers (script + tests) can
        # JSON-serialise the result without bespoke encoders.
        return [
            {k: v for k, v in doc.items() if k != "_id"}
            async for doc in cursor
        ]
    except Exception as exc:
        log.warning(
            "shadow_query_failed",
            days=days,
            error=str(exc),
        )
        return []


__all__ = [
    "SHADOW_COLLECTION",
    "ShadowDecisionEntry",
    "ShadowDecisionLeg",
    "query_shadow_decisions",
    "record_shadow_decision",
]
