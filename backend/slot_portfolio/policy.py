"""Deterministic rotation policy (Phase V-002).

Combines the two scoring halves (:mod:`backend.slot_portfolio.scoring`) into a
single deterministic rotation *proposal*: when the portfolio is full and a
challenger beats the weakest **independently-weak** incumbent by an absolute
margin, propose rotating that incumbent out. This module only **proposes** —
it never constructs an :class:`InstructionPlan` (R0 §4 single construction
point) and never touches IO beyond the one-shot config load.

The dual condition (P0-7-amendment-2026-06-01 §1.3):

* the incumbent to sell must be **independently weak** (all 7 conditions) — a
  healthy holding is never sold to chase a challenger;
* the challenger must beat that incumbent **by an absolute margin** (qualified,
  >= P75, rank lead >= 25 pct, and an absolute composite-score margin).

Churn control + the append-only ``RotationIntent`` lifecycle + the expiry
fallback live in :mod:`backend.slot_portfolio.rotation_intent` (V-003); this
module is the pure "which incumbent, which challenger" core.
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

from backend.slot_portfolio.scoring import (
    ChallengerMargin,
    ChallengerMarginConfig,
    ChallengerState,
    IncumbentState,
    IncumbentWeakConfig,
    IncumbentWeakness,
    SlotPortfolioError,
    evaluate_challenger_margin,
    evaluate_incumbent_weakness,
)

log = structlog.get_logger(component="slot_portfolio")


@dataclass(frozen=True)
class ChurnConfig:
    """Anti-churn gates (consumed by ``rotation_intent.apply_churn_gates``, V-003).

    All trading-day counts; the cooldown comparison itself lives in V-003 (it
    needs a trading-calendar distance the orchestration injects), but the
    thresholds are locked here so the whole rotation policy is one git artifact.
    """

    max_rotations_per_day: int       # <= 1 rotation SELL initiated per day
    max_open_intents: int            # <= 1 unresolved RotationIntent at a time
    rotation_subcap: int             # rotation occupies <= this of the ≤5/day cap
    same_incumbent_cooldown_td: int  # don't re-rotate this incumbent within N td
    same_pair_cooldown_td: int       # don't repeat this (challenger,incumbent) N td


@dataclass(frozen=True)
class ExpiryConfig:
    """RotationIntent expiry (consumed by ``rotation_intent`` expiry logic, V-003).

    ``expires_at = min(max_trading_days ahead, next rebalance close)`` — the
    trading-day arithmetic is done by the orchestration (calendar IO); this
    locks the max horizon.
    """

    max_trading_days: int


@dataclass(frozen=True)
class RotationPolicyConfig:
    """Locked, git-versioned rotation thresholds + their content hash."""

    version: str
    incumbent_weak: IncumbentWeakConfig
    challenger_margin: ChallengerMarginConfig
    churn: ChurnConfig
    expiry: ExpiryConfig
    config_hash: str


@dataclass(frozen=True)
class RotationProposal:
    """The deterministic rotation decision for one Line-1 run.

    ``should_rotate`` is True only when a weak incumbent + a margin-winning
    challenger are both found. The incumbent/challenger codes + their scores +
    the full per-condition breakdowns are carried so the decision is auditable
    and the downstream ``RotationIntent`` (V-003) can persist the replay inputs.
    """

    should_rotate: bool
    incumbent_code: str | None
    challenger_code: str | None
    incumbent_score: float | None
    challenger_score: float | None
    incumbent_percentile: float | None
    challenger_percentile: float | None
    reason: str
    weak_incumbents: tuple[str, ...]
    weakness: IncumbentWeakness | None = None
    margin: ChallengerMargin | None = None


def _dedup_or_raise(codes: Sequence[str], *, kind: str) -> None:
    """Fail-closed on a duplicate code (ambiguous input → no arbitrary pick)."""
    seen: set[str] = set()
    for code in codes:
        if code in seen:
            raise SlotPortfolioError(
                f"duplicate {kind} code {code!r} — ambiguous rotation input"
            )
        seen.add(code)


def propose_rotation(
    incumbents: Sequence[IncumbentState],
    challengers: Sequence[ChallengerState],
    config: RotationPolicyConfig,
) -> RotationProposal:
    """Propose at most one rotation (sell weakest weak incumbent, buy challenger).

    Deterministic: identical inputs + config always yield the identical
    proposal, so the decision replays bit-exact off the same pinned frame.

    The weakest **independently-weak** incumbent (lowest composite score, code
    asc tie-break) is paired with the strongest **qualified** challenger
    (highest composite score, code asc tie-break). A rotation is proposed only
    when that challenger beats that incumbent by an absolute margin. A challenger
    whose code is already held is dropped (it cannot be a rotation target).

    Raises:
        SlotPortfolioError: duplicate incumbent or challenger codes.
    """
    _dedup_or_raise([i.code for i in incumbents], kind="incumbent")
    _dedup_or_raise([c.code for c in challengers], kind="challenger")

    weaknesses = {
        i.code: evaluate_incumbent_weakness(i, config.incumbent_weak)
        for i in incumbents
    }
    weak_states = [i for i in incumbents if weaknesses[i.code].independently_weak]
    weak_codes = tuple(sorted(w.code for w in weak_states))

    if not weak_states:
        return RotationProposal(
            should_rotate=False,
            incumbent_code=None, challenger_code=None,
            incumbent_score=None, challenger_score=None,
            incumbent_percentile=None, challenger_percentile=None,
            reason="no incumbent is independently weak — protect all holdings",
            weak_incumbents=weak_codes,
        )

    # Weakest weak incumbent: lowest composite score, code asc for determinism.
    weakest = min(weak_states, key=lambda i: (i.composite_score, i.code))

    held = {i.code for i in incumbents}
    # A challenger already held cannot free a slot — it is not a rotation target.
    eligible = [
        c for c in challengers if c.qualified and c.code not in held
    ]
    if not eligible:
        return RotationProposal(
            should_rotate=False,
            incumbent_code=weakest.code, challenger_code=None,
            incumbent_score=weakest.composite_score, challenger_score=None,
            incumbent_percentile=weakest.line1_percentile,
            challenger_percentile=None,
            reason="weak incumbent present but no qualified, not-held challenger",
            weak_incumbents=weak_codes,
            weakness=weaknesses[weakest.code],
        )

    # Strongest qualified challenger: highest composite, code asc tie-break.
    best = max(eligible, key=lambda c: (c.composite_score, _neg_code(c.code)))
    margin = evaluate_challenger_margin(best, weakest, config.challenger_margin)

    if not margin.wins_by_margin:
        return RotationProposal(
            should_rotate=False,
            incumbent_code=weakest.code, challenger_code=best.code,
            incumbent_score=weakest.composite_score,
            challenger_score=best.composite_score,
            incumbent_percentile=weakest.line1_percentile,
            challenger_percentile=best.line1_percentile,
            reason="best challenger does not beat the weak incumbent by margin",
            weak_incumbents=weak_codes,
            weakness=weaknesses[weakest.code],
            margin=margin,
        )

    log.info(
        "rotation_proposed",
        incumbent=weakest.code, challenger=best.code,
        incumbent_score=weakest.composite_score,
        challenger_score=best.composite_score,
        config_version=config.version,
    )
    return RotationProposal(
        should_rotate=True,
        incumbent_code=weakest.code, challenger_code=best.code,
        incumbent_score=weakest.composite_score,
        challenger_score=best.composite_score,
        incumbent_percentile=weakest.line1_percentile,
        challenger_percentile=best.line1_percentile,
        reason="weak incumbent + challenger wins by absolute margin",
        weak_incumbents=weak_codes,
        weakness=weaknesses[weakest.code],
        margin=margin,
    )


def _neg_code(code: str) -> tuple[int, ...]:
    """Key that orders codes ascending under a ``max(...)`` selection.

    ``max`` picks the largest key; to break a composite-score tie toward the
    smallest code we invert each character's ordinal so "002138" sorts before
    "600510" under ``max`` (mirrors the screener's score-desc / code-asc rule).
    """
    return tuple(-ord(ch) for ch in code)


# ---------------------------------------------------------------------------
# Config loading (runtime-immutable, git-versioned)
# ---------------------------------------------------------------------------


def load_rotation_policy_config(yaml_path: str | Path) -> RotationPolicyConfig:
    """Load + validate the rotation policy config (runtime-immutable).

    Raises:
        FileNotFoundError: ``yaml_path`` does not exist.
        SlotPortfolioError: any parameter invariant is violated.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"slot-rotation policy config not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise SlotPortfolioError("slot_rotation_policy missing non-empty 'version'")

    weak_raw = _require_block(raw, "incumbent_weak")
    conf_raw = _require_block(weak_raw, "confirmation")
    incumbent_weak = IncumbentWeakConfig(
        min_holding_age_trading_days=_require_positive_int(
            weak_raw, "min_holding_age_trading_days"
        ),
        max_line1_percentile=_require_pct(weak_raw, "max_line1_percentile"),
        min_rank_deterioration_pct=_require_pct(
            weak_raw, "min_rank_deterioration_pct"
        ),
        score_below_median_mad_mult=_require_finite_nonneg(
            conf_raw, "score_below_median_mad_mult"
        ),
        drawdown_soft_threshold=_require_pct(conf_raw, "drawdown_soft_threshold"),
    )

    margin_raw = _require_block(raw, "challenger_margin")
    challenger_margin = ChallengerMarginConfig(
        min_percentile=_require_pct(margin_raw, "min_percentile"),
        min_rank_lead_pct=_require_pct(margin_raw, "min_rank_lead_pct"),
        min_composite_score_margin=_require_finite_nonneg(
            margin_raw, "min_composite_score_margin"
        ),
    )

    churn_raw = _require_block(raw, "churn")
    churn = ChurnConfig(
        max_rotations_per_day=_require_positive_int(
            churn_raw, "max_rotations_per_day"
        ),
        max_open_intents=_require_positive_int(churn_raw, "max_open_intents"),
        rotation_subcap=_require_positive_int(churn_raw, "rotation_subcap"),
        same_incumbent_cooldown_td=_require_positive_int(
            churn_raw, "same_incumbent_cooldown_td"
        ),
        same_pair_cooldown_td=_require_positive_int(
            churn_raw, "same_pair_cooldown_td"
        ),
    )

    expiry_raw = _require_block(raw, "expiry")
    expiry = ExpiryConfig(
        max_trading_days=_require_positive_int(expiry_raw, "max_trading_days"),
    )

    config_hash = _hash_config(
        version=version,
        min_holding_age_trading_days=incumbent_weak.min_holding_age_trading_days,
        max_line1_percentile=incumbent_weak.max_line1_percentile,
        min_rank_deterioration_pct=incumbent_weak.min_rank_deterioration_pct,
        score_below_median_mad_mult=incumbent_weak.score_below_median_mad_mult,
        drawdown_soft_threshold=incumbent_weak.drawdown_soft_threshold,
        min_percentile=challenger_margin.min_percentile,
        min_rank_lead_pct=challenger_margin.min_rank_lead_pct,
        min_composite_score_margin=challenger_margin.min_composite_score_margin,
        max_rotations_per_day=churn.max_rotations_per_day,
        max_open_intents=churn.max_open_intents,
        rotation_subcap=churn.rotation_subcap,
        same_incumbent_cooldown_td=churn.same_incumbent_cooldown_td,
        same_pair_cooldown_td=churn.same_pair_cooldown_td,
        max_trading_days=expiry.max_trading_days,
    )
    config = RotationPolicyConfig(
        version=version,
        incumbent_weak=incumbent_weak,
        challenger_margin=challenger_margin,
        churn=churn,
        expiry=expiry,
        config_hash=config_hash,
    )
    log.info(
        "rotation_policy_config_loaded",
        path=str(path), version=version, config_hash=config_hash,
    )
    return config


def _require_block(block: dict[str, Any], key: str) -> dict[str, Any]:
    value = block.get(key)
    if not isinstance(value, dict):
        raise SlotPortfolioError(
            f"slot_rotation_policy.{key} must be a mapping, got {value!r}"
        )
    return value


def _require_positive_int(block: dict[str, Any], key: str) -> int:
    value = block.get(key)
    # bool is an int subclass — reject it so true/false can't pose as a count.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SlotPortfolioError(
            f"slot_rotation_policy.{key} must be a positive int, got {value!r}"
        )
    return value


def _require_pct(block: dict[str, Any], key: str) -> float:
    value = block.get(key)
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not (0.0 <= float(value) <= 1.0)
    ):
        raise SlotPortfolioError(
            f"slot_rotation_policy.{key} must be in [0, 1], got {value!r}"
        )
    return float(value)


def _require_finite_nonneg(block: dict[str, Any], key: str) -> float:
    value = block.get(key)
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise SlotPortfolioError(
            f"slot_rotation_policy.{key} must be a finite non-negative number, "
            f"got {value!r}"
        )
    return float(value)


def _hash_config(**fields: Any) -> str:
    """Stable sha256 of the effective config (LiveArtifactRegistry pin)."""
    blob = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


__all__ = [
    "RotationPolicyConfig",
    "RotationProposal",
    "load_rotation_policy_config",
    "propose_rotation",
]
