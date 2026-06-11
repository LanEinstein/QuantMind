"""Thesis-review LLM advisory (Phase W-002).

The post-close (17:30) Line-2 advisory path: compare the current evidence
against a held position's original buy-time ``PositionThesis`` pillars and emit a
**health verdict + reason text** — *evidence only*. This is the
"LLM-as-perception, rules-as-actuator" paradigm (P0-10-amendment-line2-2026-06-01
§1.1): the LLM never touches a decision field (side / volume / limit_price /
RiskCheckSummary); it writes ``evidence_collection.content`` (the locked
``DEBATE-`` prefix — LLM reasoning text, reused rather than minting a new prefix)
and a display-only Feishu digest (W-003). The owner reads it and acts manually.

Red lines enforced by construction:

* The LLM call is gated through :func:`reserve_thesis_review_slot` (same
  ``llm:usage`` counter → cannot bypass the ¥100/day hard cap; per-(code,date)
  dedup; daily cap; fail-closed skip on any limit — the advisory is optional).
* The verdict DTO carries **no** order field, so the advisory can never reach an
  InstructionPlan. The deterministic SELL path (Line-2 monitoring) is untouched.
* This module lives OUTSIDE ``backend/monitoring`` (which stays zero-LLM +
  import-isolated); it is a ``backend/services`` LLM helper invoked by the
  orchestration runner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

from backend.models.evidence import EvidencePrefix, validate_evidence_id
from backend.models.position_thesis import PositionThesis, ThesisHealth
from backend.services.cost_guard import (
    reserve_thesis_review_slot,
    settle_budget,
)

if TYPE_CHECKING:  # pragma: no cover — type-only imports
    import redis.asyncio

log = structlog.get_logger(component="services.thesis_advisory")

# The advisory reuses the locked DEBATE- evidence prefix (LLM reasoning/conclusion
# text); Phase W mints NO new prefix (THEME- lands in Phase Y). P0-8 §1.6.2.
_THESIS_REVIEW_LABEL = "thesis"

# Estimated per-call spend reserved before the LLM fires (settled after). The
# real spend is tracked by the router's track_usage. Sized for the dedicated
# ``thesis_reviewer`` agent on kimi-k2.6 (P0-10-amendment-2026-06-11) at the
# TRUE worst case: the call passes no max_tokens, so the provider request is
# defaults.max_tokens 4096 PLUS the router's kimi thinking growth 8000 =
# 12,096 output-billable tokens × ¥30/M ≈ ¥0.363, plus ~2.5k prompt × ¥7.5/M
# ≈ ¥0.019 → reserve ¥0.40 of headroom (kept ≥ the formula by a drift test in
# tests/services/test_theme_llm_client.py). ≤10 reviews/day keeps the
# transient reservation ≪ the ¥100/day hard cap.
_DEFAULT_ESTIMATED_RMB = 0.40


@dataclass(frozen=True)
class ThesisAdvisoryVerdict:
    """One thesis-review advisory verdict — evidence only, never a decision.

    Carries NO order field (side / volume / limit_price / risk_summary) by
    construction: the LLM advisory can never turn into an instruction.
    """

    code: str
    instruction_id: str
    health: ThesisHealth
    reason_text: str
    evidence_id: str
    trade_date: str


@dataclass(frozen=True)
class ThesisReviewEvidence:
    """Frozen DTO for one ``evidence_collection`` thesis-review write (DEBATE-)."""

    evidence_id: str
    content: str
    health: str
    trade_date: str
    instruction_id: str
    stock_codes: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def to_mongo(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "prefix": EvidencePrefix.DEBATE.value,
            "path": "thesis_review",
            "content": self.content,
            "health": self.health,
            "trade_date": self.trade_date,
            "instruction_id": self.instruction_id,
            "stock_codes": list(self.stock_codes),
            "created_at": self.created_at,
        }


def make_thesis_review_evidence_id(code: str, trade_date: str) -> str:
    """Build the locked ``DEBATE-thesis-{YYYYMMDD}-{code}`` evidence id."""
    yyyymmdd = trade_date.replace("-", "")
    return f"{EvidencePrefix.DEBATE.value}-{_THESIS_REVIEW_LABEL}-{yyyymmdd}-{code}"


def build_thesis_review_evidence(
    verdict: ThesisAdvisoryVerdict,
) -> ThesisReviewEvidence:
    """Project a verdict into the DEBATE- evidence DTO."""
    content = f"持仓复盘[{verdict.health.value}] {verdict.code}: {verdict.reason_text}"
    return ThesisReviewEvidence(
        evidence_id=verdict.evidence_id,
        content=content[:4000],
        health=verdict.health.value,
        trade_date=verdict.trade_date,
        instruction_id=verdict.instruction_id,
        stock_codes=(verdict.code,),
    )


# Map an LLM label token → ThesisHealth. The prompt asks the model to lead with
# one of these tokens; parsing is lenient + fail-safe (unparsed → WEAKENING, a
# neutral "look at this" flag for the owner, never a silent INTACT).
_HEALTH_TOKENS: tuple[tuple[str, ThesisHealth], ...] = (
    ("THESIS_BROKEN", ThesisHealth.BROKEN),
    ("THESIS_WEAKENING", ThesisHealth.WEAKENING),
    ("THESIS_INTACT", ThesisHealth.INTACT),
)


def parse_advisory_health(text: str) -> tuple[ThesisHealth, str]:
    """Parse the LLM response into a (health, reason) pair (lenient, fail-safe).

    The reason is the response with the leading label token stripped + collapsed
    to a single line + bounded. An unrecognised response defaults to WEAKENING so
    the owner is prompted to look rather than silently reassured.
    """
    raw = (text or "").strip()
    upper = raw.upper()
    # Anchor to the EARLIEST-occurring label (the leading verdict), NOT the first
    # in enum order — else a rationale that merely *mentions* another label flips
    # the verdict (codex W-002 P3: "THESIS_INTACT ... 未达到 THESIS_BROKEN" must
    # parse as INTACT, the leading token).
    best: tuple[int, ThesisHealth] | None = None
    for token, health in _HEALTH_TOKENS:
        idx = upper.find(token)
        if idx != -1 and (best is None or idx < best[0]):
            best = (idx, health)
    found = best[1] if best is not None else None
    # Strip every label token from the reason (case-insensitive).
    for token, _health in _HEALTH_TOKENS:
        raw = re.sub(re.escape(token), "", raw, flags=re.IGNORECASE)
    reason = " ".join(raw.split())[:1024] or "(无理由文本)"
    return (found or ThesisHealth.WEAKENING), reason


def build_review_prompt(
    thesis: PositionThesis, evidence_context: str
) -> list[dict[str, str]]:
    """Build the messages for the advisory review (evidence-only framing).

    The system prompt hard-forbids any buy/sell recommendation — the LLM is a
    perception layer; the deterministic rules are the actuator. Returns the
    OpenAI-format message list the router consumes.
    """
    pillars = "\n".join(f"  支柱{i + 1}: {p}" for i, p in enumerate(thesis.pillars))
    system = (
        "你是持仓复盘分析师。对比【当前证据】与该持仓【原始买入逻辑支柱】,判断买入"
        "逻辑是否仍然成立。**严格只做证据评估,绝不给出任何买入/卖出/加减仓建议,"
        "绝不输出任何价格、股数或指令。** 回答必须以下列三个标签之一开头,其后用 2-4 句"
        "中文说明理由(引用证据):THESIS_INTACT(逻辑完好)/ THESIS_WEAKENING(逻辑"
        "削弱)/ THESIS_BROKEN(逻辑破坏)。"
    )
    user = (
        f"标的: {thesis.stock_code} {thesis.stock_name}\n"
        f"买入日: {thesis.trade_date}\n"
        f"原始买入逻辑支柱:\n{pillars}\n\n"
        f"当前证据(盘后):\n{evidence_context or '(无新增证据)'}\n\n"
        "请判断买入逻辑当前是否仍成立,并按要求格式回答。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


class ThesisAdvisoryReviewer:
    """Cost-gated LLM client for one thesis review (the orchestration runner's
    injected :class:`ThesisAdvisoryClient`).

    Imports ``backend.llm`` + ``cost_guard`` legitimately — it is the LLM helper
    that lives OUTSIDE ``backend/monitoring`` (which stays zero-LLM). Returns
    ``None`` when the advisory is skipped (budget / dedup / cap) so the runner
    treats it as "no advisory this position".
    """

    def __init__(
        self,
        *,
        router: Any,
        redis_client: redis.asyncio.Redis,
        # P0-10-amendment-2026-06-11: dedicated yaml entry, decoupled from
        # ``intelligence_officer`` (shared by the legacy pipeline + MiroFish)
        # so the three call points can be routed independently.
        agent_name: str = "thesis_reviewer",
        estimated_rmb: float = _DEFAULT_ESTIMATED_RMB,
    ) -> None:
        self._router = router
        self._redis = redis_client
        self._agent_name = agent_name
        self._estimated_rmb = estimated_rmb

    async def review(
        self,
        thesis: PositionThesis,
        evidence_context: str,
        *,
        now: datetime,
    ) -> ThesisAdvisoryVerdict | None:
        """Run one cost-gated advisory review; ``None`` when skipped/failed."""
        trade_date = now.strftime("%Y-%m-%d")
        trigger_key = f"{thesis.stock_code}:{trade_date}"
        reservation = await reserve_thesis_review_slot(
            self._redis,
            trigger_key=trigger_key,
            estimated_rmb=self._estimated_rmb,
            today=now.date(),
        )
        if reservation is None:
            log.info("thesis_review_skipped", code=thesis.stock_code, reason="gated")
            return None
        try:
            messages = build_review_prompt(thesis, evidence_context)
            completion = await self._router.complete(self._agent_name, messages)
            text = completion.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 — advisory never crashes the run
            log.warning(
                "thesis_review_llm_failed", code=thesis.stock_code, error=str(exc)
            )
            return None
        finally:
            await settle_budget(self._redis, reservation)

        health, reason = parse_advisory_health(text)
        evidence_id = make_thesis_review_evidence_id(thesis.stock_code, trade_date)
        return ThesisAdvisoryVerdict(
            code=thesis.stock_code,
            instruction_id=thesis.instruction_id,
            health=health,
            reason_text=reason,
            evidence_id=evidence_id,
            trade_date=trade_date,
        )


class ThesisReviewEvidenceWriter:
    """Single entry point for thesis-review ``evidence_collection`` writes.

    Mirrors :class:`backend.mirofish.output_writer.MiroFishEvidenceWriter`:
    narrow (one insert per call), validates the DEBATE- prefix before Mongo, and
    has NO risk-summary plumbing (evidence-only by construction).
    """

    COLLECTION_NAME = "evidence_collection"

    def __init__(self, mongodb: Any) -> None:
        # ``Any`` (not MongoDBService) keeps this services helper from importing
        # backend.data (TID251). The caller passes the live MongoDBService.
        self._mongodb = mongodb

    async def write(self, evidence: ThesisReviewEvidence) -> bool:
        """Persist one thesis-review evidence row. Returns True on insert."""
        if not evidence.evidence_id.startswith(f"{EvidencePrefix.DEBATE.value}-"):
            raise ValueError(
                f"non-DEBATE thesis-review evidence_id {evidence.evidence_id!r}"
            )
        validate_evidence_id(evidence.evidence_id)
        coll = self._mongodb._db[self.COLLECTION_NAME]
        try:
            await coll.insert_one(evidence.to_mongo())
        except Exception as exc:  # noqa: BLE001 — log + continue (advisory)
            log.warning(
                "thesis_review_evidence_insert_failed",
                evidence_id=evidence.evidence_id,
                error=str(exc),
            )
            return False
        log.info("thesis_review_evidence_written", evidence_id=evidence.evidence_id)
        return True


@runtime_checkable
class ThesisAdvisoryClient(Protocol):
    """The advisory LLM contract the orchestration runner depends on."""

    async def review(
        self, thesis: PositionThesis, evidence_context: str, *, now: datetime
    ) -> ThesisAdvisoryVerdict | None: ...


__all__ = [
    "ThesisAdvisoryClient",
    "ThesisAdvisoryReviewer",
    "ThesisAdvisoryVerdict",
    "ThesisReviewEvidence",
    "ThesisReviewEvidenceWriter",
    "build_review_prompt",
    "build_thesis_review_evidence",
    "make_thesis_review_evidence_id",
    "parse_advisory_health",
]
