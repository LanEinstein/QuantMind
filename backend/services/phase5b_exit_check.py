"""Phase 5B exit-gate verification — pure analysis layer.

The companion CLI ``scripts/phase5b_exit_check.py`` is a thin shell over
this module. We aggregate:

* daily LLM spend per stock (from Redis cost_tracker)
* fast / slow pipeline latency (from analysis_records.created_at /
  completed_at + watchlist policy categorisation)
* decision consistency (delegates to
  :mod:`backend.services.shadow_compare`)

and emit a :class:`ExitGateReport` whose ``passes`` dict mirrors the
SSoT §6.972 exit checklist:

* fast cost ≤ ¥0.20 / stock
* slow cost ≤ ¥0.50 / stock
* daily total ≤ ¥1.20
* fast p95 latency ≤ 8 min
* slow p95 latency ≤ 15 min
* decision consistency ≥ 0.85

A gate that has zero data records ``has_data=False`` and the
corresponding ``passes`` entry is False — Phase 5B exit cannot pass on
silence.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.services.shadow_compare import (
    ACTION_MATCH_THRESHOLD,
    CONFIDENCE_DELTA_THRESHOLD,
    ShadowReport,
    compute_shadow_report,
)
from backend.services.universe_policy import UniversePolicy, assign_category

# SSoT §6.972 thresholds
FAST_COST_PER_STOCK_RMB = 0.20
SLOW_COST_PER_STOCK_RMB = 0.50
DAILY_TOTAL_RMB = 1.20
FAST_P95_LATENCY_SEC = 8 * 60  # 8 min
SLOW_P95_LATENCY_SEC = 15 * 60  # 15 min


@dataclass(frozen=True)
class CostMetric:
    """Aggregated cost per (run/day) bucket; -1 sentinel when no data."""

    fast_p95_rmb: float
    slow_p95_rmb: float
    daily_total_rmb: float
    fast_runs: int
    slow_runs: int


@dataclass(frozen=True)
class LatencyMetric:
    fast_p95_sec: float
    slow_p95_sec: float
    fast_runs: int
    slow_runs: int


@dataclass(frozen=True)
class ExitGateReport:
    days: int
    cost: CostMetric
    latency: LatencyMetric
    shadow: ShadowReport | None
    has_data: dict[str, bool] = field(default_factory=dict)
    passes: dict[str, bool] = field(default_factory=dict)


def aggregate_per_run_costs(
    cost_entries: Iterable[Mapping[str, Any]],
    *,
    record_index: Mapping[str, str],
) -> dict[str, dict[str, float]]:
    """Bucket Redis cost entries into per-run totals.

    Inputs:

    * ``cost_entries`` — ``llm:usage:{date}:{agent}:{provider}`` rows
      flattened to dicts with keys ``date``, ``agent_name``, ``cost_rmb``.
      Phase 5B-T03 splits agent names with ``/triage`` and
      ``/escalation`` suffixes; we strip them to attribute spend back to
      the parent agent. Per-run isn't directly recoverable from Redis
      keys (they aggregate per day per agent), so callers pass an
      auxiliary ``record_index`` mapping ``run_id → trade_date`` from
      the analysis_records collection. The function then re-scales the
      day's total by the number of runs we know happened on that day.
    * ``record_index`` — ``run_id → trade_date`` for the runs in scope.

    Returns ``{run_id: {"date": str, "cost_rmb": float}}``.
    """
    daily_totals: dict[str, float] = {}
    for entry in cost_entries:
        date = entry.get("date")
        cost = entry.get("cost_rmb")
        if not isinstance(date, str) or not isinstance(cost, (int, float)):
            continue
        cost_f = float(cost)
        if not math.isfinite(cost_f) or cost_f < 0:
            continue
        daily_totals[date] = daily_totals.get(date, 0.0) + cost_f

    by_day: dict[str, list[str]] = {}
    for run_id, date in record_index.items():
        by_day.setdefault(date, []).append(run_id)

    per_run: dict[str, dict[str, float]] = {}
    for date, run_ids in by_day.items():
        if date not in daily_totals:
            # No cost telemetry exists for this date (Redis miss, scan
            # failure, expired keys). Emitting a 0-cost per-run row here
            # would make the cost gate silently pass on absent data —
            # see codex P5B-exit R1 P1 follow-up. Drop the runs from
            # the per-run map so has_data["fast_cost"] / ["slow_cost"]
            # correctly resolves to False upstream.
            continue
        n = len(run_ids)
        if n == 0:
            continue
        share = daily_totals[date] / n
        for run_id in run_ids:
            per_run[run_id] = {"date": date, "cost_rmb": round(share, 6)}
    return per_run


def split_runs_by_category(
    records: Iterable[Mapping[str, Any]],
    policy: UniversePolicy,
) -> dict[str, list[Mapping[str, Any]]]:
    """Partition analysis_records into ``fast`` and ``slow`` buckets.

    Categorisation reuses the same ``assign_category`` rule the live
    scheduler uses, so the gate report and the runtime cron always
    agree. Records missing ``stock_code`` or ``run_id`` are dropped
    (cannot be attributed to a cost row) and never fail-open into
    either bucket.
    """
    buckets: dict[str, list[Mapping[str, Any]]] = {"fast": [], "slow": []}
    for record in records:
        code = record.get("stock_code")
        run_id = record.get("run_id")
        if not isinstance(code, str) or not code:
            continue
        if not isinstance(run_id, str) or not run_id:
            # Without a run_id we cannot reconcile against per-run
            # cost telemetry, so the completeness check would always
            # fail. Drop here so a single malformed record does not
            # poison the whole gate.
            continue
        category = assign_category(code, policy)
        if category in buckets:
            buckets[category].append(record)
    return buckets


def _coerce_datetime(value: Any) -> datetime | None:
    """Coerce a Mongo datetime field into a tz-aware ``datetime``.

    Production callers persist records via ``model_dump(mode="json")``,
    which serialises ``datetime`` to an ISO-8601 string before Mongo
    writes it. Test-built dicts (and older code paths) hand us a real
    ``datetime``. We accept both shapes; anything else returns ``None``
    so the caller can drop the record from the latency aggregate
    rather than fail-open.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def _record_latency_seconds(record: Mapping[str, Any]) -> float | None:
    """Compute end-to-end latency from a record; None when unparseable.

    Accepts both raw ``datetime`` instances (test-built records) and
    ISO-8601 strings (live records persisted via ``model_dump(mode="json")``).
    Naive datetimes are rejected so a missing tz cannot silently shift
    the latency by hours.
    """
    started = _coerce_datetime(record.get("created_at"))
    completed = _coerce_datetime(record.get("completed_at"))
    if started is None or completed is None:
        return None
    if completed < started:
        return None
    return (completed - started).total_seconds()


def compute_exit_report(
    records: Iterable[Mapping[str, Any]],
    cost_entries: Iterable[Mapping[str, Any]],
    shadow_docs: Iterable[Mapping[str, Any]],
    policy: UniversePolicy,
    *,
    days: int,
) -> ExitGateReport:
    """Reduce raw inputs into an :class:`ExitGateReport`.

    Empty inputs flag ``has_data=False`` for the affected sub-gates so
    ``passes`` does not silently report success on silence.
    """
    record_list = [r for r in records if isinstance(r, Mapping)]
    cost_list = [c for c in cost_entries if isinstance(c, Mapping)]
    shadow_list = [s for s in shadow_docs if isinstance(s, Mapping)]

    record_index = {
        r["run_id"]: r["trade_date"]
        for r in record_list
        if isinstance(r.get("run_id"), str)
        and isinstance(r.get("trade_date"), str)
    }
    per_run_cost = aggregate_per_run_costs(
        cost_list, record_index=record_index
    )
    bucketed = split_runs_by_category(record_list, policy)

    # Cost p95 per category — caller consumes p95 because the SSoT
    # threshold is "per stock" worst-case rather than mean. We collect
    # via ``.get("run_id")`` so a malformed record (missing run_id) is
    # skipped instead of crashing the report (codex P5B-exit R2 P2).
    def _cost_for(record: Mapping[str, Any]) -> float | None:
        run_id = record.get("run_id")
        if not isinstance(run_id, str):
            return None
        slot = per_run_cost.get(run_id)
        if slot is None:
            return None
        return slot["cost_rmb"]

    fast_costs = [
        c for r in bucketed["fast"] if (c := _cost_for(r)) is not None
    ]
    slow_costs = [
        c for r in bucketed["slow"] if (c := _cost_for(r)) is not None
    ]
    fast_latencies = [
        sec
        for r in bucketed["fast"]
        if (sec := _record_latency_seconds(r)) is not None
    ]
    slow_latencies = [
        sec
        for r in bucketed["slow"]
        if (sec := _record_latency_seconds(r)) is not None
    ]
    daily_totals = {
        per["date"]: 0.0 for per in per_run_cost.values()
    }
    for per in per_run_cost.values():
        daily_totals[per["date"]] += per["cost_rmb"]
    daily_total_p95 = (
        _percentile(list(daily_totals.values()), 95) if daily_totals else 0.0
    )

    cost_metric = CostMetric(
        fast_p95_rmb=round(_percentile(fast_costs, 95), 4),
        slow_p95_rmb=round(_percentile(slow_costs, 95), 4),
        daily_total_rmb=round(daily_total_p95, 4),
        fast_runs=len(fast_costs),
        slow_runs=len(slow_costs),
    )
    latency_metric = LatencyMetric(
        fast_p95_sec=round(_percentile(fast_latencies, 95), 2),
        slow_p95_sec=round(_percentile(slow_latencies, 95), 2),
        fast_runs=len(fast_latencies),
        slow_runs=len(slow_latencies),
    )
    shadow_report = compute_shadow_report(shadow_list) if shadow_list else None

    # Cost telemetry must cover every in-window run that we have a
    # latency reading for — otherwise we can't confidently answer
    # "did fast/slow stay under ¥0.20/0.50 over the whole window".
    # Partial telemetry would let the gate quietly pass on whichever
    # subset Redis happens to have. Treat missing runs as no-data
    # rather than fail-open (codex P5B-exit R2 P2 follow-up).
    fast_total = len(bucketed["fast"])
    slow_total = len(bucketed["slow"])
    fast_cost_complete = (
        fast_total > 0 and cost_metric.fast_runs == fast_total
    )
    slow_cost_complete = (
        slow_total > 0 and cost_metric.slow_runs == slow_total
    )
    # daily_total spans BOTH buckets, so it is trustworthy only when
    # every populated bucket is fully covered. Allowing OR (codex
    # P5B-exit R6 UNRESOLVED follow-up: a fast bucket without cost
    # while slow has cost would otherwise pass) silently biases the
    # daily figure toward whichever side Redis happened to have.
    daily_total_has_data = (
        bool(daily_totals)
        and (fast_total + slow_total) > 0
        and (fast_total == 0 or fast_cost_complete)
        and (slow_total == 0 or slow_cost_complete)
    )

    has_data = {
        "fast_cost": fast_cost_complete,
        "slow_cost": slow_cost_complete,
        "daily_total": daily_total_has_data,
        "fast_latency": latency_metric.fast_runs > 0,
        "slow_latency": latency_metric.slow_runs > 0,
        "shadow": shadow_report is not None and shadow_report.total_pairs > 0,
    }

    passes = {
        "fast_cost": (
            has_data["fast_cost"]
            and cost_metric.fast_p95_rmb <= FAST_COST_PER_STOCK_RMB
        ),
        "slow_cost": (
            has_data["slow_cost"]
            and cost_metric.slow_p95_rmb <= SLOW_COST_PER_STOCK_RMB
        ),
        "daily_total": (
            has_data["daily_total"]
            and cost_metric.daily_total_rmb <= DAILY_TOTAL_RMB
        ),
        "fast_latency": (
            has_data["fast_latency"]
            and latency_metric.fast_p95_sec <= FAST_P95_LATENCY_SEC
        ),
        "slow_latency": (
            has_data["slow_latency"]
            and latency_metric.slow_p95_sec <= SLOW_P95_LATENCY_SEC
        ),
        "shadow_action_match": (
            has_data["shadow"]
            and shadow_report is not None
            and shadow_report.passes.get("action_match", False)
        ),
        "shadow_confidence_delta": (
            has_data["shadow"]
            and shadow_report is not None
            and shadow_report.passes.get("confidence_delta", False)
        ),
    }

    return ExitGateReport(
        days=days,
        cost=cost_metric,
        latency=latency_metric,
        shadow=shadow_report,
        has_data=has_data,
        passes=passes,
    )


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    q = max(0.0, min(100.0, q))
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q / 100 * (len(sorted_values) - 1)
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sorted_values[lower]
    weight = pos - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


_GATE_TO_DATA_KEY: dict[str, str] = {
    "fast_cost": "fast_cost",
    "slow_cost": "slow_cost",
    "daily_total": "daily_total",
    "fast_latency": "fast_latency",
    "slow_latency": "slow_latency",
    # Both shadow gates reuse the same has_data slot — there is one
    # shadow stream feeding two derived metrics.
    "shadow_action_match": "shadow",
    "shadow_confidence_delta": "shadow",
}


def render_markdown(report: ExitGateReport) -> str:
    """Render an :class:`ExitGateReport` as a single markdown gate table."""

    def fmt_pass(name: str) -> str:
        ok = report.passes.get(name, False)
        # Look up by full gate key — the previous ``name.split("_")[0]``
        # produced "fast" / "daily" which were not present in
        # ``has_data`` and made every no-data gate render as ❌
        # (codex P5B-exit R1 P3).
        data_key = _GATE_TO_DATA_KEY.get(name, name)
        has = report.has_data.get(data_key, True)
        if not has and not ok:
            return "⚠️ no-data"
        return "✅" if ok else "❌"

    lines = [
        "# Phase 5B Exit Gate",
        "",
        f"Window: last **{report.days}** days.",
        "",
        "| Gate | Threshold | Observed | Status |",
        "|------|-----------|---------:|:------:|",
        f"| fast_cost | ≤ {FAST_COST_PER_STOCK_RMB} RMB / stock | "
        f"p95 {report.cost.fast_p95_rmb:.4f} RMB ({report.cost.fast_runs} runs) | "
        f"{fmt_pass('fast_cost')} |",
        f"| slow_cost | ≤ {SLOW_COST_PER_STOCK_RMB} RMB / stock | "
        f"p95 {report.cost.slow_p95_rmb:.4f} RMB ({report.cost.slow_runs} runs) | "
        f"{fmt_pass('slow_cost')} |",
        f"| daily_total | ≤ {DAILY_TOTAL_RMB} RMB / day | "
        f"p95 {report.cost.daily_total_rmb:.4f} RMB | "
        f"{fmt_pass('daily_total')} |",
        f"| fast_latency | p95 ≤ {FAST_P95_LATENCY_SEC}s | "
        f"{report.latency.fast_p95_sec:.2f}s ({report.latency.fast_runs} runs) | "
        f"{fmt_pass('fast_latency')} |",
        f"| slow_latency | p95 ≤ {SLOW_P95_LATENCY_SEC}s | "
        f"{report.latency.slow_p95_sec:.2f}s ({report.latency.slow_runs} runs) | "
        f"{fmt_pass('slow_latency')} |",
        f"| shadow_action_match | ≥ {ACTION_MATCH_THRESHOLD} | "
        f"{report.shadow.action_match_rate if report.shadow else 0.0:.4f} "
        f"({report.shadow.total_pairs if report.shadow else 0} pairs) | "
        f"{fmt_pass('shadow_action_match')} |",
        f"| shadow_confidence_delta | < {CONFIDENCE_DELTA_THRESHOLD} | "
        # Escape the literal pipes around ``|Δ|`` — raw pipes split
        # the markdown row into extra columns (codex P5B-exit R2 P3).
        f"\\|Δ\\| mean "
        f"{report.shadow.confidence_delta_mean_abs if report.shadow else 0.0:.4f} | "
        f"{fmt_pass('shadow_confidence_delta')} |",
        "",
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "DAILY_TOTAL_RMB",
    "FAST_COST_PER_STOCK_RMB",
    "FAST_P95_LATENCY_SEC",
    "SLOW_COST_PER_STOCK_RMB",
    "SLOW_P95_LATENCY_SEC",
    "CostMetric",
    "ExitGateReport",
    "LatencyMetric",
    "aggregate_per_run_costs",
    "compute_exit_report",
    "render_markdown",
    "split_runs_by_category",
]
