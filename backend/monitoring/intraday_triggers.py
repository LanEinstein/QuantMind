"""Line-2 intraday deterministic triggers (Phase U-C3).

The 30s intraday sibling of the daily statistical :class:`AnomalyDetector`
(U-C2). Where the daily path flags **statistical** anomalies (z-score / EWMA
/ Bollinger over a daily-close baseline, PIT-replayable from the T-1 EOD
snapshot), this module fires **deterministic** intraday triggers against a
live spot quote (§设计4 — *确定性触发, 非统计异动*):

* **SELL** — a risk exit on either an intraday drawdown vs ``prev_close``
  beyond a threshold (:attr:`IntradayTriggerKind.DRAWDOWN_STOP`) **or** a
  break below ``recent_high − k·ATR`` where the recent high and the ATR are
  both taken from the **daily** close history (:attr:`ATR_TRAILING_STOP`).
  Using the daily history — never a cross-tick in-memory high-water mark —
  keeps the trigger a pure function of the persisted snapshots so an offline
  replay reproduces it bit-for-bit (R0 §3 PIT contract).
* **ADD** — a disciplined scale-in driven by the live price, reusing the
  ``add_position`` helpers + every hard ban (Van Tharp fixed fraction +
  close-based ATR stop, anti-martingale drawdown-vs-cost guard, bear-regime
  ban, single-stock headroom). It emits the **existing** :class:`AddIntent`
  so the unchanged ``make_add_context`` consumes it.

The new :class:`IntradayTriggerKind` / :class:`IntradaySellIntent` keep the
locked daily types (:class:`backend.monitoring.anomaly.AnomalyKind` /
:class:`backend.monitoring.sell_signal.SellIntent`) untouched, so audit /
replay tell the daily-statistical and intraday-deterministic paths apart by
type (owner decision B2, 2026-05-26).

Module red line (``backend/monitoring/CLAUDE.md`` import isolation, N-005):
pure quant — **no** ``backend.{llm,agents,agents_team,mirofish}`` import. A
trigger here is a deterministic observation; the SELL/ADD InstructionPlan it
drives is built downstream by ``instruction_plan_builder`` (single
construction point, R0 §4).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog

# backend.{broker,risk,data} are legitimate Line-2 dependencies (positions /
# RiskEngine / data-quality types). The per-line noqa keeps the global TID251
# ban ACTIVE for backend.{llm,agents,agents_team,mirofish} — this module's own
# red line (backend/monitoring/CLAUDE.md).
from backend.broker.models import AccountInfo, Position  # noqa: TID251
from backend.data.data_quality import DataQualityState  # noqa: TID251
from backend.models.instruction import DataSnapshot, InstructionSide
from backend.models.market import WatchlistMarketSnapshot
from backend.monitoring.add_position import (
    AddConfig,
    AddEvaluation,
    AddIntent,
    AddRejection,
    AddRejectReason,
    MarketRegime,
    classify_regime,
    close_atr,
    moving_average,
    vanthorp_size,
)
from backend.monitoring.sell_signal import normalize_position_codes
from backend.risk.circuit_breaker import CircuitBreaker  # noqa: TID251
from backend.risk.daily_state import DailyTradingState  # noqa: TID251
from backend.risk.engine import RiskEngine  # noqa: TID251
from backend.risk.stock_meta import StockMetadata  # noqa: TID251
from backend.services.instruction_plan_builder import (
    MONITORING_SIGNAL_PREFIX,
    MonitoringAssemblyContext,
    WatchlistMarketSignal,
)
from backend.services.universe_policy import UniversePolicy

log = structlog.get_logger(component="monitoring.intraday_triggers")

_LOT = 100

# Pinned feature-code version — bump when the trigger maths changes so a stale
# replay manifest fails closed instead of silently recomputing.
FEATURE_CODE_VERSION: str = "monitoring.intraday_triggers/v1"

# Canonical CSV header for a persisted intraday quote snapshot (one row per
# fired held code — the consumed-row lineage the IntradayTriggerManifest pins).
INTRADAY_QUOTE_HEADER: tuple[str, ...] = (
    "code",
    "name",
    "price",
    "prev_close",
    "volume",
    "amount",
    "change_pct",
    "snapshot_at",
)


class IntradayTriggerKind(StrEnum):
    """Which deterministic intraday trigger fired (distinct from AnomalyKind)."""

    DRAWDOWN_STOP = "drawdown_stop"
    """Intraday drawdown vs ``prev_close`` beyond ``drawdown_threshold``."""
    ATR_TRAILING_STOP = "atr_trailing_stop"
    """Live price below ``recent_high − k·ATR`` (daily-history ATR + high)."""


@dataclass(frozen=True)
class IntradayTriggerConfig:
    """Locked intraday trigger thresholds (runtime-immutable).

    Conservative defaults (precision over recall — alert-fatigue red line).
    ``recent_high`` and the ATR both come from the daily close history, so the
    stop level is a pure function of the persisted snapshots (no cross-tick
    state) and the trigger is offline-replayable.
    """

    drawdown_threshold: float = 0.05  # intraday return vs prev_close ≤ -5%
    atr_window: int = 14
    atr_stop_mult: float = 2.0  # k in recent_high − k·ATR
    recent_high_window: int = 20  # trailing daily closes for the recent high
    max_quote_staleness_seconds: float = 60.0  # older spot → fail-closed


@dataclass(frozen=True)
class IntradaySellIntent:
    """A deterministic intraday decision to exit a held position (pre-RiskEngine).

    The intraday twin of :class:`backend.monitoring.sell_signal.SellIntent`;
    ``trigger_kind`` is an :class:`IntradayTriggerKind` (not ``AnomalyKind``)
    so the audit/replay path distinguishes the deterministic intraday exit
    from the daily statistical one.
    """

    code: str
    name: str
    available_volume: int  # lot-aligned, T+1 settled
    limit_price: float  # live spot price
    trigger_kind: IntradayTriggerKind
    anomaly_reason: str  # human-readable strongest-trigger detail
    drawdown_pct: float
    atr: float
    recent_high: float
    stop_level: float


def _bare(code: str) -> str:
    """Strip a ``.SH`` / ``.SZ`` suffix → bare 6-digit code."""
    return code.split(".")[0].strip()


# ---------------------------------------------------------------------------
# Quote freshness + serialisation (pure over the spot rows)
# ---------------------------------------------------------------------------


def filter_fresh_quotes(
    spots: Mapping[str, WatchlistMarketSnapshot],
    codes: Sequence[str],
    *,
    now: datetime,
    max_staleness_seconds: float,
) -> tuple[frozenset[str], tuple[str, ...]]:
    """Split ``codes`` into fresh-quote vs stale (fail-closed) sets.

    A code is **stale** — and therefore must NOT trigger this tick — when its
    spot is missing, carries a non-finite / non-positive price or prev_close,
    has a tz-naive timestamp, is older than ``max_staleness_seconds``, or is
    **not strictly before** ``now`` (a quote tagged at/after the decision time:
    either a clock-skew red flag or a quote not yet observable when the
    decision is taken). The strictly-before gate mirrors the InstructionPlan
    invariant (``data_snapshot.snapshot_at`` must be strictly before
    ``created_at``) so a same-instant / future quote fails closed HERE rather
    than crashing the plan build after the tick already persisted (codex U-C3
    P1). A stale code never produces an order from a dirty price (§设计4
    invariant 3). Output is deterministic (sorted).
    """
    fresh: list[str] = []
    stale: list[str] = []
    for code in sorted(_bare(c) for c in codes):
        spot = spots.get(code)
        if spot is None:
            stale.append(code)
            continue
        if (
            not math.isfinite(spot.price)
            or not math.isfinite(spot.prev_close)
            or spot.price <= 0
            or spot.prev_close <= 0
        ):
            stale.append(code)
            continue
        if spot.snapshot_at.tzinfo is None:
            stale.append(code)
            continue
        age = (now - spot.snapshot_at).total_seconds()
        # age <= 0 ⇒ the quote is tagged at/after ``now`` — not strictly before
        # the decision, so it fails closed (matches the InstructionPlan
        # strictly-before invariant; avoids a post-persist plan-build crash).
        if age <= 0 or age > max_staleness_seconds:
            stale.append(code)
            continue
        fresh.append(code)
    return frozenset(fresh), tuple(stale)


def _sanitise_field(value: str) -> str:
    """Collapse commas / newlines so a name cannot break the canonical CSV."""
    return value.replace(",", " ").replace("\n", " ").replace("\r", " ").strip()


def serialize_intraday_quotes(
    spots: Mapping[str, WatchlistMarketSnapshot],
    codes: Sequence[str],
) -> tuple[bytes, dict[str, bytes]]:
    """Serialise the ``codes`` spots into canonical CSV bytes + per-code rows.

    Returns ``(raw_payload, rows_by_code)``: ``raw_payload`` is the full CSV
    (header + one sorted row per code) the runner persists as a
    :class:`MarketDataSnapshot`; ``rows_by_code`` maps each code to its exact
    row bytes for the consumed-row lineage (``build_consumed_row``). The row
    order is sorted by code so the bytes are deterministic + replayable.
    """
    header_line = ",".join(INTRADAY_QUOTE_HEADER)
    rows_by_code: dict[str, bytes] = {}
    lines: list[str] = [header_line]
    for code in sorted(_bare(c) for c in codes):
        spot = spots[code]
        line = ",".join(
            [
                code,
                _sanitise_field(spot.name),
                repr(spot.price),
                repr(spot.prev_close),
                repr(spot.volume),
                repr(spot.amount),
                repr(spot.change_pct),
                spot.snapshot_at.isoformat(),
            ]
        )
        rows_by_code[code] = line.encode("utf-8")
        lines.append(line)
    raw = "\n".join(lines).encode("utf-8")
    return raw, rows_by_code


# ---------------------------------------------------------------------------
# SELL — deterministic intraday triggers
# ---------------------------------------------------------------------------


def evaluate_intraday_sell_intents(
    spots: Mapping[str, WatchlistMarketSnapshot],
    closes_by_code: Mapping[str, tuple[float, ...]],
    positions: tuple[Position, ...],
    *,
    name_by_code: Mapping[str, str] | None = None,
    config: IntradayTriggerConfig | None = None,
) -> tuple[IntradaySellIntent, ...]:
    """Pick held positions to exit from the live quotes (deterministic).

    For each held code with a fresh spot we evaluate the two intraday triggers
    and emit at most one :class:`IntradaySellIntent`. When both fire the
    structural ``ATR_TRAILING_STOP`` (a break below trend support) takes
    precedence over the single-bar ``DRAWDOWN_STOP`` — a fixed, explainable
    priority (no fragile cross-unit magnitude comparison). The order is sized
    to the lot-aligned **settled** ``available_volume`` (T+1) and priced at
    the live spot. Output is ordered by code for stable, replayable results.

    ``closes_by_code`` is the daily close history per bare code (parsed from
    the persisted T-1 frame by ``add_position.parse_held_series``); a code
    without enough daily history simply cannot fire the ATR trigger (the
    drawdown trigger needs only the live spot).
    """
    cfg = config or IntradayTriggerConfig()
    names = name_by_code or {}
    pos_by_code = {_bare(p.code): p for p in positions}

    intents: list[IntradaySellIntent] = []
    for code in sorted(spots):
        spot = spots[code]
        pos = pos_by_code.get(code)
        if pos is None or pos.available_volume <= 0:
            continue
        price = spot.price
        prev_close = spot.prev_close
        if price <= 0 or prev_close <= 0:
            continue  # defensive — filter_fresh_quotes already drops these
        vol = (pos.available_volume // _LOT) * _LOT
        if vol <= 0:
            continue

        drawdown = (price - prev_close) / prev_close
        closes = closes_by_code.get(code)
        atr = close_atr(closes, cfg.atr_window) if closes else None
        # The ATR trailing stop self-gates on a COMPLETE recent-high window:
        # a partial window would understate the true recent high and fire the
        # stop early (precision over recall — codex U-C3 P2). The drawdown
        # trigger needs no history (it reads the live quote alone).
        recent_high = (
            max(closes[-cfg.recent_high_window:])
            if closes and len(closes) >= cfg.recent_high_window
            else None
        )
        atr_fired = (
            atr is not None
            and atr > 0
            and recent_high is not None
            and price < recent_high - cfg.atr_stop_mult * atr
        )
        drawdown_fired = drawdown <= -cfg.drawdown_threshold

        if atr_fired:
            stop_level = recent_high - cfg.atr_stop_mult * atr  # type: ignore[operator]
            detail = (
                f"intraday price {price:.3f} < trailing stop "
                f"{stop_level:.3f} (recent high {recent_high:.3f} − "
                f"{cfg.atr_stop_mult:.0f}×ATR {atr:.3f})"
            )
            intents.append(
                IntradaySellIntent(
                    code=code,
                    name=names.get(code, code),
                    available_volume=vol,
                    limit_price=price,
                    trigger_kind=IntradayTriggerKind.ATR_TRAILING_STOP,
                    anomaly_reason=detail,
                    drawdown_pct=round(drawdown, 6),
                    atr=round(atr, 4),  # type: ignore[arg-type]
                    recent_high=round(recent_high, 4),  # type: ignore[arg-type]
                    stop_level=round(stop_level, 4),
                )
            )
        elif drawdown_fired:
            detail = (
                f"intraday drawdown {drawdown:.2%} vs prev_close "
                f"{prev_close:.3f} ≤ -{cfg.drawdown_threshold:.0%}"
            )
            intents.append(
                IntradaySellIntent(
                    code=code,
                    name=names.get(code, code),
                    available_volume=vol,
                    limit_price=price,
                    trigger_kind=IntradayTriggerKind.DRAWDOWN_STOP,
                    anomaly_reason=detail,
                    drawdown_pct=round(drawdown, 6),
                    atr=round(atr, 4) if atr else 0.0,
                    recent_high=round(recent_high, 4) if recent_high else 0.0,
                    stop_level=0.0,
                )
            )
    log.info("intraday_sell_intents_evaluated", intents=len(intents))
    return tuple(intents)


def make_intraday_sell_context(
    intent: IntradaySellIntent,
    *,
    now: datetime,
    signal_id: str,
    seq: int,
    snapshot_at: datetime,
    account: AccountInfo,
    positions: tuple[Position, ...],
    prev_close: float | None,
    daily_state: DailyTradingState | None,
    stock_meta: StockMetadata | None,
    risk_engine: RiskEngine,
    open_tickets: tuple,
    circuit_breaker: CircuitBreaker,
    data_quality: DataQualityState,
    watchlist_policy: UniversePolicy,
    watchlist_signal: WatchlistMarketSignal,
    quote_source: str = "intraday_spot",
    analysis_record_id: str = "",
    risk_validation_id: str = "",
) -> MonitoringAssemblyContext:
    """Build the deterministic intraday SELL :class:`MonitoringAssemblyContext`.

    Mirrors ``sell_signal.make_sell_context`` for the intraday intent type.
    ``signal_id`` must carry the ``LINE2-MON-`` prefix (the per-tick id the
    runner derives) so the plan ties back to the persisted intraday-quote
    snapshot for replay. The ``MARKET-`` evidence id references the intraday
    trigger kind; the DataSnapshot's ``snapshot_at`` is the spot fetch time
    (strictly before ``now``, enforced by the InstructionPlan validator).
    """
    if not signal_id.startswith(MONITORING_SIGNAL_PREFIX):
        raise ValueError(
            f"intraday sell signal_id {signal_id!r} must start with "
            f"{MONITORING_SIGNAL_PREFIX!r}"
        )
    evidence_id = f"MARKET-{intent.code}-{intent.trigger_kind.value}"
    invalidation = (
        f"Line-2 deterministic intraday SELL ({intent.trigger_kind.value}); "
        f"exit if the adverse move resolves before fill."
    )[:200]
    data_snapshot = DataSnapshot(
        snapshot_at=snapshot_at,
        quote_source=quote_source,
        prev_close=prev_close if (prev_close and prev_close > 0) else None,
        is_trading_day=True,
        is_trading_hours=True,
    )
    return MonitoringAssemblyContext(
        stock_code=intent.code,
        stock_name=intent.name,
        side=InstructionSide.SELL,
        now=now,
        open_tickets=tuple(open_tickets),
        circuit_breaker=circuit_breaker,
        data_quality=data_quality,
        watchlist_policy=watchlist_policy,
        watchlist_signal=watchlist_signal,
        risk_engine=risk_engine,
        account=account,
        # Bare-code positions so RiskEngine exact-matches the bare order code
        # for a suffixed holding (end-to-end suffix safety — codex N-005).
        positions=normalize_position_codes(positions),
        prev_close=prev_close,
        daily_state=daily_state,
        stock_meta=stock_meta,
        proposed_volume=intent.available_volume,
        proposed_limit_price=intent.limit_price,
        seq=seq,
        signal_id=signal_id,
        # Per-plan correlation handles embed code+seq so a multi-code tick that
        # shares the scan signal_id does not collide on ledger/risk lookups
        # (mirrors make_sell_context — codex N-002 P2). code+seq lead so they
        # survive the 64-char truncation.
        analysis_record_id=(
            analysis_record_id or f"mon:{intent.code}:{seq}:{signal_id}"[:64]
        ),
        risk_validation_id=(
            risk_validation_id or f"rv:{intent.code}:{seq}:{signal_id}"[:64]
        ),
        evidence_ids=(evidence_id,),
        data_snapshot=data_snapshot,
        invalidation_summary=invalidation,
    )


# ---------------------------------------------------------------------------
# ADD — reuse add_position (live-price driven)
# ---------------------------------------------------------------------------


def _evaluate_one_add(
    code: str,
    name: str,
    live_price: float,
    closes: tuple[float, ...],
    position: Position,
    account: AccountInfo,
    regime: MarketRegime,
    config: AddConfig,
) -> AddIntent | AddRejection:
    """Decide an intraday ADD / reject for one held code (first-match reason).

    Reuses the ``add_position`` bans + helpers but is driven by the **live**
    price (not the daily close):

    * **bear regime → no add** (``classify_regime`` over the benchmark index);
    * **anti-martingale** — reject when the live price is more than
      ``max_add_drawdown_pct`` below cost (averaging down into a loser);
    * **oversold vs cost** — the live price must be below cost (a dip worth
      adding to) — combined with the anti-martingale floor this bounds the add
      to a *controlled* pullback ``[cost·(1−max_add_drawdown), cost)``;
    * **no structural breakdown** — the live price stays within
      ``breakdown_tolerance`` of the long daily MA;
    * **headroom** — the post-add position stays under the single-stock cap;
    * **size** — Van Tharp fixed fraction with a close-based ATR stop, capped
      by the headroom (never scaled up to average down).
    """
    if len(closes) < max(config.atr_window + 1, config.ma_long_window):
        return AddRejection(code, AddRejectReason.INSUFFICIENT_HISTORY)
    if regime is MarketRegime.BEAR:
        return AddRejection(
            code, AddRejectReason.BEAR_REGIME, "bear regime forbids add"
        )
    if live_price <= 0 or position.cost_price <= 0:
        return AddRejection(code, AddRejectReason.NO_SERIES, "no price / cost")

    drawdown = (live_price - position.cost_price) / position.cost_price
    if drawdown < -config.max_add_drawdown_pct:
        return AddRejection(
            code,
            AddRejectReason.MARTINGALE,
            f"drawdown {drawdown:.1%} beyond -{config.max_add_drawdown_pct:.0%}",
        )
    if drawdown >= 0:
        # Not below cost — no dip to add to (oversold-vs-cost gate).
        return AddRejection(code, AddRejectReason.NOT_OVERSOLD)

    ma_long = moving_average(closes, config.ma_long_window)
    no_breakdown = (
        ma_long is not None
        and ma_long > 0
        and live_price >= ma_long * (1.0 - config.breakdown_tolerance)
    )
    if not no_breakdown:
        return AddRejection(code, AddRejectReason.STRUCTURAL_BREAKDOWN)

    position_value = position.volume * live_price
    has_headroom = (
        account.total_assets > 0
        and position_value < config.max_single_stock_pct * account.total_assets
    )
    if not has_headroom:
        return AddRejection(code, AddRejectReason.NO_HEADROOM)

    atr = close_atr(closes, config.atr_window)
    if atr is None or atr <= 0:
        return AddRejection(code, AddRejectReason.INSUFFICIENT_HISTORY, "no ATR")

    vt_shares = vanthorp_size(
        equity=account.total_assets, atr=atr, price=live_price, config=config
    )
    headroom_value = (
        config.max_single_stock_pct * account.total_assets - position_value
    )
    headroom_shares = (
        int(max(0.0, headroom_value) // (live_price * _LOT)) * _LOT
    )
    add_shares = min(vt_shares, headroom_shares)
    if add_shares <= 0:
        return AddRejection(
            code,
            AddRejectReason.NO_HEADROOM,
            f"van-tharp {vt_shares} / headroom {headroom_shares} → 0 lots",
        )

    stop_price = round(live_price - config.atr_stop_mult * atr, 2)
    rationale = (
        f"盘中补仓: 实时价 {live_price:.3f} 低于成本 {position.cost_price:.3f} "
        f"({drawdown:.1%}) 超卖未破位; Van Tharp 固定分数 "
        f"{config.risk_fraction:.0%} 风险, ATR {atr:.3f} × "
        f"{config.atr_stop_mult:.0f} 移动止损 @ {stop_price}"
    )
    return AddIntent(
        code=code,
        name=name,
        add_volume=add_shares,
        limit_price=live_price,
        atr=round(atr, 4),
        stop_price=stop_price,
        rsi=0.0,  # intraday add is dip-vs-cost driven, not RSI-gated
        rationale=rationale,
    )


def evaluate_intraday_add_intents(
    spots: Mapping[str, WatchlistMarketSnapshot],
    closes_by_code: Mapping[str, tuple[float, ...]],
    positions: tuple[Position, ...],
    account: AccountInfo,
    *,
    index_closes: tuple[float, ...],
    name_by_code: Mapping[str, str] | None = None,
    config: AddConfig | None = None,
) -> AddEvaluation:
    """Evaluate every held code with a fresh quote for a disciplined ADD.

    Live-price driven (the ``add_position`` daily evaluator would misread a
    cumulative intraday turnover as a daily volume) but reuses every
    ``add_position`` ban + sizing helper. Output is ordered by code for
    replayable results.
    """
    cfg = config or AddConfig()
    names = name_by_code or {}
    pos_by_code = {_bare(p.code): p for p in positions}

    intents: list[AddIntent] = []
    rejections: list[AddRejection] = []
    regime = classify_regime(index_closes)
    for code in sorted(spots):
        pos = pos_by_code.get(code)
        if pos is None:
            continue
        closes = closes_by_code.get(code)
        if closes is None:
            rejections.append(AddRejection(code, AddRejectReason.NO_SERIES))
            continue
        outcome = _evaluate_one_add(
            code,
            names.get(code, code),
            spots[code].price,
            closes,
            pos,
            account,
            regime,
            cfg,
        )
        if isinstance(outcome, AddIntent):
            intents.append(outcome)
        else:
            rejections.append(outcome)
    log.info(
        "intraday_add_intents_evaluated",
        intents=len(intents),
        rejections=len(rejections),
        regime=regime.value,
    )
    return AddEvaluation(intents=tuple(intents), rejections=tuple(rejections))


__all__ = [
    "FEATURE_CODE_VERSION",
    "INTRADAY_QUOTE_HEADER",
    "IntradaySellIntent",
    "IntradayTriggerConfig",
    "IntradayTriggerKind",
    "evaluate_intraday_add_intents",
    "evaluate_intraday_sell_intents",
    "filter_fresh_quotes",
    "make_intraday_sell_context",
    "serialize_intraday_quotes",
]
