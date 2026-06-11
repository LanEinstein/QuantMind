"""Cold-start seed (Q-002) — ~600+ factors + trader heuristics into the KG.

Three factor tiers (coldstart dossier §1/§7, P2-2-amendment-2026-05-24):
* qlib Alpha158 / Alpha360 — structures are public MIT-licensed qlib
  configs; we GENERATE the definitions programmatically (exact, no
  hand-transcription drift).
* GTJA-191 / WorldQuant-101 — formula TEXT rewritten from the public
  papers (Kakushadze 2016 arXiv:1601.00991; GTJA 2017 research report)
  into ``data/kg_seed/*.json``. The unlicensed GitHub repos' CODE is
  never copied (license red line, dossier §2).
* Trader heuristics — dual momentum / turtle / CAN SLIM / Minervini /
  缠论 / sector rotation, encoded as Heuristic nodes.

Every seeded node carries a ``provenance_ref`` anchored to its source
doc + the seed data file's sha256, and a DERIVED_FROM edge to the
corresponding SourceDoc node — an unsourced node is low-trust by
convention (dossier §1.4).

Y-001 adds an industry-chain tier (``industry_chain``): a small but real
semiconductor-localization chain reconstructed from public-domain knowledge
(``liuhuanyong/ChainKnowledgeGraph`` is NOASSERTION — its code/JSON is never
copied), seeded into Trend/Sector/ChainLink/Product/Instrument nodes for the
Phase Y reverse-deduction layer.
"""

from backend.knowledge_graph.seed.industry_chain import (
    IndustryChainSeedReport,
    seed_industry_chain,
)
from backend.knowledge_graph.seed.loader import SeedReport, seed_knowledge_graph

__all__ = [
    "IndustryChainSeedReport",
    "SeedReport",
    "seed_industry_chain",
    "seed_knowledge_graph",
]
