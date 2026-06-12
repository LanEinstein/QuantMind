"""MockBroker — full A-share simulation trading engine.

P1-2.C / E-003 enhancements:

* **ALL_OR_NONE** — every place_order either fills the full requested
  volume or returns REJECTED; partial-fill semantics live exclusively on
  the user-reported execution-report path (ExecutionReportApplier).
* **At-fill price-limit recheck** — even after the InstructionPlanBuilder
  early-returns and RiskEngine 14-check clear the order, the MockBroker
  re-verifies against the live quote at fill time using the injected
  :class:`MarketMetaProvider`. A breach raises with the locked reason
  ``price_limit_violation_at_fill`` (distinct from RiskEngine's
  ``limit_up_block`` / ``limit_down_block`` so audit can attribute the
  exact gate that bounced the order).
* **Board-tiered slippage + Shenzhen transfer fee** — all cost math
  delegated to :mod:`backend.broker.cost_calculator` so the friction
  model is unit-testable in isolation.
* **MarketMetaProvider injection** — replaces the orphan helper
  ``get_price_limits()`` that lived at module-level pre-E-003. The new
  provider centralises prev_close + live-price lookups behind a
  two-tier Redis→Mongo fallback (no cost_price fallback per P1-2.B
  §2 red line 6).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

import structlog

from backend.broker.cost_calculator import OrderCostBreakdown, calculate_cost
from backend.broker.interface import IBroker
from backend.broker.models import (
    AccountInfo,
    BrokerConfig,
    Order,
    OrderDirection,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    Trade,
)
from backend.broker.trade_dates import instruction_trade_date, record_buy_date
from backend.data.market_meta_provider import (
    MarketMetaProvider,
    StaleQuoteError,
)
from backend.data.stock_metadata import (
    Board,
    ForbiddenCodeError,
    UnknownCodeError,
    classify_board,
    get_price_limit_pct,
)
from backend.models.execution import REPORT_SCHEMA_V1_OWNER_FEE
from backend.utils.trading_hours import SHANGHAI, is_trading_hours

log = structlog.get_logger(component="mock_broker")

PRICE_LIMIT_VIOLATION_REASON = "price_limit_violation_at_fill"
"""Locked rejection reason emitted when the MockBroker's at-fill
price-limit recheck fires. Distinct from the RiskEngine reasons
``limit_up_block`` / ``limit_down_block`` so audit can attribute the
exact tripwire that bounced the order. Mirrored by
:data:`backend.audit.models.AuditEventType
.MOCKBROKER_PRICE_LIMIT_VIOLATION_AT_FILL`."""


# ---------------------------------------------------------------------------
# Internal mutable state types
# ---------------------------------------------------------------------------


@dataclass
class _MutablePosition:
    code: str
    volume: int = 0
    today_bought_volume: int = 0
    cost_price: float = 0.0
    # P0-4-amendment-2026-06-04: per-trade-date buy volumes. Unlike
    # ``today_bought_volume`` (cleared by the 16:30 ``advance_day`` cron),
    # this map is date-keyed and only buys mutate it, so the external-report
    # T+1 guard stays correct for a late same-day report arriving AFTER the
    # settlement reset (codex P1) and for multi-day buy sequences (a newer
    # buy must not erase an older date's record — codex cycle-3 P1). Pruned
    # to the most recent BOUGHT_BY_DATE_KEEP dates; rebuilt on recovery by
    # the persistence replay.
    bought_by_date: dict[date, int] = field(default_factory=dict)
    # AA-004 (P2-2-amendment-2026-06-12 §1.6) position nameplate: the
    # policy hash / sell-stack version active when the EPISODE opened
    # (volume 0 → >0). Add-on buys keep the original stamp; a position
    # that closes and re-opens gets a fresh one. ``entry_style`` stays
    # None until Phase AC's StyleClassifier lands. A demotion only
    # affects FUTURE entries — held positions ride their entry stack.
    entry_policy_hash: str | None = None
    entry_style: str | None = None
    entry_sell_stack_version: str | None = None

    @property
    def available_volume(self) -> int:
        return self.volume - self.today_bought_volume


@dataclass
class _MutableOrder:
    order_id: str
    code: str
    price: float
    volume: int
    direction: OrderDirection
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING
    filled_volume: int = 0
    avg_fill_price: float = 0.0
    reject_reason: str | None = None
    frozen_amount: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=SHANGHAI))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=SHANGHAI))


# ---------------------------------------------------------------------------
# MockBroker
# ---------------------------------------------------------------------------


class MockBroker(IBroker):
    """Full A-share simulation trading engine.

    Simulates virtual account, order matching, T+1, price limits,
    and friction costs (commission, stamp tax, slippage).
    """

    def __init__(
        self,
        config: BrokerConfig,
        now_func: Callable[[], datetime] | None = None,
        market_meta: MarketMetaProvider | None = None,
    ) -> None:
        self._config = config
        self._now = now_func or (lambda: datetime.now(tz=SHANGHAI))
        self._cash: float = config.initial_capital
        self._frozen_cash: float = 0.0
        self._initial_capital: float = config.initial_capital
        self._orders: dict[str, _MutableOrder] = {}
        self._positions: dict[str, _MutablePosition] = {}
        self._trades: list[Trade] = []
        self._lock = asyncio.Lock()
        self._market_meta = market_meta
        self._log = log
        # AA-004 nameplate source — set once at boot by main.py from the
        # policy manifest; None (unwired/legacy) stamps None.
        self._entry_policy_hash: str | None = None
        self._entry_sell_stack_version: str | None = None
        # AC-001 per-code pending style: the deterministic buy-time style the
        # Line-1 runner registers BEFORE routing each candidate, consumed
        # (popped) when that code's episode opens. Per-code (unlike the global
        # policy nameplate) because style varies per name. Unregistered codes
        # (Line-2 ADDs, recovery) stamp None — the legacy behaviour.
        self._pending_entry_styles: dict[str, str] = {}

    def set_entry_nameplate(
        self,
        *,
        policy_hash: str | None,
        sell_stack_version: str | None,
    ) -> None:
        """Set the nameplate stamped onto NEW position episodes (AA-004).

        Called once at boot by main.py with the active policy-manifest
        hash + the Line-2 trigger stack version. Existing positions are
        untouched — a promotion/demotion only affects future entries
        (P2-2-amendment-2026-06-12 §1.6).
        """
        self._entry_policy_hash = policy_hash
        self._entry_sell_stack_version = sell_stack_version

    @property
    def entry_nameplate(self) -> tuple[str | None, str | None]:
        """(policy_hash, sell_stack_version) stamped on new episodes."""
        return (self._entry_policy_hash, self._entry_sell_stack_version)

    def entry_style_for(self, code: str) -> str | None:
        """The ``entry_style`` stamped on the held ``code`` (None if absent).

        Read by the fill-event writer (AC-001) so the per-code style nameplate
        rides the ORDER_FILLED payload and a recovery replay rebuilds it (the
        global policy nameplate uses ``entry_nameplate``; style is per-code).
        """
        pos = self._positions.get(code)
        return pos.entry_style if pos is not None else None

    def set_pending_entry_style(self, code: str, style: str | None) -> None:
        """Register the buy-time style to stamp on ``code``'s next episode (AC-001).

        Called by the Line-1 runner's style sink BEFORE routing a candidate's
        BUY; consumed (popped) when that code's position episode opens. ``None``
        clears any stale registration. Per-code because style varies per name —
        unlike the global policy/sell-stack nameplate. Display-only: it never
        changes a fill, a risk number, or the matching path.
        """
        if style is None:
            self._pending_entry_styles.pop(code, None)
        else:
            self._pending_entry_styles[code] = style

    def attach_market_meta(self, market_meta: MarketMetaProvider) -> None:
        """Swap in the market-meta provider after construction.

        BrokerRegistry creates the default broker at import time, before
        the data layer (which owns the Mongo-backed MarketMetaProvider)
        is wired. Phase I-001 calls this once during the orchestration
        layer init so the at-fill price-limit recheck (P1-2.C §1.2) is
        live for simulation_auto. Re-attachment is allowed — the broker
        does not cache provider state.
        """
        self._market_meta = market_meta

    async def seed_from_recovery(
        self,
        *,
        cash: float,
        frozen_cash: float,
        initial_capital: float,
        positions: tuple,
    ) -> None:
        """Overwrite the broker mirror from a recovered persistence state.

        Distinct from :meth:`reset_to_snapshot` (used by reconciliation
        where ``frozen_cash`` is intentionally zeroed because user-
        reported truth has no in-flight orders): recovery must preserve
        ``frozen_cash`` so a crash between ORDER_PLACED and ORDER_FILLED
        is reflected correctly in the rebuilt broker. Called once at
        lifespan startup by :func:`_init_orchestration_layer` before
        :class:`SimulationExecutor` is exposed (Codex Cycle 6 P1 fix —
        without this the broker started from initial_capital while the
        durable broker_events tracked thousands of CNY of positions,
        and the first routed order silently diverged the two mirrors).

        ``positions`` is an iterable of objects exposing
        ``code`` / ``volume`` / ``today_bought_volume`` / ``cost_price``
        (e.g. :class:`backend.broker.persistence.snapshots.BrokerSnapshotPosition`
        or the recovery module's ``_MutablePosition``).
        """
        async with self._lock:
            self._cash = float(cash)
            self._frozen_cash = float(frozen_cash)
            self._initial_capital = float(initial_capital)
            self._positions.clear()
            for pos in positions:
                if pos.volume <= 0:
                    continue
                # Per-date buy record (P0-4-amendment-2026-06-04). Carriers
                # differ: recovery's _MutablePosition keys by datetime.date,
                # BrokerSnapshotPosition by ISO string — normalise to date so
                # the T+1 guard's date comparisons always hold. Absent →
                # empty (guard degrades to the over-holding check).
                raw_buys = getattr(pos, "bought_by_date", None) or {}
                bought_by_date = {
                    (k if isinstance(k, date) else date.fromisoformat(k)): int(
                        v
                    )
                    for k, v in raw_buys.items()
                }
                # Nameplate fields ride through recovery when the carrier
                # has them (snapshot v3 / recovery state); absent → None
                # (legacy rows, AA-004 backward compat).
                self._positions[pos.code] = _MutablePosition(
                    code=pos.code,
                    volume=int(pos.volume),
                    today_bought_volume=int(
                        getattr(pos, "today_bought_volume", 0)
                    ),
                    cost_price=float(pos.cost_price),
                    bought_by_date=bought_by_date,
                    entry_policy_hash=getattr(
                        pos, "entry_policy_hash", None
                    ),
                    entry_style=getattr(pos, "entry_style", None),
                    entry_sell_stack_version=getattr(
                        pos, "entry_sell_stack_version", None
                    ),
                )

    async def place_order(
        self,
        code: str,
        price: float,
        volume: int,
        direction: OrderDirection,
        order_type: OrderType,
    ) -> OrderResult:
        """Place an order. ALL_OR_NONE: fills fully or rejects.

        Pipeline (locked under ``self._lock``):

        1. Pre-flight validate (trading hours / volume / cash / T+1).
        2. Classify the board so the cost model + at-fill recheck can
           reason about price limits and SZ transfer fee.
        3. Run :func:`calculate_cost` to derive the per-fill economics
           (slippage-adjusted fill price + friction breakdown).
        4. At-fill price-limit recheck via :class:`MarketMetaProvider`
           when one is wired up; raise reason
           ``price_limit_violation_at_fill`` on breach (distinct from
           RiskEngine reasons for audit attribution).
        5. Freeze cash (BUY) and apply the fill atomically.
        """
        async with self._lock:
            now = self._now()
            order_id = uuid.uuid4().hex[:12]

            try:
                board = classify_board(code)
            except (ForbiddenCodeError, UnknownCodeError) as exc:
                msg = f"Order rejected: {exc}"
                order = _MutableOrder(
                    order_id=order_id, code=code, price=price,
                    volume=volume, direction=direction,
                    order_type=order_type, status=OrderStatus.REJECTED,
                    reject_reason=msg, created_at=now, updated_at=now,
                )
                self._orders[order_id] = order
                return OrderResult(
                    order_id=order_id, success=False, message=msg
                )

            valid, msg = self._validate(
                code, price, volume, direction, now, board
            )
            if not valid:
                order = _MutableOrder(
                    order_id=order_id, code=code, price=price,
                    volume=volume, direction=direction,
                    order_type=order_type, status=OrderStatus.REJECTED,
                    reject_reason=msg, created_at=now, updated_at=now,
                )
                self._orders[order_id] = order
                return OrderResult(
                    order_id=order_id, success=False, message=msg
                )

            # Compute friction now so the BUY freeze is exact (the prior
            # implementation under-froze on commission floor).
            cost = calculate_cost(
                code=code, board=board, order_price=price,
                volume=volume, direction=direction, config=self._config,
            )

            # At-fill price-limit recheck (P1-2.C §1.2).
            limit_check = await self._recheck_price_limit(
                code, board, cost.fill_price, direction, now
            )
            if limit_check is not None:
                order = _MutableOrder(
                    order_id=order_id, code=code, price=price,
                    volume=volume, direction=direction,
                    order_type=order_type, status=OrderStatus.REJECTED,
                    reject_reason=limit_check, created_at=now, updated_at=now,
                )
                self._orders[order_id] = order
                return OrderResult(
                    order_id=order_id, success=False, message=limit_check
                )

            order = _MutableOrder(
                order_id=order_id, code=code, price=price,
                volume=volume, direction=direction,
                order_type=order_type, created_at=now, updated_at=now,
            )

            # Freeze cash for BUY using the exact precomputed net_amount.
            if direction == OrderDirection.BUY:
                order.frozen_amount = cost.net_amount
                self._cash -= cost.net_amount
                self._frozen_cash += cost.net_amount

            self._fill_order(order, cost, now)
            self._orders[order_id] = order

            self._log.info(
                "order_placed",
                order_id=order_id, code=code, board=board.value,
                direction=direction, status=order.status,
            )
            return OrderResult(
                order_id=order_id, success=True, message="Order filled"
            )

    async def _recheck_price_limit(
        self,
        code: str,
        board: Board,
        fill_price: float,
        direction: OrderDirection,
        now: datetime,
    ) -> str | None:
        """Return a reject reason if the at-fill recheck fires, else None.

        ``MarketMetaProvider`` may be absent (legacy test paths); in that
        case the recheck is a no-op so existing fixtures still pass.
        Production wiring always injects the provider via
        :class:`BrokerRegistry`.
        """
        if self._market_meta is None:
            return None
        prev_close = await self._market_meta.get_prev_close(code)
        if prev_close is None or prev_close <= 0:
            return None
        try:
            current = await self._market_meta.get_current_price(code, now=now)
        except StaleQuoteError:
            # Live quote unavailable — reject the order with the locked
            # reason so audit can attribute. Falling back to prev_close
            # silently would defeat the at-fill recheck purpose: the
            # whole point of this gate is to bounce orders against
            # current price drift between Builder check 12 and the fill
            # moment (codex P2; P1-2.B §2 redline 6 forbids cost_price /
            # stale-cache fallback for live decisions).
            return (
                f"Order rejected: {PRICE_LIMIT_VIOLATION_REASON} "
                f"(live quote unavailable; cost_price fallback forbidden)"
            )
        pct = get_price_limit_pct(board)
        upper = round(prev_close * (1.0 + pct), 2)
        lower = round(prev_close * (1.0 - pct), 2)
        if direction is OrderDirection.BUY and (
            fill_price >= upper or current >= upper
        ):
            return (
                f"Order rejected: {PRICE_LIMIT_VIOLATION_REASON} "
                f"(BUY at fill_price={fill_price} hits limit-up "
                f"{upper}; prev_close={prev_close})"
            )
        if direction is OrderDirection.SELL and (
            fill_price <= lower or current <= lower
        ):
            return (
                f"Order rejected: {PRICE_LIMIT_VIOLATION_REASON} "
                f"(SELL at fill_price={fill_price} hits limit-down "
                f"{lower}; prev_close={prev_close})"
            )
        return None

    def _validate(
        self,
        code: str,
        price: float,
        volume: int,
        direction: OrderDirection,
        now: datetime,
        board: Board,
    ) -> tuple[bool, str]:
        """Pre-trade validation checks (ALL_OR_NONE preflight)."""
        if not is_trading_hours(now):
            return False, "Order rejected: outside trading hours"

        if volume <= 0 or volume % 100 != 0:
            return (
                False,
                f"Order rejected: volume {volume} must be "
                f"a positive multiple of 100",
            )

        if price <= 0:
            return False, "Order rejected: price must be positive"

        if direction == OrderDirection.BUY:
            # Probe the cost model so the affordability check matches
            # what the fill path will actually charge (slippage-adjusted
            # fill price + commission floor + SZ transfer fee).
            probe = calculate_cost(
                code=code, board=board, order_price=price,
                volume=volume, direction=direction, config=self._config,
            )
            if self._cash < probe.net_amount:
                return (
                    False,
                    f"Order rejected: insufficient funds "
                    f"(need {probe.net_amount:.2f}, "
                    f"available {self._cash:.2f})",
                )
        else:
            pos = self._positions.get(code)
            if pos is None or pos.volume == 0:
                return False, f"Order rejected: no position for {code}"
            if pos.available_volume < volume:
                return (
                    False,
                    f"Order rejected: insufficient available shares "
                    f"(T+1 restriction, available: {pos.available_volume}, "
                    f"requested: {volume})",
                )

        return True, ""

    def _fill_order(
        self,
        order: _MutableOrder,
        cost: OrderCostBreakdown,
        now: datetime,
    ) -> None:
        """Fill an order ALL_OR_NONE using the pre-computed cost breakdown.

        ``cost`` was computed by :func:`calculate_cost` in
        :meth:`place_order` and includes slippage / commission floor /
        stamp tax / SZ transfer fee. We do NOT recompute here so the
        affordability check and the fill see identical numbers.
        """
        fill_price = cost.fill_price
        gross = cost.gross_amount
        commission = cost.commission
        stamp_tax = cost.stamp_tax
        transfer_fee = cost.transfer_fee
        slippage_cost = cost.slippage_cost

        order.status = OrderStatus.FILLED
        order.filled_volume = order.volume
        order.avg_fill_price = fill_price
        order.updated_at = now

        # net_amount on the trade row is the cash-out for BUY / cash-in
        # for SELL — the cost_calculator already encodes the sign-free
        # value. Trade.net_amount must always be >= 0 per schema.
        trade = Trade(
            trade_id=uuid.uuid4().hex[:12],
            order_id=order.order_id,
            code=order.code,
            price=fill_price,
            volume=order.volume,
            amount=gross,
            direction=order.direction,
            commission=commission,
            stamp_tax=stamp_tax,
            slippage_cost=slippage_cost,
            transfer_fee=transfer_fee,
            net_amount=cost.net_amount,
            traded_at=now,
        )
        self._trades.append(trade)

        if order.direction == OrderDirection.BUY:
            self._apply_buy(
                order.code,
                fill_price,
                order.volume,
                traded_date=now.astimezone(SHANGHAI).date(),
            )
            # Frozen amount equals net_amount (set in place_order) — so
            # delta is always 0 here. The defensive arithmetic is kept
            # to make a future divergence noisy rather than silent.
            self._frozen_cash -= order.frozen_amount
            delta = order.frozen_amount - cost.net_amount
            self._cash += delta
            if self._cash < -0.01:
                self._log.error(
                    "cash_underflow_detected", cash=self._cash, delta=delta
                )
                self._cash = 0.0
        else:
            self._apply_sell(order.code, order.volume)
            self._cash += cost.net_amount

    def _apply_buy(
        self,
        code: str,
        fill_price: float,
        volume: int,
        traded_date: date | None = None,
        lock_today: bool = True,
    ) -> None:
        """Update position for a buy fill.

        ``traded_date`` (the buy's trade date, Shanghai calendar) feeds the
        date-keyed same-day-buy record consumed by the external-report T+1
        guard (P0-4-amendment-2026-06-04); ``None`` (legacy caller) leaves
        the record untouched. ``lock_today=False`` (a 盘后/次日补录 BUY whose
        instruction date is BEFORE today) skips the ``today_bought_volume``
        T+1 lock: those shares settled on instruction-date+1, so locking them
        for the parse day would wrongly freeze a sellable position (codex
        cycle-6 P2).
        """
        pos = self._positions.get(code)
        if pos is None:
            pos = _MutablePosition(
                code=code,
                volume=volume,
                today_bought_volume=volume if lock_today else 0,
                cost_price=fill_price,
                # Nameplate stamped at episode open only (AA-004 + AC-001).
                entry_policy_hash=self._entry_policy_hash,
                entry_sell_stack_version=self._entry_sell_stack_version,
                # Per-code style registered by the Line-1 runner before route;
                # popped so a later unrelated buy of the same code does not
                # reuse a stale label (the runner re-registers per episode).
                entry_style=self._pending_entry_styles.pop(code, None),
            )
            self._positions[code] = pos
        else:
            # Cost averaging. The episode is already open, so the add-on keeps
            # the original nameplate — but still CONSUME any pending style for
            # this code so a registration meant for this delivered add-on cannot
            # linger and stamp a later unrelated episode (codex P2).
            self._pending_entry_styles.pop(code, None)
            total_cost = pos.cost_price * pos.volume + fill_price * volume
            new_volume = pos.volume + volume
            pos.cost_price = total_cost / new_volume
            pos.volume = new_volume
            if lock_today:
                pos.today_bought_volume += volume
        if traded_date is not None:
            record_buy_date(pos.bought_by_date, traded_date, volume)

    def _apply_sell(self, code: str, volume: int) -> None:
        """Update position for a sell fill."""
        pos = self._positions.get(code)
        if pos is None:
            self._log.error("sell_position_missing", code=code, volume=volume)
            return
        pos.volume -= volume
        if pos.volume <= 0:
            del self._positions[code]

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        async with self._lock:
            order = self._orders.get(order_id)
            if order is None or order.status != OrderStatus.PENDING:
                return False

            order.status = OrderStatus.CANCELLED
            order.updated_at = self._now()

            # Unfreeze cash for BUY orders
            if order.direction == OrderDirection.BUY:
                self._frozen_cash -= order.frozen_amount
                self._cash += order.frozen_amount

            self._log.info("order_cancelled", order_id=order_id)
            return True

    async def get_positions(self) -> tuple[Position, ...]:
        """Get all current positions as frozen models."""
        async with self._lock:
            return self._build_positions()

    async def export_bought_by_date(self) -> dict[str, dict[str, int]]:
        """Per-position per-trade-date buy volumes (ISO keys), for snapshots.

        The public :class:`Position` model has no per-date field, so the
        EOD snapshot pipeline reads this dedicated export and persists it
        (BrokerSnapshot v2) — keeping the external-report T+1 guard correct
        across a restart from a checkpoint spanning multi-day buys
        (P0-4-amendment-2026-06-04, codex cycle-7 P1). Read-only copy.
        """
        async with self._lock:
            return {
                code: {d.isoformat(): v for d, v in pos.bought_by_date.items()}
                for code, pos in self._positions.items()
                if pos.bought_by_date
            }

    def _build_positions(self) -> tuple[Position, ...]:
        """Build position snapshots (must be called under lock)."""
        result: list[Position] = []
        for pos in self._positions.values():
            if pos.volume <= 0:
                continue
            mv = pos.cost_price * pos.volume
            pnl = 0.0
            pnl_pct = 0.0
            result.append(
                Position(
                    code=pos.code,
                    volume=pos.volume,
                    available_volume=pos.available_volume,
                    cost_price=round(pos.cost_price, 2),
                    market_value=round(mv, 2),
                    unrealized_pnl=round(pnl, 2),
                    unrealized_pnl_pct=round(pnl_pct, 4),
                    entry_policy_hash=pos.entry_policy_hash,
                    entry_style=pos.entry_style,
                    entry_sell_stack_version=pos.entry_sell_stack_version,
                )
            )
        return tuple(result)

    async def get_account(self) -> AccountInfo:
        """Get current account snapshot as a frozen model."""
        async with self._lock:
            positions = self._build_positions()
            market_value = sum(p.market_value for p in positions)
            total = self._cash + self._frozen_cash + market_value
            pnl = total - self._initial_capital
            pnl_pct = (
                pnl / self._initial_capital
                if self._initial_capital > 0
                else 0.0
            )
            return AccountInfo(
                total_assets=round(total, 2),
                available_cash=round(self._cash, 2),
                frozen_cash=round(self._frozen_cash, 2),
                market_value=round(market_value, 2),
                total_pnl=round(pnl, 2),
                total_pnl_pct=round(pnl_pct, 6),
                initial_capital=self._initial_capital,
            )

    async def get_orders(
        self, status: OrderStatus | None = None
    ) -> tuple[Order, ...]:
        """Get orders, optionally filtered by status."""
        result: list[Order] = []
        for o in self._orders.values():
            if status is not None and o.status != status:
                continue
            result.append(
                Order(
                    order_id=o.order_id,
                    code=o.code,
                    price=o.price,
                    volume=o.volume,
                    filled_volume=o.filled_volume,
                    avg_fill_price=o.avg_fill_price,
                    direction=o.direction,
                    order_type=o.order_type,
                    status=o.status,
                    created_at=o.created_at,
                    updated_at=o.updated_at,
                    reject_reason=o.reject_reason,
                )
            )
        return tuple(result)

    async def get_trades(self) -> tuple[Trade, ...]:
        """Get all executed trades."""
        return tuple(self._trades)

    async def advance_day(self) -> None:
        """Advance to the next trading day (T+1 resolution).

        Makes all today's bought shares available for selling.
        """
        async with self._lock:
            for pos in self._positions.values():
                pos.today_bought_volume = 0
            # AC-001 (codex verify P2): discard any pending entry-style that was
            # NOT consumed by a fill today. A same-day order that never filled has
            # expired, so its registration must not survive to stamp a later
            # day's new episode — the concrete same-day-expiry bound for the
            # per-code style registry.
            self._pending_entry_styles.clear()
            self._log.info("day_advanced")

    # ------------------------------------------------------------------
    # External-write entries (E-004 / P1-2.A red line)
    # ------------------------------------------------------------------

    async def apply_external_fill(
        self,
        *,
        order_id_hint: str,
        code: str,
        volume: int,
        fill_price: float,
        side_is_buy: bool,
        traded_at: datetime,
        report_id: str,
        kind: str,
        report_schema_version: int,
        fee: float | None = None,
    ) -> dict:
        """Apply a user-reported fill to the broker mirror.

        Called exclusively by :class:`backend.broker.appliers
        .ExecutionReportApplier`; direct mutation of ``_cash`` /
        ``_positions`` / ``_trades`` from outside the broker is a red
        line (P1-2.A §2 redline 1).

        Two cost schemas (P0-4-amendment-2026-05-27 §2.4):

        * **v1 (legacy)** — the owner reported the ``fee`` itself; it is
          applied as the whole commission on the synthesized Trade with
          stamp tax / transfer fee folded in. The position cost basis is
          the raw fill price. Never produced by the current parser; kept
          for deterministic replay of any persisted v1 event.
        * **v2 (current)** — the owner reports「price + volume」only; the
          system derives commission (万分之1.5 floored at 5 CNY) + stamp
          tax (SELL only) + Shenzhen 过户费 via
          :func:`backend.broker.cost_calculator.calculate_cost` with
          ``apply_slippage_model=False`` (the reported price IS the real
          fill, slippage already embedded). For a BUY the position cost
          basis is the **fee-inclusive** per-share cost
          ``net_amount / volume`` (P0-4-amendment §2.2), so the weighted
          average reflects the true acquisition cost.

        Returns a dict with the keys ``order_id``, ``trade_id``,
        ``cash_delta``, ``positions_delta`` plus the derived friction
        breakdown (``commission`` / ``stamp_tax`` / ``transfer_fee`` /
        ``net`` / ``gross``) and ``report_schema_version`` so the applier
        can persist a version-tagged BrokerEvent.
        """
        if volume <= 0:
            raise ValueError(f"apply_external_fill volume {volume} must be > 0")
        if fill_price <= 0:
            raise ValueError(
                f"apply_external_fill fill_price {fill_price} must be > 0"
            )
        direction = OrderDirection.BUY if side_is_buy else OrderDirection.SELL

        # Derive the full per-fill economics ONCE, before taking the lock.
        # v1 trusts the owner-reported fee verbatim; v2 delegates every
        # number (gross / commission / stamp / transfer / net / fill_price)
        # to the locked cost model so there is a single source of truth —
        # re-deriving gross/net here from the raw fill_price would diverge
        # from the fee, which is computed off the settlement-rounded price
        # (codex/claude review: dual-gross inconsistency on sub-0.01 prices).
        if report_schema_version == REPORT_SCHEMA_V1_OWNER_FEE:
            if fee is None:
                raise ValueError(
                    "apply_external_fill v1 (owner-fee) requires fee"
                )
            trade_price = fill_price
            gross = round(fill_price * volume, 2)
            commission = round(fee, 2)
            stamp_tax = 0.0
            transfer_fee = 0.0
            slippage_cost = 0.0
            if side_is_buy:
                net = round(gross + commission + transfer_fee, 2)
                # Legacy: cost basis ignores fee.
                buy_cost_basis = fill_price
            else:
                net = round(gross - commission - stamp_tax - transfer_fee, 2)
                if net < 0:
                    raise ValueError(
                        f"apply_external_fill SELL friction "
                        f"{commission + stamp_tax + transfer_fee} exceeds "
                        f"gross {gross}; reject upstream"
                    )
                buy_cost_basis = fill_price  # unused on SELL
        else:
            if fee is not None:
                raise ValueError(
                    "apply_external_fill v2 (system-fee) must not receive "
                    "an owner fee; the system computes it"
                )
            # calculate_cost raises if a SELL's friction exceeds its gross.
            breakdown = calculate_cost(
                code=code,
                board=classify_board(code),
                order_price=fill_price,
                volume=volume,
                direction=direction,
                config=self._config,
                apply_slippage_model=False,
            )
            trade_price = breakdown.fill_price
            gross = breakdown.gross_amount
            commission = breakdown.commission
            stamp_tax = breakdown.stamp_tax
            transfer_fee = breakdown.transfer_fee
            slippage_cost = breakdown.slippage_cost  # 0 — no slippage model
            net = breakdown.net_amount
            # Fee-inclusive cost basis on BUY (P0-4-amendment §2.2);
            # unchanged cost basis on SELL (value below is unused there).
            buy_cost_basis = net / volume if side_is_buy else trade_price

        async with self._lock:
            order_id = f"ext-{uuid.uuid4().hex[:8]}"
            trade_id = uuid.uuid4().hex[:12]

            if side_is_buy:
                # P0-4-amendment-2026-06-01: the report's volume is the owner's
                # actual execution (no longer cross-checked == plan.volume), so
                # an IMPOSSIBLE over-buy (a volume/price typo — e.g. an extra
                # zero) can reach here. A fill the account cannot afford is not
                # "truth"; reject it (mirrors the SELL over-holding guard below)
                # before mutating, so the orchestrator sends a clarification
                # rather than silently driving _cash negative.
                if net > self._cash:
                    raise ValueError(
                        f"apply_external_fill BUY net {net} exceeds available "
                        f"cash {self._cash} ({volume}@{code}); reported fill is "
                        f"unaffordable (likely a volume/price typo)"
                    )
                cash_delta = -net
                self._cash -= net
                buy_trade_date = instruction_trade_date(
                    order_id_hint, traded_at
                )
                self._apply_buy(
                    code,
                    buy_cost_basis,
                    volume,
                    # The instruction's embedded date, not parsed_at: a late
                    # next-day BUY 补录 still bought on the instruction date,
                    # so those shares must unlock the day AFTER that date.
                    traded_date=buy_trade_date,
                    # A backfilled BUY whose instruction date is before today
                    # is already SETTLED — locking it in today_bought_volume
                    # would wrongly freeze a sellable position until the next
                    # advance_day (codex cycle-6 P2).
                    lock_today=(
                        buy_trade_date
                        >= self._now().astimezone(SHANGHAI).date()
                    ),
                )
                positions_delta = [
                    {
                        "code": code,
                        "volume_delta": volume,
                        "cost_price": buy_cost_basis,
                    }
                ]
            else:
                cash_delta = net
                pos = self._positions.get(code)
                if pos is None or pos.volume < volume:
                    raise ValueError(
                        f"apply_external_fill SELL {volume}@{code} but "
                        f"available volume is {pos.volume if pos else 0}"
                    )
                # P0-4-amendment-2026-06-04: a report selling shares bought
                # the SAME trade date could not have executed at the real
                # broker (T+1) — it is a typo, not truth. Reject before
                # mutating (mirror of the BUY affordability guard above) so
                # the orchestrator clarifies instead of silently desyncing
                # the mirror. The check is keyed on the DATE-stamped buy
                # record, not today_bought_volume: the 16:30 advance_day cron
                # clears the counter, which would let a late same-day report
                # bypass a counter-based guard (codex P1). The trade date
                # comes from the instruction id, not parsed_at — a next-day
                # 补录 report still refers to the instruction-date execution
                # (codex cycle-2 P1). Distinct message from the over-holding
                # guard so ops can tell "more than held" from "held but not
                # yet settled".
                report_trade_date = instruction_trade_date(
                    order_id_hint, traded_at
                )
                # Sellable AS OF the report's trade date: shares bought ON
                # that date were unsettled (T+1) and shares bought AFTER it
                # did not exist yet — both must be excluded, else a
                # backfilled SELL dated D passes against a position built by
                # later buys (codex cycle-6 P1). Sells applied since D only
                # shrink pos.volume, so this bound under-estimates (a rare
                # complex backfill may be falsely rejected → human
                # clarification; it can never over-accept).
                unavailable_on_date = sum(
                    v
                    for d, v in pos.bought_by_date.items()
                    if d >= report_trade_date
                )
                sellable = pos.volume - unavailable_on_date
                if volume > sellable:
                    raise ValueError(
                        f"apply_external_fill SELL {volume}@{code} violates "
                        f"T+1: only {sellable} settled shares were sellable "
                        f"on {report_trade_date} ({unavailable_on_date} "
                        f"bought on/after that date); the real broker could "
                        f"not have executed this fill (likely a typo)"
                    )
                self._apply_sell(code, volume)
                self._cash += net
                positions_delta = [
                    {
                        "code": code,
                        "volume_delta": -volume,
                        "cost_price": trade_price,
                    }
                ]

            trade = Trade(
                trade_id=trade_id,
                order_id=order_id,
                code=code,
                price=trade_price,
                volume=volume,
                amount=gross,
                direction=direction,
                commission=commission,
                stamp_tax=stamp_tax,
                slippage_cost=slippage_cost,
                transfer_fee=transfer_fee,
                net_amount=net,
                traded_at=traded_at,
            )
            self._trades.append(trade)

            # Record a synthetic order so /api/trades + UI can show it.
            order = _MutableOrder(
                order_id=order_id,
                code=code,
                price=trade_price,
                volume=volume,
                direction=direction,
                order_type=OrderType.LIMIT,
                status=OrderStatus.FILLED,
                filled_volume=volume,
                avg_fill_price=trade_price,
                created_at=traded_at,
                updated_at=traded_at,
            )
            self._orders[order_id] = order

            self._log.info(
                "external_fill_applied",
                report_id=report_id,
                instruction_id=order_id_hint,
                kind=kind,
                code=code,
                volume=volume,
                fill_price=fill_price,
                cash_delta=cash_delta,
                report_schema_version=report_schema_version,
                commission=commission,
            )
            return {
                "order_id": order_id,
                "trade_id": trade_id,
                "cash_delta": cash_delta,
                "positions_delta": positions_delta,
                "gross": gross,
                "commission": commission,
                "stamp_tax": stamp_tax,
                "transfer_fee": transfer_fee,
                "net": net,
                "report_schema_version": report_schema_version,
            }

    async def reset_to_snapshot(
        self,
        *,
        cash: float,
        positions: tuple,
        reset_at: datetime,
        reason: str,
    ) -> dict:
        """Overwrite the broker mirror with a target snapshot.

        Called exclusively by :class:`backend.broker.appliers
        .ReconciliationApplier` (or the mode-switch lifecycle in
        D-005). ``positions`` is an iterable of objects exposing
        ``code`` / ``volume`` / ``cost_price`` — both
        :class:`backend.models.reconciliation.ReportedPosition` and
        :class:`backend.broker.persistence.snapshots.BrokerSnapshotPosition`
        satisfy this duck type.

        Returns a dict describing the delta against the prior state so
        the applier can include it in the BrokerEvent payload.
        """
        async with self._lock:
            prior_cash = self._cash
            prior_positions = {
                code: pos.volume for code, pos in self._positions.items()
            }

            self._cash = float(cash)
            self._frozen_cash = 0.0
            self._positions.clear()
            for pos in positions:
                if pos.volume <= 0:
                    continue
                # Reconciliation rewrite: a user-reported position has no
                # nameplate → None (origin = reconciliation_reset, AA-004).
                # Carriers that do expose one (amended snapshots built from
                # system state) keep it.
                self._positions[pos.code] = _MutablePosition(
                    code=pos.code,
                    volume=int(pos.volume),
                    today_bought_volume=0,
                    cost_price=float(pos.cost_price),
                    entry_policy_hash=getattr(
                        pos, "entry_policy_hash", None
                    ),
                    entry_style=getattr(pos, "entry_style", None),
                    entry_sell_stack_version=getattr(
                        pos, "entry_sell_stack_version", None
                    ),
                )

            new_positions = {
                code: pos.volume for code, pos in self._positions.items()
            }
            positions_delta = []
            for code in sorted(set(prior_positions) | set(new_positions)):
                delta = new_positions.get(code, 0) - prior_positions.get(code, 0)
                if delta == 0:
                    continue
                positions_delta.append(
                    {
                        "code": code,
                        "volume_delta": delta,
                        "cost_price": (
                            self._positions[code].cost_price
                            if code in self._positions
                            else 0.0
                        ),
                    }
                )

            self._log.info(
                "broker_reset_to_snapshot",
                reason=reason,
                reset_at=reset_at.isoformat(),
                cash=cash,
                positions=len(self._positions),
            )
            return {
                "cash_delta": round(self._cash - prior_cash, 2),
                "positions_delta": positions_delta,
            }
