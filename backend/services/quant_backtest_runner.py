"""Production quant-lane backtest runner factory (AE-005 boot seam).

The seam between the (import-isolated) quant evolution lane and the real PIT
data + factor-scoring layer. The lane in ``strategy_evolution`` may not import
``backend.data``; this factory — living in ``backend.services`` — is where the
real :class:`BarSource` over the AE-001 historical PIT store and the weighted
:class:`ScoreProvider` (selector factor weights → per-day quant scores) are
constructed and injected.

Tonight the factor-scoring wiring is **owner-gated** (it depends on the AE-001
historical PIT ingestion run, itself owner-gated — thousands of Tushare calls
over hours): until a ``runner_builder`` is supplied, :meth:`build` fail-closes
with :class:`BacktestDataUnavailableError`, so the 22:00 cron runs the real lane and
records an honest "data not ingested" skip rather than the old DEGRADED
"dispatcher unwired" placeholder. When the owner wires the real BarSource +
ScoreProvider, the same factory drives full-window backtests through the
promotion judgement with no further plumbing.

This module references neither the promotion-judgement engine (redline
[AB-008] confines it to ``strategy_evolution``) nor any forbidden ``backend``
subpackage of the quant lane; it only depends on the lane's injected Protocols.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.strategy_evolution.candidate_batch import CandidateBatch
from backend.strategy_evolution.quant_param_lane import (
    BacktestDataUnavailableError,
    BacktestRunnerProtocol,
)

RunnerBuilder = Callable[[str, CandidateBatch], BacktestRunnerProtocol]
"""Owner-supplied builder: ``(family, batch) -> runner`` over real PIT data."""


@dataclass(frozen=True)
class PitQuantRunnerFactory:
    """Builds a backtest runner over the PIT window for a family's batch.

    ``runner_builder`` is the owner-gated hook that constructs a real runner
    from the ingested historical PIT store + the weighted factor ScoreProvider.
    Until it is supplied, :meth:`build` raises :class:`BacktestDataUnavailableError`
    so the lane records an honest skip.
    """

    window_start: str
    window_end: str
    runner_builder: RunnerBuilder | None = None

    def window(self) -> tuple[str, str]:
        return (self.window_start, self.window_end)

    def build(self, *, family: str, batch: CandidateBatch) -> BacktestRunnerProtocol:
        if self.runner_builder is None:
            raise BacktestDataUnavailableError(
                "real PIT BarSource + weighted ScoreProvider not wired "
                "(owner-gated: AE-001 historical ingestion + factor scoring)"
            )
        return self.runner_builder(family, batch)


__all__ = [
    "PitQuantRunnerFactory",
    "RunnerBuilder",
]
