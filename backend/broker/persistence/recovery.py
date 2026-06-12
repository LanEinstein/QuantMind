"""recover_state — load latest snapshot + replay events (E-002 / P1-2.A).

The recovery flow runs at BrokerScheduler boot (after the E-001
replica-set fence). Steps:

1. ``snapshot_store.read_latest()`` — most recent BrokerSnapshot, or
   ``None`` for a fresh deploy.
2. Recompute the checksum from the snapshot's stored state. If the
   stored ``checksum`` and recomputed value disagree, raise
   :class:`ChecksumMismatchError` — corrupted checkpoints must NOT
   silently drive the broker; the operator decides whether to roll
   back to an older snapshot.
3. Replay events with ``sequence > last_event_sequence`` in sequence
   order on top of the snapshot. Each apply step mutates a working
   :class:`RecoveredState` dataclass; the apply logic mirrors the
   in-process MockBroker so the rebuild is bit-identical.
4. Return the rebuilt :class:`RecoveredState` to the BrokerScheduler,
   which uses it to seed the in-process MockBroker mirror.

If no snapshot exists, recovery returns the "fresh account" state
derived from ``initial_capital`` and replays every event (sequence > 0
matches all events).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog

from backend.broker.persistence.checksum import compute_snapshot_checksum
from backend.broker.persistence.events import BrokerEvent, BrokerEventType
from backend.broker.persistence.snapshots import (
    BrokerSnapshotPosition,
)
from backend.broker.persistence.store import (
    BrokerEventStore,
    BrokerSnapshotStore,
)
from backend.broker.trade_dates import instruction_trade_date, record_buy_date
from backend.models.execution import (
    REPORT_SCHEMA_V1_OWNER_FEE,
    REPORT_SCHEMA_V2_SYSTEM_FEE,
)
from backend.utils.trading_hours import SHANGHAI

log = structlog.get_logger(component="broker.persistence.recovery")


class RecoveryError(RuntimeError):
    """Base class for recovery failures (corrupted checkpoint, replay
    inconsistency, etc.). Subclasses identify the precise red line."""


class ChecksumMismatchError(RecoveryError):
    """Raised when a snapshot's stored checksum does not match the
    deterministic re-derivation. The operator must resolve manually
    (roll back to an older snapshot or accept replaying from origin)."""


@dataclass
class _MutablePosition:
    code: str
    volume: int
    today_bought_volume: int
    cost_price: float
    # Per-trade-date buy volumes consumed by the external-report T+1 guard
    # (P0-4-amendment-2026-06-04). Rebuilt here during replay so a restart
    # does not blind the guard (codex cycle-3 P1); seeded into the live
    # MockBroker via seed_from_recovery.
    bought_by_date: dict[date, int] = field(default_factory=dict)
    # AA-004 nameplate (snapshot v3): stamped on episode-open from the
    # event payload during replay; carried from snapshot v3 rows on seed.
    entry_policy_hash: str | None = None
    entry_style: str | None = None
    entry_sell_stack_version: str | None = None


@dataclass
class RecoveredState:
    """Working state produced by recovery.

    Mutable on purpose: the apply loop walks events in order and
    updates fields in place. The BrokerScheduler converts the final
    object to immutable snapshot inputs before seeding the broker.
    """

    cash: float
    frozen_cash: float
    initial_capital: float
    positions: dict[str, _MutablePosition] = field(default_factory=dict)
    last_sequence: int = 0
    events_replayed: int = 0

    def to_snapshot_positions(self) -> tuple[BrokerSnapshotPosition, ...]:
        out: list[BrokerSnapshotPosition] = []
        for code, pos in sorted(self.positions.items()):
            if pos.volume <= 0:
                continue
            out.append(
                BrokerSnapshotPosition(
                    code=code,
                    volume=pos.volume,
                    today_bought_volume=pos.today_bought_volume,
                    cost_price=pos.cost_price,
                    # Carry the per-date T+1 buy record (ISO keys) — a
                    # checkpoint written from a recovered state (e.g.
                    # scripts/reconcile_now.py appends one) must not drop
                    # multi-day buy dates, or a restart from it re-opens the
                    # cycle-7 gap (P0-4-amendment-2026-06-04).
                    bought_by_date={
                        d.isoformat(): v
                        for d, v in sorted(pos.bought_by_date.items())
                    },
                    entry_policy_hash=pos.entry_policy_hash,
                    entry_style=pos.entry_style,
                    entry_sell_stack_version=pos.entry_sell_stack_version,
                )
            )
        return tuple(out)


def _apply_event(state: RecoveredState, event: BrokerEvent) -> None:
    """Mutate ``state`` to reflect ``event``. See module docstring for the
    invariant — must be bit-identical to MockBroker's in-process apply.
    """
    payload: dict[str, Any] = event.payload

    if event.event_type is BrokerEventType.ACCOUNT_INITIALIZED:
        # Hard reset to the recorded initial capital. Used when the
        # account is first created or when a mode-switch lifecycle
        # archives the prior account.
        state.cash = float(payload.get("cash", state.initial_capital))
        state.frozen_cash = float(payload.get("frozen_cash", 0.0))
        state.initial_capital = float(
            payload.get("initial_capital", state.initial_capital)
        )
        state.positions.clear()
        return

    if event.event_type is BrokerEventType.ORDER_PLACED:
        # Freeze cash for BUY. SELL never moves cash here.
        direction = payload.get("direction")
        if direction == "BUY":
            frozen = float(payload.get("frozen_amount", 0.0))
            state.cash -= frozen
            state.frozen_cash += frozen
        return

    if event.event_type is BrokerEventType.ORDER_REJECTED:
        # No state effect — kept in the event stream for audit.
        return

    if event.event_type is BrokerEventType.ORDER_CANCELLED:
        direction = payload.get("direction")
        if direction == "BUY":
            frozen = float(payload.get("frozen_amount", 0.0))
            state.frozen_cash -= frozen
            state.cash += frozen
        return

    if event.event_type is BrokerEventType.ORDER_FILLED:
        direction = payload.get("direction")
        code = str(payload.get("code", ""))
        volume = int(payload.get("volume", 0))
        fill_price = float(payload.get("fill_price", 0.0))
        commission = float(payload.get("commission", 0.0))
        stamp_tax = float(payload.get("stamp_tax", 0.0))
        transfer_fee = float(payload.get("transfer_fee", 0.0))
        amount = fill_price * volume

        if direction == "BUY":
            frozen = float(payload.get("frozen_amount", 0.0))
            actual_cost = amount + commission + transfer_fee
            state.frozen_cash -= frozen
            delta = frozen - actual_cost
            state.cash += delta
            pos = state.positions.get(code)
            if pos is None:
                pos = _MutablePosition(
                    code=code,
                    volume=volume,
                    today_bought_volume=volume,
                    cost_price=fill_price,
                    # AA-004: nameplate rides in the event payload; absent
                    # on pre-v3 events → None (legacy semantics).
                    entry_policy_hash=payload.get("entry_policy_hash"),
                    entry_style=payload.get("entry_style"),
                    entry_sell_stack_version=payload.get(
                        "entry_sell_stack_version"
                    ),
                )
                state.positions[code] = pos
            else:
                total_cost = pos.cost_price * pos.volume + fill_price * volume
                pos.volume += volume
                pos.today_bought_volume += volume
                pos.cost_price = total_cost / pos.volume if pos.volume else 0.0
            # Sim fills happen at the event time — key the T+1 buy record on
            # the event's Shanghai trade date (P0-4-amendment-2026-06-04).
            record_buy_date(
                pos.bought_by_date,
                event.occurred_at.astimezone(SHANGHAI).date(),
                volume,
            )
        else:  # SELL
            net = amount - commission - stamp_tax - transfer_fee
            state.cash += net
            pos = state.positions.get(code)
            if pos is None:
                # Defensive: a SELL fill without an existing position
                # implies the snapshot/event chain was mis-ordered.
                raise RecoveryError(
                    f"replay error: SELL fill for {code} but no position "
                    f"in state (event sequence {event.sequence})"
                )
            pos.volume -= volume
            if pos.volume <= 0:
                state.positions.pop(code, None)
        return

    if event.event_type is BrokerEventType.DAY_ADVANCED:
        for pos in state.positions.values():
            pos.today_bought_volume = 0
        return

    if event.event_type is BrokerEventType.RECONCILIATION_RESET:
        # Reset the working state to the snapshot recorded in payload.
        # The replay loop expects this to be a self-contained transition
        # (snapshot rewrite + sequence cursor moves forward).
        state.cash = float(payload.get("cash", state.cash))
        state.frozen_cash = float(payload.get("frozen_cash", state.frozen_cash))
        state.positions.clear()
        for raw in payload.get("positions", []) or []:
            code = str(raw["code"])
            state.positions[code] = _MutablePosition(
                code=code,
                volume=int(raw["volume"]),
                today_bought_volume=int(raw.get("today_bought_volume", 0)),
                cost_price=float(raw["cost_price"]),
                # AA-004: reconciliation payload rows usually carry no
                # nameplate (user-reported truth) → None.
                entry_policy_hash=raw.get("entry_policy_hash"),
                entry_style=raw.get("entry_style"),
                entry_sell_stack_version=raw.get(
                    "entry_sell_stack_version"
                ),
            )
        return

    if event.event_type is BrokerEventType.MODE_SWITCH_RESET:
        # Archive — full reset to initial capital. Same payload contract
        # as ACCOUNT_INITIALIZED so the loader can replay either.
        state.cash = float(payload.get("cash", state.initial_capital))
        state.frozen_cash = float(payload.get("frozen_cash", 0.0))
        state.positions.clear()
        return

    if event.event_type is BrokerEventType.EXECUTION_REPORT_APPLIED:
        # Generic delta carrier. payload['positions_delta'] entries
        # carry the per-share cost basis (cost_price field) — recovery
        # must compute a weighted average for positive deltas (add-on
        # buys) and leave cost basis unchanged for negative deltas
        # (sells / reductions) so the rebuild matches the live
        # broker's _apply_buy averaging. Setting pos.cost_price
        # directly from the fill price would diverge on add-on
        # buys (codex P1).
        #
        # P0-4-amendment-2026-05-27 §2.4 — version branch. Replay is
        # delta-based for both schemas (deterministic; never recompute
        # the fee from config, which could have changed). The branch is
        # a fail-closed schema guard:
        #   * v1 (legacy / absent) — positions_delta cost_price is the
        #     raw fill price; the owner fee was already folded into the
        #     stored cash_delta.
        #   * v2 (current) — the broker derived the fee; positions_delta
        #     cost_price is the fee-inclusive basis. A v2 row MUST carry
        #     the friction breakdown it was applied with AND a cost_price
        #     on every BUY (positive-volume) leg — the very field this
        #     replay consumes for the weighted average — else the event is
        #     corrupt and recovery refuses it (fail-closed) rather than
        #     silently rebuilding a position at cost_price 0.0.
        report_schema_version = int(
            payload.get("report_schema_version", REPORT_SCHEMA_V1_OWNER_FEE)
        )
        deltas = payload.get("positions_delta", []) or []
        if report_schema_version == REPORT_SCHEMA_V2_SYSTEM_FEE:
            if "net" not in payload or "commission" not in payload:
                raise RecoveryError(
                    f"replay error: v2 EXECUTION_REPORT_APPLIED event "
                    f"(sequence {event.sequence}) missing derived friction "
                    "breakdown; refusing automatic recovery (P0-4-amendment "
                    "§2.4 fail-closed)"
                )
            for delta in deltas:
                if (
                    int(delta.get("volume_delta", 0)) > 0
                    and delta.get("cost_price") is None
                ):
                    raise RecoveryError(
                        f"replay error: v2 EXECUTION_REPORT_APPLIED event "
                        f"(sequence {event.sequence}) BUY leg missing "
                        "cost_price; refusing automatic recovery "
                        "(P0-4-amendment §2.4 fail-closed)"
                    )
        cash_delta = float(payload.get("cash_delta", 0.0))
        state.cash += cash_delta
        # External fills are keyed on the INSTRUCTION's embedded trade date
        # (not the event/parse time) — mirrors MockBroker.apply_external_fill
        # so the rebuilt T+1 buy record matches the live one bit-for-bit
        # (P0-4-amendment-2026-06-04).
        fill_trade_date = instruction_trade_date(
            str(payload.get("instruction_id", "") or ""), event.occurred_at
        )
        for delta in deltas:
            code = str(delta["code"])
            volume_delta = int(delta.get("volume_delta", 0))
            fill_price = delta.get("cost_price")
            pos = state.positions.get(code)
            if pos is None:
                if volume_delta <= 0:
                    continue
                pos = _MutablePosition(
                    code=code,
                    volume=volume_delta,
                    today_bought_volume=0,
                    cost_price=(
                        float(fill_price) if fill_price is not None else 0.0
                    ),
                    entry_policy_hash=payload.get("entry_policy_hash"),
                    entry_style=payload.get("entry_style"),
                    entry_sell_stack_version=payload.get(
                        "entry_sell_stack_version"
                    ),
                )
                state.positions[code] = pos
                record_buy_date(pos.bought_by_date, fill_trade_date, volume_delta)
            else:
                if volume_delta > 0 and fill_price is not None:
                    # Weighted average mirrors MockBroker._apply_buy.
                    total_cost = (
                        pos.cost_price * pos.volume
                        + float(fill_price) * volume_delta
                    )
                    new_volume = pos.volume + volume_delta
                    pos.cost_price = (
                        total_cost / new_volume if new_volume > 0 else 0.0
                    )
                    pos.volume = new_volume
                    record_buy_date(
                        pos.bought_by_date, fill_trade_date, volume_delta
                    )
                else:
                    pos.volume += volume_delta
                if pos.volume <= 0:
                    state.positions.pop(code, None)
        return

    # Unknown event_type would have failed Pydantic validation already;
    # reaching here means the StrEnum was extended without updating
    # this dispatch. Fail-closed.
    raise RecoveryError(
        f"replay error: no apply rule for event_type {event.event_type!r}"
    )


async def recover_state(
    event_store: BrokerEventStore,
    snapshot_store: BrokerSnapshotStore,
    initial_capital: float,
) -> RecoveredState:
    """Load latest snapshot, verify checksum, replay newer events.

    Args:
        event_store: append-only broker_events store.
        snapshot_store: append-only broker_snapshots store.
        initial_capital: fallback cash + initial_capital when no
            snapshot exists. The MockBroker config supplies this.

    Returns:
        The rebuilt :class:`RecoveredState`. ``last_sequence`` is the
        sequence of the last event applied (snapshot's sequence + count
        of replayed events).

    Raises:
        ChecksumMismatchError: snapshot checksum mismatch — fail-closed.
        RecoveryError: any other replay invariant breach.
    """
    snapshot = await snapshot_store.read_latest()
    if snapshot is None:
        state = RecoveredState(
            cash=initial_capital,
            frozen_cash=0.0,
            initial_capital=initial_capital,
        )
        replay_from = 0
    else:
        expected_checksum = compute_snapshot_checksum(
            snapshot.cash,
            snapshot.frozen_cash,
            snapshot.initial_capital,
            snapshot.positions,
        )
        if expected_checksum != snapshot.checksum:
            raise ChecksumMismatchError(
                f"snapshot {snapshot.snapshot_id} stored checksum "
                f"{snapshot.checksum!r} != recomputed {expected_checksum!r}; "
                "refusing automatic recovery (P1-2.A red line)"
            )
        # Re-seed the per-date T+1 buy record (P0-4-amendment-2026-06-04):
        # v2 snapshots persist the full per-date map (codex cycle-7 P1 —
        # multi-day buy dates survive the snapshot cursor). v1 rows carry no
        # map → fall back to the today_bought_volume reseed: the EOD snapshot
        # is written BEFORE the 16:30 advance_day cron, so that counter IS
        # the volume bought on the snapshot's own trade date (codex cycle-4
        # P1). Without either, a restart from a checkpoint containing a
        # same-day buy blinds the external-report SELL guard.
        snapshot_day = date.fromisoformat(snapshot.trade_date)

        def _buy_dates(pos: BrokerSnapshotPosition) -> dict[date, int]:
            if pos.bought_by_date:
                return {
                    date.fromisoformat(k): v
                    for k, v in pos.bought_by_date.items()
                }
            if pos.today_bought_volume > 0:
                return {snapshot_day: pos.today_bought_volume}
            return {}

        state = RecoveredState(
            cash=snapshot.cash,
            frozen_cash=snapshot.frozen_cash,
            initial_capital=snapshot.initial_capital,
            positions={
                pos.code: _MutablePosition(
                    code=pos.code,
                    volume=pos.volume,
                    today_bought_volume=pos.today_bought_volume,
                    cost_price=pos.cost_price,
                    bought_by_date=_buy_dates(pos),
                    entry_policy_hash=pos.entry_policy_hash,
                    entry_style=pos.entry_style,
                    entry_sell_stack_version=pos.entry_sell_stack_version,
                )
                for pos in snapshot.positions
            },
            last_sequence=snapshot.last_event_sequence,
        )
        replay_from = snapshot.last_event_sequence

    async for event in event_store.stream_since(replay_from):
        _apply_event(state, event)
        state.last_sequence = event.sequence
        state.events_replayed += 1

    log.info(
        "broker_state_recovered",
        replay_from=replay_from,
        events_replayed=state.events_replayed,
        last_sequence=state.last_sequence,
        positions=len(state.positions),
    )
    return state


__all__ = [
    "ChecksumMismatchError",
    "RecoveredState",
    "RecoveryError",
    "recover_state",
]
