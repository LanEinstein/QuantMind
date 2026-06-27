"""Avoid-top EXIT-on-held overlays for the C1 ablation (treatment + placebos).

These implement the C0b :class:`~scripts.factor_research.e2e_simulator.ExitOverlay`
protocol — every day they look at the held book (close-T) and return extra SELL
:class:`OrderIntent` s that fill on T+1 through the frozen barrier, honouring the
frozen :class:`ExitExecutionContract` (lot floor / queue un-fillable / mandatory
stop bypasses confirmation / re-entry lock).

Four overlays share ONE base (:class:`_ReentryLockExitOverlay`) so the placebos
are a FAIR de-exposure control — identical queue / mandatory-stop / re-entry-lock
machinery; only the *signal* differs (codex R1-#3/#16, the head self-deception
guard):

* :class:`AvoidTopOverlay` — SELL a held name that is **crowded** (top-decile
  size/industry-neutral ``ideal_amplitude_20d``, forward-filled by
  :class:`AvoidTopTriggerTable`) **AND** has confirmed a **rolling-top** (close
  ≥ ``rollover_drop`` below its trailing ``confirm_window`` peak — the P-A "确认
  滚顶, 非择顶" symmetry / §9 Erratum: we never sell on extension alone, only when
  the up-move has started rolling over). The treatment.
* :class:`StopOnlyOverlay` — the P-B mandatory stop alone (isolates the stop's
  protective value; the amendment's "消融无止损").
* :class:`RandomHeldExitOverlay` — SELL **random** held names per a precomputed
  plan (calendar-matched: same dates+counts as the treatment's avoid-top SELLs;
  or rate-matched: same total spread over the window). The placebos.

The mandatory P-B stop (``unrealized ≤ −stop_loss_frac``) fires in ALL four
(a shared safety floor) so the avoid-top-vs-placebo comparison isolates the
crowding *signal* beyond the common stop + de-exposure. All thresholds are
single PRE-COMMITTED values (NOT searched — no extra mining-debt degree of
freedom; ``top_q`` is the batch-A §3 frozen decile). Stateful by contract,
deterministic (the only randomness is the explicitly-seeded placebo draw),
offline; never the live path.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from backend.backtest.strategy import OrderIntent

from .avoid_top_panel import AvoidTopTriggerTable
from .e2e_simulator import ExitOverlayContext, HeldContext

_LOT_SIZE = 100

# Pre-committed avoid-top thresholds (single values, committed before evaluation;
# A9 discovery/calibration — NOT a searched grid). ``top_q`` is set on the trigger
# table (batch-A §3 = 0.90). Changing any of these is an amendment + ledger debit.
DEFAULT_CONFIRM_WINDOW: int = 5  # trailing daily peak window for the rolling-top
DEFAULT_ROLLOVER_DROP: float = 0.03  # close ≥3% below the recent peak = 动能转弱
DEFAULT_STOP_LOSS_FRAC: float = 0.12  # P-B hard stop: −12% unrealized → forced SELL
DEFAULT_REENTRY_LOCK_DAYS: int = 5  # re-bought-during-lock → re-exit next bar


@dataclass(frozen=True)
class AvoidTopExitConfig:
    """Frozen overlay thresholds (the deterministic EXIT *signal* parameters)."""

    confirm_window: int = DEFAULT_CONFIRM_WINDOW
    rollover_drop: float = DEFAULT_ROLLOVER_DROP
    stop_loss_frac: float = DEFAULT_STOP_LOSS_FRAC
    reentry_lock_days: int = DEFAULT_REENTRY_LOCK_DAYS
    lot_size: int = _LOT_SIZE


@dataclass(frozen=True)
class ExitEvent:
    """One SELL the overlay signalled on a day (for the placebo calendar / diagnostics).

    ``reason`` ∈ {avoid_top, stop, reentry, placebo, queued}; ``queued`` = a
    re-emit of an already-triggered un-filled exit. The ablation extracts the
    treatment's ``avoid_top`` first-trigger events to build the matched placebos.
    """

    day: str
    current_index: int
    code: str
    volume: int
    reason: str
    # Held-book attributes at the moment of the EXIT (the §A4 / R2-M1 balance
    # diagnostic: are avoid-top exits systematically older / bigger / more
    # profitable than the placebo's random exits?).
    holding_age: int
    cost_cents: int
    market_value_cents: int
    unrealized_pnl_cents: int


def _stop_triggered(h: HeldContext, stop_loss_frac: float) -> bool:
    """P-B: True iff the held lot's unrealized return ≤ −``stop_loss_frac``."""
    basis = h.cost_cents * h.volume
    if basis <= 0:
        return False
    return (h.unrealized_pnl_cents / basis) <= -stop_loss_frac


class _ReentryLockExitOverlay:
    """Shared EXIT machinery: close history, queue, mandatory stop, re-entry lock.

    Subclasses implement :meth:`_signal_exits` — the codes to exit for the *signal*
    reason (avoid-top crowding / random placebo / none). The base adds the P-B
    mandatory stop, the un-fillable queue (re-emit until gone), and the re-entry
    lock (a name we exited that re-appears in the book within the lock window is
    re-sold). All overlays share this so the placebo is a FAIR de-exposure control.
    """

    def __init__(self, config: AvoidTopExitConfig) -> None:
        self._cfg = config
        self._close_hist: dict[str, list[int]] = defaultdict(list)
        self._pending: set[str] = set()  # triggered, not yet confirmed sold
        self._reentry_lock_until: dict[str, int] = {}  # code → day index (exclusive)
        self._held_prev: set[str] = set()
        self.events: list[ExitEvent] = []

    # -- hooks -------------------------------------------------------------- #
    def _signal_exits(
        self, ctx: ExitOverlayContext, eligible: dict[str, HeldContext]
    ) -> set[str]:
        """Codes to exit for the signal reason (default: none — stop-only base)."""
        return set()

    def _signal_reason(self) -> str:
        return "stop"

    # -- machinery ---------------------------------------------------------- #
    def _forced_exit_codes(
        self, ctx: ExitOverlayContext, eligible: dict[str, HeldContext]
    ) -> set[str]:
        """Codes the base will force out today REGARDLESS of the signal.

        = already-queued (un-filled pending) ∪ P-B stop ∪ active re-entry lock.
        A placebo draw must exclude these so its random EXIT is genuinely
        *additional* de-exposure (codex P2: a draw landing on a name that would
        exit anyway under-counts the placebo's incremental exits and mislabels a
        forced re-entry as ``placebo``).
        """
        stop_frac = self._cfg.stop_loss_frac
        forced = {c for c in eligible if c in self._pending}
        forced |= {c for c, h in eligible.items() if _stop_triggered(h, stop_frac)}
        forced |= {
            c
            for c in eligible
            if c in self._reentry_lock_until
            and ctx.current_index < self._reentry_lock_until[c]
        }
        return forced

    def _update_close_hist(self, ctx: ExitOverlayContext) -> None:
        """Append today's raw close for every code with a bar (bounded deque)."""
        w = max(2, self._cfg.confirm_window)
        for code, bar in ctx.bars.items():
            hist = self._close_hist[code]
            hist.append(bar.close_cents)
            if len(hist) > w:
                del hist[0 : len(hist) - w]

    def _rolled_over(self, code: str) -> bool:
        """P-A confirmation: close ≥ ``rollover_drop`` below its trailing peak.

        Needs ≥2 observed closes (else the rolling-top cannot be confirmed →
        fail-closed to "not confirmed", so a freshly-bought crowded name is never
        sold on extension alone). Uses the raw close path the overlay observes; a
        corporate action inside the short window is a documented proxy boundary.
        """
        hist = self._close_hist.get(code, [])
        if len(hist) < 2:
            return False
        peak = max(hist)
        if peak <= 0:
            return False
        return hist[-1] <= peak * (1.0 - self._cfg.rollover_drop)

    def _settle_reentry_locks(
        self, ctx: ExitOverlayContext, held_codes: set[str]
    ) -> None:
        """A pending name that left the book sold → arm its re-entry lock window."""
        sold = self._held_prev - held_codes
        for code in sold:
            if code in self._pending:
                self._reentry_lock_until[code] = (
                    ctx.current_index + self._cfg.reentry_lock_days
                )
            self._pending.discard(code)

    def orders_for_day(self, ctx: ExitOverlayContext) -> tuple[OrderIntent, ...]:
        self._update_close_hist(ctx)
        held = ctx.held_by_code
        held_codes = set(held)
        self._settle_reentry_locks(ctx, held_codes)

        # Eligible = currently held with a positive lot (can actually be sold).
        eligible = {c: h for c, h in held.items() if h.volume > 0}

        stop_frac = self._cfg.stop_loss_frac
        stop_codes = {c for c, h in eligible.items() if _stop_triggered(h, stop_frac)}
        signal_codes = self._signal_exits(ctx, eligible)
        reentry_codes = {
            c
            for c in eligible
            if c in self._reentry_lock_until
            and ctx.current_index < self._reentry_lock_until[c]
        }

        orders: list[OrderIntent] = []
        for code, h in sorted(eligible.items()):
            queued = code in self._pending
            is_stop = code in stop_codes
            is_signal = code in signal_codes
            is_reentry = code in reentry_codes
            if not (queued or is_stop or is_signal or is_reentry):
                continue
            volume = (h.volume // self._cfg.lot_size) * self._cfg.lot_size
            if volume <= 0:
                continue
            # Precedence: a force-out reason (queued / stop / re-entry lock) beats
            # the fresh signal, so the avoid-top calendar counts only genuinely
            # new avoid-top exits, never a name dragged out by the re-entry lock
            # that happens to also be crowded today (codex P2).
            if queued:
                reason = "queued"
            elif is_stop:
                reason = "stop"
            elif is_reentry:
                reason = "reentry"
            else:  # is_signal (guaranteed by the early-continue above)
                reason = self._signal_reason()
            self._pending.add(code)
            self.events.append(
                ExitEvent(
                    day=ctx.day,
                    current_index=ctx.current_index,
                    code=code,
                    volume=volume,
                    reason=reason,
                    holding_age=h.holding_age_trading_days,
                    cost_cents=h.cost_cents,
                    market_value_cents=h.market_value_cents,
                    unrealized_pnl_cents=h.unrealized_pnl_cents,
                )
            )
            orders.append(OrderIntent(code=code, side_is_buy=False, volume=volume))

        self._held_prev = held_codes
        return tuple(orders)

    # -- introspection (the ablation reads these after the run) ------------- #
    def first_trigger_events(self, reason: str) -> list[ExitEvent]:
        """The first-trigger (non-``queued``) events of a given signal reason."""
        return [e for e in self.events if e.reason == reason]


class StopOnlyOverlay(_ReentryLockExitOverlay):
    """The P-B mandatory stop alone (no avoid-top signal) — the stop-floor arm."""


class AvoidTopOverlay(_ReentryLockExitOverlay):
    """Treatment: SELL a held name that is crowded AND has confirmed a rolling-top."""

    def __init__(
        self, triggers: AvoidTopTriggerTable, config: AvoidTopExitConfig
    ) -> None:
        super().__init__(config)
        self._triggers = triggers

    def _signal_reason(self) -> str:
        return "avoid_top"

    def _signal_exits(
        self, ctx: ExitOverlayContext, eligible: dict[str, HeldContext]
    ) -> set[str]:
        crowded = self._triggers.crowded_asof(ctx.day)
        if not crowded:
            return set()
        return {c for c in eligible if c in crowded and self._rolled_over(c)}


@dataclass(frozen=True)
class PlaceboPlan:
    """A precomputed placebo EXIT schedule (codes are still drawn at runtime).

    ``counts_by_index`` (rate-matched) or ``counts_by_day`` (calendar-matched)
    give the number of RANDOM held names to exit on each day; the actual names
    are drawn (seeded) from the eligible book at run time, since the held set is
    only known during the replay. Exactly one of the two is populated.
    """

    seed: int
    counts_by_index: dict[int, int] = field(default_factory=dict)
    counts_by_day: dict[str, int] = field(default_factory=dict)

    def target_count(self, ctx: ExitOverlayContext) -> int:
        if self.counts_by_day:
            return self.counts_by_day.get(ctx.day, 0)
        return self.counts_by_index.get(ctx.current_index, 0)


class RandomHeldExitOverlay(_ReentryLockExitOverlay):
    """Placebo: SELL ``plan.target_count`` RANDOM eligible held names per day.

    Draws (seeded per day) from the eligible book MINUS names already stop- or
    queue-bound (those exit anyway via the shared base, so a placebo draw on them
    would be wasted). The shared queue / stop / re-entry-lock machinery makes this
    a fair de-exposure control for the avoid-top treatment.
    """

    def __init__(self, plan: PlaceboPlan, config: AvoidTopExitConfig) -> None:
        super().__init__(config)
        self._plan = plan

    def _signal_reason(self) -> str:
        return "placebo"

    def _signal_exits(
        self, ctx: ExitOverlayContext, eligible: dict[str, HeldContext]
    ) -> set[str]:
        k = self._plan.target_count(ctx)
        if k <= 0:
            return set()
        # Exclude names already destined to exit (queued / stop / re-entry lock) so
        # the placebo draw lands on genuinely-additional names — matching the
        # avoid-top SELLs, which are likewise additional to the common force-outs
        # (codex P2).
        forced = self._forced_exit_codes(ctx, eligible)
        pool = sorted(c for c in eligible if c not in forced)
        if not pool:
            return set()
        rng = np.random.default_rng(self._plan.seed + ctx.current_index)
        m = min(k, len(pool))
        idx = rng.choice(len(pool), size=m, replace=False)
        return {pool[int(i)] for i in idx}


__all__ = [
    "AvoidTopExitConfig",
    "AvoidTopOverlay",
    "DEFAULT_CONFIRM_WINDOW",
    "DEFAULT_REENTRY_LOCK_DAYS",
    "DEFAULT_ROLLOVER_DROP",
    "DEFAULT_STOP_LOSS_FRAC",
    "ExitEvent",
    "PlaceboPlan",
    "RandomHeldExitOverlay",
    "StopOnlyOverlay",
]
