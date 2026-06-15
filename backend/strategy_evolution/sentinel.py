"""Null-edge sentinel control group (AE-005 / P2-2-amendment-2026-06-14 §2.4).

The anti-self-deception control. Every Sobol batch is spiked with a handful of
**known-no-edge sentinels** — legal parameter sets whose backtest is driven by a
deterministically-shuffled (signal-free) score stream, so they carry no
systematic edge by construction (amendment §2.4 — "shuffle 信号").

Sentinels make "is the machine actually finding edge?" *observable*:

* a gate that lets ANY sentinel through to a forward-shadow mandate is broken
  (the lane asserts ``sentinels_passed == 0``);
* sentinels rejected but real candidates also all rejected ⇒ the search space
  has no alpha (honest dashboard surfaces this, not a false "it works").

A sentinel is caught on two independent axes: the statistical prefilter (no edge
in the shuffled-score backtest) AND the mechanism gate (a sentinel declares no
mechanism — :data:`ParamSet.mechanism is None`).

Pure + deterministic — no IO, no clock, no LLM.
"""

from __future__ import annotations

from dataclasses import replace

from backend.strategy_evolution.mechanism_registry import EconomicMechanism
from backend.strategy_evolution.quant_param_search import (
    ParamExperimentProducer,
    ParamSearchError,
    ParamSet,
)

# A sentinel reuses the producer's legal-point transforms (so it is a valid
# parameter set) but is re-flagged as a mechanism-less sentinel. The mechanism
# only labels how the legal point was drawn; the sentinel's *backtest* is what
# is signal-free, driven by the shuffled-score provider the lane selects when
# ``ParamSet.is_sentinel`` is set.
_SENTINEL_DRAW_MECHANISM: dict[str, EconomicMechanism] = {
    "selector_weights": EconomicMechanism.MOMENTUM_CONTINUATION,
    "allocation.value_slot_quota": EconomicMechanism.VALUE_PREMIUM,
    "theme_tier_weights": EconomicMechanism.DIVERSIFICATION,
}

SENTINEL_SEED_OFFSET = 982_451_653
"""Prime offset so the sentinel Sobol stream never coincides with the real
candidate stream for the same base seed."""


def make_sentinels(*, family: str, count: int, seed: int) -> tuple[ParamSet, ...]:
    """Deterministically draw ``count`` null-edge sentinel parameter sets.

    Same ``(family, count, seed)`` → bit-identical sentinels. The points are
    legal (drawn via the producer's constraint transforms) but stripped of any
    mechanism and flagged ``is_sentinel`` so the lane drives their backtest with
    a signal-free score stream.
    """
    if count < 0:
        raise ValueError("count must be >= 0")
    if count == 0:
        return ()
    draw_mechanism = _SENTINEL_DRAW_MECHANISM.get(family)
    if draw_mechanism is None:
        # A family known to the producer but missing a sentinel draw mechanism
        # is a config-drift bug; fail closed with ParamSearchError so the
        # nightly runner skips just this family (it catches ParamSearchError)
        # instead of a KeyError crashing the whole run.
        raise ParamSearchError(
            f"no sentinel draw mechanism registered for family {family!r}"
        )
    producer = ParamExperimentProducer(family=family)
    drawn = producer.produce(
        seed=seed + SENTINEL_SEED_OFFSET,
        n_candidates=count,
        mechanism=draw_mechanism,
    )
    return tuple(replace(p, mechanism=None, is_sentinel=True) for p in drawn)


__all__ = [
    "SENTINEL_SEED_OFFSET",
    "make_sentinels",
]
