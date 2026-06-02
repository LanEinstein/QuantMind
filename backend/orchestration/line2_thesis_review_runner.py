"""Line-2 post-close thesis-review runner (Phase W-002).

The 17:30 EOD Line-2 advisory entry point (P0-10-amendment-line2-2026-06-01 §1.2
/ P1-2.A-amendment-2026-06-02). For each held position with a persisted
``PositionThesis`` it asks an LLM to compare the current evidence against the
original buy-logic pillars → intact / weakening / broken + reason text, writes
the verdict to ``evidence_collection`` (DEBATE-), and (W-003) renders a
display-only Feishu digest. The owner reads it and acts manually.

This runner is the orchestration seam that keeps the LLM OUT of
``backend/monitoring`` (which stays zero-LLM + import-isolated): the LLM call is
behind an injected :class:`backend.services.thesis_advisory.ThesisAdvisoryClient`
(cost-gated; never bypasses the ¥100/day cap) and the verdict carries no order
field. The deterministic SELL path is untouched.

LLM red line (orchestration isolation): imports NO
``backend.{api,broker,risk,llm,agents,agents_team,mirofish,data}``. The advisory
client + evidence writer + per-code evidence context are supplied by the caller's
provider (the scheduler / ``main.py``); the runner orchestrates the flow only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

import structlog

from backend.models.position_thesis import PositionThesis
from backend.services.thesis_advisory import (
    ThesisAdvisoryClient,
    ThesisAdvisoryVerdict,
    ThesisReviewEvidence,
    build_thesis_review_evidence,
)

log = structlog.get_logger(component="orchestration.line2_thesis_review_runner")


@runtime_checkable
class ThesisReviewContextProvider(Protocol):
    """Caller-supplied bridge to the held positions + their theses + evidence."""

    @property
    def held_codes(self) -> frozenset[str]:
        """Currently held, settled, bare 6-digit codes."""
        ...

    def open_theses(self) -> Mapping[str, PositionThesis]:
        """code → open :class:`PositionThesis` (from the PositionThesisStore)."""
        ...

    def evidence_context_for(self, code: str) -> str:
        """Deterministic recent-evidence text for ``code`` (news / market /
        MiroFish summary) the LLM compares against the thesis pillars."""
        ...


@runtime_checkable
class ThesisEvidenceWriter(Protocol):
    """Persists a thesis-review verdict to evidence_collection (DEBATE-)."""

    async def write(self, evidence: ThesisReviewEvidence) -> bool: ...


@dataclass(frozen=True)
class ThesisReviewRunResult:
    """Audit-grade summary of one post-close thesis review."""

    trade_date: str
    held_count: int
    reviewed: int
    verdicts: tuple[ThesisAdvisoryVerdict, ...] = ()
    skipped_codes: tuple[str, ...] = ()


class Line2ThesisReviewRunner:
    """Compose the post-close thesis-review chain into one production run."""

    def __init__(
        self,
        *,
        client: ThesisAdvisoryClient,
        evidence_writer: ThesisEvidenceWriter | None = None,
        pilot: bool = False,
    ) -> None:
        self._client = client
        self._evidence_writer = evidence_writer
        self._pilot = pilot

    async def run(
        self,
        *,
        provider: ThesisReviewContextProvider,
        now: datetime,
    ) -> ThesisReviewRunResult:
        """Review every held position's thesis; write evidence; collect verdicts."""
        trade_date = now.strftime("%Y-%m-%d")
        held = provider.held_codes
        theses = provider.open_theses()
        # Only review theses for STILL-held positions — a closed position's thesis
        # is stale (the store's sync_holdings should have retired it; we guard
        # here so a lagging close never produces a phantom review).
        targets = sorted(c for c in theses if c in held)

        verdicts: list[ThesisAdvisoryVerdict] = []
        skipped: list[str] = []
        for code in targets:
            thesis = theses[code]
            context = provider.evidence_context_for(code)
            verdict = await self._client.review(thesis, context, now=now)
            if verdict is None:
                # Budget / dedup / cap / LLM failure → no advisory this position.
                skipped.append(code)
                continue
            verdicts.append(verdict)
            await self._write_evidence(verdict)

        log.info(
            "thesis_review_complete",
            trade_date=trade_date,
            held=len(held),
            reviewed=len(verdicts),
            skipped=len(skipped),
        )
        return ThesisReviewRunResult(
            trade_date=trade_date,
            held_count=len(held),
            reviewed=len(verdicts),
            verdicts=tuple(verdicts),
            skipped_codes=tuple(skipped),
        )

    async def _write_evidence(self, verdict: ThesisAdvisoryVerdict) -> None:
        """Persist the verdict as DEBATE- evidence (never raises)."""
        if self._evidence_writer is None:
            return
        try:
            evidence = build_thesis_review_evidence(verdict)
            await self._evidence_writer.write(evidence)
        except Exception as exc:  # noqa: BLE001 — advisory never blocks the run
            log.warning(
                "thesis_review_evidence_write_failed",
                code=verdict.code,
                error=str(exc),
            )


__all__ = [
    "Line2ThesisReviewRunner",
    "ThesisEvidenceWriter",
    "ThesisReviewContextProvider",
    "ThesisReviewRunResult",
]
