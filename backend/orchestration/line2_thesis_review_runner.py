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

from backend.integrations.feishu.renderer import MessageRenderer
from backend.models.position_thesis import PositionThesis
from backend.orchestration.instruction_dispatcher import (
    FeishuSender,
    OutboxRepository,
)
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
        renderer: MessageRenderer | None = None,
        digest_sender: FeishuSender | None = None,
        digest_chat_id: str = "",
        digest_outbox: OutboxRepository | None = None,
        pilot: bool = False,
    ) -> None:
        self._client = client
        self._evidence_writer = evidence_writer
        # W-003 display-only digest: a once-per-run overview sent AFTER the
        # reviews, INDEPENDENT of any dispatcher (own outbox key, own claim).
        # All three must be wired (feishu_interactive) for it to send; otherwise
        # the digest is silently skipped (simulation_auto / offline).
        self._renderer = renderer
        self._digest_sender = digest_sender
        self._digest_chat_id = digest_chat_id
        self._digest_outbox = digest_outbox
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
        persisted: list[ThesisAdvisoryVerdict] = []
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
            if await self._write_evidence(verdict):
                persisted.append(verdict)

        # W-003: a display-only digest AFTER the reviews, once per run, idempotent.
        # Only verdicts whose evidence DURABLY persisted are digested (codex
        # W-003 P2): the owner must never receive an advisory that has no
        # evidence_collection trail (evidence-only / provenance guarantee). On a
        # Mongo outage (writes fail) the digest is skipped entirely.
        if persisted:
            await self._send_thesis_digest(tuple(persisted), now)

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

    async def _send_thesis_digest(
        self, verdicts: tuple[ThesisAdvisoryVerdict, ...], now: datetime
    ) -> None:
        """Send the display-only thesis-review overview once per run (idempotent).

        Independent of any dispatcher: its own outbox key
        ``thesis-review-digest-{trade_date}`` is the at-most-once gate, so a
        same-day re-run never double-sends. Skipped when the digest channel is
        not wired (simulation_auto / offline). Never raises — a digest is an
        operator convenience, not a gate (P0-3 §display-only / mirrors the
        Line-1 basket digest).
        """
        if (
            self._renderer is None
            or self._digest_sender is None
            or self._digest_outbox is None
            or not self._digest_chat_id
        ):
            return
        trade_date = now.strftime("%Y-%m-%d")
        key = f"thesis-review-digest-{trade_date}"
        try:
            if not await self._digest_outbox.try_claim(key, at=now):
                log.info("thesis_digest_skipped_duplicate", trade_date=trade_date)
                return
            text = self._renderer.render_thesis_review_digest(
                list(verdicts), pilot=self._pilot
            )
            result = await self._digest_sender.send_message(
                self._digest_chat_id, text, uuid=key
            )
            if result.ok:
                await self._digest_outbox.mark_sent(
                    key, message_id=result.message_id, at=now
                )
                log.info(
                    "thesis_digest_sent",
                    trade_date=trade_date,
                    count=len(verdicts),
                    message_id=result.message_id,
                )
            else:
                # Definitive API rejection → release the claim so a later rerun
                # can re-send. A transport EXCEPTION (below) leaves the claim
                # PENDING — never auto-resent.
                await self._digest_outbox.release(key)
                log.warning(
                    "thesis_digest_send_failed", trade_date=trade_date,
                    code=result.code,
                )
        except Exception as exc:  # noqa: BLE001 — digest never blocks the run
            log.warning("thesis_digest_error", trade_date=trade_date, error=str(exc))

    async def _write_evidence(self, verdict: ThesisAdvisoryVerdict) -> bool:
        """Persist the verdict as DEBATE- evidence. Returns True iff persisted.

        Never raises (advisory never blocks the run). A ``False`` return (no
        writer wired, or the insert failed / raised) means the verdict has no
        durable trail, so the caller excludes it from the display-only digest
        (codex W-003 P2 — no advisory to the owner without a persisted record).
        """
        if self._evidence_writer is None:
            return False
        try:
            evidence = build_thesis_review_evidence(verdict)
            return bool(await self._evidence_writer.write(evidence))
        except Exception as exc:  # noqa: BLE001 — advisory never blocks the run
            log.warning(
                "thesis_review_evidence_write_failed",
                code=verdict.code,
                error=str(exc),
            )
            return False


__all__ = [
    "Line2ThesisReviewRunner",
    "ThesisEvidenceWriter",
    "ThesisReviewContextProvider",
    "ThesisReviewRunResult",
]
