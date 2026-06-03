"""Production Line-2 context providers (Phase U-D1).

The Line-2 daily + 30s-intraday runners (``backend.orchestration.line2_*``) are
import-isolated: they orchestrate the *flow* but never import
``backend.{risk,broker,data}``. The heavy per-code risk/broker objects each
``MonitoringAssemblyContext`` needs are supplied by a caller-built
``…ContextProvider`` — *"built by the U-D1 scheduler / main.py"*. This module
holds those production providers (this package legitimately imports
risk/broker/data; the redline only forbids that inside
``backend/orchestration``).

Design — pre-assembled bundle:
    The provider protocols expose **sync** ``build_sell_context`` /
    ``build_add_context`` but the inputs they need (prev_close, daily_state
    counters, per-code data quality) come from **async** services. So the
    async cron callback first assembles a :class:`Line2RunState` (run/tick-wide
    risk inputs) + a per-code :class:`Line2CodeContext` map, then constructs the
    provider; the sync ``build_*`` methods are pure look-ups that feed
    ``make_sell_context`` / ``make_intraday_sell_context`` / ``make_add_context``
    (the deterministic, zero-LLM Line-2 context builders).

LLM red line: this is a deterministic wiring module — no LLM imports; SELL/ADD
direction is already derived by the pure monitoring detectors upstream.

Real-data seams validated in U-D3 (1 real trading day) / U-D4 (real smoke),
recorded so they are not silently shipped half-wired:

* ``today_portfolio_pnl_pct`` / ``recent_trade_pnls`` default to ``0.0`` / ``()``
  here (the daily-loss + consecutive-loss circuit-breaker inputs). A pre-open
  daily SELL scan is unaffected (SELL never trips the breaker — CLAUDE.md §2.4);
  the intraday ADD path needs the real day-open NAV + ledger PnLs wired in U-D3.
* ``watchlist_signal`` defaults permissive when the frame row is absent. SELL
  skips the watchlist early-return (exits are not gated by the entry universe —
  monitoring CLAUDE.md §7), so this only binds for ADD (entry) — which only
  fires on a real intraday quote, i.e. U-D3.
* ``data_quality`` falls back to a clean state when no per-code
  :class:`DataQualityProvider` is injected.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import structlog

from backend.broker.models import AccountInfo, Position
from backend.data.data_quality import DataQualityState
from backend.data.stock_metadata import (
    ForbiddenCodeError,
    UnknownCodeError,
    classify_board,
    is_st_name,
)
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.monitoring.add_position import (
    AddIntent,
    make_add_context,
    parse_held_series,
)
from backend.monitoring.intraday_triggers import (
    IntradaySellIntent,
    make_intraday_sell_context,
)
from backend.monitoring.sell_signal import SellIntent, make_sell_context
from backend.risk.circuit_breaker import CircuitBreaker
from backend.risk.daily_state import DailyTradingState
from backend.risk.engine import RiskEngine
from backend.risk.stock_meta import Board as RiskBoard
from backend.risk.stock_meta import StockMetadata as RiskStockMetadata
from backend.services.instruction_plan_builder import (
    MonitoringAssemblyContext,
    WatchlistMarketSignal,
)
from backend.services.universe_policy import UniversePolicy

log = structlog.get_logger(component="services.line2_context_providers")

# A permissive watchlist signal: SELL exits skip the watchlist early-return, so
# this value is only consulted for an ADD that lacks a parsed frame row (an edge
# case recorded for U-D3 — real ADD entries must carry a real signal).
_PERMISSIVE_WATCHLIST_SIGNAL = WatchlistMarketSignal(
    listed_at_trading_days=720,
    avg_amount_20d_yuan=1_000_000_000.0,
    last_price_yuan=None,
)


def _bare_code(code: str) -> str:
    """Strip an exchange suffix (``600000.SH`` → ``600000``)."""
    return code.split(".")[0].strip()


def clean_data_quality() -> DataQualityState:
    """A clean (BUY/SELL-acceptable) DataQualityState fallback.

    Used when no per-code :class:`DataQualityProvider` is injected; the real
    per-code probe wiring is a U-D3 item.
    """
    return DataQualityState(
        quote_unavailable=False,
        quote_staleness_breach=False,
        quote_divergence_breach=False,
        minimum_freshness_breach=False,
        news_outage_breach=False,
        mirofish_unavailable=False,
        watchlist_snapshot_outage=False,
        primary_quote_age_seconds=2,
        backup_quote_age_seconds=2,
        news_sources_alive_count=5,
    )


def blocking_data_quality() -> DataQualityState:
    """A fail-closed (NOT BUY/SELL-acceptable) DataQualityState.

    Used when an injected :class:`DataQualityProvider` raises — a provider
    outage / miswire must NOT be treated as a clean quote (that would route
    Line-2 BUY/SELL exactly when the data-quality freeze source is unavailable).
    ``quote_unavailable`` is a blocking breach, so the builder's data-quality
    early return fires and the code does not route (Codex U-D1 P2).
    """
    return DataQualityState(
        quote_unavailable=True,
        quote_staleness_breach=False,
        quote_divergence_breach=False,
        minimum_freshness_breach=False,
        news_outage_breach=False,
        mirofish_unavailable=False,
        watchlist_snapshot_outage=False,
        primary_quote_age_seconds=0,
        backup_quote_age_seconds=0,
        news_sources_alive_count=0,
    )


def risk_meta_for(code: str, name: str) -> RiskStockMetadata | None:
    """Build a :class:`RiskStockMetadata` from the code's board classification.

    Returns ``None`` for a forbidden / unknown code so the RiskEngine universe
    check (14-check #11) fail-closes rather than the provider guessing a board.
    The two ``Board`` enums share string values, so the data-layer board maps to
    the risk-layer board by value.
    """
    bare = _bare_code(code)
    try:
        data_board = classify_board(bare)
    except (ForbiddenCodeError, UnknownCodeError):
        log.info("line2_stock_meta_unknown_board", code=code)
        return None
    risk_board = RiskBoard(data_board.value)
    return RiskStockMetadata(
        code=bare,
        name=name,
        board=risk_board,
        is_st=is_st_name(name),
        instrument_type="etf" if risk_board is RiskBoard.ETF else "stock",
    )


def derive_halt_until(breaker: CircuitBreaker, *, halted: bool) -> datetime | None:
    """Materialise the halt-expiry wall-clock from the breaker's cooldown.

    Mirrors ``daily_state_assembler._derive_halt_until`` (kept local so this
    module does not import a private helper). ``None`` when not halted or the
    breaker exposes no cooldown.
    """
    if not halted:
        return None
    started = getattr(breaker, "_halted_at", None)
    cooldown_min = getattr(getattr(breaker, "_config", None), "cooldown_minutes", 0)
    if started is None or cooldown_min <= 0:
        return None
    return started + timedelta(minutes=int(cooldown_min))


@dataclass(frozen=True)
class Line2RunState:
    """Run/tick-wide risk inputs shared across every held code in one run."""

    account: AccountInfo
    positions: tuple[Position, ...]
    risk_engine: RiskEngine
    circuit_breaker: CircuitBreaker
    watchlist_policy: UniversePolicy
    open_tickets: tuple[Any, ...] = ()
    today_instruction_count: int = 0
    today_portfolio_pnl_pct: float = 0.0
    recent_trade_pnls: tuple[float, ...] = ()
    halted: bool = False
    halt_until: datetime | None = None

    def daily_state(self, *, current_price: float | None) -> DailyTradingState:
        """Build the per-order :class:`DailyTradingState` for the 14-check.

        ``current_price`` is the order-specific price the trigger derived
        (``intent.limit_price``) — the deterministic Line-2 path prices off the
        consumed quote / frame bar, not a re-queried live tick, so the plan ties
        back to the same PIT value the manifest records.
        """
        return DailyTradingState(
            today_new_instruction_count=self.today_instruction_count,
            today_portfolio_pnl_pct=self.today_portfolio_pnl_pct,
            last_3_trade_pnls=self.recent_trade_pnls[-3:],
            current_price=current_price,
            is_in_halt_cooldown=self.halted,
            halt_until=self.halt_until,
        )


@dataclass(frozen=True)
class Line2CodeContext:
    """Pre-assembled per-held-code risk inputs (everything a SELL/ADD context
    needs beyond the order-specific intent + the run-wide state)."""

    prev_close: float | None
    stock_meta: RiskStockMetadata | None
    data_quality: DataQualityState
    watchlist_signal: WatchlistMarketSignal


class _Line2ProviderBase:
    """Shared per-code lookup for the daily + intraday providers."""

    def __init__(
        self,
        *,
        run_state: Line2RunState,
        code_contexts: Mapping[str, Line2CodeContext],
        name_by_code: Mapping[str, str],
    ) -> None:
        self._run = run_state
        self._ctx = dict(code_contexts)
        self._names = dict(name_by_code)

    @property
    def held_positions(self) -> Sequence[Position]:
        return self._run.positions

    @property
    def name_by_code(self) -> Mapping[str, str]:
        return self._names

    def _ctx_for(self, code: str) -> Line2CodeContext:
        bare = _bare_code(code)
        ctx = self._ctx.get(bare)
        if ctx is not None:
            return ctx
        # Every held code is assembled up-front, so a miss means a code that is
        # not held (should not happen). Degrade to a fail-closed context (no
        # stock_meta → RiskEngine universe check rejects) rather than crashing
        # the whole run on one stray code.
        log.warning("line2_code_context_missing", code=code)
        return Line2CodeContext(
            prev_close=None,
            stock_meta=None,
            data_quality=clean_data_quality(),
            watchlist_signal=_PERMISSIVE_WATCHLIST_SIGNAL,
        )


class Line2DailyProvider(_Line2ProviderBase):
    """Production :class:`Line2DailyContextProvider` (T-1 EOD frame SELL scan)."""

    def __init__(
        self,
        *,
        run_state: Line2RunState,
        code_contexts: Mapping[str, Line2CodeContext],
        name_by_code: Mapping[str, str],
        snapshot_at: datetime,
        spot_by_code: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            run_state=run_state, code_contexts=code_contexts, name_by_code=name_by_code
        )
        # snapshot_at = the T-1 EOD frame fetch time (strictly before ``now``,
        # so the InstructionPlan strictly-before invariant holds).
        self._snapshot_at = snapshot_at
        self._spots = dict(spot_by_code or {})

    @property
    def spot_by_code(self) -> Mapping[str, Any]:
        return self._spots

    def build_sell_context(
        self, intent: SellIntent, *, signal_id: str, seq: int, now: datetime
    ) -> MonitoringAssemblyContext:
        cc = self._ctx_for(intent.code)
        return make_sell_context(
            intent,
            now=now,
            signal_id=signal_id,
            seq=seq,
            snapshot_at=self._snapshot_at,
            account=self._run.account,
            positions=self._run.positions,
            prev_close=cc.prev_close,
            daily_state=self._run.daily_state(current_price=intent.limit_price),
            stock_meta=cc.stock_meta,
            risk_engine=self._run.risk_engine,
            open_tickets=self._run.open_tickets,
            circuit_breaker=self._run.circuit_breaker,
            data_quality=cc.data_quality,
            watchlist_policy=self._run.watchlist_policy,
            watchlist_signal=cc.watchlist_signal,
        )


class Line2IntradayProvider(_Line2ProviderBase):
    """Production :class:`Line2IntradayContextProvider` (30s deterministic tick)."""

    def __init__(
        self,
        *,
        run_state: Line2RunState,
        code_contexts: Mapping[str, Line2CodeContext],
        name_by_code: Mapping[str, str],
        daily_frame: MarketDataSnapshot,
        index_closes: tuple[float, ...],
        fetch_spots_fn: Callable[[Sequence[str]], Awaitable[Mapping[str, Any]]],
        theses_by_code: Mapping[str, Any] | None = None,
        holding_trade_days_by_code: Mapping[str, int] | None = None,
        exempt_theses_by_code: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            run_state=run_state, code_contexts=code_contexts, name_by_code=name_by_code
        )
        self._daily_frame = daily_frame
        self._index_closes = index_closes
        self._fetch = fetch_spots_fn
        self._spots: dict[str, Any] = {}
        # W-004 — held PositionThesis + holding trading-days for the deterministic
        # THESIS_QUANT_BREAK trigger. Empty unless the caller wires them (gated):
        # an empty map makes the trigger inert, so it is strictly additive.
        self._theses = dict(theses_by_code or {})
        self._holding_days = dict(holding_trade_days_by_code or {})
        # P0-10-amendment-line2-2026-06-03 — held PositionThesis for the
        # long-term-hold take-profit EXEMPTION. A SEPARATE field (own env gate)
        # so the THESIS_QUANT_BREAK wiring above is untouched: this is empty
        # unless the exemption is enabled, keeping the two features decoupled.
        self._exempt_theses = dict(exempt_theses_by_code or {})

    def theses_by_code(self) -> Mapping[str, Any]:
        """Held code → open PositionThesis (W-004; empty when not wired)."""
        return self._theses

    def holding_trade_days_by_code(self) -> Mapping[str, int]:
        """Held code → holding trading-days since buy (W-004 TIME_STOP input)."""
        return self._holding_days

    def exempt_theses_by_code(self) -> Mapping[str, Any]:
        """Held code → open PositionThesis for the take-profit exemption.

        P0-10-amendment-line2-2026-06-03; empty unless the exemption is enabled.
        """
        return self._exempt_theses

    @property
    def account(self) -> AccountInfo:
        return self._run.account

    @property
    def daily_frame(self) -> MarketDataSnapshot:
        return self._daily_frame

    @property
    def index_closes(self) -> tuple[float, ...]:
        return self._index_closes

    async def fetch_spots(self, codes: Sequence[str]) -> Mapping[str, Any]:
        """Fetch the held codes' live spots (delegates to the injected fetcher).

        The fetcher must tag each spot's ``snapshot_at`` strictly before the
        tick ``now`` (runner contract); a quote at/after ``now`` fails closed in
        the runner's ``filter_fresh_quotes``.
        """
        spots = dict(await self._fetch(codes))
        self._spots = spots
        return spots

    def build_sell_context(
        self,
        intent: IntradaySellIntent,
        *,
        signal_id: str,
        seq: int,
        now: datetime,
        snapshot_at: datetime,
    ) -> MonitoringAssemblyContext:
        cc = self._ctx_for(intent.code)
        return make_intraday_sell_context(
            intent,
            now=now,
            signal_id=signal_id,
            seq=seq,
            snapshot_at=snapshot_at,
            account=self._run.account,
            positions=self._run.positions,
            prev_close=cc.prev_close,
            daily_state=self._run.daily_state(current_price=intent.limit_price),
            stock_meta=cc.stock_meta,
            risk_engine=self._run.risk_engine,
            open_tickets=self._run.open_tickets,
            circuit_breaker=self._run.circuit_breaker,
            data_quality=cc.data_quality,
            watchlist_policy=self._run.watchlist_policy,
            watchlist_signal=cc.watchlist_signal,
        )

    def build_add_context(
        self,
        intent: AddIntent,
        *,
        signal_id: str,
        seq: int,
        now: datetime,
        snapshot_at: datetime,
    ) -> MonitoringAssemblyContext:
        cc = self._ctx_for(intent.code)
        return make_add_context(
            intent,
            now=now,
            signal_id=signal_id,
            seq=seq,
            snapshot_at=snapshot_at,
            account=self._run.account,
            positions=self._run.positions,
            prev_close=cc.prev_close,
            daily_state=self._run.daily_state(current_price=intent.limit_price),
            stock_meta=cc.stock_meta,
            risk_engine=self._run.risk_engine,
            open_tickets=self._run.open_tickets,
            circuit_breaker=self._run.circuit_breaker,
            data_quality=cc.data_quality,
            watchlist_policy=self._run.watchlist_policy,
            watchlist_signal=cc.watchlist_signal,
        )


# ---------------------------------------------------------------------------
# Async assembly — composes the real services into the run-state + per-code map.
# main.py's cron callbacks call these; tests exercise them with fakes/fixtures.
# ---------------------------------------------------------------------------


async def build_line2_run_state(
    *,
    broker: Any,
    risk_engine: RiskEngine,
    circuit_breaker: CircuitBreaker,
    watchlist_policy: UniversePolicy,
    now: datetime,
    open_tickets: Sequence[Any] = (),
    today_instruction_count: int = 0,
    today_portfolio_pnl_pct: float = 0.0,
    recent_trade_pnls: Sequence[float] = (),
) -> Line2RunState:
    """Assemble the run/tick-wide :class:`Line2RunState` from live broker state.

    ``today_*`` / ``recent_trade_pnls`` are inputs (defaults 0/0/()) so the
    caller can wire the real broker_events / ledger sources in U-D3 without this
    function reaching into private assembler helpers.
    """
    account = await broker.get_account()
    positions = tuple(await broker.get_positions())
    halted = circuit_breaker.is_halted(now)
    return Line2RunState(
        account=account,
        positions=positions,
        risk_engine=risk_engine,
        circuit_breaker=circuit_breaker,
        watchlist_policy=watchlist_policy,
        open_tickets=tuple(open_tickets),
        today_instruction_count=today_instruction_count,
        today_portfolio_pnl_pct=today_portfolio_pnl_pct,
        recent_trade_pnls=tuple(recent_trade_pnls),
        halted=halted,
        halt_until=derive_halt_until(circuit_breaker, halted=halted),
    )


def _watchlist_signal_from_frame(
    frame: MarketDataSnapshot | None, code: str
) -> WatchlistMarketSignal:
    """Derive a per-code :class:`WatchlistMarketSignal` from the daily frame.

    Uses the frame's closes (→ last price) + amounts (→ 20-day average CNY
    turnover). The IPO-age (``listed_at_trading_days``) column is not parsed by
    ``parse_held_series``; it defaults permissive here and is wired from the
    frame's ``listed_trading_days`` column in U-D3 (real ADD entries).
    """
    if frame is None:
        return _PERMISSIVE_WATCHLIST_SIGNAL
    bare = _bare_code(code)
    series = parse_held_series(frame, [bare])
    parsed = series.get(bare)
    if not parsed:
        return _PERMISSIVE_WATCHLIST_SIGNAL
    closes, amounts = parsed
    last_price = closes[-1] if closes else None
    recent = amounts[-20:] if amounts else ()
    avg_amount = sum(recent) / len(recent) if recent else None
    return WatchlistMarketSignal(
        listed_at_trading_days=720,
        avg_amount_20d_yuan=avg_amount,
        last_price_yuan=last_price,
    )


async def build_line2_code_contexts(
    *,
    codes: Sequence[str],
    name_by_code: Mapping[str, str],
    market_meta: Any,
    frame: MarketDataSnapshot | None = None,
    data_quality_provider: Any | None = None,
    now: datetime,
) -> dict[str, Line2CodeContext]:
    """Assemble the per-held-code :class:`Line2CodeContext` map.

    For each (bare) code: prev_close via the MarketMetaProvider, board-derived
    stock_meta, per-code data quality (clean fallback when no provider), and a
    frame-derived watchlist signal. A prev_close lookup failure degrades to
    ``None`` (RiskEngine handles a missing prev_close conservatively) rather than
    aborting the whole run.
    """
    out: dict[str, Line2CodeContext] = {}
    for raw_code in codes:
        bare = _bare_code(raw_code)
        if bare in out:
            continue
        name = name_by_code.get(raw_code) or name_by_code.get(bare) or bare
        try:
            prev_close = await market_meta.get_prev_close(bare)
        except Exception as exc:  # noqa: BLE001 — degrade per-code, never abort
            log.warning("line2_prev_close_failed", code=bare, error=str(exc))
            prev_close = None
        if data_quality_provider is not None:
            try:
                dq = await data_quality_provider.evaluate(bare, now)
            except Exception as exc:  # noqa: BLE001 — fail-closed, not clean
                log.warning("line2_data_quality_failed", code=bare, error=str(exc))
                dq = blocking_data_quality()
        else:
            # No provider injected (U-D1 baseline): clean state is the
            # documented fallback (real per-code probe wiring = U-D3).
            dq = clean_data_quality()
        out[bare] = Line2CodeContext(
            prev_close=prev_close,
            stock_meta=risk_meta_for(bare, name),
            data_quality=dq,
            watchlist_signal=_watchlist_signal_from_frame(frame, bare),
        )
    return out


__all__ = [
    "Line2CodeContext",
    "Line2DailyProvider",
    "Line2IntradayProvider",
    "Line2RunState",
    "blocking_data_quality",
    "build_line2_code_contexts",
    "build_line2_run_state",
    "clean_data_quality",
    "derive_halt_until",
    "risk_meta_for",
]
