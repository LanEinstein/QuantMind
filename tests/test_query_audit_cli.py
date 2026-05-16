"""H-002 — scripts/query_audit.py CLI tests.

Coverage:
- Parses ISO8601 / enum / limit args
- JSONL happy path + JSON envelope
- Limit truncation
- Bad arg surfaces SystemExit
- _truncate caps payload value length so a multi-page LLM dump cannot
  wreck the operator terminal
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.audit.models import AuditActor, AuditEvent, AuditEventType, AuditOutcome
from scripts import query_audit  # type: ignore[import-not-found]


def _event(
    *,
    ts: datetime,
    event_type: AuditEventType = AuditEventType.EXECUTION_REPORT_SUBMITTED,
    actor: AuditActor = AuditActor.FEISHU_USER,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    correlation_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        timestamp=ts,
        event_type=event_type,
        actor=actor,
        resource_type="execution_report",
        payload={"k": "v"},
        outcome=outcome,
        correlation_id=correlation_id,
    )


def _write_jsonl(events: list[AuditEvent], path: Path) -> None:
    path.write_text(
        "\n".join(e.model_dump_json() for e in events) + "\n",
        encoding="utf-8",
    )


def _now() -> datetime:
    return datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


def test_parse_args_defaults() -> None:
    args = query_audit._parse_args([])
    assert args.source == "jsonl"
    assert args.limit == 50
    assert args.json is False


def test_parse_args_all_fields() -> None:
    args = query_audit._parse_args(
        [
            "--source",
            "mongo",
            "--since",
            "2026-05-16T00:00:00Z",
            "--event-type",
            "execution_report_submitted",
            "--limit",
            "10",
            "--json",
        ]
    )
    assert args.source == "mongo"
    assert args.event_type == "execution_report_submitted"
    assert args.limit == 10
    assert args.json is True


def test_invalid_iso_raises_systemexit() -> None:
    with pytest.raises(SystemExit):
        query_audit._parse_iso("not-a-date")


def test_invalid_enum_raises_systemexit() -> None:
    with pytest.raises(SystemExit):
        query_audit._validate_enum("nonsense", AuditEventType, "event_type")


def test_jsonl_query_returns_most_recent_first(tmp_path: Path) -> None:
    base = _now()
    events = [
        _event(ts=base - timedelta(minutes=20)),
        _event(ts=base - timedelta(minutes=5)),
        _event(
            ts=base - timedelta(minutes=10),
            event_type=AuditEventType.RECONCILIATION_TICKET_DECIDED,
        ),
    ]
    path = tmp_path / "audit.jsonl"
    _write_jsonl(events, path)

    out = query_audit._query_jsonl(
        path=path,
        since=None,
        until=None,
        event_type=None,
        actor=None,
        outcome=None,
        correlation_id=None,
        resource_type=None,
        limit=10,
    )
    assert len(out) == 3
    assert out[0].event_type is AuditEventType.EXECUTION_REPORT_SUBMITTED
    assert out[0].timestamp > out[1].timestamp > out[2].timestamp


def test_jsonl_query_limit_truncates(tmp_path: Path) -> None:
    base = _now()
    events = [_event(ts=base - timedelta(minutes=i)) for i in range(5)]
    path = tmp_path / "audit.jsonl"
    _write_jsonl(events, path)
    out = query_audit._query_jsonl(
        path=path,
        since=None,
        until=None,
        event_type=None,
        actor=None,
        outcome=None,
        correlation_id=None,
        resource_type=None,
        limit=2,
    )
    assert len(out) == 2


def test_jsonl_query_filters_event_type(tmp_path: Path) -> None:
    base = _now()
    events = [
        _event(ts=base, event_type=AuditEventType.RECONCILIATION_TICKET_DECIDED),
        _event(ts=base - timedelta(minutes=1)),
    ]
    path = tmp_path / "audit.jsonl"
    _write_jsonl(events, path)
    out = query_audit._query_jsonl(
        path=path,
        since=None,
        until=None,
        event_type=AuditEventType.RECONCILIATION_TICKET_DECIDED,
        actor=None,
        outcome=None,
        correlation_id=None,
        resource_type=None,
        limit=10,
    )
    assert len(out) == 1
    assert out[0].event_type is AuditEventType.RECONCILIATION_TICKET_DECIDED


def test_truncate_long_payload_values_capped() -> None:
    long_val = "x" * 5_000
    out = query_audit._truncate(long_val, max_len=200)
    assert len(out) <= 201  # 200 chars + ellipsis
    assert out.endswith("…")


def test_format_payload_repr_of_dict() -> None:
    payload = {"foo": 1, "bar": [1, 2, 3]}
    out = query_audit._format_payload(payload)
    assert "foo=" in out
    assert "bar=" in out


def test_format_payload_empty() -> None:
    assert query_audit._format_payload({}) == "-"


def test_table_format_includes_header(tmp_path: Path) -> None:
    base = _now()
    events = [_event(ts=base)]
    out = query_audit._format_table(events)
    assert "timestamp" in out
    assert "event_type" in out
    assert "execution_report_submitted" in out


def test_table_format_empty() -> None:
    assert query_audit._format_table([]) == "(no rows)"


def test_json_envelope_round_trips(tmp_path: Path) -> None:
    base = _now()
    events = [_event(ts=base)]
    raw = query_audit._format_json(events, source="jsonl", limit=10)
    parsed = json.loads(raw)
    assert parsed["count"] == 1
    assert parsed["source"] == "jsonl"
    assert parsed["events"][0]["event_type"] == "execution_report_submitted"


def test_main_runs_with_jsonl_source(tmp_path: Path) -> None:
    base = _now()
    events = [_event(ts=base)]
    path = tmp_path / "audit.jsonl"
    _write_jsonl(events, path)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = query_audit.main(["--jsonl", str(path), "--limit", "5"])
    assert rc == 0
    assert "execution_report_submitted" in buf.getvalue()


def test_main_json_mode(tmp_path: Path) -> None:
    base = _now()
    events = [_event(ts=base)]
    path = tmp_path / "audit.jsonl"
    _write_jsonl(events, path)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = query_audit.main(["--jsonl", str(path), "--json"])
    assert rc == 0
    parsed = json.loads(buf.getvalue())
    assert parsed["count"] == 1
    assert parsed["source"] == "jsonl"


def test_main_limit_out_of_range() -> None:
    with pytest.raises(SystemExit):
        query_audit.main(["--limit", "0"])


def test_hydrate_normalises_naive_mongo_timestamp() -> None:
    """Codex cycle 1 P2 regression.

    Motor returns BSON Dates as naive UTC datetimes; the CLI must pin
    the tz so the table / JSON formatters do not interpret the value as
    local time on Asia/Shanghai hosts.
    """
    naive = datetime(2026, 5, 16, 12, 0)
    aware = naive.replace(tzinfo=UTC)
    raw_doc = {
        "event_id": "8925fdb0-b77d-4e67-9222-475551f017e7",
        "timestamp": naive,
        "event_type": "execution_report_submitted",
        "actor": "feishu_user",
        "resource_type": "execution_report",
        "payload": {},
        "outcome": "success",
        "actor_detail": None,
        "resource_id": None,
        "correlation_id": None,
        "reason_namespace": None,
    }
    event = query_audit._hydrate(raw_doc)
    assert event is not None
    assert event.timestamp.tzinfo is not None
    assert event.timestamp.astimezone(UTC) == aware


def test_main_since_after_until() -> None:
    with pytest.raises(SystemExit):
        query_audit.main(
            [
                "--since",
                "2026-05-16T12:00:00Z",
                "--until",
                "2026-05-16T11:00:00Z",
            ]
        )
