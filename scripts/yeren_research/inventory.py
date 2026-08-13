"""Read-only inventory of the Yeren corpus and the PIT byte archive."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield JSON objects without loading large append-only logs into memory."""
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                message = f"invalid JSON at {path}:{line_number}: {exc}"
                raise ValueError(message) from exc
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            yield value


def audit_corpus(root: Path) -> dict[str, Any]:
    """Describe the corpus state and faults that change research handling."""
    metadata_path = root / "metadata.jsonl"
    ledger_path = root / "ledger.jsonl"
    transcript_dir = root / "transcripts"

    metadata_rows = list(read_jsonl(metadata_path))
    metadata_ids = [str(row["aweme_id"]) for row in metadata_rows]
    metadata_by_id = {str(row["aweme_id"]): row for row in metadata_rows}
    metadata_counts = Counter(metadata_ids)

    ledger_rows = list(read_jsonl(ledger_path))
    terminal_by_id: dict[str, dict[str, Any]] = {}
    for row in ledger_rows:
        terminal_by_id[str(row["aweme_id"])] = row

    transcript_paths = sorted(transcript_dir.glob("*.json"))
    transcript_ids = {path.stem for path in transcript_paths}
    empty_text_ids: list[str] = []
    empty_sentence_ids: list[str] = []
    invalid_sentence_offsets: list[dict[str, Any]] = []
    sentence_count = 0

    for path in transcript_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        text = str(payload.get("text") or "").strip()
        sentences = payload.get("sentences") or []
        if not text:
            empty_text_ids.append(path.stem)
        if not sentences:
            empty_sentence_ids.append(path.stem)
        sentence_count += len(sentences)
        duration_ms = int(metadata_by_id.get(path.stem, {}).get("duration_ms") or 0)
        for index, sentence in enumerate(sentences):
            start_ms = int(sentence.get("start_ms", -1))
            end_ms = int(sentence.get("end_ms", -1))
            if (
                start_ms < 0
                or end_ms < start_ms
                or (duration_ms > 0 and end_ms > duration_ms)
            ):
                invalid_sentence_offsets.append(
                    {
                        "aweme_id": path.stem,
                        "sentence_index": index,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "duration_ms": duration_ms,
                    }
                )

    status_counts = Counter(
        str(row.get("status") or "unknown") for row in terminal_by_id.values()
    )
    unavailable_ids = sorted(
        aweme_id
        for aweme_id, row in terminal_by_id.items()
        if row.get("status") == "unavailable"
    )
    unique_metadata_ids = set(metadata_ids)
    expected_transcripts = unique_metadata_ids - set(unavailable_ids)
    published_values = sorted(
        str(row["published_at"]) for row in metadata_rows if row.get("published_at")
    )

    return {
        "metadata_rows": len(metadata_rows),
        "unique_video_ids": len(unique_metadata_ids),
        "duplicate_metadata_ids": sorted(
            aweme_id for aweme_id, count in metadata_counts.items() if count > 1
        ),
        "published_at_start": published_values[0] if published_values else None,
        "published_at_end": published_values[-1] if published_values else None,
        "ledger_rows": len(ledger_rows),
        "latest_status_counts": dict(sorted(status_counts.items())),
        "unavailable_ids": unavailable_ids,
        "transcript_files": len(transcript_paths),
        "sentence_count": sentence_count,
        "empty_text_ids": sorted(empty_text_ids),
        "empty_sentence_ids": sorted(empty_sentence_ids),
        "missing_transcript_ids": sorted(expected_transcripts - transcript_ids),
        "orphan_transcript_ids": sorted(transcript_ids - unique_metadata_ids),
        "invalid_sentence_offsets": invalid_sentence_offsets,
    }


def _payload_header(root: Path, row: dict[str, Any]) -> tuple[str, ...]:
    if row.get("encoding") != "csv" or row.get("compression") != "none":
        return ()
    digest = str(row["raw_payload_sha256"])
    payload_path = root / "payloads" / digest[:2] / f"{digest}.bin"
    with payload_path.open(encoding="utf-8", newline="") as source:
        first_line = source.readline()
    return tuple(next(csv.reader([first_line]))) if first_line else ()


def audit_pit(root: Path) -> dict[str, Any]:
    """Summarize actual snapshot keys, ranges, fields, and stored row counts."""
    index_path = root / "index.jsonl"
    rows_by_endpoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    index_rows = 0
    indexed_payload_bytes = 0
    for row in read_jsonl(index_path):
        endpoint = str(row["endpoint"])
        rows_by_endpoint[endpoint].append(row)
        index_rows += 1
        indexed_payload_bytes += int(row.get("size") or 0)

    endpoints: dict[str, Any] = {}
    for endpoint, rows in sorted(rows_by_endpoint.items()):
        ordered = sorted(
            rows,
            key=lambda row: (str(row["trade_date"]), int(row.get("version") or 1)),
        )
        dates = {str(row["trade_date"]) for row in rows}
        field_variants = {_payload_header(root, row) for row in ordered}
        field_variants.discard(())
        metadata_rows = [int(row.get("metadata", {}).get("rows") or 0) for row in rows]
        endpoints[endpoint] = {
            "snapshot_count": len(rows),
            "logical_date_count": len(dates),
            "date_start": min(dates),
            "date_end": max(dates),
            "max_version": max(int(row.get("version") or 1) for row in rows),
            "restatement_snapshots": sum(
                int(row.get("version") or 1) > 1 for row in rows
            ),
            "parameter_keys": sorted(
                {key for row in rows for key in (row.get("params") or {})}
            ),
            "encodings": sorted({str(row.get("encoding")) for row in rows}),
            "compressions": sorted({str(row.get("compression")) for row in rows}),
            "stored_rows_total": sum(metadata_rows),
            "stored_rows_min": min(metadata_rows),
            "stored_rows_max": max(metadata_rows),
            "field_variants": [list(fields) for fields in sorted(field_variants)],
            "fetch_time_start": min(str(row["fetch_time_utc"]) for row in rows),
            "fetch_time_end": max(str(row["fetch_time_utc"]) for row in rows),
        }

    return {
        "index_rows": index_rows,
        "indexed_payload_bytes": indexed_payload_bytes,
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
    }


def build_asset_inventory(corpus_root: Path, pit_root: Path) -> dict[str, Any]:
    """Build the combined M2 phase-A inventory from immutable inputs."""
    return {
        "schema_version": 1,
        "corpus_root": str(corpus_root),
        "pit_root": str(pit_root),
        "corpus": audit_corpus(corpus_root),
        "pit": audit_pit(pit_root),
    }
