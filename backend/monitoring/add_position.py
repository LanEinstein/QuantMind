"""Line-2 add-position (补仓) detection — Van Tharp fixed-fractional + ATR.

When a held position dips into an oversold-but-not-broken state in a
non-bear regime, the second line may propose a disciplined ADD (a BUY onto
the existing position). This is the pure, deterministic decision + sizing
layer (R0 §8 — zero LLM); the single-construction-point builder
(``assemble_monitoring_plan``, side=BUY) runs the freeze early-returns +
RiskEngine 14-check, and ``render_add_position`` emits the decision-chat
message.

An ADD requires **all four** conditions (precision over recall):

1. **oversold** — Wilder RSI below the threshold;
2. **volume stabilized** — recent turnover has not collapsed vs the prior
   window (a falling knife on drying volume is not a dip to buy);
3. **no structural breakdown** — the price has not broken far below the long
   moving average (a confirmed trend break is not a dip);
4. **position headroom** — the post-add position stays under the single-stock
   cap, so there is room to add.

Hard bans (R0 §6 / dossier):

* **martingale forbidden** — size is ALWAYS the Van Tharp fixed-fractional
  amount (a constant fraction of equity at risk per add), never scaled up to
  "average down"; additionally an ADD onto a deeply-underwater position
  (drawdown beyond ``max_add_drawdown_pct`` vs cost) is rejected outright as
  averaging-down into a loser.
* **bear regime → no add** — a ``MarketRegime.BEAR`` classification blocks
  every ADD.

ATR sizing uses a close-based ATR proxy (the K market-frame carries no
high/low; Phase T upgrades to true OHLC ATR). Module red line
(``backend/monitoring/CLAUDE.md``): no ``backend.{llm,agents,mirofish}``
import — the ADD direction is a deterministic observation.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog

# backend.{broker,risk} are legitimate Line-2 dependencies (positions /
# RiskEngine types). The per-line noqa keeps the global TID251 ban ACTIVE for
# backend.{llm,agents,mirofish} — this module's own red line.
from backend.broker.models import AccountInfo, Position  # noqa: TID251
from backend.data.data_quality import DataQualityState  # noqa: TID251
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.models.instruction import DataSnapshot, InstructionSide
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

log = structlog.get_logger(component="monitoring.add_position")

_LOT = 100
_EXPECTED_HEADER: tuple[str, ...] = (
    "ts_code", "name", "listed_trading_days", "closes", "amounts",
)


class MarketRegime(StrEnum):
    """Coarse market regime — a BEAR classification blocks every ADD."""

    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"


class AddRejectReason(StrEnum):
    """Why an ADD was not proposed for a held code (deterministic)."""

    NOT_HELD = "not_held"
    NO_SERIES = "no_series"
    INSUFFICIENT_HISTORY = "insufficient_history"
    BEAR_REGIME = "bear_regime"
    MARTINGALE = "martingale"
    NOT_OVERSOLD = "not_oversold"
    VOLUME_NOT_STABILIZED = "volume_not_stabilized"
    STRUCTURAL_BREAKDOWN = "structural_breakdown"
    NO_HEADROOM = "no_headroom"


@dataclass(frozen=True)
class AddConfig:
    """Locked add-position thresholds (runtime-immutable)."""

    rsi_window: int = 14
    rsi_oversold: float = 35.0
    atr_window: int = 14
    atr_stop_mult: float = 2.0
    risk_fraction: float = 0.01  # Van Tharp fixed fraction of equity at risk
    volume_window: int = 5
    volume_stabilize_ratio: float = 0.7  # recent vol >= 0.7 × prior vol
    ma_long_window: int = 20
    breakdown_tolerance: float = 0.10  # >10% below the long MA = broken
    max_single_stock_pct: float = 0.15  # headroom cap (P0-7 single-stock)
    max_add_drawdown_pct: float = 0.10  # deeper loss → averaging-down = martingale

    @property
    def min_bars(self) -> int:
        """Fewest closes to evaluate every gate (RSI / ATR / long-MA + prior vol)."""
        return max(
            self.rsi_window + 1,
            self.atr_window + 1,
            self.ma_long_window,
            self.volume_window * 2,
        )


@dataclass(frozen=True)
class AddConditions:
    """The four-condition checklist outcome (all must hold)."""

    oversold: bool
    volume_stabilized: bool
    no_structural_breakdown: bool
    has_headroom: bool

    @property
    def all_met(self) -> bool:
        return (
            self.oversold
            and self.volume_stabilized
            and self.no_structural_breakdown
            and self.has_headroom
        )


@dataclass(frozen=True)
class AddIntent:
    """A deterministic decision to ADD to a held position (pre-RiskEngine)."""

    code: str
    name: str
    add_volume: int  # lot-aligned, Van Tharp fixed-fraction, headroom-capped
    limit_price: float
    atr: float
    stop_price: float
    rsi: float
    rationale: str


@dataclass(frozen=True)
class AddRejection:
    """A held code that did NOT qualify for an ADD + the first-match reason."""

    code: str
    reason: AddRejectReason
    detail: str = ""


@dataclass(frozen=True)
class AddEvaluation:
    """Deterministic add-scan output."""

    intents: tuple[AddIntent, ...]
    rejections: tuple[AddRejection, ...]


# ---------------------------------------------------------------------------
# Pure indicator helpers (oldest → newest; None = cannot evaluate)
# ---------------------------------------------------------------------------


def _returns(closes: tuple[float, ...]) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:], strict=False):
        out.append((cur / prev - 1.0) if prev > 0 else 0.0)
    return out


def rsi(closes: tuple[float, ...], window: int) -> float | None:
    """Wilder-style RSI scaled 0-100; ``None`` if too short."""
    rets = _returns(closes)
    if len(rets) < window:
        return None
    win = rets[-window:]
    gains = sum(r for r in win if r > 0) / window
    losses = sum(-r for r in win if r < 0) / window
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def close_atr(closes: tuple[float, ...], window: int) -> float | None:
    """Close-based ATR proxy = mean absolute daily price change over ``window``.

    The K market-frame has no high/low, so true ATR is not computable for the
    MVP; the mean absolute close-to-close move is a stable, monotone proxy for
    realised range (Phase T upgrades to OHLC ATR). ``None`` if too short.
    """
    if len(closes) < window + 1:
        return None
    diffs = [
        abs(closes[i] - closes[i - 1])
        for i in range(len(closes) - window, len(closes))
    ]
    return statistics.fmean(diffs)


def moving_average(values: tuple[float, ...], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return statistics.fmean(values[-window:])


def classify_regime(
    index_closes: tuple[float, ...], *, window: int = 20
) -> MarketRegime:
    """Classify the market regime from a benchmark index close series.

    MA-based: BULL when the last close is above the ``window`` MA and the MA is
    rising; BEAR when below a falling MA; NEUTRAL otherwise (incl. insufficient
    history — fail-safe to NEUTRAL never blocks on missing data, but also never
    *enables* an add on its own; the four conditions still gate).
    """
    ma_now = moving_average(index_closes, window)
    ma_prev = (
        moving_average(index_closes[:-1], window)
        if len(index_closes) > window
        else None
    )
    if ma_now is None or ma_prev is None:
        return MarketRegime.NEUTRAL
    last = index_closes[-1]
    # Strict MA slope so a flat / choppy market is NEUTRAL (a tie on the MA
    # must not be read as a trend just because the last bar ticked up).
    if last > ma_now and ma_now > ma_prev:
        return MarketRegime.BULL
    if last < ma_now and ma_now < ma_prev:
        return MarketRegime.BEAR
    return MarketRegime.NEUTRAL


def vanthorp_size(
    *, equity: float, atr: float, price: float, config: AddConfig
) -> int:
    """Van Tharp fixed-fractional share count, lot-aligned (anti-martingale).

    ``shares = (risk_fraction × equity) / (atr_stop_mult × atr)`` floored to a
    whole lot. Size is governed by a CONSTANT fraction of equity at risk and
    the ATR stop distance — never by how far the position is down (that would
    be martingale). Returns 0 when the inputs are degenerate.
    """
    per_share_risk = config.atr_stop_mult * atr
    if (
        not math.isfinite(equity) or equity <= 0
        or not math.isfinite(per_share_risk) or per_share_risk <= 0
        or not math.isfinite(price) or price <= 0
    ):
        return 0
    raw_shares = (config.risk_fraction * equity) / per_share_risk
    return int(raw_shares // _LOT) * _LOT


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def assess_conditions(
    closes: tuple[float, ...],
    amounts: tuple[float, ...],
    *,
    position_value: float,
    total_assets: float,
    config: AddConfig,
) -> AddConditions:
    """Evaluate the four ADD conditions (pure)."""
    rsi_val = rsi(closes, config.rsi_window)
    oversold = rsi_val is not None and rsi_val < config.rsi_oversold

    w = config.volume_window
    volume_stabilized = False
    if len(amounts) >= 2 * w:
        recent = statistics.fmean(amounts[-w:])
        prior = statistics.fmean(amounts[-2 * w:-w])
        volume_stabilized = (
            prior > 0 and recent >= config.volume_stabilize_ratio * prior
        )

    ma_long = moving_average(closes, config.ma_long_window)
    no_breakdown = (
        ma_long is not None
        and ma_long > 0
        and closes[-1] >= ma_long * (1.0 - config.breakdown_tolerance)
    )

    has_headroom = (
        total_assets > 0
        and position_value < config.max_single_stock_pct * total_assets
    )
    return AddConditions(
        oversold=oversold,
        volume_stabilized=volume_stabilized,
        no_structural_breakdown=no_breakdown,
        has_headroom=has_headroom,
    )


def _evaluate_one(
    code: str,
    name: str,
    closes: tuple[float, ...],
    amounts: tuple[float, ...],
    position: Position,
    account: AccountInfo,
    regime: MarketRegime,
    config: AddConfig,
) -> AddIntent | AddRejection:
    """Decide ADD / reject for one held code (first-match reason)."""
    if len(closes) < config.min_bars:
        return AddRejection(code, AddRejectReason.INSUFFICIENT_HISTORY)
    if regime is MarketRegime.BEAR:
        return AddRejection(
            code, AddRejectReason.BEAR_REGIME, "bear regime forbids add"
        )

    last = closes[-1]
    if last <= 0 or position.cost_price <= 0:
        return AddRejection(code, AddRejectReason.NO_SERIES, "no price / cost")

    # Anti-martingale: never add to a deeply-underwater position (averaging
    # down into a loser). Drawdown uses cost_price (the broker mirror MTM may
    # be zeroed) so the guard is reliable.
    drawdown = (last - position.cost_price) / position.cost_price
    if drawdown < -config.max_add_drawdown_pct:
        return AddRejection(
            code, AddRejectReason.MARTINGALE,
            f"drawdown {drawdown:.1%} beyond -{config.max_add_drawdown_pct:.0%}",
        )

    position_value = position.volume * last
    cond = assess_conditions(
        closes, amounts,
        position_value=position_value, total_assets=account.total_assets,
        config=config,
    )
    if not cond.oversold:
        return AddRejection(code, AddRejectReason.NOT_OVERSOLD)
    if not cond.volume_stabilized:
        return AddRejection(code, AddRejectReason.VOLUME_NOT_STABILIZED)
    if not cond.no_structural_breakdown:
        return AddRejection(code, AddRejectReason.STRUCTURAL_BREAKDOWN)
    if not cond.has_headroom:
        return AddRejection(code, AddRejectReason.NO_HEADROOM)

    atr = close_atr(closes, config.atr_window)
    if atr is None or atr <= 0:
        return AddRejection(code, AddRejectReason.INSUFFICIENT_HISTORY, "no ATR")

    vt_shares = vanthorp_size(
        equity=account.total_assets, atr=atr, price=last, config=config
    )
    # Cap by the single-stock headroom so the post-add position stays under
    # the cap (RiskEngine check 5 re-validates independently downstream).
    headroom_value = config.max_single_stock_pct * account.total_assets - position_value
    headroom_shares = int(max(0.0, headroom_value) // (last * _LOT)) * _LOT
    add_shares = min(vt_shares, headroom_shares)
    if add_shares <= 0:
        return AddRejection(
            code, AddRejectReason.NO_HEADROOM,
            f"van-tharp {vt_shares} / headroom {headroom_shares} → 0 lots",
        )

    stop_price = round(last - config.atr_stop_mult * atr, 2)
    rsi_val = rsi(closes, config.rsi_window) or 0.0
    rationale = (
        f"补仓四条件齐: RSI {rsi_val:.0f}<{config.rsi_oversold:.0f} 超卖 + 量能企稳 + "
        f"无结构性破位 + 仓位余量; Van Tharp 固定分数 {config.risk_fraction:.0%} 风险, "
        f"ATR {atr:.3f} × {config.atr_stop_mult:.0f} 移动止损 @ {stop_price}"
    )
    return AddIntent(
        code=code, name=name, add_volume=add_shares, limit_price=last,
        atr=round(atr, 4), stop_price=stop_price, rsi=round(rsi_val, 2),
        rationale=rationale,
    )


def evaluate_add_intents(
    series_by_code: dict[str, tuple[tuple[float, ...], tuple[float, ...]]],
    positions: tuple[Position, ...],
    account: AccountInfo,
    *,
    regime: MarketRegime,
    name_by_code: dict[str, str] | None = None,
    config: AddConfig | None = None,
) -> AddEvaluation:
    """Evaluate every held code for a disciplined ADD (deterministic).

    ``series_by_code`` maps a held 6-digit code to its ``(closes, amounts)``
    series (oldest → newest), parsed from the PIT snapshot by
    :func:`parse_held_series`. Output is ordered by code for replayable results.
    """
    cfg = config or AddConfig()
    names = name_by_code or {}
    pos_by_code = {p.code: p for p in positions}

    intents: list[AddIntent] = []
    rejections: list[AddRejection] = []
    for code in sorted(pos_by_code):
        series = series_by_code.get(code)
        if series is None:
            rejections.append(AddRejection(code, AddRejectReason.NO_SERIES))
            continue
        closes, amounts = series
        outcome = _evaluate_one(
            code, names.get(code, code), closes, amounts,
            pos_by_code[code], account, regime, cfg,
        )
        if isinstance(outcome, AddIntent):
            intents.append(outcome)
        else:
            rejections.append(outcome)
    log.info(
        "add_intents_evaluated",
        held=len(pos_by_code), intents=len(intents),
        rejections=len(rejections), regime=regime.value,
    )
    return AddEvaluation(intents=tuple(intents), rejections=tuple(rejections))


def parse_held_series(
    snapshot: MarketDataSnapshot, held_codes: list[str]
) -> dict[str, tuple[tuple[float, ...], tuple[float, ...]]]:
    """Parse the held rows' ``(closes, amounts)`` from the K CSV market-frame.

    Same canonical frame the anomaly detector + screener consume. A held code
    appearing more than once is dropped (ambiguous, fail-closed).
    """
    if snapshot.encoding != "csv":
        raise ValueError(f"add-scan requires csv snapshot, got {snapshot.encoding!r}")
    held = {c.split(".")[0].strip() for c in held_codes}
    text = snapshot.raw_payload.decode("utf-8")
    lines = text.splitlines()
    if not lines or tuple(h.strip() for h in lines[0].split(",")) != _EXPECTED_HEADER:
        raise ValueError("add-scan: unexpected/empty CSV header")
    raw_counts: dict[str, int] = {}
    parsed: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(",")
        # Count held-code occurrences from the FIRST field BEFORE any
        # structural filter (column count / float parse). Otherwise a valid +
        # malformed duplicate of a held code would drop the malformed copy
        # uncounted and return the valid one as unique, violating the
        # fail-closed duplicate contract (codex N-003 P2; mirrors
        # anomaly._parse).
        code = parts[0].split(".")[0].strip()
        if code in held:
            raw_counts[code] = raw_counts.get(code, 0) + 1
        if len(parts) != 5 or code not in held:
            continue
        closes = _parse_floats(parts[3].strip())
        amounts = _parse_floats(parts[4].strip())
        if closes is None or amounts is None:
            continue
        parsed[code] = (closes, amounts)
    return {c: v for c, v in parsed.items() if raw_counts.get(c, 0) == 1}


def _parse_floats(raw: str) -> tuple[float, ...] | None:
    if raw == "":
        return ()
    out: list[float] = []
    for tok in raw.split("|"):
        try:
            value = float(tok)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        out.append(value)
    return tuple(out)


def make_add_context(
    intent: AddIntent,
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
    quote_source: str = "marketdata_snapshot",
    analysis_record_id: str = "",
    risk_validation_id: str = "",
) -> MonitoringAssemblyContext:
    """Build the deterministic ADD (BUY) :class:`MonitoringAssemblyContext`.

    ``signal_id`` must carry the ``LINE2-MON-`` prefix. Per-plan correlation
    handles embed code+seq (a multi-code add-scan shares the scan signal_id).
    The BUY path runs ALL five early-returns (incl. watchlist — an ADD must
    respect the entry universe) + RiskEngine 14-check downstream.
    """
    if not signal_id.startswith(MONITORING_SIGNAL_PREFIX):
        raise ValueError(
            f"add signal_id {signal_id!r} must start with {MONITORING_SIGNAL_PREFIX!r}"
        )
    evidence_id = f"MARKET-{intent.code}-add"
    invalidation = (
        f"Line-2 deterministic add (Van Tharp {AddConfig().risk_fraction:.0%} + "
        f"ATR stop @ {intent.stop_price}); abort if structure breaks before fill."
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
        side=InstructionSide.BUY,
        now=now,
        open_tickets=tuple(open_tickets),
        circuit_breaker=circuit_breaker,
        data_quality=data_quality,
        watchlist_policy=watchlist_policy,
        watchlist_signal=watchlist_signal,
        risk_engine=risk_engine,
        account=account,
        positions=positions,
        prev_close=prev_close,
        daily_state=daily_state,
        stock_meta=stock_meta,
        proposed_volume=intent.add_volume,
        proposed_limit_price=intent.limit_price,
        seq=seq,
        signal_id=signal_id,
        analysis_record_id=(
            analysis_record_id or f"add:{intent.code}:{seq}:{signal_id}"[:64]
        ),
        risk_validation_id=(
            risk_validation_id or f"rv:{intent.code}:{seq}:{signal_id}"[:64]
        ),
        evidence_ids=(evidence_id,),
        data_snapshot=data_snapshot,
        invalidation_summary=invalidation,
    )


__all__ = [
    "AddConditions",
    "AddConfig",
    "AddEvaluation",
    "AddIntent",
    "AddRejectReason",
    "AddRejection",
    "MarketRegime",
    "assess_conditions",
    "classify_regime",
    "close_atr",
    "evaluate_add_intents",
    "make_add_context",
    "moving_average",
    "parse_held_series",
    "rsi",
    "vanthorp_size",
]
