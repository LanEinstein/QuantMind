"""Peer-sourcing gate — pinned theme artifact → sourcing candidates (Y-004).

The one place a theme candidate crosses from "investigated + human-pinned" into
the deterministic selection pipeline (P0-8-amendment-2026-06-01 §2.5/§2.6). It is
fail-closed by construction:

* the source run must have been **promotable** (all bytes captured), and
* the artifact's content hash must be **pinned** in the
  :class:`ThemeCandidateRegistry` (human-approved + git + restart).

Either condition failing yields an EMPTY result — so with no fresh pinned artifact
the theme quota is simply empty and the pure-quant path runs unchanged (§2.7, the
human gate never stalls Line-1). The emitted candidates are *sourcing only*: they
still pass the exclusion four-set + affordability + RiskEngine 14-check + builder
single construction + Feishu human gate downstream (the selector never qualifies
them, it only reserves a bounded number of slots — Y-004 selector merge).

Pure module: a frozen dataclass + the two pure types it consumes. No LLM, no IO,
no ``backend.*`` trading-stack imports.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from backend.theme_research.candidate_artifact import ThemeCandidateArtifact
from backend.theme_research.candidate_registry import ThemeCandidateRegistry

log = structlog.get_logger(component="theme_research.peer_sourcing")


@dataclass(frozen=True)
class PeerSourcedCandidate:
    """A theme-sourced candidate cleared the pin gate (sourcing only, no order)."""

    code: str
    sector: str
    chain_link: str
    confidence: float


def verify_pinned_candidates(
    artifact: ThemeCandidateArtifact,
    registry: ThemeCandidateRegistry,
) -> tuple[PeerSourcedCandidate, ...]:
    """Return peer-sourced candidates iff the artifact is promotable AND pinned.

    Fail-closed: a non-promotable source run or an unpinned content hash yields
    ``()`` (empty quota; pure-quant path is unaffected).
    """
    if not artifact.source_promotable:
        log.info(
            "peer_sourcing_refused_non_promotable",
            run_id=artifact.run_id,
            content_sha256=artifact.content_sha256,
        )
        return ()
    if not registry.is_pinned(artifact.content_hash()):
        log.info(
            "peer_sourcing_refused_unpinned",
            run_id=artifact.run_id,
            content_sha256=artifact.content_sha256,
        )
        return ()
    log.info(
        "peer_sourcing_admitted",
        run_id=artifact.run_id,
        content_sha256=artifact.content_sha256,
        candidates=len(artifact.entries),
    )
    return tuple(
        PeerSourcedCandidate(
            code=e.code,
            sector=e.sector,
            chain_link=e.chain_link,
            confidence=e.confidence,
        )
        for e in artifact.entries
    )


__all__ = [
    "PeerSourcedCandidate",
    "verify_pinned_candidates",
]
