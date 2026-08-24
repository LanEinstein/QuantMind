from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from scripts.yeren_research.market import (
    _rows,
    build_market_bundles,
    earliest_a_share_action,
    partition_records,
)
from scripts.yeren_research.schema import EvidenceKind, EvidenceRecord

CALENDAR = ("20240105", "20240108", "20240109")


def _record(record_id: str, available_at: str, value: int) -> EvidenceRecord:
    return EvidenceRecord(
        record_id=record_id,
        source_kind=EvidenceKind.MARKET,
        source_ref=f"snapshot:{record_id}",
        information_available_at=datetime.fromisoformat(available_at),
        data={"value": value},
    )


def test_earliest_action_handles_lunch_and_after_close() -> None:
    lunch = earliest_a_share_action(
        datetime.fromisoformat("2024-01-08T12:10:00+08:00"), CALENDAR
    )
    after_close = earliest_a_share_action(
        datetime.fromisoformat("2024-01-05T16:10:00+08:00"), CALENDAR
    )

    assert lunch == datetime.fromisoformat("2024-01-08T13:00:00+08:00")
    assert after_close == datetime.fromisoformat("2024-01-08T09:30:00+08:00")


def test_future_outcome_mutation_cannot_change_decision_partition() -> None:
    cutoff = datetime.fromisoformat("2024-01-05T16:00:00+08:00")
    known = _record("known", "2024-01-05T15:00:00+08:00", 1)
    future = _record("future", "2024-01-08T15:00:00+08:00", 2)
    changed_future = _record("future", "2024-01-08T15:00:00+08:00", 999)

    decision_before, _ = partition_records((known, future), cutoff)
    decision_after, _ = partition_records((known, changed_future), cutoff)

    assert decision_before == decision_after == (known,)


def test_naive_cutoff_is_rejected() -> None:
    record = _record("known", "2024-01-05T15:00:00+08:00", 1)

    try:
        partition_records((record,), datetime(2024, 1, 5, 16))
    except ValueError as exc:
        assert "timezone" in str(exc)
    else:
        raise AssertionError("naive cutoff should not be accepted")


def test_rows_without_codes_does_not_copy_full_market_frame() -> None:
    frame = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "close": 10.0},
            {"ts_code": "600000.SH", "close": 20.0},
        ]
    )

    assert _rows(frame, frozenset()) == []


def test_market_bundle_reports_requested_range_outside_archive(
    tmp_path: Path,
) -> None:
    (tmp_path / "index.jsonl").write_text("", encoding="utf-8")

    decision, outcome = build_market_bundles(
        case_id="missing-range",
        video_ids=("v1",),
        decision_cutoff=datetime.fromisoformat("2027-01-01T12:00:00+08:00"),
        pit_root=tmp_path,
        start_date="20270101",
        end_date="20270102",
        endpoints=("daily",),
    )

    assert decision.records == outcome.records == ()
    assert decision.omissions == outcome.omissions
    assert "No archived daily" in decision.omissions[0]
