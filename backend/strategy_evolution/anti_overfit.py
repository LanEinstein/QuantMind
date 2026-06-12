"""de Prado anti-overfit toolbox (R-002).

Pure math, no IO: purged + embargoed K-fold splits and the deflated
Sharpe ratio (DSR). Both are promotion-gate inputs (P2-2-amendment +
backtest dossier §111): every backtested discovery must clear

* purged+embargoed CV — no train/test leakage across the prediction
  horizon (purge) nor through serial correlation at the boundary
  (embargo);
* deflated Sharpe — the observed Sharpe must beat the expected maximum
  Sharpe of ``n_trials`` random tries (multiple-testing correction),
  with non-normality (skew/kurtosis) folded in.

References: López de Prado, *Advances in Financial Machine Learning*
(2018), ch.7 (purged CV) + Bailey & López de Prado (2014), "The
Deflated Sharpe Ratio". Formulas re-derived, no code copied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EULER_MASCHERONI = 0.5772156649015329

DSR_CONFIDENCE_FLOOR = 0.95
"""A discovery is promotable only when P(true SR > 0 | trials) ≥ 95%."""


@dataclass(frozen=True)
class PurgedFold:
    """One CV fold: train indices with the test window purged+embargoed."""

    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]


def purged_kfold_splits(
    n_samples: int,
    *,
    n_splits: int = 5,
    purge: int = 0,
    embargo: int = 0,
) -> tuple[PurgedFold, ...]:
    """Contiguous K-fold splits with purge + embargo around each test set.

    Args:
        n_samples: number of time-ordered observations.
        n_splits: number of contiguous test folds.
        purge: observations dropped from the train set immediately
            BEFORE the test window (labels overlapping the test
            horizon must not be trained on).
        embargo: observations dropped from the train set immediately
            AFTER the test window (serial correlation leaks backward).

    Raises:
        ValueError: non-positive sizes or more splits than samples.
    """
    if n_samples <= 0 or n_splits <= 1:
        raise ValueError("need n_samples > 0 and n_splits > 1")
    if n_splits > n_samples:
        raise ValueError("more splits than samples")
    if purge < 0 or embargo < 0:
        raise ValueError("purge/embargo must be >= 0")

    fold_sizes = [n_samples // n_splits] * n_splits
    for i in range(n_samples % n_splits):
        fold_sizes[i] += 1

    folds: list[PurgedFold] = []
    start = 0
    for size in fold_sizes:
        test_start, test_end = start, start + size  # [start, end)
        train: list[int] = []
        for idx in range(n_samples):
            if test_start <= idx < test_end:
                continue
            if test_start - purge <= idx < test_start:
                continue  # purged (pre-test label overlap)
            if test_end <= idx < test_end + embargo:
                continue  # embargoed (post-test serial correlation)
            train.append(idx)
        folds.append(
            PurgedFold(
                train_indices=tuple(train),
                test_indices=tuple(range(test_start, test_end)),
            )
        )
        start = test_end
    return tuple(folds)


def expected_max_sharpe(n_trials: int, variance_of_sr: float) -> float:
    """E[max SR] of ``n_trials`` zero-skill tries (Bailey-de Prado).

    ``E[max] ≈ sqrt(V) * ((1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e)))``
    where γ is the Euler-Mascheroni constant.
    """
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    if variance_of_sr < 0:
        raise ValueError("variance_of_sr must be >= 0")
    if n_trials == 1 or variance_of_sr == 0.0:
        return 0.0
    g = EULER_MASCHERONI
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(variance_of_sr) * ((1.0 - g) * z1 + g * z2)


def probabilistic_sharpe_ratio(
    observed_sr: float,
    *,
    benchmark_sr: float,
    n_samples: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """PSR — P(true SR > benchmark) given non-normal returns.

    ``PSR = Φ( (SR − SR*)·sqrt(T−1) / sqrt(1 − γ₃·SR + (γ₄−1)/4·SR²) )``
    """
    if n_samples < 2:
        raise ValueError("n_samples must be >= 2")
    denom_sq = (
        1.0
        - skew * observed_sr
        + (kurtosis - 1.0) / 4.0 * observed_sr**2
    )
    if denom_sq <= 0.0:
        # Pathological moments — fail-closed: no confidence.
        return 0.0
    z = (
        (observed_sr - benchmark_sr)
        * math.sqrt(n_samples - 1.0)
        / math.sqrt(denom_sq)
    )
    return _norm_cdf(z)


def deflated_sharpe_ratio(
    observed_sr: float,
    *,
    n_trials: int,
    variance_of_sr: float,
    n_samples: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """DSR — PSR against the expected-max-SR benchmark of N trials.

    The multiple-testing killer: a Sharpe that merely beats zero is
    meaningless after hundreds of experiment registry entries; it must
    beat what N random tries would have produced.
    """
    benchmark = expected_max_sharpe(n_trials, variance_of_sr)
    return probabilistic_sharpe_ratio(
        observed_sr,
        benchmark_sr=benchmark,
        n_samples=n_samples,
        skew=skew,
        kurtosis=kurtosis,
    )


def meets_anti_overfit_bar(
    dsr: float, *, floor: float = DSR_CONFIDENCE_FLOOR
) -> bool:
    """Promotion-gate predicate: DSR confidence ≥ floor."""
    return dsr >= floor


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse normal CDF via bisection on erf (deterministic, no scipy).

    Accurate to ~1e-12 over (0, 1); raises outside the open interval.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    lo, hi = -40.0, 40.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


__all__ = [
    "DSR_CONFIDENCE_FLOOR",
    "PurgedFold",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "meets_anti_overfit_bar",
    "probabilistic_sharpe_ratio",
    "purged_kfold_splits",
]
