"""SQLite + NetworkX KG store — bitemporal, append-only (Q-001).

Design decisions (dossier §6 + P2-2-amendment-2026-05-24):
* SQLite is the durable log (already in the stack, zero new server);
  NetworkX is the in-memory analysis VIEW derived from it. The storage
  interface is a small Protocol so a future engine (LadybugDB) can slot in
  without touching consumers.
* Append-only is enforced PHYSICALLY: the SQLite connection carries an
  authorizer that denies UPDATE/DELETE on the graph tables — a code-path
  that mutates history cannot exist, mirroring broker_events' red line.
* Bitemporality: every write is a new VERSION row stamped with ``t_ingest``
  (store clock, never client-supplied); the current graph = the latest
  version per id; ``as_of`` queries replay any past ingest state. Domain
  time travels separately in ``t_valid``.
* Retirement/promotion (SUPERSEDES) appends — never rewrites — so "why was
  strategy X retired on date D" stays answerable forever.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import networkx as nx
import structlog

from backend.knowledge_graph.schema import (
    EDGE_ENDPOINTS,
    EdgeType,
    KGEdge,
    KGNode,
    NodeStatus,
    NodeType,
)

log = structlog.get_logger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kg_node_versions (
    version_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT NOT NULL,
    node_type   TEXT NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    attrs_json  TEXT NOT NULL,
    provenance_ref TEXT,
    t_valid     TEXT,
    t_ingest    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_id ON kg_node_versions (node_id, version_id);
CREATE TABLE IF NOT EXISTS kg_edge_versions (
    version_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id     TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    src_id      TEXT NOT NULL,
    dst_id      TEXT NOT NULL,
    attrs_json  TEXT NOT NULL,
    provenance_ref TEXT,
    t_valid     TEXT,
    t_ingest    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kg_edges_id ON kg_edge_versions (edge_id, version_id);
"""

_GRAPH_TABLES = ("kg_node_versions", "kg_edge_versions")


class KGSchemaError(ValueError):
    """Raised when an edge's endpoints violate EDGE_ENDPOINTS legality."""


def _deny_mutations(action: int, arg1: str | None, *_rest: object) -> int:
    """SQLite authorizer — physically refuse history rewrites on KG tables.

    WHY an authorizer instead of code discipline: append-only is a red line
    (P2-2 rollback depends on it); a guard at the engine level makes the
    forbidden statement unrunnable rather than merely unreviewed.
    """
    if action in (sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and (
        arg1 in _GRAPH_TABLES
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


class KnowledgeGraphStore(Protocol):
    """Swappable storage contract (future LadybugDB slots in here)."""

    def add_node(self, node: KGNode) -> int: ...

    def add_edge(self, edge: KGEdge) -> int: ...

    def get_node(
        self, node_id: str, *, as_of: datetime | None = None
    ) -> KGNode | None: ...

    def get_edge(
        self, edge_id: str, *, as_of: datetime | None = None
    ) -> KGEdge | None: ...

    def node_history(self, node_id: str) -> tuple[tuple[KGNode, datetime], ...]: ...

    def to_networkx(self, *, as_of: datetime | None = None) -> nx.MultiDiGraph: ...


class SqliteKGStore:
    """The Q-001 production store. One file, no server, append-only."""

    def __init__(self, path: Path | str, *, now: datetime | None = None) -> None:
        self._path = str(path)
        self._fixed_now = now  # deterministic tests inject a clock
        self._conn = sqlite3.connect(self._path)
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        # Authorizer must be installed AFTER schema bootstrap (CREATE needs
        # rights) and stays on for the connection's whole life.
        self._conn.set_authorizer(_deny_mutations)

    # -- time ---------------------------------------------------------------

    def _now(self) -> datetime:
        return self._fixed_now or datetime.now(UTC)

    # -- writes (append-only) ------------------------------------------------

    def add_node(self, node: KGNode) -> int:
        """Append a node VERSION; returns the monotonic version_id.

        Re-adding an existing ``node_id`` is the legal way to change state
        (e.g. retire a strategy): history keeps every prior version.
        """
        t_ingest = self._now().isoformat()
        cur = self._conn.execute(
            "INSERT INTO kg_node_versions "
            "(node_id, node_type, name, status, attrs_json, provenance_ref,"
            " t_valid, t_ingest) VALUES (?,?,?,?,?,?,?,?)",
            (
                node.node_id,
                node.node_type.value,
                node.name,
                node.status.value,
                json.dumps(node.attrs, ensure_ascii=False, sort_keys=True),
                node.provenance_ref,
                node.t_valid.isoformat() if node.t_valid else None,
                t_ingest,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def add_edge(self, edge: KGEdge) -> int:
        """Append an edge VERSION after endpoint-legality + existence checks.

        Both endpoints must already exist as nodes (current view) and their
        types must satisfy EDGE_ENDPOINTS — schema drift fails fast.
        """
        src = self.get_node(edge.src_id)
        dst = self.get_node(edge.dst_id)
        if src is None or dst is None:
            raise KGSchemaError(
                f"edge {edge.edge_id}: endpoint missing "
                f"(src={'ok' if src else edge.src_id}, "
                f"dst={'ok' if dst else edge.dst_id})"
            )
        legal_src, legal_dst = EDGE_ENDPOINTS[edge.edge_type]
        if src.node_type not in legal_src or dst.node_type not in legal_dst:
            raise KGSchemaError(
                f"edge {edge.edge_id}: {edge.edge_type.value} forbids "
                f"{src.node_type.value} -> {dst.node_type.value}"
            )
        t_ingest = self._now().isoformat()
        cur = self._conn.execute(
            "INSERT INTO kg_edge_versions "
            "(edge_id, edge_type, src_id, dst_id, attrs_json, provenance_ref,"
            " t_valid, t_ingest) VALUES (?,?,?,?,?,?,?,?)",
            (
                edge.edge_id,
                edge.edge_type.value,
                edge.src_id,
                edge.dst_id,
                json.dumps(edge.attrs, ensure_ascii=False, sort_keys=True),
                edge.provenance_ref,
                edge.t_valid.isoformat() if edge.t_valid else None,
                t_ingest,
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def supersede_strategy(
        self,
        *,
        old_id: str,
        new_id: str,
        t_valid: datetime,
        provenance_ref: str | None = None,
    ) -> None:
        """Retire ``old_id`` in favour of ``new_id`` — by appending only.

        Appends (1) a SUPERSEDES edge new->old carrying the domain time and
        (2) a NEW version of the old node with status=retired. The old
        node's full history (candidate -> shadow -> active -> retired)
        remains queryable — this is the dossier's promotion/retirement model.
        """
        old = self.get_node(old_id)
        new = self.get_node(new_id)
        if old is None or new is None:
            raise KGSchemaError(
                f"supersede: missing node (old={old_id!r}, new={new_id!r})"
            )
        self.add_edge(
            KGEdge(
                edge_id=f"supersedes:{new_id}->{old_id}",
                edge_type=EdgeType.SUPERSEDES,
                src_id=new_id,
                dst_id=old_id,
                provenance_ref=provenance_ref,
                t_valid=t_valid,
            )
        )
        self.add_node(
            old.model_copy(
                update={"status": NodeStatus.RETIRED, "t_valid": t_valid}
            )
        )

    # -- reads ----------------------------------------------------------------

    def get_node(
        self, node_id: str, *, as_of: datetime | None = None
    ) -> KGNode | None:
        """Latest node version (optionally as of a past ingest time)."""
        row = self._latest_row(
            "kg_node_versions", "node_id", node_id, as_of=as_of
        )
        return _node_from_row(row) if row else None

    def get_edge(
        self, edge_id: str, *, as_of: datetime | None = None
    ) -> KGEdge | None:
        row = self._latest_row(
            "kg_edge_versions", "edge_id", edge_id, as_of=as_of
        )
        return _edge_from_row(row) if row else None

    def node_history(self, node_id: str) -> tuple[tuple[KGNode, datetime], ...]:
        """Every version of a node, oldest first, with its ingest stamp."""
        rows = self._conn.execute(
            "SELECT * FROM kg_node_versions WHERE node_id = ? ORDER BY version_id",
            (node_id,),
        ).fetchall()
        return tuple(
            (_node_from_row(r), datetime.fromisoformat(r[8])) for r in rows
        )

    def to_networkx(self, *, as_of: datetime | None = None) -> nx.MultiDiGraph:
        """Materialise the current (or as-of) graph as a NetworkX view.

        The view is DERIVED and disposable — analysis (centrality, paths)
        runs here; durability stays in SQLite.
        """
        g = nx.MultiDiGraph()
        for node in self._iter_latest_nodes(as_of=as_of):
            # Domain attrs stay NESTED under "attrs": splatting them would let
            # a legal attr named "name"/"status"/"node_type" collide with the
            # reserved keywords and raise TypeError (codex P2).
            g.add_node(
                node.node_id,
                node_type=node.node_type.value,
                name=node.name,
                status=node.status.value,
                attrs=dict(node.attrs),
            )
        for edge in self._iter_latest_edges(as_of=as_of):
            if edge.src_id in g and edge.dst_id in g:
                g.add_edge(
                    edge.src_id,
                    edge.dst_id,
                    key=edge.edge_id,
                    edge_type=edge.edge_type.value,
                    attrs=dict(edge.attrs),
                )
        return g

    # -- internals -------------------------------------------------------------

    def _latest_row(
        self,
        table: str,
        id_col: str,
        id_value: str,
        *,
        as_of: datetime | None,
    ) -> Sequence[object] | None:
        time_clause = " AND t_ingest <= ?" if as_of else ""
        params: tuple[object, ...] = (id_value,)
        if as_of:
            params += (as_of.isoformat(),)
        return self._conn.execute(
            f"SELECT * FROM {table} WHERE {id_col} = ?{time_clause} "  # noqa: S608 — table/col from internal literals
            "ORDER BY version_id DESC LIMIT 1",
            params,
        ).fetchone()

    def _iter_latest_nodes(self, *, as_of: datetime | None) -> Iterator[KGNode]:
        for node_id in self._distinct_ids("kg_node_versions", "node_id"):
            node = self.get_node(node_id, as_of=as_of)
            if node is not None:
                yield node

    def _iter_latest_edges(self, *, as_of: datetime | None) -> Iterator[KGEdge]:
        for edge_id in self._distinct_ids("kg_edge_versions", "edge_id"):
            edge = self.get_edge(edge_id, as_of=as_of)
            if edge is not None:
                yield edge

    def _distinct_ids(self, table: str, id_col: str) -> list[str]:
        rows = self._conn.execute(
            f"SELECT DISTINCT {id_col} FROM {table} ORDER BY {id_col}"  # noqa: S608
        ).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self._conn.close()


def _node_from_row(row: Sequence[object]) -> KGNode:
    return KGNode(
        node_id=str(row[1]),
        node_type=NodeType(str(row[2])),
        name=str(row[3]),
        status=NodeStatus(str(row[4])),
        attrs=json.loads(str(row[5])),
        provenance_ref=row[6] if row[6] is None else str(row[6]),
        t_valid=datetime.fromisoformat(str(row[7])) if row[7] else None,
    )


def _edge_from_row(row: Sequence[object]) -> KGEdge:
    return KGEdge(
        edge_id=str(row[1]),
        edge_type=EdgeType(str(row[2])),
        src_id=str(row[3]),
        dst_id=str(row[4]),
        attrs=json.loads(str(row[5])),
        provenance_ref=row[6] if row[6] is None else str(row[6]),
        t_valid=datetime.fromisoformat(str(row[7])) if row[7] else None,
    )


__all__ = [
    "KGSchemaError",
    "KnowledgeGraphStore",
    "SqliteKGStore",
]
