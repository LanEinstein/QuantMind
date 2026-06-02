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
# replay manifest fails closed instead of silently recomputing. v2: added the
# TAKE_PROFIT (+1R tranche) + WEIGHT_TRIM triggers (P-005). v3: added the
# THESIS_QUANT_BREAK trigger (W-004), which adds a deterministic SELL-intent
# output when a held thesis is broken (the maths is otherwise unchanged — an
# empty thesis-break map reproduces v2 outputs bit-for-bit).
FEATURE_CODE_VERSION: str = "monitoring.intraday_triggers/v3"

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
    TAKE_PROFIT = "take_profit"
    """Live price ≥ cost + ``r_multiple``×R (R = ``atr_stop_mult``×ATR) AND net
    profit → sell a ``tranche_fraction`` slice to lock gains (P0-10-amendment-
    line2-2026-05-30). The residual rides the existing ATR trailing stop."""
    WEIGHT_TRIM = "weight_trim"
    """Position weight (vol×live / total_assets) above ``max_single_stock_pct``
    × (1 + ``trim_band``) → trim back toward ``trim_target_pct`` (over-allocation
    rebalance, P0-10-amendment-line2-2026-05-30)."""
    THESIS_QUANT_BREAK = "thesis_quant_break"
    """The held position's deterministic PositionThesis is BROKEN over PIT data
    (whitelist quant templates only, computed by ``monitoring.thesis_break`` —
    zero LLM; P0-10-amendment-line2-2026-06-01 §1.3). A full settled-volume exit.
    Strictly ADD-only sell pressure: it ranks BELOW the protective risk exits
    (ATR / drawdown) and is never evaluated when one of them fired, so it can
    never relax / suppress an existing stop — only add an exit a thesis break
    justifies."""


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
    # Take-profit + over-allocation trim (P0-10-amendment-line2-2026-05-30).
    r_multiple: float = 1.0  # take profit at cost + r_multiple × R
    tranche_fraction: float = 0.5  # sell this fraction of settled volume on +1R
    trim_band: float = 0.10  # trigger trim above cap × (1 + trim_band) = 16.5%
    trim_target_pct: float = 0.13  # trim the position back toward this weight


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


def _take_profit_intent(
    *,
    code: str,
    name: str,
    spot: WatchlistMarketSnapshot,
    pos: Position,
    atr: float | None,
    cfg: IntradayTriggerConfig,
    already_taken: frozenset[str],
) -> IntradaySellIntent | None:
    """Lock a tranche of gains at ``cost + r_multiple × R`` (R = k×ATR).

    Returns ``None`` (no take-profit) when this episode already took profit,
    the ATR/cost is unusable, the price is not yet at the +R target, the
    position is not net-profitable, or the tranche floors below one lot.
    """
    if code in already_taken or atr is None or atr <= 0:
        return None
    cost = pos.cost_price
    price = spot.price
    if cost <= 0:
        return None
    r_unit = cfg.atr_stop_mult * atr
    target = cost + cfg.r_multiple * r_unit
    if price < target or price <= cost:  # not at +R target, or not net profit
        return None
    settled = (pos.available_volume // _LOT) * _LOT
    sell_vol = int((settled * cfg.tranche_fraction) // _LOT) * _LOT
    if sell_vol <= 0:  # sub-1-lot tranche → skip (never sell 0)
        return None
    detail = (
        f"止盈 +{cfg.r_multiple:.0f}R: 实时价 {price:.3f} ≥ 成本 {cost:.3f} + "
        f"{cfg.r_multiple:.0f}×R({r_unit:.3f}); 减 {sell_vol} 股锁盈,余仓续交移动止损"
    )
    return IntradaySellIntent(
        code=code,
        name=name,
        available_volume=sell_vol,
        limit_price=price,
        trigger_kind=IntradayTriggerKind.TAKE_PROFIT,
        anomaly_reason=detail,
        drawdown_pct=round((price - spot.prev_close) / spot.prev_close, 6),
        atr=round(atr, 4),
        recent_high=0.0,
        stop_level=round(target, 4),
    )


def _weight_trim_intent(
    *,
    code: str,
    name: str,
    spot: WatchlistMarketSnapshot,
    pos: Position,
    account: AccountInfo,
    cfg: IntradayTriggerConfig,
    max_single_stock_pct: float,
) -> IntradaySellIntent | None:
    """Trim an over-allocated position back toward ``trim_target_pct``.

    Fires only when the position weight (full holding × live / total assets)
    exceeds ``max_single_stock_pct × (1 + trim_band)``. The trim quantity is
    clamped to the lot-aligned **settled** ``available_volume`` (a fresh,
    unsettled BUY cannot be sold under T+1). Returns ``None`` when total assets
    are non-positive, the weight is within band, or the trim floors below a lot.
    """
    total = float(account.total_assets)
    price = spot.price
    if total <= 0:
        return None
    weight = pos.volume * price / total
    threshold = max_single_stock_pct * (1.0 + cfg.trim_band)
    if weight <= threshold:
        return None
    excess_value = pos.volume * price - cfg.trim_target_pct * total
    if excess_value <= 0:
        return None
    trim_shares = int(excess_value // (price * _LOT)) * _LOT
    settled = (pos.available_volume // _LOT) * _LOT
    trim_shares = min(trim_shares, settled)
    if trim_shares <= 0:  # nothing settled to trim, or sub-1-lot → skip
        return None
    detail = (
        f"超配回调: 持仓权重 {weight:.1%} > {threshold:.1%} (上限 "
        f"{max_single_stock_pct:.0%}×{1 + cfg.trim_band:.2f}); 减 {trim_shares} 股 "
        f"回 ~{cfg.trim_target_pct:.0%}"
    )
    return IntradaySellIntent(
        code=code,
        name=name,
        available_volume=trim_shares,
        limit_price=price,
        trigger_kind=IntradayTriggerKind.WEIGHT_TRIM,
        anomaly_reason=detail,
        drawdown_pct=round((price - spot.prev_close) / spot.prev_close, 6),
        atr=0.0,
        recent_high=0.0,
        stop_level=0.0,
    )


def _thesis_break_intent(
    *,
    code: str,
    name: str,
    vol: int,
    price: float,
    drawdown: float,
    reason: str,
    max_single_instruction_amount: float,
    atr: float | None,
) -> IntradaySellIntent | None:
    """Full-exit SELL for a broken thesis, clamped to the single-instruction cap.

    The clamp keeps the SELL ≤ ``max_single_instruction_amount`` (P0-7 check #9,
    ¥50k 单次) so the builder never REJECTS it for size — preserving the
    only-add-pressure invariant even for a large position (codex W-004 P2): a
    rejected full exit must never replace a smaller pre-existing trigger that
    would have passed. Returns ``None`` when even one lot exceeds the cap, so the
    caller falls back to the lower-priority triggers (the feature never
    suppresses them).
    """
    cap_lots = (
        int(max_single_instruction_amount // (price * _LOT)) * _LOT
        if max_single_instruction_amount > 0
        else vol
    )
    sell_vol = min(vol, cap_lots) if cap_lots > 0 else 0
    if sell_vol <= 0:
        return None
    return IntradaySellIntent(
        code=code,
        name=name,
        available_volume=sell_vol,
        limit_price=price,
        trigger_kind=IntradayTriggerKind.THESIS_QUANT_BREAK,
        anomaly_reason=reason,
        drawdown_pct=round(drawdown, 6),
        atr=round(atr, 4) if atr else 0.0,
        recent_high=0.0,
        stop_level=0.0,
    )


def evaluate_intraday_sell_intents(
    spots: Mapping[str, WatchlistMarketSnapshot],
    closes_by_code: Mapping[str, tuple[float, ...]],
    positions: tuple[Position, ...],
    *,
    name_by_code: Mapping[str, str] | None = None,
    config: IntradayTriggerConfig | None = None,
    account: AccountInfo | None = None,
    max_single_stock_pct: float = 0.15,
    take_profit_already_taken: frozenset[str] = frozenset(),
    thesis_break_by_code: Mapping[str, str] | None = None,
    max_single_instruction_amount: float = 50_000.0,
) -> tuple[IntradaySellIntent, ...]:
    """Pick held positions to exit from the live quotes (deterministic).

    For each held code with a fresh spot we evaluate the intraday triggers and
    emit at most one :class:`IntradaySellIntent`, by a fixed, explainable
    priority (no fragile cross-unit magnitude comparison):

        ATR_TRAILING_STOP > DRAWDOWN_STOP > THESIS_QUANT_BREAK
          > TAKE_PROFIT > WEIGHT_TRIM

    Risk exits (a break below trend support, then a single-bar intraday
    drawdown) always outrank everything — a protective stop is never masked.
    THESIS_QUANT_BREAK (a broken deterministic PositionThesis, W-004) ranks just
    below the protective stops and just ABOVE the profit-taking triggers: a
    broken thesis fully exits rather than merely taking a tranche of profit, yet
    it can NEVER relax an existing stop. Crucially it is **strictly ADD-only**:
    an empty ``thesis_break_by_code`` reproduces the prior (v2) outputs
    bit-for-bit, so the feature can only ever ADD a SELL, never remove or weaken
    one (the W-004 only-add-pressure red line). The risk exits + THESIS_QUANT_BREAK
    sell the full lot-aligned **settled** ``available_volume``; take-profit sells
    a ``tranche_fraction`` slice; weight-trim sells just enough to rebalance.
    Output is ordered by code for stable, replayable results.

    ``account`` enables the TAKE_PROFIT + WEIGHT_TRIM triggers (legacy callers
    that pass no account get only the risk exits — back-compat).
    ``take_profit_already_taken`` (P-006, ledger-derived) suppresses a repeat
    take-profit on a still-open episode. ``max_single_stock_pct`` is the
    single-source single-stock cap (P0-7) the trim band references.
    ``thesis_break_by_code`` (code → deterministic break reason, computed by
    ``monitoring.thesis_break``) drives the THESIS_QUANT_BREAK exit; absent /
    empty → no thesis exits (the prior behaviour exactly).

    ``closes_by_code`` is the daily close history per bare code (parsed from
    the persisted T-1 frame by ``add_position.parse_held_series``); a code
    without enough daily history cannot fire the ATR trigger or take-profit (R
    needs the ATR); the drawdown trigger needs only the live spot.
    """
    cfg = config or IntradayTriggerConfig()
    names = name_by_code or {}
    pos_by_code = {_bare(p.code): p for p in positions}
    thesis_breaks = thesis_break_by_code or {}

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
        else:
            # No protective stop fired. A broken deterministic thesis (W-004)
            # exits FIRST (ranked above the profit-taking triggers so a broken
            # thesis fully exits rather than merely trims) — but ONLY when it can
            # size a SELL that passes the single-instruction cap. If it cannot
            # (a position so large that even the cap-clamped lot floors to 0), we
            # fall through to the lower-priority triggers, so enabling the feature
            # NEVER suppresses a smaller exit that would have passed (codex W-004
            # P2 / the only-add-pressure red line). An empty thesis_breaks map
            # skips this entirely (v2-identical output).
            thesis_intent = (
                _thesis_break_intent(
                    code=code,
                    name=names.get(code, code),
                    vol=vol,
                    price=price,
                    drawdown=drawdown,
                    reason=thesis_breaks[code],
                    max_single_instruction_amount=max_single_instruction_amount,
                    atr=atr,
                )
                if code in thesis_breaks
                else None
            )
            if thesis_intent is not None:
                intents.append(thesis_intent)
            elif account is not None:
                # The lower-priority profit-taking / rebalance triggers (only
                # when an account is supplied). Take-profit outranks weight-trim;
                # at most one fires.
                tp = _take_profit_intent(
                    code=code,
                    name=names.get(code, code),
                    spot=spot,
                    pos=pos,
                    atr=atr,
                    cfg=cfg,
                    already_taken=take_profit_already_taken,
                )
                trim = (
                    None
                    if tp is not None
                    else _weight_trim_intent(
                        code=code,
                        name=names.get(code, code),
                        spot=spot,
                        pos=pos,
                        account=account,
                        cfg=cfg,
                        max_single_stock_pct=max_single_stock_pct,
                    )
                )
                if tp is not None:
                    intents.append(tp)
                elif trim is not None:
                    intents.append(trim)
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
