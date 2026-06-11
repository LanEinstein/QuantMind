"""Seed loader (Q-002) — factors + heuristics into a KnowledgeGraphStore.

Provenance discipline: every seeded Factor/Heuristic node gets
(1) ``provenance_ref`` = ``{source_doc_id}#sha256:{artifact_hash}`` where
the hash pins the exact seed artifact (the rewritten-formula JSON file,
or this package's generated/encoded content), and (2) a DERIVED_FROM
edge to its SourceDoc node — the dossier treats an unsourced node as
low-trust, so the loader makes sourcing structural, not optional.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

import structlog

from backend.knowledge_graph.schema import EdgeType, KGEdge, KGNode, NodeType
from backend.knowledge_graph.seed.heuristics import HEURISTICS
from backend.knowledge_graph.seed.industry_chain import (
    IndustryChainSeedReport,
    seed_industry_chain,
)
from backend.knowledge_graph.seed.qlib_factors import (
    FactorSeed,
    alpha158_factors,
    alpha360_factors,
)
from backend.knowledge_graph.store import SqliteKGStore

log = structlog.get_logger(__name__)

# Anchor seed-artifact defaults to the REPO root, not the caller's cwd —
# scripts/cron invoked from elsewhere would otherwise FileNotFoundError
# (codex P2). loader.py sits at backend/knowledge_graph/seed/.
_REPO_ROOT = Path(__file__).resolve().parents[3]

_WQ101_DOC = "sourcedoc:kakushadze-2016-101-alphas"
_GTJA_DOC = "sourcedoc:gtja-2017-191-alphas"
_QLIB_DOC = "sourcedoc:qlib-alpha-benchmarks"
_PLAYBOOK_DOC = "sourcedoc:trader-playbooks-encoded"

_SOURCE_DOCS: tuple[tuple[str, str, str], ...] = (
    # (doc_id, name, url) — content_sha256 is stamped per artifact below.
    (
        _QLIB_DOC,
        "qlib Alpha158/Alpha360 benchmark configs (MIT)",
        "https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md",
    ),
    (
        _WQ101_DOC,
        "Kakushadze (2016) 101 Formulaic Alphas",
        "https://arxiv.org/abs/1601.00991",
    ),
    (
        _GTJA_DOC,
        "国泰君安金工 (2017) 基于短周期价量特征的多因子选股体系 (191 alphas)",
        "https://www.gtja.com/",
    ),
    (
        _PLAYBOOK_DOC,
        "Encoded trader playbooks "
        "(dual momentum/turtle/CAN SLIM/Minervini/缠论/rotation)",
        "https://github.com/LanEinstein/QuantMind",
    ),
)


class SeedReport(NamedTuple):
    """What one seeding run wrote (counts are nodes, not versions).

    ``industry_chain`` is the Y-001 sub-report (None only if chain seeding
    was skipped); appended last so the field order stays backward-compatible.
    """

    alpha158: int
    alpha360: int
    wq101: int
    gtja191: int
    heuristics: int
    source_docs: int
    industry_chain: IndustryChainSeedReport | None = None

    @property
    def factors(self) -> int:
        return self.alpha158 + self.alpha360 + self.wq101 + self.gtja191

    @property
    def total_source_docs(self) -> int:
        """ALL SourceDoc nodes written, including the chain tier's (else an
        audit relying on ``source_docs`` undercounts provenance — codex P3)."""
        chain = self.industry_chain.source_docs if self.industry_chain else 0
        return self.source_docs + chain


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _records_hash(seeds: tuple[FactorSeed, ...]) -> str:
    """Canonical hash of the FULL seed records (not just formula text).

    A metadata change (name/category/source derivation) must shift the
    provenance anchor too, or two different seed graphs share one hash
    (codex P2) — same discipline for every tier.
    """
    return _sha256_bytes(
        json.dumps([s._asdict() for s in seeds], sort_keys=True).encode("utf-8")
    )


def _load_paper_factors(
    path: Path, *, prefix: str, family: str, source_doc_id: str
) -> tuple[tuple[FactorSeed, ...], str]:
    """Rewritten-formula JSON -> FactorSeeds + the raw artifact's sha256.

    The returned hash pins the SOURCE FILE bytes (BrokerSnapshot-style raw
    anchor, used for the SourceDoc node); the factor-level provenance hash
    is computed over the canonical records by the caller. Fail-fast on
    malformed entries: a seed file is a curated in-repo artifact, so any
    shape problem is a bug to surface, not degrade.
    """
    raw = path.read_bytes()
    artifact_hash = _sha256_bytes(raw)
    entries = json.loads(raw.decode("utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: expected a non-empty JSON array")
    seeds: list[FactorSeed] = []
    for entry in entries:
        ident = entry["id"]
        formula = entry["formula"]
        if not isinstance(ident, int) or not isinstance(formula, str) or not formula:
            raise ValueError(f"{path}: malformed entry {entry!r}")
        seeds.append(
            FactorSeed(
                factor_id=f"factor:{prefix}:{ident:03d}",
                name=f"{family} #{ident}",
                category="price",
                definition=formula,
                source_doc_id=source_doc_id,
            )
        )
    return tuple(seeds), artifact_hash


def _add_factor(
    store: SqliteKGStore, seed: FactorSeed, *, artifact_hash: str
) -> None:
    provenance = f"{seed.source_doc_id}#sha256:{artifact_hash}"
    store.add_node(
        KGNode(
            node_id=seed.factor_id,
            node_type=NodeType.FACTOR,
            name=seed.name,
            attrs={"category": seed.category, "definition": seed.definition},
            provenance_ref=provenance,
        )
    )
    store.add_edge(
        KGEdge(
            edge_id=f"derived:{seed.factor_id}",
            edge_type=EdgeType.DERIVED_FROM,
            src_id=seed.factor_id,
            dst_id=seed.source_doc_id,
            provenance_ref=provenance,
        )
    )


def seed_knowledge_graph(
    store: SqliteKGStore,
    *,
    wq101_path: Path | str = _REPO_ROOT / "data/kg_seed/wq101.json",
    gtja191_path: Path | str = _REPO_ROOT / "data/kg_seed/gtja191.json",
) -> SeedReport:
    """Seed the cold-start corpus; idempotent by node_id (re-runs append
    new versions of identical content, current view unchanged)."""
    a158 = alpha158_factors()
    a360 = alpha360_factors()
    generated_hash = _records_hash((*a158, *a360))
    wq101, wq_file_hash = _load_paper_factors(
        Path(wq101_path), prefix="wq101", family="WorldQuant Alpha",
        source_doc_id=_WQ101_DOC,
    )
    gtja, gtja_file_hash = _load_paper_factors(
        Path(gtja191_path), prefix="gtja191", family="GTJA Alpha",
        source_doc_id=_GTJA_DOC,
    )
    wq_hash = _records_hash(wq101)
    gtja_hash = _records_hash(gtja)
    heur_hash = _sha256_bytes(
        json.dumps(
            [h._asdict() for h in HEURISTICS], sort_keys=True
        ).encode("utf-8")
    )
    # SourceDoc pins the RAW artifact bytes where a file exists (the two
    # rewritten-formula JSONs); factor provenance pins the canonical
    # records derived from it — both anchors shift on any drift.
    doc_hash = {
        _QLIB_DOC: generated_hash,
        _WQ101_DOC: wq_file_hash,
        _GTJA_DOC: gtja_file_hash,
        _PLAYBOOK_DOC: heur_hash,
    }
    for doc_id, name, url in _SOURCE_DOCS:
        store.add_node(
            KGNode(
                node_id=doc_id,
                node_type=NodeType.SOURCE_DOC,
                name=name,
                attrs={"url": url, "content_sha256": doc_hash[doc_id]},
            )
        )
    for seed in a158:
        _add_factor(store, seed, artifact_hash=generated_hash)
    for seed in a360:
        _add_factor(store, seed, artifact_hash=generated_hash)
    for seed in wq101:
        _add_factor(store, seed, artifact_hash=wq_hash)
    for seed in gtja:
        _add_factor(store, seed, artifact_hash=gtja_hash)
    for heur in HEURISTICS:
        provenance = f"{_PLAYBOOK_DOC}#sha256:{heur_hash}"
        store.add_node(
            KGNode(
                node_id=heur.heuristic_id,
                node_type=NodeType.HEURISTIC,
                name=heur.heuristic_id.split(":", 1)[1],
                attrs={
                    "text": heur.text,
                    "attributed_to": heur.attributed_to,
                    "confidence": heur.confidence,
                },
                provenance_ref=provenance,
            )
        )
        store.add_edge(
            KGEdge(
                edge_id=f"derived:{heur.heuristic_id}",
                edge_type=EdgeType.DERIVED_FROM,
                src_id=heur.heuristic_id,
                dst_id=_PLAYBOOK_DOC,
                provenance_ref=provenance,
            )
        )
    # Industry-chain subcapability (Y-001): reconstructed, provenance-anchored
    # the same way; folded into cold-start so the graph is complete in one run.
    chain_report = seed_industry_chain(store)
    report = SeedReport(
        alpha158=len(a158),
        alpha360=len(a360),
        wq101=len(wq101),
        gtja191=len(gtja),
        heuristics=len(HEURISTICS),
        source_docs=len(_SOURCE_DOCS),
        industry_chain=chain_report,
    )
    log.info(
        "kg_seed_complete",
        alpha158=report.alpha158,
        alpha360=report.alpha360,
        wq101=report.wq101,
        gtja191=report.gtja191,
        factors=report.factors,
        heuristics=report.heuristics,
        source_docs=report.total_source_docs,
        chain_nodes=chain_report.chain_nodes,
    )
    return report


__all__ = ["SeedReport", "seed_knowledge_graph"]
