"""AcceptanceReport — 45 trading-day rolling acceptance gate (E-008 / P0-6).

The acceptance report is the single source of truth for "is the system
ready to flip ``FEISHU_INTERACTIVE_ENABLED=true``?". P0-6 locks an
8-metric gate evaluated on a 45-trading-day rolling window:

* **5 stability metrics** (CLAUDE.md §2.8):
  - instruction_completion_rate ≥ 0.95
  - execution_report_accuracy_rate ≥ 0.99
  - data_missing_rate ≤ 0.01
  - llm_timeout_rate ≤ 0.05
  - signal_generation_rate ≥ 0.95
* **3 strategy metrics**:
  - max_drawdown ≤ 0.08
  - pnl_cny ≥ 0
  - csi300_excess_pct ≥ 0

A report passes only when ALL eight gates clear. The
:meth:`AcceptanceService.can_switch_to_feishu_on` method is the
authoritative gate — env-var / CLI bypass is forbidden (P0-6 §2 redline 5).

Daily 16:00:30 upsert: the BrokerScheduler EOD chain plugs the
acceptance hook in here, but the report can also be computed ad-hoc
via :meth:`AcceptanceService.compute`. Interruption handling:
P0 system-level interruptions reset the window; reconciliation freeze
PAUSES the window without resetting (CLAUDE.md §2.8).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.data.trading_calendar import compute_window_back, is_trading_day
from backend.utils.trading_hours import SHANGHAI

log = structlog.get_logger(component="services.acceptance_report")


# ---------------------------------------------------------------------------
# Locked thresholds — change requires an amendment doc + restart (P0-6 §2).
# ---------------------------------------------------------------------------


INSTRUCTION_COMPLETION_FLOOR = 0.95
EXECUTION_REPORT_ACCURACY_FLOOR = 0.99
DATA_MISSING_CEILING = 0.01
LLM_TIMEOUT_CEILING = 0.05
SIGNAL_GENERATION_FLOOR = 0.95

MAX_DRAWDOWN_CEILING = 0.08
PNL_FLOOR = 0.0
CSI300_EXCESS_FLOOR = 0.0

WINDOW_TRADING_DAYS = 45
"""P0-6 45-trading-day rolling window (uses backend.utils.holiday_loader)."""


class AcceptanceOutcome(StrEnum):
    """Locked outcome enum surfaced on every AcceptanceReport."""

    PASS = "PASS"
    FAIL = "FAIL"
    PAUSED = "PAUSED"
    """The reconciliation-ticket-pause path: window did not progress,
    no decision rendered (no FAIL stamp on the window)."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    """Window does not yet have 45 trading days of data."""


class AcceptanceMetric(BaseModel):
    """Single 8-metric gate row inside an :class:`AcceptanceReport`."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    value: float
    threshold: float
    passed: bool
    direction: str = Field(pattern=r"^(at_least|at_most)$")
    """``at_least`` means ``value >= threshold`` passes; ``at_most`` means
    ``value <= threshold`` passes. Lock + audit consumers rely on the
    direction tag, not on re-deriving the comparison from the name."""


class WindowResetState(BaseModel):
    """Metadata for the most recent reset event affecting the rolling window.

    J-004 — populated by :class:`AcceptanceService.record_reset` whenever
    one of the 5 P0-6 §1 system-level interruptions fires:
    ``MARKET_DATA_OUTAGE_30MIN`` / ``LLM_FULL_STOP_1H`` /
    ``MOCK_BROKER_CORRUPTION`` / ``STATE_MACHINE_ILLEGAL_TRANSITION`` /
    ``LONG_CONN_OUTAGE_4H``. ``reconciliation freeze`` (P0-6 §1) is
    deliberately excluded — it PAUSES the window without resetting.

    When both fields are ``None`` no reset has been observed in the
    current process. Persistence across restart is intentionally
    out-of-scope for J-004 (see the J-006 runbook).
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    last_reset_at: dt.datetime | None = None
    last_reset_reason: str | None = Field(default=None, max_length=64)


class AcceptanceReport(BaseModel):
    """One row of the ``acceptance_reports`` collection (P0-6)."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    report_id: UUID = Field(default_factory=uuid4)
    computed_at: dt.datetime
    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    trading_days_in_window: int = Field(ge=0, le=WINDOW_TRADING_DAYS)
    outcome: AcceptanceOutcome
    metrics: tuple[AcceptanceMetric, ...]
    notes: str = Field(default="", max_length=256)
    reset_state: WindowResetState = Field(default_factory=WindowResetState)

    @model_validator(mode="after")
    def _check_window(self) -> AcceptanceReport:
        start = dt.date.fromisoformat(self.window_start)
        end = dt.date.fromisoformat(self.window_end)
        if start > end:
            raise ValueError("window_start must be <= window_end")
        return self


# ---------------------------------------------------------------------------
# Computation inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StabilityCounters:
    """Window-aggregate counters fed by the upstream collectors.

    Each pair is (numerator, denominator) so the service can compute
    the ratio in one place and produce a deterministic ``AcceptanceMetric``
    row. Zero denominators degrade to INSUFFICIENT_DATA so a fresh
    deploy can't accidentally pass acceptance.
    """

    completed_instructions: int
    total_instructions: int

    accurate_reports: int
    total_reports: int

    data_missing_ticks: int
    total_data_ticks: int

    llm_timeout_calls: int
    total_llm_calls: int

    generated_signal_days: int
    expected_signal_days: int


@dataclass(frozen=True)
class StrategyCounters:
    """Strategy-side aggregates: drawdown, PnL, CSI300 excess."""

    max_drawdown_pct: float
    pnl_cny: float
    csi300_excess_pct: float


@dataclass(frozen=True)
class AcceptanceComputeInput:
    trade_date: dt.date
    now: dt.datetime
    stability: StabilityCounters
    strategy: StrategyCounters
    reconciliation_paused: bool = False
    """Set by upstream when an OPEN/EXPIRED reconciliation ticket is
    pausing the acceptance window (CLAUDE.md §2.8). The service still
    builds a report row but tags it ``PAUSED`` so the UI can render
    the pause provenance."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _build_metric(
    name: str,
    *,
    value: float,
    threshold: float,
    direction: str,
) -> AcceptanceMetric:
    if not math.isfinite(value):
        passed = False
    elif direction == "at_least":
        passed = value >= threshold
    else:
        passed = value <= threshold
    return AcceptanceMetric(
        name=name,
        value=round(value, 6) if math.isfinite(value) else float("nan"),
        threshold=threshold,
        passed=passed,
        direction=direction,
    )


# ---------------------------------------------------------------------------
# Tier-aware go-live gate (P0-6-amendment-2026-05-25 §2.2 / §2.3 / §2.4)
# ---------------------------------------------------------------------------


class GoLiveTier(StrEnum):
    """The two go-live tiers of the acceptance gate.

    PILOT — bounded double-line pilot on the SIM account with owner-in-loop
    manual execution (amendment §2.3, 11-condition minimal set). FULL — the
    original P0-6 45-trading-day rolling window + 5 stability + 3 strategy
    gates (amendment §2.4). A PILOT pass NEVER satisfies FULL (§2.4 / §4 #2).
    """

    PILOT = "pilot"
    FULL = "full"


@dataclass(frozen=True)
class GateDecision:
    """Result of :meth:`AcceptanceService.can_switch_to_feishu_on`.

    ``allowed`` is the ONLY authoritative switch sanction (P0-6 §2 redline 5 —
    no env-var / CLI bypass). ``reasons`` names every unmet condition so the
    caller can surface a fail-closed explanation; it is empty when allowed.
    ``tier`` echoes the evaluated tier so a PILOT decision can never be
    mistaken for a FULL one downstream (amendment §4 #2).
    """

    tier: GoLiveTier
    allowed: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        """Truthiness == ``allowed`` (fail-closed defence-in-depth).

        All known callers read ``.allowed`` explicitly, but a missed/legacy
        ``if await gate.can_switch_to_feishu_on():`` would otherwise treat the
        always-truthy dataclass as a pass. Mirroring ``allowed`` here means
        such a caller still fails closed on a denied decision (Codex U-D2 P1).
        """
        return self.allowed


@runtime_checkable
class PilotReadinessProbeProtocol(Protocol):
    """Duck-typed probe consulted for the PILOT branch.

    Kept as a Protocol so ``acceptance_report`` does not import the concrete
    :mod:`backend.services.pilot_readiness` (which reaches into the broker /
    reconciliation surfaces) — the acceptance path stays import-clean of those
    and of the LLM stack (CLAUDE.md §2.8). ``evaluate`` is async (it does
    reconciliation / data-quality / cost-guard I/O) and returns the tuple of
    unmet PILOT condition reasons; empty == all 11 conditions met.
    """

    async def evaluate(self) -> tuple[str, ...]: ...


class AcceptanceService:
    """Computes + persists AcceptanceReport rows.

    Persistence is abstracted via a duck-typed repository so unit tests
    can use an in-memory list. The service is intentionally narrow —
    it owns the 45-day rolling window arithmetic, the 8 threshold gates
    and the ``can_switch_to_feishu_on`` lookup, and nothing else.

    J-004 extension — :meth:`record_reset` clamps the rolling window
    start to the reset wall-clock so subsequent ``compute()`` calls
    report INSUFFICIENT_DATA until 45 fresh trading days accumulate.
    """

    def __init__(
        self,
        repository: AcceptanceRepository | None = None,
    ) -> None:
        self._repo = repository or InMemoryAcceptanceRepository()
        self._reset_state: WindowResetState = WindowResetState()
        self._pilot_probe: PilotReadinessProbeProtocol | None = None

    def set_pilot_probe(
        self, probe: PilotReadinessProbeProtocol | None
    ) -> None:
        """Wire (or clear) the PILOT readiness probe at startup.

        Wiring-time setter (mirrors :meth:`set_reset_state`): the concrete
        probe needs live broker / reconciliation / cost-guard refs that are
        only built after the orchestration layer is up — later than this
        service is constructed. Simulation-only / unit-test contexts leave it
        ``None`` → PILOT is fail-closed there (no probe == not ready).
        """
        self._pilot_probe = probe

    def record_reset(self, *, when: dt.datetime, reason: str) -> None:
        """Mark the rolling window as reset; subsequent compute() starts fresh.

        Called by :class:`backend.services.reset_trigger_detector.ResetTriggerDetector`
        when any of the 5 P0-6 §1 system-level interruptions fires.
        ``when`` is the wall-clock instant the interruption was confirmed;
        ``reason`` is the locked trigger identifier (see
        :class:`ResetTriggerType`). The next ``compute()`` clamps
        ``window_start`` to ``when.date()`` so the rolling window
        restarts and the 45-day counter is zeroed in effect.

        Codex cycle 5 P2 fix — monotonic guard: if the current
        ``_reset_state.last_reset_at`` is newer than ``when``, the
        call is a no-op. Two reset triggers can race through
        ``ResetTriggerDetector._fire`` (audit-first + Feishu await),
        and an older trigger whose dispatch finished later must not
        rewind the clamp.
        """
        if when.tzinfo is None:
            raise ValueError("record_reset requires an aware datetime")
        if not reason or len(reason) > 64:
            raise ValueError("reason must be a non-empty <=64-char identifier")
        if (
            self._reset_state.last_reset_at is not None
            and when <= self._reset_state.last_reset_at
        ):
            log.info(
                "acceptance_window_reset_ignored_older",
                reason=reason,
                when=when.isoformat(),
                current_last_reset_at=(
                    self._reset_state.last_reset_at.isoformat()
                ),
            )
            return
        self._reset_state = WindowResetState(
            last_reset_at=when,
            last_reset_reason=reason,
        )
        log.info(
            "acceptance_window_reset",
            reason=reason,
            when=when.isoformat(),
        )

    def set_reset_state(self, state: WindowResetState) -> None:
        """Replace the in-memory reset state (used by startup hydration).

        Codex cycle 2 P1 fix — the ``_reset_state`` was process-local,
        so a restart after a J-004 reset (but before 45 fresh trading
        days accumulated) silently dropped the clamp and let the next
        ``compute()`` use pre-reset history. The lifespan now hydrates
        from the audit trail: scan for the most recent
        ``SYSTEM_INTERRUPTED`` event with
        ``reason_namespace='acceptance_reset_trigger'`` and feed the
        resulting state in here before the first ``compute()`` runs.

        Codex cycle 5 P2 fix — monotonic guard: hydration must pick
        the max-timestamp event, but as defence in depth the setter
        also refuses to move the state backwards in time when a
        newer state is already in place.
        """
        if state.last_reset_at is not None and state.last_reset_at.tzinfo is None:
            raise ValueError(
                "WindowResetState.last_reset_at must be timezone-aware"
            )
        if (
            state.last_reset_at is not None
            and self._reset_state.last_reset_at is not None
            and state.last_reset_at < self._reset_state.last_reset_at
        ):
            log.info(
                "acceptance_window_reset_state_set_ignored_older",
                attempted_at=state.last_reset_at.isoformat(),
                current_last_reset_at=(
                    self._reset_state.last_reset_at.isoformat()
                ),
            )
            return
        self._reset_state = state

    def reset_state(self) -> WindowResetState:
        """Return the most recent reset metadata (empty when none observed)."""
        return self._reset_state

    def compute(self, payload: AcceptanceComputeInput) -> AcceptanceReport:
        """Build an :class:`AcceptanceReport` from the supplied counters."""
        end = payload.trade_date
        if not is_trading_day(end):
            log.warning(
                "acceptance_compute_non_trading_day",
                trade_date=end.isoformat(),
            )
        start = compute_window_back(end, WINDOW_TRADING_DAYS)

        # J-004 — clamp window_start to the most recent reset wall-clock
        # date. Mathematically equivalent to "force zero the 45-day
        # counter": any data before the reset is excluded from the
        # acceptance window so the rolling window restarts fresh.
        #
        # Codex cycle 3 P2 fix — use Asia/Shanghai for the clamp date so
        # a reset at e.g. 2026-05-14T16:30:00Z (= 2026-05-15 Shanghai)
        # clamps to the correct trade date rather than the UTC-derived
        # 2026-05-14 (which would let one pre-reset day leak in).
        if self._reset_state.last_reset_at is not None:
            reset_date = self._reset_state.last_reset_at.astimezone(
                SHANGHAI
            ).date()
            if reset_date > start:
                start = reset_date

        actual_days = _count_trading_days_inclusive(start, end)

        if payload.reconciliation_paused:
            return AcceptanceReport(
                computed_at=payload.now,
                trade_date=end.isoformat(),
                window_start=start.isoformat(),
                window_end=end.isoformat(),
                trading_days_in_window=actual_days,
                outcome=AcceptanceOutcome.PAUSED,
                metrics=(),
                notes="acceptance paused — reconciliation OPEN/EXPIRED",
                reset_state=self._reset_state,
            )

        if actual_days < WINDOW_TRADING_DAYS:
            return AcceptanceReport(
                computed_at=payload.now,
                trade_date=end.isoformat(),
                window_start=start.isoformat(),
                window_end=end.isoformat(),
                trading_days_in_window=actual_days,
                outcome=AcceptanceOutcome.INSUFFICIENT_DATA,
                metrics=(),
                notes=(
                    f"window contains {actual_days} trading days; "
                    f"need {WINDOW_TRADING_DAYS}"
                ),
                reset_state=self._reset_state,
            )

        metrics = self._build_metrics(payload)
        all_pass = all(m.passed for m in metrics)
        outcome = AcceptanceOutcome.PASS if all_pass else AcceptanceOutcome.FAIL
        return AcceptanceReport(
            computed_at=payload.now,
            trade_date=end.isoformat(),
            window_start=start.isoformat(),
            window_end=end.isoformat(),
            trading_days_in_window=actual_days,
            outcome=outcome,
            metrics=tuple(metrics),
            notes="",
            reset_state=self._reset_state,
        )

    def _build_metrics(
        self, payload: AcceptanceComputeInput
    ) -> list[AcceptanceMetric]:
        s = payload.stability
        # Insufficient data on any stability denominator → fail-closed
        def _ratio(num: int, den: int) -> float:
            value = _safe_ratio(num, den)
            return value if value is not None else 0.0

        instr = _ratio(s.completed_instructions, s.total_instructions)
        report_acc = _ratio(s.accurate_reports, s.total_reports)
        data_miss = _ratio(s.data_missing_ticks, s.total_data_ticks)
        llm_to = _ratio(s.llm_timeout_calls, s.total_llm_calls)
        sig_gen = _ratio(s.generated_signal_days, s.expected_signal_days)

        return [
            _build_metric(
                "instruction_completion_rate",
                value=instr,
                threshold=INSTRUCTION_COMPLETION_FLOOR,
                direction="at_least",
            ),
            _build_metric(
                "execution_report_accuracy_rate",
                value=report_acc,
                threshold=EXECUTION_REPORT_ACCURACY_FLOOR,
                direction="at_least",
            ),
            _build_metric(
                "data_missing_rate",
                value=data_miss,
                threshold=DATA_MISSING_CEILING,
                direction="at_most",
            ),
            _build_metric(
                "llm_timeout_rate",
                value=llm_to,
                threshold=LLM_TIMEOUT_CEILING,
                direction="at_most",
            ),
            _build_metric(
                "signal_generation_rate",
                value=sig_gen,
                threshold=SIGNAL_GENERATION_FLOOR,
                direction="at_least",
            ),
            _build_metric(
                "max_drawdown_pct",
                value=payload.strategy.max_drawdown_pct,
                threshold=MAX_DRAWDOWN_CEILING,
                direction="at_most",
            ),
            _build_metric(
                "pnl_cny",
                value=payload.strategy.pnl_cny,
                threshold=PNL_FLOOR,
                direction="at_least",
            ),
            _build_metric(
                "csi300_excess_pct",
                value=payload.strategy.csi300_excess_pct,
                threshold=CSI300_EXCESS_FLOOR,
                direction="at_least",
            ),
        ]

    async def upsert(self, report: AcceptanceReport) -> None:
        await self._repo.upsert(report)

    async def latest(self) -> AcceptanceReport | None:
        return await self._repo.latest()

    async def can_switch_to_feishu_on(
        self, target_tier: GoLiveTier | str = GoLiveTier.FULL
    ) -> GateDecision:
        """Return the :class:`GateDecision` for ``target_tier``.

        The ONLY sanctioned switching gate (P0-6 §2 redline 5) — env-var / CLI
        bypass is forbidden. ``target_tier`` only selects WHICH tier's gate to
        evaluate; it never bypasses the ``allowed`` verdict (amendment §4 #1).

        * FULL  — most-recent report ``PASS`` AND no acceptance reset on/after
          the report's trade date (the original 45-day rolling-window
          semantics, unchanged).
        * PILOT — the 11-condition minimal set (amendment §2.3) via the
          injected readiness probe; fail-closed — no probe wired, a probe
          error, or any unmet condition → ``allowed=False`` with the reasons
          named.

        Backward-compat: a no-arg call evaluates FULL, preserving the original
        "bare boolean == 45-day FULL pass" contract so a legacy caller can
        never be fooled by a PILOT pass (amendment §2.3 / §4 #2).
        """
        tier = GoLiveTier(target_tier)
        if tier is GoLiveTier.PILOT:
            return await self._evaluate_pilot()
        return await self._evaluate_full()

    async def _evaluate_full(self) -> GateDecision:
        """FULL tier — the original 45-day rolling-window gate.

        Codex cycle 3 P1 invariant preserved — without the reset-state check a
        stale PASS report from before a J-004 reset could authorise Feishu
        interactive mode after the rolling window was invalidated. Asia/Shanghai
        trade dates are used so a reset at 2026-05-14 16:30 UTC
        (= 2026-05-15 Shanghai) correctly invalidates a PASS dated 2026-05-15.
        """
        latest = await self.latest()
        reasons: list[str] = []
        if latest is None:
            reasons.append("full:no_acceptance_report")
        elif latest.outcome is not AcceptanceOutcome.PASS:
            reasons.append(f"full:latest_outcome_{latest.outcome.value}")
        elif self._reset_state.last_reset_at is not None:
            reset_date = self._reset_state.last_reset_at.astimezone(
                SHANGHAI
            ).date()
            pass_date = dt.date.fromisoformat(latest.trade_date)
            if reset_date >= pass_date:
                reasons.append("full:reset_on_or_after_latest_pass")
        return GateDecision(
            tier=GoLiveTier.FULL,
            allowed=not reasons,
            reasons=tuple(reasons),
        )

    async def _evaluate_pilot(self) -> GateDecision:
        """PILOT tier — delegate the 11-condition minimal set to the probe.

        Fail-closed: no probe wired (simulation-only / tests) or a probe that
        raises → not allowed. A PILOT decision can NEVER satisfy FULL — the
        ``tier`` field on the result makes that explicit downstream.
        """
        if self._pilot_probe is None:
            return GateDecision(
                tier=GoLiveTier.PILOT,
                allowed=False,
                reasons=("pilot:readiness_probe_not_wired",),
            )
        try:
            unmet = tuple(await self._pilot_probe.evaluate())
        except Exception as exc:  # noqa: BLE001 — fail-closed
            log.warning("pilot_probe_evaluate_raised", error=str(exc))
            return GateDecision(
                tier=GoLiveTier.PILOT,
                allowed=False,
                reasons=("pilot:readiness_probe_error",),
            )
        return GateDecision(
            tier=GoLiveTier.PILOT,
            allowed=not unmet,
            reasons=unmet,
        )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class AcceptanceRepository:
    """Duck-typed persistence contract for acceptance_reports."""

    async def upsert(self, report: AcceptanceReport) -> None:
        raise NotImplementedError

    async def latest(self) -> AcceptanceReport | None:
        raise NotImplementedError


class InMemoryAcceptanceRepository(AcceptanceRepository):
    """Process-local repository used by tests + the no-Mongo dev loop."""

    def __init__(self) -> None:
        self._rows: list[AcceptanceReport] = []

    async def upsert(self, report: AcceptanceReport) -> None:
        # Replace any row with the same trade_date so a daily 16:00:30
        # re-run overwrites the prior point.
        self._rows = [r for r in self._rows if r.trade_date != report.trade_date]
        self._rows.append(report)
        self._rows.sort(key=lambda r: r.trade_date)

    async def latest(self) -> AcceptanceReport | None:
        return self._rows[-1] if self._rows else None

    @property
    def rows(self) -> list[AcceptanceReport]:
        return list(self._rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_trading_days_inclusive(start: dt.date, end: dt.date) -> int:
    """Count trading days in the inclusive interval ``[start, end]``."""
    if start > end:
        return 0
    count = 0
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor):
            count += 1
        cursor += dt.timedelta(days=1)
    return count


def trading_days_in_window(
    end: dt.date,
    n: int = WINDOW_TRADING_DAYS,
) -> tuple[dt.date, dt.date]:
    """Return the (start, end) date pair for an n-trading-day window."""
    start = compute_window_back(end, n)
    return start, end


__all__ = [
    "CSI300_EXCESS_FLOOR",
    "DATA_MISSING_CEILING",
    "EXECUTION_REPORT_ACCURACY_FLOOR",
    "INSTRUCTION_COMPLETION_FLOOR",
    "LLM_TIMEOUT_CEILING",
    "MAX_DRAWDOWN_CEILING",
    "PNL_FLOOR",
    "SIGNAL_GENERATION_FLOOR",
    "WINDOW_TRADING_DAYS",
    "AcceptanceComputeInput",
    "AcceptanceMetric",
    "AcceptanceOutcome",
    "AcceptanceReport",
    "AcceptanceRepository",
    "AcceptanceService",
    "InMemoryAcceptanceRepository",
    "StabilityCounters",
    "StrategyCounters",
    "WindowResetState",
    "trading_days_in_window",
]


# Silence unused-import warning on Any (kept for callers).
_ = Any
