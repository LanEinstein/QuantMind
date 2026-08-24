"""M3-A prediction registry settlement engine.

A prediction is settled only against values that were observable on its
evaluation date. Mechanical settlements (breadth / median / volume) use
the fixed contracts documented below; everything else is settled by the
analyst with an explicit rationale.

Contracts frozen with this module:
- BREADTH: hit when the day's advance/decline majority matches the claim.
- MEDIAN: hit when the median pct_chg sign matches the claim.
- BREADTH_MEDIAN: hit only when both breadth and median match; any
  sub-verdict that cannot be decided leaves the whole claim unsettled.
- VOLUME_DELTA: hit when total amount moves in the claimed direction;
  equal amounts (within float tolerance) are a tie.
- FLAT on BREADTH: hit when |advance - decline| / traded_rows <= 0.10.

Anti-lookahead: a window day whose close (15:00 Asia/Shanghai) was
already known at publication can never be a prediction target; such
records raise at settlement. Multi-day windows are not mechanically
settled (no aggregation contract yet) and stay unsettled.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from backend.data.historical_ingest.job import VENDOR
from backend.data.historical_ingest.serialization import parse_csv_bytes
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.market import load_trade_dates
from scripts.yeren_research.schema import (
    MarketSettlement,
    PredictionDirection,
    PredictionRecord,
    PredictionVerdict,
    SettlementKind,
)

FLAT_BREADTH_RATIO = 0.10
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_AUTO_KINDS = frozenset(
    {
        SettlementKind.BREADTH,
        SettlementKind.MEDIAN,
        SettlementKind.BREADTH_MEDIAN,
        SettlementKind.VOLUME_DELTA,
    }
)
_MANUAL_KINDS = frozenset(
    {
        SettlementKind.EVENT_FACT,
        SettlementKind.SECURITY_CLOSE,
        SettlementKind.NOT_SETTLEABLE,
    }
)


def market_observables(pit_root: Path, trade_date: str) -> dict[str, Any] | None:
    """Read the daily snapshot for ``trade_date`` and compute observables."""
    return _observables(SnapshotStore(pit_root), trade_date)


def _observables(store: SnapshotStore, trade_date: str) -> dict[str, Any] | None:
    snapshot = store.latest(vendor=VENDOR, endpoint="daily", trade_date=trade_date)
    if snapshot is None:
        return None
    frame = parse_csv_bytes(snapshot.raw_payload)
    if "pct_chg" not in frame.columns:
        return {"trade_date": trade_date, "note": "daily rows lack pct_chg"}
    changes = pd.to_numeric(frame["pct_chg"], errors="coerce").dropna()
    if changes.empty:
        return {
            "trade_date": trade_date,
            "note": "daily rows have no numeric pct_chg on this date",
        }
    observables: dict[str, Any] = {
        "trade_date": trade_date,
        "row_count": len(frame),
        "advance": int((changes > 0).sum()),
        "decline": int((changes < 0).sum()),
        "unchanged": int((changes == 0).sum()),
        "median_pct": float(changes.median()),
    }
    if "amount" in frame.columns:
        amounts = pd.to_numeric(frame["amount"], errors="coerce")
        observables["amount_thousand_yuan"] = float(amounts.sum(skipna=True))
    return observables


def _prev_observables(
    store: SnapshotStore, dates: tuple[str, ...], trade_date: str
) -> dict[str, Any] | None:
    before = [d for d in dates if d < trade_date]
    if not before:
        return None
    previous = _observables(store, before[-1])
    if previous is None:
        return None
    return {
        "prev_trade_date": before[-1],
        "prev_amount_thousand_yuan": previous.get("amount_thousand_yuan"),
    }


def _close_available_at(trade_date: str) -> datetime:
    day = datetime.strptime(trade_date, "%Y%m%d")
    return day.replace(hour=15, minute=0, second=0, tzinfo=_SHANGHAI)


def settle_with_observables(
    record: PredictionRecord, observables: dict[str, Any]
) -> PredictionRecord:
    """Return a new record with settlement attached and, when mechanical and
    unconditional, the verdict."""
    settlement = MarketSettlement(**observables)
    if record.branch_trigger is not None or record.settle_kind not in _AUTO_KINDS:
        # conditional claims and manual kinds never auto-verdict: the trigger
        # or the official reading is judged by the analyst. Keep any rationale
        # already recorded on the claim.
        return record.model_copy(update={"settlement": settlement})
    verdict = _mechanical_verdict(record, settlement)
    rationale = _verdict_rationale(record, settlement, verdict)
    return record.model_copy(
        update={
            "settlement": settlement,
            "verdict": verdict,
            "verdict_rationale": rationale,
        }
    )


def settle_market(record: PredictionRecord, pit_root: Path) -> PredictionRecord:
    """Settle one record against the archived daily data under ``pit_root``."""
    if record.settle_kind in _MANUAL_KINDS:
        return record
    store = SnapshotStore(pit_root)
    return _settle_with_store(record, store, load_trade_dates(pit_root))


def settle_records(
    records: Iterable[PredictionRecord], pit_root: Path
) -> tuple[PredictionRecord, ...]:
    """Settle many records with one store and one trade calendar load."""
    items = tuple(records)
    if all(record.settle_kind in _MANUAL_KINDS for record in items):
        return items
    store = SnapshotStore(pit_root)
    dates = load_trade_dates(pit_root)
    return tuple(_settle_with_store(record, store, dates) for record in items)


def _settle_with_store(
    record: PredictionRecord,
    store: SnapshotStore,
    dates: tuple[str, ...],
) -> PredictionRecord:
    if record.settle_kind in _MANUAL_KINDS:
        return record
    if record.window_start != record.window_end:
        return record.model_copy(
            update={
                "verdict": PredictionVerdict.UNSETTLED,
                "verdict_rationale": (
                    f"multi-day window {record.window_start}.."
                    f"{record.window_end} has no aggregation contract"
                ),
            }
        )
    close = _close_available_at(record.window_start)
    if record.published_at >= close:
        raise ValueError(
            f"lookahead window for {record.prediction_id}: "
            f"{record.window_start} close {close.isoformat()} precedes "
            f"publication {record.published_at.isoformat()}"
        )
    observables = _observables(store, record.window_start)
    if observables is None:
        return record.model_copy(
            update={
                "verdict": PredictionVerdict.BEYOND_COVERAGE,
                "verdict_rationale": (
                    f"no daily snapshot archived for {record.window_start}"
                ),
            }
        )
    if record.settle_kind == SettlementKind.VOLUME_DELTA:
        previous = _prev_observables(store, dates, record.window_start)
        if previous is None:
            return record.model_copy(
                update={
                    "verdict": PredictionVerdict.UNSETTLED,
                    "settlement": MarketSettlement(**observables),
                    "verdict_rationale": (
                        f"no archived daily for the previous trade date of "
                        f"{record.window_start}"
                    ),
                }
            )
        observables.update(previous)
    return settle_with_observables(record, observables)


def _mechanical_verdict(
    record: PredictionRecord, settlement: MarketSettlement
) -> PredictionVerdict:
    direction = record.direction
    if direction == PredictionDirection.OTHER:
        return PredictionVerdict.UNSETTLED
    if record.settle_kind == SettlementKind.BREADTH:
        return _breadth_verdict(direction, settlement)
    if record.settle_kind == SettlementKind.MEDIAN:
        return _median_verdict(direction, settlement)
    if record.settle_kind == SettlementKind.BREADTH_MEDIAN:
        breadth = _breadth_verdict(direction, settlement)
        median = _median_verdict(direction, settlement)
        if PredictionVerdict.UNSETTLED in (breadth, median):
            return PredictionVerdict.UNSETTLED
        if breadth == PredictionVerdict.HIT and median == PredictionVerdict.HIT:
            return PredictionVerdict.HIT
        return PredictionVerdict.MISS
    if record.settle_kind == SettlementKind.VOLUME_DELTA:
        return _volume_verdict(direction, settlement)
    return PredictionVerdict.UNSETTLED


def _breadth_verdict(
    direction: PredictionDirection, settlement: MarketSettlement
) -> PredictionVerdict:
    if settlement.advance is None or settlement.decline is None:
        return PredictionVerdict.UNSETTLED
    if direction == PredictionDirection.FLAT:
        traded = (
            settlement.advance
            + settlement.decline
            + (settlement.unchanged or 0)
        )
        if traded == 0:
            return PredictionVerdict.UNSETTLED
        gap = abs(settlement.advance - settlement.decline)
        if gap <= FLAT_BREADTH_RATIO * traded:
            return PredictionVerdict.HIT
        return PredictionVerdict.MISS
    if direction == PredictionDirection.UP:
        return (
            PredictionVerdict.HIT
            if settlement.advance > settlement.decline
            else PredictionVerdict.MISS
        )
    if direction == PredictionDirection.DOWN:
        return (
            PredictionVerdict.HIT
            if settlement.advance < settlement.decline
            else PredictionVerdict.MISS
        )
    return PredictionVerdict.UNSETTLED


def _median_verdict(
    direction: PredictionDirection, settlement: MarketSettlement
) -> PredictionVerdict:
    if settlement.median_pct is None or math.isnan(settlement.median_pct):
        return PredictionVerdict.UNSETTLED
    if direction == PredictionDirection.UP:
        return (
            PredictionVerdict.HIT
            if settlement.median_pct > 0
            else PredictionVerdict.MISS
        )
    if direction == PredictionDirection.DOWN:
        return (
            PredictionVerdict.HIT
            if settlement.median_pct < 0
            else PredictionVerdict.MISS
        )
    return PredictionVerdict.UNSETTLED


def _volume_verdict(
    direction: PredictionDirection, settlement: MarketSettlement
) -> PredictionVerdict:
    if settlement.amount_thousand_yuan is None or (
        settlement.prev_amount_thousand_yuan is None
    ):
        return PredictionVerdict.UNSETTLED
    amount = settlement.amount_thousand_yuan
    previous = settlement.prev_amount_thousand_yuan
    if math.isclose(amount, previous, rel_tol=1e-9):
        return PredictionVerdict.TIE
    higher = amount > previous
    if direction == PredictionDirection.UP:
        return PredictionVerdict.HIT if higher else PredictionVerdict.MISS
    if direction == PredictionDirection.DOWN:
        return PredictionVerdict.HIT if not higher else PredictionVerdict.MISS
    return PredictionVerdict.UNSETTLED


def _verdict_rationale(
    record: PredictionRecord,
    settlement: MarketSettlement,
    verdict: PredictionVerdict,
) -> str:
    values = (
        f"advance={settlement.advance} decline={settlement.decline} "
        f"median_pct={settlement.median_pct} "
        f"amount={settlement.amount_thousand_yuan} "
        f"prev_amount={settlement.prev_amount_thousand_yuan}"
    )
    return (
        f"{verdict.value} on {settlement.trade_date} "
        f"via {record.settle_kind.value}: {values}"
    )


def hit_rate_stats(records: Iterable[PredictionRecord]) -> dict[str, Any]:
    """Count verdicts; hit rates are reported both excluding ties and strict."""
    counts: dict[str, int] = {
        "hit": 0,
        "miss": 0,
        "tie": 0,
        "not_settleable": 0,
        "beyond_coverage": 0,
        "unsettled": 0,
    }
    for record in records:
        counts[record.verdict.value] += 1
    decided = counts["hit"] + counts["miss"] + counts["tie"]
    decisive = counts["hit"] + counts["miss"]
    counts["total"] = sum(counts.values())
    counts["decided"] = decided
    counts["hit_rate_excluding_ties"] = (
        counts["hit"] / decisive if decisive else 0.0
    )
    counts["hit_rate_strict"] = counts["hit"] / decided if decided else 0.0
    return counts
