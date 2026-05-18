"""AmendmentDrafter — auto-draft ``docs/decisions/pending/{id}.md`` (P2-2 + X-013).

After the X-007 ``ShadowChain`` reports a passing challenger verdict
the dispatcher (X-008) invokes this module to write a markdown draft
the human reviewer can read before promoting the artifact. The draft
is intentionally *mechanical* — no LLM-authored narrative — so the
reviewer's attention focuses on the shadow metrics + the diff rather
than on chasing free-form prose.

R7 red line (P2-2 §2 red line 23): every draft must contain the four
mandatory sections, in order:

1. ``## diff``                — what changed (rendered from the
                                ``DiffBlock`` the caller supplies).
2. ``## shadow evidence``     — challenger-vs-champion verdict +
                                bootstrap CI + metric table.
3. ``## readability check``   — flags such as ``length_inflation``
                                (challenger prompt length >50% larger
                                than champion) so prompt regressions
                                surface before promotion.
4. ``## rollback``            — explicit rollback instructions; rolls
                                back to ``champion_baseline_id`` via the
                                file-based prompt registry.

Missing any section raises :class:`AmendmentSchemaError` BEFORE the
file is written so the half-baked draft never appears on disk.

Cost guard: the drafter is presently template-driven and emits zero
LLM calls, so the ¥5 budget envelope (P2-2 §1.1.1 / P1-7 §1.4) is
trivially satisfied. The interface still routes through
:func:`backend.services.cost_guard.assert_budget_allows` when the
caller passes a redis client so a future LLM-augmented templates
upgrade slots in without changing the dispatcher (X-008) wiring.

Module isolation: zero ``backend.{api, broker, risk, llm, agents,
mirofish, data}`` imports — Phase X red line (P2-2 §2 red line 17).
``backend.services.cost_guard`` is explicitly allowed (it is the
budget substrate; itself isolated from the decision path).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from backend.services.cost_guard import assert_budget_allows
from backend.services.evolution_audit_writer import EvolutionAuditWriter
from backend.services.shadow_chain import (
    ChallengerVerdict,
    ShadowAcceptanceReport,
)

if TYPE_CHECKING:
    import redis.asyncio

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

MANDATORY_SECTIONS: tuple[str, ...] = (
    "## diff",
    "## shadow evidence",
    "## readability check",
    "## rollback",
)
"""Four R7 sections (P2-2 §2 red line 23). Order is fixed so the
reviewer always sees the diff before the verdict justification."""

PENDING_DIR = Path("docs/decisions/pending")
"""Where every drafted amendment lands. The X-014 notifier validates
the path matches this directory before paging the operator."""

DEFAULT_LENGTH_INFLATION_THRESHOLD = 0.50
"""50% size growth triggers the ``length_inflation`` warning (P2-2
§2 red line 23). Plain ratio: ``new_chars / old_chars > 1.50``."""

ARTIFACT_TYPES: frozenset[str] = frozenset(
    {"prompt", "rag_document", "risk_parameter_proposal", "exemplar_schema"}
)

AMENDMENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
"""Locked allowed shape for ``amendment_id``. Rejects path separators
``/``, ``\\``, and ``..`` so an attacker-supplied id cannot write
outside ``docs/decisions/pending/`` via path traversal (codex review
P1-2)."""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AmendmentDrafterError(Exception):
    """Base error for the drafter."""


class AmendmentSchemaError(AmendmentDrafterError):
    """Raised when the assembled draft lacks one of the four R7 sections."""


class AmendmentBudgetError(AmendmentDrafterError):
    """Raised when ``cost_guard`` would block the draft as over-budget."""


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiffBlock:
    """Pre-rendered diff snippet for the ``## diff`` section.

    The caller (X-008 dispatcher) is responsible for producing this
    string — typically a unified-diff between champion and challenger
    artifact bodies. The drafter does NOT consume the raw artifact
    bytes so it stays decoupled from prompt-text parsing.
    """

    label: str
    body: str
    """Markdown-safe body. Caller pre-renders it (e.g. fenced code
    block) — the drafter inlines the string verbatim."""


@dataclass(frozen=True)
class DraftResult:
    """Outcome of one :meth:`AmendmentDrafter.draft` call."""

    amendment_id: str
    amendment_path: Path
    flags: tuple[str, ...] = field(default_factory=tuple)
    """Readability-check flags emitted (currently ``length_inflation``)."""


# ---------------------------------------------------------------------------
# Drafter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmendmentDrafter:
    """Mechanical amendment drafter — no LLM call in the body.

    Frozen so the audit writer / pending-dir wiring cannot be swapped
    at runtime. The X-008 dispatcher constructs one instance at boot.
    """

    audit: EvolutionAuditWriter
    pending_dir: Path = PENDING_DIR
    length_inflation_threshold: float = DEFAULT_LENGTH_INFLATION_THRESHOLD

    async def draft(
        self,
        *,
        amendment_id: str,
        artifact_type: Literal[
            "prompt",
            "rag_document",
            "risk_parameter_proposal",
            "exemplar_schema",
        ],
        artifact_id: str,
        champion_baseline_id: str,
        shadow_report: ShadowAcceptanceReport,
        verdict: ChallengerVerdict,
        diff: DiffBlock,
        champion_body_length: int,
        challenger_body_length: int,
        redis_client: redis.asyncio.Redis | None = None,
        correlation_id: str | None = None,
    ) -> DraftResult:
        """Build the markdown draft, validate the 4 R7 sections, and write.

        Args:
            amendment_id: matches the proposal / artifact id; used as
                the file basename under :data:`PENDING_DIR`.
            artifact_type: discriminator the X-014 notifier echoes to
                the operator.
            artifact_id: original challenger artifact identifier.
            champion_baseline_id: production artifact under comparison.
            shadow_report: P0-6 acceptance report + 3 forensic fields
                from :func:`backend.services.shadow_chain.ShadowChain.run`.
            verdict: :class:`ChallengerVerdict` produced by
                :func:`backend.services.shadow_chain.evaluate_challenger`.
            diff: pre-rendered diff for the ``## diff`` section.
            champion_body_length: raw chars in the champion artifact.
                Used for the readability flag computation.
            challenger_body_length: raw chars in the challenger.
            redis_client: optional; when supplied, ``cost_guard``
                enforces the daily ¥20 hard ceiling before the draft
                proceeds (kept here so a future LLM-augmented variant
                drops in without re-plumbing the dispatcher).
            correlation_id: forwarded to the audit row.

        Returns:
            :class:`DraftResult` with the absolute path of the written
            file + the readability flags.

        Raises:
            AmendmentSchemaError: if the assembled body fails the
                R7 four-section check.
            AmendmentBudgetError: if ``cost_guard`` reports a hard
                breach.
        """
        if artifact_type not in ARTIFACT_TYPES:
            raise AmendmentSchemaError(
                f"artifact_type {artifact_type!r} is not one of "
                f"{sorted(ARTIFACT_TYPES)}"
            )
        if (
            not AMENDMENT_ID_RE.fullmatch(amendment_id)
            or amendment_id in {".", ".."}
            or amendment_id.startswith(".")
            or ".." in amendment_id
        ):
            raise AmendmentSchemaError(
                f"amendment_id {amendment_id!r} contains characters outside "
                f"{AMENDMENT_ID_RE.pattern!r} or traversal sequences "
                f"(``..``, leading ``.``); path separators and traversal "
                f"are forbidden so the draft cannot escape "
                f"docs/decisions/pending/"
            )
        if challenger_body_length < 0 or champion_body_length < 0:
            raise AmendmentSchemaError(
                "challenger/champion body lengths must be non-negative"
            )

        if redis_client is not None:
            try:
                await assert_budget_allows(
                    redis_client, agent_name="amendment_drafter"
                )
            except Exception as exc:  # noqa: BLE001 — cost guard fail-closes
                raise AmendmentBudgetError(
                    f"cost_guard blocked amendment_drafter: {exc}"
                ) from exc

        flags = self._compute_flags(
            champion_body_length=champion_body_length,
            challenger_body_length=challenger_body_length,
        )

        body = self._compose_body(
            amendment_id=amendment_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            champion_baseline_id=champion_baseline_id,
            shadow_report=shadow_report,
            verdict=verdict,
            diff=diff,
            flags=flags,
        )
        self._validate_sections(body)

        amendment_path = self._write(amendment_id, body)

        await self.audit.evolution_amendment_drafted(
            amendment_id=amendment_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            amendment_path=str(amendment_path).replace("\\", "/"),
            correlation_id=correlation_id,
        )

        return DraftResult(
            amendment_id=amendment_id,
            amendment_path=amendment_path,
            flags=flags,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _compute_flags(
        self,
        *,
        champion_body_length: int,
        challenger_body_length: int,
    ) -> tuple[str, ...]:
        flags: list[str] = []
        if champion_body_length == 0 and challenger_body_length > 0:
            # Going from zero → non-zero is by definition an inflation;
            # surface it instead of swallowing as a divide-by-zero.
            flags.append("length_inflation")
        elif champion_body_length > 0:
            ratio = challenger_body_length / champion_body_length
            if ratio - 1.0 > self.length_inflation_threshold:
                flags.append("length_inflation")
        return tuple(flags)

    def _compose_body(
        self,
        *,
        amendment_id: str,
        artifact_type: str,
        artifact_id: str,
        champion_baseline_id: str,
        shadow_report: ShadowAcceptanceReport,
        verdict: ChallengerVerdict,
        diff: DiffBlock,
        flags: tuple[str, ...],
    ) -> str:
        header = (
            f"# Pending amendment {amendment_id}\n\n"
            f"- artifact_type: {artifact_type}\n"
            f"- artifact_id: {artifact_id}\n"
            f"- champion_baseline_id: {champion_baseline_id}\n"
            f"- drafted_at: {datetime.now(UTC).isoformat()}\n"
        )

        diff_section = (
            "## diff\n\n"
            f"**{diff.label}**\n\n"
            f"{diff.body.rstrip()}\n"
        )

        strict_lines = [
            f"- {c.name}: champion={c.champion_value:.6f} "
            f"challenger={c.challenger_value:.6f} delta={c.delta:+.6f} "
            f"{'PASS' if c.passed else 'FAIL'}"
            for c in verdict.strict_better
        ]
        no_regress_lines = [
            f"- {c.name}: champion={c.champion_value:.6f} "
            f"challenger={c.challenger_value:.6f} delta={c.delta:+.6f} "
            f"{'PASS' if c.passed else 'FAIL'}"
            for c in verdict.no_regression
        ]
        ci_low, ci_high = shadow_report.bootstrap_pnl_ci_95pct
        evidence_section = (
            "## shadow evidence\n\n"
            f"- outcome: {'PASS' if verdict.passed else 'FAIL'}\n"
            f"- champion_passed_all_gates: {verdict.champion_passed_all_gates}\n"
            f"- challenger_passed_all_gates: {verdict.challenger_passed_all_gates}\n"
            f"- bootstrap_pnl_ci_95pct: [{ci_low:.4f}, {ci_high:.4f}]\n"
            f"- trade_date: {shadow_report.trade_date}\n"
            f"- trading_days_in_window: {shadow_report.trading_days_in_window}\n"
            "\n"
            "### strict-better metrics\n"
            + ("\n".join(strict_lines) if strict_lines else "- (none)")
            + "\n\n### no-regression metrics\n"
            + ("\n".join(no_regress_lines) if no_regress_lines else "- (none)")
            + "\n"
        )

        flags_block = (
            ", ".join(flags) if flags else "(no readability flags raised)"
        )
        readability_section = (
            "## readability check\n\n"
            f"- challenger_body_length / champion_body_length comparison run\n"
            f"- flags: {flags_block}\n"
        )

        rollback_section = (
            "## rollback\n\n"
            f"To roll back, restore the champion baseline by re-pinning\n"
            f"`{champion_baseline_id}` in the relevant registry and\n"
            f"removing this pending amendment file. The promotion is\n"
            f"reversible until the human reviewer signs off + restarts.\n"
        )

        return (
            header
            + "\n"
            + diff_section
            + "\n"
            + evidence_section
            + "\n"
            + readability_section
            + "\n"
            + rollback_section
        )

    def _validate_sections(self, body: str) -> None:
        for section in MANDATORY_SECTIONS:
            if section not in body:
                raise AmendmentSchemaError(
                    f"R7 violation: draft body is missing section {section!r}; "
                    f"all four sections are mandatory before write_disk"
                )

    def _write(self, amendment_id: str, body: str) -> Path:
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        path = self.pending_dir / f"{amendment_id}.md"
        path.write_text(body, encoding="utf-8")
        return path


__all__ = [
    "AMENDMENT_ID_RE",
    "ARTIFACT_TYPES",
    "AmendmentBudgetError",
    "AmendmentDrafter",
    "AmendmentDrafterError",
    "AmendmentSchemaError",
    "DEFAULT_LENGTH_INFLATION_THRESHOLD",
    "DiffBlock",
    "DraftResult",
    "MANDATORY_SECTIONS",
    "PENDING_DIR",
]
