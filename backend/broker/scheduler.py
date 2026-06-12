"""BrokerScheduler — EOD pipeline + intraday MTM + post-close + evolution.

E-005 / P1-2.A / P1-2.B / X-005 owns the dedicated APScheduler that
drives the broker lifecycle outside of intraday order routing. Eight
cron jobs land at launch (the original five + the U-D1 Line-2 daily +
intraday runners + the U-D1b Line-1 runner); ``evolution_shadow_run``
is gated by the Phase X self-evolution chain (P2-2 §1.5) and the three
double-line runners are no-ops until ``main.py`` wires their callbacks.

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
* ``line1_runner`` — 09:35 mon-fri Line-1 full-market BUY-selection run over
  the T-1 EOD frame (U-D1b). Same 09:35 slot as ``line2_daily_runner`` so the
  RiskEngine trading-hours gate passes for every routed BUY (a pre-open 09:00
  run would reject each BUY as outside trading hours). Holiday-gated; a no-op
  when no callback is wired.
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

    THESIS_REVIEW_CRON = "0 30 17 * * mon-fri"
    """17:30 mon-fri — Line-2 post-close thesis review (W-002 / P1-2.A-amendment-
    2026-06-02). Runs AFTER mirofish_postclose 17:00 so the day's evidence is
    already written. LLM advisory in the orchestration layer (monitoring stays
    zero-LLM); writes evidence + a display-only Feishu digest only — owner acts
    manually. Holiday-gated; a no-op when no callback is wired."""

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

    LINE1_DAILY_CRON = "0 35 9 * * mon-fri"
    """09:35 weekday — Line-1 full-market BUY-selection run over the T-1 EOD
    frame (U-D1b). Shares the 09:35 slot with ``line2_daily_runner``: it runs
    just after the 09:30 open (inside trading hours) so the RiskEngine 14-check
    #7 trading-hours gate passes for every routed BUY — a pre-open 09:00 run
    would reject each BUY as "outside trading hours". Holiday-gated inside the
    job."""

    EVOLUTION_SHADOW_RUN_CRON = "0 22 * * mon-fri"
    """22:00 mon-fri — Phase X self-evolution shadow validate
    (P1-2.A amendment 2026-05-11 + P2-2 §1.5). One retry on failure;
    emits ``SHADOW_EVOLUTION_RUN_COMPLETED`` audit; does not activate
    any freeze (X-005 acceptance criteria)."""

    WEEKEND_DEEP_REVIEW_CRON = "0 10 * * sat"
    """10:00 Saturday — weekly deep review + experiment planning lane
    (AA-003 / P1-2.A-amendment-2026-06-12 §1.1). NON-trading-day lane:
    a makeup-workday Saturday skips (the trading-day cron family owns
    that day). The callback enforces the §1.4 ops gate; failures never
    freeze trading (decoupled, X-005 precedent)."""

    HOLIDAY_CATCHUP_REVIEW_CRON = "0 10 * * *"
    """10:00 daily — holiday / Sunday catch-up window for the weekly
    deep review (AA-003). Self-gates: skips trading days (live session)
    and Saturdays (the weekend lane owns them); the callback further
    skips when the weekly record already exists or the review week is
    not complete yet (mid-week holiday)."""

    DAILY_ATTRIBUTION_REVIEW_CRON = "0 18 * * mon-fri"
    """18:00 mon-fri — facts-first daily attribution review (AA-002 /
    P1-2.A-amendment-2026-06-12 §1.1). Runs AFTER the 17:30 thesis
    review so the day's evidence chain is complete. Deterministic
    (``backend/review``); writes one append-only ReviewRecord. One retry
    on failure; a final failure emits a DEGRADED audit and never
    freezes trading (the review lane is decoupled, X-005 precedent)."""

    SIM_AUTO_RECONCILIATION_CRON = "10 16 * * mon-fri"
    """16:10 mon-fri — pure-sim self-integrity reconciliation (AA-001 /
    P1-2.A-amendment-2026-06-12 §1.2). Runs AFTER the 16:00 EOD pipeline
    so today's snapshot + closing equity point exist. The callback skips
    itself when ``feishu_interactive`` is enabled (the human arbitration
    semantics of P0-5 are untouched). One retry on failure; a final
    failure emits a DEGRADED audit but does NOT activate any scheduler
    freeze — fail-closed safety rides on the OPEN ticket the run itself
    persists before resolving."""

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
            [datetime], Awaitable[None | str]
        ] | None = None,
        line2_daily_runner_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        line2_intraday_runner_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        line1_runner_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        thesis_review_runner_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        sim_auto_reconciliation_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        daily_attribution_review_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        weekend_deep_review_callback: Callable[
            [datetime], Awaitable[None]
        ] | None = None,
        holiday_catchup_review_callback: Callable[
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
        self._line1 = line1_runner_callback
        self._thesis_review = thesis_review_runner_callback
        self._sim_auto_recon = sim_auto_reconciliation_callback
        self._daily_attribution = daily_attribution_review_callback
        self._weekend_review = weekend_deep_review_callback
        self._holiday_catchup = holiday_catchup_review_callback
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
        # U-D1b — Line-1 production runner. Same always-register / no-op-when-
        # unwired pattern as the Line-2 crons so a deploy without the
        # orchestration layer still boots cleanly.
        self._scheduler.add_job(
            self._line1_daily_job,
            trigger=CronTrigger.from_crontab(
                "35 9 * * mon-fri", timezone="Asia/Shanghai"
            ),
            id="line1_runner",
            replace_existing=True,
            misfire_grace_time=300,
        )
        # W-002 — Line-2 post-close thesis review (17:30, after mirofish 17:00).
        # Same always-register / no-op-when-unwired pattern as the other runner
        # crons (P1-2.A-amendment-2026-06-02).
        self._scheduler.add_job(
            self._thesis_review_job,
            trigger=CronTrigger.from_crontab(
                "30 17 * * mon-fri", timezone="Asia/Shanghai"
            ),
            id="thesis_review_runner",
            replace_existing=True,
            misfire_grace_time=300,
        )
        # AA-001 — pure-sim self-integrity reconciliation at 16:10, after
        # the 16:00 EOD pipeline. Same always-register / no-op-when-unwired
        # pattern as the other runner crons (P1-2.A-amendment-2026-06-12).
        self._scheduler.add_job(
            self._sim_auto_reconciliation_job,
            trigger=CronTrigger.from_crontab(
                "10 16 * * mon-fri", timezone="Asia/Shanghai"
            ),
            id="sim_auto_reconciliation",
            replace_existing=True,
            misfire_grace_time=300,
        )
        # AA-002 — facts-first daily attribution review at 18:00, after
        # the 17:30 thesis review. Same always-register / no-op-when-
        # unwired pattern (P1-2.A-amendment-2026-06-12 §1.1).
        self._scheduler.add_job(
            self._daily_attribution_review_job,
            trigger=CronTrigger.from_crontab(
                "0 18 * * mon-fri", timezone="Asia/Shanghai"
            ),
            id="daily_attribution_review",
            replace_existing=True,
            misfire_grace_time=300,
        )
        # AA-003 — non-trading-day review lanes (weekend deep review +
        # holiday/Sunday catch-up). Same always-register / no-op-when-
        # unwired pattern (P1-2.A-amendment-2026-06-12 §1.1/§1.4).
        self._scheduler.add_job(
            self._weekend_deep_review_job,
            trigger=CronTrigger.from_crontab(
                "0 10 * * sat", timezone="Asia/Shanghai"
            ),
            id="weekend_deep_review",
            replace_existing=True,
            misfire_grace_time=300,
        )
        self._scheduler.add_job(
            self._holiday_catchup_review_job,
            trigger=CronTrigger.from_crontab(
                "0 10 * * *", timezone="Asia/Shanghai"
            ),
            id="holiday_catchup_review",
            replace_existing=True,
            misfire_grace_time=300,
        )
        self._scheduler.start()
        await self._audit.write(
            event_type=AuditEventType.BROKERSCHEDULER_STARTED,
            actor=AuditActor.SCHEDULER,
            resource_type="broker_scheduler",
            payload={"jobs": ["eod_pipeline", "intraday_mtm",
                              "mirofish_postclose", "advance_day",
                              "line2_daily_runner", "line2_intraday_runner",
                              "line1_runner", "thesis_review_runner",
                              "evolution_shadow_run",
                              "sim_auto_reconciliation",
                              "daily_attribution_review",
                              "weekend_deep_review",
                              "holiday_catchup_review"]},
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

    async def _line1_daily_job(self) -> None:
        """09:35 cron — Line-1 full-market BUY-selection run (U-D1b).

        Holiday-gated (mirrors :meth:`_line2_daily_job`): a weekday exchange
        holiday skips the run. A ``None`` callback (main.py has not wired the
        orchestration layer yet) is a clean no-op.
        """
        if self._line1 is None:
            return
        now = self._now()
        trade_date = now.astimezone(SHANGHAI).date()
        if not is_trading_day(trade_date):
            log.info(
                "line1_daily_skipped_non_trading_day",
                date=trade_date.isoformat(),
            )
            return
        try:
            await self._line1(now)
        except Exception as exc:  # noqa: BLE001 — log + continue
            log.warning("line1_daily_failed", error=str(exc))

    async def _thesis_review_job(self) -> None:
        """17:30 cron — Line-2 post-close thesis review (W-002).

        Holiday-gated (mirrors :meth:`_line2_daily_job`): a weekday exchange
        holiday skips the review. A ``None`` callback (main.py has not wired the
        orchestration layer yet) is a clean no-op. A failure is logged + swallowed
        — the advisory review must never freeze next-day routing.
        """
        if self._thesis_review is None:
            return
        now = self._now()
        trade_date = now.astimezone(SHANGHAI).date()
        if not is_trading_day(trade_date):
            log.info(
                "thesis_review_skipped_non_trading_day",
                date=trade_date.isoformat(),
            )
            return
        try:
            await self._thesis_review(now)
        except Exception as exc:  # noqa: BLE001 — log + continue (advisory)
            log.warning("thesis_review_failed", error=str(exc))

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

    async def _sim_auto_reconciliation_job(self) -> None:
        """16:10 cron entry for the AA-001 sim auto-reconciliation.

        Calls into :meth:`run_sim_auto_reconciliation` so tests and
        operators can exercise the retry + audit semantics without
        scheduling.
        """
        await self.run_sim_auto_reconciliation()

    async def run_sim_auto_reconciliation(
        self,
        *,
        retry: bool = True,
        force_now: datetime | None = None,
    ) -> bool:
        """Trigger the pure-sim self-integrity reconciliation (AA-001).

        Returns ``True`` on success (or when no callback is wired / the
        day is a holiday), ``False`` when both the initial attempt and
        the single retry failed. A final failure emits a DEGRADED
        ``SYSTEM_INTERRUPTED`` audit (mirofish best-effort precedent)
        and does NOT activate any scheduler freeze: the run persists its
        OPEN ticket before resolving, so a mid-run crash already leaves
        routing frozen fail-closed via the ticket — a scheduler-level
        freeze would be redundant.
        """
        if self._sim_auto_recon is None:
            return True
        started = force_now or self._now()
        trade_date = started.astimezone(SHANGHAI).date()
        if not is_trading_day(trade_date):
            log.info(
                "sim_auto_reconciliation_skipped_non_trading_day",
                date=trade_date.isoformat(),
            )
            return True
        error: str | None = None
        try:
            await self._sim_auto_recon(started)
            return True
        except Exception as exc:  # noqa: BLE001 — the run is the trust boundary
            error = str(exc)
            log.warning(
                "sim_auto_reconciliation_failed",
                trade_date=trade_date.isoformat(),
                error=error,
                will_retry=retry,
            )

        if retry:
            log.info(
                "sim_auto_reconciliation_retrying",
                trade_date=trade_date.isoformat(),
            )
            await asyncio.sleep(0)
            return await self.run_sim_auto_reconciliation(
                retry=False, force_now=started
            )

        await self._audit.write(
            event_type=AuditEventType.SYSTEM_INTERRUPTED,
            actor=AuditActor.SCHEDULER,
            resource_type="sim_auto_reconciliation",
            resource_id=trade_date.isoformat(),
            payload={
                "trade_date": trade_date.isoformat(),
                "error": error,
                "retried": True,
            },
            outcome=AuditOutcome.DEGRADED,
            reason_namespace="sim_auto_reconciliation_failed",
        )
        return False

    async def _daily_attribution_review_job(self) -> None:
        """18:00 cron entry for the AA-002 daily attribution review."""
        await self.run_daily_attribution_review()

    async def run_daily_attribution_review(
        self,
        *,
        retry: bool = True,
        force_now: datetime | None = None,
    ) -> bool:
        """Trigger the facts-first daily attribution review (AA-002).

        Same retry + audit contract as
        :meth:`run_sim_auto_reconciliation`: one retry, a final failure
        emits a DEGRADED ``SYSTEM_INTERRUPTED`` audit, and NOTHING here
        freezes trading — the review lane is decoupled from routing
        (X-005 precedent; a broken review must not block tomorrow's
        session).
        """
        if self._daily_attribution is None:
            return True
        started = force_now or self._now()
        trade_date = started.astimezone(SHANGHAI).date()
        if not is_trading_day(trade_date):
            log.info(
                "daily_attribution_review_skipped_non_trading_day",
                date=trade_date.isoformat(),
            )
            return True
        error: str | None = None
        try:
            await self._daily_attribution(started)
            return True
        except Exception as exc:  # noqa: BLE001 — review is the trust boundary
            error = str(exc)
            log.warning(
                "daily_attribution_review_failed",
                trade_date=trade_date.isoformat(),
                error=error,
                will_retry=retry,
            )

        if retry:
            log.info(
                "daily_attribution_review_retrying",
                trade_date=trade_date.isoformat(),
            )
            await asyncio.sleep(0)
            return await self.run_daily_attribution_review(
                retry=False, force_now=started
            )

        await self._audit.write(
            event_type=AuditEventType.SYSTEM_INTERRUPTED,
            actor=AuditActor.SCHEDULER,
            resource_type="daily_attribution_review",
            resource_id=trade_date.isoformat(),
            payload={
                "trade_date": trade_date.isoformat(),
                "error": error,
                "retried": True,
            },
            outcome=AuditOutcome.DEGRADED,
            reason_namespace="daily_attribution_review_failed",
        )
        return False

    async def _weekend_deep_review_job(self) -> None:
        """10:00 Saturday cron entry for the AA-003 weekend lane."""
        await self.run_weekend_deep_review()

    async def run_weekend_deep_review(
        self,
        *,
        retry: bool = True,
        force_now: datetime | None = None,
    ) -> bool:
        """Trigger the weekend deep-review lane (AA-003).

        NON-trading-day gating: a makeup-workday Saturday skips. Retry +
        audit contract mirrors :meth:`run_daily_attribution_review`;
        failures never freeze trading (§2 of the amendment, X-005
        precedent).
        """
        if self._weekend_review is None:
            return True
        started = force_now or self._now()
        review_date = started.astimezone(SHANGHAI).date()
        if is_trading_day(review_date):
            log.info(
                "weekend_deep_review_skipped_trading_day",
                date=review_date.isoformat(),
            )
            return True
        return await self._attempt_review_lane(
            self._weekend_review,
            started,
            retry=retry,
            resource_type="weekend_deep_review",
        )

    async def _holiday_catchup_review_job(self) -> None:
        """10:00 daily cron entry for the AA-003 catch-up lane."""
        await self.run_holiday_catchup_review()

    async def run_holiday_catchup_review(
        self,
        *,
        retry: bool = True,
        force_now: datetime | None = None,
    ) -> bool:
        """Trigger the Sunday/holiday catch-up lane (AA-003).

        Skips trading days (live session) and Saturdays (owned by the
        weekend lane); the main.py callback additionally skips when the
        weekly record already exists or the week is incomplete.
        """
        if self._holiday_catchup is None:
            return True
        started = force_now or self._now()
        review_date = started.astimezone(SHANGHAI).date()
        if is_trading_day(review_date):
            return True
        if review_date.weekday() == 5:  # Saturday → weekend lane owns it
            log.info(
                "holiday_catchup_skipped_saturday",
                date=review_date.isoformat(),
            )
            return True
        return await self._attempt_review_lane(
            self._holiday_catchup,
            started,
            retry=retry,
            resource_type="holiday_catchup_review",
        )

    async def _attempt_review_lane(
        self,
        callback: Callable[[datetime], Awaitable[None]],
        started: datetime,
        *,
        retry: bool,
        resource_type: str,
    ) -> bool:
        """Shared retry-once + DEGRADED-audit body for the review lanes."""
        review_date = started.astimezone(SHANGHAI).date()
        error: str | None = None
        try:
            await callback(started)
            return True
        except Exception as exc:  # noqa: BLE001 — lane is the trust boundary
            error = str(exc)
            log.warning(
                f"{resource_type}_failed",
                date=review_date.isoformat(),
                error=error,
                will_retry=retry,
            )
        if retry:
            await asyncio.sleep(0)
            return await self._attempt_review_lane(
                callback, started, retry=False, resource_type=resource_type
            )
        await self._audit.write(
            event_type=AuditEventType.SYSTEM_INTERRUPTED,
            actor=AuditActor.SCHEDULER,
            resource_type=resource_type,
            resource_id=review_date.isoformat(),
            payload={
                "date": review_date.isoformat(),
                "error": error,
                "retried": True,
            },
            outcome=AuditOutcome.DEGRADED,
            reason_namespace=f"{resource_type}_failed",
        )
        return False

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
            outcome_signal = await self._evolution_shadow_run(started)
            if isinstance(outcome_signal, str):
                # AB-007 (codex AB P2) — the callback signalled a SKIP
                # (budget exhausted / dispatcher unwired): audit it as
                # DEGRADED so the trail never shows a SUCCESS for a run
                # that did not happen. Backward compatible: a None
                # return keeps the original SUCCESS semantics.
                await self._audit.write(
                    event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
                    actor=AuditActor.SCHEDULER,
                    resource_type="evolution_shadow_run",
                    resource_id=trade_date,
                    payload={
                        "trade_date": trade_date,
                        "status": outcome_signal,
                        "retried": not retry,
                    },
                    outcome=AuditOutcome.DEGRADED,
                    reason_namespace="evolution_shadow_run_skipped",
                )
                return True
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
            # Per-trade-date buy volumes (BrokerSnapshot v2) — the public
            # Position model has no per-date field, so the broker exposes a
            # dedicated export. getattr-guarded: scheduler test fakes / older
            # broker views without the export degrade to empty maps
            # (P0-4-amendment-2026-06-04, codex cycle-7 P1).
            _export = getattr(self._broker, "export_bought_by_date", None)
            bought_maps: dict[str, dict[str, int]] = (
                await _export() if _export is not None else {}
            )
            snapshot_positions = tuple(
                BrokerSnapshotPosition(
                    code=pos.code,
                    volume=pos.volume,
                    # The public Position model exposes volume +
                    # available_volume (not the internal counter): their
                    # difference IS the same-day bought volume. Persisting it
                    # keeps the recovery T+1 reseed (and the post-restart
                    # available_volume) correct — the previous getattr on the
                    # public model silently wrote 0 for every position
                    # (P0-4-amendment-2026-06-04, codex cycle-5 P1).
                    today_bought_volume=max(
                        0,
                        int(pos.volume)
                        - int(getattr(pos, "available_volume", pos.volume)),
                    ),
                    cost_price=pos.cost_price,
                    bought_by_date=bought_maps.get(pos.code, {}),
                    # AA-004 nameplate (snapshot v3) — getattr-guarded so
                    # scheduler test fakes without the fields degrade to
                    # None (legacy semantics).
                    entry_policy_hash=getattr(
                        pos, "entry_policy_hash", None
                    ),
                    entry_style=getattr(pos, "entry_style", None),
                    entry_sell_stack_version=getattr(
                        pos, "entry_sell_stack_version", None
                    ),
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
