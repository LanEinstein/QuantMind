"""Unit tests for backend.services.phase5b_exit_check.

Covers:
* aggregate_per_run_costs: per-day shareing, malformed entries, suffix
  handling
* split_runs_by_category: assign_category integration, dropping bad codes
* compute_exit_report: per-gate has_data + passes, empty inputs, latency
  parsing, daily total, shadow integration
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from backend.services.phase5b_exit_check import (
    DAILY_TOTAL_RMB,
    FAST_COST_PER_STOCK_RMB,
    SLOW_COST_PER_STOCK_RMB,
    aggregate_per_run_costs,
    compute_exit_report,
    render_markdown,
    split_runs_by_category,
)
from backend.services.watchlist_policy import BucketConfig, WatchlistPolicy

# ----------------------------------------------------------------------
# Test fixtures
# ----------------------------------------------------------------------


def _policy(
    *,
    fast_codes: tuple[str, ...] = ("600519",),
    slow_codes: tuple[str, ...] = ("000001",),
    default_category: str = "slow",
) -> WatchlistPolicy:
    return WatchlistPolicy(
        fast=BucketConfig(
            cron="*/15 9-15 * * 1-5",
            pipeline="fast",
            max_debate_rounds=1,
            pipeline_timeout_seconds=480,
            default_codes=fast_codes,
        ),
        slow=BucketConfig(
            cron="0 16 * * 1-5",
            pipeline="slow",
            max_debate_rounds=2,
            pipeline_timeout_seconds=900,
            default_codes=slow_codes,
        ),
        overrides={},
        default_category=default_category,  # type: ignore[arg-type]
        fast_default_set=frozenset(fast_codes),
        slow_default_set=frozenset(slow_codes),
    )


def _record(
    *,
    run_id: str,
    stock_code: str,
    trade_date: str,
    started_at: datetime.datetime,
    duration_sec: float,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "stock_code": stock_code,
        "trade_date": trade_date,
        "created_at": started_at,
        "completed_at": started_at + datetime.timedelta(seconds=duration_sec),
    }


# ----------------------------------------------------------------------
# Group 1: aggregate_per_run_costs
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestAggregatePerRunCosts:
    def test_shares_daily_total_evenly(self) -> None:
        cost_entries = [
            {"date": "2026-05-02", "agent_name": "fund_manager", "cost_rmb": 0.4},
            {"date": "2026-05-02", "agent_name": "intelligence", "cost_rmb": 0.4},
        ]
        record_index = {"r1": "2026-05-02", "r2": "2026-05-02"}
        out = aggregate_per_run_costs(cost_entries, record_index=record_index)
        assert out["r1"]["cost_rmb"] == 0.4
        assert out["r2"]["cost_rmb"] == 0.4

    def test_drops_negative_cost(self) -> None:
        cost_entries = [
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.5},
            {"date": "2026-05-02", "agent_name": "y", "cost_rmb": -1.0},
        ]
        out = aggregate_per_run_costs(
            cost_entries, record_index={"r1": "2026-05-02"}
        )
        assert out["r1"]["cost_rmb"] == 0.5

    def test_drops_nan_cost(self) -> None:
        # NaN is equivalent to missing telemetry — the date has zero
        # validated cost entries, so per-run sharing must not generate
        # a 0-cost row (otherwise the cost gate could pass on garbage
        # data; see codex P5B-exit R1 P1 follow-up).
        cost_entries = [
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": float("nan")},
        ]
        out = aggregate_per_run_costs(
            cost_entries, record_index={"r1": "2026-05-02"}
        )
        assert out == {}

    def test_no_runs_for_day_yields_empty(self) -> None:
        out = aggregate_per_run_costs(
            [{"date": "2026-05-02", "agent_name": "x", "cost_rmb": 1.0}],
            record_index={},
        )
        assert out == {}

    def test_runs_without_cost_telemetry_dropped(self) -> None:
        # codex P5B-exit R1 P1: when Redis is silent for a date, runs
        # on that date must NOT generate 0-cost rows (which would let
        # the cost gate pass on missing telemetry).
        out = aggregate_per_run_costs(
            [],
            record_index={"r1": "2026-05-02", "r2": "2026-05-03"},
        )
        assert out == {}

    def test_partial_cost_telemetry_drops_uncovered_dates(self) -> None:
        out = aggregate_per_run_costs(
            [{"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.4}],
            record_index={
                "r-covered": "2026-05-02",
                "r-uncovered": "2026-05-03",
            },
        )
        assert "r-covered" in out
        assert "r-uncovered" not in out


# ----------------------------------------------------------------------
# Group 2: split_runs_by_category
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestSplitRunsByCategory:
    def test_partitions_by_policy(self) -> None:
        records = [
            {"run_id": "r1", "stock_code": "600519"},
            {"run_id": "r2", "stock_code": "000001"},
            {"run_id": "r3", "stock_code": "300750"},  # default_category=slow
        ]
        buckets = split_runs_by_category(records, _policy())
        assert len(buckets["fast"]) == 1
        assert len(buckets["slow"]) == 2

    def test_drops_records_missing_stock_code(self) -> None:
        records = [
            {"run_id": "r0"},  # no stock_code
            {"run_id": "r1", "stock_code": "600519"},
            {"run_id": "r2", "stock_code": ""},  # empty
        ]
        buckets = split_runs_by_category(records, _policy())
        assert len(buckets["fast"]) == 1
        assert len(buckets["slow"]) == 0

    def test_drops_records_missing_run_id(self) -> None:
        # Records without run_id cannot be reconciled against
        # cost telemetry; drop them so completeness checks stay honest.
        records = [
            {"stock_code": "600519"},  # no run_id
            {"run_id": "r1", "stock_code": "600519"},
        ]
        buckets = split_runs_by_category(records, _policy())
        assert len(buckets["fast"]) == 1


# ----------------------------------------------------------------------
# Group 3: compute_exit_report
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestComputeExitReport:
    def test_empty_inputs_flag_no_data(self) -> None:
        report = compute_exit_report([], [], [], _policy(), days=7)
        assert report.has_data["fast_cost"] is False
        assert report.has_data["slow_cost"] is False
        assert report.has_data["shadow"] is False
        # No gate can pass on silence.
        assert all(v is False for v in report.passes.values())

    def test_gate_passes_when_thresholds_met(self) -> None:
        records = [
            _record(
                run_id="r-fast",
                stock_code="600519",
                trade_date="2026-05-02",
                started_at=datetime.datetime(
                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
                ),
                duration_sec=300,  # 5 min < 8 min threshold
            ),
            _record(
                run_id="r-slow",
                stock_code="000001",
                trade_date="2026-05-02",
                started_at=datetime.datetime(
                    2026, 5, 2, 16, 0, tzinfo=datetime.UTC
                ),
                duration_sec=600,  # 10 min < 15 min threshold
            ),
        ]
        cost_entries = [
            {
                "date": "2026-05-02",
                "agent_name": "fund_manager",
                "cost_rmb": 0.4,  # split 0.2 each across the 2 runs
            },
        ]
        report = compute_exit_report(
            records, cost_entries, [], _policy(), days=7
        )
        assert report.cost.fast_p95_rmb == 0.2
        assert report.cost.slow_p95_rmb == 0.2
        assert report.cost.daily_total_rmb == 0.4
        assert report.passes["fast_cost"] is True
        assert report.passes["slow_cost"] is True
        assert report.passes["daily_total"] is True
        assert report.passes["fast_latency"] is True
        assert report.passes["slow_latency"] is True

    def test_fast_cost_breach_fails(self) -> None:
        records = [
            _record(
                run_id="r-fast",
                stock_code="600519",
                trade_date="2026-05-02",
                started_at=datetime.datetime(
                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
                ),
                duration_sec=100,
            )
        ]
        cost_entries = [
            {
                "date": "2026-05-02",
                "agent_name": "fund_manager",
                "cost_rmb": FAST_COST_PER_STOCK_RMB + 0.1,
            }
        ]
        report = compute_exit_report(
            records, cost_entries, [], _policy(), days=7
        )
        assert report.passes["fast_cost"] is False

    def test_slow_latency_breach_fails(self) -> None:
        records = [
            _record(
                run_id="r-slow",
                stock_code="000001",
                trade_date="2026-05-02",
                started_at=datetime.datetime(
                    2026, 5, 2, 16, 0, tzinfo=datetime.UTC
                ),
                duration_sec=20 * 60,  # 20 min > 15 min threshold
            )
        ]
        cost_entries = [
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.1}
        ]
        report = compute_exit_report(
            records, cost_entries, [], _policy(), days=7
        )
        assert report.passes["slow_latency"] is False
        assert report.passes["slow_cost"] is True  # cost was fine

    def test_daily_total_breach_fails(self) -> None:
        records = [
            _record(
                run_id=f"r-{i}",
                stock_code="000001",
                trade_date="2026-05-02",
                started_at=datetime.datetime(
                    2026, 5, 2, 16, 0, tzinfo=datetime.UTC
                ),
                duration_sec=300,
            )
            for i in range(2)
        ]
        cost_entries = [
            {
                "date": "2026-05-02",
                "agent_name": "x",
                "cost_rmb": DAILY_TOTAL_RMB + 0.5,
            }
        ]
        report = compute_exit_report(
            records, cost_entries, [], _policy(), days=7
        )
        assert report.passes["daily_total"] is False

    def test_shadow_integration(self) -> None:
        shadow_docs = [
            {
                "run_id": "r1",
                "stock_code": "600519",
                "trade_date": "2026-05-02",
                "baseline": {
                    "action": "买入",
                    "confidence": 0.7,
                    "model": "kimi-k2.6",
                    "latency_ms": 100.0,
                    "escalated": False,
                    "parse_ok": True,
                },
                "routed": {
                    "action": "买入",
                    "confidence": 0.72,
                    "model": "qwen3.6-plus",
                    "latency_ms": 80.0,
                    "escalated": False,
                    "parse_ok": True,
                },
            }
        ]
        report = compute_exit_report(
            [], [], shadow_docs, _policy(), days=7
        )
        assert report.has_data["shadow"] is True
        assert report.passes["shadow_action_match"] is True
        assert report.passes["shadow_confidence_delta"] is True

    def test_partial_cost_telemetry_marks_no_data(self) -> None:
        # codex P5B-exit R2 P2: when only some in-window runs have
        # cost telemetry, the gate must fail-closed rather than pass
        # on the covered subset.
        records = [
            _record(
                run_id=f"r-{i}",
                stock_code="600519",
                trade_date=trade_date,
                started_at=datetime.datetime(
                    2026, 5, 2 + i, 9, 0, tzinfo=datetime.UTC
                ),
                duration_sec=120,
            )
            for i, trade_date in enumerate(("2026-05-02", "2026-05-03"))
        ]
        cost_entries = [
            # Only 2026-05-02 has telemetry; 2026-05-03 is uncovered.
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.1},
        ]
        report = compute_exit_report(
            records, cost_entries, [], _policy(), days=7
        )
        assert report.has_data["fast_cost"] is False
        assert report.passes["fast_cost"] is False

    def test_daily_total_no_data_when_only_one_bucket_covered(self) -> None:
        # codex P5B-exit R6 UNRESOLVED follow-up: daily_total spans
        # both buckets, so a fast-only or slow-only telemetry window
        # must be flagged no-data, not silently pass on the covered
        # subset. We have one fast run on date A (covered) plus one
        # slow run on date B (uncovered).
        records = [
            _record(
                run_id="r-fast",
                stock_code="600519",
                trade_date="2026-05-02",
                started_at=datetime.datetime(
                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
                ),
                duration_sec=100,
            ),
            _record(
                run_id="r-slow",
                stock_code="000001",
                trade_date="2026-05-03",
                started_at=datetime.datetime(
                    2026, 5, 3, 16, 0, tzinfo=datetime.UTC
                ),
                duration_sec=300,
            ),
        ]
        cost_entries = [
            # Only fast bucket's date has cost telemetry.
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.1},
        ]
        report = compute_exit_report(
            records, cost_entries, [], _policy(), days=7
        )
        # Fast cost is fully covered (1/1 runs).
        assert report.has_data["fast_cost"] is True
        # Slow cost has 0 covered out of 1 — incomplete, fail-closed.
        assert report.has_data["slow_cost"] is False
        # daily_total spans BOTH buckets and one is incomplete →
        # must NOT report as has_data.
        assert report.has_data["daily_total"] is False
        assert report.passes["daily_total"] is False

    def test_record_missing_run_id_does_not_crash(self) -> None:
        # codex P5B-exit R2 P2 (WARNING): a malformed record with no
        # run_id used to KeyError out of the cost comprehension.
        records = [
            {"stock_code": "600519"},  # no run_id, no trade_date
            _record(
                run_id="r1",
                stock_code="600519",
                trade_date="2026-05-02",
                started_at=datetime.datetime(
                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
                ),
                duration_sec=100,
            ),
        ]
        cost_entries = [
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.1}
        ]
        report = compute_exit_report(
            records, cost_entries, [], _policy(), days=7
        )
        # The well-formed record still drives the fast_cost gate.
        assert report.cost.fast_runs == 1
        assert report.passes["fast_cost"] is True

    def test_confidence_delta_pipes_escaped_in_markdown(self) -> None:
        # codex P5B-exit R2 P3 (INFO): raw ``|Δ|`` pipes split the row
        # into extra markdown columns. We escape them as ``\|Δ\|`` so
        # the third cell stays one cell.
        out = render_markdown(
            compute_exit_report([], [], [], _policy(), days=7)
        )
        line = next(
            line
            for line in out.splitlines()
            if line.startswith("| shadow_confidence_delta")
        )
        # The escape sequence must be present.
        assert "\\|Δ\\|" in line
        # The status (last) cell must be a single cell — the literal
        # ``|`` after the no-data marker is the closing pipe; nothing
        # follows. Splitting on un-escaped pipes via a quick regex
        # assertion would be overkill — checking the suffix is enough
        # to confirm the row terminator wasn't shifted.
        assert line.rstrip().endswith("no-data |")

    def test_iso_string_timestamps_parsed(self) -> None:
        # codex P5B-exit R1 P1: live records persist via
        # ``model_dump(mode="json")`` so timestamps arrive as ISO
        # strings, not datetime objects. Make sure the latency gate
        # still sees them.
        records = [
            {
                "run_id": "r1",
                "stock_code": "600519",
                "trade_date": "2026-05-02",
                "created_at": "2026-05-02T09:00:00+00:00",
                "completed_at": "2026-05-02T09:05:00+00:00",
            }
        ]
        cost_entries = [
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.1}
        ]
        report = compute_exit_report(
            records, cost_entries, [], _policy(), days=7
        )
        assert report.has_data["fast_latency"] is True
        assert report.latency.fast_p95_sec == 300.0
        assert report.passes["fast_latency"] is True

    def test_naive_iso_timestamp_dropped(self) -> None:
        records = [
            {
                "run_id": "r1",
                "stock_code": "600519",
                "trade_date": "2026-05-02",
                "created_at": "2026-05-02T09:00:00",  # no tz
                "completed_at": "2026-05-02T09:05:00",
            }
        ]
        report = compute_exit_report(
            records, [], [], _policy(), days=7
        )
        assert report.has_data["fast_latency"] is False

    def test_record_with_completed_before_created_drops_latency(self) -> None:
        # Anomalous record (clock skew on a node?) — should not poison
        # the percentile.
        records = [
            {
                "run_id": "r1",
                "stock_code": "600519",
                "trade_date": "2026-05-02",
                "created_at": datetime.datetime(
                    2026, 5, 2, 10, 0, tzinfo=datetime.UTC
                ),
                "completed_at": datetime.datetime(
                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
                ),
            }
        ]
        report = compute_exit_report(
            records, [], [], _policy(), days=7
        )
        assert report.has_data["fast_latency"] is False
        assert report.passes["fast_latency"] is False


# ----------------------------------------------------------------------
# Group 4: render_markdown
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestRenderMarkdown:
    def test_empty_report_renders_no_data(self) -> None:
        out = render_markdown(
            compute_exit_report([], [], [], _policy(), days=7)
        )
        assert "no-data" in out

    def test_happy_path_renders_check_marks(self) -> None:
        records = [
            _record(
                run_id="r-fast",
                stock_code="600519",
                trade_date="2026-05-02",
                started_at=datetime.datetime(
                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
                ),
                duration_sec=100,
            )
        ]
        cost_entries = [
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.1}
        ]
        out = render_markdown(
            compute_exit_report(
                records, cost_entries, [], _policy(), days=7
            )
        )
        assert "✅" in out
        # slow gates have no data here so we expect a no-data marker.
        assert "no-data" in out

    def test_no_data_marker_uses_full_gate_key(self) -> None:
        # codex P5B-exit R1 P3: ``name.split("_")[0]`` looked up keys
        # like ``"fast"`` / ``"daily"`` which never existed in
        # ``has_data``. Every no-data row therefore rendered as ❌
        # instead of ⚠️ no-data, hiding missing telemetry.
        out = render_markdown(
            compute_exit_report([], [], [], _policy(), days=7)
        )
        # All six gates are no-data ⇒ every row should be ⚠️ no-data;
        # no plain ❌ should appear.
        assert "no-data" in out
        for gate in (
            "fast_cost",
            "slow_cost",
            "daily_total",
            "fast_latency",
            "slow_latency",
            "shadow_action_match",
            "shadow_confidence_delta",
        ):
            line = next(
                line for line in out.splitlines() if line.startswith(f"| {gate}")
            )
            assert "no-data" in line

    def test_includes_all_six_gates(self) -> None:
        out = render_markdown(
            compute_exit_report([], [], [], _policy(), days=7)
        )
        for gate in (
            "fast_cost",
            "slow_cost",
            "daily_total",
            "fast_latency",
            "slow_latency",
            "shadow_action_match",
            "shadow_confidence_delta",
        ):
            assert gate in out
        assert f"{FAST_COST_PER_STOCK_RMB}" in out
        assert f"{SLOW_COST_PER_STOCK_RMB}" in out
