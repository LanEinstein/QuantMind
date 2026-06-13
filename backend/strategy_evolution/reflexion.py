"""Reflexion lessons + FinMem-style decaying exemplars (T-004).

Governance: P0-10-amendment-2026-05-24 §2.2 (人格稳定 vs 行为进化分离) + P2-2
(保守 3 路径离线进化 + 人工 gate + LiveArtifactRegistry pin) + R0 §8. This is the
OFFLINE behavioural-evolution half of the trader-agent design: the persona card
(T-001) defines *who the agent is* and is IMMUTABLE; the exemplars curated here
demonstrate *what a good output looks like* and evolve — never the frozen
identity skeleton.

Hard constraints enforced here (all deterministic, all fail-closed):

* **≤3 exemplars per persona** (:data:`MAX_EXEMPLARS`, FinMem cap) — a curated
  set over the cap is truncated by decay-weight, never widened.
* **Passed-cases only** — an exemplar may only come from a debate whose order
  was RiskEngine-VALIDATED *and* realised a good outcome. A rejected or losing
  case is never demonstrated as "good".
* **Policy-linted** — every exemplar text must pass the AB-006 prompt deny-list
  (``lint_prompt_artifact``): an exemplar that smuggles a buy/sell directive,
  an order quantity, or an injection marker is dropped, never promoted.
* **Human-gated pin** — the curated set is rendered into a NEW persona card
  version (skeleton frozen, exemplars filled); that card's SHA256 must be
  approved as a ``PROMPT_VERSION`` in the LiveArtifactRegistry (via amendment +
  restart) before runtime use. This module only PROPOSES; it never auto-applies.

Module isolation (strategy_evolution CLAUDE.md): no
``backend.{api,broker,risk,llm,agents,agents_team,mirofish,data}`` imports — it
reuses only the sibling ``prompt_policy`` lint. The base persona card is passed
in as opaque text so this module never imports the agents_team package.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.strategy_evolution.prompt_policy import lint_prompt_artifact

# The FinMem exemplar cap (P0-10-amendment-2026-05-24 §2.2). Mirrors
# ``backend.agents_team.persona_registry.MAX_PERSONA_EXEMPLARS`` — kept as a
# local constant so this evolution module has no dependency on the agents_team
# package (module isolation). A curated set is truncated to this many.
MAX_EXEMPLARS: int = 3

# The frozen persona-card skeleton keys (mirrors persona_registry) — a proposed
# card version must keep exactly these + the (filled) ``exemplars`` so the
# identity stays immutable while only the demonstrations evolve.
_REQUIRED_CARD_KEYS: frozenset[str] = frozenset(
    {"version", "persona_id", "identity", "mandate", "output_contract"}
)

PERSONA_ID_MAX = 64
EXEMPLAR_TEXT_MAX = 2000


# ---------------------------------------------------------------------------
# Inputs / outputs (frozen)
# ---------------------------------------------------------------------------


class ReflexionOutcome(BaseModel):
    """One past debate's reflexion record — the raw material for curation.

    ``risk_passed`` is True iff the debate's order was RiskEngine-VALIDATED;
    ``profitable`` is True iff it realised a good outcome. Both must hold for the
    advice to be eligible as an exemplar (passed-cases only). ``recency_rank`` is
    0 for the newest record (drives the FinMem decay weight).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    persona_id: str = Field(min_length=1, max_length=PERSONA_ID_MAX)
    advice_text: str = Field(min_length=1, max_length=EXEMPLAR_TEXT_MAX)
    risk_passed: bool
    profitable: bool
    recency_rank: int = Field(ge=0)
    occurred_at: datetime


class Exemplar(BaseModel):
    """One curated, policy-linted demonstration for a persona (immutable)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    persona_id: str = Field(min_length=1, max_length=PERSONA_ID_MAX)
    text: str = Field(min_length=1, max_length=EXEMPLAR_TEXT_MAX)
    weight: float = Field(ge=0.0)


class ExemplarArtifact(BaseModel):
    """A content-addressed, human-pinnable curated exemplar set for one persona.

    ``content_hash()`` is the audit/pin value over the persona + base version +
    ORDERED exemplar texts. The exemplars are baked into a new persona card
    version (``propose_persona_card_version``) whose own SHA256 is what the
    LiveArtifactRegistry approves as a ``PROMPT_VERSION``; this artifact is the
    deterministic, reproducible record of *what was curated from which cases*.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    persona_id: str = Field(min_length=1, max_length=PERSONA_ID_MAX)
    base_version: str = Field(min_length=1, max_length=32)
    exemplar_texts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_cap(self) -> ExemplarArtifact:
        if len(self.exemplar_texts) > MAX_EXEMPLARS:
            raise ValueError(
                f"exemplar set has {len(self.exemplar_texts)} > {MAX_EXEMPLARS} "
                f"(FinMem cap); curation must truncate first"
            )
        for text in self.exemplar_texts:
            if not lint_prompt_artifact(text).passed:
                raise ValueError(
                    "exemplar text failed the prompt policy deny-list; an "
                    "exemplar may never contain a buy/sell/order/injection token"
                )
        return self

    def content_hash(self) -> str:
        """Deterministic SHA256 over the persona + base version + ordered texts."""
        payload = {
            "persona_id": self.persona_id,
            "base_version": self.base_version,
            "exemplar_texts": list(self.exemplar_texts),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Curation (deterministic, passed-cases only, FinMem decay, ≤3)
# ---------------------------------------------------------------------------


def _decay_weight(recency_rank: int, half_life: float) -> float:
    """FinMem-style exponential recency decay (newest = weight 1.0)."""
    return float(0.5 ** (recency_rank / half_life))


def curate_exemplars(
    outcomes: tuple[ReflexionOutcome, ...],
    *,
    persona_id: str,
    max_exemplars: int = MAX_EXEMPLARS,
    half_life: float = 5.0,
) -> tuple[Exemplar, ...]:
    """Curate ≤``max_exemplars`` policy-linted exemplars for one persona.

    Deterministic pipeline:

    1. keep only this persona's records that are BOTH RiskEngine-passed AND
       profitable (passed-cases only);
    2. drop any whose advice fails the AB-006 prompt deny-list (no buy/sell /
       order / injection text may ever be demonstrated as "good");
    3. dedup identical advice texts (keep the most recent = smallest rank);
    4. weight each by FinMem recency decay and take the top ``max_exemplars``,
       tie-broken by (rank asc, text asc) so the result is reproducible.

    Returns an empty tuple when nothing qualifies (a fresh / all-failed history
    is a valid no-op — the persona card keeps its current exemplars).
    """
    cap = min(max_exemplars, MAX_EXEMPLARS)
    by_text: dict[str, ReflexionOutcome] = {}
    for o in outcomes:
        if o.persona_id != persona_id:
            continue
        if not (o.risk_passed and o.profitable):
            continue
        if not lint_prompt_artifact(o.advice_text).passed:
            continue
        prior = by_text.get(o.advice_text)
        if prior is None or o.recency_rank < prior.recency_rank:
            by_text[o.advice_text] = o
    ranked = sorted(
        by_text.values(),
        key=lambda o: (
            -_decay_weight(o.recency_rank, half_life),
            o.recency_rank,
            o.advice_text,
        ),
    )
    return tuple(
        Exemplar(
            persona_id=persona_id,
            text=o.advice_text,
            weight=round(_decay_weight(o.recency_rank, half_life), 6),
        )
        for o in ranked[:cap]
    )


def build_artifact(
    exemplars: tuple[Exemplar, ...], *, persona_id: str, base_version: str
) -> ExemplarArtifact:
    """Bundle curated exemplars into a content-addressed, pinnable artifact.

    Fail-closed on cross-persona contamination: every exemplar must belong to
    ``persona_id`` (codex T-004 P2) — otherwise a demonstration curated for one
    trader could be baked into another trader's card.
    """
    for e in exemplars:
        if e.persona_id != persona_id:
            raise ValueError(
                f"exemplar persona {e.persona_id!r} != artifact persona "
                f"{persona_id!r}; cross-persona demonstrations are forbidden"
            )
    return ExemplarArtifact(
        persona_id=persona_id,
        base_version=base_version,
        exemplar_texts=tuple(e.text for e in exemplars),
    )


# ---------------------------------------------------------------------------
# Proposed persona card version (skeleton frozen, exemplars filled)
# ---------------------------------------------------------------------------


class ProposedPersonaCard(BaseModel):
    """A NEW persona card version proposed for the human pin gate (immutable)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    persona_id: str
    version: str
    content: str  # the rendered YAML
    sha256: str  # the value to pin as a PROMPT_VERSION


def _render_exemplars_block(texts: tuple[str, ...]) -> str:
    """Render the trailing ``exemplars:`` YAML block (JSON-quoted scalars)."""
    if not texts:
        return "exemplars: []\n"
    # JSON strings are valid YAML double-quoted scalars — safe for any content.
    lines = ["exemplars:"]
    lines.extend(f"  - {json.dumps(t, ensure_ascii=False)}" for t in texts)
    return "\n".join(lines) + "\n"


def propose_persona_card_version(
    base_card_content: str,
    artifact: ExemplarArtifact,
    *,
    new_version: str,
) -> ProposedPersonaCard:
    """Render a new persona card version with the curated exemplars filled in.

    Byte-preservation (codex T-004 P2): the frozen identity sections
    (``identity`` / ``mandate`` / ``output_contract`` / ``persona_id`` / any
    ``constraints`` + all governance comments) are carried over VERBATIM from
    ``base_card_content`` via targeted text surgery — only the top-level
    ``version:`` line and the trailing ``exemplars:`` block change — so a human
    reviewing the proposal sees a minimal, auditable diff. The result is a
    PROPOSAL: its SHA256 must be human-approved as a ``PROMPT_VERSION`` in the
    LiveArtifactRegistry (amendment + restart) before any runtime use; this
    function never writes a lockfile or mutates live config.

    Fail-closed ``ValueError`` if: the base card is malformed; its ``persona_id``
    or ``version`` does not match the artifact (the artifact must be curated
    against the exact base it is applied to, for reproducible audit); a skeleton
    key is missing; the surgery cannot find exactly one ``version:`` line / an
    ``exemplars:`` key (``exemplars`` must be the LAST top-level key); or the
    surgical result drifts from "only version + exemplars changed".
    """
    try:
        doc = yaml.safe_load(base_card_content)
    except yaml.YAMLError as exc:
        raise ValueError(f"base persona card does not parse: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError("base persona card root must be a mapping")
    missing = sorted(_REQUIRED_CARD_KEYS - frozenset(doc.keys()))
    if missing:
        raise ValueError(f"base persona card missing skeleton keys {missing}")
    if doc.get("persona_id") != artifact.persona_id:
        raise ValueError(
            f"base card persona_id {doc.get('persona_id')!r} != artifact "
            f"{artifact.persona_id!r}"
        )
    # The artifact must be curated against THIS exact base version, or the
    # pinned hash records one base while the card uses another (codex T-004 P2).
    if str(doc.get("version")) != artifact.base_version:
        raise ValueError(
            f"base card version {doc.get('version')!r} != artifact base_version "
            f"{artifact.base_version!r}; curate against the version you apply to"
        )
    if len(artifact.exemplar_texts) > MAX_EXEMPLARS:
        raise ValueError(f"artifact exceeds the {MAX_EXEMPLARS}-exemplar cap")

    # --- targeted text surgery (preserve everything except version + exemplars)
    new_text, n_ver = re.subn(
        r"(?m)^version:[ \t]*\S+[ \t]*$",
        f"version: {new_version}",
        base_card_content,
        count=1,
    )
    if n_ver != 1:
        raise ValueError(
            "expected exactly one top-level 'version:' line to replace"
        )
    match = re.search(r"(?m)^exemplars:", new_text)
    if match is None:
        raise ValueError("expected a top-level 'exemplars:' key (must be last)")
    content = new_text[: match.start()] + _render_exemplars_block(
        artifact.exemplar_texts
    )

    # Re-validate the surgery: ONLY version + exemplars changed, frozen sections
    # byte-equal, key set unchanged (catches an 'exemplars not last' drop).
    out = yaml.safe_load(content)
    if not isinstance(out, dict) or frozenset(out.keys()) != frozenset(doc.keys()):
        raise ValueError("surgery changed the card's top-level key set")
    if out.get("version") != new_version or out.get("exemplars") != list(
        artifact.exemplar_texts
    ):
        raise ValueError("surgery produced an inconsistent version/exemplars")
    for key in doc:
        if key in ("version", "exemplars"):
            continue
        if out.get(key) != doc.get(key):
            raise ValueError(f"surgery altered the frozen section {key!r}")

    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return ProposedPersonaCard(
        persona_id=artifact.persona_id,
        version=new_version,
        content=content,
        sha256=sha,
    )


def is_promotable(artifact: ExemplarArtifact) -> bool:
    """Defense-in-depth re-check: ≤cap AND every exemplar passes the deny-list.

    The :class:`ExemplarArtifact` validators already enforce both at construction,
    so this is a belt-and-braces gate for a caller that received an artifact from
    an untrusted boundary (it never raises — returns False on any violation).
    """
    if len(artifact.exemplar_texts) > MAX_EXEMPLARS:
        return False
    return all(lint_prompt_artifact(t).passed for t in artifact.exemplar_texts)


__all__ = [
    "EXEMPLAR_TEXT_MAX",
    "MAX_EXEMPLARS",
    "Exemplar",
    "ExemplarArtifact",
    "ProposedPersonaCard",
    "ReflexionOutcome",
    "build_artifact",
    "curate_exemplars",
    "is_promotable",
    "propose_persona_card_version",
]
