"""Production Line-1 context provider (Phase U-D1b).

The Line-1 runner (:class:`backend.orchestration.line1_runner.Line1Runner`) is
import-isolated: it orchestrates the BUY-selection *flow* (screen → budget tier
→ select → ONE 4-agent debate → assemble_plan → route) but never imports
``backend.{risk,broker,data}``. The heavy per-lead risk/broker objects the
debate + the single construction point consume are supplied by a caller-built
:class:`backend.orchestration.line1_runner.Line1ContextProvider` — *"built by the
U-D1 scheduler / main.py"*. This module holds that production provider (this
package legitimately imports risk/broker/data; the redline only forbids those
inside ``backend/orchestration``).

Mirror of ``backend.services.line2_context_providers`` (U-D1) for Line-1:

* ``build_line1_run_state`` (async) pulls the live account / positions / halt
  state off the broker once per daily run;
* :class:`Line1ContextProvider` (sync ``build_lead_context``) is a pure
  assembler — it derives the lead's PIT prev_close from the T-1 EOD frame,
  sizes a deterministic BUY volume, and hands the runner a TeamContext (debate)
  + an AssemblyContext factory (the 14-check single construction point).

LLM red line: the provider only *passes through* the injected LLM router into
the debate's :class:`TeamContext`. It derives no decision field from an LLM —
``side`` is the fund_manager's downstream proposal; ``volume`` / ``limit_price``
are deterministic (R0 §4 InstructionPlan single construction point).

Real-data seams validated in U-D3 (1 real trading day) / U-D4 (real smoke),
recorded so they are not silently shipped half-wired:

* ``prev_close`` is parsed from the same T-1 EOD frame (PIT, replay-stable). The
  intraday-spot limit-up recheck against a *live* quote is a U-D3 item.
* ``today_instruction_count`` defaults to ``0`` here (the daily-new-instruction
  cap input); the real broker_events-derived count is wired in U-D3.
* ``data_quality`` falls back to a clean state — the per-code DataQualityProvider
  probe is a U-D3 item (and, unlike Line-2 held codes, the Line-1 lead is only
  known *after* the screen runs inside the runner, so the clean fallback is the
  honest U-D1b baseline).
* ``listed_at_trading_days`` is permissive: the screener has already hard-excluded
  new (≤30d) / sub-new (≤180d) names, so any lead survived the IPO-age gate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import floor, isfinite
from typing import Any

import structlog

from backend.agents_team.state import CandidateBrief, TeamContext
from backend.broker.models import AccountInfo, Position, RiskConfig
from backend.data.data_quality import DataQualityState
from backend.data.stock_metadata import (
    ForbiddenCodeError,
    UnknownCodeError,
    classify_board,
    get_lot_size,
)
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import DataSnapshot
from backend.monitoring.add_position import parse_held_series
from backend.orchestration.line1_runner import CommittedBuy, Line1LeadContext
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.screening.screener import CandidateRow
from backend.services.instruction_plan_builder import (
    AssemblyContext,
    WatchlistMarketSignal,
)
from backend.services.line2_context_providers import (
    clean_data_quality,
    derive_halt_until,
    risk_meta_for,
)
from backend.services.universe_policy import UniversePolicy

log = structlog.get_logger(component="services.line1_context_provider")

# A permissive listed-age: the screener has already hard-excluded new/sub-new
# names, so a Line-1 lead is always past the IPO-age gate (recorded for U-D3 —
# the real listed_trading_days column threads through then).
_LISTED_PERMISSIVE_DAYS = 720

# Returned by ``per_lot_cost`` for a code whose board cannot be classified: a
# non-finite lot cost makes the BudgetTierPolicy mark the candidate UNAFFORDABLE
# (fail-closed) instead of the provider guessing a board.
_UNAFFORDABLE_LOT_COST = float("inf")

# Mirrors RiskEngine check 4 (fund_sufficiency): estimated_cost = price × volume
# × 1.001 (≈0.1% commission headroom). Sizing the cash cap WITHOUT this buffer
# overshoots — a cash-bound order's true cost would exceed available_cash and be
# REJECTED (codex U-D1b finding 1).
_FUND_SUFFICIENCY_BUFFER = 1.001


def _bare_code(code: str) -> str:
    """Strip an exchange suffix (``600000.SH`` → ``600000``)."""
    return code.split(".")[0].strip()


def _apply_committed(
    account: AccountInfo,
    positions: tuple[Position, ...],
    committed: tuple[CommittedBuy, ...],
) -> tuple[AccountInfo, tuple[Position, ...]]:
    """Fold basket BUYs already routed this run into account + positions.

    Each committed BUY debits cash by its *check-4 cost* (``volume × price ×
    fee buffer`` — the same ``× 1.001`` RiskEngine check 4 applies, so later
    candidates do not see cash overstated by the prior orders' fees, codex P2)
    and adds the shares (valued at notional) to ``positions`` (same-code
    merged). ``total_assets`` is recomputed (it drops by the cumulative fees —
    a real draw-down). Sizing + the authoritative 14-check then see the
    in-flight basket, so a multi-candidate BASKET stays collectively ≤ available
    cash (check 4) and ≤ the 70% total-position cap (check 8) —
    P1-7-amendment-2026-05-26 §2.3. Empty ``committed`` returns the inputs
    unchanged (SINGLE mode / the first BASKET candidate = U-D1b path).
    """
    if not committed:
        return account, positions
    notional = sum(cb.volume * cb.limit_price for cb in committed)
    cash_debit = notional * _FUND_SUFFICIENCY_BUFFER  # mirror check 4 (price×vol×1.001)
    adj_cash = account.available_cash - cash_debit
    adj_market_value = account.market_value + notional
    adj_account = account.model_copy(
        update={
            "available_cash": adj_cash,
            "market_value": adj_market_value,
            # Recompute net worth: the cumulative fees are a real draw-down.
            "total_assets": adj_cash + account.frozen_cash + adj_market_value,
        }
    )
    by_code: dict[str, Position] = {p.code: p for p in positions}
    for cb in committed:
        add_value = cb.volume * cb.limit_price
        existing = by_code.get(cb.code)
        if existing is not None:
            by_code[cb.code] = existing.model_copy(
                update={
                    "volume": existing.volume + cb.volume,
                    "market_value": existing.market_value + add_value,
                }
            )
        else:
            by_code[cb.code] = Position(
                code=cb.code,
                volume=cb.volume,
                available_volume=0,  # bought today; T+1 unsettled (SELL N/A)
                cost_price=cb.limit_price,
                market_value=add_value,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
            )
    return adj_account, tuple(by_code.values())


def max_compliant_buy_volume(
    *,
    last_price: float,
    total_assets: float,
    available_cash: float,
    other_positions_value: float,
    existing_shares: int,
    lot_size: int,
    max_single_stock_pct: float,
    max_total_position_pct: float,
    max_single_instruction_amount: float,
    concentration_exception: bool,
    exception_max_lots: int,
) -> int:
    """Largest whole-lot BUY volume that VALIDATES against the 14-check.

    Each cap below bounds THIS ORDER's shares so they ``min`` directly; the
    helper mirrors the four value/quantity RiskEngine BUY checks so the proposed
    plan VALIDATES (rather than getting REJECTED — and therefore failing to
    route a compliant trade a smaller order would have):

    * **check 4** — order notional × ``_FUND_SUFFICIENCY_BUFFER`` ≤ cash;
    * **check 5** — the *resulting* single-stock value (existing held + order) ≤
      ``max_single_stock_pct`` of total assets. An over-15% whitelisted-ETF buy
      instead caps the resulting position at ``exception_max_lots`` lots (the
      engine exempts check 5 only there — checks 4/8/9 still apply);
    * **check 8** — total holdings (this order + existing same-code + the other
      positions' market value) ≤ ``max_total_position_pct`` of total assets;
    * **check 9** — order notional ≤ ``max_single_instruction_amount``.

    Returns a whole-lot share count, floored to ≥1 lot. The upstream budget
    tier already proved 1 lot is affordable + ≤15% for a fresh pick, so the
    floor is reached only when an existing holding / a loaded book leaves no
    room — and there the RiskEngine 14-check is the authoritative REJECT, so the
    floor never bypasses a cap, it only avoids a 0-volume plan (Pydantic
    ``volume > 0``).
    """
    if not isfinite(last_price) or last_price <= 0 or lot_size <= 0:
        # A bad price is rejected downstream (builder price reasonability /
        # engine); return a single lot so the plan still constructs.
        return lot_size if lot_size > 0 else 0
    by_instruction = max_single_instruction_amount / last_price  # check 9
    by_cash = available_cash / (last_price * _FUND_SUFFICIENCY_BUFFER)  # check 4
    # check 8: (existing + order) × price + other_value ≤ pct × total_assets.
    by_total = (
        max_total_position_pct * total_assets - other_positions_value
    ) / last_price - existing_shares
    if concentration_exception:
        # The engine grants the over-15% ETF exception only while the resulting
        # position ≤ max_lots × lot_size — size the order to fill that cap.
        by_single_stock: float = exception_max_lots * lot_size - existing_shares
    else:
        # check 5: (existing + order) × price ≤ pct × total_assets.
        by_single_stock = (
            max_single_stock_pct * total_assets
        ) / last_price - existing_shares
    order_cap = min(by_single_stock, by_instruction, by_cash, by_total)
    lots = floor(order_cap / lot_size) if isfinite(order_cap) else 0
    return max(lots, 1) * lot_size


@dataclass(frozen=True)
class Line1RunState:
    """Run-wide risk/broker inputs shared across one daily Line-1 run."""

    account: AccountInfo
    positions: tuple[Position, ...]
    risk_engine: RiskEngine
    circuit_breaker: CircuitBreaker
    watchlist_policy: UniversePolicy
    risk_config: RiskConfig
    open_tickets: tuple[Any, ...] = ()
    today_instruction_count: int = 0
    halted: bool = False
    halt_until: datetime | None = None


class Line1ContextProvider:
    """Production :class:`Line1ContextProvider` (T-1 EOD frame BUY selection).

    Structurally satisfies the runner's ``Line1ContextProvider`` protocol. The
    sync ``build_lead_context`` is a pure assembler over the pre-fetched
    :class:`Line1RunState` + the frame, so no ``await`` is needed once the
    run-state is built (the lead is only known after the runner's screen, so it
    cannot be pre-assembled per-code the way Line-2 held codes are).
    """

    def __init__(
        self,
        *,
        run_state: Line1RunState,
        frame: MarketDataSnapshot,
        llm_router: Any,
        now: datetime,
        data_quality: DataQualityState | None = None,
    ) -> None:
        self._run = run_state
        self._frame = frame
        self._llm_router = llm_router
        self._now = now
        # No per-code DataQualityProvider in the U-D1b baseline (the lead is
        # unknown until the screen runs); clean fallback, real probe = U-D3.
        self._data_quality = data_quality or clean_data_quality()

    @property
    def available_cash(self) -> float:
        """Investable cash for the budget tier (``account.available_cash``)."""
        return self._run.account.available_cash

    def per_lot_cost(self, code: str, last_price: float) -> float:
        """One A-share lot cost in ¥ (``last_price × lot_size``).

        Fail-closed to a non-finite cost for a forbidden / unclassifiable code
        so the BudgetTierPolicy excludes it as UNAFFORDABLE rather than the
        provider guessing a board (the screener should have excluded it first).
        """
        try:
            board = classify_board(_bare_code(code))
        except (ForbiddenCodeError, UnknownCodeError):
            log.info("line1_per_lot_cost_unknown_board", code=code)
            return _UNAFFORDABLE_LOT_COST
        return last_price * get_lot_size(board)

    def build_lead_context(
        self,
        lead: CandidateRow,
        *,
        concentration_exception: bool = False,
        committed: tuple[CommittedBuy, ...] = (),
    ) -> Line1LeadContext:
        """Build the TeamContext + AssemblyContext factory for the lead.

        ``concentration_exception`` (the lead's budget-tier over-15% ETF flag)
        threads into BOTH the debate ``TeamContext`` (so the debate's risk-gate
        node does not record a REJECTED decision that contradicts the routed
        plan) AND the ``AssemblyContext`` (the authoritative 14-check). The
        RiskEngine still independently re-derives ETF + whitelist + ≤max_lots,
        so the flag never bypasses the cap on its own (U-C4).

        ``committed`` are the BUYs already routed earlier in this BASKET run;
        they are folded into the account (cash ↓) + positions (value ↑) so this
        candidate is sized + validated against the post-commitment state, and
        the basket stays collectively cash- + 70%-compliant
        (P1-7-amendment-2026-05-26 §2.3).
        """
        rs = self._run
        account, positions = _apply_committed(rs.account, rs.positions, committed)
        bare = _bare_code(lead.code)
        limit_price = round(lead.last_price, 2)
        prev_close = self._prev_close_from_frame(bare, fallback=limit_price)
        # Match the RiskEngine's own exact-code comparison (checks 5 + 8 net
        # the held same-code position by ``p.code == order.code`` and value the
        # OTHER positions at their snapshot market_value) so the sizing math
        # mirrors the gate it must pass.
        existing_shares = sum(
            p.volume for p in positions if p.code == lead.code
        )
        other_positions_value = sum(
            p.market_value for p in positions if p.code != lead.code
        )
        limits = rs.risk_config.position_limits
        exception = rs.risk_config.concentration_exception
        volume = max_compliant_buy_volume(
            last_price=limit_price,
            total_assets=account.total_assets,
            available_cash=account.available_cash,
            other_positions_value=other_positions_value,
            existing_shares=existing_shares,
            lot_size=limits.volume_lot_size,
            max_single_stock_pct=limits.max_single_stock_pct,
            max_total_position_pct=limits.max_total_position_pct,
            max_single_instruction_amount=limits.max_single_instruction_amount,
            concentration_exception=concentration_exception,
            exception_max_lots=exception.max_lots,
        )
        stock_meta = risk_meta_for(bare, lead.name)
        daily_state = DailyTradingState(
            # Count the BUYs already routed earlier in this BASKET run so the
            # ≤5-orders/day cap (check 10) binds ACROSS the basket, not just the
            # day's pre-run count (codex P1) — otherwise a partially-used day
            # could route more than its remaining order slots.
            today_new_instruction_count=rs.today_instruction_count + len(committed),
            # Daily-loss + consecutive-loss breaker inputs default to 0/(): a
            # pre-open / morning-open BUY scan is unaffected (real day-open NAV +
            # ledger PnLs wired in U-D3 for the breaker to bind a BUY).
            today_portfolio_pnl_pct=0.0,
            last_3_trade_pnls=(),
            current_price=limit_price,
            is_in_halt_cooldown=rs.halted,
            halt_until=rs.halt_until,
        )
        watchlist_signal = WatchlistMarketSignal(
            listed_at_trading_days=_LISTED_PERMISSIVE_DAYS,
            avg_amount_20d_yuan=lead.factors.avg_amount_20d,
            last_price_yuan=limit_price,
        )
        brief = CandidateBrief(
            code=lead.code,
            name=lead.name,
            proposed_volume=volume,
            proposed_limit_price=limit_price,
        )
        team_context = TeamContext(
            risk_engine=rs.risk_engine,
            account=account,
            positions=positions,
            prev_close=prev_close,
            daily_state=daily_state,
            stock_meta=stock_meta,
            concentration_exception=concentration_exception,
            now=self._now,
            llm_router=self._llm_router,
        )

        def make_assembly_context(
            *,
            signal_id: str,
            seq: int,
            debate_round_count: int,
            analysis_record_id: str,
            risk_validation_id: str,
        ) -> AssemblyContext:
            return AssemblyContext(
                stock_code=lead.code,
                stock_name=lead.name,
                now=self._now,
                open_tickets=rs.open_tickets,
                circuit_breaker=rs.circuit_breaker,
                data_quality=self._data_quality,
                watchlist_policy=rs.watchlist_policy,
                watchlist_signal=watchlist_signal,
                risk_engine=rs.risk_engine,
                account=account,
                positions=positions,
                prev_close=prev_close,
                daily_state=daily_state,
                stock_meta=stock_meta,
                proposed_volume=volume,
                proposed_limit_price=limit_price,
                concentration_exception=concentration_exception,
                seq=seq,
                signal_id=signal_id,
                analysis_record_id=analysis_record_id,
                risk_validation_id=risk_validation_id,
                debate_round_count=debate_round_count,
                evidence_ids=(),
                data_snapshot=DataSnapshot(
                    # The T-1 EOD frame fetch time is strictly before the
                    # 09:35 run ``now``, so the InstructionPlan strictly-before
                    # invariant holds (real intraday limit-up recheck = U-D3).
                    snapshot_at=self._frame.fetch_time_utc,
                    quote_source="primary",
                    is_trading_day=True,
                    is_trading_hours=True,
                    prev_close=prev_close,
                ),
                invalidation_summary="Line-1 daily BUY",
            )

        return Line1LeadContext(
            brief=brief,
            team_context=team_context,
            make_assembly_context=make_assembly_context,
        )

    def _prev_close_from_frame(self, bare: str, *, fallback: float) -> float:
        """Derive the lead's prev_close (prior bar) from the T-1 EOD frame.

        Parses the same canonical CSV frame the screener consumed (PIT,
        replay-stable). Falls back to ``fallback`` (the last price) when the
        frame lacks ≥2 closes for the code, or the prior bar is non-positive — a
        0% move is always price-reasonable and never trips the limit-up band, so
        the fallback is conservative. The non-positive guard matters because
        ``DataSnapshot.prev_close`` has a ``gt=0.0`` constraint (a 0/negative
        prior bar from a data glitch would otherwise raise a ValidationError and
        abort the whole daily run), and the screener's positive-price gate only
        validates the LAST close, not the prior bar (codex U-D1b finding 3).
        """
        try:
            series = parse_held_series(self._frame, [bare])
        except (ValueError, KeyError) as exc:
            log.info("line1_prev_close_parse_failed", code=bare, error=str(exc))
            return fallback
        parsed = series.get(bare)
        if not parsed:
            return fallback
        closes = parsed[0]
        if len(closes) >= 2 and closes[-2] > 0:
            return closes[-2]
        return fallback


async def build_line1_run_state(
    *,
    broker: Any,
    risk_engine: RiskEngine,
    circuit_breaker: CircuitBreaker,
    watchlist_policy: UniversePolicy,
    risk_config: RiskConfig,
    now: datetime,
    open_tickets: Sequence[Any] = (),
    today_instruction_count: int = 0,
) -> Line1RunState:
    """Assemble the run-wide :class:`Line1RunState` from live broker state.

    Mirrors ``build_line2_run_state``: ``today_instruction_count`` is an input
    (default 0) so the caller can wire the real broker_events count in U-D3
    without this function reaching into private assembler helpers.
    """
    account = await broker.get_account()
    positions = tuple(await broker.get_positions())
    halted = circuit_breaker.is_halted(now)
    return Line1RunState(
        account=account,
        positions=positions,
        risk_engine=risk_engine,
        circuit_breaker=circuit_breaker,
        watchlist_policy=watchlist_policy,
        risk_config=risk_config,
        open_tickets=tuple(open_tickets),
        today_instruction_count=today_instruction_count,
        halted=halted,
        halt_until=derive_halt_until(circuit_breaker, halted=halted),
    )


__all__ = [
    "Line1ContextProvider",
    "Line1RunState",
    "build_line1_run_state",
    "max_compliant_buy_volume",
]
