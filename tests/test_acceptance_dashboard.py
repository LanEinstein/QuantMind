"""J-001 — Unit tests for scripts/acceptance_dashboard.py.

Covers:

* Read-only invariant — the dashboard issues only ``find()`` calls and
  no ``update_one`` / ``insert_one`` / ``delete_*`` / ``drop`` /
  ``replace_*`` (asserted via a recording Motor stub).
* Projection arithmetic across the 5 outcome branches (no report,
  PASS, INSUFFICIENT_DATA, FAIL, PAUSED).
* JSON envelope schema + table rendering smoke.
* Reset-event hydration tolerates J-004-shaped audit rows (and
  degrades cleanly when ``timestamp`` is naive or absent).
* ``walk_n_trading_days`` skips weekends + holidays.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from uuid import uuid4

import pytest

from backend.audit.models import AuditEventType
from backend.services.acceptance_report import (
    WINDOW_TRADING_DAYS,
    AcceptanceMetric,
    AcceptanceOutcome,
    AcceptanceReport,
)
from scripts.acceptance_dashboard import (
    RESET_REASON_NAMESPACE,
    DashboardSummary,
    ProjectionResult,
    ResetEvent,
    build_summary,
    fetch_latest_report,
    fetch_latest_reset_event,
    format_json,
    format_table,
    project_pass_date,
    walk_n_trading_days,
)

# ---------------------------------------------------------------------------
# Recording stubs (no real Mongo dependency)
# ---------------------------------------------------------------------------


_WRITE_METHODS: frozenset[str] = frozenset(
    {
        "update_one",
        "update_many",
        "insert_one",
        "insert_many",
        "delete_one",
        "delete_many",
        "replace_one",
        "drop",
        "find_one_and_update",
        "find_one_and_replace",
        "find_one_and_delete",
        "bulk_write",
        "rename",
        "create_index",
        "drop_index",
        "drop_indexes",
    }
)


class _RecordingCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = list(rows)
        self._sort_key: str | None = None
        self._sort_dir: int = 1
        self._limit: int | None = None

    def sort(self, key: str, direction: int = 1) -> _RecordingCursor:
        self._sort_key = key
        self._sort_dir = direction
        return self

    def limit(self, n: int) -> _RecordingCursor:
        self._limit = n
        return self

    def __aiter__(self) -> _RecordingCursor:
        rows = list(self._rows)
        if self._sort_key:

            def _key(row: dict[str, Any]) -> Any:
                value = row.get(self._sort_key)
                if value is None:
                    return ""
                return value

            rows.sort(key=_key, reverse=self._sort_dir < 0)
        if self._limit is not None:
            rows = rows[: self._limit]
        self._iter = iter(rows)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _RecordingCollection:
    def __init__(self, name: str, rows: list[dict[str, Any]]) -> None:
        self.name = name
        self._rows = rows
        self.last_query: dict[str, Any] | None = None

    def find(self, query: dict[str, Any] | None = None) -> _RecordingCursor:
        self.last_query = dict(query or {})
        matched = [r for r in self._rows if _matches(r, query or {})]
        return _RecordingCursor(matched)

    def __getattr__(self, item: str) -> Any:
        if item in _WRITE_METHODS:
            raise AssertionError(
                f"acceptance_dashboard called forbidden write method "
                f"{item!r} on collection {self.name!r}"
            )
        raise AttributeError(item)


class _RecordingDatabase:
    def __init__(
        self, collections: dict[str, list[dict[str, Any]]]
    ) -> None:
        self._collections = {
            name: _RecordingCollection(name, rows)
            for name, rows in collections.items()
        }

    def __getitem__(self, name: str) -> _RecordingCollection:
        if name not in self._collections:
            self._collections[name] = _RecordingCollection(name, [])
        return self._collections[name]


def _matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if row.get(key) != expected:
            return False
    return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _gate_metric(
    name: str,
    *,
    value: float,
    threshold: float,
    direction: str,
    passed: bool,
) -> AcceptanceMetric:
    return AcceptanceMetric(
        name=name,
        value=value,
        threshold=threshold,
        passed=passed,
        direction=direction,
    )


def _passing_report(trade_date: str = "2026-05-15") -> AcceptanceReport:
    """A PASS report with all 8 gates green."""
    return AcceptanceReport(
        report_id=uuid4(),
        computed_at=dt.datetime(2026, 5, 15, 8, 0, 30, tzinfo=dt.UTC),
        trade_date=trade_date,
        window_start="2026-03-10",
        window_end=trade_date,
        trading_days_in_window=WINDOW_TRADING_DAYS,
        outcome=AcceptanceOutcome.PASS,
        metrics=(
            _gate_metric(
                "instruction_completion_rate",
                value=0.99,
                threshold=0.95,
                direction="at_least",
                passed=True,
            ),
            _gate_metric(
                "execution_report_accuracy_rate",
                value=1.0,
                threshold=0.99,
                direction="at_least",
                passed=True,
            ),
            _gate_metric(
                "data_missing_rate",
                value=0.001,
                threshold=0.01,
                direction="at_most",
                passed=True,
            ),
            _gate_metric(
                "llm_timeout_rate",
                value=0.002,
                threshold=0.05,
                direction="at_most",
                passed=True,
            ),
            _gate_metric(
                "signal_generation_rate",
                value=0.99,
                threshold=0.95,
                direction="at_least",
                passed=True,
            ),
            _gate_metric(
                "max_drawdown_pct",
                value=0.04,
                threshold=0.08,
                direction="at_most",
                passed=True,
            ),
            _gate_metric(
                "pnl_cny",
                value=1250.0,
                threshold=0.0,
                direction="at_least",
                passed=True,
            ),
            _gate_metric(
                "csi300_excess_pct",
                value=0.015,
                threshold=0.0,
                direction="at_least",
                passed=True,
            ),
        ),
        notes="",
    )


def _insufficient_report(
    *, trade_date: str = "2026-05-15", days_in_window: int = 12
) -> AcceptanceReport:
    return AcceptanceReport(
        report_id=uuid4(),
        computed_at=dt.datetime(2026, 5, 15, 8, 0, 30, tzinfo=dt.UTC),
        trade_date=trade_date,
        window_start="2026-03-10",
        window_end=trade_date,
        trading_days_in_window=days_in_window,
        outcome=AcceptanceOutcome.INSUFFICIENT_DATA,
        metrics=(),
        notes=f"window contains {days_in_window} trading days; need 45",
    )


def _fail_report(
    *, trade_date: str = "2026-05-15"
) -> AcceptanceReport:
    return AcceptanceReport(
        report_id=uuid4(),
        computed_at=dt.datetime(2026, 5, 15, 8, 0, 30, tzinfo=dt.UTC),
        trade_date=trade_date,
        window_start="2026-03-10",
        window_end=trade_date,
        trading_days_in_window=WINDOW_TRADING_DAYS,
        outcome=AcceptanceOutcome.FAIL,
        metrics=(
            _gate_metric(
                "max_drawdown_pct",
                value=0.12,
                threshold=0.08,
                direction="at_most",
                passed=False,
            ),
        ),
        notes="",
    )


def _paused_report() -> AcceptanceReport:
    return AcceptanceReport(
        report_id=uuid4(),
        computed_at=dt.datetime(2026, 5, 15, 8, 0, 30, tzinfo=dt.UTC),
        trade_date="2026-05-15",
        window_start="2026-03-10",
        window_end="2026-05-15",
        trading_days_in_window=10,
        outcome=AcceptanceOutcome.PAUSED,
        metrics=(),
        notes="acceptance paused — reconciliation OPEN/EXPIRED",
    )


# ---------------------------------------------------------------------------
# walk_n_trading_days
# ---------------------------------------------------------------------------


def test_walk_n_trading_days_n_must_be_positive() -> None:
    with pytest.raises(ValueError):
        walk_n_trading_days(dt.date(2026, 5, 15), 0)


def test_walk_n_trading_days_n1_on_trading_day_returns_start() -> None:
    # 2026-05-15 is a Friday and not a holiday.
    assert walk_n_trading_days(dt.date(2026, 5, 15), 1) == dt.date(2026, 5, 15)


def test_walk_n_trading_days_skips_weekend() -> None:
    # Friday → next trading day is Monday 2026-05-18.
    assert walk_n_trading_days(dt.date(2026, 5, 15), 2) == dt.date(2026, 5, 18)


def test_walk_n_trading_days_starts_on_weekend_advances() -> None:
    # Saturday → first trading day is Monday 2026-05-18.
    assert walk_n_trading_days(dt.date(2026, 5, 16), 1) == dt.date(2026, 5, 18)


def test_walk_n_trading_days_45_from_today_matches_acceptance_window() -> None:
    """Walking forward WINDOW_TRADING_DAYS gives a sane projection."""
    start = dt.date(2026, 5, 18)  # Monday
    target = walk_n_trading_days(start, WINDOW_TRADING_DAYS)
    # 45 trading days forward should land somewhere in July 2026 (depending
    # on holidays). Verify it lands on a trading day strictly > start.
    assert target > start
    from backend.data.trading_calendar import is_trading_day
    assert is_trading_day(target)


# ---------------------------------------------------------------------------
# project_pass_date
# ---------------------------------------------------------------------------


def test_projection_cold_start_when_no_report() -> None:
    today = dt.date(2026, 5, 18)
    result = project_pass_date(None, today)
    assert result.status == "cold_start"
    assert result.projected_date is not None
    # Date string format check
    dt.date.fromisoformat(result.projected_date)


def test_projection_already_passed() -> None:
    today = dt.date(2026, 5, 18)
    report = _passing_report(trade_date="2026-05-15")
    result = project_pass_date(report, today)
    assert result.status == "already_passed"
    assert result.projected_date == "2026-05-15"


def test_projection_insufficient_data_projects_remaining() -> None:
    today = dt.date(2026, 5, 18)
    report = _insufficient_report(days_in_window=12)
    result = project_pass_date(report, today)
    assert result.status == "insufficient_data"
    assert "33 more trading days" in result.reason


def test_projection_fail_projects_full_window() -> None:
    today = dt.date(2026, 5, 18)
    report = _fail_report()
    result = project_pass_date(report, today)
    assert result.status == "fail"
    assert result.projected_date is not None


def test_projection_paused_returns_no_date() -> None:
    today = dt.date(2026, 5, 18)
    report = _paused_report()
    result = project_pass_date(report, today)
    assert result.status == "paused"
    assert result.projected_date is None


# ---------------------------------------------------------------------------
# fetch_latest_report / fetch_latest_reset_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_latest_report_returns_none_when_empty() -> None:
    db = _RecordingDatabase({})
    assert await fetch_latest_report(db) is None


@pytest.mark.asyncio
async def test_fetch_latest_report_picks_newest_trade_date() -> None:
    older = _insufficient_report(trade_date="2026-05-10", days_in_window=5)
    newer = _insufficient_report(trade_date="2026-05-15", days_in_window=12)
    rows = [older.model_dump(mode="python"), newer.model_dump(mode="python")]
    db = _RecordingDatabase({"acceptance_reports": rows})
    result = await fetch_latest_report(db)
    assert result is not None
    assert result.trade_date == "2026-05-15"


@pytest.mark.asyncio
async def test_fetch_latest_reset_event_returns_none_when_no_rows() -> None:
    db = _RecordingDatabase({"audit_events": []})
    assert await fetch_latest_reset_event(db) is None


@pytest.mark.asyncio
async def test_fetch_latest_reset_event_filters_by_reason_namespace() -> None:
    older = {
        "event_type": AuditEventType.SYSTEM_INTERRUPTED.value,
        "reason_namespace": RESET_REASON_NAMESPACE,
        "timestamp": dt.datetime(2026, 5, 10, 12, 0, tzinfo=dt.UTC),
        "payload": {"trigger_type": "MARKET_DATA_OUTAGE_30MIN"},
    }
    newer = {
        "event_type": AuditEventType.SYSTEM_INTERRUPTED.value,
        "reason_namespace": RESET_REASON_NAMESPACE,
        "timestamp": dt.datetime(2026, 5, 14, 9, 0, tzinfo=dt.UTC),
        "payload": {"trigger_type": "LLM_FULL_STOP_1H"},
    }
    unrelated = {
        "event_type": AuditEventType.SYSTEM_INTERRUPTED.value,
        "reason_namespace": "unrelated_namespace",
        "timestamp": dt.datetime(2026, 5, 15, 9, 0, tzinfo=dt.UTC),
        "payload": {},
    }
    db = _RecordingDatabase(
        {"audit_events": [older, newer, unrelated]}
    )
    event = await fetch_latest_reset_event(db)
    assert event is not None
    assert event.trigger_type == "LLM_FULL_STOP_1H"
    assert event.reason_namespace == RESET_REASON_NAMESPACE


@pytest.mark.asyncio
async def test_fetch_latest_reset_event_coerces_naive_timestamp_to_utc() -> None:
    row = {
        "event_type": AuditEventType.SYSTEM_INTERRUPTED.value,
        "reason_namespace": RESET_REASON_NAMESPACE,
        "timestamp": dt.datetime(2026, 5, 14, 9, 0),  # naive
        "payload": {"trigger_type": "MOCK_BROKER_CORRUPTION"},
    }
    db = _RecordingDatabase({"audit_events": [row]})
    event = await fetch_latest_reset_event(db)
    assert event is not None
    assert event.timestamp.tzinfo is dt.UTC


# ---------------------------------------------------------------------------
# build_summary (integration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_summary_aggregates_report_and_reset() -> None:
    report = _insufficient_report(days_in_window=20)
    reset_row = {
        "event_type": AuditEventType.SYSTEM_INTERRUPTED.value,
        "reason_namespace": RESET_REASON_NAMESPACE,
        "timestamp": dt.datetime(2026, 5, 14, 9, 0, tzinfo=dt.UTC),
        "payload": {
            "trigger_type": "LONG_CONN_OUTAGE_4H",
            "observation_window_minutes": 240,
        },
    }
    db = _RecordingDatabase(
        {
            "acceptance_reports": [report.model_dump(mode="python")],
            "audit_events": [reset_row],
        }
    )
    now = dt.datetime(2026, 5, 18, 8, 0, tzinfo=dt.UTC)
    summary = await build_summary(db, now=now, source="mongo")
    assert summary.latest_report is not None
    assert summary.latest_report.trading_days_in_window == 20
    assert summary.latest_reset_event is not None
    assert summary.latest_reset_event.trigger_type == "LONG_CONN_OUTAGE_4H"
    assert summary.projection.status == "insufficient_data"


# ---------------------------------------------------------------------------
# Read-only invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_summary_issues_no_writes() -> None:
    """Calling any forbidden write method on the recording stub raises."""
    db = _RecordingDatabase({"acceptance_reports": [], "audit_events": []})
    now = dt.datetime(2026, 5, 18, 8, 0, tzinfo=dt.UTC)
    # No exception ⇒ the recording stub recorded only allowed methods.
    await build_summary(db, now=now, source="mongo")


@pytest.mark.asyncio
async def test_recording_stub_raises_on_write_method() -> None:
    """Sanity: the stub really does flag forbidden writes."""
    db = _RecordingDatabase({"acceptance_reports": []})
    with pytest.raises(AssertionError, match="forbidden write method"):
        db["acceptance_reports"].update_one  # noqa: B018 — accessing attr


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_format_table_no_report() -> None:
    summary = DashboardSummary(
        generated_at=dt.datetime(2026, 5, 18, 8, 0, tzinfo=dt.UTC),
        source="mongo",
        latest_report=None,
        latest_reset_event=None,
        projection=ProjectionResult(
            status="cold_start",
            projected_date="2026-07-21",
            reason="no rows",
        ),
    )
    text = format_table(summary)
    assert "QuantMind Acceptance Dashboard" in text
    assert "no rows in acceptance_reports" in text
    assert "PROJECTED PASS DATE" in text


def test_format_table_with_report_and_reset() -> None:
    summary = DashboardSummary(
        generated_at=dt.datetime(2026, 5, 18, 8, 0, tzinfo=dt.UTC),
        source="mongo",
        latest_report=_passing_report(),
        latest_reset_event=ResetEvent(
            timestamp=dt.datetime(2026, 5, 10, 12, 0, tzinfo=dt.UTC),
            trigger_type="LLM_FULL_STOP_1H",
            reason_namespace=RESET_REASON_NAMESPACE,
            payload={"trigger_type": "LLM_FULL_STOP_1H"},
        ),
        projection=ProjectionResult(
            status="already_passed",
            projected_date="2026-05-15",
            reason="passed",
        ),
    )
    text = format_table(summary)
    assert "PASS" in text  # outcome cell
    assert "instruction_completion_rate" in text
    assert "LLM_FULL_STOP_1H" in text


def test_format_json_envelope_schema() -> None:
    summary = DashboardSummary(
        generated_at=dt.datetime(2026, 5, 18, 8, 0, tzinfo=dt.UTC),
        source="mongo",
        latest_report=_insufficient_report(days_in_window=20),
        latest_reset_event=None,
        projection=ProjectionResult(
            status="insufficient_data",
            projected_date="2026-07-21",
            reason="need 25 more trading days",
        ),
    )
    payload = json.loads(format_json(summary))
    assert payload["window_trading_days"] == WINDOW_TRADING_DAYS
    assert payload["latest_report"]["trading_days_in_window"] == 20
    assert payload["latest_report"]["outcome"] == "INSUFFICIENT_DATA"
    assert payload["latest_reset_event"] is None
    assert payload["projection"]["status"] == "insufficient_data"
    assert payload["source"] == "mongo"
