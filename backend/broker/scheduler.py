"""BrokerScheduler — EOD pipeline + intraday MTM + post-close + evolution.

E-005 / P1-2.A / P1-2.B / X-005 owns the dedicated APScheduler that
drives the broker lifecycle outside of intraday order routing. Five
cron jobs land at launch; the fifth (``evolution_shadow_run``) is
gated by the Phase X self-evolution chain (P2-2 §1.5) and runs as a
no-op when no callback is wired.

Cron jobs (Asia/Shanghai):

* ``eod_pipeline`` — 16:00:30 sequential chain (verify_equity_point →
  write EOD snapshot → advance_day → acceptance_report stub). One
  retry on failure; second failure activates the
  :class:`EodPipelineFreezeState` so next day's BUY/SELL routing is
  blocked (the 5th freeze source per CLAUDE.md §2.7).
* ``intraday_mtm`` — every 30 seconds during trading hours; computes a
  per-position MTM EquityPoint. The actual MTM logic lands with E-006;
  the scheduler only wires the cadence.
* ``mirofish_postclose`` — 17:00 post-close MiroFish re-analysis;
  failures here are best-effort (audit + log, no freeze).
* ``advance_day`` — 16:30 advances T+1 settlement on the broker mirror.
  Holiday-gated (U-D1 / Codex P1): a weekday exchange holiday must NOT
  unlock T+1 (no session happened), so the job skips when
  :func:`backend.utils.trading_hours.is_trading_day` is False.
* ``line2_daily_runner`` — 09:35 mon-fri Line-2 daily anomaly scan over the
  T-1 EOD frame (U-D1). Runs just after the open so the RiskEngine
  trading-hours gate passes. Holiday-gated; a no-op when no callback is wired.
* ``line2_intraday_runner`` — every 30 seconds; the Line-2 deterministic 30s
  intraday trigger tick (U-D1). The job pre-gates on trading hours and the
  runner self-gates (U-C3 invariants 4+5) as the authoritative check; a
  no-op when no callback is wired.
* ``evolution_shadow_run`` — 22:00 mon-fri Phase X self-evolution
  shadow validate (P2-2 §1.5 + P1-2.A amendment 2026-05-11). One
  retry on failure; emits ``SHADOW_EVOLUTION_RUN_COMPLETED`` audit
  with outcome ``SUCCESS`` / ``FAILURE``. Failures do **not** activate
  the EOD freeze — the evolution chain is intentionally decoupled
  from the live trading path so a misbehaving challenger prompt cannot
  freeze next-day routing (X-005 acceptance criteria).

Replica-set fence: ``start()`` calls
:meth:`backend.data.database.MongoDBService.assert_replica_set` before
registering any job so a misconfigured Mongo deployment fail-closes at
boot rather than silently breaking the multi-doc transactions used by
``broker_events`` / ``broker_snapshots``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.audit.models import AuditActor, AuditEventType, AuditOutcome
from backend.audit.store import AuditStore
from backend.broker.persistence.checksum import compute_snapshot_checksum
from backend.broker.persistence.events import BrokerEventType
from backend.broker.persistence.snapshots import (
    BrokerSnapshot,
    BrokerSnapshotPosition,
)
from backend.broker.persistence.store import (
    BrokerEventStore,
    BrokerSnapshotStore,
)
from backend.utils.trading_hours import is_trading_day, is_trading_hours

log = structlog.get_logger(component="broker.scheduler")
SHANGHAI = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# Freeze state — 5th buy/sell freeze source (eod_pipeline_freeze)
# ---------------------------------------------------------------------------


@dataclass
class EodPipelineFreezeState:
    """Tracks whether the EOD pipeline failed twice in a row.

    The state is intentionally simple: an active flag plus the trade_date
    when the freeze was raised. Builder / SimulationExecutor consult
    :meth:`is_active` to know whether next-day BUY/SELL routing is
    locked. The flag clears after a successful EOD pipeline run via
    :meth:`clear`.

    Concurrent reads / writes are safe under the asyncio single-thread
    model — only the BrokerScheduler's EOD callback mutates this.
    """

    _active: bool = False
    _reason: str | None = None
    _raised_at: datetime | None = None
    _raised_for_trade_date: str | None = None
    _consecutive_failures: int = 0

    def is_active(self) -> bool:
        return self._active

    def reason(self) -> str | None:
        return self._reason

    def raised_at(self) -> datetime | None:
        return self._raised_at

    def raised_for_trade_date(self) -> str | None:
        return self._raised_for_trade_date

    def record_failure(
        self,
        *,
        reason: str,
        trade_date: str,
        when: datetime,
    ) -> bool:
        """Activate the freeze for ``trade_date``.

        Per CLAUDE.md §2.7 the EOD chain runs initial-plus-one-retry
        inside a single :meth:`BrokerScheduler.run_eod_pipeline` call;
        a failure here means both attempts blew up, so the freeze
        activates immediately (no second-day grace period). Idempotent
        when already active: returns ``False`` so callers can avoid a
        duplicate audit row.
        """
        self._consecutive_failures += 1
        if self._active:
            return False
        self._active = True
        self._reason = reason
        self._raised_at = when
        self._raised_for_trade_date = trade_date
        return True

    def clear(self) -> None:
        self._active = False
        self._reason = None
        self._raised_at = None
        self._raised_for_trade_date = None
        self._consecutive_failures = 0


# ---------------------------------------------------------------------------
# Protocols for injected components — kept narrow so unit tests stay simple
# ---------------------------------------------------------------------------


@runtime_checkable
class _BrokerLike(Protocol):
    """Minimal broker view needed for snapshot / advance_day / get_account."""

    async def get_account(self) -> Any: ...

    async def get_positions(self) -> Any: ...

    async def advance_day(self) -> None: ...


@runtime_checkable
class _ReplicaSetGate(Protocol):
    """Hook for the E-001 boot fence; production passes MongoDBService."""

    async def assert_replica_set(self) -> str: ...


# ---------------------------------------------------------------------------
# EOD pipeline result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EodPipelineResult:
    """Audit-grade summary of one EOD pipeline invocation."""

    trade_date: str
    started_at: datetime
    finished_at: datetime
    snapshot_id: str | None
    success: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class BrokerScheduler:
    """Dedicated APScheduler for the broker lifecycle.

    Independent from :class:`backend.data.scheduler.DataScheduler` and
    :class:`backend.data.analysis_scheduler.AnalysisScheduler` so failures
    in one cron family cannot disturb the others.

    The constructor accepts every collaborator the cron callbacks need
    — broker, event store, snapshot store, audit store, optional
    intraday-MTM + post-close hooks (E-006 / C-006 plug in later). The
    replica-set gate runs once at :meth:`start`.
    """

    EOD_CRON = "0 0 16 * * mon-fri"
    """16:00 sequential chain on weekdays (Asia/Shanghai). The plan.html
    EOD chain runs 16:00 → 16:00:35 within this single job; sub-steps
    are sequential awaits inside :meth:`_run_eod_pipeline`."""

    INTRADAY_MTM_INTERVAL_SECONDS = 30
    """30-second intraday MTM cadence (P1-2.B §1.1). Trading-hours
    gating is applied inside the callback to keep the trigger config
    plain."""

    MIROFISH_POSTCLOSE_CRON = "0 0 17 * * mon-fri"
    """17:00 MiroFish post-close re-analysis (P0-8). Failures are
    best-effort — audit + log, no freeze."""

    ADVANCE_DAY_CRON = "0 30 16 * * mon-fri"
    """16:30 advance_day — clears today_bought_volume so T+1 holdings
    are sellable on the next session."""

    LINE2_DAILY_CRON = "0 35 9 * * mon-fri"
    """09:35 weekday — Line-2 daily anomaly scan over the T-1 EOD frame
    (U-D1). Runs just after the 09:30 open (inside trading hours) so the
    RiskEngine 14-check #7 trading-hours gate passes for every routed SELL —
    a pre-open 09:00 run would reject every SELL as "outside trading hours"
    (Codex U-D1 P1). Reads T-1 EOD data; holiday-gated inside the job."""

    LINE2_INTRADAY_INTERVAL_SECONDS = 30
    """30-second cadence for the Line-2 deterministic intraday trigger tick
    (U-D1 / U-C3). Trading-hours gating is applied inside the job to keep the
    trigger config plain; the runner re-checks as the authoritative gate."""

    EVOLUTION_SHADOW_RUN_CRON = "0 22 * * mon-fri"
    """22:00 mon-fri — Phase X self-evolution shadow validate
    (P1-2.A amendment 2026-05-11 + P2-2 §1.5). One retry on failure;
    emits ``SHADOW_EVOLUTION_RUN_COMPLETED`` audit; does not activate
    any freeze (X-005 acceptance criteria)."""

    def __init__(
        self,
        *,
        broker: _BrokerLike,
        event_store: BrokerEventStore,
        snapshot_store: BrokerSnapshotStore,
        audit_store: AuditStore,
        replica_set_gate: _ReplicaSetGate | None = None,
        freeze_state: EodPipelineFreezeState | None = None,
        intraday_mtm_callback: Callable[[datetime], Awaitable[None]] | None = None,
        eod_close_callback: Callable[[datetime], Awaitable[None]] | None = None,
        mirofish_postclose_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        acceptance_callback: Callable[[datetime], Awaitable[None]] | None = None,
        evolution_shadow_run_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        line2_daily_runner_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        line2_intraday_runner_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        now_func: Callable[[], datetime] | None = None,
        initial_capital: float = 100_000.0,
    ) -> None:
        self._broker = broker
        self._events = event_store
        self._snapshots = snapshot_store
        self._audit = audit_store
        self._replica_gate = replica_set_gate
        self._freeze = freeze_state or EodPipelineFreezeState()
        self._intraday = intraday_mtm_callback
        # EOD-only close callback (Codex Cycle 7 P2 fix): the 16:00 EOD
        # chain needs to write exactly ONE closing EquityPoint without
        # allowing the 30s :class:`IntervalTrigger` to flood the same
        # window with 180+ ticks. Wiring main.py passes a callback here
        # that bypasses ``is_trading_hours`` — the 30s callback keeps
        # the strict guard. Defaults to ``intraday_mtm_callback`` for
        # backward compatibility with tests / older deploys that don't
        # wire the new param.
        self._eod_close = eod_close_callback or intraday_mtm_callback
        self._mirofish = mirofish_postclose_callback
        self._acceptance = acceptance_callback
        self._evolution_shadow_run = evolution_shadow_run_callback
        self._line2_daily = line2_daily_runner_callback
        self._line2_intraday = line2_intraday_runner_callback
        self._now = now_func or (lambda: datetime.now(tz=SHANGHAI))
        self._initial_capital = initial_capital
        self._scheduler: AsyncIOScheduler | None = None
        self._last_eod_result: EodPipelineResult | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Verify the replica set, register the cron jobs, start ticking."""
        if self._replica_gate is not None:
            set_name = await self._replica_gate.assert_replica_set()
            log.info("broker_scheduler_replica_set_ok", set_name=set_name)

        if self._scheduler is not None and self._scheduler.running:
            log.info("broker_scheduler_already_running")
            return

        self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self._scheduler.add_job(
            self._eod_pipeline_job,
            trigger=CronTrigger.from_crontab(
                "0 16 * * mon-fri", timezone="Asia/Shanghai"
            ),
            id="eod_pipeline",
            replace_existing=True,
            misfire_grace_time=300,
        )
        self._scheduler.add_job(
            self._intraday_mtm_job,
            trigger=IntervalTrigger(
                seconds=self.INTRADAY_MTM_INTERVAL_SECONDS
            ),
            id="intraday_mtm",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self._mirofish_postclose_job,
            trigger=CronTrigger.from_crontab(
                "0 17 * * mon-fri", timezone="Asia/Shanghai"
            ),
            id="mirofish_postclose",
            replace_existing=True,
            misfire_grace_time=300,
        )
        self._scheduler.add_job(
            self._advance_day_job,
            trigger=CronTrigger.from_crontab(
                "30 16 * * mon-fri", timezone="Asia/Shanghai"
            ),
            id="advance_day",
            replace_existing=True,
            misfire_grace_time=300,
        )
        # X-005 — Phase X self-evolution shadow validate. Always
        # register so the cron is in flight regardless of whether the
        # evolution chain (X-008 EvolutionDispatcher) has been wired
        # yet; the callback is a no-op when ``_evolution_shadow_run``
        # is ``None`` so deploys that do not yet ship the X-B chain
        # still boot cleanly.
        self._scheduler.add_job(
            self._evolution_shadow_run_job,
            trigger=CronTrigger.from_crontab(
                self.EVOLUTION_SHADOW_RUN_CRON, timezone="Asia/Shanghai"
            ),
            id="evolution_shadow_run",
            replace_existing=True,
            misfire_grace_time=300,
        )
        # U-D1 — Line-2 production runners. Always register so the cron is in
        # flight regardless of whether main.py has wired the runner callbacks
        # yet; both jobs are a no-op when their callback is ``None`` (mirrors
        # ``evolution_shadow_run``) so deploys without the orchestration layer
        # still boot cleanly.
        self._scheduler.add_job(
            self._line2_daily_job,
            trigger=CronTrigger.from_crontab(
                "35 9 * * mon-fri", timezone="Asia/Shanghai"
            ),
            id="line2_daily_runner",
            replace_existing=True,
            misfire_grace_time=300,
        )
        self._scheduler.add_job(
            self._line2_intraday_job,
            trigger=IntervalTrigger(
                seconds=self.LINE2_INTRADAY_INTERVAL_SECONDS
            ),
            id="line2_intraday_runner",
            replace_existing=True,
        )
        self._scheduler.start()
        await self._audit.write(
            event_type=AuditEventType.BROKERSCHEDULER_STARTED,
            actor=AuditActor.SCHEDULER,
            resource_type="broker_scheduler",
            payload={"jobs": ["eod_pipeline", "intraday_mtm",
                              "mirofish_postclose", "advance_day",
                              "line2_daily_runner", "line2_intraday_runner",
                              "evolution_shadow_run"]},
            outcome=AuditOutcome.SUCCESS,
        )
        log.info("broker_scheduler_started")

    async def stop(self) -> None:
        if self._scheduler is None or not self._scheduler.running:
            return
        self._scheduler.shutdown(wait=False)
        await self._audit.write(
            event_type=AuditEventType.BROKERSCHEDULER_STOPPED,
            actor=AuditActor.SCHEDULER,
            resource_type="broker_scheduler",
            outcome=AuditOutcome.SUCCESS,
        )
        log.info("broker_scheduler_stopped")

    @property
    def freeze_state(self) -> EodPipelineFreezeState:
        return self._freeze

    def last_eod_result(self) -> EodPipelineResult | None:
        return self._last_eod_result

    # ------------------------------------------------------------------
    # Cron job callbacks
    # ------------------------------------------------------------------

    async def _eod_pipeline_job(self) -> None:
        await self.run_eod_pipeline()

    async def _intraday_mtm_job(self) -> None:
        if self._intraday is None:
            return
        now = self._now()
        try:
            await self._intraday(now)
        except Exception as exc:  # noqa: BLE001 — log + continue
            log.warning("intraday_mtm_failed", error=str(exc))

    async def _mirofish_postclose_job(self) -> None:
        if self._mirofish is None:
            return
        now = self._now()
        try:
            await self._mirofish(now)
        except Exception as exc:  # noqa: BLE001 — best-effort per spec
            log.warning("mirofish_postclose_failed", error=str(exc))
            await self._audit.write(
                event_type=AuditEventType.SYSTEM_INTERRUPTED,
                actor=AuditActor.SCHEDULER,
                resource_type="mirofish_postclose",
                payload={"error": str(exc)},
                outcome=AuditOutcome.DEGRADED,
                reason_namespace="mirofish_best_effort",
            )

    async def _advance_day_job(self) -> None:
        now = self._now()
        # Holiday gating (U-D1 / Codex P1 + P0-6 §2.8 static holidays.yaml):
        # the 16:30 cron fires every weekday, but a weekday exchange holiday is
        # NOT a trading day — advancing T+1 then would unlock today_bought_volume
        # for a session that never happened. Skip cleanly on a non-trading day.
        trade_date = now.astimezone(SHANGHAI).date()
        if not is_trading_day(trade_date):
            log.info(
                "advance_day_skipped_non_trading_day",
                date=trade_date.isoformat(),
            )
            return
        try:
            await self._broker.advance_day()
            await self._events.append(
                event_type=BrokerEventType.DAY_ADVANCED,
                occurred_at=now,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("advance_day_failed", error=str(exc))

    async def _line2_daily_job(self) -> None:
        """09:35 cron — Line-2 daily anomaly scan (U-D1).

        Holiday-gated: a weekday exchange holiday skips the scan. A ``None``
        callback (main.py has not wired the orchestration layer yet) is a
        clean no-op.
        """
        if self._line2_daily is None:
            return
        now = self._now()
        trade_date = now.astimezone(SHANGHAI).date()
        if not is_trading_day(trade_date):
            log.info(
                "line2_daily_skipped_non_trading_day",
                date=trade_date.isoformat(),
            )
            return
        try:
            await self._line2_daily(now)
        except Exception as exc:  # noqa: BLE001 — log + continue
            log.warning("line2_daily_failed", error=str(exc))

    async def _line2_intraday_job(self) -> None:
        """30s cron — Line-2 deterministic intraday trigger tick (U-D1).

        Pre-gates on :func:`is_trading_hours` (which also checks the static
        calendar) so the per-tick provider is not built off-hours; the runner
        re-checks the calendar + hours invariants (U-C3 4+5) as the
        authoritative gate. A ``None`` callback is a clean no-op.
        """
        if self._line2_intraday is None:
            return
        now = self._now()
        if not is_trading_hours(now):
            return
        try:
            await self._line2_intraday(now)
        except Exception as exc:  # noqa: BLE001 — log + continue
            log.warning("line2_intraday_failed", error=str(exc))

    async def _evolution_shadow_run_job(self) -> None:
        """22:00 cron entry for the Phase X evolution shadow chain.

        Calls into :meth:`run_evolution_shadow` so tests and operators
        can exercise the retry + audit semantics without scheduling.
        """
        await self.run_evolution_shadow()

    async def run_evolution_shadow(
        self,
        *,
        retry: bool = True,
        force_now: datetime | None = None,
    ) -> bool:
        """Trigger the Phase X evolution shadow chain.

        Returns ``True`` on success (or when no callback is wired —
        the cron is a no-op until X-008 lands the dispatcher), ``False``
        when both the initial attempt and the single retry failed.

        Audit emission per attempt outcome (P2-2 §1.10 + P1-6 third
        amendment 34): ``SHADOW_EVOLUTION_RUN_COMPLETED`` with
        ``outcome=SUCCESS`` on the first successful attempt or
        ``outcome=FAILURE`` after the retry blew up. The retry path
        explicitly does **not** activate the EOD freeze — failure
        here only means tonight's challenger comparison is skipped;
        next-day live routing must stay open (X-005 acceptance criteria
        + P2-2 §1.5 decoupling rationale).
        """
        if self._evolution_shadow_run is None:
            # Cron is registered but no chain is wired yet — typical
            # state during Phase X-A before X-008 EvolutionDispatcher
            # lands. Return True without emitting audit so the booth
            # is silent during the gap; the X-B integration tests
            # exercise the real callback path.
            return True

        started = force_now or self._now()
        trade_date = started.astimezone(SHANGHAI).strftime("%Y-%m-%d")
        error: str | None = None
        try:
            await self._evolution_shadow_run(started)
            await self._audit.write(
                event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
                actor=AuditActor.SCHEDULER,
                resource_type="evolution_shadow_run",
                resource_id=trade_date,
                payload={"trade_date": trade_date, "retried": not retry},
                outcome=AuditOutcome.SUCCESS,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — the chain is the trust boundary
            error = str(exc)
            log.warning(
                "evolution_shadow_run_failed",
                trade_date=trade_date,
                error=error,
                will_retry=retry,
            )

        if retry:
            log.info("evolution_shadow_run_retrying", trade_date=trade_date)
            await asyncio.sleep(0)
            return await self.run_evolution_shadow(
                retry=False, force_now=started
            )

        await self._audit.write(
            event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
            actor=AuditActor.SCHEDULER,
            resource_type="evolution_shadow_run",
            resource_id=trade_date,
            payload={"trade_date": trade_date, "error": error,
                     "retried": True},
            outcome=AuditOutcome.FAILURE,
            reason_namespace="evolution_shadow_run_failed",
        )
        return False

    # ------------------------------------------------------------------
    # EOD pipeline orchestration
    # ------------------------------------------------------------------

    async def run_eod_pipeline(
        self,
        *,
        retry: bool = True,
        force_now: datetime | None = None,
    ) -> EodPipelineResult:
        """Execute the EOD chain end-to-end.

        Steps in order:

        1. ``verify_equity_point`` placeholder — calls the intraday MTM
           callback one final time so the closing point exists. Logged
           only; never freezes the day.
        2. Write a :class:`BrokerSnapshot` capturing the current broker
           mirror (cash + frozen_cash + positions + checksum). The
           snapshot's ``last_event_sequence`` reads
           :meth:`BrokerEventStore.read_latest_sequence` so recovery
           later replays only events past the checkpoint.
        3. ``acceptance_callback`` hook — E-008 plugs the 45-day window
           write-back in here.

        Retry policy: on first failure the pipeline calls itself once
        more (recursive single retry). A second failure activates the
        freeze and writes the FREEZE_SOURCE_EOD_PIPELINE_FREEZE audit
        event so Builder + SimulationExecutor can react.
        """
        started = force_now or self._now()
        trade_date = started.astimezone(SHANGHAI).strftime("%Y-%m-%d")
        snapshot_id: str | None = None
        error: str | None = None
        try:
            if self._eod_close is not None:
                await self._eod_close(started)

            account = await self._broker.get_account()
            positions = await self._broker.get_positions()
            snapshot_positions = tuple(
                BrokerSnapshotPosition(
                    code=pos.code,
                    volume=pos.volume,
                    today_bought_volume=getattr(pos, "today_bought_volume", 0),
                    cost_price=pos.cost_price,
                )
                for pos in positions
                if getattr(pos, "volume", 0) > 0
            )
            last_seq = await self._events.read_latest_sequence()
            checksum = compute_snapshot_checksum(
                account.available_cash,
                account.frozen_cash,
                account.initial_capital or self._initial_capital,
                snapshot_positions,
            )
            snapshot = BrokerSnapshot(
                created_at=started,
                trade_date=trade_date,
                last_event_sequence=last_seq,
                cash=account.available_cash,
                frozen_cash=account.frozen_cash,
                initial_capital=account.initial_capital or self._initial_capital,
                positions=snapshot_positions,
                checksum=checksum,
                metadata={"run_id": uuid.uuid4().hex[:12]},
            )
            await self._snapshots.append(snapshot)
            snapshot_id = str(snapshot.snapshot_id)

            if self._acceptance is not None:
                await self._acceptance(started)

            # Success — clear any prior freeze + reset failure counter.
            if self._freeze.is_active():
                self._freeze.clear()
                log.info("eod_pipeline_freeze_cleared", trade_date=trade_date)
            else:
                self._freeze.clear()  # idempotent; also resets _consecutive_failures

            finished = self._now()
            result = EodPipelineResult(
                trade_date=trade_date,
                started_at=started,
                finished_at=finished,
                snapshot_id=snapshot_id,
                success=True,
            )
            self._last_eod_result = result
            log.info(
                "eod_pipeline_completed",
                trade_date=trade_date,
                snapshot_id=snapshot_id,
            )
            return result
        except Exception as exc:  # noqa: BLE001 — pipeline is the trust boundary
            error = str(exc)
            log.warning(
                "eod_pipeline_failed", trade_date=trade_date, error=error
            )

        # One automatic retry (single attempt); on the second failure
        # the freeze flips active for next-day buy/sell routing.
        if retry:
            log.info("eod_pipeline_retrying", trade_date=trade_date)
            await asyncio.sleep(0)
            return await self.run_eod_pipeline(retry=False, force_now=started)

        when = self._now()
        activated = self._freeze.record_failure(
            reason="eod_pipeline_failed_twice",
            trade_date=trade_date,
            when=when,
        )
        if activated:
            await self._audit.write(
                event_type=AuditEventType.FREEZE_SOURCE_EOD_PIPELINE_FREEZE,
                actor=AuditActor.SCHEDULER,
                resource_type="broker_scheduler",
                resource_id=trade_date,
                payload={"trade_date": trade_date, "error": error},
                outcome=AuditOutcome.BLOCKED,
                reason_namespace="eod_pipeline_freeze",
                timestamp=when,
            )
        result = EodPipelineResult(
            trade_date=trade_date,
            started_at=started,
            finished_at=when,
            snapshot_id=None,
            success=False,
            error=error,
        )
        self._last_eod_result = result
        return result


__all__ = [
    "BrokerScheduler",
    "EodPipelineFreezeState",
    "EodPipelineResult",
]


# Silence unused-field warning on dataclass slot reservation.
_ = field
