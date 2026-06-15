"""Nightly quant-parameter evolution lane runner (AE-005 / amendment §2.5).

The async orchestration that the 22:00 cron drives (``app.state.evolution_quant_lane``).
It closes the long-standing "dispatcher present, no tasks" gap: for each evolvable
family it draws a Sobol batch (honest pre-declared N), spikes in null-edge
sentinels, registers every candidate in the append-only experiment registry
(so the cumulative trial count never resets), builds a backtest runner over the
PIT window, and funnels the batch (:func:`run_batch`) into forward-shadow
mandates + an honest dashboard.

Nothing is promoted here — a survivor only earns a mandate to ENTER a 45-day
frozen forward shadow; the human pin remains the only promotion path. The
nightly candidate count is hard-bounded (amendment §2.5 — wall-clock is the real
limit for a zero-LLM quant lane, not the ¥100 LLM cap) and dropped candidates
are logged, never silently truncated.

The runner is INJECTED (a :class:`QuantRunnerFactory`) so this module stays
inside ``strategy_evolution``'s import isolation — the production factory that
touches ``backend.data`` lives in ``backend.services``. When the owner-gated PIT
history is not ingested the factory raises :class:`BacktestDataUnavailableError` and
the family is recorded as skipped (an honest outcome, not the old DEGRADED
placeholder).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import structlog

from backend.strategy_evolution.backtest_oracle import OracleVerdict
from backend.strategy_evolution.candidate_batch import CandidateBatch, assemble_batch
from backend.strategy_evolution.experiment_registry import (
    ExperimentKind,
    ExperimentRecord,
    compute_experiment_id,
)
from backend.strategy_evolution.mechanism_registry import EconomicMechanism
from backend.strategy_evolution.quant_param_lane import (
    BacktestDataUnavailableError,
    BacktestRunnerProtocol,
    BatchEvaluation,
    run_batch,
)
from backend.strategy_evolution.quant_param_search import (
    ParamExperimentProducer,
    ParamSearchError,
    ParamSet,
    assert_cumulative_n_not_reset,
)
from backend.strategy_evolution.sentinel import make_sentinels

log = structlog.get_logger(component="strategy_evolution.quant_lane_runner")

DEFAULT_MAX_CANDIDATES_PER_NIGHT = 64
"""Wall-clock bound on a night's total candidates (zero-LLM ⇒ compute-bound)."""

DEFAULT_SENTINELS_PER_FAMILY = 2
"""Null-edge control candidates spiked into each family's batch (§2.4)."""


@runtime_checkable
class QuantRunnerFactory(Protocol):
    """Builds a backtest runner for a family's batch (production touches data).

    Raises :class:`BacktestDataUnavailableError` when the PIT history needed to
    replay the window is not ingested (owner-gated) — fail-closed, never a
    fabricated result.
    """

    def build(
        self, *, family: str, batch: CandidateBatch
    ) -> BacktestRunnerProtocol: ...

    def window(self) -> tuple[str, str]:
        """The (start, end) ISO dates of the available PIT backtest window."""
        ...


@runtime_checkable
class ExperimentRegistryProtocol(Protocol):
    """The append-only experiment registry surface the runner needs."""

    async def count_trials(self, family: str | None = None) -> int: ...

    async def register(self, record: ExperimentRecord) -> bool: ...

    async def last_registered_at(self, family: str) -> dt.datetime | None: ...


@dataclass(frozen=True)
class FamilyShadowConfig:
    """One family's nightly search budget + its pre-registered mechanism."""

    family: str
    mechanism: EconomicMechanism
    n_candidates: int
    sentinel_count: int = DEFAULT_SENTINELS_PER_FAMILY


@dataclass(frozen=True)
class NightlyEvolutionReport:
    """The night's outcome — batch evaluations + honestly-logged skips/drops."""

    ran_at: dt.datetime
    seed: int
    batch_evaluations: tuple[BatchEvaluation, ...]
    skipped: tuple[tuple[str, str], ...]
    dropped_candidates: int

    @property
    def total_mandates(self) -> int:
        return sum(len(b.mandates) for b in self.batch_evaluations)

    @property
    def integrity_breached(self) -> bool:
        return any(b.sentinel_integrity_breached for b in self.batch_evaluations)

    def summary(self) -> str:
        return (
            f"quant_lane seed={self.seed} "
            f"families={len(self.batch_evaluations)} "
            f"mandates={self.total_mandates} "
            f"skipped={len(self.skipped)} dropped={self.dropped_candidates} "
            f"integrity_ok={not self.integrity_breached}"
        )


@dataclass(frozen=True)
class QuantParamEvolutionLane:
    """Boot-wired nightly lane (frozen so its wiring cannot drift)."""

    registry: ExperimentRegistryProtocol
    runner_factory: QuantRunnerFactory
    families: tuple[FamilyShadowConfig, ...]
    seed: int
    max_candidates_per_night: int = DEFAULT_MAX_CANDIDATES_PER_NIGHT

    async def run_nightly(self, *, now: dt.datetime) -> NightlyEvolutionReport:
        """Run every family's batch through the funnel (fail-closed per family)."""
        window_start, window_end = self.runner_factory.window()
        calendar_start = now.date().isoformat()
        budget = self.max_candidates_per_night
        dropped = 0

        evaluations: list[BatchEvaluation] = []
        skipped: list[tuple[str, str]] = []

        for cfg in self.families:
            if budget <= 0:
                dropped += cfg.n_candidates
                skipped.append((cfg.family, "night candidate budget exhausted"))
                log.warning(
                    "quant_lane_family_dropped_budget",
                    family=cfg.family,
                    dropped=cfg.n_candidates,
                )
                continue
            n_real = min(cfg.n_candidates, budget)
            if n_real < cfg.n_candidates:
                dropped += cfg.n_candidates - n_real
                log.warning(
                    "quant_lane_family_truncated",
                    family=cfg.family,
                    requested=cfg.n_candidates,
                    ran=n_real,
                )
            budget -= n_real
            try:
                evaluation = await self._run_family(
                    cfg=cfg,
                    n_real=n_real,
                    window_start=window_start,
                    window_end=window_end,
                    calendar_start=calendar_start,
                    now=now,
                )
                evaluations.append(evaluation)
            except BacktestDataUnavailableError as exc:
                skipped.append((cfg.family, f"data_unavailable: {exc}"))
                log.info(
                    "quant_lane_family_skipped_no_data",
                    family=cfg.family,
                    reason=str(exc),
                )
            except ParamSearchError as exc:
                skipped.append((cfg.family, f"search_error: {exc}"))
                log.warning(
                    "quant_lane_family_skipped_search_error",
                    family=cfg.family,
                    error=str(exc),
                )

        report = NightlyEvolutionReport(
            ran_at=now,
            seed=self.seed,
            batch_evaluations=tuple(evaluations),
            skipped=tuple(skipped),
            dropped_candidates=dropped,
        )
        log.info("quant_lane_nightly_complete", summary=report.summary())
        return report

    async def _run_family(
        self,
        *,
        cfg: FamilyShadowConfig,
        n_real: int,
        window_start: str,
        window_end: str,
        calendar_start: str,
        now: dt.datetime,
    ) -> BatchEvaluation:
        n_before = await self.registry.count_trials(cfg.family)
        # Honest N: the append-only registry only grows; a declared count below
        # what the registry already holds is tampering (fail-closed).
        assert_cumulative_n_not_reset(
            declared_cumulative_n=n_before, registry_trial_count=n_before
        )
        # Advance the Sobol seed by the cumulative trial count so each night
        # EXPLORES fresh candidates instead of re-drawing the identical batch
        # forever (a constant seed → idempotent registry skips → the declared N
        # would inflate while the registry stays frozen, and the search would
        # never move). The derivation is deterministic given the registry state,
        # so a same-night re-run still reproduces the batch bit-for-bit.
        effective_seed = self.seed + n_before
        producer = ParamExperimentProducer(family=cfg.family)
        real = producer.produce(
            seed=effective_seed, n_candidates=n_real, mechanism=cfg.mechanism
        )
        sentinels = make_sentinels(
            family=cfg.family, count=cfg.sentinel_count, seed=effective_seed
        )
        cumulative = n_before + len(real)
        batch = assemble_batch(
            family=cfg.family,
            seed=effective_seed,
            declared_n=n_real,
            window_start=window_start,
            window_end=window_end,
            cumulative_n_at_creation=cumulative,
            mechanism=cfg.mechanism,
            real_candidates=real,
            sentinels=sentinels,
        )
        runner = self.runner_factory.build(family=cfg.family, batch=batch)
        evaluation = run_batch(
            batch=batch,
            runner=runner,
            oracle_verdict=OracleVerdict.ORACLE_UNAVAILABLE,
            now=now,
            calendar_start=calendar_start,
            days_since_last_promotion=None,
        )
        await self._register_experiments(
            batch=batch,
            evaluation=evaluation,
            n_obs=runner.observation_count(),
            now=now,
        )
        return evaluation

    async def _register_experiments(
        self,
        *,
        batch: CandidateBatch,
        evaluation: BatchEvaluation,
        n_obs: int,
        now: dt.datetime,
    ) -> None:
        """Append every REAL candidate to the registry (failures included).

        The cumulative trial count is the honest ``N`` the deflated-Sharpe gate
        deflates against; hiding failures would turn the search into a
        multiple-testing machine. Sentinels are NOT registered as trials (they
        are a control group, not hypotheses under test).
        """
        survived_by_hash = {e.param_hash: e.survived for e in evaluation.evaluations}
        excess_by_hash = {
            e.param_hash: (e.excess_sharpe or 0.0) for e in evaluation.evaluations
        }
        for candidate in batch.real_candidates:
            record = self._experiment_record(
                candidate=candidate,
                window_start=batch.window_start,
                window_end=batch.window_end,
                n_obs=n_obs,
                success=survived_by_hash.get(candidate.param_hash, False),
                excess_sharpe=excess_by_hash.get(candidate.param_hash, 0.0),
                now=now,
            )
            await self.registry.register(record)

    @staticmethod
    def _experiment_record(
        *,
        candidate: ParamSet,
        window_start: str,
        window_end: str,
        n_obs: int,
        success: bool,
        excess_sharpe: float,
        now: dt.datetime,
    ) -> ExperimentRecord:
        hypothesis = (
            f"quant param search: {candidate.family} via "
            f"{candidate.mechanism.value if candidate.mechanism else 'none'}"
        )
        param_space = candidate.param_space_strings()
        experiment_id = compute_experiment_id(
            kind=ExperimentKind.THRESHOLD_PARAM,
            family=candidate.family,
            hypothesis=hypothesis,
            artifact_hash=candidate.param_hash,
            param_space=param_space,
            window_start=window_start,
            window_end=window_end,
        )
        return ExperimentRecord(
            experiment_id=experiment_id,
            kind=ExperimentKind.THRESHOLD_PARAM,
            family=candidate.family,
            hypothesis=hypothesis,
            artifact_hash=candidate.param_hash,
            param_space=param_space,
            window_start=window_start,
            window_end=window_end,
            trading_days=n_obs,
            sample_count=0,
            metrics={"excess_sharpe": excess_sharpe},
            ci_low=None,
            ci_high=None,
            success=success,
            registered_at=now,
        )


def default_first_batch_families(
    *, n_candidates: int = 16
) -> tuple[FamilyShadowConfig, ...]:
    """The AE-005 first batch (owner card ③): selector + allocation weights.

    Each family carries a single pre-registered economic mechanism — the
    hypothesis under test for the night's batch.
    """
    from backend.strategy_evolution.quant_param_search import (
        SELECTOR_WEIGHTS_FAMILY,
        THEME_TIER_WEIGHTS_FAMILY,
        VALUE_SLOT_QUOTA_FAMILY,
    )

    return (
        FamilyShadowConfig(
            family=SELECTOR_WEIGHTS_FAMILY,
            mechanism=EconomicMechanism.MOMENTUM_CONTINUATION,
            n_candidates=n_candidates,
        ),
        FamilyShadowConfig(
            family=VALUE_SLOT_QUOTA_FAMILY,
            mechanism=EconomicMechanism.VALUE_PREMIUM,
            n_candidates=min(n_candidates, 3),
        ),
        FamilyShadowConfig(
            family=THEME_TIER_WEIGHTS_FAMILY,
            mechanism=EconomicMechanism.DIVERSIFICATION,
            n_candidates=n_candidates,
        ),
    )


__all__ = [
    "DEFAULT_MAX_CANDIDATES_PER_NIGHT",
    "DEFAULT_SENTINELS_PER_FAMILY",
    "ExperimentRegistryProtocol",
    "FamilyShadowConfig",
    "NightlyEvolutionReport",
    "QuantParamEvolutionLane",
    "QuantRunnerFactory",
    "default_first_batch_families",
]
