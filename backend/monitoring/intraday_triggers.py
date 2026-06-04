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
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

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
from backend.monitoring.intraday_calibration import (
    ChandelierConfig,
    DrawdownCalibrationConfig,
    TakeProfitCalibrationConfig,
    TieredTakeProfitConfig,
    derive_drawdown_threshold,
    derive_entry_anchored_stop,
    effective_r_multiple,
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
# The E2 confirmation window is defined in Asia/Shanghai wall-clock minutes;
# tick_time is normalized exactly like utils.trading_hours.is_trading_hours
# (naive → assume Shanghai; aware → convert) so a UTC-aware caller cannot
# silently shift the window by 8h (review P1 finding).
_SHANGHAI = ZoneInfo("Asia/Shanghai")

# Pinned feature-code version — bump when the trigger maths changes so a stale
# replay manifest fails closed instead of silently recomputing. v2: added the
# TAKE_PROFIT (+1R tranche) + WEIGHT_TRIM triggers (P-005). v3: added the
# THESIS_QUANT_BREAK trigger (W-004), which adds a deterministic SELL-intent
# output when a held thesis is broken (the maths is otherwise unchanged — an
# empty thesis-break map reproduces v2 outputs bit-for-bit). v4: added the
# long-term-hold (intact PositionThesis) exemption — such holds skip TAKE_PROFIT
# + the soft WEIGHT_TRIM but are still trimmed at the single-stock hard cap; an
# empty long_term_hold_codes set reproduces v3 outputs bit-for-bit
# (P0-10-amendment-line2-2026-06-03-thesis-gated-takeprofit-exemption). v5: the
# DRAWDOWN_STOP threshold may be derived per-stock from its |daily return|
# percentile when a DrawdownCalibrationConfig is supplied; a None calibration
# reproduces v4 outputs bit-for-bit
# (P0-7-amendment-2026-06-03-adaptive-intraday-thresholds). v6: that adaptive
# threshold may be tightened in a BEAR regime; a None regime reproduces v5
# (P0-7-amendment-2026-06-03-regime-conditioned-drawdown). v7: the TAKE_PROFIT
# r_multiple may be regime-conditioned (BULL later / BEAR earlier) when a
# TakeProfitCalibrationConfig is supplied via its own independent regime
# channel; a None calibration reproduces v6 outputs bit-for-bit
# (P0-7-amendment-2026-06-04-regime-conditioned-takeprofit). v8: TAKE_PROFIT
# may run a tiered ladder (+1R half → +2R another tranche → residual rides
# the trailing stop) gated by the episode's ledger-folded tiers-taken count;
# a None tiered config reproduces v7 outputs bit-for-bit
# (P0-10-amendment-line2-2026-06-04-tiered-takeprofit). v9: the trailing stop
# may be ENTRY-ANCHORED (max(cost−2×ATR, max-close-since-entry−3×ATR)) with
# depth-tiered confirmation (deep breach immediate; shallow breach only in
# the late-session window) when a ChandelierConfig is supplied; a None config
# reproduces v8 outputs bit-for-bit. The take-profit R-unit deliberately
# stays ``cfg.atr_stop_mult×ATR`` (the D1-c/D1-d ladders are calibrated in
# those units — the chandelier multiplier conditions ONLY the trailing stop)
# (P0-7-amendment-2026-06-04-entry-anchored-chandelier). v10: the
# sell-into-strength family (LIMIT_BREAK / SURGE_FADE / VOLUME_CLIMAX /
# OVERBOUGHT_BIAS, each a 1/3-tranche discretionary sell) + the
# SEALED_LIMIT_HOLD suppression (a sealed limit-up rides — TP + strength
# sells are suppressed that tick) when a StrengthSellConfig is supplied; a
# None config reproduces v9 outputs bit-for-bit
# (P0-10-amendment-line2-2026-06-04-sell-into-strength). v11: the
# STALE_EXIT time stop (held >=stale_days with <min_return and no recent
# post-entry high -> full settled exit) + the next-day RE_ENTRY BUY after a
# delivered discretionary sell (evaluate_reentry_add_intents); None configs
# reproduce v10 outputs bit-for-bit
# (P0-10-amendment-line2-2026-06-04-reentry-and-time-stop).
FEATURE_CODE_VERSION: str = "monitoring.intraday_triggers/v11"

# Canonical CSV header for a persisted intraday quote snapshot (one row per
# fired held code — the consumed-row lineage the IntradayTriggerManifest pins).
INTRADAY_QUOTE_HEADER: tuple[str, ...] = (
    "code",
    "name",
    "price",
    "prev_close",
    "high",
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
    LIMIT_BREAK = "limit_break"
    """炸板 — touched limit-up intraday then fell back ≥``break_pullback``:
    the strongest A-share distribution signal (next-day premium negative);
    sells a 1/3 tranche (P0-10-amendment-line2-2026-06-04-sell-into-strength)."""
    SURGE_FADE = "surge_fade"
    """冲高回落 — intraday gain ≥``surge_min`` at the day high, then a fade
    ≥``fade_min`` from that high; profit-gated 1/3 tranche."""
    VOLUME_CLIMAX = "volume_climax"
    """放量滞涨 — cumulative amount ≥``climax_mult``× the 5-day average while
    the price stalls (<``stall_max``) at an extended level (≥``extension_min``
    above MA20); profit-gated 1/3 tranche."""
    OVERBOUGHT_BIAS = "overbought_bias"
    """乖离超买 — (price−MA20)/MA20 ≥ ``bias_threshold`` (classic A-share
    BIAS sell band); profit-gated 1/3 tranche."""
    STALE_EXIT = "stale_exit"
    """时间止损 — held ≥``stale_days`` trading days with interval return
    <``min_return`` and no post-entry high in the last ``high_window``
    closes: the momentum edge is spent, free the slot (E4,
    P0-10-amendment-line2-2026-06-04-reentry-and-time-stop). Full settled
    exit (¥50k-clamped)."""


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
class StrengthSellConfig:
    """Locked sell-into-strength thresholds (runtime-immutable, E3).

    Each trigger sells ``tranche_fraction`` of the settled volume (rounded
    to lots, skipped when it rounds to zero, clamped to the ¥50k single-
    instruction cap). Evidence anchors per threshold: see
    P0-10-amendment-line2-2026-06-04-sell-into-strength §1.1 (Wan 2015 炸板 /
    华安 2026 放量首板 / 民生 2025 盘中过强反转 / BIAS 经典带). Recalibrated
    only offline (P2-2 shadow + human gate + git + restart).
    """

    break_pullback: float = 0.02  # 炸板: fell ≥2% back from the limit price
    surge_min: float = 0.06  # 冲高: day-high gain ≥ +6% vs prev_close
    fade_min: float = 0.03  # 回落: ≥3% down from the day high
    climax_mult: float = 3.0  # 放量: cumulative amount ≥ 3× 5-day average
    stall_max: float = 0.03  # 滞涨: intraday gain < +3%
    extension_min: float = 0.10  # 高位: price ≥ MA20 × 1.10
    bias_threshold: float = 0.15  # 乖离: (price−MA20)/MA20 ≥ +15%
    tranche_fraction: float = 1 / 3  # sell this slice per fired signal
    ma_window: int = 20
    amount_window: int = 5
    limit_epsilon: float = 0.005  # price-grid tolerance for limit touch/seal


@dataclass(frozen=True)
class StaleExitConfig:
    """Runtime-immutable time-stop thresholds (E4).

    Research anchor (dossier §2.4): a momentum swing's edge is spent in
    ~8-15 trading days; a position that has gone nowhere by then is dead
    weight in one of the ≤5 slots. The STALE_EXIT is the deterministic
    lower bound of the rotation's "incumbent weak enough" test — it needs
    no challenger to exist. Recalibrated only offline (P2-2 + human gate).
    """

    stale_days: int = 10  # held trading days before the test applies
    min_return: float = 0.03  # interval return below this = going nowhere
    high_window: int = 5  # no post-entry high within the last N closes


@dataclass(frozen=True)
class ReentryConfig:
    """Runtime-immutable next-day re-entry gate (E5).

    After a DELIVERED discretionary sell (TAKE_PROFIT / strength family) the
    residual position may be topped back up the NEXT morning when the
    overnight discount materialises: open-window price ≥``discount`` below
    yesterday's sale, structure intact (price > MA). Microstructure anchor
    (dossier §3.1): T+1 forces sellers onto the next-day open — buy there,
    sell into intraday strength. Protective-stop exits are NEVER re-entered
    (trend falsified — Turtle fail-safe; a fresh entry is Line-1's job).
    """

    discount: float = 0.02  # buy back ≥2% below yesterday's sale price
    window_start_minute: int = 9 * 60 + 30  # 09:30 Asia/Shanghai
    window_end_minute: int = 10 * 60  # 10:00 (exclusive)
    ma_window: int = 20  # structure gate: price must hold above this MA


@dataclass(frozen=True)
class ReentryAddIntent(AddIntent):
    """An E5 re-entry BUY — same wire shape as :class:`AddIntent` (it rides
    the unchanged ADD pipeline) but a DISTINCT type so the runner can give
    it its own dedup kind (``reentry``) and persist the gate's actual
    decision inputs (yesterday's sale + discount) into the trigger manifest
    instead of a misleading plain-ADD record (codex P2)."""

    sold_price: float = 0.0
    reentry_discount: float = 0.0


# The sell kinds whose DELIVERED sale makes a code re-entry-eligible the
# next day: discretionary harvests only — a protective-stop / thesis /
# stale exit means the trend is falsified and is never bought back.
REENTRY_ELIGIBLE_KINDS: frozenset[str] = frozenset(
    {
        IntradayTriggerKind.TAKE_PROFIT.value,
        IntradayTriggerKind.LIMIT_BREAK.value,
        IntradayTriggerKind.SURGE_FADE.value,
        IntradayTriggerKind.VOLUME_CLIMAX.value,
        IntradayTriggerKind.OVERBOUGHT_BIAS.value,
    }
)


def limit_up_price(code: str, name: str, prev_close: float) -> float | None:
    """Deterministic limit-up price for KNOWN regimes (E3 trigger input ONLY).

    Exact prefix rules: ``60*``/``00*`` main board → 10% (5% when the name
    carries ``ST``), ``30*`` 创业板 / ``688*`` 科创 → 20%. Everything else —
    notably ETFs, whose limit regime is mixed (most 10%, 创业板/科创/跨境
    ETFs 20%) — returns ``None`` = FAIL CLOSED: no LIMIT_BREAK and no
    sealed-board hold for codes whose board we cannot derive exactly (a 10%
    guess would fire a FALSE limit-break on a 20% ETF after a normal +10%
    day — codex P2). The RiskEngine's limit_up_down_block stays the sole
    order-legality authority. ``None`` also when prev_close is unusable.
    """
    if not (
        isinstance(prev_close, (int, float))
        and math.isfinite(prev_close)
        and prev_close > 0
    ):
        return None
    bare = _bare(code)
    if bare.startswith(("60", "00")):
        ratio = "0.05" if "ST" in name.upper() else "0.10"
    elif bare.startswith(("30", "688")):
        ratio = "0.20"
    else:
        return None  # unknown limit regime (ETF etc.) → fail closed
    # Exchange half-up rounding (SSE/SZSE convention, mirrors
    # backend.data.stock_metadata) — Python round() is banker's/binary and
    # rounds e.g. 1.65×1.1=1.815 DOWN to 1.81 vs the exchange's 1.82,
    # which would misclassify touches/seals (codex P2 cycle-3).
    limit = (
        Decimal(str(prev_close)) * (Decimal("1") + Decimal(ratio))
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(limit)


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
    # The drawdown threshold actually applied this tick (per-stock adaptive value
    # when calibration is on, else the static config). Carried so the persisted
    # IntradayTriggerRecord records the threshold that FIRED — not the static
    # config — so audit / offline replay reproduce the decision
    # (P0-7-amendment-2026-06-03). ``None`` on non-drawdown triggers → the
    # recorder falls back to the static config (drawdown threshold is not their
    # firing criterion).
    effective_drawdown_threshold: float | None = None
    # The take-profit r_multiple in force this tick (regime-conditioned tier
    # when the D1-c calibration is on, else the static config). Carried on
    # EVERY intent — a lower-priority trigger's record must reproduce "why
    # TAKE_PROFIT did not fire first" under the conditioned target
    # (P0-7-amendment-2026-06-04, mirroring the drawdown precedent above).
    # ``None`` only on legacy constructions → the recorder falls back to the
    # static config.
    effective_r_multiple: float | None = None
    # The 1-based take-profit tier this intent fires (D1-d tiered ladder,
    # P0-10-amendment-line2-2026-06-04). ``None`` on non-TAKE_PROFIT intents
    # and when the tiered ladder is off (single-tier v7 behaviour).
    take_profit_tier: int | None = None
    # The episode's ledger-folded tiers-taken count in force this tick —
    # carried on EVERY intent when the ladder is on (a lower-priority
    # trigger's record must reproduce WHY take-profit was gated at a higher
    # tier; mirrors the effective_drawdown_threshold precedent — codex P2).
    # ``None`` when the tiered ladder is off.
    take_profit_tiers_taken: int | None = None
    # Entry-anchored chandelier (E1, P0-7-amendment-2026-06-04-entry-
    # anchored-chandelier). ``effective_atr_stop_mult`` = the trailing-stop
    # multiplier actually in force (3.0 when the chandelier layer governs /
    # 2.0 for the initial money-management layer / None on legacy v8 maths →
    # the recorder falls back to the static config). ``stop_anchor`` = the
    # highest-close-since-entry anchor (floored at cost). Both carried on
    # EVERY intent when the feature is on (replay must reproduce why the
    # trailing stop did or did not fire first — the dd_thr precedent).
    # ``stop_governing`` ("initial" | "chandelier") only on the
    # ATR_TRAILING_STOP intent itself (message wording: 止损 vs 回撤锁盈).
    effective_atr_stop_mult: float | None = None
    stop_anchor: float | None = None
    stop_governing: str | None = None


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
                # The day high is a DECISION input for LIMIT_BREAK /
                # SURGE_FADE (codex P2) — replay must recompute the touch /
                # fade from the persisted row alone.
                repr(spot.high),
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
    effective_drawdown_threshold: float,
    effective_r: float,
    tier_ladder: tuple[float, ...] | None = None,
    tiers_taken: int = 0,
    effective_atr_stop_mult: float | None = None,
    stop_anchor: float | None = None,
) -> IntradaySellIntent | None:
    """Lock a tranche of gains at the (possibly tiered) take-profit target.

    ``effective_r`` is the r_multiple in force this tick — the regime-
    conditioned tier when the D1-c calibration is on, else the static
    ``cfg.r_multiple`` (P0-7-amendment-2026-06-04). With a ``tier_ladder``
    (D1-d, P0-10-amendment-line2-2026-06-04) the NEXT untaken tier gates the
    target — ``cost + ladder[tiers_taken] × effective_r × R`` — and an
    exhausted ladder takes no further profit (the residual rides the
    trailing stop). ``None`` ladder reproduces the single-target v7 maths.
    Returns ``None`` when this episode already took profit (legacy gate),
    the ATR/cost is unusable, the price is not yet at the target, the
    position is not net-profitable, or the tranche floors below one lot.
    """
    if code in already_taken or atr is None or atr <= 0:
        return None
    cost = pos.cost_price
    price = spot.price
    if cost <= 0:
        return None
    r_unit = cfg.atr_stop_mult * atr
    if tier_ladder is None:
        tier_index: int | None = None
        target = cost + effective_r * r_unit
        tier_label = f"+{effective_r:g}R"
    else:
        if tiers_taken >= len(tier_ladder):
            return None  # ladder exhausted — residual rides the ATR stop
        tier_index = tiers_taken + 1
        target = cost + tier_ladder[tiers_taken] * effective_r * r_unit
        tier_label = (
            f"第{tier_index}档 +{tier_ladder[tiers_taken] * effective_r:g}R"
        )
    if price < target or price <= cost:  # not at target, or not net profit
        return None
    settled = (pos.available_volume // _LOT) * _LOT
    sell_vol = int((settled * cfg.tranche_fraction) // _LOT) * _LOT
    if sell_vol <= 0:  # sub-1-lot tranche → skip (never sell 0)
        return None
    detail = (
        f"止盈 {tier_label}: 实时价 {price:.3f} ≥ 目标 {target:.3f} "
        f"(成本 {cost:.3f}, R={r_unit:.3f}); 减 {sell_vol} 股锁盈,余仓续交移动止损"
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
        effective_drawdown_threshold=effective_drawdown_threshold,
        effective_r_multiple=effective_r,
        take_profit_tier=tier_index,
        take_profit_tiers_taken=(
            tiers_taken if tier_ladder is not None else None
        ),
        effective_atr_stop_mult=effective_atr_stop_mult,
        stop_anchor=stop_anchor,
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
    effective_drawdown_threshold: float,
    effective_r: float,
    take_profit_tiers_taken: int | None = None,
    hard_cap_only: bool = False,
    max_single_instruction_amount: float = 50_000.0,
    effective_atr_stop_mult: float | None = None,
    stop_anchor: float | None = None,
) -> IntradaySellIntent | None:
    """Trim an over-allocated position back toward ``trim_target_pct``.

    Fires only when the position weight (full holding × live / total assets)
    exceeds ``max_single_stock_pct × (1 + trim_band)``. The trim quantity is
    clamped to the lot-aligned **settled** ``available_volume`` (a fresh,
    unsettled BUY cannot be sold under T+1). Returns ``None`` when total assets
    are non-positive, the weight is within band, or the trim floors below a lot.

    ``hard_cap_only`` (a long-term-hold, P0-10-amendment-line2-2026-06-03):
    such a position is exempt from the **soft** re-balance but still bounded by
    the single-stock **hard cap** (P0-7) — it fires only above
    ``max_single_stock_pct`` (no ``trim_band`` buffer) and trims back to exactly
    that cap (not ``trim_target_pct``). So a conviction hold may ride up to the
    cap but never breaches the concentration red line.
    """
    total = float(account.total_assets)
    price = spot.price
    if total <= 0:
        return None
    weight = pos.volume * price / total
    if hard_cap_only:
        threshold = max_single_stock_pct
        target_pct = max_single_stock_pct
    else:
        threshold = max_single_stock_pct * (1.0 + cfg.trim_band)
        target_pct = cfg.trim_target_pct
    if weight <= threshold:
        return None
    excess_value = pos.volume * price - target_pct * total
    if excess_value <= 0:
        return None
    if hard_cap_only:
        # Round UP to the next lot so the post-trim weight lands AT/BELOW the
        # hard cap — flooring would leave a residual breach (e.g. 16% → 15.2%),
        # violating the concentration red line this path enforces (codex P2).
        trim_shares = math.ceil(excess_value / (price * _LOT)) * _LOT
        # Clamp to the single-instruction cap (P0-7 check #9 applies to SELLs):
        # an oversized full-excess trim would be REJECTED downstream and then
        # deduped as "fired", leaving the position stuck above the cap with no
        # sell. A capped partial trim is a VALID SELL that reduces the position
        # now and re-fires on the next session until it converges ≤ cap (codex
        # P2 cycle-2; mirrors the thesis-break exit clamp).
        if max_single_instruction_amount > 0:
            cap_lots = int(max_single_instruction_amount // (price * _LOT)) * _LOT
            trim_shares = min(trim_shares, cap_lots)
    else:
        # Soft re-balance toward trim_target_pct (13%) has ample buffer below
        # the cap, so flooring (sell no more than needed) is the safe direction.
        trim_shares = int(excess_value // (price * _LOT)) * _LOT
    settled = (pos.available_volume // _LOT) * _LOT
    trim_shares = min(trim_shares, settled)
    if trim_shares <= 0:  # nothing settled to trim, or sub-1-lot → skip
        return None
    band_label = (
        f"硬顶 {max_single_stock_pct:.0%}"
        if hard_cap_only
        else f"上限 {max_single_stock_pct:.0%}×{1 + cfg.trim_band:.2f}"
    )
    detail = (
        f"超配回调: 持仓权重 {weight:.1%} > {threshold:.1%} ({band_label}); "
        f"减 {trim_shares} 股回 ~{target_pct:.0%}"
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
        effective_drawdown_threshold=effective_drawdown_threshold,
        effective_r_multiple=effective_r,
        take_profit_tiers_taken=take_profit_tiers_taken,
        effective_atr_stop_mult=effective_atr_stop_mult,
        stop_anchor=stop_anchor,
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
    effective_drawdown_threshold: float,
    effective_r: float,
    take_profit_tiers_taken: int | None = None,
    effective_atr_stop_mult: float | None = None,
    stop_anchor: float | None = None,
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
        effective_drawdown_threshold=effective_drawdown_threshold,
        effective_r_multiple=effective_r,
        take_profit_tiers_taken=take_profit_tiers_taken,
        effective_atr_stop_mult=effective_atr_stop_mult,
        stop_anchor=stop_anchor,
    )


def _strength_sell_intent(
    *,
    code: str,
    name: str,
    spot: WatchlistMarketSnapshot,
    pos: Position,
    closes: tuple[float, ...] | None,
    amounts: tuple[float, ...] | None,
    cfg: StrengthSellConfig,
    limit_up: float | None,
    max_single_instruction_amount: float,
    effective_drawdown_threshold: float,
    effective_r: float,
    take_profit_tiers_taken: int | None,
    effective_atr_stop_mult: float | None,
    stop_anchor: float | None,
) -> IntradaySellIntent | None:
    """First matching sell-into-strength trigger for one held code (E3).

    Family priority (evidence strength): LIMIT_BREAK > SURGE_FADE >
    VOLUME_CLIMAX > OVERBOUGHT_BIAS. LIMIT_BREAK has no profit gate (炸板
    is a de-risk signal regardless of P&L); the other three only harvest a
    NET-PROFITABLE position (a loser's bounce-and-fade is not收割 material —
    it would churn against the oversold ADD). Returns ``None`` when nothing
    matches or the 1/3 tranche rounds to zero lots.
    """
    price = spot.price
    prev = spot.prev_close
    high = spot.high
    cost = pos.cost_price
    if price <= 0 or prev <= 0:
        return None
    high_ok = isinstance(high, (int, float)) and math.isfinite(high) and high > 0
    profitable = cost > 0 and price > cost
    ma = moving_average(closes or (), cfg.ma_window)
    avg_amount = None
    if amounts and len(amounts) >= cfg.amount_window:
        tail = [
            a
            for a in amounts[-cfg.amount_window :]
            if isinstance(a, (int, float)) and math.isfinite(a) and a > 0
        ]
        if len(tail) == cfg.amount_window:
            avg_amount = sum(tail) / len(tail)
    spot_amount = (
        spot.amount
        if isinstance(spot.amount, (int, float))
        and math.isfinite(spot.amount)
        and spot.amount > 0
        else None
    )

    kind: IntradayTriggerKind | None = None
    detail = ""
    if (
        limit_up is not None
        and high_ok
        and high >= limit_up - cfg.limit_epsilon
        and price <= limit_up * (1.0 - cfg.break_pullback)
    ):
        kind = IntradayTriggerKind.LIMIT_BREAK
        detail = (
            f"炸板回落: 日内触涨停 {limit_up:.2f} 后回落至 {price:.3f} "
            f"(≥{cfg.break_pullback:.0%}); 减仓 1/3 落袋"
        )
    elif (
        profitable
        and high_ok
        and high / prev - 1.0 >= cfg.surge_min
        and price / high - 1.0 <= -cfg.fade_min
    ):
        kind = IntradayTriggerKind.SURGE_FADE
        detail = (
            f"冲高回落: 日内最高 +{high / prev - 1.0:.1%} 后自高点回落 "
            f"{1.0 - price / high:.1%}; 减仓 1/3 落袋"
        )
    elif (
        profitable
        and avg_amount is not None
        and spot_amount is not None
        and spot_amount >= cfg.climax_mult * avg_amount
        and price / prev - 1.0 < cfg.stall_max
        and ma is not None
        and ma > 0
        and price >= ma * (1.0 + cfg.extension_min)
    ):
        kind = IntradayTriggerKind.VOLUME_CLIMAX
        detail = (
            f"放量滞涨: 成交额 {spot_amount / avg_amount:.1f}× 于5日均额而日内仅 "
            f"{price / prev - 1.0:+.1%}, 价位高于 MA{cfg.ma_window} "
            f"{(price - ma) / ma:.0%}; 减仓 1/3 落袋"
        )
    elif (
        profitable
        and ma is not None
        and ma > 0
        and (price - ma) / ma >= cfg.bias_threshold
    ):
        kind = IntradayTriggerKind.OVERBOUGHT_BIAS
        detail = (
            f"乖离超买: 价格高于 MA{cfg.ma_window} "
            f"{(price - ma) / ma:.0%} (≥{cfg.bias_threshold:.0%}); 减仓 1/3 落袋"
        )
    if kind is None:
        return None

    settled = (pos.available_volume // _LOT) * _LOT
    lots = round(settled * cfg.tranche_fraction / _LOT)
    if lots <= 0:
        return None  # a 1-lot position has no sub-tranche — TP still covers it
    if max_single_instruction_amount > 0:
        cap_lots = int(max_single_instruction_amount // (price * _LOT))
        lots = min(lots, cap_lots)
    sell_vol = min(lots * _LOT, settled)
    if sell_vol <= 0:
        return None
    return IntradaySellIntent(
        code=code,
        name=name,
        available_volume=sell_vol,
        limit_price=price,
        trigger_kind=kind,
        anomaly_reason=detail,
        drawdown_pct=round((price - prev) / prev, 6),
        atr=0.0,
        recent_high=0.0,
        stop_level=0.0,
        effective_drawdown_threshold=effective_drawdown_threshold,
        effective_r_multiple=effective_r,
        take_profit_tiers_taken=take_profit_tiers_taken,
        effective_atr_stop_mult=effective_atr_stop_mult,
        stop_anchor=stop_anchor,
    )


def _stale_exit_intent(
    *,
    code: str,
    name: str,
    spot: WatchlistMarketSnapshot,
    pos: Position,
    entry_closes: tuple[float, ...] | None,
    cfg: StaleExitConfig,
    max_single_instruction_amount: float,
    effective_drawdown_threshold: float,
    effective_r: float,
    take_profit_tiers_taken: int | None,
    effective_atr_stop_mult: float | None,
    stop_anchor: float | None,
) -> IntradaySellIntent | None:
    """Time-stop exit for a position whose momentum edge is spent (E4).

    Holding age = ``len(entry_closes)`` (the episode-sliced closes — one per
    held trading day, deterministic from pinned inputs; a series shorter
    than the calendar count fires LATER, the conservative direction).
    Full settled exit, clamped to the ¥50k single-instruction cap exactly
    like the thesis-break exit (a partial clamp re-fires next day until the
    position drains). ``None`` when any condition fails.
    """
    price = spot.price
    cost = pos.cost_price
    if price <= 0 or cost <= 0 or entry_closes is None:
        return None
    held_days = len(entry_closes)
    if held_days < cfg.stale_days or held_days <= cfg.high_window:
        return None
    if price / cost - 1.0 >= cfg.min_return:
        return None  # it IS going somewhere — not stale
    recent = [c for c in entry_closes[-cfg.high_window :] if c > 0]
    earlier = [c for c in entry_closes[: -cfg.high_window] if c > 0]
    if not recent or not earlier or max(recent) >= max(earlier):
        return None  # a recent post-entry high → momentum not spent
    vol = (pos.available_volume // _LOT) * _LOT
    if vol <= 0:
        return None
    if max_single_instruction_amount > 0:
        cap_lots = int(max_single_instruction_amount // (price * _LOT)) * _LOT
        vol = min(vol, cap_lots)
    if vol <= 0:
        return None
    detail = (
        f"时间止损: 持有 {held_days} 交易日收益 {price / cost - 1.0:+.1%} "
        f"(<{cfg.min_return:.0%}) 且近 {cfg.high_window} 日无入场后新高; "
        f"清仓释放槽位"
    )
    return IntradaySellIntent(
        code=code,
        name=name,
        available_volume=vol,
        limit_price=price,
        trigger_kind=IntradayTriggerKind.STALE_EXIT,
        anomaly_reason=detail,
        drawdown_pct=round((price - spot.prev_close) / spot.prev_close, 6),
        atr=0.0,
        recent_high=0.0,
        stop_level=0.0,
        effective_drawdown_threshold=effective_drawdown_threshold,
        effective_r_multiple=effective_r,
        take_profit_tiers_taken=take_profit_tiers_taken,
        effective_atr_stop_mult=effective_atr_stop_mult,
        stop_anchor=stop_anchor,
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
    long_term_hold_codes: frozenset[str] = frozenset(),
    drawdown_calibration: DrawdownCalibrationConfig | None = None,
    regime: MarketRegime | None = None,
    takeprofit_calibration: TakeProfitCalibrationConfig | None = None,
    takeprofit_regime: MarketRegime | None = None,
    tiered_takeprofit: TieredTakeProfitConfig | None = None,
    take_profit_tiers_taken: Mapping[str, int] | None = None,
    chandelier: ChandelierConfig | None = None,
    entry_closes_by_code: Mapping[str, tuple[float, ...]] | None = None,
    tick_time: datetime | None = None,
    strength: StrengthSellConfig | None = None,
    amounts_by_code: Mapping[str, tuple[float, ...]] | None = None,
    stale: StaleExitConfig | None = None,
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

    ``long_term_hold_codes`` (P0-10-amendment-line2-2026-06-03, intact
    PositionThesis) are conviction holds exempt from the discretionary
    profit-taking: a code in this set skips TAKE_PROFIT and the **soft**
    WEIGHT_TRIM, but is still trimmed at the single-stock **hard cap** (so it can
    ride a winner without forced profit-taking yet never breaches the
    concentration red line). The exemption only touches the else branch, so it
    can NEVER relax a protective stop (ATR / drawdown) or the THESIS_QUANT_BREAK
    exit — those outrank it and fire first. Empty set → v3 outputs bit-for-bit.

    ``closes_by_code`` is the daily close history per bare code (parsed from
    the persisted T-1 frame by ``add_position.parse_held_series``); a code
    without enough daily history cannot fire the ATR trigger or take-profit (R
    needs the ATR); the drawdown trigger needs only the live spot.

    ``takeprofit_calibration`` + ``takeprofit_regime`` (D1-c, P0-7-amendment-
    2026-06-04): regime-conditioned take-profit multiple — BEAR locks gains
    earlier, BULL lets winners run; NEUTRAL equals the static default. The
    regime rides its OWN channel (not the D1-b ``regime`` param) so each
    env-gated feature conditions only its own maths: enabling the take-profit
    tiers must never tighten the D1-a drawdown stop while
    QUANTMIND_LINE2_REGIME_DRAWDOWN_ENABLED is off (and vice versa). A None
    calibration reproduces v6 outputs bit-for-bit (the regime channel alone is
    inert). Only the discretionary TAKE_PROFIT target moves — protective
    stops, the thesis-break exit and the hard cap are untouched.

    ``tiered_takeprofit`` + ``take_profit_tiers_taken`` (D1-d,
    P0-10-amendment-line2-2026-06-04): a price-laddered scale-out — the NEXT
    untaken tier (per the runner's ledger-folded per-episode count) gates the
    take-profit target; an exhausted ladder takes no further profit (the
    residual rides the trailing stop). Composes with the D1-c multiple (a
    BEAR regime shifts the whole ladder earlier). ``None`` reproduces v7
    outputs bit-for-bit (single target, cross-day scale-out semantics).

    ``chandelier`` + ``entry_closes_by_code`` + ``tick_time`` (E1+E2,
    P0-7-amendment-2026-06-04-entry-anchored-chandelier): the trailing stop
    becomes ENTRY-ANCHORED — ``max(cost − 2×ATR, max(close since entry) −
    3×ATR)`` (LeBeau's canonical two-layer structure; the absolute-window
    high that parked stops at cost on fresh positions is gone) — with
    depth-tiered confirmation: a breach deeper than ``deep_band_atr×ATR``
    exits THIS tick, a shallow breach routes only inside the late-session
    confirmation window (the A-share weak-open/strong-close structure made
    morning intraday-touch stops systematically sell the low). A code
    missing from ``entry_closes_by_code`` (no episode data) falls back to
    the v8 window stop — protection never disappears. DRAWDOWN_STOP is
    untouched (the immediate disaster stop, all session). ``None`` config
    reproduces v8 outputs bit-for-bit.

    ``strength`` + ``amounts_by_code`` (E3,
    P0-10-amendment-line2-2026-06-04-sell-into-strength): the
    sell-into-strength family — LIMIT_BREAK / SURGE_FADE / VOLUME_CLIMAX /
    OVERBOUGHT_BIAS, each a 1/3-tranche discretionary sell ranked between
    THESIS_QUANT_BREAK and TAKE_PROFIT — plus the SEALED_LIMIT_HOLD
    suppression: a price sealed AT the (deterministically approximated)
    limit-up suppresses TAKE_PROFIT + the strength family this tick (79.4%
    next-day continuation — ride the board; the hard-cap WEIGHT_TRIM and
    every protective exit stay live). Long-term holds (D2) skip the
    strength family exactly as they skip TAKE_PROFIT. ``None`` config
    reproduces v9 outputs bit-for-bit.

    ``stale`` (E4, P0-10-amendment-line2-2026-06-04-reentry-and-time-stop):
    the STALE_EXIT time stop, ranked after the strength family and before
    TAKE_PROFIT; needs ``entry_closes_by_code`` (the E1 episode slices —
    holding age and post-entry-high test both derive from it). Sealed
    boards and long-term holds are exempt. ``None`` reproduces v10
    outputs bit-for-bit.
    """
    cfg = config or IntradayTriggerConfig()
    names = name_by_code or {}
    pos_by_code = {_bare(p.code): p for p in positions}
    thesis_breaks = thesis_break_by_code or {}
    # D1-b regime conditioning: a BEAR market regime tightens the adaptive
    # drawdown stop (passed through to the per-stock derivation). ``None`` regime
    # (feature off / not supplied) leaves the threshold unconditioned.
    is_bear = regime is MarketRegime.BEAR
    # D1-c: the take-profit multiple in force this tick — one global value (the
    # regime is market-wide, not per-stock). Static config when the calibration
    # is off; recorded on every intent for PIT replay (mirrors dd_thr below).
    eff_r = (
        effective_r_multiple(
            takeprofit_calibration,
            is_bull=takeprofit_regime is MarketRegime.BULL,
            is_bear=takeprofit_regime is MarketRegime.BEAR,
        )
        if takeprofit_calibration is not None
        else cfg.r_multiple
    )

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
        # Per-stock adaptive DRAWDOWN_STOP threshold (P0-7-amendment-2026-06-03):
        # derived from this code's own |daily return| percentile when a
        # calibration is supplied; falls back to the static default when no
        # calibration is wired OR there is not enough clean history. A None
        # calibration reproduces the v4 fixed-threshold behaviour exactly.
        dd_threshold = cfg.drawdown_threshold
        if drawdown_calibration is not None and closes:
            derived = derive_drawdown_threshold(
                closes, drawdown_calibration, is_bear=is_bear
            )
            if derived is not None:
                dd_threshold = derived
        # Carried onto EVERY intent this code produces (not just DRAWDOWN_STOP):
        # a lower-priority SELL firing under a widened adaptive threshold must
        # still record the threshold in effect, else the manifest would imply a
        # drawdown stop should have fired (codex P2 — audit/replay consistency).
        dd_thr = round(dd_threshold, 6)
        # D1-d: the episode's tiers-taken count in force this tick — carried
        # on EVERY intent (PIT: a lower-priority record must reproduce why
        # take-profit was gated at a higher tier). None when the ladder is off.
        tp_taken = (
            (take_profit_tiers_taken or {}).get(code, 0)
            if tiered_takeprofit is not None
            else None
        )
        atr = close_atr(closes, cfg.atr_window) if closes else None
        # The ATR trailing stop self-gates on a COMPLETE recent-high window:
        # a partial window would understate the true recent high and fire the
        # stop early (precision over recall — codex U-C3 P2). The drawdown
        # trigger needs no history (it reads the live quote alone).
        recent_high = (
            max(closes[-cfg.recent_high_window :])
            if closes and len(closes) >= cfg.recent_high_window
            else None
        )
        # E1+E2 — entry-anchored chandelier with depth-tiered confirmation
        # (None config / no episode data → the v8 window stop bit-for-bit).
        anchored = None
        if chandelier is not None and atr is not None and atr > 0:
            entry_closes = (entry_closes_by_code or {}).get(code)
            if entry_closes is not None:
                anchored = derive_entry_anchored_stop(
                    entry_closes,
                    cost=pos.cost_price,
                    atr=atr,
                    config=chandelier,
                )
        eff_mult: float | None = None
        anchor_val: float | None = None
        deep_breach = False
        if anchored is not None:
            eff_mult = (
                chandelier.chandelier_atr_mult  # type: ignore[union-attr]
                if anchored.governing == "chandelier"
                else chandelier.initial_atr_mult  # type: ignore[union-attr]
            )
            anchor_val = anchored.anchor
            breached = price < anchored.stop_level
            deep_breach = price <= (
                anchored.stop_level
                - chandelier.deep_band_atr * atr  # type: ignore[union-attr, operator]
            )
            if tick_time is None:
                minute = None
            else:
                local = (
                    tick_time.replace(tzinfo=_SHANGHAI)
                    if tick_time.tzinfo is None
                    else tick_time.astimezone(_SHANGHAI)
                )
                minute = local.hour * 60 + local.minute
            in_confirm_window = (
                minute is not None
                and chandelier.confirm_start_minute  # type: ignore[union-attr]
                <= minute
                < chandelier.confirm_end_minute  # type: ignore[union-attr]
            )
            # Deep breach → exit NOW (gap/crash protection). Shallow breach →
            # only inside the late-session confirmation window (a morning
            # touch that recovers by 14:30 never sells the low).
            atr_fired = breached and (deep_breach or in_confirm_window)
        else:
            atr_fired = (
                atr is not None
                and atr > 0
                and recent_high is not None
                and price < recent_high - cfg.atr_stop_mult * atr
            )
        drawdown_fired = drawdown <= -dd_threshold

        if atr_fired:
            if anchored is not None:
                stop_level = anchored.stop_level
                label = (
                    "回撤锁盈" if anchored.governing == "chandelier" else "止损"
                )
                mode_label = "深破即时" if deep_breach else "尾盘确认"
                detail = (
                    f"{label}: 实时价 {price:.3f} < 止损线 {stop_level:.3f} "
                    f"(入场锚 {anchored.anchor:.3f} − {eff_mult:g}×ATR "
                    f"{atr:.3f}; {mode_label})"
                )
                # recent_high keeps its v8 meaning (window high; 0.0 when
                # unavailable) on EVERY record — the E1 anchor lives uniformly
                # in stop_anchor so cross-kind provenance never diverges
                # (review P1 finding).
                rh_field = recent_high if recent_high is not None else 0.0
            else:
                stop_level = recent_high - cfg.atr_stop_mult * atr  # type: ignore[operator]
                detail = (
                    f"intraday price {price:.3f} < trailing stop "
                    f"{stop_level:.3f} (recent high {recent_high:.3f} − "
                    f"{cfg.atr_stop_mult:.0f}×ATR {atr:.3f})"
                )
                rh_field = recent_high
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
                    recent_high=round(rh_field, 4),  # type: ignore[arg-type]
                    stop_level=round(stop_level, 4),
                    effective_drawdown_threshold=dd_thr,
                    effective_r_multiple=eff_r,
                    take_profit_tiers_taken=tp_taken,
                    effective_atr_stop_mult=eff_mult,
                    stop_anchor=anchor_val,
                    stop_governing=(
                        anchored.governing if anchored is not None else None
                    ),
                )
            )
        elif drawdown_fired:
            detail = (
                f"intraday drawdown {drawdown:.2%} vs prev_close "
                f"{prev_close:.3f} ≤ -{dd_threshold:.1%}"
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
                    effective_drawdown_threshold=dd_thr,
                    effective_r_multiple=eff_r,
                    take_profit_tiers_taken=tp_taken,
                    effective_atr_stop_mult=eff_mult,
                    stop_anchor=anchor_val,
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
                    effective_drawdown_threshold=dd_thr,
                    effective_r=eff_r,
                    take_profit_tiers_taken=tp_taken,
                    effective_atr_stop_mult=eff_mult,
                    stop_anchor=anchor_val,
                )
                if code in thesis_breaks
                else None
            )
            if thesis_intent is not None:
                intents.append(thesis_intent)
                continue
            is_long_term = code in long_term_hold_codes
            # E3 — sealed-limit hold + the sell-into-strength family
            # (P0-10-amendment-line2-2026-06-04-sell-into-strength). A price
            # sealed at the approximated limit-up rides the board: TP + the
            # strength family are suppressed this tick (protective exits and
            # the hard-cap trim below stay live). Long-term holds (D2) skip
            # the discretionary strength sells exactly like TAKE_PROFIT.
            limit_up = (
                limit_up_price(code, names.get(code, code), prev_close)
                if (strength is not None or stale is not None)
                else None
            )
            _limit_eps = (
                strength.limit_epsilon if strength is not None else 0.005
            )
            at_board = (
                limit_up is not None and price >= limit_up - _limit_eps
            )
            # The sealed-board TP suppression belongs to the STRENGTH gate:
            # enabling only the stale time-stop must not change TAKE_PROFIT
            # behaviour (codex P2). The stale trigger itself still skips a
            # sealed board via ``at_board`` (a board is not a zombie).
            sealed = strength is not None and at_board
            strength_intent = (
                _strength_sell_intent(
                    code=code,
                    name=names.get(code, code),
                    spot=spot,
                    pos=pos,
                    closes=closes,
                    amounts=(amounts_by_code or {}).get(code),
                    cfg=strength,
                    limit_up=limit_up,
                    max_single_instruction_amount=max_single_instruction_amount,
                    effective_drawdown_threshold=dd_thr,
                    effective_r=eff_r,
                    take_profit_tiers_taken=tp_taken,
                    effective_atr_stop_mult=eff_mult,
                    stop_anchor=anchor_val,
                )
                if strength is not None and not at_board and not is_long_term
                else None
            )
            stale_intent = (
                _stale_exit_intent(
                    code=code,
                    name=names.get(code, code),
                    spot=spot,
                    pos=pos,
                    entry_closes=(entry_closes_by_code or {}).get(code),
                    cfg=stale,
                    max_single_instruction_amount=max_single_instruction_amount,
                    effective_drawdown_threshold=dd_thr,
                    effective_r=eff_r,
                    take_profit_tiers_taken=tp_taken,
                    effective_atr_stop_mult=eff_mult,
                    stop_anchor=anchor_val,
                )
                if (
                    stale is not None
                    and strength_intent is None
                    and not at_board
                    and not is_long_term
                )
                else None
            )
            if strength_intent is not None:
                intents.append(strength_intent)
            elif stale_intent is not None:
                intents.append(stale_intent)
            elif account is not None:
                # The lower-priority profit-taking / rebalance triggers (only
                # when an account is supplied). Take-profit outranks weight-trim;
                # at most one fires. A long-term hold (intact thesis) is exempt
                # from take-profit + the soft trim, but the hard-cap trim still
                # bounds it (P0-10-amendment-line2-2026-06-03) — never relaxing
                # the protective stops, which already fired above if applicable.
                tp = (
                    None
                    if (is_long_term or sealed)
                    else _take_profit_intent(
                        code=code,
                        name=names.get(code, code),
                        spot=spot,
                        pos=pos,
                        atr=atr,
                        cfg=cfg,
                        already_taken=take_profit_already_taken,
                        effective_drawdown_threshold=dd_thr,
                        effective_r=eff_r,
                        tier_ladder=(
                            tiered_takeprofit.tiers
                            if tiered_takeprofit is not None
                            else None
                        ),
                        tiers_taken=(take_profit_tiers_taken or {}).get(
                            code, 0
                        ),
                        effective_atr_stop_mult=eff_mult,
                        stop_anchor=anchor_val,
                    )
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
                        effective_drawdown_threshold=dd_thr,
                        effective_r=eff_r,
                        take_profit_tiers_taken=tp_taken,
                        hard_cap_only=is_long_term,
                        max_single_instruction_amount=max_single_instruction_amount,
                        effective_atr_stop_mult=eff_mult,
                        stop_anchor=anchor_val,
                    )
                )
                if tp is not None:
                    intents.append(tp)
                elif trim is not None:
                    intents.append(trim)
    log.info("intraday_sell_intents_evaluated", intents=len(intents))
    return tuple(intents)


def evaluate_reentry_add_intents(
    spots: Mapping[str, WatchlistMarketSnapshot],
    closes_by_code: Mapping[str, tuple[float, ...]],
    positions: tuple[Position, ...],
    account: AccountInfo,
    *,
    yesterday_sales: Mapping[str, Mapping[str, float]],
    config: ReentryConfig,
    tick_time: datetime,
    name_by_code: Mapping[str, str] | None = None,
    thesis_break_codes: frozenset[str] = frozenset(),
    max_single_stock_pct: float = 0.15,
    max_single_instruction_amount: float = 50_000.0,
) -> tuple[AddIntent, ...]:
    """Next-day re-entry BUY after a delivered discretionary sell (E5).

    ``yesterday_sales`` = the PREVIOUS trading day's delivered sales from the
    fired-trigger store (code → {kind, sold_price, sold_volume}); only
    :data:`REENTRY_ELIGIBLE_KINDS` qualify — a protective-stop / thesis /
    stale exit is never bought back (Turtle fail-safe: a falsified trend
    needs a FRESH Line-1 signal, not a reflex top-up). Gates, all
    deterministic: the code is still held (a residual from the partial
    sale — a full exit closed the episode and re-entry belongs to Line-1);
    tick inside the morning window (overnight-discount harvest, dossier
    §3.1); price ≥``discount`` below yesterday's sale; structure intact
    (price > MA). Volume = yesterday's sold volume clamped to single-stock
    headroom + the ¥50k cap. The produced :class:`AddIntent` rides the
    existing ADD pipeline (same-tick SELL suppression, same-day SELL→ADD
    mutex, 5 early-returns, 14-check, human gate).
    """
    names = name_by_code or {}
    pos_by_code = {_bare(p.code): p for p in positions}
    local = (
        tick_time.replace(tzinfo=_SHANGHAI)
        if tick_time.tzinfo is None
        else tick_time.astimezone(_SHANGHAI)
    )
    minute = local.hour * 60 + local.minute
    if not (config.window_start_minute <= minute < config.window_end_minute):
        return ()
    total = float(account.total_assets)
    intents: list[AddIntent] = []
    for code in sorted(yesterday_sales):
        sale = yesterday_sales[code]
        if str(sale.get("kind", "")) not in REENTRY_ELIGIBLE_KINDS:
            continue
        if code in thesis_break_codes:
            continue
        pos = pos_by_code.get(code)
        if pos is None or pos.volume <= 0:
            continue  # full exit → episode closed → Line-1 territory
        spot = spots.get(code)
        if spot is None:
            continue
        price = spot.price
        sold_price = float(sale.get("sold_price", 0.0))
        sold_volume = int(sale.get("sold_volume", 0))
        if price <= 0 or sold_price <= 0 or sold_volume <= 0:
            continue
        if price > sold_price * (1.0 - config.discount):
            continue  # the overnight discount did not materialise
        closes = closes_by_code.get(code)
        ma = moving_average(closes or (), config.ma_window)
        if ma is None or ma <= 0 or price <= ma:
            continue  # structure broken — no reflex top-up
        atr = close_atr(closes or (), 14)
        # Clamp: yesterday's volume → single-stock headroom → ¥50k cap.
        vol = (sold_volume // _LOT) * _LOT
        if total > 0:
            headroom_value = (
                max_single_stock_pct * total - pos.volume * price
            )
            headroom = int(max(0.0, headroom_value) // (price * _LOT)) * _LOT
            vol = min(vol, headroom)
        if max_single_instruction_amount > 0:
            cap = (
                int(max_single_instruction_amount // (price * _LOT)) * _LOT
            )
            vol = min(vol, cap)
        if vol <= 0:
            continue
        stop_price = (
            round(price - 2.0 * atr, 2) if atr is not None and atr > 0 else 0.0
        )
        rationale = (
            f"止盈回补: 昨日 {sold_price:.3f} 卖出 ({sale.get('kind')}),"
            f"今晨 {price:.3f} 低 {1.0 - price / sold_price:.1%} 回补 "
            f"{vol} 股;结构完好 (>MA{config.ma_window})"
        )
        intents.append(
            ReentryAddIntent(
                code=code,
                name=names.get(code, code),
                add_volume=vol,
                limit_price=price,
                atr=round(atr, 4) if atr else 0.0,
                stop_price=stop_price,
                rsi=0.0,  # re-entry is discount-vs-sale driven, not RSI-gated
                rationale=rationale,
                sold_price=sold_price,
                reentry_discount=config.discount,
            )
        )
    if intents:
        log.info("reentry_add_intents_evaluated", intents=len(intents))
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
    headroom_value = config.max_single_stock_pct * account.total_assets - position_value
    headroom_shares = int(max(0.0, headroom_value) // (live_price * _LOT)) * _LOT
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
    "REENTRY_ELIGIBLE_KINDS",
    "ReentryAddIntent",
    "ReentryConfig",
    "StaleExitConfig",
    "StrengthSellConfig",
    "evaluate_reentry_add_intents",
    "limit_up_price",
    "evaluate_intraday_add_intents",
    "evaluate_intraday_sell_intents",
    "filter_fresh_quotes",
    "make_intraday_sell_context",
    "serialize_intraday_quotes",
]
