"""Honest deterministic parameter search (AE-005 / P2-2-amendment-2026-06-14 §2.2).

The task producer for the 5th (deterministic, zero-LLM) evolution lane. It draws
candidate parameter sets from a **pre-declared, fixed-N, space-filling** design
— ``scipy.stats.qmc.Sobol`` with a fixed seed — NOT an adaptive Bayesian
optimiser. Three independent reasons (amendment §2.2), not "Sobol is more
honest":

* pre-declared fixed N ⇒ the deflated-Sharpe trial count ``N`` is *exact*
  (Optuna-TPE / Ax-BoTorch are adaptive → N is uncountable, overfits faster);
* Sobol ⇒ low-discrepancy coverage of low-dimensional projections;
* rejecting Bayesian ⇒ N stays countable.

Constraints are satisfied by **deterministic, each-point-legal transforms**
(no rejection sampling — that would make N inexact): the selector-weight simplex
(sum == 1, per-weight caps) via a capped-simplex water-fill, the theme-tier
monotone order via a sequential conditional transform. The search boundary is
the frozen :data:`EVOLVABLE_WHITELIST` clamp — changing it is an amendment +
restart, never a runtime act — and the cumulative trial count never resets
across sessions (:func:`assert_cumulative_n_not_reset`): every parameter-space /
window edit is a fresh content-addressed experiment, so it counts as a trial.

Pure + deterministic given a seed; no IO, no clock, no LLM.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Sequence
from dataclasses import dataclass

from scipy.stats import qmc

from backend.strategy_evolution.evolvable_params import (
    EVOLVABLE_WHITELIST,
    EvolvableParamSpec,
    validate_param_set,
)
from backend.strategy_evolution.mechanism_registry import (
    EconomicMechanism,
    has_valid_mechanism,
)

_VALUE_PRECISION = 6
"""Decimal places for the canonical (content-addressed) parameter value."""

SELECTOR_WEIGHTS_FAMILY = "selector_weights"
THEME_TIER_WEIGHTS_FAMILY = "theme_tier_weights"
VALUE_SLOT_QUOTA_FAMILY = "allocation.value_slot_quota"

# The families the AE-005 first batch evolves (owner card ③): selector factor
# weights + allocation quota + theme tier weights. Line-2 intraday parameters
# are deliberately absent (amendment §2.5 — "Line-2 不进环").
FIRST_BATCH_FAMILIES: tuple[str, ...] = (
    SELECTOR_WEIGHTS_FAMILY,
    VALUE_SLOT_QUOTA_FAMILY,
    THEME_TIER_WEIGHTS_FAMILY,
)


class ParamSearchError(ValueError):
    """A search-space / constraint / cumulative-N violation."""


# ---------------------------------------------------------------------------
# ParamSet — one candidate's frozen, content-addressed parameter delta
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSet:
    """One candidate: a frozen parameter delta for a single family.

    ``values`` is the evolved assignment for that family's parameters ONLY
    (the lane overlays it on the pinned incumbent for the untouched families).
    ``mechanism`` is the pre-registered economic rationale (``None`` for a
    null-edge sentinel). The content address covers family + values +
    mechanism + sentinel flag.
    """

    family: str
    values: tuple[tuple[str, float], ...]
    mechanism: EconomicMechanism | None
    is_sentinel: bool = False

    def as_dict(self) -> dict[str, float]:
        return {name: value for name, value in self.values}

    def param_space_strings(self) -> dict[str, str]:
        """Canonical stringified assignment for ``ExperimentRecord.param_space``."""
        return {name: f"{value:.{_VALUE_PRECISION}f}" for name, value in self.values}

    @property
    def param_hash(self) -> str:
        payload = json.dumps(
            {
                "family": self.family,
                "values": [
                    [name, round(value, _VALUE_PRECISION)]
                    for name, value in self.values
                ],
                "mechanism": self.mechanism.value if self.mechanism else None,
                "is_sentinel": self.is_sentinel,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _round(value: float) -> float:
    return round(value, _VALUE_PRECISION)


def _family_specs(family: str) -> tuple[EvolvableParamSpec, ...]:
    if family == SELECTOR_WEIGHTS_FAMILY:
        names = [
            spec.name
            for spec in EVOLVABLE_WHITELIST.values()
            if spec.group == SELECTOR_WEIGHTS_FAMILY
        ]
    elif family == THEME_TIER_WEIGHTS_FAMILY:
        names = [
            "theme.tier1_weight",
            "theme.tier2_weight",
            "theme.tier3_weight",
            "theme.tier4_weight",
        ]
    elif family == VALUE_SLOT_QUOTA_FAMILY:
        names = [VALUE_SLOT_QUOTA_FAMILY]
    else:
        raise ParamSearchError(f"unknown evolvable family: {family}")
    return tuple(EVOLVABLE_WHITELIST[name] for name in names)


def effective_dimension(family: str) -> int:
    """Sobol dimension AFTER constraints (amendment §2.2 — constraints first).

    selector weights: a (k−1)-simplex ⇒ k−1 dims; theme tiers: k sequential
    conditionals ⇒ k dims; the integer quota: 1 dim.
    """
    specs = _family_specs(family)
    if family == SELECTOR_WEIGHTS_FAMILY:
        return len(specs) - 1
    return len(specs)


# ---------------------------------------------------------------------------
# Deterministic, each-point-legal constraint transforms
# ---------------------------------------------------------------------------


def _kraemer_simplex(u: Sequence[float]) -> list[float]:
    """Sorted-spacings (Kraemer) map of (k−1) uniforms → k simplex weights.

    Uniform on the open unit simplex (each weight in (0, 1), sum == 1).
    """
    cuts = sorted([0.0, *u, 1.0])
    return [cuts[i + 1] - cuts[i] for i in range(len(cuts) - 1)]


def _project_capped_simplex(
    weights: Sequence[float], caps: Sequence[float]
) -> list[float]:
    """Deterministic water-fill projection onto {sum == 1, 0 <= w_i <= cap_i}.

    The unit-simplex draw may exceed a per-weight cap; this caps the offenders
    and redistributes the freed mass proportionally over the uncapped weights
    until none exceeds its cap. Each step is deterministic and the feasible
    region is non-empty by construction (sum of caps > 1), so it converges —
    NOT rejection sampling.
    """
    w = list(weights)
    capped = [False] * len(w)
    for _ in range(len(w) + 1):
        over = [i for i in range(len(w)) if not capped[i] and w[i] > caps[i]]
        if not over:
            break
        excess = 0.0
        for i in over:
            excess += w[i] - caps[i]
            w[i] = caps[i]
            capped[i] = True
        free = [i for i in range(len(w)) if not capped[i]]
        free_mass = sum(w[i] for i in free)
        if not free or free_mass <= 0.0:
            # Spread the residual equally if no proportional base remains.
            share = excess / len(free) if free else 0.0
            for i in free:
                w[i] += share
            break
        for i in free:
            w[i] += excess * (w[i] / free_mass)
    return w


def _finalise_sum_to_one(
    weights: Sequence[float], caps: Sequence[float]
) -> list[float]:
    """Round to fixed precision and absorb the tiny rounding residual so the sum
    is *exactly* 1.0 while staying inside ``[0, cap]`` for every weight.

    The residual direction matters: a POSITIVE residual (sum rounded down) goes
    to the weight with the most headroom below its cap; a NEGATIVE residual (sum
    rounded up) is taken from the weight with the most room above its 0 floor
    (the largest weight). Always picking max-headroom regardless of sign would
    dump a negative residual onto a near-zero weight and drive it below 0 — a
    rare but real illegal point that would make ``produce()`` raise."""
    rounded = [_round(x) for x in weights]
    residual = round(1.0 - sum(rounded), _VALUE_PRECISION)
    if residual != 0.0:
        if residual > 0.0:
            target = max(range(len(rounded)), key=lambda i: caps[i] - rounded[i])
        else:
            target = max(range(len(rounded)), key=lambda i: rounded[i])
        rounded[target] = _round(rounded[target] + residual)
    return rounded


def _selector_weights_point(
    u: Sequence[float], specs: Sequence[EvolvableParamSpec]
) -> dict[str, float]:
    caps = [spec.clamp_max for spec in specs]
    simplex = _kraemer_simplex(u)
    projected = _project_capped_simplex(simplex, caps)
    final = _finalise_sum_to_one(projected, caps)
    return {spec.name: value for spec, value in zip(specs, final, strict=True)}


def _theme_tiers_point(
    u: Sequence[float], specs: Sequence[EvolvableParamSpec]
) -> dict[str, float]:
    """Sequential conditional transform: each tier in its clamp AND <= the
    previous tier (monotone non-increasing). Each point legal, N exact."""
    values: list[float] = []
    prev_upper = specs[0].clamp_max
    for spec, ui in zip(specs, u, strict=True):
        upper = min(spec.clamp_max, prev_upper)
        lower = spec.clamp_min
        if upper < lower:
            upper = lower  # clamp floors dominate — degenerate but legal
        value = lower + ui * (upper - lower)
        values.append(value)
        prev_upper = value
    rounded = [_round(v) for v in values]
    # Rounding can flip two near-equal tiers; a running-min restores monotone
    # order while staying >= each tier's clamp floor.
    for i in range(1, len(rounded)):
        rounded[i] = min(rounded[i], rounded[i - 1])
    return {spec.name: value for spec, value in zip(specs, rounded, strict=True)}


def _value_quota_point(
    u: Sequence[float], specs: Sequence[EvolvableParamSpec]
) -> dict[str, float]:
    spec = specs[0]
    span = int(spec.clamp_max - spec.clamp_min) + 1
    step = min(span - 1, int(u[0] * span))
    return {spec.name: float(int(spec.clamp_min) + step)}


def _transform_point(family: str, u: Sequence[float]) -> dict[str, float]:
    specs = _family_specs(family)
    if family == SELECTOR_WEIGHTS_FAMILY:
        return _selector_weights_point(u, specs)
    if family == THEME_TIER_WEIGHTS_FAMILY:
        return _theme_tiers_point(u, specs)
    if family == VALUE_SLOT_QUOTA_FAMILY:
        return _value_quota_point(u, specs)
    raise ParamSearchError(f"unknown evolvable family: {family}")


# ---------------------------------------------------------------------------
# Cumulative-N monotonicity guard (amendment §2.2 — never reset)
# ---------------------------------------------------------------------------


def assert_cumulative_n_not_reset(
    *, declared_cumulative_n: int, registry_trial_count: int
) -> None:
    """Reject a batch that claims FEWER cumulative trials than the registry holds.

    The DSR deflation only bites if ``N`` is honest: an operator cannot shrink
    the trial count by re-seeding or pruning the registry. Cumulative N is
    read from the append-only :class:`ExperimentRegistry`; a declared value
    below it is a tampering attempt — fail-closed.
    """
    if declared_cumulative_n < 0 or registry_trial_count < 0:
        raise ParamSearchError("trial counts must be >= 0")
    if declared_cumulative_n < registry_trial_count:
        raise ParamSearchError(
            f"cumulative N reset detected: declared {declared_cumulative_n} "
            f"< registry {registry_trial_count} (append-only — never resets)"
        )


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamExperimentProducer:
    """Deterministic Sobol candidate producer for one evolvable family."""

    family: str

    def __post_init__(self) -> None:
        _family_specs(self.family)  # validates the family early (fail-fast)

    def produce(
        self,
        *,
        seed: int,
        n_candidates: int,
        mechanism: EconomicMechanism,
    ) -> tuple[ParamSet, ...]:
        """Draw ``n_candidates`` legal parameter sets (same seed → identical).

        ``mechanism`` is the pre-registered hypothesis under test for the whole
        batch; it must be admissible for the family (a candidate with no valid
        mechanism is rejected downstream by the mechanism gate). Every returned
        :class:`ParamSet` passes :func:`validate_param_set` by construction.
        """
        if n_candidates < 1:
            raise ParamSearchError("n_candidates must be >= 1")
        if not has_valid_mechanism(self.family, mechanism):
            raise ParamSearchError(
                f"{mechanism} is not an admissible mechanism for {self.family}"
            )
        dim = effective_dimension(self.family)
        sampler = qmc.Sobol(d=dim, scramble=True, seed=seed)
        with warnings.catch_warnings():
            # A non-power-of-2 sample loses Sobol's balance guarantee but stays
            # deterministic; the fixed pre-declared N is the honest-search
            # requirement, not a power-of-2 count.
            warnings.simplefilter("ignore")
            points = sampler.random(n_candidates)
        out: list[ParamSet] = []
        for row in points:
            assignment = _transform_point(self.family, [float(x) for x in row])
            self._assert_legal(assignment)
            out.append(
                ParamSet(
                    family=self.family,
                    values=tuple(sorted((k, _round(v)) for k, v in assignment.items())),
                    mechanism=mechanism,
                )
            )
        return tuple(out)

    def _assert_legal(self, assignment: dict[str, float]) -> None:
        result = validate_param_set(assignment)
        if not result.passed:
            raise ParamSearchError(
                f"transform produced an illegal point for {self.family}: "
                f"{result.violations}"
            )


__all__ = [
    "FIRST_BATCH_FAMILIES",
    "SELECTOR_WEIGHTS_FAMILY",
    "THEME_TIER_WEIGHTS_FAMILY",
    "VALUE_SLOT_QUOTA_FAMILY",
    "ParamExperimentProducer",
    "ParamSearchError",
    "ParamSet",
    "assert_cumulative_n_not_reset",
    "effective_dimension",
]
