"""EvolutionDispatcher — single entry point for the 4 self-evolution lanes (X-008).

The 22:00 ``evolution_shadow_run`` cron (X-005) and any manual
operator invocations route through one object so the
``Phase X 18-module isolation`` constraint (P2-2 §2 red line 17)
stays trivially verifiable: only this dispatcher imports the four
artifact-specific helpers (X-007 ShadowChain, X-009 DSPyGEPARunner,
X-010 FrontierCrawler, X-011 RagIngester, X-013 AmendmentDrafter,
X-014 EvolutionFeishuNotifier, X-015 EvolutionAuditWriter); the rest
of the codebase only depends on the dispatcher's surface.

Four lanes (one method each), all of which terminate in either an
``IGNORED`` outcome (no challenger work to do) or a passing-shadow
amendment + Feishu page:

* :meth:`run_prompt_evolution`           — wraps the X-009 GEPA loop.
* :meth:`run_rag_ingest`                 — wraps the X-010 + X-011
                                           crawler/ingester chain.
* :meth:`run_risk_proposal_shadow_pass`  — runs the X-007 ShadowChain
                                           against a proposal record.
* :meth:`run_exemplar_schema_refresh`    — exemplar selector smoke
                                           check + shadow validate.

The dispatcher does NOT call into the LLM directly — it forwards into
the X-009 runner, which itself enforces the R1 cap + cost_guard.
``activate_*`` methods on the soft-degrade manager remain off-limits
(LLM-author-reachable code must never invoke them — P0-10 §1.2).

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from backend.evolution.frontier_crawler import (
    FrontierCrawler,
    FrontierCrawlResult,
)
from backend.models.risk_proposal import RiskParameterProposal
from backend.services.amendment_drafter import (
    AmendmentDrafter,
    DiffBlock,
    DraftResult,
)
from backend.services.dspy_gepa_runner import (
    DSPyGEPARunner,
    GEPARunResult,
    GEPATrainingExample,
)
from backend.services.evolution_audit_writer import EvolutionAuditWriter
from backend.services.evolution_feishu_notifier import (
    EvolutionFeishuNotifier,
    EvolutionNotifyResult,
)
from backend.services.exemplar_selector import ExemplarSelector
from backend.services.shadow_chain import (
    ChallengerVerdict,
    ShadowAcceptanceReport,
    ShadowChain,
)

if TYPE_CHECKING:
    import redis.asyncio

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome envelopes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispatchOutcome:
    """Shared shape for every lane's result.

    The four discriminator strings let callers (BrokerScheduler 5th
    cron, tests) tell why no amendment was drafted without parsing
    free-form ``reason`` strings.
    """

    lane: Literal["prompt", "rag", "risk_proposal", "exemplar"]
    artifact_id: str
    status: Literal[
        "ignored", "shadow_failed", "drafted_and_notified", "skipped_no_shadow"
    ]
    shadow_passed: bool = False
    draft_result: DraftResult | None = None
    notify_result: EvolutionNotifyResult | None = None
    gepa_result: GEPARunResult | None = None
    crawl_result: FrontierCrawlResult | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PromptEvolutionTask:
    """Caller-supplied bundle for :meth:`run_prompt_evolution`."""

    agent: str
    seed_prompt: str
    examples: Sequence[GEPATrainingExample]
    champion_baseline_id: str
    champion_body_length: int


@dataclass(frozen=True)
class RiskProposalShadowTask:
    """Caller-supplied bundle for :meth:`run_risk_proposal_shadow_pass`."""

    proposal: RiskParameterProposal
    champion_baseline_id: str
    diff_label: str = "risk proposal diff"
    diff_body: str = ""


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvolutionDispatcher:
    """Single entry point routing the four self-evolution lanes.

    Frozen so the wiring (shadow chain + drafter + notifier + audit
    + GEPA runner + frontier crawler + exemplar selector) cannot drift
    once the BrokerScheduler picks the instance up. The cron simply
    invokes the four ``run_*`` methods in sequence with the day's
    proposal / candidate inputs.
    """

    shadow_chain: ShadowChain
    drafter: AmendmentDrafter
    notifier: EvolutionFeishuNotifier
    audit: EvolutionAuditWriter
    gepa_runner: DSPyGEPARunner | None = None
    frontier_crawler: FrontierCrawler | None = None
    exemplar_selector: ExemplarSelector | None = None

    # ------------------------------------------------------------------
    # Prompt evolution lane (DSPy GEPA → ShadowChain → draft → notify)
    # ------------------------------------------------------------------

    async def run_prompt_evolution(
        self,
        *,
        task: PromptEvolutionTask,
        champion_report: ShadowAcceptanceReport,
        as_of: dt.date,
        correlation_id: str | None = None,
        redis_client: redis.asyncio.Redis | None = None,
    ) -> DispatchOutcome:
        """GEPA → ShadowChain → draft → notify for prompt evolution.

        Returns a :class:`DispatchOutcome` describing the lane's final
        state. ``skipped_no_shadow`` indicates the GEPA runner is
        absent (test wiring); ``ignored`` indicates GEPA produced an
        identical prompt (no challenger to validate).
        """
        if self.gepa_runner is None:
            return DispatchOutcome(
                lane="prompt",
                artifact_id=task.champion_baseline_id,
                status="skipped_no_shadow",
                reason="no GEPA runner wired",
            )

        gepa_result = await self.gepa_runner.run(
            agent=task.agent,
            seed_prompt=task.seed_prompt,
            examples=task.examples,
            redis_client=redis_client,
        )

        if gepa_result.new_prompt_text == task.seed_prompt:
            return DispatchOutcome(
                lane="prompt",
                artifact_id=task.champion_baseline_id,
                status="ignored",
                gepa_result=gepa_result,
                reason="GEPA returned identical prompt — no challenger",
            )

        challenger_id = (
            f"PROMPT-{task.agent}-gepa-"
            f"{gepa_result.started_at.strftime('%Y%m%dT%H%M%SZ')}"
        )

        shadow_report, verdict = self.shadow_chain.run(
            as_of=as_of,
            champion_baseline_id=task.champion_baseline_id,
            champion_report=champion_report,
            challenger_artifact_id=challenger_id,
        )

        return await self._finalise_shadow_outcome(
            lane="prompt",
            artifact_type="prompt",
            artifact_id=challenger_id,
            champion_baseline_id=task.champion_baseline_id,
            champion_body_length=task.champion_body_length,
            challenger_body_length=len(gepa_result.new_prompt_text),
            shadow_report=shadow_report,
            verdict=verdict,
            diff=DiffBlock(
                label=f"prompt diff: {task.agent}",
                body=gepa_result.new_prompt_text,
            ),
            correlation_id=correlation_id,
            gepa_result=gepa_result,
        )

    # ------------------------------------------------------------------
    # RAG ingest lane (FrontierCrawler — no shadow chain step here;
    # documents become first-class once persisted)
    # ------------------------------------------------------------------

    async def run_rag_ingest(
        self,
        *,
        as_of: dt.datetime | None = None,
        correlation_id: str | None = None,
        redis_client: redis.asyncio.Redis | None = None,
    ) -> DispatchOutcome:
        """Fan-out frontier crawl into the RagIngester.

        RAG documents are not challenger artifacts — they are bytes
        the X-013 drafter cites. The lane returns
        ``drafted_and_notified=False`` and ``shadow_passed=False``
        because no challenger comparison is run; the dispatcher
        still emits an outcome row so the cron can log fetch /
        ingest counts uniformly.
        """
        if self.frontier_crawler is None:
            return DispatchOutcome(
                lane="rag",
                artifact_id="(frontier)",
                status="skipped_no_shadow",
                reason="no frontier crawler wired",
            )
        crawl = await self.frontier_crawler.run(
            as_of=as_of,
            redis_client=redis_client,
            correlation_id=correlation_id,
        )
        return DispatchOutcome(
            lane="rag",
            artifact_id="(frontier)",
            status="ignored",
            crawl_result=crawl,
            reason=(
                f"fetched={crawl.fetched} ingested={crawl.ingested} "
                f"rejected={crawl.rejected}"
            ),
        )

    # ------------------------------------------------------------------
    # Risk proposal shadow lane (ShadowChain → draft → notify)
    # ------------------------------------------------------------------

    async def run_risk_proposal_shadow_pass(
        self,
        *,
        task: RiskProposalShadowTask,
        champion_report: ShadowAcceptanceReport,
        as_of: dt.date,
        correlation_id: str | None = None,
    ) -> DispatchOutcome:
        """Run X-007 ShadowChain on a risk-parameter proposal."""
        shadow_report, verdict = self.shadow_chain.run(
            as_of=as_of,
            champion_baseline_id=task.champion_baseline_id,
            champion_report=champion_report,
            challenger_artifact_id=f"RISK-PROPOSAL-{task.proposal.proposal_id}",
        )

        return await self._finalise_shadow_outcome(
            lane="risk_proposal",
            artifact_type="risk_parameter_proposal",
            artifact_id=task.proposal.proposal_id,
            champion_baseline_id=task.champion_baseline_id,
            champion_body_length=(
                len(task.diff_body) or len(task.proposal.proposal_text)
            ),
            challenger_body_length=len(task.proposal.proposal_text),
            shadow_report=shadow_report,
            verdict=verdict,
            diff=DiffBlock(label=task.diff_label, body=task.diff_body),
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # Exemplar schema refresh lane (smoke check + shadow validate)
    # ------------------------------------------------------------------

    async def run_exemplar_schema_refresh(
        self,
        *,
        challenger_schema_id: str,
        champion_baseline_id: str,
        champion_report: ShadowAcceptanceReport,
        as_of: dt.date,
        diff: DiffBlock,
        champion_body_length: int,
        challenger_body_length: int,
        correlation_id: str | None = None,
    ) -> DispatchOutcome:
        """Validate a new exemplar schema via the shared shadow chain."""
        shadow_report, verdict = self.shadow_chain.run(
            as_of=as_of,
            champion_baseline_id=champion_baseline_id,
            champion_report=champion_report,
            challenger_artifact_id=challenger_schema_id,
        )
        return await self._finalise_shadow_outcome(
            lane="exemplar",
            artifact_type="exemplar_schema",
            artifact_id=challenger_schema_id,
            champion_baseline_id=champion_baseline_id,
            champion_body_length=champion_body_length,
            challenger_body_length=challenger_body_length,
            shadow_report=shadow_report,
            verdict=verdict,
            diff=diff,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # Shared shadow-pass finaliser
    # ------------------------------------------------------------------

    async def _finalise_shadow_outcome(
        self,
        *,
        lane: Literal["prompt", "rag", "risk_proposal", "exemplar"],
        artifact_type: Literal[
            "prompt", "rag_document", "risk_parameter_proposal", "exemplar_schema"
        ],
        artifact_id: str,
        champion_baseline_id: str,
        champion_body_length: int,
        challenger_body_length: int,
        shadow_report: ShadowAcceptanceReport,
        verdict: ChallengerVerdict,
        diff: DiffBlock,
        correlation_id: str | None,
        gepa_result: GEPARunResult | None = None,
    ) -> DispatchOutcome:
        await self.audit.shadow_evolution_run_completed(
            challenger_artifact_id=artifact_id,
            champion_baseline_id=champion_baseline_id,
            passed=verdict.passed,
            metrics_summary=_metrics_summary(verdict),
            correlation_id=correlation_id,
        )

        if not verdict.passed:
            return DispatchOutcome(
                lane=lane,
                artifact_id=artifact_id,
                status="shadow_failed",
                shadow_passed=False,
                gepa_result=gepa_result,
                reason="shadow chain rejected challenger",
            )

        draft = await self.drafter.draft(
            amendment_id=artifact_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            champion_baseline_id=champion_baseline_id,
            shadow_report=shadow_report,
            verdict=verdict,
            diff=diff,
            champion_body_length=champion_body_length,
            challenger_body_length=challenger_body_length,
            correlation_id=correlation_id,
        )
        amendment_path_str = str(draft.amendment_path).replace("\\", "/")
        if "docs/decisions/pending/" not in amendment_path_str:
            amendment_path_str = (
                f"docs/decisions/pending/{draft.amendment_path.name}"
            )
        notify = await self.notifier.fire_pending(
            amendment_id=artifact_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            amendment_path=amendment_path_str,
            correlation_id=correlation_id,
        )
        return DispatchOutcome(
            lane=lane,
            artifact_id=artifact_id,
            status="drafted_and_notified",
            shadow_passed=True,
            draft_result=draft,
            notify_result=notify,
            gepa_result=gepa_result,
        )


def _metrics_summary(verdict: ChallengerVerdict) -> dict[str, float]:
    out: dict[str, float] = {}
    for cmp in verdict.strict_better + verdict.no_regression:
        out[f"{cmp.name}__champion"] = cmp.champion_value
        out[f"{cmp.name}__challenger"] = cmp.challenger_value
        out[f"{cmp.name}__delta"] = cmp.delta
    return out


__all__ = [
    "DispatchOutcome",
    "EvolutionDispatcher",
    "PromptEvolutionTask",
    "RiskProposalShadowTask",
]
