"""Economic-mechanism hypothesis gate (AE-005 / P2-2-amendment-2026-06-14 §2.4).

The anti-self-deception layer that statistics cannot provide. Every quant
parameter candidate that survives the historical prefilter must additionally
name a **pre-registered economic mechanism** explaining *why* its edge should
persist out of sample (momentum continuation / mean reversion / liquidity
premium / quality / value). A pure-data winner with no mechanism is, by
default, overfitting — and is rejected (amendment §2.4 — "无机制的纯数据胜出
= 默认过拟合,拒").

This gate is deliberately NOT statistical: it is a frozen human-curated
whitelist per parameter family. A null-edge sentinel (see
:mod:`backend.strategy_evolution.sentinel`) carries no mechanism, so this gate
rejects it on a second, independent axis from the statistical prefilter.

Everything here is pure, frozen data — no IO, no clock, no LLM. Adding a
mechanism or binding it to a new family is a code change + amendment + restart,
never a runtime mutation (mirrors the EVOLVABLE_WHITELIST discipline).
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType


class EconomicMechanism(StrEnum):
    """The pre-registered economic rationales a candidate may invoke.

    Each names a documented market phenomenon with an out-of-sample
    persistence argument — not a data artefact. The set is frozen; a genuinely
    new mechanism requires an amendment.
    """

    MOMENTUM_CONTINUATION = "momentum_continuation"
    MEAN_REVERSION = "mean_reversion"
    LIQUIDITY_PREMIUM = "liquidity_premium"
    QUALITY_PREMIUM = "quality_premium"
    VALUE_PREMIUM = "value_premium"
    LOW_VOLATILITY_ANOMALY = "low_volatility_anomaly"
    DIVERSIFICATION = "diversification"
    """Allocation-shape mechanisms (slot quota / tier weights): the edge is
    risk-budgeting / breadth, not a return factor."""


# Which mechanisms are admissible for which parameter family. The family is the
# canonical dotted prefix of the evolvable parameters (selector.* / allocation.*
# / theme.*). A candidate must declare a mechanism drawn from its family's set.
_FAMILY_MECHANISMS: dict[str, frozenset[EconomicMechanism]] = {
    "selector_weights": frozenset(
        {
            EconomicMechanism.MOMENTUM_CONTINUATION,
            EconomicMechanism.MEAN_REVERSION,
            EconomicMechanism.LIQUIDITY_PREMIUM,
            EconomicMechanism.QUALITY_PREMIUM,
            EconomicMechanism.VALUE_PREMIUM,
            EconomicMechanism.LOW_VOLATILITY_ANOMALY,
        }
    ),
    "allocation.value_slot_quota": frozenset(
        {
            EconomicMechanism.VALUE_PREMIUM,
            EconomicMechanism.DIVERSIFICATION,
        }
    ),
    "theme_tier_weights": frozenset(
        {
            EconomicMechanism.MOMENTUM_CONTINUATION,
            EconomicMechanism.DIVERSIFICATION,
        }
    ),
}

FAMILY_MECHANISMS: MappingProxyType[str, frozenset[EconomicMechanism]] = (
    MappingProxyType(_FAMILY_MECHANISMS)
)
"""Immutable family → admissible-mechanisms map. No runtime mutation surface."""


def known_family(family: str) -> bool:
    """Whether ``family`` is a registered evolvable parameter family."""
    return family in FAMILY_MECHANISMS


def admissible_mechanisms(family: str) -> frozenset[EconomicMechanism]:
    """The admissible mechanisms for ``family`` (empty for an unknown family)."""
    return FAMILY_MECHANISMS.get(family, frozenset())


def has_valid_mechanism(family: str, mechanism: EconomicMechanism | None) -> bool:
    """The gate predicate: ``True`` iff ``mechanism`` is registered for ``family``.

    ``None`` (a candidate that named no mechanism — e.g. a sentinel) is always
    ``False``: no mechanism means default-overfit, rejected.
    """
    if mechanism is None:
        return False
    return mechanism in admissible_mechanisms(family)


__all__ = [
    "FAMILY_MECHANISMS",
    "EconomicMechanism",
    "admissible_mechanisms",
    "has_valid_mechanism",
    "known_family",
]
