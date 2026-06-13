"""Line-2 monitoring SELL signal — anomaly → deterministic exit (Phase N-002).

When :mod:`backend.monitoring.anomaly` flags an **adverse** move on a held
position, the second line proposes a SELL to exit. This module is the pure,
deterministic decision + sizing layer (R0 §8 — zero LLM): it picks which held
positions to exit, sizes the order from the **settled** ``available_volume``
(T+1; today's buys cannot be sold — ``mock_broker.py``), and builds the
:class:`MonitoringAssemblyContext` the single-construction-point builder
consumes (``instruction_plan_builder.assemble_monitoring_plan``,
P0-10-amendment-2026-05-25). The builder runs the freeze early-returns +
RiskEngine 14-check; the renderer (``render_monitoring_sell``) turns the
resulting VALIDATED plan into the decision-chat message.

Trigger policy (precision over recall — alert-fatigue red line): a SELL fires
only on a **DOWN** price z-score / EWMA deviation / Bollinger breakout — a
sharp adverse move or a support break. A volume z-score alone is *not* a sell
trigger (a volume spike is ambiguous without price context); it stays an
informational signal.

Module red line (``backend/monitoring/CLAUDE.md``): no
``backend.{llm,agents,mirofish}`` import. The SELL direction is a deterministic
observation; the LLM never proposes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import structlog

# backend.{broker,data,risk} are legitimate Line-2 dependencies (positions /
# data-quality / RiskEngine types). The per-line noqa keeps the global TID251
# ban ACTIVE for backend.{llm,agents,mirofish} — this module's own red line
# (backend/monitoring/CLAUDE.md) — so any such import would still fail ruff.
from backend.broker.models import AccountInfo, Position  # noqa: TID251
from backend.data.data_quality import DataQualityState  # noqa: TID251
from backend.models.instruction import DataSnapshot, InstructionSide
from backend.monitoring.anomaly import (
    AnomalyDirection,
    AnomalyKind,
    AnomalyScanResult,
    AnomalySignal,
)
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

log = structlog.get_logger(component="monitoring.sell_signal")

_LOT = 100

# DOWN-direction kinds that warrant a risk exit. Volume z-score is excluded
# (ambiguous without price context) — precision over recall. The T-003
# full-stack kinds (IsolationForest multivariate outlier + ruptures
# change-point) are DOWN-gated risk-exit triggers too; they only fire when the
# env-gated stack is enabled (P0-10-amendment-line2-2026-06-13).
SELL_TRIGGER_KINDS: frozenset[AnomalyKind] = frozenset(
    {
        AnomalyKind.PRICE_ZSCORE,
        AnomalyKind.EWMA_DEVIATION,
        AnomalyKind.BOLLINGER_BREAKOUT,
        AnomalyKind.ISOLATION_FOREST,
        AnomalyKind.CHANGEPOINT,
    }
)


@dataclass(frozen=True)
class SellIntent:
    """A deterministic decision to exit a held position (pre-RiskEngine)."""

    code: str
    name: str
    available_volume: int  # lot-aligned, T+1 settled
    limit_price: float
    anomaly_reason: str  # human-readable strongest-trigger detail
    trigger_kind: AnomalyKind


def _bare(code: str) -> str:
    """Strip a ``.SH`` / ``.SZ`` suffix → bare 6-digit code."""
    return code.split(".")[0].strip()


def normalize_position_codes(
    positions: tuple[Position, ...],
) -> tuple[Position, ...]:
    """Return ``positions`` with every code reduced to its bare 6-digit form.

    The anomaly detector + the deterministic intents use bare codes, but
    ``Position.code`` is an unconstrained ``str``; a suffixed holding (e.g.
    ``510300.SH``) would otherwise NOT exact-match the order code inside
    RiskEngine (``_check_fund_sufficiency`` / ``_check_position_limit``) and a
    valid SELL exit would be rejected as "No position" (codex N-005). Normalising
    the tuple BEFORE it flows into the builder keeps the intent / order /
    positions / stock_meta code identity aligned end-to-end. A position whose
    code is already bare is returned unchanged.
    """
    out: list[Position] = []
    for p in positions:
        bare = _bare(p.code)
        out.append(p if bare == p.code else p.model_copy(update={"code": bare}))
    return tuple(out)


def is_sell_trigger(signal: AnomalySignal) -> bool:
    """True if an anomaly is an adverse (DOWN) risk-exit trigger."""
    return (
        signal.direction is AnomalyDirection.DOWN
        and signal.kind in SELL_TRIGGER_KINDS
    )


def evaluate_sell_intents(
    scan_result: AnomalyScanResult,
    positions: tuple[Position, ...],
    *,
    name_by_code: dict[str, str] | None = None,
) -> tuple[SellIntent, ...]:
    """Pick held positions to exit from the scan; size from available_volume.

    Deterministic: for each held code with ≥1 sell-trigger signal we keep the
    **strongest** (highest score) trigger, size the order to the lot-aligned
    settled ``available_volume``, and skip codes with nothing sellable today
    (``available_volume <= 0`` under T+1). Output is ordered by code for
    stable, replayable results.
    """
    names = name_by_code or {}
    # Normalise the position code suffix (.SH/.SZ) so the lookup matches the
    # anomaly detector's bare-6-digit codes (the downstream positions tuple is
    # normalised the same way in make_sell_context — codex N-005).
    pos_by_code = {_bare(p.code): p for p in positions}

    strongest: dict[str, AnomalySignal] = {}
    for sig in scan_result.signals:
        if not is_sell_trigger(sig):
            continue
        cur = strongest.get(sig.code)
        if cur is None or sig.score > cur.score:
            strongest[sig.code] = sig

    intents: list[SellIntent] = []
    for code in sorted(strongest):
        sig = strongest[code]
        pos = pos_by_code.get(code)
        if pos is None or pos.available_volume <= 0:
            # Nothing settled to sell (all bought today → T+1) — no exit order.
            continue
        vol = (pos.available_volume // _LOT) * _LOT
        if vol <= 0:
            continue
        intents.append(
            SellIntent(
                code=code,
                name=names.get(code, code),
                available_volume=vol,
                limit_price=sig.last_price,
                anomaly_reason=sig.detail,
                trigger_kind=sig.kind,
            )
        )
    log.info(
        "sell_intents_evaluated",
        triggers=len(strongest),
        intents=len(intents),
    )
    return tuple(intents)


def make_sell_context(
    intent: SellIntent,
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
    """Build the deterministic SELL :class:`MonitoringAssemblyContext`.

    ``signal_id`` must already carry the ``LINE2-MON-`` prefix (typically the
    anomaly scan's ``manifest.signal_id`` so the plan ties back to the consumed
    PIT rows for replay). The ``MARKET-`` evidence id references the anomaly
    trigger; the DataSnapshot's ``snapshot_at`` is the K snapshot fetch time
    (strictly before ``now``, enforced by the InstructionPlan validator).
    """
    if not signal_id.startswith(MONITORING_SIGNAL_PREFIX):
        raise ValueError(
            f"sell signal_id {signal_id!r} must start with "
            f"{MONITORING_SIGNAL_PREFIX!r}"
        )
    evidence_id = f"MARKET-{intent.code}-{intent.trigger_kind.value}"
    invalidation = (
        f"Line-2 deterministic monitoring SELL ({intent.trigger_kind.value}); "
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
        # Per-plan correlation handles. One anomaly scan can fan out to several
        # held codes that SHARE the scan's signal_id (the PIT link to the
        # consumed-row manifest), so analysis_record_id / risk_validation_id
        # MUST embed the per-plan (code, seq) or ledger/risk lookups by those
        # fields would collide across plans (codex N-002 P2). code+seq lead so
        # they survive the 64-char truncation. Callers pass a distinct seq per
        # intent (also required for instruction_id uniqueness).
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


__all__ = [
    "SELL_TRIGGER_KINDS",
    "SellIntent",
    "evaluate_sell_intents",
    "is_sell_trigger",
    "make_sell_context",
    "normalize_position_codes",
]
