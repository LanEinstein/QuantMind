"""KG ingest pipeline (Q-003) — extract, second-pass verify, HUMAN gate.

Pipeline (dossier §2, FinReflectKG-style):
    document --extractor--> proposed triples --verifier (2nd agent)-->
    PENDING ledger --human approval--> graph write

Red lines honoured by construction:
* LLM never writes the graph: the extractor/verifier are INJECTED
  Protocols (wired with real LLM clients only at the orchestration
  layer, mirroring W-002); this module never imports backend.llm. What
  they produce lands in an append-only PENDING ledger — nothing reaches
  the store until a named human approves it (P2-2 人工 gate).
* Provenance fail-closed: a document may only be ingested if it is
  already anchored in ``data/rag/provenance.jsonl`` (the X-002/X-004
  RAG ledger is REUSED read-only as the single source of truth); every
  applied node/edge carries ``{doc_id}#sha256:{content_sha256}``.
* Append-only: proposals and decisions are JSONL appends; a decision is
  recorded even when it rejects.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, field_validator

from backend.knowledge_graph.schema import (
    EDGE_ENDPOINTS,
    EdgeType,
    KGEdge,
    KGNode,
    NodeType,
)
from backend.knowledge_graph.store import SqliteKGStore

log = structlog.get_logger(__name__)


class KGIngestError(ValueError):
    """Raised on provenance/gate violations (fail-closed)."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ProposedNode(_Frozen):
    """A node the extractor wants to exist (created only on approval)."""

    node_id: str
    node_type: NodeType
    name: str
    attrs: dict[str, str | int | float | bool | None] = {}


class ProposedTriple(_Frozen):
    """One extracted (subject, predicate, object) + the LLM's rationale.

    ``rationale`` is the only LLM-authored field and is descriptive text
    (P0-10 positive-list compatible); it never becomes a decision field.
    """

    src: ProposedNode
    edge_type: EdgeType
    dst: ProposedNode
    rationale: str
    doc_id: str
    content_sha256: str

    @field_validator("doc_id", "content_sha256")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-empty")
        return v

    @property
    def proposal_id(self) -> str:
        """Deterministic id over the canonical triple + doc anchor."""
        canon = json.dumps(
            {
                "src": self.src.model_dump(mode="json"),
                "edge_type": self.edge_type.value,
                "dst": self.dst.model_dump(mode="json"),
                "doc_id": self.doc_id,
                "content_sha256": self.content_sha256,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:24]


class TripleExtractor(Protocol):
    """First-pass entity/relation extraction (LLM-backed, injected)."""

    def extract(self, doc_text: str, doc_id: str) -> tuple[ProposedTriple, ...]: ...


class TripleVerifier(Protocol):
    """Second-agent triple verification (FinReflectKG-style, injected)."""

    def verify(self, triple: ProposedTriple, doc_text: str) -> bool: ...


class JsonlProvenanceIndex:
    """Read-only view of ``data/rag/provenance.jsonl`` (X-002 ledger reuse).

    Fail-closed: an unreadable/absent ledger anchors NOTHING.
    """

    def __init__(self, path: Path | str = Path("data/rag/provenance.jsonl")) -> None:
        self._path = Path(path)

    def is_anchored(self, doc_id: str, content_sha256: str) -> bool:
        """LATEST-wins: only the doc's newest ledger row anchors it.

        An older row must not keep superseded/re-ingested content usable,
        and a row carrying a rejection must anchor nothing (codex P1 —
        mirrors the append-only ledger's latest-wins + rejection_reason
        semantics).
        """
        if not self._path.exists():
            return False
        latest: dict[str, object] | None = None
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("doc_id") == doc_id:
                    latest = entry
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("provenance_index_unreadable", error=str(exc))
            return False
        if latest is None:
            return False
        # Rejection means the FIELD IS PRESENT — an explicit empty-string
        # reason still rejects (matches the ledger model; codex verify).
        if latest.get("rejection_reason") is not None:
            return False
        return latest.get("content_sha256") == content_sha256


class KGIngestPipeline:
    """extract -> verify -> pending ledger -> (human) apply/reject."""

    def __init__(
        self,
        *,
        extractor: TripleExtractor,
        verifier: TripleVerifier,
        provenance: JsonlProvenanceIndex,
        ledger_dir: Path | str = Path("data/kg_ingest"),
        now: datetime | None = None,
    ) -> None:
        self._extractor = extractor
        self._verifier = verifier
        self._provenance = provenance
        self._dir = Path(ledger_dir)
        self._fixed_now = now

    # -- paths / time ----------------------------------------------------------

    @property
    def pending_path(self) -> Path:
        return self._dir / "pending.jsonl"

    @property
    def decisions_path(self) -> Path:
        return self._dir / "decisions.jsonl"

    def _now_iso(self) -> str:
        return (self._fixed_now or datetime.now(UTC)).isoformat()

    def _append(self, path: Path, record: dict[str, object]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    # -- stage 1+2: extract + second-pass verify -> PENDING ----------------------

    def propose(
        self, *, doc_text: str, doc_id: str, content_sha256: str
    ) -> tuple[ProposedTriple, ...]:
        """Run extraction + verification; persist survivors as PENDING.

        Never touches the graph. Fail-closed on provenance: a doc absent
        from the RAG ledger cannot even be proposed.
        """
        if not self._provenance.is_anchored(doc_id, content_sha256):
            raise KGIngestError(
                f"doc {doc_id!r} not anchored in provenance ledger — refuse"
            )
        # The text the LLM actually reads must BE the anchored bytes — an
        # edited payload or stale caller text would otherwise mint graph
        # facts that merely look hash-anchored (codex P1).
        actual = hashlib.sha256(doc_text.encode("utf-8")).hexdigest()
        if actual != content_sha256:
            raise KGIngestError(
                f"doc {doc_id!r}: text hashes to {actual[:12]}…, "
                f"ledger anchors {content_sha256[:12]}… — refuse"
            )
        proposed = self._extractor.extract(doc_text, doc_id)
        accepted: list[ProposedTriple] = []
        for triple in proposed:
            if triple.doc_id != doc_id or triple.content_sha256 != content_sha256:
                raise KGIngestError(
                    f"triple {triple.proposal_id} anchors a different doc"
                )
            if not self._verifier.verify(triple, doc_text):
                log.info("triple_rejected_by_verifier", pid=triple.proposal_id)
                continue
            accepted.append(triple)
            self._append(
                self.pending_path,
                {
                    "proposal_id": triple.proposal_id,
                    "triple": triple.model_dump(mode="json"),
                    "proposed_at": self._now_iso(),
                },
            )
        log.info(
            "kg_ingest_proposed",
            doc_id=doc_id, extracted=len(proposed), pending=len(accepted),
        )
        return tuple(accepted)

    # -- stage 3: HUMAN gate ------------------------------------------------------

    def _pending(self) -> dict[str, ProposedTriple]:
        out: dict[str, ProposedTriple] = {}
        if not self.pending_path.exists():
            return out
        for line in self.pending_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                # Lenient (non-strict) validation ONLY for rehydrating our own
                # ledger rows — JSON carries enum values as plain strings.
                out[rec["proposal_id"]] = ProposedTriple.model_validate(
                    rec["triple"], strict=False
                )
        return out

    def _decided(self) -> set[str]:
        if not self.decisions_path.exists():
            return set()
        return {
            json.loads(line)["proposal_id"]
            for line in self.decisions_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    def decide(
        self,
        store: SqliteKGStore,
        proposal_id: str,
        *,
        decision: Literal["approve", "reject"],
        decided_by: str,
    ) -> bool:
        """Apply (or reject) ONE pending proposal — the human gate.

        ``decided_by`` must name the human; the decision is appended to
        the decisions ledger either way. Only "approve" writes the graph.
        Returns True when the graph was written.
        """
        if not decided_by.strip():
            raise KGIngestError("decided_by must name the human approver")
        pending = self._pending()
        triple = pending.get(proposal_id)
        if triple is None:
            raise KGIngestError(f"unknown proposal {proposal_id!r}")
        if proposal_id in self._decided():
            raise KGIngestError(f"proposal {proposal_id!r} already decided")
        decision_record = {
            "proposal_id": proposal_id,
            "decision": decision,
            "decided_by": decided_by,
            "decided_at": self._now_iso(),
        }
        if decision != "approve":
            self._append(self.decisions_path, decision_record)
            return False
        # Prevalidate edge endpoint legality BEFORE any write: an
        # LLM-proposed triple with illegal endpoint types must fail the
        # approval cleanly (retryable, no orphan nodes) instead of being
        # recorded as decided with nothing applied (codex P2).
        legal_src, legal_dst = EDGE_ENDPOINTS[triple.edge_type]
        if (
            triple.src.node_type not in legal_src
            or triple.dst.node_type not in legal_dst
        ):
            raise KGIngestError(
                f"proposal {proposal_id!r}: {triple.edge_type.value} forbids "
                f"{triple.src.node_type.value} -> {triple.dst.node_type.value}"
            )
        provenance = f"{triple.doc_id}#sha256:{triple.content_sha256}"
        for node in (triple.src, triple.dst):
            if store.get_node(node.node_id) is None:
                store.add_node(
                    KGNode(
                        node_id=node.node_id,
                        node_type=node.node_type,
                        name=node.name,
                        attrs=dict(node.attrs),
                        provenance_ref=provenance,
                    )
                )
        store.add_edge(
            KGEdge(
                edge_id=f"ingest:{proposal_id}",
                edge_type=triple.edge_type,
                src_id=triple.src.node_id,
                dst_id=triple.dst.node_id,
                attrs={"rationale": triple.rationale},
                provenance_ref=provenance,
            )
        )
        # The decision is recorded AFTER the graph write succeeded — a
        # failed write leaves the proposal undecided and retryable.
        self._append(self.decisions_path, decision_record)
        log.info("kg_ingest_applied", pid=proposal_id, by=decided_by)
        return True


__all__ = [
    "JsonlProvenanceIndex",
    "KGIngestError",
    "KGIngestPipeline",
    "ProposedNode",
    "ProposedTriple",
    "TripleExtractor",
    "TripleVerifier",
]
