"""O-004 off-market briefing provider (orchestration seam).

Assembles the "off-market information" the multi-agent debate reads as
deliberation background (R0 §1, P0-8-amendment-2026-05-24): the latest
MiroFish sector-forecast evidence + the deterministic multi-domain
market/news digest, rendered as a bounded TEXT block.

Boundary: this is **evidence text for deliberation**, never a decision.
The LLM agents still write only the four allowed text fields; the block
is explicitly labelled as background and forbids deriving order fields
from it (the prompt wrapper in agents.py reinforces this). Reading
evidence cannot construct an InstructionPlan or touch RiskCheckSummary.

Fail-open by construction: any gap (no evidence, Mongo error, malformed
doc) yields ``""`` so the debate runs bit-identical to the no-evidence
path. PIT discipline matches O-003 — the consumed evidence is dated
strictly before the deterministic selection-day boundary.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

import structlog

log = structlog.get_logger(component="orchestration.off_market")

# Total briefing budget so the debate prompt stays bounded/cheap.
MAX_BRIEFING_CHARS = 2500
# Per-evidence excerpt cap.
MAX_EXCERPT_CHARS = 600
# How many recent rows to pull per evidence family.
_FORECAST_LIMIT = 1
_DIGEST_LIMIT = 2
# Reject evidence older than this many calendar days before the selection
# day, mirroring the O-003 advisory recency guard — a stalled EOD pipeline
# must not inject a weeks-old digest/forecast into the debate prompts.
MAX_EVIDENCE_AGE_DAYS = 5

# Evidence families surfaced as off-market background, in render order.
# MARKET-DIGEST / NEWS-DIGEST are O-001; MIROFISH-FORECAST is O-002.
_FAMILIES: tuple[tuple[str, str], ...] = (
    ("板块推演", "MIROFISH-FORECAST-"),
    ("市场汇总", "MARKET-DIGEST-"),
    ("资讯汇总", "NEWS-DIGEST-"),
)


class OffMarketBriefingProvider:
    """Builds the debate's off-market briefing text from evidence_collection."""

    def __init__(self, *, mongodb: Any) -> None:
        self._mongodb = mongodb
        self._log = log

    async def __call__(
        self, codes: Sequence[str], *, trade_date: str
    ) -> str:
        """Return the bounded briefing text, or ``""`` on any gap.

        ``trade_date`` is the deterministic selection-day boundary (the day
        after the T-1 frame, matching O-003); evidence is consumed strictly
        before it so the briefing is replayable and free of look-ahead.
        """
        if self._mongodb is None or not trade_date:
            return ""
        try:
            coll = self._mongodb._db["evidence_collection"]  # noqa: SLF001
        except Exception:  # noqa: BLE001 — fail-open
            return ""

        sections: list[str] = []
        for label, prefix in _FAMILIES:
            limit = _FORECAST_LIMIT if "FORECAST" in prefix else _DIGEST_LIMIT
            excerpts = await self._recent_excerpts(
                coll, prefix, trade_date, limit
            )
            if excerpts:
                sections.append(f"【{label}】\n" + "\n".join(excerpts))
        if not sections:
            return ""
        return "\n\n".join(sections)[:MAX_BRIEFING_CHARS]

    async def _recent_excerpts(
        self, coll: Any, prefix: str, trade_date: str, limit: int
    ) -> list[str]:
        """Most-recent ``content`` excerpts for an evidence_id prefix.

        Uses a prefix range query on ``evidence_id`` (the ids embed the
        date, e.g. ``MARKET-DIGEST-20260611``) bounded strictly before the
        selection date's compact form so no same-/future-day evidence leaks
        in. Fail-open: a query error yields no excerpts for that family.
        """
        compact = trade_date.replace("-", "")
        if len(compact) != 8 or not compact.isdigit():
            return []
        try:
            sel = dt.date.fromisoformat(trade_date)
        except ValueError:
            return []
        # Bound BOTH ends at the query: only evidence dated within
        # [selection - MAX_AGE, selection) survives, so a stalled pipeline's
        # weeks-old row is never injected (codex O-004 staleness guard).
        lo_compact = (
            sel - dt.timedelta(days=MAX_EVIDENCE_AGE_DAYS)
        ).strftime("%Y%m%d")
        lo = f"{prefix}{lo_compact}"
        hi = f"{prefix}{compact}"  # exclusive upper bound (strictly before T)
        try:
            cursor = (
                coll.find(
                    {"evidence_id": {"$gte": lo, "$lt": hi}}
                )
                .sort("evidence_id", -1)
                .limit(limit)
            )
            docs = await cursor.to_list(length=limit)
        except Exception as exc:  # noqa: BLE001 — fail-open per family
            self._log.warning(
                "off_market_query_failed", prefix=prefix, error=str(exc)
            )
            return []
        out: list[str] = []
        for doc in docs:
            content = str(doc.get("content", "")).strip()
            if content:
                out.append(content[:MAX_EXCERPT_CHARS])
        return out


__all__ = [
    "MAX_BRIEFING_CHARS",
    "MAX_EXCERPT_CHARS",
    "OffMarketBriefingProvider",
]
