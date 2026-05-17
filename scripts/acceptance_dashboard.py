#!/usr/bin/env python
"""J-001 — Acceptance dashboard CLI (read-only Mongo + audit aggregator).

Aggregates the latest ``acceptance_reports`` row + the latest acceptance
reset event from ``audit_events`` and projects an earliest possible PASS
date using ``config/holidays.yaml`` + weekend skips.

The CLI is **read-only**: it issues ``find()`` against Mongo and never
``update_one`` / ``insert_one`` / ``delete_one`` / ``drop`` / ``replace_one``.
The companion test ``tests/test_acceptance_dashboard.py`` asserts this
invariant via a recording Motor stub so a future contributor cannot
accidentally turn this script into a write path.

Usage::

    # Human-readable table (default)
    python scripts/acceptance_dashboard.py

    # JSON envelope for monitoring consumption
    python scripts/acceptance_dashboard.py --json

    # Override Mongo connection (defaults to MONGODB_URI env)
    python scripts/acceptance_dashboard.py --mongodb-uri mongodb://localhost:27017

Red lines:

* CLI uses ``actor=cli`` semantics (does NOT write audit; read-only).
* Reset events are pulled from ``audit_events`` filtered on
  ``event_type=SYSTEM_INTERRUPTED`` + ``reason_namespace=acceptance_reset_trigger``
  (the J-004 reset_trigger_detector contract). Until J-004 ships, no
  reset rows exist and the dashboard prints ``none``.
* Projection assumes "no further FAIL or reset from today" — it is an
  *earliest* possible PASS date, not a forecast. The operator reads the
  projection alongside the latest reset event to gauge volatility.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from backend.audit.models import AuditEventType
from backend.data.trading_calendar import is_trading_day, next_trading_day
from backend.services.acceptance_report import (
    WINDOW_TRADING_DAYS,
    AcceptanceMetric,
    AcceptanceOutcome,
    AcceptanceReport,
)
from backend.utils.trading_hours import SHANGHAI

_ACCEPTANCE_COLLECTION = "acceptance_reports"
_AUDIT_COLLECTION = "audit_events"

RESET_REASON_NAMESPACE = "acceptance_reset_trigger"
"""J-004 reset_trigger_detector writes SYSTEM_INTERRUPTED audit events
with this single reason_namespace; the trigger sub-type lives in the
payload under ``trigger_type`` (one of the 5 P0-6 §1 locked values)."""


# ---------------------------------------------------------------------------
# Motor protocols — duck-typed so tests can swap in a recording stub without
# importing motor at module load time.
# ---------------------------------------------------------------------------


@runtime_checkable
class _MotorCollection(Protocol):
    def find(self, *args: Any, **kwargs: Any) -> Any: ...


@runtime_checkable
class _MotorDatabase(Protocol):
    def __getitem__(self, name: str) -> _MotorCollection: ...


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResetEvent:
    """Single row pulled from ``audit_events`` for the reset summary."""

    timestamp: dt.datetime
    trigger_type: str
    reason_namespace: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProjectionResult:
    """Earliest-possible PASS-date projection."""

    status: str
    projected_date: str | None
    reason: str


@dataclass(frozen=True)
class DashboardSummary:
    """Aggregated dashboard payload — what the CLI renders + the JSON shape."""

    generated_at: dt.datetime
    source: str
    latest_report: AcceptanceReport | None
    latest_reset_event: ResetEvent | None
    projection: ProjectionResult


# ---------------------------------------------------------------------------
# Read-only Mongo queries
# ---------------------------------------------------------------------------


async def fetch_latest_report(db: _MotorDatabase) -> AcceptanceReport | None:
    """Return the newest acceptance report or ``None`` if none exist."""
    cursor = db[_ACCEPTANCE_COLLECTION].find({}).sort("trade_date", -1).limit(1)
    async for raw in cursor:
        cleaned = _drop_mongo_id(_attach_utc(raw))
        try:
            return AcceptanceReport.model_validate(cleaned, strict=False)
        except Exception as exc:  # noqa: BLE001 — defensive decode
            print(
                f"warning: skipping malformed acceptance_reports row: {exc}",
                file=sys.stderr,
            )
            return None
    return None


async def fetch_latest_reset_event(db: _MotorDatabase) -> ResetEvent | None:
    """Return the newest acceptance reset event (J-004 contract) or ``None``."""
    cursor = (
        db[_AUDIT_COLLECTION]
        .find(
            {
                "event_type": AuditEventType.SYSTEM_INTERRUPTED.value,
                "reason_namespace": RESET_REASON_NAMESPACE,
            }
        )
        .sort("timestamp", -1)
        .limit(1)
    )
    async for raw in cursor:
        timestamp = raw.get("timestamp")
        if isinstance(timestamp, dt.datetime) and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=dt.UTC)
        if not isinstance(timestamp, dt.datetime):
            return None
        payload = raw.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        trigger_type = str(payload.get("trigger_type", "UNKNOWN"))
        return ResetEvent(
            timestamp=timestamp,
            trigger_type=trigger_type,
            reason_namespace=str(raw.get("reason_namespace", "")),
            payload=dict(payload),
        )
    return None


def _attach_utc(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value
    if isinstance(value, dict):
        return {k: _attach_utc(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_attach_utc(v) for v in value]
    return value


def _drop_mongo_id(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in raw.items() if k != "_id"}


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def walk_n_trading_days(start: dt.date, n: int) -> dt.date:
    """Return the date of the Nth trading day from (and including) ``start``.

    ``N=1`` returns ``start`` itself when it is a trading day, else
    ``next_trading_day(start)``. ``N=2`` returns the next trading day after
    that. This matches "after N more days of clean operation" semantics so
    the projection counts today when today is a trading day.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    cursor = start if is_trading_day(start) else next_trading_day(start)
    for _ in range(n - 1):
        cursor = next_trading_day(cursor)
    return cursor


def project_pass_date(
    report: AcceptanceReport | None,
    today: dt.date,
) -> ProjectionResult:
    """Return an *earliest possible* PASS-date projection.

    The projection is calendar-only: it assumes the system runs without
    further FAIL or reset from ``today`` onward. The operator pairs the
    projection with the latest reset event to gauge realism — a window
    that resets weekly will never reach the projected date.
    """
    if report is None:
        target = walk_n_trading_days(today, WINDOW_TRADING_DAYS)
        return ProjectionResult(
            status="cold_start",
            projected_date=target.isoformat(),
            reason=(
                "no acceptance_reports rows yet; earliest PASS = today + "
                f"{WINDOW_TRADING_DAYS} clean trading days"
            ),
        )
    if report.outcome is AcceptanceOutcome.PASS:
        return ProjectionResult(
            status="already_passed",
            projected_date=report.trade_date,
            reason=f"acceptance passed on {report.trade_date}",
        )
    if report.outcome is AcceptanceOutcome.INSUFFICIENT_DATA:
        remaining = WINDOW_TRADING_DAYS - report.trading_days_in_window
        target = walk_n_trading_days(today, remaining)
        return ProjectionResult(
            status="insufficient_data",
            projected_date=target.isoformat(),
            reason=(
                f"need {remaining} more trading days; projection assumes no "
                "FAIL or reset from today"
            ),
        )
    if report.outcome is AcceptanceOutcome.FAIL:
        target = walk_n_trading_days(today, WINDOW_TRADING_DAYS)
        return ProjectionResult(
            status="fail",
            projected_date=target.isoformat(),
            reason=(
                "rolling window FAIL — earliest possible PASS = today + "
                f"{WINDOW_TRADING_DAYS} clean trading days so the failure "
                "rolls out of the window"
            ),
        )
    if report.outcome is AcceptanceOutcome.PAUSED:
        return ProjectionResult(
            status="paused",
            projected_date=None,
            reason=(
                "acceptance paused — depends on reconciliation "
                "OPEN/EXPIRED resolution before projection can resume"
            ),
        )
    return ProjectionResult(
        status="unknown",
        projected_date=None,
        reason=f"unknown outcome value: {report.outcome!r}",
    )


# ---------------------------------------------------------------------------
# Summary assembly
# ---------------------------------------------------------------------------


async def build_summary(
    db: _MotorDatabase,
    *,
    now: dt.datetime,
    source: str = "mongo",
) -> DashboardSummary:
    """Aggregate the dashboard payload from read-only Mongo queries."""
    report = await fetch_latest_report(db)
    reset_event = await fetch_latest_reset_event(db)
    # Codex cycle 2 P2 fix — A-share trade dates are Asia/Shanghai
    # calendar days; using ``now.astimezone(dt.UTC).date()`` shifted
    # the projection one day backwards for any run between 00:00 and
    # 08:00 local time. The projection now matches the BrokerScheduler
    # cron timezone + the AcceptanceReport.trade_date convention.
    today = now.astimezone(SHANGHAI).date()
    projection = project_pass_date(report, today)
    return DashboardSummary(
        generated_at=now,
        source=source,
        latest_report=report,
        latest_reset_event=reset_event,
        projection=projection,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _format_metric_row(metric: AcceptanceMetric) -> str:
    arrow = "≥" if metric.direction == "at_least" else "≤"
    status = "PASS" if metric.passed else "FAIL"
    return (
        f"  {metric.name:<32} {metric.value:<10.6f} "
        f"{arrow} {metric.threshold:<8} {status}"
    )


def format_table(summary: DashboardSummary) -> str:
    """Render the dashboard as a human-readable table."""
    lines: list[str] = []
    header = (
        "QuantMind Acceptance Dashboard — "
        f"{summary.generated_at.astimezone(dt.UTC).isoformat()} (UTC)"
    )
    lines.append(header)
    lines.append("=" * len(header))
    lines.append("")
    lines.append("LATEST ACCEPTANCE REPORT")
    if summary.latest_report is None:
        lines.append("  (no rows in acceptance_reports collection)")
    else:
        report = summary.latest_report
        lines.append(f"  trade_date              : {report.trade_date}")
        lines.append(
            "  computed_at             : "
            f"{report.computed_at.astimezone(dt.UTC).isoformat()}"
        )
        lines.append(f"  outcome                 : {report.outcome.value}")
        lines.append(f"  window_start            : {report.window_start}")
        lines.append(f"  window_end              : {report.window_end}")
        lines.append(
            f"  trading_days_in_window  : "
            f"{report.trading_days_in_window} / {WINDOW_TRADING_DAYS}"
        )
        if report.notes:
            lines.append(f"  notes                   : {report.notes}")
    lines.append("")

    lines.append("8 HARD GATES (5 stability + 3 strategy)")
    if summary.latest_report is None or not summary.latest_report.metrics:
        lines.append(
            "  (no metrics — window has < 45 trading days or no report yet)"
        )
    else:
        lines.append(
            f"  {'metric':<32} {'value':<10} "
            f"  {'threshold':<10} status"
        )
        lines.append("  " + "-" * 70)
        for metric in summary.latest_report.metrics:
            lines.append(_format_metric_row(metric))
    lines.append("")

    lines.append("LATEST RESET EVENT")
    if summary.latest_reset_event is None:
        lines.append("  none")
    else:
        event = summary.latest_reset_event
        lines.append(
            "  timestamp               : "
            f"{event.timestamp.astimezone(dt.UTC).isoformat()}"
        )
        lines.append(f"  trigger_type            : {event.trigger_type}")
        lines.append(f"  reason_namespace        : {event.reason_namespace}")
    lines.append("")

    lines.append("PROJECTED PASS DATE")
    lines.append(f"  status                  : {summary.projection.status}")
    lines.append(
        f"  projected_date          : {summary.projection.projected_date or '-'}"
    )
    lines.append(f"  reason                  : {summary.projection.reason}")
    return "\n".join(lines)


def format_json(summary: DashboardSummary) -> str:
    """Render the dashboard as a JSON envelope for monitoring consumers."""
    envelope: dict[str, Any] = {
        "generated_at": summary.generated_at.astimezone(dt.UTC).isoformat(),
        "source": summary.source,
        "window_trading_days": WINDOW_TRADING_DAYS,
        "latest_report": _serialise_report(summary.latest_report),
        "latest_reset_event": _serialise_reset_event(summary.latest_reset_event),
        "projection": {
            "status": summary.projection.status,
            "projected_date": summary.projection.projected_date,
            "reason": summary.projection.reason,
        },
    }
    return json.dumps(envelope, indent=2, ensure_ascii=False)


def _serialise_report(report: AcceptanceReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "report_id": str(report.report_id),
        "computed_at": report.computed_at.astimezone(dt.UTC).isoformat(),
        "trade_date": report.trade_date,
        "window_start": report.window_start,
        "window_end": report.window_end,
        "trading_days_in_window": report.trading_days_in_window,
        "outcome": report.outcome.value,
        "metrics": [
            {
                "name": m.name,
                "value": m.value,
                "threshold": m.threshold,
                "direction": m.direction,
                "passed": m.passed,
            }
            for m in report.metrics
        ],
        "notes": report.notes,
    }


def _serialise_reset_event(event: ResetEvent | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "timestamp": event.timestamp.astimezone(dt.UTC).isoformat(),
        "trigger_type": event.trigger_type,
        "reason_namespace": event.reason_namespace,
        "payload": event.payload,
    }


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="acceptance_dashboard",
        description=(
            "Read-only acceptance dashboard. Aggregates the latest "
            "acceptance_reports row + the latest reset audit event and "
            "projects an earliest possible PASS date."
        ),
    )
    parser.add_argument(
        "--mongodb-uri",
        default=os.environ.get("MONGODB_URI", "mongodb://localhost:27017"),
        help="MongoDB connection string (env MONGODB_URI takes precedence).",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("MONGODB_DATABASE", "quantmind"),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON envelope (default: human-readable table).",
    )
    parser.add_argument(
        "--now",
        default=None,
        help=(
            "Override the projection clock (ISO8601). Used by tests and "
            "for time-pinned dashboards; production leaves unset."
        ),
    )
    return parser.parse_args(argv)


def _resolve_now(raw: str | None) -> dt.datetime:
    if raw is None:
        return dt.datetime.now(dt.UTC)
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"invalid --now timestamp: {raw!r} ({exc})") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    now = _resolve_now(args.now)

    import motor.motor_asyncio as motor

    client = motor.AsyncIOMotorClient(
        args.mongodb_uri, uuidRepresentation="standard"
    )
    try:
        db = client[args.database]
        summary = await build_summary(db, now=now, source="mongo")
    finally:
        client.close()

    if args.json:
        print(format_json(summary))
    else:
        print(format_table(summary))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":  # pragma: no cover — exercised via tests + integration
    raise SystemExit(main())
