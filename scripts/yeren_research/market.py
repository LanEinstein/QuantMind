"""Build small, as-of market evidence bundles from the immutable PIT archive."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import JsonValue

from backend.data.historical_ingest.job import VENDOR
from backend.data.historical_ingest.serialization import parse_csv_bytes
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.inventory import read_jsonl
from scripts.yeren_research.schema import EvidenceBundle, EvidenceKind, EvidenceRecord

SHANGHAI = ZoneInfo("Asia/Shanghai")
MARKET_OPEN = time(9, 30)
LUNCH_START = time(11, 30)
LUNCH_END = time(13, 0)
MARKET_CLOSE = time(15, 0)

DAILY_CLOSE_ENDPOINTS = frozenset(
    {
        "daily",
        "daily_basic",
        "adj_factor",
        "fund_daily",
        "stk_limit",
        "cyq_perf",
        "stk_factor_pro",
        "limit_list_d",
        "suspend_d",
    }
)


def load_trade_dates(pit_root: Path) -> tuple[str, ...]:
    """Use stored ``daily`` keys as the authoritative offline calendar."""
    dates = {
        str(row["trade_date"])
        for row in read_jsonl(pit_root / "index.jsonl")
        if row.get("endpoint") == "daily"
    }
    return tuple(sorted(dates))


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("decision timestamp must include a timezone")
    return value.astimezone(SHANGHAI)


def earliest_a_share_action(
    published_at: datetime, trading_dates: Sequence[str]
) -> datetime | None:
    """Map publication time to the first continuous-auction action boundary."""
    local = _local(published_at)
    day_key = local.strftime("%Y%m%d")
    trading_set = set(trading_dates)
    if day_key in trading_set:
        clock = local.timetz().replace(tzinfo=None)
        if clock < MARKET_OPEN:
            return datetime.combine(local.date(), MARKET_OPEN, SHANGHAI)
        if MARKET_OPEN <= clock < LUNCH_START:
            return local
        if LUNCH_START <= clock < LUNCH_END:
            return datetime.combine(local.date(), LUNCH_END, SHANGHAI)
        if LUNCH_END <= clock < MARKET_CLOSE:
            return local
    next_days = [value for value in trading_dates if value > day_key]
    if not next_days:
        return None
    next_date = datetime.strptime(next_days[0], "%Y%m%d").date()
    return datetime.combine(next_date, MARKET_OPEN, SHANGHAI)


def _available_at_close(trade_date: str) -> datetime:
    parsed = datetime.strptime(trade_date, "%Y%m%d").date()
    return datetime.combine(parsed, MARKET_CLOSE, SHANGHAI)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _rows(frame: pd.DataFrame, codes: frozenset[str]) -> list[dict[str, Any]]:
    if not codes:
        return []
    selected = frame
    if "ts_code" in frame.columns:
        selected = frame[frame["ts_code"].astype(str).isin(codes)]
    return [
        {str(key): _json_value(value) for key, value in row.items()}
        for row in selected.to_dict(orient="records")
    ]


def _daily_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if "pct_chg" not in frame.columns:
        return {"row_count": len(frame)}
    changes = pd.to_numeric(frame["pct_chg"], errors="coerce")
    summary: dict[str, Any] = {
        "row_count": len(frame),
        "advance_count": int((changes > 0).sum()),
        "decline_count": int((changes < 0).sum()),
        "unchanged_count": int((changes == 0).sum()),
    }
    if "amount" in frame.columns:
        amounts = pd.to_numeric(frame["amount"], errors="coerce")
        summary["amount_thousand_yuan"] = float(amounts.sum(skipna=True))
    return summary


def read_market_records(
    *,
    store: SnapshotStore,
    trading_dates: Iterable[str],
    endpoints: Sequence[str],
    codes: Iterable[str] = (),
) -> tuple[EvidenceRecord, ...]:
    """Read selected daily-close evidence while retaining snapshot provenance."""
    unsupported = set(endpoints) - DAILY_CLOSE_ENDPOINTS
    if unsupported:
        raise ValueError(
            "daily-close availability is undefined for endpoints: "
            + ", ".join(sorted(unsupported))
        )
    selected_codes = frozenset(codes)
    records: list[EvidenceRecord] = []
    for trade_date in sorted(set(trading_dates)):
        for endpoint in endpoints:
            snapshot = store.latest(
                vendor=VENDOR, endpoint=endpoint, trade_date=trade_date
            )
            if snapshot is None:
                continue
            frame = parse_csv_bytes(snapshot.raw_payload)
            data: dict[str, Any] = {
                "endpoint": endpoint,
                "trade_date": trade_date,
                "snapshot_id": str(snapshot.snapshot_id),
                "snapshot_version": snapshot.version,
                "row_count": len(frame),
                "selected_rows": _rows(frame, selected_codes),
            }
            if endpoint == "daily":
                data["market_summary"] = _daily_summary(frame)
            records.append(
                EvidenceRecord(
                    record_id=f"{endpoint}:{trade_date}:{snapshot.snapshot_id}",
                    source_kind=EvidenceKind.MARKET,
                    source_ref=f"data/marketdata_pit#{snapshot.snapshot_id}",
                    information_available_at=_available_at_close(trade_date),
                    data=data,
                )
            )
    return tuple(records)


def partition_records(
    records: Iterable[EvidenceRecord], decision_cutoff: datetime
) -> tuple[tuple[EvidenceRecord, ...], tuple[EvidenceRecord, ...]]:
    """Split solely on historical availability, never on eventual usefulness."""
    cutoff = _local(decision_cutoff)
    decision: list[EvidenceRecord] = []
    outcome: list[EvidenceRecord] = []
    for record in records:
        target = decision if record.information_available_at <= cutoff else outcome
        target.append(record)
    return tuple(decision), tuple(outcome)


def build_market_bundles(
    *,
    case_id: str,
    video_ids: Sequence[str],
    decision_cutoff: datetime,
    pit_root: Path,
    start_date: str,
    end_date: str,
    endpoints: Sequence[str],
    codes: Sequence[str] = (),
) -> tuple[EvidenceBundle, EvidenceBundle]:
    """Build physically separate decision and outcome bundles for one case."""
    calendar = load_trade_dates(pit_root)
    dates = tuple(day for day in calendar if start_date <= day <= end_date)
    records = read_market_records(
        store=SnapshotStore(pit_root),
        trading_dates=dates,
        endpoints=endpoints,
        codes=codes,
    )
    decision_records, outcome_records = partition_records(records, decision_cutoff)
    query: dict[str, JsonValue] = {
        "pit_root": str(pit_root),
        "start_date": start_date,
        "end_date": end_date,
        "endpoints": list(endpoints),
        "codes": list(codes),
    }
    earliest_action = earliest_a_share_action(decision_cutoff, calendar)
    omissions: list[str] = []
    if not dates:
        omissions.append("No archived daily trading dates in the requested range.")
    else:
        present = {
            (str(record.data["endpoint"]), str(record.data["trade_date"]))
            for record in records
        }
        for endpoint in endpoints:
            missing = sum((endpoint, trade_date) not in present for trade_date in dates)
            if missing:
                omissions.append(
                    f"{endpoint}: missing {missing}/{len(dates)} requested sessions."
                )
    if "daily" not in endpoints:
        omissions.append("No all-market breadth requested.")
    return (
        EvidenceBundle(
            bundle_type="decision",
            case_id=case_id,
            video_ids=tuple(video_ids),
            decision_cutoff=decision_cutoff,
            earliest_action_at=earliest_action,
            query=query,
            records=decision_records,
            omissions=tuple(omissions),
        ),
        EvidenceBundle(
            bundle_type="outcome",
            case_id=case_id,
            video_ids=tuple(video_ids),
            decision_cutoff=decision_cutoff,
            earliest_action_at=earliest_action,
            query=query,
            records=outcome_records,
            omissions=tuple(omissions),
        ),
    )


def write_new_json(path: Path, value: Any) -> None:
    """Publish a new append-only research artifact; revisions use a new path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    with path.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")
