"""Build conservative date-gated financial evidence from the local PIT store."""

from __future__ import annotations

import math
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from backend.data.historical_ingest.job import VENDOR
from backend.data.historical_ingest.serialization import parse_csv_bytes
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.inventory import read_jsonl
from scripts.yeren_research.market import earliest_a_share_action, load_trade_dates
from scripts.yeren_research.schema import EvidenceBundle, EvidenceKind, EvidenceRecord

SHANGHAI = ZoneInfo("Asia/Shanghai")
ENDPOINT = "fina_indicator_vip"
FINANCIAL_FIELDS = ("roe", "grossprofit_margin", "netprofit_yoy", "or_yoy")


def _date_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"", "nan", "NaT", "None"} else text


def _number(value: object) -> float | None:
    try:
        converted = float(str(value))
    except (TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def _conservative_available_at(
    ann_date: str, trading_dates: tuple[str, ...]
) -> datetime | None:
    next_dates = (trade_date for trade_date in trading_dates if trade_date > ann_date)
    next_date = next(next_dates, None)
    if next_date is None:
        return None
    parsed = datetime.strptime(next_date, "%Y%m%d").date()
    return datetime.combine(parsed, time(9, 30), SHANGHAI)


def _periods(pit_root: Path, decision_key: str, limit: int) -> tuple[str, ...]:
    values = {
        str(row["trade_date"])
        for row in read_jsonl(pit_root / "index.jsonl")
        if row.get("endpoint") == ENDPOINT
        and str(row.get("trade_date") or "") < decision_key
    }
    return tuple(sorted(values)[-limit:])


def read_financial_records(
    *,
    pit_root: Path,
    codes: tuple[str, ...],
    decision_cutoff: datetime,
    lookback_periods: int = 8,
) -> tuple[EvidenceRecord, ...]:
    """Return each code's latest financial indicators safely known by cutoff."""
    if decision_cutoff.tzinfo is None:
        raise ValueError("decision timestamp must include a timezone")
    if lookback_periods <= 0:
        raise ValueError("lookback_periods must be positive")
    cutoff = decision_cutoff.astimezone(SHANGHAI)
    decision_key = cutoff.strftime("%Y%m%d")
    store = SnapshotStore(pit_root)
    trading_dates = load_trade_dates(pit_root)
    best: dict[str, tuple[tuple[str, str, str], EvidenceRecord]] = {}

    for period in _periods(pit_root, decision_key, lookback_periods):
        snapshot = store.latest(vendor=VENDOR, endpoint=ENDPOINT, trade_date=period)
        if snapshot is None:
            continue
        frame = parse_csv_bytes(snapshot.raw_payload)
        selected = frame[frame["ts_code"].astype(str).isin(codes)]
        for row in selected.to_dict(orient="records"):
            code = _date_text(row.get("ts_code"))
            ann_date = _date_text(row.get("ann_date"))
            end_date = _date_text(row.get("end_date"))
            update_flag = _date_text(row.get("update_flag"))
            if len(ann_date) != 8 or ann_date >= decision_key:
                continue
            available_at = _conservative_available_at(ann_date, trading_dates)
            if available_at is None or available_at > cutoff:
                continue
            values: dict[str, JsonValue] = {
                field: _number(row.get(field)) for field in FINANCIAL_FIELDS
            }
            data: dict[str, JsonValue] = {
                "endpoint": ENDPOINT,
                "ts_code": code,
                "end_date": end_date,
                "ann_date": ann_date,
                "update_flag": update_flag,
                "values": values,
                "snapshot_id": str(snapshot.snapshot_id),
                "snapshot_version": snapshot.version,
                "availability_semantics": (
                    "ann_date has date precision; first tradable from 09:30 on the "
                    "first archived A-share session after ann_date"
                ),
            }
            record = EvidenceRecord(
                record_id=(
                    f"{ENDPOINT}:{code}:{end_date}:{ann_date}:{snapshot.snapshot_id}"
                ),
                source_kind=EvidenceKind.FINANCIAL,
                source_ref=f"data/marketdata_pit#{snapshot.snapshot_id}",
                information_available_at=available_at,
                data=data,
            )
            rank = (end_date, ann_date, update_flag)
            current = best.get(code)
            if current is None or rank > current[0]:
                best[code] = (rank, record)
    return tuple(best[code][1] for code in sorted(best))


def build_financial_decision_bundle(
    *,
    case_id: str,
    video_ids: tuple[str, ...],
    decision_cutoff: datetime,
    pit_root: Path,
    codes: tuple[str, ...],
    lookback_periods: int = 8,
) -> EvidenceBundle:
    """Package financial indicators without pretending they are announcement text."""
    records = read_financial_records(
        pit_root=pit_root,
        codes=codes,
        decision_cutoff=decision_cutoff,
        lookback_periods=lookback_periods,
    )
    calendar = load_trade_dates(pit_root)
    next_action = earliest_a_share_action(decision_cutoff, calendar)
    query: dict[str, JsonValue] = {
        "pit_root": str(pit_root),
        "endpoint": ENDPOINT,
        "codes": list(codes),
        "lookback_periods": lookback_periods,
    }
    returned_codes = {str(record.data["ts_code"]) for record in records}
    missing_codes = sorted(set(codes) - returned_codes)
    omissions = [
        "No announcement body is stored in this endpoint.",
        "Same-day announcements are excluded because ann_date has no time.",
    ]
    if missing_codes:
        omissions.append(
            "No eligible financial record for: " + ", ".join(missing_codes)
        )
    return EvidenceBundle(
        bundle_type="decision",
        case_id=case_id,
        video_ids=video_ids,
        decision_cutoff=decision_cutoff,
        earliest_action_at=next_action,
        query=query,
        records=records,
        omissions=tuple(omissions),
    )
