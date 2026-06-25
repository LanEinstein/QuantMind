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
* :class:`Line1ContextProvider.build_lead_context` (async, U-E2) fetches the
  lead's live quote (dual-source spot last + 卖一 orderbook), derives the
  price-cage-bounded BUY limit + a deterministic volume off it, derives the
  lead's PIT prev_close from the T-1 EOD frame, and hands the runner a
  TeamContext (debate) + an AssemblyContext factory (the 14-check single
  construction point). When the live quote is unusable it returns a
  :class:`Line1QuoteDegrade` instead — the runner never prices on the T-1 close.

LLM red line: the provider only *passes through* the injected LLM router into
the debate's :class:`TeamContext`. It derives no decision field from an LLM —
``side`` is the fund_manager's downstream proposal; ``volume`` / ``limit_price``
are deterministic (R0 §4 InstructionPlan single construction point).

Real-data seams (U-E2 wires the live cage quote; the rest as recorded):

* ``prev_close`` is parsed from the same T-1 EOD frame (PIT, replay-stable); the
  ``limit_price`` / ``current_price`` are now driven by the live spot (U-E2).
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

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import floor, isfinite
from typing import Any

import structlog

from backend.agents_team.state import CandidateBrief, TeamContext
from backend.broker.models import AccountInfo, Position, RiskConfig
from backend.data.data_quality import DataQualityState
from backend.data.divergence import evaluate_divergence
from backend.data.market_data import MarketDataService
from backend.data.stock_metadata import (
    ForbiddenCodeError,
    UnknownCodeError,
    classify_board,
    get_lot_size,
)
from backend.marketdata_snapshot import MarketDataSnapshot, SnapshotStore
from backend.models.instruction import DataSnapshot
from backend.models.market import StockQuote
from backend.monitoring.add_position import parse_held_series
from backend.orchestration.line1_runner import (
    CommittedBuy,
    Line1AllocationSkip,
    Line1LeadContext,
    Line1QuoteDegrade,
)
from backend.portfolio_allocation import AllocationPolicy, cash_to_lots
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.price_cage import CageQuote, cage_bounded_buy_limit
from backend.risk.stock_meta import Board
from backend.screening.screener import CandidateRow
from backend.services.instruction_plan_builder import (
    AssemblyContext,
    WatchlistMarketSignal,
)
from backend.services.line2_context_providers import (
    blocking_data_quality,
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

# P0-8 §1.1 dual-source spot thresholds for the U-E2 live cage-quote gate.
# divergence: |adata - fallback| / adata > 0.3% → untrusted; staleness: the spot
# fetch must be ≤5s old. A breach (or a single-source / missing 卖一) degrades
# the lead to a non-actionable notice — never priced on the last / T-1 close.
# (P0-8-amendment-2026-05-28: the fallback leg switched from akshare to
# Tushare ``realtime_quote(src='sina')``; threshold and degrade semantics
# unchanged.)
_DIVERGENCE_THRESHOLD_PCT = 0.003
_STALENESS_THRESHOLD_SECONDS = 5.0


@dataclass(frozen=True)
class _CageDerivation:
    """Successful live-quote derivation for a lead (U-E2 / 缺口4).

    Either ``build_lead_context`` gets one of these (and prices the BUY off
    ``limit_price`` with ``cage_quote`` threaded into the 14-check) or it gets a
    :class:`Line1QuoteDegrade` reason string — never a guessed price.
    """

    last_price: float
    limit_price: float
    cage_quote: CageQuote


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
    today_portfolio_pnl_pct: float = 0.0
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
        data_quality_provider: Any | None = None,
        market_data: MarketDataService | None = None,
        snapshot_store: SnapshotStore | None = None,
        allocation_policy: AllocationPolicy | None = None,
    ) -> None:
        self._run = run_state
        self._frame = frame
        self._llm_router = llm_router
        self._now = now
        # P-003 portfolio allocation (P0-7-amendment-2026-05-30). When a policy
        # is injected, ``prime_allocation`` (called once by the runner before the
        # shortlist walk) fills ``_target_cash_by_code`` with each name's
        # inverse-volatility incremental cash target; ``build_lead_context`` then
        # clamps ``volume = min(max_compliant, cash_to_lots(target, limit))`` —
        # allocation only tightens, never relaxes the 14-check caps. ``None`` (the
        # offline / test default) leaves sizing at the existing max_compliant.
        self._allocation_policy = allocation_policy
        self._target_cash_by_code: dict[str, float] | None = None
        # Per-code DataQualityProvider (C3 fix A): when injected, evaluate()
        # is called with the lead's bare code inside build_lead_context so the
        # DQ gate is real (not a permanent clean-state no-op).  Falls back to
        # ``data_quality`` (which defaults to clean_data_quality() for
        # back-compat / offline tests) when no provider is supplied.
        self._data_quality_provider = data_quality_provider
        self._data_quality = data_quality or clean_data_quality()
        # U-E2 / 缺口4: the live quote layer (dual-source spot + 卖一 orderbook).
        # When ``None`` the provider cannot price a BUY safely → every lead
        # degrades to a non-actionable notice (never the last / T-1 close).
        self._market_data = market_data
        # Optional PIT sink for the live spot bytes (R0 §3 redline A). When
        # present the raw dual-source quote + orderbook is content-addressed +
        # checksummed so ``replay <signal_id>`` can reconstruct the cage inputs.
        self._snapshot_store = snapshot_store
        self._cage_tolerance_pct = run_state.risk_config.universe.cage_tolerance_pct

    @property
    def available_cash(self) -> float:
        """Investable cash for the budget tier (``account.available_cash``)."""
        return self._run.account.available_cash

    @property
    def held_codes(self) -> frozenset[str]:
        """Bare 6-digit codes currently held (volume > 0) — holdings-aware Line-1.

        Line-1 excludes these from the BUY candidate set so it only fills genuine
        empty slots with NEW names (P0-7-amendment-2026-06-01 §1.4). Codes are
        normalised to bare form so a ``.SH`` / ``.SZ``-suffixed holding still
        matches the screener's bare candidate codes.
        """
        return frozenset(
            _bare_code(p.code) for p in self._run.positions if p.volume > 0
        )

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

    def prime_allocation(self, shortlist_rows: Sequence[CandidateRow]) -> None:
        """Compute each shortlist name's inverse-volatility cash target (P-003).

        Called once by the runner AFTER selection and BEFORE the shortlist walk
        (P0-7-amendment-2026-05-30 §2.3): σ is read off each row's PIT factor
        (``volatility_20d``), the conservative deploy envelope off the run-state
        account, and existing holdings off the run-state positions — all at
        walk-start, so the targets are deterministic and do NOT re-allocate
        mid-walk (redline 6). No-op when no policy was injected. ``build_lead_context``
        then clamps each order to its target.
        """
        if self._allocation_policy is None:
            return
        policy = self._allocation_policy
        account = self._run.account
        # σ is the screener's PIT volatility_20d (float | None). A None (history
        # too short) is intended to fall back to equal weight for that name
        # inside inverse_vol_weights (amendment §2.1) — never fabricated.
        sigma_by_code = {
            row.code: row.factors.volatility_20d for row in shortlist_rows
        }
        weights = policy.inverse_vol_weights(sigma_by_code)
        deployable = policy.deployable_cash(
            account.available_cash, account.total_assets
        )
        existing_value_by_code: dict[str, float] = {}
        for pos in self._run.positions:
            existing_value_by_code[pos.code] = (
                existing_value_by_code.get(pos.code, 0.0) + pos.market_value
            )
        targets = policy.target_cash(
            weights, deployable, account.total_assets, existing_value_by_code
        )
        self._target_cash_by_code = targets
        log.info(
            "line1_allocation_primed",
            shortlist_size=len(sigma_by_code),
            deployable=deployable,
            targets={c: round(v, 2) for c, v in targets.items()},
        )

    async def build_lead_context(
        self,
        lead: CandidateRow,
        *,
        concentration_exception: bool = False,
        committed: tuple[CommittedBuy, ...] = (),
        signal_id: str = "",
        seq: int = 0,
    ) -> Line1LeadContext | Line1QuoteDegrade | Line1AllocationSkip:
        """Build the TeamContext + AssemblyContext factory for the lead.

        U-E2 / 缺口4: fetches the lead's live quote (dual-source spot last + 卖一
        orderbook) FIRST and derives the price-cage BUY 限价上限. When the quote
        is unusable (no live layer, single-source / divergent / stale spot, or a
        missing 卖一) it returns a :class:`Line1QuoteDegrade` — the runner then
        emits a non-actionable notice and NEVER prices a BUY on the last / T-1
        close. ``volume`` is re-sized off the cage limit so the single
        construction point still owns the number.

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
        bare = _bare_code(lead.code)
        # The board drives the cage tick + the 14-check universe rule. A
        # forbidden / unknown board (the screener should have excluded it) yields
        # None → degrade rather than guess (fail-closed).
        stock_meta = risk_meta_for(bare, lead.name)
        if stock_meta is None:
            return Line1QuoteDegrade(
                code=lead.code,
                name=lead.name,
                reason="unclassifiable board (forbidden / unknown code)",
            )
        # Derive the cage-bounded limit off a dual-source-validated live quote.
        cage = await self._derive_cage_quote(
            bare=bare, board=stock_meta.board, signal_id=signal_id, seq=seq
        )
        if isinstance(cage, str):
            return Line1QuoteDegrade(code=lead.code, name=lead.name, reason=cage)
        limit_price = cage.limit_price
        live_last = cage.last_price

        account, positions = _apply_committed(rs.account, rs.positions, committed)
        # Fall back to the live last (not the inflated cage ceiling) when the
        # frame lacks a prior bar — a conservative ~0% move that never trips the
        # band, and a better prev_close proxy than the limit (U-E2).
        prev_close = self._prev_close_from_frame(bare, fallback=live_last)
        # Match the RiskEngine's own exact-code comparison (checks 5 + 8 net
        # the held same-code position by ``p.code == order.code`` and value the
        # OTHER positions at their snapshot market_value) so the sizing math
        # mirrors the gate it must pass.
        existing_shares = sum(p.volume for p in positions if p.code == lead.code)
        other_positions_value = sum(
            p.market_value for p in positions if p.code != lead.code
        )
        limits = rs.risk_config.position_limits
        exception = rs.risk_config.concentration_exception
        # Size off the CAGE limit (the price the order will carry) so the
        # cash / 15% / 70% / ¥50k caps bind against the actual notional — the
        # single construction point still owns ``volume`` deterministically.
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
        # P-003 portfolio-allocation clamp (P0-7-amendment-2026-05-30 §2.2):
        # tighten the order to the inverse-volatility cash target primed for
        # this run. The target lots are floored off the SAME cage limit the
        # notional caps bind against (single-source lot size). A 0-lot target
        # means allocation says do not buy this name today (conservative
        # under-deployment) → degrade, never coerce to 1 lot (would violate the
        # tranche envelope + Pydantic ``volume > 0``). The clamp only tightens;
        # the RiskEngine 14-check stays independently authoritative.
        if self._target_cash_by_code is not None:
            target_lots = cash_to_lots(
                self._target_cash_by_code.get(lead.code, 0.0),
                limit_price,
                lot=limits.volume_lot_size,
            )
            if target_lots <= 0:
                # The quote was usable — allocation simply did not fund this name
                # today. A distinct skip (not a quote degrade) so the runner does
                # not mislabel it QUOTE_DEGRADED / emit a non-actionable-quote
                # notice (codex/code-review P-003 finding).
                return Line1AllocationSkip(
                    code=lead.code,
                    name=lead.name,
                    reason="allocation target 0 lots today (inverse-vol under-deploy)",
                )
            volume = min(volume, target_lots)
        # Per-code DQ gate (C3 fix A): evaluate the real DataQualityProvider for
        # this lead's bare code.  Fail-closed: any probe exception → blocking
        # DataQualityState so the builder's check_data_quality early-return fires
        # (a probe outage must NOT let a BUY through).  Back-compat baseline: when
        # no provider is injected fall through to the existing self._data_quality
        # (defaults to clean_data_quality() for offline / test callers).
        if self._data_quality_provider is not None:
            try:
                dq = await self._data_quality_provider.evaluate(bare, self._now)
            except Exception as exc:  # noqa: BLE001 — any probe fault → fail-closed
                log.warning(
                    "line1_data_quality_failed", code=bare, error=str(exc)
                )
                dq = blocking_data_quality()
        else:
            dq = self._data_quality

        daily_state = DailyTradingState(
            # Count the BUYs already routed earlier in this BASKET run so the
            # ≤5-orders/day cap (check 10) binds ACROSS the basket, not just the
            # day's pre-run count (codex P1) — otherwise a partially-used day
            # could route more than its remaining order slots.
            today_new_instruction_count=rs.today_instruction_count + len(committed),
            # Daily-loss brake bound to the live MTM NAV drawdown
            # (P0-7-amendment-2026-06-23): rs carries the equity-point-derived
            # today_portfolio_pnl_pct so check 13 halts a BUY on a -5% day. The
            # consecutive-loss streak (check 14 / last_3_trade_pnls) needs
            # realized per-trade PnL → deferred (stays () = check 14 PASSes).
            today_portfolio_pnl_pct=rs.today_portfolio_pnl_pct,
            last_3_trade_pnls=(),
            # Live last drives check #12 (limit-up block) so the BUY is gated
            # against the real intraday price, not the T-1 close.
            current_price=live_last,
            is_in_halt_cooldown=rs.halted,
            halt_until=rs.halt_until,
        )
        watchlist_signal = WatchlistMarketSignal(
            listed_at_trading_days=_LISTED_PERMISSIVE_DAYS,
            avg_amount_20d_yuan=lead.factors.avg_amount_20d,
            last_price_yuan=live_last,
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
                data_quality=dq,
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
                # The dual-source-validated 卖一 the cage limit was derived from
                # → RiskEngine check #02 independently re-verifies the limit is
                # within the legal cage (a 废单 guard, U-E2 / 缺口4).
                live_quote=cage.cage_quote,
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

    async def _derive_cage_quote(
        self, *, bare: str, board: Board, signal_id: str, seq: int
    ) -> _CageDerivation | str:
        """Fetch + validate the live quote and derive the cage limit, or degrade.

        Returns a :class:`_CageDerivation` (last + cage limit + 卖一 CageQuote) on
        success, or a degrade-reason ``str`` for the runner's non-actionable
        notice. Gates, in order (P0-8 dual-source + U-E2): a live layer must
        exist; BOTH spot legs must return; their last prints must agree within
        0.3%; the spot must be ≤5s old; the 卖一 orderbook must carry a positive
        best_ask. Only then is the cage limit deterministic. ``board`` is the
        lead's :class:`RiskStockMetadata` ``.board`` (passed in to avoid a second
        classification).
        """
        if self._market_data is None:
            return "no live market-data layer (offline run cannot price a BUY)"
        try:
            primary, fallback = await self._market_data.get_stock_realtime_dual(bare)
        except Exception as exc:  # noqa: BLE001 — any vendor fault → degrade
            log.warning("line1_dual_spot_failed", code=bare, error=str(exc))
            return f"dual-source spot fetch failed: {exc}"
        if primary is None:
            return "primary spot leg (adata) unavailable"
        if fallback is None:
            return (
                "single-source spot — backup leg (tushare-sina) unavailable "
                "(P0-8 dual-source required)"
            )
        div = evaluate_divergence(
            code=bare,
            primary_price=primary.price,
            fallback_price=fallback.price,
            threshold_pct=_DIVERGENCE_THRESHOLD_PCT,
        )
        if div.relative_diff is None:
            # evaluate_divergence folds a non-finite (NaN/inf) backup price or a
            # non-positive primary into ``relative_diff=None, is_divergent=False``
            # — so a malformed fallback cell would otherwise pass as
            # "dual-source-confirmed" and route a BUY priced off adata ALONE.
            # That is effectively single-source → fail closed (codex U-E2 P1).
            return (
                "untrusted spot — non-finite/non-positive price, cannot confirm "
                f"dual-source (adata {primary.price} vs tushare-sina {fallback.price})"
            )
        if div.is_divergent:
            return (
                f"spot divergence {div.relative_diff:.4f} > "
                f"{_DIVERGENCE_THRESHOLD_PCT} "
                f"(adata {primary.price} vs tushare-sina {fallback.price})"
            )
        try:
            age = (self._now - primary.timestamp).total_seconds()
        except TypeError:
            # A tz-naive vs tz-aware mix (a misconfigured ``now``) would crash
            # the whole basket walk mid-run — fail closed for THIS lead instead
            # (degrade, never price on an uncomputable freshness; review U-E2).
            return "spot staleness uncomputable (tz mismatch) — fail closed"
        if age > _STALENESS_THRESHOLD_SECONDS:
            return f"spot stale: age {age:.1f}s > {_STALENESS_THRESHOLD_SECONDS}s"
        try:
            ob = await self._market_data.get_stock_orderbook(bare)
        except Exception as exc:  # noqa: BLE001 — any vendor fault → degrade
            log.warning("line1_orderbook_failed", code=bare, error=str(exc))
            return f"orderbook fetch failed: {exc}"
        if ob.best_ask is None:
            return "no 卖一 (best_ask) in orderbook"
        try:
            limit_price = cage_bounded_buy_limit(
                last_price=primary.price,
                best_ask=ob.best_ask,
                board=board,
                tolerance_pct=self._cage_tolerance_pct,
            )
        except ValueError as exc:
            return f"cage limit derivation failed: {exc}"
        self._persist_pit(
            bare=bare,
            signal_id=signal_id,
            seq=seq,
            primary=primary,
            fallback=fallback,
            ob=ob,
        )
        return _CageDerivation(
            last_price=primary.price,
            limit_price=limit_price,
            cage_quote=CageQuote(best_ask=ob.best_ask, source=ob.source),
        )

    def _persist_pit(
        self,
        *,
        bare: str,
        signal_id: str,
        seq: int,
        primary: StockQuote,
        fallback: StockQuote,
        ob: Any,
    ) -> None:
        """Persist the raw live cage inputs to the SnapshotStore (R0 §3 redline A).

        The PIT payload is the canonical-JSON serialisation of the dual-source
        spot + the 卖一 orderbook (the vendor responses are in-process DataFrames
        with no stable wire bytes, so the parsed-quote canonical JSON IS the
        replay-stable record). Content-addressed + checksummed by the store;
        ``params`` / ``metadata`` carry the signal_id lineage so
        ``replay <signal_id>`` can reconstruct the cage inputs. Best-effort: a
        store fault is logged, never fatal (PIT is an audit/replay concern, not a
        tradeability gate — the order already priced off the same in-memory
        quote). No store injected → skipped.
        """
        if self._snapshot_store is None:
            return
        payload = {
            "code": bare,
            "primary": primary.model_dump(mode="json"),
            "fallback": fallback.model_dump(mode="json"),
            "orderbook": ob.model_dump(mode="json"),
        }
        raw = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        try:
            snap = MarketDataSnapshot.create(
                vendor="line1_live_cage",
                endpoint=f"spot_orderbook:{bare}",
                params={"code": bare, "signal_id": signal_id, "seq": str(seq)},
                trade_date=self._now.astimezone(UTC).strftime("%Y%m%d"),
                raw_payload=raw,
                encoding="utf-8",
                compression="none",
                fetch_time_utc=self._now.astimezone(UTC),
                metadata={
                    "signal_id": signal_id,
                    "seq": seq,
                    "orderbook_source": ob.source,
                },
            )
            self._snapshot_store.put(snap)
        except Exception as exc:  # noqa: BLE001 — PIT is best-effort, never fatal
            log.warning("line1_pit_persist_failed", code=bare, error=str(exc))

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
    today_portfolio_pnl_pct: float = 0.0,
) -> Line1RunState:
    """Assemble the run-wide :class:`Line1RunState` from live broker state.

    Mirrors ``build_line2_run_state``: ``today_instruction_count`` is an input
    (default 0) so the caller can wire the real broker_events count in U-D3
    without this function reaching into private assembler helpers.
    """
    account = await broker.get_account()
    positions = tuple(await broker.get_positions())
    # Daily-loss brake: trip the 60-min cooldown latch off the live MTM NAV
    # drawdown before reading the halt state (P0-7-amendment-2026-06-23). With
    # the default 0.0 this is a no-op; the cron passes the equity-point pnl.
    circuit_breaker.observe_daily_drawdown(today_portfolio_pnl_pct, now)
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
        today_portfolio_pnl_pct=today_portfolio_pnl_pct,
        halted=halted,
        halt_until=derive_halt_until(circuit_breaker, halted=halted),
    )


__all__ = [
    "Line1ContextProvider",
    "Line1RunState",
    "build_line1_run_state",
    "max_compliant_buy_volume",
]
