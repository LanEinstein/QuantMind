"""First-class immutable candidate batch (AE-005 / P2-2-amendment-2026-06-14 §2.3).

The batch is promoted to a **first-class registry object** (amendment §2.3):
an immutable set of candidates sharing one data window, one Sobol seed and one
pre-registered economic mechanism, plus the honest cumulative trial count at
creation. Batch-level statistics (PBO / SPA) attach to the batch without
breaking the per-candidate experiment registration.

Why first-class + immutable: the search's overfit exposure lives at the batch
level (you select the best of N), so the unit the disclosure statistics and the
MinBTL admission gate reason over must be a frozen, content-addressed object —
not a mutable list an operator could prune after seeing the results.

Pure data — no IO, no clock, no LLM.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass

from backend.strategy_evolution.mechanism_registry import EconomicMechanism
from backend.strategy_evolution.quant_param_search import (
    ParamSearchError,
    ParamSet,
)

_ISO_DATE_LEN = 10


@dataclass(frozen=True)
class CandidateBatch:
    """A frozen, content-addressed set of candidates (real + sentinels).

    Immutable by construction: every field is a tuple / frozenset / scalar, so
    there is no mutation surface — an operator cannot drop a losing candidate or
    a passing sentinel after the fact.
    """

    family: str
    seed: int
    declared_n: int
    window_start: str
    window_end: str
    cumulative_n_at_creation: int
    mechanism: EconomicMechanism
    candidates: tuple[ParamSet, ...]
    sentinel_hashes: frozenset[str]

    @property
    def real_candidates(self) -> tuple[ParamSet, ...]:
        return tuple(c for c in self.candidates if not c.is_sentinel)

    @property
    def sentinels(self) -> tuple[ParamSet, ...]:
        return tuple(c for c in self.candidates if c.is_sentinel)

    @property
    def batch_id(self) -> str:
        """Content address over the batch DESIGN (candidate hashes + window)."""
        payload = json.dumps(
            {
                "family": self.family,
                "seed": self.seed,
                "declared_n": self.declared_n,
                "window_start": self.window_start,
                "window_end": self.window_end,
                "cumulative_n_at_creation": self.cumulative_n_at_creation,
                "mechanism": self.mechanism.value,
                "candidate_hashes": sorted(c.param_hash for c in self.candidates),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def assemble_batch(
    *,
    family: str,
    seed: int,
    declared_n: int,
    window_start: str,
    window_end: str,
    cumulative_n_at_creation: int,
    mechanism: EconomicMechanism,
    real_candidates: Sequence[ParamSet],
    sentinels: Sequence[ParamSet],
) -> CandidateBatch:
    """Validate + freeze a candidate batch (fail-closed on a malformed set).

    Rejects: a mismatched family, a real candidate flagged as a sentinel (or
    vice-versa), a sentinel that carries a mechanism, a real candidate whose
    mechanism is not the batch mechanism, and duplicate param hashes (a batch
    must be a *set* — duplicates would double-count the search).
    """
    if len(window_start) != _ISO_DATE_LEN or len(window_end) != _ISO_DATE_LEN:
        raise ParamSearchError("window bounds must be ISO YYYY-MM-DD")
    if declared_n < 1:
        raise ParamSearchError("declared_n must be >= 1")
    if cumulative_n_at_creation < len(real_candidates):
        raise ParamSearchError(
            "cumulative_n_at_creation must include this batch's real candidates"
        )

    for cand in real_candidates:
        if cand.family != family:
            raise ParamSearchError(f"candidate family {cand.family} != {family}")
        if cand.is_sentinel:
            raise ParamSearchError("a real candidate must not be flagged sentinel")
        if cand.mechanism != mechanism:
            raise ParamSearchError(
                f"real candidate mechanism {cand.mechanism} != batch {mechanism}"
            )
    for sent in sentinels:
        if sent.family != family:
            raise ParamSearchError(f"sentinel family {sent.family} != {family}")
        if not sent.is_sentinel:
            raise ParamSearchError("a sentinel must be flagged is_sentinel")
        if sent.mechanism is not None:
            raise ParamSearchError("a sentinel must not declare a mechanism")

    candidates = tuple(real_candidates) + tuple(sentinels)
    hashes = [c.param_hash for c in candidates]
    if len(set(hashes)) != len(hashes):
        raise ParamSearchError("duplicate candidate in batch (must be a set)")

    return CandidateBatch(
        family=family,
        seed=seed,
        declared_n=declared_n,
        window_start=window_start,
        window_end=window_end,
        cumulative_n_at_creation=cumulative_n_at_creation,
        mechanism=mechanism,
        candidates=candidates,
        sentinel_hashes=frozenset(s.param_hash for s in sentinels),
    )


__all__ = [
    "CandidateBatch",
    "assemble_batch",
]
