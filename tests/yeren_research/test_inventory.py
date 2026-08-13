from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.inventory import audit_corpus, audit_pit


def _jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_corpus_audit_uses_latest_terminal_state_and_finds_empty_text(
    tmp_path: Path,
) -> None:
    _jsonl(
        tmp_path / "metadata.jsonl",
        [
            {
                "aweme_id": "done",
                "published_at": "2022-01-01T16:00:00+08:00",
                "duration_ms": 2_000,
            },
            {
                "aweme_id": "empty",
                "published_at": "2023-01-01T16:00:00+08:00",
                "duration_ms": 1_000,
            },
            {
                "aweme_id": "gone",
                "published_at": "2024-01-01T16:00:00+08:00",
                "duration_ms": 1_000,
            },
        ],
    )
    _jsonl(
        tmp_path / "ledger.jsonl",
        [
            {"aweme_id": "done", "status": "failed"},
            {"aweme_id": "done", "status": "done"},
            {"aweme_id": "empty", "status": "done"},
            {"aweme_id": "gone", "status": "unavailable"},
        ],
    )
    _jsonl(
        tmp_path / "transcripts" / "done.json",
        [
            {
                "text": "先试错",
                "sentences": [{"start_ms": 100, "end_ms": 900, "text": "先试错"}],
            }
        ],
    )
    _jsonl(
        tmp_path / "transcripts" / "empty.json",
        [{"text": "", "sentences": []}],
    )

    inventory = audit_corpus(tmp_path)

    assert inventory["unique_video_ids"] == 3
    assert inventory["latest_status_counts"] == {"done": 2, "unavailable": 1}
    assert inventory["transcript_files"] == 2
    assert inventory["sentence_count"] == 1
    assert inventory["empty_text_ids"] == ["empty"]
    assert inventory["missing_transcript_ids"] == []


def test_pit_audit_reads_real_payload_headers_and_versions(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    for version, payload in (
        (1, b"ts_code,trade_date,close\n000001.SZ,20240102,10\n"),
        (2, b"ts_code,trade_date,close\n000001.SZ,20240102,11\n"),
    ):
        store.put(
            MarketDataSnapshot.create(
                vendor="tushare",
                endpoint="daily",
                params={"trade_date": "20240102"},
                trade_date="20240102",
                raw_payload=payload,
                encoding="csv",
                compression="none",
                fetch_time_utc=datetime(2024, 1, 3, tzinfo=UTC),
                version=version,
                metadata={"rows": 1},
            )
        )

    inventory = audit_pit(tmp_path)
    daily = inventory["endpoints"]["daily"]

    assert inventory["index_rows"] == 2
    assert daily["logical_date_count"] == 1
    assert daily["max_version"] == 2
    assert daily["restatement_snapshots"] == 1
    assert daily["field_variants"] == [["ts_code", "trade_date", "close"]]
