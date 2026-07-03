"""AP-0.5 return-blind power precheck — is passing DSR>=0.95 even reachable?

Before spending a promotion trial on the AP-2 arena, this module answers a purely
analytical question (spec outline §5.0, implementation plan §4): given the frozen
deflation N and sample structure, what annualized Sharpe would the A4 composite
NEED to clear the deflated-Sharpe gate — and is that within reach of the best
disclosed edge (pure-reversal eq_5)? It is the debt-saving front gate: if the
required Sharpe is unreachable, we do NOT burn the four-gate trial.

**Return-blind by construction**: it reads NO A4/panel returns and imports NO
backtest / bar-source / panel module (grep-guarded in the test). It uses only
(a) the pre-declared :data:`alpha_pivot_spec.POWER_INPUTS` (normal moments, the
conservative HAC upper-bound rule, T, deflation N, K), and (b) DISCLOSED frontier
summary scalars (eq_5 DSR / N / n_periods) read from the frontier result JSON —
zero new peek, no return series (``period_returns`` is null there anyway).

Algorithm (Bailey–López de Prado DSR back-solve):
  1. HAC inflation = the structural 5td-overlap conservative upper bound for
     lag = horizon-1 (:func:`hac_conservative_inflation`; = 3.40 at H=5) — a
     pre-declared rule, never a sample estimate.
  2. ``SR_req`` = the minimal per-period Sharpe s.t. ``DSR(SR_req; N, T, HAC,
     normal) = 0.95`` (bisection; DSR is monotone increasing in SR). Annualized
     ``SR_req_ann = SR_req * sqrt(252/rebalance_freq)`` disclosed alongside.
  3. ``SR_ref`` = the pure-reversal eq_5 edge, recovered by inverting its
     DISCLOSED DSR under the same normal moments and eq_5's own disclosed
     structure (lag=0, N_ref, T_ref) — the reference's own risk-adjusted edge.
  4. ``go`` iff ``SR_req <= K * SR_ref`` (K=2, owner decision #2).

A no-go pre-commits (owner decision #3) to downgrading this cut to pure
diagnostics (attribution IC + relative-to-pure-reversal SPA, no four-gate claim)
and reporting the evidence to the owner — never moving the goalposts.

Pure + deterministic; no RNG, no wall-clock. Reuses the audited DSR primitives
in ``backend.strategy_evolution.anti_overfit`` (same ones ``honest_gates`` uses).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

from backend.strategy_evolution.anti_overfit import (
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)

from .alpha_pivot_spec import GATE_CALIBRATION, POWER_INPUTS, spec_hash

DEFAULT_FRONTIER_RESULT: str = "data/factor_research/slot_frontier_result.json"
DEFAULT_OUT: str = "data/factor_research/alpha_pivot_power.json"
TRADING_DAYS_PER_YEAR: int = 252
GATE_TARGET: float = GATE_CALIBRATION.dsr_threshold  # 0.95 (frozen spec)


def hac_conservative_inflation(horizon: int) -> float:
    """Structural conservative HAC variance-inflation for ``horizon``-td overlap.

    Under the DSR null (zero-skill ``horizon``-day overlapping holdings), the
    daily overlap of moving windows induces autocorrelation ``rho_l = (H-l)/H``
    for ``l < H``. The Newey-West (Bartlett) inflation at lag ``H-1`` is then a
    pre-declared upper bound (not a sample estimate):

        ``1 + 2 * sum_{l=1..H-1} (1 - l/H) * (H-l)/H = 1 + 2 * sum ((H-l)/H)**2``

    (the Bartlett weight ``1 - l/(lag+1)`` equals ``(H-l)/H`` at ``lag = H-1``).
    For ``H = 5`` this is exactly ``3.40`` — the "5td-overlap 结构保守上界" the
    spec pre-declares. ``horizon <= 1`` → ``1.0`` (no overlap).
    """
    if horizon <= 1:
        return 1.0
    tail = sum(((horizon - k) / horizon) ** 2 for k in range(1, horizon))
    return 1.0 + 2.0 * tail


def dsr_from_sr(
    sr: float,
    *,
    t: int,
    n_trials: int,
    hac: float,
    skew: float,
    kurt: float,
) -> float:
    """Deflated Sharpe for a *hypothetical* per-period Sharpe ``sr`` (no returns).

    Mirrors :func:`honest_gates.deflated_sharpe_hac` but takes ``sr`` directly and
    the HAC inflation as a pre-declared scalar, so nothing here reads returns.
    """
    var_sr = (1.0 + 0.5 * sr * sr) / t * hac
    benchmark = expected_max_sharpe(n_trials, var_sr)
    return probabilistic_sharpe_ratio(
        sr, benchmark_sr=benchmark, n_samples=t, skew=skew, kurtosis=kurt
    )


def solve_sr_req(
    *,
    t: int,
    n_trials: int,
    hac: float,
    target: float,
    skew: float,
    kurt: float,
    hi: float = 10.0,
    iters: int = 200,
) -> float:
    """Minimal per-period Sharpe with ``DSR = target`` (bisection; DSR ↑ in SR)."""
    def dsr(sr: float) -> float:
        return dsr_from_sr(sr, t=t, n_trials=n_trials, hac=hac, skew=skew, kurt=kurt)

    lo, high = 0.0, hi
    if dsr(high) < target:
        raise ValueError(f"target {target} unreachable below SR={hi}")
    for _ in range(iters):
        mid = 0.5 * (lo + high)
        if dsr(mid) < target:
            lo = mid
        else:
            high = mid
    return 0.5 * (lo + high)


def sr_ref_from_disclosed_dsr(
    dsr: float,
    *,
    t: int,
    n_trials: int,
    skew: float,
    kurt: float,
    hi: float = 10.0,
    iters: int = 200,
) -> float:
    """Recover the reference per-period Sharpe by inverting its DISCLOSED DSR.

    Uses the reference's own disclosed structure (lag=0 → HAC=1.0, its N and T)
    under the pre-declared normal moments — an inversion of a disclosed scalar,
    not a new read of returns.
    """
    def deflated(sr: float) -> float:
        return dsr_from_sr(sr, t=t, n_trials=n_trials, hac=1.0, skew=skew, kurt=kurt)

    lo, high = -1.0, hi
    for _ in range(iters):
        mid = 0.5 * (lo + high)
        if deflated(mid) < dsr:
            lo = mid
        else:
            high = mid
    return 0.5 * (lo + high)


def _annualization_factor(rebalance_freq: int) -> float:
    return math.sqrt(TRADING_DAYS_PER_YEAR / rebalance_freq)


@dataclass(frozen=True)
class DisclosedReference:
    """Disclosed pure-reversal eq_5 summary scalars (zero new peek)."""

    label: str
    dsr: float
    n_trials_deflation: int
    n_periods: int


def load_disclosed_reference(
    frontier_result_path: str, *, label: str = "eq_5"
) -> DisclosedReference:
    """Read the disclosed eq_5 DSR / N / n_periods from the frontier result JSON.

    Fail-closed if the artifact or the labelled config is missing (no silent
    fallback that would smuggle an undisclosed number).
    """
    path = Path(frontier_result_path)
    if not path.exists():
        raise FileNotFoundError(f"frontier result not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = data.get("configs", {}).get(label)
    if cfg is None:
        raise KeyError(f"frontier result has no config {label!r}")
    return DisclosedReference(
        label=label,
        dsr=float(cfg["dsr"]),
        n_trials_deflation=int(data["n_trials_deflation"]),
        n_periods=int(cfg["n_periods"]),
    )


def run_precheck(
    *, frontier_result_path: str = DEFAULT_FRONTIER_RESULT
) -> dict[str, object]:
    """Compute SR_req vs K·SR_ref and the go/no-go verdict (return-blind)."""
    p = POWER_INPUTS
    if p.hac_lag != p.horizon - 1:
        raise ValueError(f"spec hac_lag {p.hac_lag} != horizon-1 {p.horizon - 1}")
    hac = hac_conservative_inflation(p.horizon)
    ann = _annualization_factor(p.rebalance_freq)

    sr_req = solve_sr_req(
        t=p.t_onc_effective,
        n_trials=p.deflation_n,
        hac=hac,
        target=GATE_TARGET,
        skew=p.skew,
        kurt=p.kurtosis,
    )
    benchmark_sr = expected_max_sharpe(
        p.deflation_n, (1.0 + 0.5 * sr_req * sr_req) / p.t_onc_effective * hac
    )

    ref = load_disclosed_reference(frontier_result_path)
    sr_ref = sr_ref_from_disclosed_dsr(
        ref.dsr, t=ref.n_periods, n_trials=ref.n_trials_deflation,
        skew=p.skew, kurt=p.kurtosis,
    )

    go = sr_req <= p.k_power * sr_ref
    return {
        "spec_hash16": spec_hash()[:16],
        "gate_target_dsr": GATE_TARGET,
        "hac_inflation": hac,
        "sr0_benchmark_period": benchmark_sr,
        "sr0_benchmark_ann": benchmark_sr * ann,
        "sr_req_period": sr_req,
        "sr_req_ann": sr_req * ann,
        "sr_ref_period": sr_ref,
        "sr_ref_ann": sr_ref * ann,
        "k_power": p.k_power,
        "k_times_sr_ref_ann": p.k_power * sr_ref * ann,
        "gap_factor": sr_req / sr_ref if sr_ref > 0 else math.inf,
        "go": go,
        "verdict": "go" if go else "no-go",
        "inputs_echo": {
            "deflation_n": p.deflation_n,
            "t_onc_effective": p.t_onc_effective,
            "horizon": p.horizon,
            "rebalance_freq": p.rebalance_freq,
            "hac_lag": p.hac_lag,
            "hac_rule": p.hac_rule,
            "skew": p.skew,
            "kurtosis": p.kurtosis,
            "annualization_factor": ann,
            "sr_ref_source": p.sr_ref_source,
            "reference": {
                "label": ref.label,
                "disclosed_dsr": ref.dsr,
                "n_trials_deflation": ref.n_trials_deflation,
                "n_periods": ref.n_periods,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontier-result", default=DEFAULT_FRONTIER_RESULT)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args()
    result = run_precheck(frontier_result_path=args.frontier_result)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    sr_req_ann = float(result["sr_req_ann"])  # type: ignore[arg-type]
    k_sr_ref_ann = float(result["k_times_sr_ref_ann"])  # type: ignore[arg-type]
    gap = float(result["gap_factor"])  # type: ignore[arg-type]
    verdict = str(result["verdict"]).upper()
    print(
        f"SR_req_ann={sr_req_ann:.4f}  K*SR_ref_ann={k_sr_ref_ann:.4f}  "
        f"gap={gap:.1f}x  -> {verdict}"
    )
    print(f"[written: {out}]")


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_FRONTIER_RESULT",
    "DEFAULT_OUT",
    "DisclosedReference",
    "GATE_TARGET",
    "dsr_from_sr",
    "hac_conservative_inflation",
    "load_disclosed_reference",
    "run_precheck",
    "solve_sr_req",
    "sr_ref_from_disclosed_dsr",
]
