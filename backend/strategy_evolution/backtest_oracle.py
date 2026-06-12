"""rqalpha differential backtest oracle (R-002).

rqalpha is the ONLY sanctioned authoritative A-share execution backtest
(P2-2-amendment-2026-05-24 + backtest dossier §107): a **test-time
differential oracle** that cross-checks MockBroker shadow results —
NEVER a second execution truth, NEVER on the realtime path. MockBroker
stays the single mirror; a divergence here is a bug signal in one of
the two engines, surfaced for human investigation.

LICENSE (read 2026-06-12, acceptance requirement — GitHub reports
``NOASSERTION``): rqalpha is dual-licensed — Apache 2.0 for
non-commercial use, with COMMERCIAL USE FORBIDDEN without written
Ricequant authorization (public@ricequant.com). QuantMind is a personal
simulation-research system with real order placement permanently
forbidden → non-commercial, Apache 2.0 terms apply. Consequences baked
into this module:

* rqalpha is an OPTIONAL runtime dependency — never vendored, no code
  copied (the Y-001 "NOASSERTION 不抄" discipline);
* the import is lazy and confined to :class:`RqalphaBacktestRunner`;
  an absent install degrades to ``ORACLE_UNAVAILABLE`` (fail-closed:
  unavailable is NOT a pass — the promotion gate must treat it as
  "not cross-checked");
* if QuantMind ever commercialises, rqalpha must be re-licensed or
  removed (tracked here so the constraint is greppable).

Realtime isolation is enforced three ways: ruff TID251 already bans
this package from importing the trading stack; the redline ``[R-002]``
grep confines the string ``rqalpha`` to this file; and
``tests/strategy_evolution/test_module_contract.py`` AST-verifies no
module outside ``backend/strategy_evolution`` imports this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import structlog
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger(component="strategy_evolution.backtest_oracle")

EQUITY_TOLERANCE_BPS = 25.0
"""Max per-day |equity diff| in basis points of the MockBroker equity
before the day counts as divergent. The two engines legitimately differ
in friction detail (slippage model, rounding); 25bp is far above any
rounding noise yet far below a wrong-fill error (a single mispriced
A-share lot moves >100bp on a ¥100k account)."""

DIVERGENT_DAY_RATIO_CEILING = 0.05
"""A run is CONSISTENT only when ≤5% of compared days diverge — a lone
boundary day (e.g. a limit-up no-fill modelled differently) does not
fail the cross-check, a systematic drift does."""

MIN_OVERLAP_RATIO = 0.9
"""Codex R-002 P1 — the shared dates must cover ≥90% of the MockBroker
curve (the authoritative shadow window). A truncated / wrong-calendar
oracle curve that overlaps on a single quiet day must NOT produce
CONSISTENT while most of the window went un-cross-checked."""


class OracleVerdict(StrEnum):
    """Differential cross-check outcome (fail-closed semantics)."""

    CONSISTENT = "consistent"
    DIVERGENT = "divergent"
    ORACLE_UNAVAILABLE = "oracle_unavailable"
    """rqalpha not installed / runner failed. NOT a pass: the promotion
    gate must treat this as "not cross-checked" (fail-closed)."""

    INSUFFICIENT_OVERLAP = "insufficient_overlap"
    """The two equity series share no comparable dates."""


class EquityDay(BaseModel):
    """One day of an equity series from either engine."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    total_equity: float = Field(gt=0.0)


class BacktestRunResult(BaseModel):
    """Engine-agnostic backtest output the differential consumes."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    engine: str = Field(min_length=1, max_length=64)
    engine_version: str = Field(min_length=1, max_length=64)
    strategy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    equity_curve: tuple[EquityDay, ...]
    fill_count: int = Field(ge=0)


class DayDiff(BaseModel):
    """Per-day differential row."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    mock_equity: float
    oracle_equity: float
    diff_bps: float
    divergent: bool


class DifferentialReport(BaseModel):
    """Outcome of one MockBroker-vs-oracle cross-check."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    strategy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: OracleVerdict
    compared_days: int = Field(ge=0)
    divergent_days: int = Field(ge=0)
    max_abs_diff_bps: float = Field(ge=0.0)
    fill_count_mock: int = Field(ge=0)
    fill_count_oracle: int = Field(ge=0)
    day_diffs: tuple[DayDiff, ...] = Field(default_factory=tuple)
    detail: str = Field(default="", max_length=512)


@dataclass(frozen=True)
class BacktestSpec:
    """What to backtest — engine-agnostic, content-addressed.

    ``strategy_hash`` is the LiveArtifactRegistry STRATEGY_CODE
    identifier; ``strategy_source_path`` points at the artifact the
    hash was computed over (the runner re-verifies, fail-closed).
    """

    strategy_hash: str
    strategy_source_path: str
    start_date: str
    end_date: str
    initial_capital: float


class OracleUnavailableError(RuntimeError):
    """rqalpha (or the configured oracle engine) cannot run."""


@runtime_checkable
class BacktestRunner(Protocol):
    """Injected oracle engine — production wires rqalpha, tests fake."""

    async def run(self, spec: BacktestSpec) -> BacktestRunResult: ...


def compare_equity_curves(
    *,
    strategy_hash: str,
    mock: BacktestRunResult,
    oracle: BacktestRunResult,
    tolerance_bps: float = EQUITY_TOLERANCE_BPS,
    divergent_ratio_ceiling: float = DIVERGENT_DAY_RATIO_CEILING,
) -> DifferentialReport:
    """Pure differential: per-day equity diff over the shared dates.

    Days present on only one side are excluded from the bps comparison
    (calendar disagreement is an engine-config issue, not an execution
    divergence), but the shared dates must cover ≥
    :data:`MIN_OVERLAP_RATIO` of the MockBroker curve — a truncated
    oracle run must not pass on the sliver it did compute (codex P1).

    Raises:
        ValueError: either input's ``strategy_hash`` differs from the
            requested one — comparing curves of different artifacts is
            a caller bug that must fail loud, never CONSISTENT.
    """
    for label, result in (("mock", mock), ("oracle", oracle)):
        if result.strategy_hash != strategy_hash:
            raise ValueError(
                f"{label} result is for strategy "
                f"{result.strategy_hash[:12]}, not the requested "
                f"{strategy_hash[:12]} — refusing cross-artifact compare"
            )
    mock_by_date = {d.trade_date: d.total_equity for d in mock.equity_curve}
    oracle_by_date = {
        d.trade_date: d.total_equity for d in oracle.equity_curve
    }
    shared = sorted(set(mock_by_date) & set(oracle_by_date))
    overlap_ratio = (
        len(shared) / len(mock_by_date) if mock_by_date else 0.0
    )
    if not shared or overlap_ratio < MIN_OVERLAP_RATIO:
        return DifferentialReport(
            strategy_hash=strategy_hash,
            verdict=OracleVerdict.INSUFFICIENT_OVERLAP,
            compared_days=len(shared),
            divergent_days=0,
            max_abs_diff_bps=0.0,
            fill_count_mock=mock.fill_count,
            fill_count_oracle=oracle.fill_count,
            detail=(
                f"shared dates cover {overlap_ratio:.1%} of the "
                f"MockBroker window (< {MIN_OVERLAP_RATIO:.0%}); the "
                f"cross-check did not see the full run"
            ),
        )

    diffs: list[DayDiff] = []
    for day in shared:
        mock_eq = mock_by_date[day]
        oracle_eq = oracle_by_date[day]
        diff_bps = abs(oracle_eq - mock_eq) / mock_eq * 10_000.0
        diffs.append(
            DayDiff(
                trade_date=day,
                mock_equity=mock_eq,
                oracle_equity=oracle_eq,
                diff_bps=diff_bps,
                divergent=diff_bps > tolerance_bps,
            )
        )
    divergent_days = sum(1 for d in diffs if d.divergent)
    ratio = divergent_days / len(diffs)
    verdict = (
        OracleVerdict.CONSISTENT
        if ratio <= divergent_ratio_ceiling
        else OracleVerdict.DIVERGENT
    )
    return DifferentialReport(
        strategy_hash=strategy_hash,
        verdict=verdict,
        compared_days=len(diffs),
        divergent_days=divergent_days,
        max_abs_diff_bps=max(d.diff_bps for d in diffs),
        fill_count_mock=mock.fill_count,
        fill_count_oracle=oracle.fill_count,
        day_diffs=tuple(d for d in diffs if d.divergent),
        detail=(
            f"{divergent_days}/{len(diffs)} days beyond "
            f"{tolerance_bps}bps"
        ),
    )


async def run_differential_check(
    *,
    spec: BacktestSpec,
    mock_result: BacktestRunResult,
    oracle_runner: BacktestRunner,
) -> DifferentialReport:
    """Run the oracle and compare against the MockBroker result.

    An oracle failure (engine missing, run crash) degrades to
    ``ORACLE_UNAVAILABLE`` — never a silent pass, never an exception
    that could freeze the evolution lane (X-005 decoupling). Hash
    discipline (codex P1): a ``mock_result`` for a different artifact
    is a caller bug → raise; an oracle result for a different artifact
    (cached / misconfigured runner) degrades to ORACLE_UNAVAILABLE.

    Raises:
        ValueError: ``mock_result.strategy_hash`` differs from the spec.
    """
    if mock_result.strategy_hash != spec.strategy_hash:
        raise ValueError(
            f"mock_result is for strategy "
            f"{mock_result.strategy_hash[:12]}, not the requested "
            f"{spec.strategy_hash[:12]}"
        )
    try:
        oracle_result = await oracle_runner.run(spec)
    except OracleUnavailableError as exc:
        log.warning(
            "backtest_oracle_unavailable",
            strategy_hash=spec.strategy_hash[:12],
            error=str(exc),
        )
        return DifferentialReport(
            strategy_hash=spec.strategy_hash,
            verdict=OracleVerdict.ORACLE_UNAVAILABLE,
            compared_days=0,
            divergent_days=0,
            max_abs_diff_bps=0.0,
            fill_count_mock=mock_result.fill_count,
            fill_count_oracle=0,
            detail=str(exc)[:512],
        )
    except Exception as exc:  # noqa: BLE001 — oracle is best-effort
        log.warning(
            "backtest_oracle_run_failed",
            strategy_hash=spec.strategy_hash[:12],
            error=str(exc),
        )
        return DifferentialReport(
            strategy_hash=spec.strategy_hash,
            verdict=OracleVerdict.ORACLE_UNAVAILABLE,
            compared_days=0,
            divergent_days=0,
            max_abs_diff_bps=0.0,
            fill_count_mock=mock_result.fill_count,
            fill_count_oracle=0,
            detail=f"oracle run raised: {exc}"[:512],
        )
    if oracle_result.strategy_hash != spec.strategy_hash:
        log.warning(
            "backtest_oracle_hash_mismatch",
            requested=spec.strategy_hash[:12],
            returned=oracle_result.strategy_hash[:12],
        )
        return DifferentialReport(
            strategy_hash=spec.strategy_hash,
            verdict=OracleVerdict.ORACLE_UNAVAILABLE,
            compared_days=0,
            divergent_days=0,
            max_abs_diff_bps=0.0,
            fill_count_mock=mock_result.fill_count,
            fill_count_oracle=oracle_result.fill_count,
            detail=(
                f"oracle returned a result for strategy "
                f"{oracle_result.strategy_hash[:12]}, not the requested "
                f"{spec.strategy_hash[:12]} (cached/misconfigured runner)"
            ),
        )
    return compare_equity_curves(
        strategy_hash=spec.strategy_hash,
        mock=mock_result,
        oracle=oracle_result,
    )


class RqalphaBacktestRunner:
    """The production rqalpha adapter (lazy import; optional dep).

    rqalpha is NOT installed by default (NOASSERTION license — see the
    module docstring). Until the owner installs it (non-commercial
    Apache 2.0 terms), :meth:`run` raises
    :class:`OracleUnavailableError` and the differential degrades to
    ``ORACLE_UNAVAILABLE``. The adapter is deliberately thin: strategy
    translation into an rqalpha run config belongs to Phase AB's
    experiment harness, which owns the data bundle + Mod configuration.
    """

    ENGINE = "rqalpha"

    async def run(self, spec: BacktestSpec) -> BacktestRunResult:
        try:
            import rqalpha  # type: ignore[import-not-found]  # noqa: F401 — availability probe (optional dep)
        except ImportError as exc:
            raise OracleUnavailableError(
                "rqalpha is not installed (optional dependency; "
                "NOASSERTION license — non-commercial Apache 2.0 use "
                "only, never vendored). Install + configure the data "
                "bundle to enable the differential oracle."
            ) from exc
        # Phase AB wires the actual run config (data bundle path, Mod
        # set mirroring MockBroker friction, strategy file translation).
        # Landing a half-configured run here would produce a curve that
        # diverges for config reasons and train operators to ignore the
        # oracle — fail-closed until AB completes the harness.
        raise OracleUnavailableError(
            "rqalpha run harness lands with Phase AB (experiment "
            "pipeline owns the data bundle + Mod config); the "
            "differential stays ORACLE_UNAVAILABLE until then."
        )


__all__ = [
    "DIVERGENT_DAY_RATIO_CEILING",
    "EQUITY_TOLERANCE_BPS",
    "MIN_OVERLAP_RATIO",
    "BacktestRunResult",
    "BacktestRunner",
    "BacktestSpec",
    "DayDiff",
    "DifferentialReport",
    "EquityDay",
    "OracleUnavailableError",
    "OracleVerdict",
    "RqalphaBacktestRunner",
    "compare_equity_curves",
    "run_differential_check",
]
