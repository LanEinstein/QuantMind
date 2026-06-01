"""Append-only RotationIntent ledger + churn gates + expiry fallback (V-003).

P0-7-amendment-2026-06-01-five-slot-rotation §1.2 / §1.5. Three concerns, all
deterministic and replayable:

* **Append-only ``RotationIntent`` ledger** (codex round-1 overturned the
  stateless design — a stateless rotation would *lose the sell reason*: if the
  owner sells overnight but the challenger disqualifies the next day, a good
  holding is sold with nothing bought back and the system never knows). The
  ledger records the SELL instruction, both codes, both scores, ``expires_at``,
  and the replay inputs (``signal_id`` + ``config_hash``). This is an **explicit
  small append-only record** — NOT the fragile ``broker_events`` reverse-query
  that P0-10-amendment-line2-2026-05-31 rejected (that rejected reverse-query of
  a different store; an explicit intent record is compliant).

* **Churn gates** (§1.5): <=1 rotation/day, <=1 open intent, rotation subcap
  <=1, **yield to a protective stop / forced exit**, same-incumbent 20-td
  cooldown, same-(challenger,incumbent)-pair 30-td cooldown, and a hard
  ``UNDERINVESTED_ROTATION_EXPIRED`` block.

* **Expiry fallback** (§1.5, anti "sold-but-never-rebought"): once a rotation
  SELL has filled but the replacement BUY has not landed by ``expires_at``,
  resolve deterministically — (1) the original challenger if still qualified,
  else (2) the best ≥P75 qualified challenger, else (3) hold cash + mark
  ``UNDERINVESTED_ROTATION_EXPIRED`` and **block further rotation** until the
  next rebalance / a manual gate clears it.

Red lines: pure (no LLM/agent/mirofish import); never constructs an
InstructionPlan (it only persists intent + decides — the SELL/BUY go through the
builder's single construction point); fail-closed toward inaction.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog
from filelock import FileLock

from backend.slot_portfolio.policy import RotationPolicyConfig, RotationProposal
from backend.slot_portfolio.scoring import ChallengerState

log = structlog.get_logger(component="slot_portfolio.rotation_intent")


class RotationIntentError(RuntimeError):
    """Raised on a corrupt ledger row or a malformed rotation event."""


class RotationEventType(StrEnum):
    """The append-only ledger's event kinds."""

    PROPOSED = "proposed"                  # rotation SELL issued at T-day
    RESOLVED = "resolved"                  # replacement BUY filled (slot rotated)
    EXPIRED = "expired"                    # intent expired; fallback in payload
    UNDERINVESTED_CLEARED = "underinvested_cleared"  # manual gate cleared the block


class ExpiryOutcomeKind(StrEnum):
    """How an expired rotation intent is resolved."""

    FALLBACK_ORIGINAL = "fallback_original_challenger"
    FALLBACK_BEST = "fallback_best_challenger"
    UNDERINVESTED = "underinvested_rotation_expired"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RotationIntent:
    """One PROPOSED rotation — the SELL leg + everything needed to replay/resolve.

    ``intent_id`` is deterministic (no clock / RNG) so the same rotation always
    keys the same ledger row. Trade dates are ``YYYYMMDD`` (lexical order ==
    chronological order). ``signal_id`` + ``config_hash`` pin the Line-1 frame +
    the rotation-policy artifact for bit-exact replay.
    """

    intent_id: str
    created_trade_date: str
    expires_at_trade_date: str
    sell_instruction_id: str
    incumbent_code: str
    challenger_code: str
    incumbent_score: float
    challenger_score: float
    incumbent_percentile: float
    challenger_percentile: float
    signal_id: str
    config_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "created_trade_date": self.created_trade_date,
            "expires_at_trade_date": self.expires_at_trade_date,
            "sell_instruction_id": self.sell_instruction_id,
            "incumbent_code": self.incumbent_code,
            "challenger_code": self.challenger_code,
            "incumbent_score": self.incumbent_score,
            "challenger_score": self.challenger_score,
            "incumbent_percentile": self.incumbent_percentile,
            "challenger_percentile": self.challenger_percentile,
            "signal_id": self.signal_id,
            "config_hash": self.config_hash,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RotationIntent:
        try:
            return cls(
                intent_id=str(raw["intent_id"]),
                created_trade_date=str(raw["created_trade_date"]),
                expires_at_trade_date=str(raw["expires_at_trade_date"]),
                sell_instruction_id=str(raw["sell_instruction_id"]),
                incumbent_code=str(raw["incumbent_code"]),
                challenger_code=str(raw["challenger_code"]),
                incumbent_score=float(raw["incumbent_score"]),
                challenger_score=float(raw["challenger_score"]),
                incumbent_percentile=float(raw["incumbent_percentile"]),
                challenger_percentile=float(raw["challenger_percentile"]),
                signal_id=str(raw["signal_id"]),
                config_hash=str(raw["config_hash"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RotationIntentError(f"malformed RotationIntent row: {exc}") from exc


def build_intent_id(
    created_trade_date: str, incumbent_code: str, challenger_code: str
) -> str:
    """Deterministic intent id (no clock / RNG so it replays identically)."""
    return f"ROT-{created_trade_date}-{incumbent_code}-{challenger_code}"


def build_rotation_intent(
    proposal: RotationProposal,
    *,
    created_trade_date: str,
    expires_at_trade_date: str,
    sell_instruction_id: str,
    signal_id: str,
    config: RotationPolicyConfig,
) -> RotationIntent:
    """Build a :class:`RotationIntent` from an approved rotation proposal.

    Raises:
        RotationIntentError: the proposal is not an actionable rotation (missing
            codes/scores). Fail-closed — never persist a half-formed intent.
    """
    if not (
        proposal.should_rotate
        and proposal.incumbent_code
        and proposal.challenger_code
        and proposal.incumbent_score is not None
        and proposal.challenger_score is not None
        and proposal.incumbent_percentile is not None
        and proposal.challenger_percentile is not None
    ):
        raise RotationIntentError(
            "cannot build intent from a non-actionable rotation proposal"
        )
    return RotationIntent(
        intent_id=build_intent_id(
            created_trade_date, proposal.incumbent_code, proposal.challenger_code
        ),
        created_trade_date=created_trade_date,
        expires_at_trade_date=expires_at_trade_date,
        sell_instruction_id=sell_instruction_id,
        incumbent_code=proposal.incumbent_code,
        challenger_code=proposal.challenger_code,
        incumbent_score=proposal.incumbent_score,
        challenger_score=proposal.challenger_score,
        incumbent_percentile=proposal.incumbent_percentile,
        challenger_percentile=proposal.challenger_percentile,
        signal_id=signal_id,
        config_hash=config.config_hash,
    )


@dataclass(frozen=True)
class RotationEvent:
    """One append-only ledger row. ``intent`` is set only for PROPOSED rows."""

    event_type: RotationEventType
    trade_date: str
    intent_id: str | None = None
    intent: RotationIntent | None = None
    outcome_kind: ExpiryOutcomeKind | None = None
    buy_code: str | None = None
    blocks_further_rotation: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "event_type": self.event_type.value,
            "trade_date": self.trade_date,
            "intent_id": self.intent_id,
            "blocks_further_rotation": self.blocks_further_rotation,
            "note": self.note,
        }
        if self.intent is not None:
            row["intent"] = self.intent.to_dict()
        if self.outcome_kind is not None:
            row["outcome_kind"] = self.outcome_kind.value
        if self.buy_code is not None:
            row["buy_code"] = self.buy_code
        return row

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RotationEvent:
        try:
            event_type = RotationEventType(raw["event_type"])
            intent_raw = raw.get("intent")
            outcome_raw = raw.get("outcome_kind")
            return cls(
                event_type=event_type,
                trade_date=str(raw["trade_date"]),
                intent_id=(
                    None if raw.get("intent_id") is None else str(raw["intent_id"])
                ),
                intent=(
                    RotationIntent.from_dict(intent_raw)
                    if isinstance(intent_raw, dict)
                    else None
                ),
                outcome_kind=(
                    ExpiryOutcomeKind(outcome_raw) if outcome_raw else None
                ),
                buy_code=(
                    None if raw.get("buy_code") is None else str(raw["buy_code"])
                ),
                blocks_further_rotation=bool(raw.get("blocks_further_rotation", False)),
                note=str(raw.get("note", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RotationIntentError(f"malformed rotation event row: {exc}") from exc


# ---------------------------------------------------------------------------
# Append-only ledger store (self-contained JSONL — no cross-module coupling)
# ---------------------------------------------------------------------------


class RotationIntentStore:
    """Append-only JSONL ledger of rotation events; folds derive current state.

    The same insert-only / no-mutation / no-delete discipline as the snapshot
    manifests (P1-2.A). Writes are serialised by a filelock so a concurrent
    appender cannot interleave a partial line; rows are canonical JSON. State
    (open intents, cooldowns, the underinvested block) is *derived* by folding
    the log — never by mutating rows in place.
    """

    def __init__(
        self, path: str | Path, *, lock_path: str | Path | None = None
    ) -> None:
        self._path = Path(path)
        lock = lock_path or f"{self._path}.lock"
        self._lock = FileLock(str(lock))

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: RotationEvent) -> None:
        """Append one event row under the filelock (canonical, append-only)."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        event.to_dict(),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                    + "\n"
                )

    def load_events(self) -> tuple[RotationEvent, ...]:
        """Read all events in append order (offline, no network)."""
        if not self._path.exists():
            return ()
        events: list[RotationEvent] = []
        for lineno, line in enumerate(
            self._path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RotationIntentError(
                    f"corrupt rotation ledger row at {self._path}:{lineno}: {exc}"
                ) from exc
            events.append(RotationEvent.from_dict(row))
        return tuple(events)

    # -- convenience appenders ------------------------------------------

    def record_proposed(self, intent: RotationIntent) -> RotationEvent:
        event = RotationEvent(
            event_type=RotationEventType.PROPOSED,
            trade_date=intent.created_trade_date,
            intent_id=intent.intent_id,
            intent=intent,
            note="rotation SELL issued",
        )
        self.append(event)
        log.info("rotation_intent_proposed", intent_id=intent.intent_id)
        return event

    def record_resolved(
        self,
        intent_id: str,
        *,
        trade_date: str,
        note: str = "replacement BUY filled — slot rotated",
    ) -> RotationEvent:
        """Close an intent in a terminal, non-underinvested state.

        Default note = the replacement BUY landed. The runner also uses this to
        lapse an intent whose rotation SELL the owner never executed (the slot
        was never freed, so there is no under-investment) — passing an explanatory
        ``note`` keeps the ledger auditable without a separate event kind.
        """
        event = RotationEvent(
            event_type=RotationEventType.RESOLVED,
            trade_date=trade_date,
            intent_id=intent_id,
            note=note,
        )
        self.append(event)
        log.info("rotation_intent_resolved", intent_id=intent_id, note=note)
        return event

    def record_expired(
        self, intent_id: str, *, trade_date: str, outcome: ExpiryOutcome
    ) -> RotationEvent:
        event = RotationEvent(
            event_type=RotationEventType.EXPIRED,
            trade_date=trade_date,
            intent_id=intent_id,
            outcome_kind=outcome.kind,
            buy_code=outcome.buy_code,
            blocks_further_rotation=outcome.blocks_further_rotation,
            note=outcome.reason,
        )
        self.append(event)
        log.info(
            "rotation_intent_expired",
            intent_id=intent_id, outcome=outcome.kind.value,
            blocks=outcome.blocks_further_rotation,
        )
        return event

    def record_underinvested_cleared(
        self, *, trade_date: str, note: str
    ) -> RotationEvent:
        event = RotationEvent(
            event_type=RotationEventType.UNDERINVESTED_CLEARED,
            trade_date=trade_date,
            note=note,
        )
        self.append(event)
        log.info("rotation_underinvested_block_cleared", trade_date=trade_date)
        return event

    # -- derived folds --------------------------------------------------

    def open_intents(self) -> tuple[RotationIntent, ...]:
        """PROPOSED intents with no later RESOLVED / EXPIRED for the same id."""
        proposed: dict[str, RotationIntent] = {}
        closed: set[str] = set()
        for ev in self.load_events():
            if ev.event_type is RotationEventType.PROPOSED and ev.intent is not None:
                proposed[ev.intent.intent_id] = ev.intent
            elif (
                ev.event_type in (RotationEventType.RESOLVED, RotationEventType.EXPIRED)
                and ev.intent_id is not None
            ):
                closed.add(ev.intent_id)
        return tuple(
            intent for iid, intent in proposed.items() if iid not in closed
        )

    def last_rotation_date_for_incumbent(self, code: str) -> str | None:
        """Latest PROPOSED ``created_trade_date`` rotating ``code`` out (or None)."""
        dates = [
            ev.intent.created_trade_date
            for ev in self.load_events()
            if ev.event_type is RotationEventType.PROPOSED
            and ev.intent is not None
            and ev.intent.incumbent_code == code
        ]
        return max(dates) if dates else None

    def last_rotation_date_for_pair(
        self, challenger_code: str, incumbent_code: str
    ) -> str | None:
        """Latest PROPOSED date for this (challenger, incumbent) pair (or None)."""
        dates = [
            ev.intent.created_trade_date
            for ev in self.load_events()
            if ev.event_type is RotationEventType.PROPOSED
            and ev.intent is not None
            and ev.intent.challenger_code == challenger_code
            and ev.intent.incumbent_code == incumbent_code
        ]
        return max(dates) if dates else None

    def underinvested_block_active(self) -> bool:
        """True if the latest underinvested block/clear in the log is a block.

        Folded in append order: an EXPIRED-underinvested event sets the block;
        an UNDERINVESTED_CLEARED event (manual gate) clears it.
        """
        block = False
        for ev in self.load_events():
            if (
                ev.event_type is RotationEventType.EXPIRED
                and ev.blocks_further_rotation
            ):
                block = True
            elif ev.event_type is RotationEventType.UNDERINVESTED_CLEARED:
                block = False
        return block


# ---------------------------------------------------------------------------
# Churn gates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChurnGateInputs:
    """Deterministic inputs the orchestration folds from the ledger + day state.

    ``trading_days_since_*`` are computed by the orchestration via the trading
    calendar (``None`` = never rotated); the cooldown comparison itself lives
    here so the thresholds stay in this module's config.
    """

    rotations_today: int
    open_intent_count: int
    daily_new_instruction_budget_remaining: int
    protective_action_needs_cap_today: bool
    underinvested_block_active: bool
    trading_days_since_incumbent_rotation: int | None
    trading_days_since_pair_rotation: int | None


@dataclass(frozen=True)
class RotationGateResult:
    """The churn-gate verdict. ``allowed`` requires an actionable proposal AND
    every gate passing; ``blocked_by`` lists the gates that fired."""

    allowed: bool
    blocked_by: tuple[str, ...]
    reason: str


def apply_churn_gates(
    proposal: RotationProposal,
    inputs: ChurnGateInputs,
    config: RotationPolicyConfig,
) -> RotationGateResult:
    """Gate an actionable rotation proposal against the anti-churn rules.

    Deterministic. A rotation is allowed only when the proposal is actionable
    AND none of the gates fire. ``yield_to_protective_stop`` is the priority
    rule (§1.5): a protective stop / forced exit needing today's cap always
    pre-empts a rotation.
    """
    if not proposal.should_rotate:
        return RotationGateResult(
            allowed=False, blocked_by=("no_proposal",),
            reason="no actionable rotation proposed",
        )

    churn = config.churn
    blocks: list[str] = []
    if inputs.underinvested_block_active:
        blocks.append("underinvested_block")
    if inputs.rotations_today >= churn.max_rotations_per_day:
        blocks.append("daily_rotation_cap")
    if inputs.open_intent_count >= churn.max_open_intents:
        blocks.append("open_intent_cap")
    if inputs.protective_action_needs_cap_today:
        blocks.append("yield_to_protective_stop")
    if inputs.daily_new_instruction_budget_remaining < churn.rotation_subcap:
        blocks.append("insufficient_daily_cap")
    if (
        inputs.trading_days_since_incumbent_rotation is not None
        and inputs.trading_days_since_incumbent_rotation
        < churn.same_incumbent_cooldown_td
    ):
        blocks.append("incumbent_cooldown")
    if (
        inputs.trading_days_since_pair_rotation is not None
        and inputs.trading_days_since_pair_rotation < churn.same_pair_cooldown_td
    ):
        blocks.append("pair_cooldown")

    allowed = not blocks
    reason = (
        "rotation allowed (all churn gates pass)"
        if allowed
        else "rotation blocked: " + ", ".join(blocks)
    )
    return RotationGateResult(
        allowed=allowed, blocked_by=tuple(blocks), reason=reason
    )


# ---------------------------------------------------------------------------
# Expiry + fallback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpiryOutcome:
    """The deterministic resolution of an expired rotation intent."""

    kind: ExpiryOutcomeKind
    intent_id: str
    buy_code: str | None
    blocks_further_rotation: bool
    reason: str


def compute_expires_at(
    max_trading_days_ahead: str, next_rebalance_close: str | None
) -> str:
    """``expires_at = min(N-td-ahead, next rebalance close)`` — YYYYMMDD strings.

    Both are zero-padded ``YYYYMMDD`` so lexical ``min`` == chronological min.
    The trading-day arithmetic (``max_trading_days_ahead``) is done by the
    caller via the calendar; this just takes the earlier of the two horizons.
    """
    if next_rebalance_close is None:
        return max_trading_days_ahead
    return min(max_trading_days_ahead, next_rebalance_close)


def is_expired(intent: RotationIntent, today_trade_date: str) -> bool:
    """True once ``today >= expires_at`` (YYYYMMDD lexical compare)."""
    return today_trade_date >= intent.expires_at_trade_date


def resolve_expiry(
    intent: RotationIntent,
    *,
    original_challenger_qualified: bool,
    best_challenger: ChallengerState | None,
    config: RotationPolicyConfig,
) -> ExpiryOutcome:
    """Resolve an expired rotation whose SELL filled but replacement BUY did not.

    Precondition (the orchestration verifies it from settled positions): the
    rotation SELL has filled (the slot is genuinely free) and no replacement BUY
    has landed by ``expires_at``. The three deterministic fallbacks (§1.5):

    1. the **original challenger** if still qualified;
    2. else the **best qualified challenger at >= P75**;
    3. else **hold cash** + ``UNDERINVESTED_ROTATION_EXPIRED`` (blocks further
       rotation until the next rebalance / a manual gate clears it).

    Never silently under-invests: every path is an explicit, recorded outcome.
    """
    if original_challenger_qualified:
        return ExpiryOutcome(
            kind=ExpiryOutcomeKind.FALLBACK_ORIGINAL,
            intent_id=intent.intent_id,
            buy_code=intent.challenger_code,
            blocks_further_rotation=False,
            reason="expiry fallback: original challenger still qualified",
        )

    if (
        best_challenger is not None
        and best_challenger.qualified
        and math.isfinite(best_challenger.line1_percentile)
        # Bound to [0, 1] like the SELL-side ``scoring._is_pct`` guard — an
        # out-of-range (finite-but-corrupt) percentile must NOT drive a fallback
        # BUY (fail-closed parity with the rotation-proposal path).
        and 0.0 <= best_challenger.line1_percentile <= 1.0
        and best_challenger.line1_percentile >= config.challenger_margin.min_percentile
    ):
        return ExpiryOutcome(
            kind=ExpiryOutcomeKind.FALLBACK_BEST,
            intent_id=intent.intent_id,
            buy_code=best_challenger.code,
            blocks_further_rotation=False,
            reason="expiry fallback: best qualified challenger at >= P75",
        )

    return ExpiryOutcome(
        kind=ExpiryOutcomeKind.UNDERINVESTED,
        intent_id=intent.intent_id,
        buy_code=None,
        blocks_further_rotation=True,
        reason=(
            "expiry fallback: no qualified >= P75 replacement — hold cash, "
            "block further rotation until next rebalance / manual gate"
        ),
    )


__all__ = [
    "ChurnGateInputs",
    "ExpiryOutcome",
    "ExpiryOutcomeKind",
    "RotationEvent",
    "RotationEventType",
    "RotationGateResult",
    "RotationIntent",
    "RotationIntentError",
    "RotationIntentStore",
    "apply_churn_gates",
    "build_intent_id",
    "build_rotation_intent",
    "compute_expires_at",
    "is_expired",
    "resolve_expiry",
]
