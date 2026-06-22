"""Layer-B forward-confirmation skeleton (QGR-2 build-new ⑨).

A reusable arena buys an honest *development* layer, but the math is unforgiving:
the only clean go-live evidence is **new data the strategy was frozen before**
(QGR plan §4.2/§4.3). This module is the generic, ``benchmark_relative``-free
re-target of ``round4_forward_test.py``'s Layer-B prototype:

* **pre-registration** (:class:`PreRegistration`) — the byte-frozen strategy
  artifact hash + success criteria + the planned observation budget, content-
  addressed, committed before any forward data exists (QGR-5).
* **independent observations** — only *complete, non-overlapping* ``bet_horizon``
  bets count (:func:`non_overlapping_bets`); overlapping windows are never
  independent samples.
* **ACCRUING never emits a verdict on noise** — below ``min_observations`` the
  status is ACCRUING with no pass/fail (the round-4 forward runner's core
  honesty), so a lucky short window cannot manufacture a go-live.
* **alpha-spending** (:func:`obf_spend` / :func:`pocock_spend`) — repeated peeks
  as the window accrues are charged against an O'Brien-Fleming / Pocock spending
  budget, so multiple looks do not inflate the false-positive rate.

The real wiring (frozen candidate → forward PIT panel → arena metrics) lands in
QGR-5/6; this is the gating + spending math, pure + deterministic.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from scipy.stats import norm

_E_MINUS_1 = math.e - 1.0


@dataclass(frozen=True)
class PreRegistration:
    """A frozen, content-addressed go-live pre-registration (committed pre-data).

    ``spending`` (the alpha-spending family) is part of the hashed plan — freezing
    only the alpha level/criteria would let the same committed prereg be evaluated
    with the looser interim Pocock schedule after seeing partial data.
    """

    strategy_artifact_sha256: str
    freeze_date: str
    bet_horizon_td: int
    min_observations: int
    target_observations: int
    overall_alpha: float
    success_criteria: Mapping[str, float] = field(default_factory=dict)
    spending: str = "obf"

    def __post_init__(self) -> None:
        if self.spending not in ("obf", "pocock"):
            raise ValueError(
                f"unknown spending mode {self.spending!r}; use 'obf' or 'pocock'"
            )

    @property
    def content_address(self) -> str:
        payload = json.dumps(
            {
                "strategy_artifact_sha256": self.strategy_artifact_sha256,
                "freeze_date": self.freeze_date,
                "bet_horizon_td": self.bet_horizon_td,
                "min_observations": self.min_observations,
                "target_observations": self.target_observations,
                "overall_alpha": self.overall_alpha,
                "success_criteria": dict(sorted(self.success_criteria.items())),
                "spending": self.spending,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ForwardGateStatus:
    """ACCRUING (insufficient) or a spending-adjusted forward VERDICT (immutable)."""

    status: str  # "ACCRUING" | "VERDICT"
    observations: int
    min_observations: int
    information_fraction: float
    alpha_budget: float
    criteria: dict[str, bool] | None
    passed: bool | None
    note: str


def non_overlapping_bets(n_forward_td: int, bet_horizon: int) -> int:
    """Complete, non-overlapping ``bet_horizon`` bets (independent observations)."""
    if bet_horizon <= 0:
        raise ValueError("bet_horizon must be > 0")
    return max(0, n_forward_td) // bet_horizon


def pocock_spend(alpha: float, t: float) -> float:
    """Lan-DeMets Pocock-type cumulative alpha spent by information fraction ``t``.

    ``α(t) = α·ln(1 + (e−1)·t)`` — spends aggressively early, reaches ``α`` at
    ``t = 1``.
    """
    t = min(1.0, max(0.0, t))
    return alpha * math.log(1.0 + _E_MINUS_1 * t)


def obf_spend(alpha: float, t: float) -> float:
    """Lan-DeMets O'Brien-Fleming cumulative alpha spent by fraction ``t``.

    ``α(t) = 2·(1 − Φ(z_{1−α/2}/√t))`` — conservative early (spends almost
    nothing), approaching ``α`` at ``t = 1``.
    """
    t = min(1.0, max(0.0, t))
    if t <= 0.0:
        return 0.0
    z = norm.ppf(1.0 - alpha / 2.0)
    return float(2.0 * (1.0 - norm.cdf(z / math.sqrt(t))))


def evaluate_forward(
    *,
    prereg: PreRegistration,
    observations: int,
    metrics: Mapping[str, float],
    look_index: int,
    total_looks: int,
) -> ForwardGateStatus:
    """Gate a forward read: ACCRUING below the floor, else a spent-alpha verdict.

    ``metrics`` carries the realised arena scalars (e.g. ``net_total_return`` /
    ``max_drawdown`` / ``sharpe``) and optionally a ``pvalue`` (significance that
    net P&L > 0). ``success_criteria`` keys end ``_min`` (metric ≥ threshold) or
    ``_max`` (metric ≤ threshold). A ``pvalue`` is additionally required ≤ the
    alpha-spending budget for this look. The spending family is the prereg's
    frozen, hashed ``prereg.spending`` — never an after-the-fact argument.
    """
    if observations < prereg.min_observations:
        return ForwardGateStatus(
            status="ACCRUING",
            observations=observations,
            min_observations=prereg.min_observations,
            information_fraction=_info_fraction(observations, prereg),
            alpha_budget=0.0,
            criteria=None,
            passed=None,
            note=(
                f"{observations} non-overlapping bets < {prereg.min_observations} "
                "minimum — ACCRUING, never a verdict on noise. Re-run as data lands."
            ),
        )
    frac = _info_fraction(observations, prereg)
    spend_fn = obf_spend if prereg.spending == "obf" else pocock_spend
    budget = spend_fn(prereg.overall_alpha, frac)
    criteria = _evaluate_criteria(prereg.success_criteria, metrics)
    significance_ok = True
    if "pvalue" in metrics:
        significance_ok = metrics["pvalue"] <= budget
        criteria["significance_within_alpha_spend"] = significance_ok
    passed = all(criteria.values())
    return ForwardGateStatus(
        status="VERDICT",
        observations=observations,
        min_observations=prereg.min_observations,
        information_fraction=frac,
        alpha_budget=budget,
        criteria=criteria,
        passed=passed,
        note=(
            f"forward verdict over {observations} bets (look {look_index}/"
            f"{total_looks}, info {frac:.2f}, spent α={budget:.4f}) — still a "
            "forward read, strengthening as the window lengthens."
        ),
    )


def _info_fraction(observations: int, prereg: PreRegistration) -> float:
    if prereg.target_observations <= 0:
        return 1.0
    return min(1.0, observations / prereg.target_observations)


def _evaluate_criteria(
    criteria: Mapping[str, float], metrics: Mapping[str, float]
) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for key, threshold in criteria.items():
        if key.endswith("_min"):
            metric = key[: -len("_min")]
            out[key] = metrics.get(metric, float("-inf")) >= threshold
        elif key.endswith("_max"):
            metric = key[: -len("_max")]
            out[key] = metrics.get(metric, float("inf")) <= threshold
        else:
            raise ValueError(f"criterion {key!r} must end with _min or _max")
    return out


__all__ = [
    "ForwardGateStatus",
    "PreRegistration",
    "evaluate_forward",
    "non_overlapping_bets",
    "obf_spend",
    "pocock_spend",
]
