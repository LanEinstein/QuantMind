"""Materialise the cold-start knowledge graph (Q-002) — offline CLI.

Usage:
    python scripts/seed_kg.py [--db data/knowledge_graph/kg.sqlite3]

Idempotent: re-running appends new (identical) versions; the current
view is unchanged. Purely local/offline — zero network, zero LLM.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.knowledge_graph import SqliteKGStore  # noqa: E402
from backend.knowledge_graph.seed import seed_knowledge_graph  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="data/knowledge_graph/kg.sqlite3",
        help="SQLite path for the KG (created if missing)",
    )
    args = parser.parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteKGStore(db_path)
    try:
        report = seed_knowledge_graph(store)
    finally:
        store.close()
    print(
        f"seeded {report.factors} factors "
        f"(alpha158={report.alpha158}, alpha360={report.alpha360}, "
        f"wq101={report.wq101}, gtja191={report.gtja191}) "
        f"+ {report.heuristics} heuristics + {report.source_docs} source docs "
        f"-> {db_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
