"""Deterministic candidate selector (Phase M-001).

The bright line between *"MiroFish advises"* and *"code decides"*: advisory
evidence (MiroFish, Phase O) may only re-rank **within an already-qualified
set**; the qualified set itself is decided purely by the upstream quant screen
+ affordability gate. This module reads the deterministic quant shortlist +
(optional) advisory evidence and emits the final ordered shortlist the LLM
agents debate (Phase M-003) — pure Python, fixed git-versioned weights, no LLM.

Red lines (``backend/candidate_selector/CLAUDE.md``):

1. **Qualification is purely quant.** Advisory evidence NEVER adds or removes a
   member of the qualified set; it only re-orders within it.
2. **Bounded re-rank.** Advisory may shift a code's position by at most
   ``max_percentile_shift`` of the qualified-set size ("≤1 分位"); it never
   vetoes or silently prunes. A re-rank that would over-displace any code is
   dropped wholesale (fail-closed) and the pure-quant order stands.
3. **≥ ``min_quant_slots`` quant names survive truncation.** After cutting to
   ``final_shortlist_size`` the top quant-ranked codes are still present — a
   bounded re-rank can never indirectly evict the quant favorites.
4. **Advisory absent / degraded → quant fallback.** Removing the advisory may
   only change ORDER, never the qualified set.
5. **Weights/thresholds git-versioned, runtime-immutable** (``feature_def_hash``
   pinned by ``LiveArtifactRegistry`` in Phase R).
6. Pure functions, no IO beyond the one-shot config load; no
   ``import backend.{llm,agents,mirofish}`` (redline-check ``[L-002]`` + the
   module-contract test enforce the closure).

MVP (M-001): MiroFish is not wired yet (Phase O-003). The advisory input is a
generic, already-typed :class:`AdvisorySignal` so the bounded-rerank machinery
and its invariants are real and testable now; O-003 only has to map MiroFish
sector-scores into that signal and add sector-veto adversarial tests.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger(component="candidate_selector")


class CandidateSelectorError(ValueError):
    """Raised on an invalid selector config or a malformed candidate set."""


@dataclass(frozen=True)
class QuantCandidate:
    """One quant-qualified candidate: a 6-digit code + its composite score.

    ``score`` is the screener's cross-sectional composite (higher = more
    attractive). The selector treats the membership of the candidate list as
    authoritative quant qualification — it never re-qualifies, only ranks.
    """

    code: str
    score: float


@dataclass(frozen=True)
class AdvisorySignal:
    """A MiroFish-style advisory for one code (bounded re-rank input only).

    ``advisory_score`` > 0 nudges the code UP the ranking, < 0 nudges it down;
    the magnitude is interpreted in units of the max bounded shift and is then
    hard-clamped, so it can never widen the ≤1-percentile bound. A code with no
    signal is treated as 0 (neutral). MVP: no signals arrive (Phase O-003).
    """

    code: str
    advisory_score: float


@dataclass(frozen=True)
class SelectorConfig:
    """Locked, git-versioned selector parameters + their content hash."""

    version: str
    final_shortlist_size: int
    min_quant_slots: int
    max_percentile_shift: float
    advisory_weight: float
    feature_def_hash: str


@dataclass(frozen=True)
class CandidateSelection:
    """Deterministic selection result.

    ``qualified`` is the purely-quant qualified set (ranked, membership never
    touched by advisory). ``shortlist`` is the final ≤ ``final_shortlist_size``
    ordered set for the debate. ``quant_reserved`` are the guaranteed top quant
    names. ``advisory_applied`` is True only when a bounded re-rank actually ran.
    """

    shortlist: tuple[str, ...]
    qualified: tuple[str, ...]
    quant_reserved: tuple[str, ...]
    advisory_applied: bool
    config_version: str
    feature_def_hash: str = ""
    peer_sourced: tuple[str, ...] = ()
    """Theme peer-sourced codes admitted into the shortlist (Y-004). Bounded to
    ``final_shortlist_size − min_quant_slots`` (≤2); never evicts a reserved quant
    name; empty when no pinned theme artifact (pure-quant path unchanged)."""


class CandidateSelector:
    """Pure, deterministic selector. No IO, no LLM."""

    def __init__(self, config: SelectorConfig) -> None:
        self._config = config

    @property
    def config(self) -> SelectorConfig:
        return self._config

    def select(
        self,
        quant_candidates: Sequence[QuantCandidate],
        advisory: Sequence[AdvisorySignal] | None = None,
        peer_sourced: Sequence[str] | None = None,
    ) -> CandidateSelection:
        """Select the final debate shortlist from quant candidates + advisory.

        Deterministic: the same candidates + advisory + peer_sourced + config
        always yield the same shortlist. ``advisory`` and ``peer_sourced`` default
        to absent — and with ``peer_sourced=None`` the result is bit-identical to
        the pure-quant path (Y-004 peer-sourcing is purely additive).

        ``peer_sourced`` (Y-004) is the already-pin-verified theme codes (the
        caller verified promotability + the LiveArtifactRegistry/ThemeCandidate
        pin before passing them — the selector stays pure). They reserve at most
        ``final_shortlist_size − min_quant_slots`` (≤2) slots and NEVER evict the
        top ``min_quant_slots`` quant names; codes already quant-qualified are not
        double-counted.

        Raises:
            CandidateSelectorError: duplicate candidate codes (structurally
                ambiguous — fail closed rather than pick an arbitrary copy).
        """
        ranked = self._rank_quant(quant_candidates)
        qualified = tuple(c.code for c in ranked)
        peer_new = self._dedup_peer(peer_sourced, set(qualified))
        if not ranked and not peer_new:
            return CandidateSelection(
                shortlist=(),
                qualified=(),
                quant_reserved=(),
                advisory_applied=False,
                config_version=self._config.version,
                feature_def_hash=self._config.feature_def_hash,
            )

        # No quant to re-rank on the theme-only path → advisory did not apply
        # (the flag must not claim a re-rank that never had quant to act on).
        ordered, applied = (
            self._apply_bounded_rerank(ranked, advisory) if ranked else ([], False)
        )
        # Theme reserves at most (final − min_quant); quant always keeps the top
        # min_quant_slots names (red line 3). Theme fills only the slots beyond
        # the reduced quant cap, so it can never evict a reserved quant favorite.
        theme_quota = max(
            0, self._config.final_shortlist_size - self._config.min_quant_slots
        )
        theme_taken = tuple(peer_new[:theme_quota])
        quant_cap = self._config.final_shortlist_size - len(theme_taken)
        quant_shortlist, reserved = self._truncate_reserving_quant(
            ordered, ranked, quant_cap
        )
        shortlist = quant_shortlist + theme_taken

        log.info(
            "candidates_selected",
            qualified=len(qualified),
            shortlist=len(shortlist),
            quant_reserved=len(reserved),
            peer_sourced=len(theme_taken),
            advisory_applied=applied,
            config_version=self._config.version,
        )
        return CandidateSelection(
            shortlist=shortlist,
            qualified=qualified,
            quant_reserved=reserved,
            advisory_applied=applied,
            config_version=self._config.version,
            feature_def_hash=self._config.feature_def_hash,
            peer_sourced=theme_taken,
        )

    @staticmethod
    def _dedup_peer(
        peer_sourced: Sequence[str] | None, qualified: set[str]
    ) -> list[str]:
        """Peer codes not already quant-qualified, de-duplicated, order-preserved.

        A theme code that is already a quant name is counted as quant (not theme)
        so the bounded theme quota is never inflated by overlap.
        """
        seen: set[str] = set()
        out: list[str] = []
        for code in peer_sourced or ():
            if code in qualified or code in seen:
                continue
            seen.add(code)
            out.append(code)
        return out

    # -- internals -------------------------------------------------------

    @staticmethod
    def _rank_quant(
        quant_candidates: Sequence[QuantCandidate],
    ) -> list[QuantCandidate]:
        """Deterministic quant order: score desc, code asc tie-break.

        Rejects duplicate / non-finite-score candidates fail-closed so an
        ambiguous or corrupt input can never produce an arbitrary ranking.
        """
        seen: set[str] = set()
        for c in quant_candidates:
            if c.code in seen:
                raise CandidateSelectorError(
                    f"duplicate candidate code {c.code!r} — ambiguous input"
                )
            seen.add(c.code)
            if not math.isfinite(c.score):
                raise CandidateSelectorError(
                    f"candidate {c.code!r} has non-finite score {c.score!r}"
                )
        return sorted(quant_candidates, key=lambda c: (-c.score, c.code))

    def _apply_bounded_rerank(
        self,
        ranked: list[QuantCandidate],
        advisory: Sequence[AdvisorySignal] | None,
    ) -> tuple[list[QuantCandidate], bool]:
        """Re-rank within the bound; fall back to pure quant when absent/over.

        Returns ``(order, advisory_applied)``. The advisory is applied only if
        every code's displacement stays within ``max_shift`` positions; a single
        over-displacement drops the whole re-rank (fail-closed — MiroFish never
        gets an unbounded pull). ``advisory_applied`` is False in the fallback.
        """
        signals = {
            s.code: s.advisory_score
            for s in (advisory or ())
            if math.isfinite(s.advisory_score) and s.advisory_score != 0.0
        }
        if not signals:
            return ranked, False

        n = len(ranked)
        max_shift = max(1, round(n * self._config.max_percentile_shift))

        def delta(code: str) -> float:
            # Clamp the pull to ±max_shift positions so the sort key can only
            # nudge a code within the bound.
            raw = self._config.advisory_weight * signals.get(code, 0.0)
            return max(-float(max_shift), min(float(max_shift), raw))

        deltas = {c.code: delta(c.code) for c in ranked}
        # Sort by (quant_pos - pull); break ties toward the LARGER pull so a
        # one-slot bullish signal actually realizes its allowed single-position
        # move instead of being pinned by the quant-idx tie-break (codex M-001
        # P2). The final quant-idx tie-break keeps the order deterministic.
        keyed = sorted(
            (
                (idx - deltas[c.code], -deltas[c.code], idx, c)
                for idx, c in enumerate(ranked)
            ),
            key=lambda t: (t[0], t[1], t[2]),
        )
        reordered = [t[-1] for t in keyed]

        # Verify the realized displacement is within bound; clustering can
        # amplify beyond the per-element clamp, so enforce it explicitly and
        # drop the advisory wholesale if violated (red line 2, fail-closed).
        quant_pos = {c.code: idx for idx, c in enumerate(ranked)}
        for new_idx, c in enumerate(reordered):
            if abs(new_idx - quant_pos[c.code]) > max_shift:
                log.warning(
                    "advisory_rerank_over_displaced",
                    code=c.code,
                    quant_pos=quant_pos[c.code],
                    new_pos=new_idx,
                    max_shift=max_shift,
                )
                return ranked, False
        return reordered, True

    def _truncate_reserving_quant(
        self,
        ordered: list[QuantCandidate],
        ranked: list[QuantCandidate],
        cap: int | None = None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Cut to ``cap`` (default ``final_shortlist_size``) keeping the top quant.

        The top ``min_quant_slots`` quant-ranked codes (or all, if fewer) are
        guaranteed present; if truncation would drop one, it evicts the
        lowest-priority non-reserved code from the tail to make room. The final
        set is then emitted in ``ordered`` (post-rerank) order for determinism.
        ``cap`` is reduced by the theme quota in :meth:`select` so peer-sourced
        names fill only the slots beyond the reserved quant (Y-004).
        """
        final_n = self._config.final_shortlist_size if cap is None else cap
        # Cap the reserve by ``final_n`` too (defense-in-depth, review finding):
        # the production loader enforces min_quant_slots ≤ final_shortlist_size, so
        # reserve_n ≤ cap normally — but a directly-constructed config with
        # min_quant_slots > cap could otherwise make the eviction loop append a
        # reserved code with nothing to pop, growing the result past ``cap``.
        reserve_n = min(self._config.min_quant_slots, len(ranked), final_n)
        reserved = [c.code for c in ranked[:reserve_n]]

        chosen = [c.code for c in ordered[:final_n]]
        for code in reversed(reserved):
            if code in chosen:
                continue
            # Evict the lowest-priority non-reserved code from the tail.
            for i in range(len(chosen) - 1, -1, -1):
                if chosen[i] not in reserved:
                    chosen.pop(i)
                    break
            chosen.append(code)

        chosen_set = set(chosen)
        final_order = tuple(c.code for c in ordered if c.code in chosen_set)
        return final_order, tuple(reserved)


def load_selector_config(yaml_path: str | Path) -> SelectorConfig:
    """Load + validate a git-versioned selector config (runtime-immutable).

    Raises:
        FileNotFoundError: ``yaml_path`` does not exist.
        CandidateSelectorError: any parameter invariant is violated.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"candidate-weights config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise CandidateSelectorError("candidate_weights missing non-empty 'version'")

    final_n = _require_positive_int(raw, "final_shortlist_size")
    min_quant = _require_positive_int(raw, "min_quant_slots")
    if min_quant > final_n:
        raise CandidateSelectorError(
            f"min_quant_slots ({min_quant}) must be ≤ "
            f"final_shortlist_size ({final_n})"
        )

    shift = raw.get("max_percentile_shift")
    # bool is an int subclass — reject it so true/false can't pose as a ratio.
    if (
        not isinstance(shift, int | float)
        or isinstance(shift, bool)
        or not (0.0 < float(shift) <= 1.0)
    ):
        raise CandidateSelectorError(
            f"max_percentile_shift must be in (0, 1], got {shift!r}"
        )
    weight = raw.get("advisory_weight")
    # Reject non-finite (.nan/.inf) and bool — a non-finite weight would
    # silently produce non-finite re-rank deltas instead of failing closed as
    # the loader contract promises (codex M-001 P2).
    if (
        not isinstance(weight, int | float)
        or isinstance(weight, bool)
        or not math.isfinite(float(weight))
        or float(weight) < 0.0
    ):
        raise CandidateSelectorError(
            f"advisory_weight must be a finite non-negative number, got {weight!r}"
        )

    feature_def_hash = _hash_config(
        version=version,
        final_shortlist_size=final_n,
        min_quant_slots=min_quant,
        max_percentile_shift=float(shift),
        advisory_weight=float(weight),
    )
    config = SelectorConfig(
        version=version,
        final_shortlist_size=final_n,
        min_quant_slots=min_quant,
        max_percentile_shift=float(shift),
        advisory_weight=float(weight),
        feature_def_hash=feature_def_hash,
    )
    log.info(
        "selector_config_loaded",
        path=str(path),
        version=version,
        final_shortlist_size=final_n,
        min_quant_slots=min_quant,
        max_percentile_shift=float(shift),
        advisory_weight=float(weight),
        feature_def_hash=feature_def_hash,
    )
    return config


def _hash_config(**fields: Any) -> str:
    """Stable sha256 of the effective config (LiveArtifactRegistry pin)."""
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _require_positive_int(block: dict[str, Any], key: str) -> int:
    value = block.get(key)
    # bool is an int subclass — reject it so `true`/`false` can't pose as a count.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise CandidateSelectorError(
            f"candidate_weights.{key} must be a positive int, got {value!r}"
        )
    return value


__all__ = [
    "AdvisorySignal",
    "CandidateSelection",
    "CandidateSelector",
    "CandidateSelectorError",
    "QuantCandidate",
    "SelectorConfig",
    "load_selector_config",
]
