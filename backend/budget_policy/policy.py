"""Budget-adaptive position policy (Phase L-003).

A pure-Python gate that runs **upstream of both the LLM agents and the
RiskEngine** (P0-7-amendment-2026-05-24): the user's real investable cash
maps to a tier that bounds the affordable universe + the single-position
rule *before* any LLM stage. With a few-hundred-yuan account the A-share
100-share lot makes most individual stocks unaffordable, so the gate may
return ``NO_COMPLIANT_TRADE`` — a first-class outcome, never an error and
never a HOLD side-effect.

Tiers (P0-7-amendment §2.1):

* **Micro**  (cash < ~¥2,000)  — whitelisted broad ETFs only; an
  individual stock whose 1 lot is unaffordable is simply not in the tier
  universe.
* **Small**  (~¥2,000–10,000) — low-price stocks (1 lot ≤ 15% cash) + ETF;
  whitelisted broad ETFs may exceed 15% via ``concentration_exception``
  (absolute 1-lot cap + Feishu confirmation; RiskEngine re-validates).
* **Normal** (~¥10,000–100,000) — the P0-7 trio is unchanged
  (single ≤15% / total ≤70% / single trade ≤¥50k, enforced by RiskEngine).

Red lines: ``concentration_exception`` is **only** for the ETF whitelist —
an individual stock never gets the over-15% exception (it is excluded or
yields ``NO_COMPLIANT_TRADE``). This module only sets the flag; the
RiskEngine independently re-validates it (L-004), so the flag is never a
single-point bypass. Pure functions, no IO beyond the one-shot config
load, no ``import backend.{llm,agents,mirofish}`` (redline ``[L-002]``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
import yaml

log = structlog.get_logger(component="budget_policy")

# First-class outcome string (P0-7-amendment §2.2). Not an exception.
NO_COMPLIANT_TRADE: str = "NO_COMPLIANT_TRADE"


class BudgetPolicyError(ValueError):
    """Raised when the ``budget_tiers`` config fails validation."""


class BudgetTier(StrEnum):
    """The three budget tiers (P0-7-amendment §2.1)."""

    MICRO = "micro"
    SMALL = "small"
    NORMAL = "normal"


class AffordabilityOutcome(StrEnum):
    """Per-candidate affordability verdict under the active tier."""

    AFFORDABLE = "affordable"
    AFFORDABLE_WITH_EXCEPTION = "affordable_with_exception"
    EXCLUDED_TIER_UNIVERSE = "excluded_tier_universe"
    EXCLUDED_CONCENTRATION = "excluded_concentration"
    UNAFFORDABLE = "unaffordable"

    @property
    def is_tradable(self) -> bool:
        """True if a candidate with this outcome may proceed to the agents."""
        return self in {
            AffordabilityOutcome.AFFORDABLE,
            AffordabilityOutcome.AFFORDABLE_WITH_EXCEPTION,
        }


@dataclass(frozen=True)
class BudgetTierConfig:
    """Locked budget-tier thresholds + the exception ETF whitelist.

    ``max_single_stock_pct`` + ``lot_size`` are read from
    ``position_limits`` (P0-7 single source of truth) by
    :func:`load_budget_tier_config`, never duplicated in the
    ``budget_tiers`` YAML block.
    """

    micro_max_cash_yuan: float
    small_max_cash_yuan: float
    max_single_stock_pct: float
    lot_size: int
    etf_whitelist: frozenset[str]


@dataclass(frozen=True)
class BudgetCandidate:
    """Minimal per-candidate input: a 6-digit code + its one-lot cost (¥)."""

    code: str
    per_lot_cost: float


@dataclass(frozen=True)
class CandidateAffordability:
    """Per-candidate verdict + the numbers behind it."""

    code: str
    outcome: AffordabilityOutcome
    per_lot_cost: float
    max_lot_cost: float
    concentration_exception: bool = False
    requires_feishu_confirm: bool = False


@dataclass(frozen=True)
class BudgetAssessment:
    """Day-level assessment over a candidate set under the active tier."""

    tier: BudgetTier
    available_cash: float
    candidates: tuple[CandidateAffordability, ...] = field(default_factory=tuple)

    @property
    def affordable(self) -> tuple[CandidateAffordability, ...]:
        return tuple(c for c in self.candidates if c.outcome.is_tradable)

    @property
    def no_compliant_trade(self) -> bool:
        """True when no candidate is tradable under the budget (first-class)."""
        return len(self.affordable) == 0

    @property
    def outcome(self) -> str:
        return NO_COMPLIANT_TRADE if self.no_compliant_trade else "OK"


class BudgetTierPolicy:
    """Pure budget-tier gate. Deterministic; no IO."""

    def __init__(self, config: BudgetTierConfig) -> None:
        self._config = config

    @property
    def config(self) -> BudgetTierConfig:
        return self._config

    def classify_tier(self, available_cash: float) -> BudgetTier:
        """Map investable cash to a :class:`BudgetTier` (boundaries exclusive)."""
        if available_cash < self._config.micro_max_cash_yuan:
            return BudgetTier.MICRO
        if available_cash < self._config.small_max_cash_yuan:
            return BudgetTier.SMALL
        return BudgetTier.NORMAL

    def assess_candidate(
        self, available_cash: float, candidate: BudgetCandidate
    ) -> CandidateAffordability:
        """Per-candidate affordability verdict under the active tier.

        Decision order (P0-7-amendment §2.1/§2.3):
          1. Fail-closed on non-finite / non-positive cash.
          2. Micro universe gate — only whitelisted broad ETFs.
          3. Fail-closed on a missing / non-finite / non-positive 1-lot cost.
          4. Unaffordable if 1 lot > cash.
          5. Affordable if 1 lot ≤ 15% cash.
          6. Over 15%: whitelisted ETF **in Micro/Small only** → exception
             (Feishu confirm); individual stock, or Normal tier, → excluded.
             Normal (≥¥10k) keeps the P0-7 15% rule unchanged — the
             exception is a small-budget accommodation, never granted to a
             Normal account (codex L-003 P1).
        """
        cfg = self._config
        tier = self.classify_tier(available_cash)
        max_lot_cost = available_cash * cfg.max_single_stock_pct
        whitelisted = candidate.code in cfg.etf_whitelist
        lot_cost = candidate.per_lot_cost

        def result(
            outcome: AffordabilityOutcome, *, exception: bool = False
        ) -> CandidateAffordability:
            return CandidateAffordability(
                code=candidate.code,
                outcome=outcome,
                per_lot_cost=lot_cost,
                max_lot_cost=max_lot_cost,
                concentration_exception=exception,
                requires_feishu_confirm=exception,
            )

        # Fail-closed on corrupt cash (NaN / inf / non-positive) — no trade.
        if not math.isfinite(available_cash) or available_cash <= 0:
            return result(AffordabilityOutcome.UNAFFORDABLE)
        if tier is BudgetTier.MICRO and not whitelisted:
            return result(AffordabilityOutcome.EXCLUDED_TIER_UNIVERSE)
        # Fail-closed on a missing / non-finite / non-positive 1-lot cost so
        # an unknown cost can never slip past the affordability comparisons
        # (every comparison with NaN is False). codex L-003 P2.
        if not math.isfinite(lot_cost) or lot_cost <= 0 or lot_cost > available_cash:
            return result(AffordabilityOutcome.UNAFFORDABLE)
        if lot_cost <= max_lot_cost:
            return result(AffordabilityOutcome.AFFORDABLE)
        if whitelisted and tier in {BudgetTier.MICRO, BudgetTier.SMALL}:
            return result(
                AffordabilityOutcome.AFFORDABLE_WITH_EXCEPTION, exception=True
            )
        return result(AffordabilityOutcome.EXCLUDED_CONCENTRATION)

    def assess(
        self, available_cash: float, candidates: list[BudgetCandidate]
    ) -> BudgetAssessment:
        """Assess a candidate set; ``no_compliant_trade`` when none is tradable."""
        verdicts = tuple(
            self.assess_candidate(available_cash, c) for c in candidates
        )
        assessment = BudgetAssessment(
            tier=self.classify_tier(available_cash),
            available_cash=available_cash,
            candidates=verdicts,
        )
        log.info(
            "budget_assessment",
            tier=assessment.tier.value,
            available_cash=available_cash,
            candidate_count=len(candidates),
            affordable_count=len(assessment.affordable),
            outcome=assessment.outcome,
        )
        return assessment


def load_budget_tier_config(yaml_path: str | Path) -> BudgetTierConfig:
    """Load + validate ``budget_tiers`` from ``risk.yaml`` (runtime-immutable).

    ``max_single_stock_pct`` + ``lot_size`` come from ``position_limits``
    (P0-7 single source of truth), not from the ``budget_tiers`` block.

    Raises:
        FileNotFoundError: ``yaml_path`` does not exist.
        BudgetPolicyError: any threshold / whitelist invariant is violated.
    """
    path = Path(yaml_path)
    if not path.exists():
        raise FileNotFoundError(f"risk config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    tiers = raw.get("budget_tiers")
    if not isinstance(tiers, dict):
        raise BudgetPolicyError("risk.yaml missing 'budget_tiers' section")
    limits = raw.get("position_limits")
    if not isinstance(limits, dict):
        raise BudgetPolicyError("risk.yaml missing 'position_limits' section")

    micro = _require_positive_float(tiers, "micro_max_cash_yuan")
    small = _require_positive_float(tiers, "small_max_cash_yuan")
    if micro >= small:
        raise BudgetPolicyError(
            f"micro_max_cash_yuan ({micro}) must be < small_max_cash_yuan ({small})"
        )

    pct = limits.get("max_single_stock_pct")
    if not isinstance(pct, int | float) or not (0.0 < float(pct) <= 1.0):
        raise BudgetPolicyError(
            f"position_limits.max_single_stock_pct must be in (0, 1], got {pct!r}"
        )
    lot = limits.get("volume_lot_size")
    if not isinstance(lot, int) or lot < 1:
        raise BudgetPolicyError(
            f"position_limits.volume_lot_size must be a positive int, got {lot!r}"
        )

    whitelist_raw = tiers.get("etf_whitelist")
    if not isinstance(whitelist_raw, list) or not whitelist_raw:
        raise BudgetPolicyError(
            "budget_tiers.etf_whitelist must be a non-empty list"
        )
    whitelist = frozenset(str(c) for c in whitelist_raw)

    config = BudgetTierConfig(
        micro_max_cash_yuan=micro,
        small_max_cash_yuan=small,
        max_single_stock_pct=float(pct),
        lot_size=lot,
        etf_whitelist=whitelist,
    )
    log.info(
        "budget_tier_config_loaded",
        path=str(path),
        micro_max=micro,
        small_max=small,
        max_single_stock_pct=float(pct),
        lot_size=lot,
        whitelist=sorted(whitelist),
    )
    return config


def _require_positive_float(block: dict[str, Any], key: str) -> float:
    value = block.get(key)
    if not isinstance(value, int | float) or float(value) <= 0:
        raise BudgetPolicyError(
            f"budget_tiers.{key} must be a positive number, got {value!r}"
        )
    return float(value)


__all__ = [
    "NO_COMPLIANT_TRADE",
    "AffordabilityOutcome",
    "BudgetAssessment",
    "BudgetCandidate",
    "BudgetPolicyError",
    "BudgetTier",
    "BudgetTierConfig",
    "BudgetTierPolicy",
    "CandidateAffordability",
    "load_budget_tier_config",
]
