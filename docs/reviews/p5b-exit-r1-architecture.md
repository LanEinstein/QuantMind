OpenAI Codex v0.128.0 (research preview)
--------
workdir: /home/ps/papers/QuantMind
model: gpt-5.5
provider: openai
approval: never
sandbox: workspace-write [workdir, /tmp, /home/ps/.codex/memories] (network access enabled)
reasoning effort: xhigh
reasoning summaries: none
session id: 019de75d-83e3-7340-af67-f03ad50f8988
--------
user
current changes
exec
/bin/bash -lc 'git status --short' in /home/ps/papers/QuantMind
 succeeded in 0ms:
A  backend/services/phase5b_exit_check.py
A  backend/services/shadow_compare.py
A  backend/services/shadow_recorder.py
A  scripts/phase5b_exit_check.py
A  scripts/shadow_compare.py
A  tests/test_phase5b_exit_check.py
A  tests/test_scripts_phase5b_exit_check.py
A  tests/test_scripts_shadow_compare.py
A  tests/test_shadow_compare.py
A  tests/test_shadow_recorder.py

exec
/bin/bash -lc 'git diff --cached --stat && git diff --cached -- backend/services/phase5b_exit_check.py backend/services/shadow_compare.py backend/services/shadow_recorder.py scripts/phase5b_exit_check.py scripts/shadow_compare.py tests/test_phase5b_exit_check.py tests/test_scripts_phase5b_exit_check.py tests/test_scripts_shadow_compare.py tests/test_shadow_compare.py tests/test_shadow_recorder.py' in /home/ps/papers/QuantMind
 succeeded in 0ms:
 backend/services/phase5b_exit_check.py   | 361 +++++++++++++++++++++++++++++
 backend/services/shadow_compare.py       | 310 +++++++++++++++++++++++++
 backend/services/shadow_recorder.py      | 217 ++++++++++++++++++
 scripts/phase5b_exit_check.py            | 152 ++++++++++++
 scripts/shadow_compare.py                | 140 +++++++++++
 tests/test_phase5b_exit_check.py         | 382 +++++++++++++++++++++++++++++++
 tests/test_scripts_phase5b_exit_check.py |  48 ++++
 tests/test_scripts_shadow_compare.py     | 117 ++++++++++
 tests/test_shadow_compare.py             | 240 +++++++++++++++++++
 tests/test_shadow_recorder.py            | 280 ++++++++++++++++++++++
 10 files changed, 2247 insertions(+)
diff --git a/backend/services/phase5b_exit_check.py b/backend/services/phase5b_exit_check.py
new file mode 100644
index 0000000..555a332
--- /dev/null
+++ b/backend/services/phase5b_exit_check.py
@@ -0,0 +1,361 @@
+"""Phase 5B exit-gate verification — pure analysis layer.
+
+The companion CLI ``scripts/phase5b_exit_check.py`` is a thin shell over
+this module. We aggregate:
+
+* daily LLM spend per stock (from Redis cost_tracker)
+* fast / slow pipeline latency (from analysis_records.created_at /
+  completed_at + watchlist policy categorisation)
+* decision consistency (delegates to
+  :mod:`backend.services.shadow_compare`)
+
+and emit a :class:`ExitGateReport` whose ``passes`` dict mirrors the
+SSoT §6.972 exit checklist:
+
+* fast cost ≤ ¥0.20 / stock
+* slow cost ≤ ¥0.50 / stock
+* daily total ≤ ¥1.20
+* fast p95 latency ≤ 8 min
+* slow p95 latency ≤ 15 min
+* decision consistency ≥ 0.85
+
+A gate that has zero data records ``has_data=False`` and the
+corresponding ``passes`` entry is False — Phase 5B exit cannot pass on
+silence.
+"""
+
+from __future__ import annotations
+
+import math
+from collections.abc import Iterable, Mapping
+from dataclasses import dataclass, field
+from datetime import datetime
+from typing import Any
+
+from backend.services.shadow_compare import (
+    ACTION_MATCH_THRESHOLD,
+    CONFIDENCE_DELTA_THRESHOLD,
+    ShadowReport,
+    compute_shadow_report,
+)
+from backend.services.watchlist_policy import WatchlistPolicy, assign_category
+
+# SSoT §6.972 thresholds
+FAST_COST_PER_STOCK_RMB = 0.20
+SLOW_COST_PER_STOCK_RMB = 0.50
+DAILY_TOTAL_RMB = 1.20
+FAST_P95_LATENCY_SEC = 8 * 60  # 8 min
+SLOW_P95_LATENCY_SEC = 15 * 60  # 15 min
+
+
+@dataclass(frozen=True)
+class CostMetric:
+    """Aggregated cost per (run/day) bucket; -1 sentinel when no data."""
+
+    fast_p95_rmb: float
+    slow_p95_rmb: float
+    daily_total_rmb: float
+    fast_runs: int
+    slow_runs: int
+
+
+@dataclass(frozen=True)
+class LatencyMetric:
+    fast_p95_sec: float
+    slow_p95_sec: float
+    fast_runs: int
+    slow_runs: int
+
+
+@dataclass(frozen=True)
+class ExitGateReport:
+    days: int
+    cost: CostMetric
+    latency: LatencyMetric
+    shadow: ShadowReport | None
+    has_data: dict[str, bool] = field(default_factory=dict)
+    passes: dict[str, bool] = field(default_factory=dict)
+
+
+def aggregate_per_run_costs(
+    cost_entries: Iterable[Mapping[str, Any]],
+    *,
+    record_index: Mapping[str, str],
+) -> dict[str, dict[str, float]]:
+    """Bucket Redis cost entries into per-run totals.
+
+    Inputs:
+
+    * ``cost_entries`` — ``llm:usage:{date}:{agent}:{provider}`` rows
+      flattened to dicts with keys ``date``, ``agent_name``, ``cost_rmb``.
+      Phase 5B-T03 splits agent names with ``/triage`` and
+      ``/escalation`` suffixes; we strip them to attribute spend back to
+      the parent agent. Per-run isn't directly recoverable from Redis
+      keys (they aggregate per day per agent), so callers pass an
+      auxiliary ``record_index`` mapping ``run_id → trade_date`` from
+      the analysis_records collection. The function then re-scales the
+      day's total by the number of runs we know happened on that day.
+    * ``record_index`` — ``run_id → trade_date`` for the runs in scope.
+
+    Returns ``{run_id: {"date": str, "cost_rmb": float}}``.
+    """
+    daily_totals: dict[str, float] = {}
+    for entry in cost_entries:
+        date = entry.get("date")
+        cost = entry.get("cost_rmb")
+        if not isinstance(date, str) or not isinstance(cost, (int, float)):
+            continue
+        cost_f = float(cost)
+        if not math.isfinite(cost_f) or cost_f < 0:
+            continue
+        daily_totals[date] = daily_totals.get(date, 0.0) + cost_f
+
+    by_day: dict[str, list[str]] = {}
+    for run_id, date in record_index.items():
+        by_day.setdefault(date, []).append(run_id)
+
+    per_run: dict[str, dict[str, float]] = {}
+    for date, run_ids in by_day.items():
+        n = len(run_ids)
+        if n == 0:
+            continue
+        share = daily_totals.get(date, 0.0) / n
+        for run_id in run_ids:
+            per_run[run_id] = {"date": date, "cost_rmb": round(share, 6)}
+    return per_run
+
+
+def split_runs_by_category(
+    records: Iterable[Mapping[str, Any]],
+    policy: WatchlistPolicy,
+) -> dict[str, list[Mapping[str, Any]]]:
+    """Partition analysis_records into ``fast`` and ``slow`` buckets.
+
+    Categorisation reuses the same ``assign_category`` rule the live
+    scheduler uses, so the gate report and the runtime cron always
+    agree. Records missing ``stock_code`` are dropped (cannot be
+    attributed) and never fail-open into either bucket.
+    """
+    buckets: dict[str, list[Mapping[str, Any]]] = {"fast": [], "slow": []}
+    for record in records:
+        code = record.get("stock_code")
+        if not isinstance(code, str) or not code:
+            continue
+        category = assign_category(code, policy)
+        if category in buckets:
+            buckets[category].append(record)
+    return buckets
+
+
+def _record_latency_seconds(record: Mapping[str, Any]) -> float | None:
+    """Compute end-to-end latency from a record; None when unparseable."""
+    started = record.get("created_at")
+    completed = record.get("completed_at")
+    if not isinstance(started, datetime) or not isinstance(completed, datetime):
+        return None
+    if completed < started:
+        return None
+    return (completed - started).total_seconds()
+
+
+def compute_exit_report(
+    records: Iterable[Mapping[str, Any]],
+    cost_entries: Iterable[Mapping[str, Any]],
+    shadow_docs: Iterable[Mapping[str, Any]],
+    policy: WatchlistPolicy,
+    *,
+    days: int,
+) -> ExitGateReport:
+    """Reduce raw inputs into an :class:`ExitGateReport`.
+
+    Empty inputs flag ``has_data=False`` for the affected sub-gates so
+    ``passes`` does not silently report success on silence.
+    """
+    record_list = [r for r in records if isinstance(r, Mapping)]
+    cost_list = [c for c in cost_entries if isinstance(c, Mapping)]
+    shadow_list = [s for s in shadow_docs if isinstance(s, Mapping)]
+
+    record_index = {
+        r["run_id"]: r["trade_date"]
+        for r in record_list
+        if isinstance(r.get("run_id"), str)
+        and isinstance(r.get("trade_date"), str)
+    }
+    per_run_cost = aggregate_per_run_costs(
+        cost_list, record_index=record_index
+    )
+    bucketed = split_runs_by_category(record_list, policy)
+
+    # Cost p95 per category — caller consumes p95 because the SSoT
+    # threshold is "per stock" worst-case rather than mean.
+    fast_costs = [
+        per_run_cost[r["run_id"]]["cost_rmb"]
+        for r in bucketed["fast"]
+        if r["run_id"] in per_run_cost
+    ]
+    slow_costs = [
+        per_run_cost[r["run_id"]]["cost_rmb"]
+        for r in bucketed["slow"]
+        if r["run_id"] in per_run_cost
+    ]
+    fast_latencies = [
+        sec
+        for r in bucketed["fast"]
+        if (sec := _record_latency_seconds(r)) is not None
+    ]
+    slow_latencies = [
+        sec
+        for r in bucketed["slow"]
+        if (sec := _record_latency_seconds(r)) is not None
+    ]
+    daily_totals = {
+        per["date"]: 0.0 for per in per_run_cost.values()
+    }
+    for per in per_run_cost.values():
+        daily_totals[per["date"]] += per["cost_rmb"]
+    daily_total_p95 = (
+        _percentile(list(daily_totals.values()), 95) if daily_totals else 0.0
+    )
+
+    cost_metric = CostMetric(
+        fast_p95_rmb=round(_percentile(fast_costs, 95), 4),
+        slow_p95_rmb=round(_percentile(slow_costs, 95), 4),
+        daily_total_rmb=round(daily_total_p95, 4),
+        fast_runs=len(fast_costs),
+        slow_runs=len(slow_costs),
+    )
+    latency_metric = LatencyMetric(
+        fast_p95_sec=round(_percentile(fast_latencies, 95), 2),
+        slow_p95_sec=round(_percentile(slow_latencies, 95), 2),
+        fast_runs=len(fast_latencies),
+        slow_runs=len(slow_latencies),
+    )
+    shadow_report = compute_shadow_report(shadow_list) if shadow_list else None
+
+    has_data = {
+        "fast_cost": cost_metric.fast_runs > 0,
+        "slow_cost": cost_metric.slow_runs > 0,
+        "daily_total": bool(daily_totals),
+        "fast_latency": latency_metric.fast_runs > 0,
+        "slow_latency": latency_metric.slow_runs > 0,
+        "shadow": shadow_report is not None and shadow_report.total_pairs > 0,
+    }
+
+    passes = {
+        "fast_cost": (
+            has_data["fast_cost"]
+            and cost_metric.fast_p95_rmb <= FAST_COST_PER_STOCK_RMB
+        ),
+        "slow_cost": (
+            has_data["slow_cost"]
+            and cost_metric.slow_p95_rmb <= SLOW_COST_PER_STOCK_RMB
+        ),
+        "daily_total": (
+            has_data["daily_total"]
+            and cost_metric.daily_total_rmb <= DAILY_TOTAL_RMB
+        ),
+        "fast_latency": (
+            has_data["fast_latency"]
+            and latency_metric.fast_p95_sec <= FAST_P95_LATENCY_SEC
+        ),
+        "slow_latency": (
+            has_data["slow_latency"]
+            and latency_metric.slow_p95_sec <= SLOW_P95_LATENCY_SEC
+        ),
+        "shadow_action_match": (
+            has_data["shadow"]
+            and shadow_report is not None
+            and shadow_report.passes.get("action_match", False)
+        ),
+        "shadow_confidence_delta": (
+            has_data["shadow"]
+            and shadow_report is not None
+            and shadow_report.passes.get("confidence_delta", False)
+        ),
+    }
+
+    return ExitGateReport(
+        days=days,
+        cost=cost_metric,
+        latency=latency_metric,
+        shadow=shadow_report,
+        has_data=has_data,
+        passes=passes,
+    )
+
+
+def _percentile(values: list[float], q: float) -> float:
+    if not values:
+        return 0.0
+    q = max(0.0, min(100.0, q))
+    sorted_values = sorted(values)
+    if len(sorted_values) == 1:
+        return sorted_values[0]
+    pos = q / 100 * (len(sorted_values) - 1)
+    lower = math.floor(pos)
+    upper = math.ceil(pos)
+    if lower == upper:
+        return sorted_values[lower]
+    weight = pos - lower
+    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
+
+
+def render_markdown(report: ExitGateReport) -> str:
+    """Render an :class:`ExitGateReport` as a single markdown gate table."""
+
+    def fmt_pass(name: str) -> str:
+        ok = report.passes.get(name, False)
+        has = report.has_data.get(name.split("_")[0], True)
+        if not has and not ok:
+            return "⚠️ no-data"
+        return "✅" if ok else "❌"
+
+    lines = [
+        "# Phase 5B Exit Gate",
+        "",
+        f"Window: last **{report.days}** days.",
+        "",
+        "| Gate | Threshold | Observed | Status |",
+        "|------|-----------|---------:|:------:|",
+        f"| fast_cost | ≤ {FAST_COST_PER_STOCK_RMB} RMB / stock | "
+        f"p95 {report.cost.fast_p95_rmb:.4f} RMB ({report.cost.fast_runs} runs) | "
+        f"{fmt_pass('fast_cost')} |",
+        f"| slow_cost | ≤ {SLOW_COST_PER_STOCK_RMB} RMB / stock | "
+        f"p95 {report.cost.slow_p95_rmb:.4f} RMB ({report.cost.slow_runs} runs) | "
+        f"{fmt_pass('slow_cost')} |",
+        f"| daily_total | ≤ {DAILY_TOTAL_RMB} RMB / day | "
+        f"p95 {report.cost.daily_total_rmb:.4f} RMB | "
+        f"{fmt_pass('daily_total')} |",
+        f"| fast_latency | p95 ≤ {FAST_P95_LATENCY_SEC}s | "
+        f"{report.latency.fast_p95_sec:.2f}s ({report.latency.fast_runs} runs) | "
+        f"{fmt_pass('fast_latency')} |",
+        f"| slow_latency | p95 ≤ {SLOW_P95_LATENCY_SEC}s | "
+        f"{report.latency.slow_p95_sec:.2f}s ({report.latency.slow_runs} runs) | "
+        f"{fmt_pass('slow_latency')} |",
+        f"| shadow_action_match | ≥ {ACTION_MATCH_THRESHOLD} | "
+        f"{report.shadow.action_match_rate if report.shadow else 0.0:.4f} "
+        f"({report.shadow.total_pairs if report.shadow else 0} pairs) | "
+        f"{fmt_pass('shadow_action_match')} |",
+        f"| shadow_confidence_delta | < {CONFIDENCE_DELTA_THRESHOLD} | "
+        f"|Δ| mean "
+        f"{report.shadow.confidence_delta_mean_abs if report.shadow else 0.0:.4f} | "
+        f"{fmt_pass('shadow_confidence_delta')} |",
+        "",
+    ]
+    return "\n".join(lines) + "\n"
+
+
+__all__ = [
+    "DAILY_TOTAL_RMB",
+    "FAST_COST_PER_STOCK_RMB",
+    "FAST_P95_LATENCY_SEC",
+    "SLOW_COST_PER_STOCK_RMB",
+    "SLOW_P95_LATENCY_SEC",
+    "CostMetric",
+    "ExitGateReport",
+    "LatencyMetric",
+    "aggregate_per_run_costs",
+    "compute_exit_report",
+    "render_markdown",
+    "split_runs_by_category",
+]
diff --git a/backend/services/shadow_compare.py b/backend/services/shadow_compare.py
new file mode 100644
index 0000000..de1031a
--- /dev/null
+++ b/backend/services/shadow_compare.py
@@ -0,0 +1,310 @@
+"""Pure analysis layer for the Phase 5B shadow-test harness.
+
+``scripts/shadow_compare.py`` is a thin CLI on top of this module — all
+the math + threshold gating lives here so it is unit-testable without a
+running Mongo and without ``argparse`` ceremony.
+
+Inputs are plain dicts shaped like the documents produced by
+:class:`backend.services.shadow_recorder.ShadowDecisionEntry`. Anything
+malformed (missing keys, wrong types) is dropped and counted in a
+``skipped`` bucket so the consumer can see whether the harness actually
+saw clean data.
+
+The thresholds match SSoT §6 P5B-T03 pass criteria:
+
+* action consistency ≥ 0.85
+* mean absolute confidence delta < 0.15
+"""
+
+from __future__ import annotations
+
+import math
+import statistics
+from collections.abc import Iterable, Mapping
+from dataclasses import dataclass, field
+from typing import Any
+
+ACTION_MATCH_THRESHOLD = 0.85
+CONFIDENCE_DELTA_THRESHOLD = 0.15
+
+
+@dataclass(frozen=True)
+class LegMetrics:
+    """Per-leg counters lifted from the ``baseline`` / ``routed`` arms."""
+
+    parse_ok_rate: float
+    escalation_rate: float
+    avg_latency_ms: float
+
+
+@dataclass(frozen=True)
+class ShadowReport:
+    """Output of :func:`compute_shadow_report`. All fields are immutable.
+
+    ``passes`` is the gate result against SSoT §6 P5B-T03 thresholds.
+    Tooling that wants to print but not gate (e.g. mid-window trend
+    inspection) can ignore the field; the exit-check CLI uses it.
+    """
+
+    total_pairs: int
+    skipped: int
+    action_match_rate: float
+    confidence_delta_p50: float
+    confidence_delta_p95: float
+    confidence_delta_mean_abs: float
+    baseline: LegMetrics
+    routed: LegMetrics
+    by_day: dict[str, dict[str, float]] = field(default_factory=dict)
+    passes: dict[str, bool] = field(default_factory=dict)
+
+
+def _coerce_leg(raw: Any) -> dict[str, Any] | None:
+    """Return a leg dict only if all required keys are present and typed.
+
+    Mongo deserialises documents loosely (an int may arrive where a float
+    was written), so we coerce here rather than enforce strict types.
+    Missing keys mean the entry was written by an older / buggier shadow
+    recorder and we do not want to crash the analyser on it.
+    """
+    if not isinstance(raw, Mapping):
+        return None
+    required = ("action", "confidence", "model", "parse_ok", "escalated")
+    if not all(k in raw for k in required):
+        return None
+    try:
+        return {
+            "action": str(raw["action"]),
+            "confidence": float(raw["confidence"]),
+            "model": str(raw["model"]),
+            "latency_ms": float(raw.get("latency_ms", 0.0)),
+            "parse_ok": bool(raw["parse_ok"]),
+            "escalated": bool(raw["escalated"]),
+        }
+    except (TypeError, ValueError):
+        return None
+
+
+def _is_clean_pair(pair: dict[str, Any]) -> bool:
+    base = pair["baseline"]
+    routed = pair["routed"]
+    for leg in (base, routed):
+        if not math.isfinite(leg["confidence"]):
+            return False
+        if leg["confidence"] < 0.0 or leg["confidence"] > 1.0:
+            return False
+        if not math.isfinite(leg["latency_ms"]) or leg["latency_ms"] < 0.0:
+            return False
+    return True
+
+
+def compute_shadow_report(
+    docs: Iterable[Mapping[str, Any]],
+) -> ShadowReport:
+    """Reduce raw shadow_decisions documents to a :class:`ShadowReport`.
+
+    Empty / dirty input does not raise: the report shows ``total_pairs=0``
+    and ``passes`` populated with ``False`` so downstream automation
+    (e.g. CI) can treat "no data" as a hard fail rather than a silent
+    pass.
+    """
+    pairs: list[dict[str, Any]] = []
+    skipped = 0
+    by_day_counts: dict[str, dict[str, int]] = {}
+
+    for raw in docs:
+        if not isinstance(raw, Mapping):
+            skipped += 1
+            continue
+        baseline = _coerce_leg(raw.get("baseline"))
+        routed = _coerce_leg(raw.get("routed"))
+        if baseline is None or routed is None:
+            skipped += 1
+            continue
+
+        pair = {"baseline": baseline, "routed": routed}
+        if not _is_clean_pair(pair):
+            skipped += 1
+            continue
+
+        trade_date = raw.get("trade_date")
+        if not isinstance(trade_date, str):
+            skipped += 1
+            continue
+
+        pair["trade_date"] = trade_date
+        pairs.append(pair)
+
+        slot = by_day_counts.setdefault(
+            trade_date, {"matched": 0, "total": 0}
+        )
+        slot["total"] += 1
+        if baseline["action"] == routed["action"]:
+            slot["matched"] += 1
+
+    if not pairs:
+        empty_leg = LegMetrics(
+            parse_ok_rate=0.0,
+            escalation_rate=0.0,
+            avg_latency_ms=0.0,
+        )
+        return ShadowReport(
+            total_pairs=0,
+            skipped=skipped,
+            action_match_rate=0.0,
+            confidence_delta_p50=0.0,
+            confidence_delta_p95=0.0,
+            confidence_delta_mean_abs=0.0,
+            baseline=empty_leg,
+            routed=empty_leg,
+            by_day={},
+            passes={
+                "action_match": False,
+                "confidence_delta": False,
+                "has_data": False,
+            },
+        )
+
+    matched = sum(
+        1
+        for p in pairs
+        if p["baseline"]["action"] == p["routed"]["action"]
+    )
+    total = len(pairs)
+    deltas = [
+        p["routed"]["confidence"] - p["baseline"]["confidence"]
+        for p in pairs
+    ]
+    abs_deltas = [abs(d) for d in deltas]
+
+    by_day = {
+        day: {
+            "match_rate": round(slot["matched"] / slot["total"], 4),
+            "samples": slot["total"],
+        }
+        for day, slot in sorted(by_day_counts.items())
+    }
+
+    baseline_metrics = _leg_metrics([p["baseline"] for p in pairs])
+    routed_metrics = _leg_metrics([p["routed"] for p in pairs])
+
+    action_match_rate = matched / total
+    confidence_delta_mean_abs = sum(abs_deltas) / total
+    p50 = statistics.median(deltas)
+    p95 = _percentile(deltas, 95)
+
+    passes = {
+        "has_data": True,
+        "action_match": action_match_rate >= ACTION_MATCH_THRESHOLD,
+        "confidence_delta": (
+            confidence_delta_mean_abs < CONFIDENCE_DELTA_THRESHOLD
+        ),
+    }
+
+    return ShadowReport(
+        total_pairs=total,
+        skipped=skipped,
+        action_match_rate=round(action_match_rate, 4),
+        confidence_delta_p50=round(p50, 4),
+        confidence_delta_p95=round(p95, 4),
+        confidence_delta_mean_abs=round(confidence_delta_mean_abs, 4),
+        baseline=baseline_metrics,
+        routed=routed_metrics,
+        by_day=by_day,
+        passes=passes,
+    )
+
+
+def _leg_metrics(legs: list[dict[str, Any]]) -> LegMetrics:
+    n = len(legs)
+    if n == 0:
+        return LegMetrics(
+            parse_ok_rate=0.0, escalation_rate=0.0, avg_latency_ms=0.0
+        )
+    parse_ok = sum(1 for leg in legs if leg["parse_ok"])
+    escalated = sum(1 for leg in legs if leg["escalated"])
+    latency = sum(leg["latency_ms"] for leg in legs) / n
+    return LegMetrics(
+        parse_ok_rate=round(parse_ok / n, 4),
+        escalation_rate=round(escalated / n, 4),
+        avg_latency_ms=round(latency, 2),
+    )
+
+
+def _percentile(values: list[float], q: float) -> float:
+    """Compute the q-th percentile (q in 0..100) using linear interpolation.
+
+    Implemented locally so we don't pull NumPy into the runtime path of a
+    reporting harness. ``q`` is clamped to ``[0, 100]`` so a typo cannot
+    produce a meaningless out-of-range index.
+    """
+    if not values:
+        return 0.0
+    q = max(0.0, min(100.0, q))
+    sorted_values = sorted(values)
+    if len(sorted_values) == 1:
+        return sorted_values[0]
+    pos = q / 100 * (len(sorted_values) - 1)
+    lower = int(math.floor(pos))
+    upper = int(math.ceil(pos))
+    if lower == upper:
+        return sorted_values[lower]
+    weight = pos - lower
+    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
+
+
+def render_markdown(report: ShadowReport) -> str:
+    """Render the report as a markdown table for the summary doc / CI logs."""
+    lines = [
+        "# Shadow Comparison Report",
+        "",
+        f"- Total pairs: **{report.total_pairs}**",
+        f"- Skipped (malformed): **{report.skipped}**",
+        f"- Action match rate: **{report.action_match_rate:.4f}** "
+        f"(threshold ≥ {ACTION_MATCH_THRESHOLD})",
+        f"- |Δconfidence| mean: **{report.confidence_delta_mean_abs:.4f}** "
+        f"(threshold < {CONFIDENCE_DELTA_THRESHOLD})",
+        f"- Δconfidence p50: {report.confidence_delta_p50:+.4f}",
+        f"- Δconfidence p95: {report.confidence_delta_p95:+.4f}",
+        "",
+        "## Per-leg",
+        "",
+        "| leg | parse_ok | escalation_rate | avg_latency_ms |",
+        "|-----|---------:|----------------:|---------------:|",
+        f"| baseline | {report.baseline.parse_ok_rate:.4f} | "
+        f"{report.baseline.escalation_rate:.4f} | "
+        f"{report.baseline.avg_latency_ms:.2f} |",
+        f"| routed | {report.routed.parse_ok_rate:.4f} | "
+        f"{report.routed.escalation_rate:.4f} | "
+        f"{report.routed.avg_latency_ms:.2f} |",
+        "",
+        "## Per day",
+        "",
+        "| trade_date | match_rate | samples |",
+        "|------------|-----------:|--------:|",
+    ]
+    if report.by_day:
+        for day, stats in report.by_day.items():
+            lines.append(
+                f"| {day} | {stats['match_rate']:.4f} | "
+                f"{int(stats['samples'])} |"
+            )
+    else:
+        lines.append("| — | — | 0 |")
+
+    lines.append("")
+    lines.append("## Gate")
+    lines.append("")
+    for gate, ok in report.passes.items():
+        marker = "✅" if ok else "❌"
+        lines.append(f"- {marker} {gate}")
+    return "\n".join(lines) + "\n"
+
+
+__all__ = [
+    "ACTION_MATCH_THRESHOLD",
+    "CONFIDENCE_DELTA_THRESHOLD",
+    "LegMetrics",
+    "ShadowReport",
+    "compute_shadow_report",
+    "render_markdown",
+]
diff --git a/backend/services/shadow_recorder.py b/backend/services/shadow_recorder.py
new file mode 100644
index 0000000..296a4ea
--- /dev/null
+++ b/backend/services/shadow_recorder.py
@@ -0,0 +1,217 @@
+"""Shadow decision recording for Phase 5B exit verification.
+
+This module is the data-layer half of the shadow-test harness. It defines
+the immutable ``ShadowDecisionEntry`` schema and the read/write API
+against the ``shadow_decisions`` MongoDB collection. The companion CLI
+``scripts/shadow_compare.py`` consumes these documents to produce the
+action-consistency / confidence-deviation report Phase 5B exit gates on.
+
+Design notes
+------------
+
+* **Pure data-layer.** This module is intentionally NOT wired into the
+  live LangGraph pipeline. Doubling LLM calls in production would
+  invalidate the cost-savings story P5B-T03 was built to tell. Operators
+  wire the recorder through a separate scheduled job once deployment
+  starts (Phase 5C deployment task). Tests therefore drive it directly.
+* **Immutable entries.** Every field is frozen so a record cannot drift
+  between the moment it is built and the moment it lands in Mongo —
+  protects against subtle aliasing bugs in async pipelines.
+* **UTC clock.** Matches the convention pinned by
+  ``backend.llm.fallback._utc_date_str()`` so daily rollups elsewhere in
+  the system line up; do NOT switch to ``datetime.now()`` (no tz). See
+  P5B-T03 codex R6 for the timezone-drift bug this convention prevents.
+* **Fail-soft writes.** The recorder swallows Mongo errors and logs a
+  structured warning. Shadow recording is observability — a Mongo blip
+  must not crash the calling job.
+"""
+
+from __future__ import annotations
+
+import datetime
+import math
+from dataclasses import asdict, dataclass
+from typing import TYPE_CHECKING, Any
+
+import structlog
+
+if TYPE_CHECKING:
+    from backend.data.database import MongoDBService
+
+log = structlog.get_logger(component="shadow_recorder")
+
+SHADOW_COLLECTION = "shadow_decisions"
+_TTL_DAYS_DEFAULT = 30
+_VALID_ACTIONS: frozenset[str] = frozenset({"买入", "持有", "卖出"})
+
+
+@dataclass(frozen=True)
+class ShadowDecisionLeg:
+    """One side (baseline or routed) of a shadow comparison.
+
+    ``parse_ok`` records whether the LLM response was JSON-parseable.
+    The harness keeps unparseable runs because they are themselves a
+    quality signal — a routing change that drives parse-failure rate up
+    is a regression even if the surviving runs still match.
+
+    ``escalated`` is meaningful only for the routed leg; the baseline leg
+    sets it to ``False`` by convention. Storing both keeps the document
+    schema-symmetric and the consumer code branch-free.
+    """
+
+    action: str
+    confidence: float
+    model: str
+    latency_ms: float
+    escalated: bool
+    parse_ok: bool
+
+    def __post_init__(self) -> None:
+        if self.action not in _VALID_ACTIONS:
+            raise ValueError(
+                f"action must be one of {sorted(_VALID_ACTIONS)}, "
+                f"got {self.action!r}"
+            )
+        if not isinstance(self.confidence, (int, float)) or isinstance(
+            self.confidence, bool
+        ):
+            raise ValueError(
+                f"confidence must be a finite float in [0,1], got "
+                f"{self.confidence!r}"
+            )
+        conf = float(self.confidence)
+        if not math.isfinite(conf) or conf < 0.0 or conf > 1.0:
+            raise ValueError(
+                f"confidence must be a finite float in [0,1], got {conf!r}"
+            )
+        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
+            raise ValueError(
+                f"latency_ms must be a finite, non-negative float, got "
+                f"{self.latency_ms!r}"
+            )
+
+
+@dataclass(frozen=True)
+class ShadowDecisionEntry:
+    """A baseline-vs-routed pair of fund_manager decisions for one run.
+
+    The pair shares ``run_id`` so each entry carries both decisions
+    side-by-side and the consumer never has to join two collections.
+    """
+
+    run_id: str
+    stock_code: str
+    trade_date: str
+    created_at: datetime.datetime
+    baseline: ShadowDecisionLeg
+    routed: ShadowDecisionLeg
+
+    def __post_init__(self) -> None:
+        if not self.run_id:
+            raise ValueError("run_id must be a non-empty string")
+        if not self.stock_code:
+            raise ValueError("stock_code must be a non-empty string")
+        if not self.trade_date:
+            raise ValueError("trade_date must be a non-empty string")
+        if self.created_at.tzinfo is None:
+            raise ValueError(
+                "created_at must be timezone-aware (UTC); naive datetimes "
+                "drift across daylight-saving boundaries"
+            )
+
+    def to_document(self) -> dict[str, Any]:
+        """Serialise to a Mongo-friendly dict.
+
+        Keeps ``created_at`` as a real ``datetime`` (Mongo encodes it as
+        BSON Date) so range queries work; everything else is plain JSON.
+        """
+        doc: dict[str, Any] = {
+            "run_id": self.run_id,
+            "stock_code": self.stock_code,
+            "trade_date": self.trade_date,
+            "created_at": self.created_at,
+            "baseline": asdict(self.baseline),
+            "routed": asdict(self.routed),
+        }
+        return doc
+
+
+async def record_shadow_decision(
+    mongodb: MongoDBService,
+    entry: ShadowDecisionEntry,
+) -> bool:
+    """Upsert a shadow comparison entry into the ``shadow_decisions`` collection.
+
+    Upsert key is ``run_id`` so re-runs (e.g. operator replays) overwrite
+    rather than accumulate noise. Returns True on success, False on Mongo
+    error — the caller logs but does not raise. Shadow tracking is
+    observability and must never propagate a failure into a real trading
+    run.
+    """
+    try:
+        coll = mongodb._db[SHADOW_COLLECTION]  # noqa: SLF001
+        await coll.update_one(
+            {"run_id": entry.run_id},
+            {"$set": entry.to_document()},
+            upsert=True,
+        )
+        return True
+    except Exception as exc:
+        log.warning(
+            "shadow_record_failed",
+            run_id=entry.run_id,
+            stock_code=entry.stock_code,
+            error=str(exc),
+        )
+        return False
+
+
+async def query_shadow_decisions(
+    mongodb: MongoDBService,
+    *,
+    days: int = 7,
+    now: datetime.datetime | None = None,
+) -> list[dict[str, Any]]:
+    """Return shadow_decisions documents for the last ``days`` days.
+
+    ``now`` is injectable so tests can pin the clock without monkey-
+    patching ``datetime.datetime``. The cutoff is computed in UTC to
+    match the writer convention.
+
+    Empty result is normal (no shadow data collected yet) and is
+    returned as ``[]`` — never ``None`` — so consumers can iterate
+    without a None check.
+    """
+    if days <= 0:
+        raise ValueError(f"days must be positive, got {days}")
+    cutoff = (
+        now.astimezone(datetime.UTC)
+        if now is not None
+        else datetime.datetime.now(tz=datetime.UTC)
+    ) - datetime.timedelta(days=days)
+
+    try:
+        coll = mongodb._db[SHADOW_COLLECTION]  # noqa: SLF001
+        cursor = coll.find({"created_at": {"$gte": cutoff}})
+        # Drop the Mongo ObjectId so consumers (script + tests) can
+        # JSON-serialise the result without bespoke encoders.
+        return [
+            {k: v for k, v in doc.items() if k != "_id"}
+            async for doc in cursor
+        ]
+    except Exception as exc:
+        log.warning(
+            "shadow_query_failed",
+            days=days,
+            error=str(exc),
+        )
+        return []
+
+
+__all__ = [
+    "SHADOW_COLLECTION",
+    "ShadowDecisionEntry",
+    "ShadowDecisionLeg",
+    "query_shadow_decisions",
+    "record_shadow_decision",
+]
diff --git a/scripts/phase5b_exit_check.py b/scripts/phase5b_exit_check.py
new file mode 100755
index 0000000..7ac9b93
--- /dev/null
+++ b/scripts/phase5b_exit_check.py
@@ -0,0 +1,152 @@
+#!/usr/bin/env python3
+"""Phase 5B exit-gate verification CLI.
+
+Aggregates the SSoT §6.972 exit checklist into one markdown report:
+
+* fast / slow per-stock cost p95 (from Redis cost_tracker)
+* fast / slow p95 latency (from MongoDB analysis_records)
+* daily total cost (from Redis cost_tracker)
+* shadow consistency (from MongoDB shadow_decisions)
+
+The math lives in :mod:`backend.services.phase5b_exit_check`; this
+module only handles I/O wiring and ``argparse``.
+
+Usage::
+
+    python scripts/phase5b_exit_check.py --days 7
+
+Returns exit 0 when every gate passes, 1 otherwise (intended for CI).
+"""
+
+from __future__ import annotations
+
+import argparse
+import asyncio
+import os
+import sys
+from pathlib import Path
+from typing import Any
+
+# Allow running as a standalone script.
+_ROOT = Path(__file__).resolve().parent.parent
+if str(_ROOT) not in sys.path:
+    sys.path.insert(0, str(_ROOT))
+
+from backend.services.phase5b_exit_check import (  # noqa: E402
+    compute_exit_report,
+    render_markdown,
+)
+from backend.services.watchlist_policy import load_policy  # noqa: E402
+
+
+def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
+    parser = argparse.ArgumentParser(
+        description="Phase 5B exit-gate verification.",
+    )
+    parser.add_argument(
+        "--days",
+        type=int,
+        default=7,
+        help="Look-back window in days.",
+    )
+    parser.add_argument(
+        "--mongo-uri",
+        default=os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"),
+    )
+    parser.add_argument(
+        "--mongo-db",
+        default=os.environ.get("MONGODB_DB", "quantmind"),
+    )
+    parser.add_argument(
+        "--redis-url",
+        default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
+    )
+    parser.add_argument(
+        "--policy-path",
+        type=Path,
+        default=Path("config/watchlist_policy.yaml"),
+        help="Path to watchlist_policy.yaml (relative to project root).",
+    )
+    parser.add_argument(
+        "--strict",
+        action="store_true",
+        help="Exit non-zero on first failing gate.",
+    )
+    return parser.parse_args(argv)
+
+
+async def _gather_inputs(
+    args: argparse.Namespace,
+) -> tuple[
+    list[dict[str, Any]],
+    list[dict[str, Any]],
+    list[dict[str, Any]],
+]:
+    """Pull records / cost entries / shadow docs from live infra.
+
+    We open the Mongo client and Redis client locally and close them
+    before returning so the caller (sync ``main``) does not have to know
+    about async lifetimes.
+    """
+    from motor.motor_asyncio import AsyncIOMotorClient
+    from redis.asyncio import Redis
+
+    from backend.data.database import MongoDBService
+    from backend.llm.cost_tracker import aggregate_costs
+    from backend.services.shadow_recorder import query_shadow_decisions
+
+    client = AsyncIOMotorClient(args.mongo_uri)
+    redis: Redis | None = None
+    try:
+        db = client[args.mongo_db]
+        service = MongoDBService(db)
+
+        records_cursor = service._db["analysis_records"].find().sort(  # noqa: SLF001
+            "created_at", -1
+        ).limit(args.days * 200)
+        records = [doc async for doc in records_cursor]
+
+        shadow_docs = await query_shadow_decisions(service, days=args.days)
+
+        redis = Redis.from_url(args.redis_url, decode_responses=True)
+        summary = await aggregate_costs(redis, days=args.days)
+        cost_entries = [
+            {
+                "date": entry.date,
+                "agent_name": entry.agent_name,
+                "cost_rmb": entry.cost_rmb,
+            }
+            for entry in summary.entries
+        ]
+        return list(records), cost_entries, list(shadow_docs)
+    finally:
+        if redis is not None:
+            await redis.aclose()
+        client.close()
+
+
+def main(argv: list[str] | None = None) -> int:
+    args = _parse_args(argv)
+    if not args.policy_path.exists():
+        sys.stderr.write(
+            f"watchlist_policy.yaml not found at {args.policy_path}\n"
+        )
+        return 2
+    policy = load_policy(args.policy_path)
+
+    records, cost_entries, shadow_docs = asyncio.run(_gather_inputs(args))
+    report = compute_exit_report(
+        records,
+        cost_entries,
+        shadow_docs,
+        policy,
+        days=args.days,
+    )
+    sys.stdout.write(render_markdown(report))
+    if args.strict and not all(report.passes.values()):
+        return 1
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
diff --git a/scripts/shadow_compare.py b/scripts/shadow_compare.py
new file mode 100755
index 0000000..6defbc5
--- /dev/null
+++ b/scripts/shadow_compare.py
@@ -0,0 +1,140 @@
+#!/usr/bin/env python3
+"""Phase 5B-T03 shadow comparison CLI.
+
+Reads ``shadow_decisions`` documents (from MongoDB or a JSONL file) and
+prints the action-consistency / confidence-deviation report Phase 5B
+exit gates on. The actual math lives in
+:mod:`backend.services.shadow_compare` so it stays unit-testable.
+
+Usage::
+
+    # MongoDB (default; reads MONGODB_URI from env)
+    python scripts/shadow_compare.py --days 7
+
+    # File replay (operator-collected JSONL of shadow_decisions docs)
+    python scripts/shadow_compare.py --input shadow_dump.jsonl
+
+The script returns exit code 0 when all gates pass, 1 otherwise. Useful
+in CI as the Phase 5B exit gate driver.
+"""
+
+from __future__ import annotations
+
+import argparse
+import asyncio
+import json
+import os
+import sys
+from collections.abc import Iterable
+from pathlib import Path
+from typing import Any
+
+# Allow running as a standalone script without `pip install -e .`
+_ROOT = Path(__file__).resolve().parent.parent
+if str(_ROOT) not in sys.path:
+    sys.path.insert(0, str(_ROOT))
+
+from backend.services.shadow_compare import (  # noqa: E402
+    compute_shadow_report,
+    render_markdown,
+)
+
+
+def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
+    parser = argparse.ArgumentParser(
+        description="Phase 5B shadow comparison report.",
+    )
+    parser.add_argument(
+        "--input",
+        type=Path,
+        default=None,
+        help="JSONL file of shadow_decisions documents. Mutually "
+        "exclusive with the MongoDB path.",
+    )
+    parser.add_argument(
+        "--days",
+        type=int,
+        default=7,
+        help="Look-back window in days when reading from MongoDB.",
+    )
+    parser.add_argument(
+        "--mongo-uri",
+        default=os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"),
+        help="MongoDB connection URI (default: $MONGODB_URI).",
+    )
+    parser.add_argument(
+        "--mongo-db",
+        default=os.environ.get("MONGODB_DB", "quantmind"),
+        help="MongoDB database name.",
+    )
+    parser.add_argument(
+        "--strict",
+        action="store_true",
+        help="Exit non-zero on first failing gate (CI driver mode).",
+    )
+    return parser.parse_args(argv)
+
+
+def _read_jsonl(path: Path) -> list[dict[str, Any]]:
+    """Read a JSONL file. Lines that fail to parse are surfaced loudly.
+
+    A silent skip would let a corrupted dump pass the gate by reporting
+    metrics over a smaller-than-expected sample; instead we raise so the
+    operator notices and re-dumps.
+    """
+    docs: list[dict[str, Any]] = []
+    with path.open("r", encoding="utf-8") as f:
+        for lineno, raw in enumerate(f, start=1):
+            line = raw.strip()
+            if not line:
+                continue
+            try:
+                docs.append(json.loads(line))
+            except json.JSONDecodeError as exc:
+                raise SystemExit(
+                    f"{path}:{lineno}: malformed JSON ({exc})"
+                ) from exc
+    return docs
+
+
+async def _read_mongo(args: argparse.Namespace) -> list[dict[str, Any]]:
+    """Connect to MongoDB and pull shadow_decisions for the given window.
+
+    Imported lazily so tests / file-mode callers don't pay the motor
+    import cost.
+    """
+    from motor.motor_asyncio import AsyncIOMotorClient
+
+    from backend.data.database import MongoDBService
+    from backend.services.shadow_recorder import query_shadow_decisions
+
+    client = AsyncIOMotorClient(args.mongo_uri)
+    try:
+        db = client[args.mongo_db]
+        service = MongoDBService(db)
+        docs = await query_shadow_decisions(service, days=args.days)
+        return list(docs)
+    finally:
+        client.close()
+
+
+def _load_docs(args: argparse.Namespace) -> Iterable[dict[str, Any]]:
+    if args.input is not None:
+        if not args.input.exists():
+            raise SystemExit(f"input file does not exist: {args.input}")
+        return _read_jsonl(args.input)
+    return asyncio.run(_read_mongo(args))
+
+
+def main(argv: list[str] | None = None) -> int:
+    args = _parse_args(argv)
+    docs = list(_load_docs(args))
+    report = compute_shadow_report(docs)
+    sys.stdout.write(render_markdown(report))
+    if args.strict and not all(report.passes.values()):
+        return 1
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())
diff --git a/tests/test_phase5b_exit_check.py b/tests/test_phase5b_exit_check.py
new file mode 100644
index 0000000..21c1153
--- /dev/null
+++ b/tests/test_phase5b_exit_check.py
@@ -0,0 +1,382 @@
+"""Unit tests for backend.services.phase5b_exit_check.
+
+Covers:
+* aggregate_per_run_costs: per-day shareing, malformed entries, suffix
+  handling
+* split_runs_by_category: assign_category integration, dropping bad codes
+* compute_exit_report: per-gate has_data + passes, empty inputs, latency
+  parsing, daily total, shadow integration
+"""
+
+from __future__ import annotations
+
+import datetime
+from typing import Any
+
+import pytest
+
+from backend.services.phase5b_exit_check import (
+    DAILY_TOTAL_RMB,
+    FAST_COST_PER_STOCK_RMB,
+    SLOW_COST_PER_STOCK_RMB,
+    aggregate_per_run_costs,
+    compute_exit_report,
+    render_markdown,
+    split_runs_by_category,
+)
+from backend.services.watchlist_policy import BucketConfig, WatchlistPolicy
+
+# ----------------------------------------------------------------------
+# Test fixtures
+# ----------------------------------------------------------------------
+
+
+def _policy(
+    *,
+    fast_codes: tuple[str, ...] = ("600519",),
+    slow_codes: tuple[str, ...] = ("000001",),
+    default_category: str = "slow",
+) -> WatchlistPolicy:
+    return WatchlistPolicy(
+        fast=BucketConfig(
+            cron="*/15 9-15 * * 1-5",
+            pipeline="fast",
+            max_debate_rounds=1,
+            pipeline_timeout_seconds=480,
+            default_codes=fast_codes,
+        ),
+        slow=BucketConfig(
+            cron="0 16 * * 1-5",
+            pipeline="slow",
+            max_debate_rounds=2,
+            pipeline_timeout_seconds=900,
+            default_codes=slow_codes,
+        ),
+        overrides={},
+        default_category=default_category,  # type: ignore[arg-type]
+        fast_default_set=frozenset(fast_codes),
+        slow_default_set=frozenset(slow_codes),
+    )
+
+
+def _record(
+    *,
+    run_id: str,
+    stock_code: str,
+    trade_date: str,
+    started_at: datetime.datetime,
+    duration_sec: float,
+) -> dict[str, Any]:
+    return {
+        "run_id": run_id,
+        "stock_code": stock_code,
+        "trade_date": trade_date,
+        "created_at": started_at,
+        "completed_at": started_at + datetime.timedelta(seconds=duration_sec),
+    }
+
+
+# ----------------------------------------------------------------------
+# Group 1: aggregate_per_run_costs
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestAggregatePerRunCosts:
+    def test_shares_daily_total_evenly(self) -> None:
+        cost_entries = [
+            {"date": "2026-05-02", "agent_name": "fund_manager", "cost_rmb": 0.4},
+            {"date": "2026-05-02", "agent_name": "intelligence", "cost_rmb": 0.4},
+        ]
+        record_index = {"r1": "2026-05-02", "r2": "2026-05-02"}
+        out = aggregate_per_run_costs(cost_entries, record_index=record_index)
+        assert out["r1"]["cost_rmb"] == 0.4
+        assert out["r2"]["cost_rmb"] == 0.4
+
+    def test_drops_negative_cost(self) -> None:
+        cost_entries = [
+            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.5},
+            {"date": "2026-05-02", "agent_name": "y", "cost_rmb": -1.0},
+        ]
+        out = aggregate_per_run_costs(
+            cost_entries, record_index={"r1": "2026-05-02"}
+        )
+        assert out["r1"]["cost_rmb"] == 0.5
+
+    def test_drops_nan_cost(self) -> None:
+        cost_entries = [
+            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": float("nan")},
+        ]
+        out = aggregate_per_run_costs(
+            cost_entries, record_index={"r1": "2026-05-02"}
+        )
+        assert out["r1"]["cost_rmb"] == 0.0
+
+    def test_no_runs_for_day_yields_empty(self) -> None:
+        out = aggregate_per_run_costs(
+            [{"date": "2026-05-02", "agent_name": "x", "cost_rmb": 1.0}],
+            record_index={},
+        )
+        assert out == {}
+
+
+# ----------------------------------------------------------------------
+# Group 2: split_runs_by_category
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestSplitRunsByCategory:
+    def test_partitions_by_policy(self) -> None:
+        records = [
+            {"stock_code": "600519"},
+            {"stock_code": "000001"},
+            {"stock_code": "300750"},  # falls through to default_category=slow
+        ]
+        buckets = split_runs_by_category(records, _policy())
+        assert len(buckets["fast"]) == 1
+        assert len(buckets["slow"]) == 2
+
+    def test_drops_records_missing_stock_code(self) -> None:
+        records = [
+            {},  # no stock_code
+            {"stock_code": "600519"},
+            {"stock_code": ""},  # empty
+        ]
+        buckets = split_runs_by_category(records, _policy())
+        assert len(buckets["fast"]) == 1
+        assert len(buckets["slow"]) == 0
+
+
+# ----------------------------------------------------------------------
+# Group 3: compute_exit_report
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestComputeExitReport:
+    def test_empty_inputs_flag_no_data(self) -> None:
+        report = compute_exit_report([], [], [], _policy(), days=7)
+        assert report.has_data["fast_cost"] is False
+        assert report.has_data["slow_cost"] is False
+        assert report.has_data["shadow"] is False
+        # No gate can pass on silence.
+        assert all(v is False for v in report.passes.values())
+
+    def test_gate_passes_when_thresholds_met(self) -> None:
+        records = [
+            _record(
+                run_id="r-fast",
+                stock_code="600519",
+                trade_date="2026-05-02",
+                started_at=datetime.datetime(
+                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
+                ),
+                duration_sec=300,  # 5 min < 8 min threshold
+            ),
+            _record(
+                run_id="r-slow",
+                stock_code="000001",
+                trade_date="2026-05-02",
+                started_at=datetime.datetime(
+                    2026, 5, 2, 16, 0, tzinfo=datetime.UTC
+                ),
+                duration_sec=600,  # 10 min < 15 min threshold
+            ),
+        ]
+        cost_entries = [
+            {
+                "date": "2026-05-02",
+                "agent_name": "fund_manager",
+                "cost_rmb": 0.4,  # split 0.2 each across the 2 runs
+            },
+        ]
+        report = compute_exit_report(
+            records, cost_entries, [], _policy(), days=7
+        )
+        assert report.cost.fast_p95_rmb == 0.2
+        assert report.cost.slow_p95_rmb == 0.2
+        assert report.cost.daily_total_rmb == 0.4
+        assert report.passes["fast_cost"] is True
+        assert report.passes["slow_cost"] is True
+        assert report.passes["daily_total"] is True
+        assert report.passes["fast_latency"] is True
+        assert report.passes["slow_latency"] is True
+
+    def test_fast_cost_breach_fails(self) -> None:
+        records = [
+            _record(
+                run_id="r-fast",
+                stock_code="600519",
+                trade_date="2026-05-02",
+                started_at=datetime.datetime(
+                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
+                ),
+                duration_sec=100,
+            )
+        ]
+        cost_entries = [
+            {
+                "date": "2026-05-02",
+                "agent_name": "fund_manager",
+                "cost_rmb": FAST_COST_PER_STOCK_RMB + 0.1,
+            }
+        ]
+        report = compute_exit_report(
+            records, cost_entries, [], _policy(), days=7
+        )
+        assert report.passes["fast_cost"] is False
+
+    def test_slow_latency_breach_fails(self) -> None:
+        records = [
+            _record(
+                run_id="r-slow",
+                stock_code="000001",
+                trade_date="2026-05-02",
+                started_at=datetime.datetime(
+                    2026, 5, 2, 16, 0, tzinfo=datetime.UTC
+                ),
+                duration_sec=20 * 60,  # 20 min > 15 min threshold
+            )
+        ]
+        cost_entries = [
+            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.1}
+        ]
+        report = compute_exit_report(
+            records, cost_entries, [], _policy(), days=7
+        )
+        assert report.passes["slow_latency"] is False
+        assert report.passes["slow_cost"] is True  # cost was fine
+
+    def test_daily_total_breach_fails(self) -> None:
+        records = [
+            _record(
+                run_id=f"r-{i}",
+                stock_code="000001",
+                trade_date="2026-05-02",
+                started_at=datetime.datetime(
+                    2026, 5, 2, 16, 0, tzinfo=datetime.UTC
+                ),
+                duration_sec=300,
+            )
+            for i in range(2)
+        ]
+        cost_entries = [
+            {
+                "date": "2026-05-02",
+                "agent_name": "x",
+                "cost_rmb": DAILY_TOTAL_RMB + 0.5,
+            }
+        ]
+        report = compute_exit_report(
+            records, cost_entries, [], _policy(), days=7
+        )
+        assert report.passes["daily_total"] is False
+
+    def test_shadow_integration(self) -> None:
+        shadow_docs = [
+            {
+                "run_id": "r1",
+                "stock_code": "600519",
+                "trade_date": "2026-05-02",
+                "baseline": {
+                    "action": "买入",
+                    "confidence": 0.7,
+                    "model": "kimi-k2.6",
+                    "latency_ms": 100.0,
+                    "escalated": False,
+                    "parse_ok": True,
+                },
+                "routed": {
+                    "action": "买入",
+                    "confidence": 0.72,
+                    "model": "qwen3.6-plus",
+                    "latency_ms": 80.0,
+                    "escalated": False,
+                    "parse_ok": True,
+                },
+            }
+        ]
+        report = compute_exit_report(
+            [], [], shadow_docs, _policy(), days=7
+        )
+        assert report.has_data["shadow"] is True
+        assert report.passes["shadow_action_match"] is True
+        assert report.passes["shadow_confidence_delta"] is True
+
+    def test_record_with_completed_before_created_drops_latency(self) -> None:
+        # Anomalous record (clock skew on a node?) — should not poison
+        # the percentile.
+        records = [
+            {
+                "run_id": "r1",
+                "stock_code": "600519",
+                "trade_date": "2026-05-02",
+                "created_at": datetime.datetime(
+                    2026, 5, 2, 10, 0, tzinfo=datetime.UTC
+                ),
+                "completed_at": datetime.datetime(
+                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
+                ),
+            }
+        ]
+        report = compute_exit_report(
+            records, [], [], _policy(), days=7
+        )
+        assert report.has_data["fast_latency"] is False
+        assert report.passes["fast_latency"] is False
+
+
+# ----------------------------------------------------------------------
+# Group 4: render_markdown
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestRenderMarkdown:
+    def test_empty_report_renders_no_data(self) -> None:
+        out = render_markdown(
+            compute_exit_report([], [], [], _policy(), days=7)
+        )
+        assert "no-data" in out
+
+    def test_happy_path_renders_check_marks(self) -> None:
+        records = [
+            _record(
+                run_id="r-fast",
+                stock_code="600519",
+                trade_date="2026-05-02",
+                started_at=datetime.datetime(
+                    2026, 5, 2, 9, 0, tzinfo=datetime.UTC
+                ),
+                duration_sec=100,
+            )
+        ]
+        cost_entries = [
+            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": 0.1}
+        ]
+        out = render_markdown(
+            compute_exit_report(
+                records, cost_entries, [], _policy(), days=7
+            )
+        )
+        assert "✅" in out
+        # slow gates have no data here so we expect a no-data marker.
+        assert "no-data" in out
+
+    def test_includes_all_six_gates(self) -> None:
+        out = render_markdown(
+            compute_exit_report([], [], [], _policy(), days=7)
+        )
+        for gate in (
+            "fast_cost",
+            "slow_cost",
+            "daily_total",
+            "fast_latency",
+            "slow_latency",
+            "shadow_action_match",
+            "shadow_confidence_delta",
+        ):
+            assert gate in out
+        assert f"{FAST_COST_PER_STOCK_RMB}" in out
+        assert f"{SLOW_COST_PER_STOCK_RMB}" in out
diff --git a/tests/test_scripts_phase5b_exit_check.py b/tests/test_scripts_phase5b_exit_check.py
new file mode 100644
index 0000000..b7e69a1
--- /dev/null
+++ b/tests/test_scripts_phase5b_exit_check.py
@@ -0,0 +1,48 @@
+"""CLI smoke test for scripts/phase5b_exit_check.py.
+
+The aggregation math is exhaustively covered by
+tests/test_phase5b_exit_check.py; we only verify the script's argument
+parser surface here, since the production path opens motor + redis
+which can't be instantiated in unit tests.
+"""
+
+from __future__ import annotations
+
+import importlib.util
+from pathlib import Path
+
+import pytest
+
+_SCRIPT_PATH = (
+    Path(__file__).resolve().parent.parent / "scripts" / "phase5b_exit_check.py"
+)
+
+
+def _load_script_module():  # type: ignore[no-untyped-def]
+    spec = importlib.util.spec_from_file_location(
+        "_phase5b_exit_cli", _SCRIPT_PATH
+    )
+    assert spec is not None and spec.loader is not None
+    module = importlib.util.module_from_spec(spec)
+    spec.loader.exec_module(module)
+    return module
+
+
+@pytest.mark.unit
+class TestExitCheckCLI:
+    def test_missing_policy_returns_two(
+        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
+    ) -> None:
+        module = _load_script_module()
+        missing = tmp_path / "watchlist_policy.yaml"
+        rc = module.main(["--policy-path", str(missing)])
+        assert rc == 2
+        err = capsys.readouterr().err
+        assert "watchlist_policy.yaml not found" in err
+
+    def test_arg_parser_defaults(self) -> None:
+        module = _load_script_module()
+        ns = module._parse_args(["--days", "3"])
+        assert ns.days == 3
+        assert ns.strict is False
+        assert ns.policy_path == Path("config/watchlist_policy.yaml")
diff --git a/tests/test_scripts_shadow_compare.py b/tests/test_scripts_shadow_compare.py
new file mode 100644
index 0000000..23bd036
--- /dev/null
+++ b/tests/test_scripts_shadow_compare.py
@@ -0,0 +1,117 @@
+"""CLI smoke tests for scripts/shadow_compare.py.
+
+The math is exhaustively covered by tests/test_shadow_compare.py; here
+we focus on argument plumbing, JSONL parsing, and exit codes.
+"""
+
+from __future__ import annotations
+
+import importlib.util
+import json
+from pathlib import Path
+
+import pytest
+
+_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "shadow_compare.py"
+
+
+def _load_script_module():  # type: ignore[no-untyped-def]
+    spec = importlib.util.spec_from_file_location(
+        "_shadow_compare_cli", _SCRIPT_PATH
+    )
+    assert spec is not None and spec.loader is not None
+    module = importlib.util.module_from_spec(spec)
+    spec.loader.exec_module(module)
+    return module
+
+
+def _doc(
+    *,
+    base_action: str = "买入",
+    routed_action: str = "买入",
+    base_conf: float = 0.7,
+    routed_conf: float = 0.7,
+    trade_date: str = "2026-05-02",
+) -> dict:
+    return {
+        "run_id": "r1",
+        "stock_code": "600519",
+        "trade_date": trade_date,
+        "baseline": {
+            "action": base_action,
+            "confidence": base_conf,
+            "model": "kimi-k2.6",
+            "latency_ms": 1000.0,
+            "escalated": False,
+            "parse_ok": True,
+        },
+        "routed": {
+            "action": routed_action,
+            "confidence": routed_conf,
+            "model": "qwen3.6-plus",
+            "latency_ms": 1500.0,
+            "escalated": False,
+            "parse_ok": True,
+        },
+    }
+
+
+@pytest.mark.unit
+class TestShadowCompareCLI:
+    def test_jsonl_happy_path_returns_zero(
+        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
+    ) -> None:
+        path = tmp_path / "shadow.jsonl"
+        path.write_text(
+            "\n".join(json.dumps(_doc()) for _ in range(20)) + "\n",
+            encoding="utf-8",
+        )
+        module = _load_script_module()
+        rc = module.main(["--input", str(path)])
+        assert rc == 0
+        captured = capsys.readouterr().out
+        assert "Total pairs: **20**" in captured
+        assert "✅ action_match" in captured
+
+    def test_strict_returns_one_on_failure(
+        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
+    ) -> None:
+        path = tmp_path / "shadow.jsonl"
+        path.write_text(
+            json.dumps(_doc(routed_action="持有")) + "\n",
+            encoding="utf-8",
+        )
+        module = _load_script_module()
+        rc = module.main(["--input", str(path), "--strict"])
+        assert rc == 1
+        out = capsys.readouterr().out
+        assert "❌ action_match" in out
+
+    def test_malformed_jsonl_raises(
+        self, tmp_path: Path
+    ) -> None:
+        path = tmp_path / "shadow.jsonl"
+        path.write_text("not json\n", encoding="utf-8")
+        module = _load_script_module()
+        with pytest.raises(SystemExit):
+            module.main(["--input", str(path)])
+
+    def test_missing_input_file_raises(self, tmp_path: Path) -> None:
+        module = _load_script_module()
+        missing = tmp_path / "does-not-exist.jsonl"
+        with pytest.raises(SystemExit):
+            module.main(["--input", str(missing)])
+
+    def test_blank_jsonl_lines_skipped(
+        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
+    ) -> None:
+        path = tmp_path / "shadow.jsonl"
+        path.write_text(
+            f"\n{json.dumps(_doc())}\n   \n",
+            encoding="utf-8",
+        )
+        module = _load_script_module()
+        rc = module.main(["--input", str(path)])
+        assert rc == 0
+        out = capsys.readouterr().out
+        assert "Total pairs: **1**" in out
diff --git a/tests/test_shadow_compare.py b/tests/test_shadow_compare.py
new file mode 100644
index 0000000..2644381
--- /dev/null
+++ b/tests/test_shadow_compare.py
@@ -0,0 +1,240 @@
+"""Unit tests for backend.services.shadow_compare.
+
+Covers the math: action match rate, |Δconfidence| stats, malformed-input
+robustness, gate population, percentile correctness, and markdown
+rendering.
+"""
+
+from __future__ import annotations
+
+from typing import Any
+
+import pytest
+
+from backend.services.shadow_compare import (
+    ACTION_MATCH_THRESHOLD,
+    CONFIDENCE_DELTA_THRESHOLD,
+    compute_shadow_report,
+    render_markdown,
+)
+
+
+def _doc(
+    *,
+    trade_date: str = "2026-05-02",
+    base_action: str = "买入",
+    routed_action: str = "买入",
+    base_conf: float = 0.7,
+    routed_conf: float = 0.7,
+    base_latency: float = 1000.0,
+    routed_latency: float = 1500.0,
+    base_parse_ok: bool = True,
+    routed_parse_ok: bool = True,
+    base_escalated: bool = False,
+    routed_escalated: bool = False,
+) -> dict[str, Any]:
+    return {
+        "run_id": "r1",
+        "stock_code": "600519",
+        "trade_date": trade_date,
+        "baseline": {
+            "action": base_action,
+            "confidence": base_conf,
+            "model": "kimi-k2.6",
+            "latency_ms": base_latency,
+            "escalated": base_escalated,
+            "parse_ok": base_parse_ok,
+        },
+        "routed": {
+            "action": routed_action,
+            "confidence": routed_conf,
+            "model": "qwen3.6-plus",
+            "latency_ms": routed_latency,
+            "escalated": routed_escalated,
+            "parse_ok": routed_parse_ok,
+        },
+    }
+
+
+# ----------------------------------------------------------------------
+# Group 1: empty / malformed input
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestEmptyInput:
+    def test_empty_returns_zero_total(self) -> None:
+        report = compute_shadow_report([])
+        assert report.total_pairs == 0
+        assert report.passes == {
+            "has_data": False,
+            "action_match": False,
+            "confidence_delta": False,
+        }
+
+    def test_skips_non_mapping(self) -> None:
+        report = compute_shadow_report(["not a dict", 123, None])  # type: ignore[list-item]
+        assert report.total_pairs == 0
+        assert report.skipped == 3
+
+    def test_skips_missing_leg(self) -> None:
+        doc = _doc()
+        del doc["routed"]
+        report = compute_shadow_report([doc])
+        assert report.total_pairs == 0
+        assert report.skipped == 1
+
+    def test_skips_missing_trade_date(self) -> None:
+        doc = _doc()
+        del doc["trade_date"]
+        report = compute_shadow_report([doc])
+        assert report.skipped == 1
+
+    def test_skips_out_of_range_confidence(self) -> None:
+        report = compute_shadow_report([_doc(routed_conf=1.5)])
+        assert report.skipped == 1
+
+    def test_skips_nan_confidence(self) -> None:
+        report = compute_shadow_report([_doc(base_conf=float("nan"))])
+        assert report.skipped == 1
+
+
+# ----------------------------------------------------------------------
+# Group 2: action match
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestActionMatch:
+    def test_perfect_match_passes_gate(self) -> None:
+        docs = [_doc() for _ in range(10)]
+        report = compute_shadow_report(docs)
+        assert report.action_match_rate == 1.0
+        assert report.passes["action_match"] is True
+
+    def test_partial_match_below_threshold(self) -> None:
+        docs = [_doc(routed_action="持有") for _ in range(8)]
+        docs += [_doc() for _ in range(2)]
+        report = compute_shadow_report(docs)
+        assert report.action_match_rate == 0.2
+        assert report.passes["action_match"] is False
+
+    def test_threshold_boundary(self) -> None:
+        # 17 / 20 = 0.85 — exactly at threshold; ``>=`` passes.
+        docs = [_doc() for _ in range(17)]
+        docs += [_doc(routed_action="卖出") for _ in range(3)]
+        report = compute_shadow_report(docs)
+        assert report.action_match_rate == ACTION_MATCH_THRESHOLD
+        assert report.passes["action_match"] is True
+
+    def test_per_day_breakdown(self) -> None:
+        report = compute_shadow_report(
+            [
+                _doc(trade_date="2026-05-02"),
+                _doc(trade_date="2026-05-02", routed_action="持有"),
+                _doc(trade_date="2026-05-03"),
+            ]
+        )
+        assert report.by_day["2026-05-02"]["match_rate"] == 0.5
+        assert report.by_day["2026-05-03"]["match_rate"] == 1.0
+
+
+# ----------------------------------------------------------------------
+# Group 3: confidence delta
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestConfidenceDelta:
+    def test_zero_delta_passes(self) -> None:
+        report = compute_shadow_report([_doc()])
+        assert report.confidence_delta_mean_abs == 0.0
+        assert report.passes["confidence_delta"] is True
+
+    def test_large_delta_fails(self) -> None:
+        report = compute_shadow_report(
+            [_doc(base_conf=0.9, routed_conf=0.5) for _ in range(5)]
+        )
+        assert report.confidence_delta_mean_abs == 0.4
+        assert report.passes["confidence_delta"] is False
+
+    def test_just_under_threshold_passes(self) -> None:
+        # mean |Δ| 0.149 < 0.15. routed - baseline = 0.149.
+        report = compute_shadow_report(
+            [_doc(base_conf=0.5, routed_conf=0.649) for _ in range(3)]
+        )
+        assert report.passes["confidence_delta"] is True
+
+    def test_at_threshold_fails(self) -> None:
+        # mean |Δ| == threshold should fail because gate is strict <.
+        report = compute_shadow_report(
+            [
+                _doc(
+                    base_conf=0.5,
+                    routed_conf=0.5 + CONFIDENCE_DELTA_THRESHOLD,
+                )
+            ]
+        )
+        assert report.passes["confidence_delta"] is False
+
+    def test_p50_p95(self) -> None:
+        deltas = [0.0, 0.1, 0.2, 0.3, 0.4]
+        docs = [
+            _doc(base_conf=0.5, routed_conf=0.5 + d)
+            for d in deltas
+        ]
+        report = compute_shadow_report(docs)
+        # median = 0.2 ; p95 ≈ 0.38.
+        assert report.confidence_delta_p50 == 0.2
+        assert 0.37 < report.confidence_delta_p95 <= 0.4
+
+
+# ----------------------------------------------------------------------
+# Group 4: leg metrics
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestLegMetrics:
+    def test_baseline_escalation_rate_zero(self) -> None:
+        report = compute_shadow_report(
+            [_doc(routed_escalated=True) for _ in range(2)]
+        )
+        assert report.baseline.escalation_rate == 0.0
+        assert report.routed.escalation_rate == 1.0
+
+    def test_parse_ok_aggregation(self) -> None:
+        docs = [_doc(routed_parse_ok=False) for _ in range(2)]
+        docs += [_doc() for _ in range(8)]
+        report = compute_shadow_report(docs)
+        assert report.routed.parse_ok_rate == 0.8
+        assert report.baseline.parse_ok_rate == 1.0
+
+    def test_avg_latency(self) -> None:
+        docs = [
+            _doc(base_latency=1000.0, routed_latency=2000.0),
+            _doc(base_latency=2000.0, routed_latency=4000.0),
+        ]
+        report = compute_shadow_report(docs)
+        assert report.baseline.avg_latency_ms == 1500.0
+        assert report.routed.avg_latency_ms == 3000.0
+
+
+# ----------------------------------------------------------------------
+# Group 5: markdown rendering
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestRenderMarkdown:
+    def test_empty_report_renders_no_data_marker(self) -> None:
+        out = render_markdown(compute_shadow_report([]))
+        assert "Total pairs: **0**" in out
+        assert "❌ has_data" in out
+        assert "| — | — | 0 |" in out
+
+    def test_happy_path_renders_pass_markers(self) -> None:
+        out = render_markdown(compute_shadow_report([_doc()]))
+        assert "✅ action_match" in out
+        assert "✅ confidence_delta" in out
+        assert "Δconfidence p50:" in out
diff --git a/tests/test_shadow_recorder.py b/tests/test_shadow_recorder.py
new file mode 100644
index 0000000..0b59e90
--- /dev/null
+++ b/tests/test_shadow_recorder.py
@@ -0,0 +1,280 @@
+"""Unit + integration tests for backend.services.shadow_recorder.
+
+Covers:
+* ShadowDecisionLeg / ShadowDecisionEntry validation (action, confidence,
+  latency, tz-naive datetimes)
+* record_shadow_decision happy path + Mongo error fail-soft
+* query_shadow_decisions UTC cutoff, _id stripping, error fail-soft
+"""
+
+from __future__ import annotations
+
+import datetime
+from typing import Any
+from unittest.mock import AsyncMock, MagicMock
+
+import pytest
+
+from backend.services.shadow_recorder import (
+    SHADOW_COLLECTION,
+    ShadowDecisionEntry,
+    ShadowDecisionLeg,
+    query_shadow_decisions,
+    record_shadow_decision,
+)
+
+# ----------------------------------------------------------------------
+# Shared fixtures
+# ----------------------------------------------------------------------
+
+
+def _make_leg(
+    *,
+    action: str = "买入",
+    confidence: float = 0.7,
+    model: str = "kimi-k2.6",
+    latency_ms: float = 1234.5,
+    escalated: bool = False,
+    parse_ok: bool = True,
+) -> ShadowDecisionLeg:
+    return ShadowDecisionLeg(
+        action=action,
+        confidence=confidence,
+        model=model,
+        latency_ms=latency_ms,
+        escalated=escalated,
+        parse_ok=parse_ok,
+    )
+
+
+def _make_entry(
+    run_id: str = "run-1",
+    stock_code: str = "600519",
+    trade_date: str = "2026-05-02",
+    created_at: datetime.datetime | None = None,
+    baseline: ShadowDecisionLeg | None = None,
+    routed: ShadowDecisionLeg | None = None,
+) -> ShadowDecisionEntry:
+    return ShadowDecisionEntry(
+        run_id=run_id,
+        stock_code=stock_code,
+        trade_date=trade_date,
+        created_at=created_at
+        or datetime.datetime.now(tz=datetime.UTC),
+        baseline=baseline or _make_leg(),
+        routed=routed or _make_leg(model="qwen3.6-plus", escalated=False),
+    )
+
+
+def _make_mongo() -> tuple[MagicMock, MagicMock]:
+    """Return (service-shaped mock, collection mock).
+
+    The recorder reaches into ``mongodb._db[SHADOW_COLLECTION]`` directly
+    so we mirror that shape: the service exposes ``_db`` as a dict-like
+    Mongo database object.
+    """
+    coll = MagicMock()
+    coll.update_one = AsyncMock()
+    db = MagicMock()
+    db.__getitem__.return_value = coll
+    service = MagicMock()
+    service._db = db
+    return service, coll
+
+
+# ----------------------------------------------------------------------
+# Group 1: schema validation
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestShadowDecisionLeg:
+    def test_happy_path(self) -> None:
+        leg = _make_leg()
+        assert leg.action == "买入"
+        assert leg.confidence == 0.7
+
+    @pytest.mark.parametrize("bad", ["买", "buy", "", "持仓"])
+    def test_invalid_action_rejected(self, bad: str) -> None:
+        with pytest.raises(ValueError):
+            _make_leg(action=bad)
+
+    @pytest.mark.parametrize(
+        "bad",
+        [-0.1, 1.1, float("nan"), float("inf"), -float("inf")],
+    )
+    def test_invalid_confidence_rejected(self, bad: float) -> None:
+        with pytest.raises(ValueError):
+            _make_leg(confidence=bad)
+
+    def test_bool_confidence_rejected(self) -> None:
+        # Python bool is an int subclass; make sure we don't silently
+        # accept True/False as confidence.
+        with pytest.raises(ValueError):
+            _make_leg(confidence=True)  # type: ignore[arg-type]
+
+    def test_negative_latency_rejected(self) -> None:
+        with pytest.raises(ValueError):
+            _make_leg(latency_ms=-1.0)
+
+    def test_zero_latency_accepted(self) -> None:
+        leg = _make_leg(latency_ms=0.0)
+        assert leg.latency_ms == 0.0
+
+    def test_inf_latency_rejected(self) -> None:
+        with pytest.raises(ValueError):
+            _make_leg(latency_ms=float("inf"))
+
+
+@pytest.mark.unit
+class TestShadowDecisionEntry:
+    def test_happy_path(self) -> None:
+        entry = _make_entry()
+        assert entry.run_id == "run-1"
+
+    def test_empty_run_id_rejected(self) -> None:
+        with pytest.raises(ValueError):
+            _make_entry(run_id="")
+
+    def test_empty_stock_code_rejected(self) -> None:
+        with pytest.raises(ValueError):
+            _make_entry(stock_code="")
+
+    def test_empty_trade_date_rejected(self) -> None:
+        with pytest.raises(ValueError):
+            _make_entry(trade_date="")
+
+    def test_naive_created_at_rejected(self) -> None:
+        # Tests the timezone safety net P5B-T03 R6 surfaced.
+        with pytest.raises(ValueError):
+            _make_entry(created_at=datetime.datetime(2026, 5, 2, 12, 0, 0))
+
+    def test_to_document_round_trip(self) -> None:
+        entry = _make_entry()
+        doc = entry.to_document()
+        assert doc["run_id"] == entry.run_id
+        assert doc["baseline"]["action"] == entry.baseline.action
+        assert doc["routed"]["confidence"] == entry.routed.confidence
+        assert doc["created_at"] == entry.created_at  # BSON Date, not str
+
+
+# ----------------------------------------------------------------------
+# Group 2: record_shadow_decision
+# ----------------------------------------------------------------------
+
+
+@pytest.mark.unit
+class TestRecordShadowDecision:
+    async def test_happy_path_upserts_by_run_id(self) -> None:
+        service, coll = _make_mongo()
+        entry = _make_entry()
+        ok = await record_shadow_decision(service, entry)
+        assert ok is True
+        coll.update_one.assert_awaited_once()
+        args, kwargs = coll.update_one.call_args
+        assert args[0] == {"run_id": "run-1"}
+        assert "$set" in args[1]
+        # upsert kwarg must be true so re-runs replace, not duplicate.
+        assert kwargs["upsert"] is True
+
+    async def test_collection_name(self) -> None:
+        service, _ = _make_mongo()
+        entry = _make_entry()
+        await record_shadow_decision(service, entry)
+        # __getitem__ called with the canonical collection name.
+        service._db.__getitem__.assert_called_with(SHADOW_COLLECTION)
+
+    async def test_mongo_error_returns_false(self) -> None:
+        service, coll = _make_mongo()
+        coll.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
+        ok = await record_shadow_decision(service, _make_entry())
+        assert ok is False
+
+    async def test_idempotent_second_call(self) -> None:
+        service, coll = _make_mongo()
+        entry = _make_entry()
+        await record_shadow_decision(service, entry)
+        await record_shadow_decision(service, entry)
+        # Both calls hit Mongo with the same upsert key — the upsert
+        # contract is what guarantees idempotency, not the call count.
+        assert coll.update_one.await_count == 2
+
+
+# ----------------------------------------------------------------------
+# Group 3: query_shadow_decisions
+# ----------------------------------------------------------------------
+
+
+class _AsyncIterator:
+    """Minimal async iterator for the cursor mock."""
+
+    def __init__(self, items: list[Any]) -> None:
+        self._items = list(items)
+
+    def __aiter__(self) -> _AsyncIterator:
+        return self
+
+    async def __anext__(self) -> Any:
+        if not self._items:
+            raise StopAsyncIteration
+        return self._items.pop(0)
+
+
+@pytest.mark.unit
+class TestQueryShadowDecisions:
+    async def test_invalid_days_rejected(self) -> None:
+        service, _ = _make_mongo()
+        with pytest.raises(ValueError):
+            await query_shadow_decisions(service, days=0)
+
+    async def test_drops_object_id(self) -> None:
+        service, coll = _make_mongo()
+        coll.find = MagicMock(
+            return_value=_AsyncIterator(
+                [
+                    {
+                        "_id": "abc123",
+                        "run_id": "r1",
+                        "stock_code": "600519",
+                        "trade_date": "2026-05-02",
+                    },
+                ]
+            )
+        )
+        docs = await query_shadow_decisions(service, days=7)
+        assert len(docs) == 1
+        assert "_id" not in docs[0]
+        assert docs[0]["run_id"] == "r1"
+
+    async def test_uses_utc_cutoff(self) -> None:
+        service, coll = _make_mongo()
+        coll.find = MagicMock(return_value=_AsyncIterator([]))
+        now = datetime.datetime(2026, 5, 9, 12, 0, tzinfo=datetime.UTC)
+        await query_shadow_decisions(service, days=7, now=now)
+        coll.find.assert_called_once()
+        query = coll.find.call_args[0][0]
+        assert "created_at" in query
+        cutoff = query["created_at"]["$gte"]
+        # 7 days back from a UTC ``now`` is exactly 2026-05-02
+        assert cutoff == datetime.datetime(
+            2026, 5, 2, 12, 0, tzinfo=datetime.UTC
+        )
+
+    async def test_naive_now_normalised_to_utc(self) -> None:
+        service, coll = _make_mongo()
+        coll.find = MagicMock(return_value=_AsyncIterator([]))
+        # An aware now in a non-UTC tz must still be normalised.
+        tz_shanghai = datetime.timezone(datetime.timedelta(hours=8))
+        now = datetime.datetime(2026, 5, 9, 20, 0, tzinfo=tz_shanghai)
+        await query_shadow_decisions(service, days=7, now=now)
+        cutoff = coll.find.call_args[0][0]["created_at"]["$gte"]
+        # 2026-05-09 20:00+08 == 2026-05-09 12:00Z, minus 7 days
+        assert cutoff == datetime.datetime(
+            2026, 5, 2, 12, 0, tzinfo=datetime.UTC
+        )
+
+    async def test_mongo_error_returns_empty(self) -> None:
+        service, coll = _make_mongo()
+        coll.find = MagicMock(side_effect=RuntimeError("mongo down"))
+        docs = await query_shadow_decisions(service, days=1)
+        assert docs == []

exec
/bin/bash -lc "sed -n '1,240p' backend/services/shadow_compare.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""Pure analysis layer for the Phase 5B shadow-test harness.

``scripts/shadow_compare.py`` is a thin CLI on top of this module — all
the math + threshold gating lives here so it is unit-testable without a
running Mongo and without ``argparse`` ceremony.

Inputs are plain dicts shaped like the documents produced by
:class:`backend.services.shadow_recorder.ShadowDecisionEntry`. Anything
malformed (missing keys, wrong types) is dropped and counted in a
``skipped`` bucket so the consumer can see whether the harness actually
saw clean data.

The thresholds match SSoT §6 P5B-T03 pass criteria:

* action consistency ≥ 0.85
* mean absolute confidence delta < 0.15
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

ACTION_MATCH_THRESHOLD = 0.85
CONFIDENCE_DELTA_THRESHOLD = 0.15


@dataclass(frozen=True)
class LegMetrics:
    """Per-leg counters lifted from the ``baseline`` / ``routed`` arms."""

    parse_ok_rate: float
    escalation_rate: float
    avg_latency_ms: float


@dataclass(frozen=True)
class ShadowReport:
    """Output of :func:`compute_shadow_report`. All fields are immutable.

    ``passes`` is the gate result against SSoT §6 P5B-T03 thresholds.
    Tooling that wants to print but not gate (e.g. mid-window trend
    inspection) can ignore the field; the exit-check CLI uses it.
    """

    total_pairs: int
    skipped: int
    action_match_rate: float
    confidence_delta_p50: float
    confidence_delta_p95: float
    confidence_delta_mean_abs: float
    baseline: LegMetrics
    routed: LegMetrics
    by_day: dict[str, dict[str, float]] = field(default_factory=dict)
    passes: dict[str, bool] = field(default_factory=dict)


def _coerce_leg(raw: Any) -> dict[str, Any] | None:
    """Return a leg dict only if all required keys are present and typed.

    Mongo deserialises documents loosely (an int may arrive where a float
    was written), so we coerce here rather than enforce strict types.
    Missing keys mean the entry was written by an older / buggier shadow
    recorder and we do not want to crash the analyser on it.
    """
    if not isinstance(raw, Mapping):
        return None
    required = ("action", "confidence", "model", "parse_ok", "escalated")
    if not all(k in raw for k in required):
        return None
    try:
        return {
            "action": str(raw["action"]),
            "confidence": float(raw["confidence"]),
            "model": str(raw["model"]),
            "latency_ms": float(raw.get("latency_ms", 0.0)),
            "parse_ok": bool(raw["parse_ok"]),
            "escalated": bool(raw["escalated"]),
        }
    except (TypeError, ValueError):
        return None


def _is_clean_pair(pair: dict[str, Any]) -> bool:
    base = pair["baseline"]
    routed = pair["routed"]
    for leg in (base, routed):
        if not math.isfinite(leg["confidence"]):
            return False
        if leg["confidence"] < 0.0 or leg["confidence"] > 1.0:
            return False
        if not math.isfinite(leg["latency_ms"]) or leg["latency_ms"] < 0.0:
            return False
    return True


def compute_shadow_report(
    docs: Iterable[Mapping[str, Any]],
) -> ShadowReport:
    """Reduce raw shadow_decisions documents to a :class:`ShadowReport`.

    Empty / dirty input does not raise: the report shows ``total_pairs=0``
    and ``passes`` populated with ``False`` so downstream automation
    (e.g. CI) can treat "no data" as a hard fail rather than a silent
    pass.
    """
    pairs: list[dict[str, Any]] = []
    skipped = 0
    by_day_counts: dict[str, dict[str, int]] = {}

    for raw in docs:
        if not isinstance(raw, Mapping):
            skipped += 1
            continue
        baseline = _coerce_leg(raw.get("baseline"))
        routed = _coerce_leg(raw.get("routed"))
        if baseline is None or routed is None:
            skipped += 1
            continue

        pair = {"baseline": baseline, "routed": routed}
        if not _is_clean_pair(pair):
            skipped += 1
            continue

        trade_date = raw.get("trade_date")
        if not isinstance(trade_date, str):
            skipped += 1
            continue

        pair["trade_date"] = trade_date
        pairs.append(pair)

        slot = by_day_counts.setdefault(
            trade_date, {"matched": 0, "total": 0}
        )
        slot["total"] += 1
        if baseline["action"] == routed["action"]:
            slot["matched"] += 1

    if not pairs:
        empty_leg = LegMetrics(
            parse_ok_rate=0.0,
            escalation_rate=0.0,
            avg_latency_ms=0.0,
        )
        return ShadowReport(
            total_pairs=0,
            skipped=skipped,
            action_match_rate=0.0,
            confidence_delta_p50=0.0,
            confidence_delta_p95=0.0,
            confidence_delta_mean_abs=0.0,
            baseline=empty_leg,
            routed=empty_leg,
            by_day={},
            passes={
                "action_match": False,
                "confidence_delta": False,
                "has_data": False,
            },
        )

    matched = sum(
        1
        for p in pairs
        if p["baseline"]["action"] == p["routed"]["action"]
    )
    total = len(pairs)
    deltas = [
        p["routed"]["confidence"] - p["baseline"]["confidence"]
        for p in pairs
    ]
    abs_deltas = [abs(d) for d in deltas]

    by_day = {
        day: {
            "match_rate": round(slot["matched"] / slot["total"], 4),
            "samples": slot["total"],
        }
        for day, slot in sorted(by_day_counts.items())
    }

    baseline_metrics = _leg_metrics([p["baseline"] for p in pairs])
    routed_metrics = _leg_metrics([p["routed"] for p in pairs])

    action_match_rate = matched / total
    confidence_delta_mean_abs = sum(abs_deltas) / total
    p50 = statistics.median(deltas)
    p95 = _percentile(deltas, 95)

    passes = {
        "has_data": True,
        "action_match": action_match_rate >= ACTION_MATCH_THRESHOLD,
        "confidence_delta": (
            confidence_delta_mean_abs < CONFIDENCE_DELTA_THRESHOLD
        ),
    }

    return ShadowReport(
        total_pairs=total,
        skipped=skipped,
        action_match_rate=round(action_match_rate, 4),
        confidence_delta_p50=round(p50, 4),
        confidence_delta_p95=round(p95, 4),
        confidence_delta_mean_abs=round(confidence_delta_mean_abs, 4),
        baseline=baseline_metrics,
        routed=routed_metrics,
        by_day=by_day,
        passes=passes,
    )


def _leg_metrics(legs: list[dict[str, Any]]) -> LegMetrics:
    n = len(legs)
    if n == 0:
        return LegMetrics(
            parse_ok_rate=0.0, escalation_rate=0.0, avg_latency_ms=0.0
        )
    parse_ok = sum(1 for leg in legs if leg["parse_ok"])
    escalated = sum(1 for leg in legs if leg["escalated"])
    latency = sum(leg["latency_ms"] for leg in legs) / n
    return LegMetrics(
        parse_ok_rate=round(parse_ok / n, 4),
        escalation_rate=round(escalated / n, 4),
        avg_latency_ms=round(latency, 2),
    )


def _percentile(values: list[float], q: float) -> float:
    """Compute the q-th percentile (q in 0..100) using linear interpolation.

    Implemented locally so we don't pull NumPy into the runtime path of a
    reporting harness. ``q`` is clamped to ``[0, 100]`` so a typo cannot
    produce a meaningless out-of-range index.
    """
    if not values:

exec
/bin/bash -lc "sed -n '241,520p' backend/services/shadow_compare.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
        return 0.0
    q = max(0.0, min(100.0, q))
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q / 100 * (len(sorted_values) - 1)
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return sorted_values[lower]
    weight = pos - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def render_markdown(report: ShadowReport) -> str:
    """Render the report as a markdown table for the summary doc / CI logs."""
    lines = [
        "# Shadow Comparison Report",
        "",
        f"- Total pairs: **{report.total_pairs}**",
        f"- Skipped (malformed): **{report.skipped}**",
        f"- Action match rate: **{report.action_match_rate:.4f}** "
        f"(threshold ≥ {ACTION_MATCH_THRESHOLD})",
        f"- |Δconfidence| mean: **{report.confidence_delta_mean_abs:.4f}** "
        f"(threshold < {CONFIDENCE_DELTA_THRESHOLD})",
        f"- Δconfidence p50: {report.confidence_delta_p50:+.4f}",
        f"- Δconfidence p95: {report.confidence_delta_p95:+.4f}",
        "",
        "## Per-leg",
        "",
        "| leg | parse_ok | escalation_rate | avg_latency_ms |",
        "|-----|---------:|----------------:|---------------:|",
        f"| baseline | {report.baseline.parse_ok_rate:.4f} | "
        f"{report.baseline.escalation_rate:.4f} | "
        f"{report.baseline.avg_latency_ms:.2f} |",
        f"| routed | {report.routed.parse_ok_rate:.4f} | "
        f"{report.routed.escalation_rate:.4f} | "
        f"{report.routed.avg_latency_ms:.2f} |",
        "",
        "## Per day",
        "",
        "| trade_date | match_rate | samples |",
        "|------------|-----------:|--------:|",
    ]
    if report.by_day:
        for day, stats in report.by_day.items():
            lines.append(
                f"| {day} | {stats['match_rate']:.4f} | "
                f"{int(stats['samples'])} |"
            )
    else:
        lines.append("| — | — | 0 |")

    lines.append("")
    lines.append("## Gate")
    lines.append("")
    for gate, ok in report.passes.items():
        marker = "✅" if ok else "❌"
        lines.append(f"- {marker} {gate}")
    return "\n".join(lines) + "\n"


__all__ = [
    "ACTION_MATCH_THRESHOLD",
    "CONFIDENCE_DELTA_THRESHOLD",
    "LegMetrics",
    "ShadowReport",
    "compute_shadow_report",
    "render_markdown",
]

exec
/bin/bash -lc "sed -n '1,260p' backend/services/shadow_recorder.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""Shadow decision recording for Phase 5B exit verification.

This module is the data-layer half of the shadow-test harness. It defines
the immutable ``ShadowDecisionEntry`` schema and the read/write API
against the ``shadow_decisions`` MongoDB collection. The companion CLI
``scripts/shadow_compare.py`` consumes these documents to produce the
action-consistency / confidence-deviation report Phase 5B exit gates on.

Design notes
------------

* **Pure data-layer.** This module is intentionally NOT wired into the
  live LangGraph pipeline. Doubling LLM calls in production would
  invalidate the cost-savings story P5B-T03 was built to tell. Operators
  wire the recorder through a separate scheduled job once deployment
  starts (Phase 5C deployment task). Tests therefore drive it directly.
* **Immutable entries.** Every field is frozen so a record cannot drift
  between the moment it is built and the moment it lands in Mongo —
  protects against subtle aliasing bugs in async pipelines.
* **UTC clock.** Matches the convention pinned by
  ``backend.llm.fallback._utc_date_str()`` so daily rollups elsewhere in
  the system line up; do NOT switch to ``datetime.now()`` (no tz). See
  P5B-T03 codex R6 for the timezone-drift bug this convention prevents.
* **Fail-soft writes.** The recorder swallows Mongo errors and logs a
  structured warning. Shadow recording is observability — a Mongo blip
  must not crash the calling job.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from backend.data.database import MongoDBService

log = structlog.get_logger(component="shadow_recorder")

SHADOW_COLLECTION = "shadow_decisions"
_TTL_DAYS_DEFAULT = 30
_VALID_ACTIONS: frozenset[str] = frozenset({"买入", "持有", "卖出"})


@dataclass(frozen=True)
class ShadowDecisionLeg:
    """One side (baseline or routed) of a shadow comparison.

    ``parse_ok`` records whether the LLM response was JSON-parseable.
    The harness keeps unparseable runs because they are themselves a
    quality signal — a routing change that drives parse-failure rate up
    is a regression even if the surviving runs still match.

    ``escalated`` is meaningful only for the routed leg; the baseline leg
    sets it to ``False`` by convention. Storing both keeps the document
    schema-symmetric and the consumer code branch-free.
    """

    action: str
    confidence: float
    model: str
    latency_ms: float
    escalated: bool
    parse_ok: bool

    def __post_init__(self) -> None:
        if self.action not in _VALID_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(_VALID_ACTIONS)}, "
                f"got {self.action!r}"
            )
        if not isinstance(self.confidence, (int, float)) or isinstance(
            self.confidence, bool
        ):
            raise ValueError(
                f"confidence must be a finite float in [0,1], got "
                f"{self.confidence!r}"
            )
        conf = float(self.confidence)
        if not math.isfinite(conf) or conf < 0.0 or conf > 1.0:
            raise ValueError(
                f"confidence must be a finite float in [0,1], got {conf!r}"
            )
        if not math.isfinite(self.latency_ms) or self.latency_ms < 0:
            raise ValueError(
                f"latency_ms must be a finite, non-negative float, got "
                f"{self.latency_ms!r}"
            )


@dataclass(frozen=True)
class ShadowDecisionEntry:
    """A baseline-vs-routed pair of fund_manager decisions for one run.

    The pair shares ``run_id`` so each entry carries both decisions
    side-by-side and the consumer never has to join two collections.
    """

    run_id: str
    stock_code: str
    trade_date: str
    created_at: datetime.datetime
    baseline: ShadowDecisionLeg
    routed: ShadowDecisionLeg

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must be a non-empty string")
        if not self.stock_code:
            raise ValueError("stock_code must be a non-empty string")
        if not self.trade_date:
            raise ValueError("trade_date must be a non-empty string")
        if self.created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware (UTC); naive datetimes "
                "drift across daylight-saving boundaries"
            )

    def to_document(self) -> dict[str, Any]:
        """Serialise to a Mongo-friendly dict.

        Keeps ``created_at`` as a real ``datetime`` (Mongo encodes it as
        BSON Date) so range queries work; everything else is plain JSON.
        """
        doc: dict[str, Any] = {
            "run_id": self.run_id,
            "stock_code": self.stock_code,
            "trade_date": self.trade_date,
            "created_at": self.created_at,
            "baseline": asdict(self.baseline),
            "routed": asdict(self.routed),
        }
        return doc


async def record_shadow_decision(
    mongodb: MongoDBService,
    entry: ShadowDecisionEntry,
) -> bool:
    """Upsert a shadow comparison entry into the ``shadow_decisions`` collection.

    Upsert key is ``run_id`` so re-runs (e.g. operator replays) overwrite
    rather than accumulate noise. Returns True on success, False on Mongo
    error — the caller logs but does not raise. Shadow tracking is
    observability and must never propagate a failure into a real trading
    run.
    """
    try:
        coll = mongodb._db[SHADOW_COLLECTION]  # noqa: SLF001
        await coll.update_one(
            {"run_id": entry.run_id},
            {"$set": entry.to_document()},
            upsert=True,
        )
        return True
    except Exception as exc:
        log.warning(
            "shadow_record_failed",
            run_id=entry.run_id,
            stock_code=entry.stock_code,
            error=str(exc),
        )
        return False


async def query_shadow_decisions(
    mongodb: MongoDBService,
    *,
    days: int = 7,
    now: datetime.datetime | None = None,
) -> list[dict[str, Any]]:
    """Return shadow_decisions documents for the last ``days`` days.

    ``now`` is injectable so tests can pin the clock without monkey-
    patching ``datetime.datetime``. The cutoff is computed in UTC to
    match the writer convention.

    Empty result is normal (no shadow data collected yet) and is
    returned as ``[]`` — never ``None`` — so consumers can iterate
    without a None check.
    """
    if days <= 0:
        raise ValueError(f"days must be positive, got {days}")
    cutoff = (
        now.astimezone(datetime.UTC)
        if now is not None
        else datetime.datetime.now(tz=datetime.UTC)
    ) - datetime.timedelta(days=days)

    try:
        coll = mongodb._db[SHADOW_COLLECTION]  # noqa: SLF001
        cursor = coll.find({"created_at": {"$gte": cutoff}})
        # Drop the Mongo ObjectId so consumers (script + tests) can
        # JSON-serialise the result without bespoke encoders.
        return [
            {k: v for k, v in doc.items() if k != "_id"}
            async for doc in cursor
        ]
    except Exception as exc:
        log.warning(
            "shadow_query_failed",
            days=days,
            error=str(exc),
        )
        return []


__all__ = [
    "SHADOW_COLLECTION",
    "ShadowDecisionEntry",
    "ShadowDecisionLeg",
    "query_shadow_decisions",
    "record_shadow_decision",
]

exec
/bin/bash -lc "sed -n '1,240p' scripts/phase5b_exit_check.py" in /home/ps/papers/QuantMind
exec
/bin/bash -lc "sed -n '1,260p' backend/services/phase5b_exit_check.py" in /home/ps/papers/QuantMind
exec
/bin/bash -lc "sed -n '1,240p' scripts/shadow_compare.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
#!/usr/bin/env python3
"""Phase 5B exit-gate verification CLI.

Aggregates the SSoT §6.972 exit checklist into one markdown report:

* fast / slow per-stock cost p95 (from Redis cost_tracker)
* fast / slow p95 latency (from MongoDB analysis_records)
* daily total cost (from Redis cost_tracker)
* shadow consistency (from MongoDB shadow_decisions)

The math lives in :mod:`backend.services.phase5b_exit_check`; this
module only handles I/O wiring and ``argparse``.

Usage::

    python scripts/phase5b_exit_check.py --days 7

Returns exit 0 when every gate passes, 1 otherwise (intended for CI).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# Allow running as a standalone script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.phase5b_exit_check import (  # noqa: E402
    compute_exit_report,
    render_markdown,
)
from backend.services.watchlist_policy import load_policy  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 5B exit-gate verification.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Look-back window in days.",
    )
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"),
    )
    parser.add_argument(
        "--mongo-db",
        default=os.environ.get("MONGODB_DB", "quantmind"),
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
    )
    parser.add_argument(
        "--policy-path",
        type=Path,
        default=Path("config/watchlist_policy.yaml"),
        help="Path to watchlist_policy.yaml (relative to project root).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on first failing gate.",
    )
    return parser.parse_args(argv)


async def _gather_inputs(
    args: argparse.Namespace,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Pull records / cost entries / shadow docs from live infra.

    We open the Mongo client and Redis client locally and close them
    before returning so the caller (sync ``main``) does not have to know
    about async lifetimes.
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    from redis.asyncio import Redis

    from backend.data.database import MongoDBService
    from backend.llm.cost_tracker import aggregate_costs
    from backend.services.shadow_recorder import query_shadow_decisions

    client = AsyncIOMotorClient(args.mongo_uri)
    redis: Redis | None = None
    try:
        db = client[args.mongo_db]
        service = MongoDBService(db)

        records_cursor = service._db["analysis_records"].find().sort(  # noqa: SLF001
            "created_at", -1
        ).limit(args.days * 200)
        records = [doc async for doc in records_cursor]

        shadow_docs = await query_shadow_decisions(service, days=args.days)

        redis = Redis.from_url(args.redis_url, decode_responses=True)
        summary = await aggregate_costs(redis, days=args.days)
        cost_entries = [
            {
                "date": entry.date,
                "agent_name": entry.agent_name,
                "cost_rmb": entry.cost_rmb,
            }
            for entry in summary.entries
        ]
        return list(records), cost_entries, list(shadow_docs)
    finally:
        if redis is not None:
            await redis.aclose()
        client.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.policy_path.exists():
        sys.stderr.write(
            f"watchlist_policy.yaml not found at {args.policy_path}\n"
        )
        return 2
    policy = load_policy(args.policy_path)

    records, cost_entries, shadow_docs = asyncio.run(_gather_inputs(args))
    report = compute_exit_report(
        records,
        cost_entries,
        shadow_docs,
        policy,
        days=args.days,
    )
    sys.stdout.write(render_markdown(report))
    if args.strict and not all(report.passes.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

 succeeded in 0ms:
#!/usr/bin/env python3
"""Phase 5B-T03 shadow comparison CLI.

Reads ``shadow_decisions`` documents (from MongoDB or a JSONL file) and
prints the action-consistency / confidence-deviation report Phase 5B
exit gates on. The actual math lives in
:mod:`backend.services.shadow_compare` so it stays unit-testable.

Usage::

    # MongoDB (default; reads MONGODB_URI from env)
    python scripts/shadow_compare.py --days 7

    # File replay (operator-collected JSONL of shadow_decisions docs)
    python scripts/shadow_compare.py --input shadow_dump.jsonl

The script returns exit code 0 when all gates pass, 1 otherwise. Useful
in CI as the Phase 5B exit gate driver.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Allow running as a standalone script without `pip install -e .`
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.services.shadow_compare import (  # noqa: E402
    compute_shadow_report,
    render_markdown,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 5B shadow comparison report.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="JSONL file of shadow_decisions documents. Mutually "
        "exclusive with the MongoDB path.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Look-back window in days when reading from MongoDB.",
    )
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017"),
        help="MongoDB connection URI (default: $MONGODB_URI).",
    )
    parser.add_argument(
        "--mongo-db",
        default=os.environ.get("MONGODB_DB", "quantmind"),
        help="MongoDB database name.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on first failing gate (CI driver mode).",
    )
    return parser.parse_args(argv)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file. Lines that fail to parse are surfaced loudly.

    A silent skip would let a corrupted dump pass the gate by reporting
    metrics over a smaller-than-expected sample; instead we raise so the
    operator notices and re-dumps.
    """
    docs: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                docs.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"{path}:{lineno}: malformed JSON ({exc})"
                ) from exc
    return docs


async def _read_mongo(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Connect to MongoDB and pull shadow_decisions for the given window.

    Imported lazily so tests / file-mode callers don't pay the motor
    import cost.
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    from backend.data.database import MongoDBService
    from backend.services.shadow_recorder import query_shadow_decisions

    client = AsyncIOMotorClient(args.mongo_uri)
    try:
        db = client[args.mongo_db]
        service = MongoDBService(db)
        docs = await query_shadow_decisions(service, days=args.days)
        return list(docs)
    finally:
        client.close()


def _load_docs(args: argparse.Namespace) -> Iterable[dict[str, Any]]:
    if args.input is not None:
        if not args.input.exists():
            raise SystemExit(f"input file does not exist: {args.input}")
        return _read_jsonl(args.input)
    return asyncio.run(_read_mongo(args))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    docs = list(_load_docs(args))
    report = compute_shadow_report(docs)
    sys.stdout.write(render_markdown(report))
    if args.strict and not all(report.passes.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

 succeeded in 0ms:
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
from backend.services.watchlist_policy import WatchlistPolicy, assign_category

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
        n = len(run_ids)
        if n == 0:
            continue
        share = daily_totals.get(date, 0.0) / n
        for run_id in run_ids:
            per_run[run_id] = {"date": date, "cost_rmb": round(share, 6)}
    return per_run


def split_runs_by_category(
    records: Iterable[Mapping[str, Any]],
    policy: WatchlistPolicy,
) -> dict[str, list[Mapping[str, Any]]]:
    """Partition analysis_records into ``fast`` and ``slow`` buckets.

    Categorisation reuses the same ``assign_category`` rule the live
    scheduler uses, so the gate report and the runtime cron always
    agree. Records missing ``stock_code`` are dropped (cannot be
    attributed) and never fail-open into either bucket.
    """
    buckets: dict[str, list[Mapping[str, Any]]] = {"fast": [], "slow": []}
    for record in records:
        code = record.get("stock_code")
        if not isinstance(code, str) or not code:
            continue
        category = assign_category(code, policy)
        if category in buckets:
            buckets[category].append(record)
    return buckets


def _record_latency_seconds(record: Mapping[str, Any]) -> float | None:
    """Compute end-to-end latency from a record; None when unparseable."""
    started = record.get("created_at")
    completed = record.get("completed_at")
    if not isinstance(started, datetime) or not isinstance(completed, datetime):
        return None
    if completed < started:
        return None
    return (completed - started).total_seconds()


def compute_exit_report(
    records: Iterable[Mapping[str, Any]],
    cost_entries: Iterable[Mapping[str, Any]],
    shadow_docs: Iterable[Mapping[str, Any]],
    policy: WatchlistPolicy,
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
    # threshold is "per stock" worst-case rather than mean.
    fast_costs = [
        per_run_cost[r["run_id"]]["cost_rmb"]
        for r in bucketed["fast"]
        if r["run_id"] in per_run_cost
    ]
    slow_costs = [
        per_run_cost[r["run_id"]]["cost_rmb"]
        for r in bucketed["slow"]
        if r["run_id"] in per_run_cost
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

    has_data = {
        "fast_cost": cost_metric.fast_runs > 0,
        "slow_cost": cost_metric.slow_runs > 0,
        "daily_total": bool(daily_totals),
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

exec
/bin/bash -lc "sed -n '261,520p' backend/services/phase5b_exit_check.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
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


def render_markdown(report: ExitGateReport) -> str:
    """Render an :class:`ExitGateReport` as a single markdown gate table."""

    def fmt_pass(name: str) -> str:
        ok = report.passes.get(name, False)
        has = report.has_data.get(name.split("_")[0], True)
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
        f"|Δ| mean "
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

exec
/bin/bash -lc "sed -n '1,260p' backend/services/watchlist_policy.py" in /home/ps/papers/QuantMind
exec
/bin/bash -lc "sed -n '1,260p' backend/llm/cost_tracker.py" in /home/ps/papers/QuantMind
exec
/bin/bash -lc "sed -n '1,200p' backend/data/database.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""LLM cost aggregation from Redis usage data."""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from backend.llm.fallback import COST_RATES

if TYPE_CHECKING:
    import redis.asyncio

    from backend.data.database import MongoDBService

log = structlog.get_logger(component="cost_tracker")

# Per-model pricing in RMB per 1K tokens (more granular than COST_RATES)
MODEL_PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-pro": {"input": 0.0002, "output": 0.0002},
    "qwen3.6-plus": {"input": 0.001, "output": 0.001},
    "kimi-k2.6": {"input": 0.0021, "output": 0.0084},
}


@dataclass(frozen=True)
class DailyCostEntry:
    """A single usage record for one agent-provider pair on one day."""

    date: str
    agent_name: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    requests: int
    cost_rmb: float


@dataclass(frozen=True)
class CostSummary:
    """Aggregated cost statistics over a period."""

    period: str
    days: int
    entries: tuple[DailyCostEntry, ...]
    total_cost_rmb: float
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    by_agent: dict[str, float]
    by_provider: dict[str, float]
    daily_totals: dict[str, float]


def calculate_cost(
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Calculate cost in RMB for a given token count.

    Uses COST_RATES from fallback.py (per million tokens).
    """
    rate = COST_RATES.get(provider)
    if rate is None:
        return 0.0
    cost = (
        prompt_tokens * rate.input_rmb_per_million / 1_000_000
        + completion_tokens * rate.output_rmb_per_million / 1_000_000
    )
    return round(cost, 8)


async def aggregate_costs(
    redis_client: redis.asyncio.Redis,
    days: int = 30,
    period: str = "daily",
) -> CostSummary:
    """Scan Redis for usage data and aggregate cost statistics.

    Scans keys matching the pattern llm:usage:{date}:{agent}:{provider}
    for the requested number of days.

    Args:
        redis_client: Async Redis client.
        days: Number of days to look back.
        period: Aggregation period ('daily' or 'weekly').

    Returns:
        CostSummary with all aggregated data.
    """
    # Pin to UTC date — must match the writer in
    # backend.llm.fallback._utc_date_str(). Using local time here was a
    # silent timezone-drift bug: in Asia/Shanghai the cost_guard hard
    # ceiling could read zero spend during 00:00-08:00 UTC+8 even
    # though Redis already had today's UTC entries (codex P5B-T03 R6).
    today = datetime.datetime.now(tz=datetime.UTC).date()
    entries: list[DailyCostEntry] = []

    for day_offset in range(days):
        date = today - datetime.timedelta(days=day_offset)
        date_str = date.isoformat()
        pattern = f"llm:usage:{date_str}:*"

        try:
            keys = await _scan_keys(redis_client, pattern)
        except Exception as exc:
            log.warning("cost_scan_failed", date=date_str, error=str(exc))
            continue

        for key in keys:
            entry = await _parse_usage_key(redis_client, key, date_str)
            if entry is not None:
                entries.append(entry)

    return _build_summary(entries, period, days)


async def _scan_keys(
    redis_client: redis.asyncio.Redis, pattern: str
) -> list[str]:
    """Scan Redis for keys matching a pattern."""
    keys: list[str] = []
    cursor: int | bytes = 0
    while True:
        cursor, batch = await redis_client.scan(
            cursor=cursor, match=pattern, count=100
        )
        keys.extend(
            k if isinstance(k, str) else k.decode() for k in batch
        )
        if cursor == 0:
            break
    return keys


async def _parse_usage_key(
    redis_client: redis.asyncio.Redis,
    key: str,
    date_str: str,
) -> DailyCostEntry | None:
    """Parse a single Redis usage key into a DailyCostEntry."""
    try:
        data = await redis_client.hgetall(key)
        if not data:
            return None

        # Key format: llm:usage:{date}:{agent_name}:{provider}
        parts = key.split(":")
        if len(parts) < 5:
            return None

        agent_name = parts[3]
        provider = parts[4]

        prompt_tokens = int(data.get("prompt_tokens", 0))
        completion_tokens = int(data.get("completion_tokens", 0))
        requests = int(data.get("requests", 0))
        cost_rmb = float(data.get("cost_rmb", 0.0))

        # Drop entries with corrupt cost values: a negative or non-finite
        # cost_rmb would otherwise offset legitimate spend in the daily
        # aggregate and silently undercut the cost_guard hard cap. This
        # is the data-layer defense; cost_guard.get_budget_state has a
        # second fail-closed check on the aggregate.
        if not math.isfinite(cost_rmb) or cost_rmb < 0:
            log.warning(
                "cost_entry_invalid",
                key=key,
                cost_rmb=cost_rmb,
                action="dropped",
            )
            return None

        return DailyCostEntry(
            date=date_str,
            agent_name=agent_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            requests=requests,
            cost_rmb=cost_rmb,
        )
    except Exception as exc:
        log.warning("cost_parse_failed", key=key, error=str(exc))
        return None


def _build_summary(
    entries: list[DailyCostEntry], period: str, days: int
) -> CostSummary:
    """Build a CostSummary from a list of DailyCostEntry records."""
    total_cost = 0.0
    total_requests = 0
    total_prompt = 0
    total_completion = 0
    by_agent: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    daily_totals: dict[str, float] = {}

    for entry in entries:
        total_cost += entry.cost_rmb
        total_requests += entry.requests
        total_prompt += entry.prompt_tokens
        total_completion += entry.completion_tokens

        by_agent[entry.agent_name] = (
            by_agent.get(entry.agent_name, 0.0) + entry.cost_rmb
        )
        by_provider[entry.provider] = (
            by_provider.get(entry.provider, 0.0) + entry.cost_rmb
        )
        daily_totals[entry.date] = (
            daily_totals.get(entry.date, 0.0) + entry.cost_rmb
        )

    return CostSummary(
        period=period,
        days=days,
        entries=tuple(entries),
        total_cost_rmb=round(total_cost, 4),
        total_requests=total_requests,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        by_agent={k: round(v, 4) for k, v in by_agent.items()},
        by_provider={k: round(v, 4) for k, v in by_provider.items()},
        daily_totals={k: round(v, 4) for k, v in daily_totals.items()},
    )


async def flush_to_mongodb(
    redis_client: redis.asyncio.Redis,
    mongodb: MongoDBService,
    days: int = 1,
) -> int:
    """Persist cost entries from Redis to MongoDB for durable storage.

    Args:
        redis_client: Async Redis client.
        mongodb: MongoDBService instance.
        days: Number of days to flush (default: today only).

    Returns:
        Count of entries persisted.
    """
    try:
        summary = await aggregate_costs(redis_client, days=days)
    except Exception as exc:
        log.warning("cost_flush_aggregate_failed", error=str(exc))
        return 0

    count = 0
    for entry in summary.entries:
        try:
            await mongodb.save_cost_entry({
                "date": entry.date,
                "agent_name": entry.agent_name,

 succeeded in 0ms:
"""Fast/Slow watchlist categorisation policy (Phase 5B-T02).

A single immutable :class:`WatchlistPolicy` describes both buckets:

* ``fast`` runs intraday on a 4-tick cron with a tighter pipeline
  (``max_debate_rounds=1``, ~480s timeout) for short-horizon names.
* ``slow`` runs once per trading day with a deeper pipeline
  (``max_debate_rounds=2``, ~900s timeout) for long-horizon names.

The policy is loaded from ``config/watchlist_policy.yaml`` (template in
SSoT §2.7) and consumed by :class:`backend.data.analysis_scheduler.
AnalysisScheduler`. The YAML is the contract; this module only
validates and exposes it as a frozen dataclass so the scheduler can
hand each cron job its own pipeline knobs without touching disk.

Why a separate module instead of inlining into the scheduler:
- ``assign_category`` is pure-function logic worth unit testing in
  isolation (overrides win, default fallback, fast vs slow precedence).
- The API endpoint that mutates per-code overrides reuses the loader
  to round-trip the file safely.
- Follows the same pattern Phase 5A established for
  ``cost_guard`` / ``authorization`` — extract small services out of
  the scheduler so each piece is independently testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml

log = structlog.get_logger(component="watchlist_policy")

Category = Literal["fast", "slow"]
_VALID_CATEGORIES: tuple[Category, ...] = ("fast", "slow")


class WatchlistPolicyError(ValueError):
    """Raised when ``watchlist_policy.yaml`` fails validation."""


@dataclass(frozen=True)
class BucketConfig:
    """Per-bucket cron + pipeline knobs.

    Both buckets share the same shape — what differs is the values
    (cron cadence, debate depth, timeout). Storing them as one
    dataclass keeps the schema honest: the loader cannot accidentally
    load a partial bucket.

    ``pipeline`` is RESERVED for Phase 5B-T03 / Phase 5C: it carries
    an opaque identifier that future routing logic will use to pick
    a graph variant (e.g. ``fast_pipeline`` may skip Stage-2 debate).
    The scheduler currently only consumes ``max_debate_rounds`` and
    ``pipeline_timeout_seconds`` — keep the YAML values stable so
    later phases can wire it up without a config break.
    """

    cron: str
    pipeline: str
    max_debate_rounds: int
    pipeline_timeout_seconds: int
    default_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WatchlistPolicy:
    """Immutable view of ``watchlist_policy.yaml``.

    ``overrides`` maps stock_code → category and wins over
    ``default_category``. ``policy_version`` lets future schema bumps
    be detected before silently misreading old YAML.

    The two ``*_default_set`` frozensets are derived caches built at
    construction (in :func:`load_policy`) so per-tick membership
    checks in :func:`assign_category` stay O(1) even if the YAML
    grows long lists of default codes.
    """

    fast: BucketConfig
    slow: BucketConfig
    overrides: dict[str, Category] = field(default_factory=dict)
    default_category: Category = "slow"
    policy_version: int = 1
    last_updated: str | None = None
    fast_default_set: frozenset[str] = field(default_factory=frozenset)
    slow_default_set: frozenset[str] = field(default_factory=frozenset)

    def cron_for(self, category: Category) -> str:
        """Return the cron string for ``fast`` or ``slow``."""
        return self.fast.cron if category == "fast" else self.slow.cron

    def bucket_for(self, category: Category) -> BucketConfig:
        """Return the BucketConfig for ``fast`` or ``slow``."""
        return self.fast if category == "fast" else self.slow


def _coerce_bucket(name: str, raw: Any) -> BucketConfig:
    """Validate one bucket subdocument into a BucketConfig."""
    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            f"watchlist_policy.{name} must be a mapping, got {type(raw).__name__}"
        )
    required = ("cron", "pipeline", "max_debate_rounds", "pipeline_timeout_seconds")
    missing = [k for k in required if k not in raw]
    if missing:
        raise WatchlistPolicyError(
            f"watchlist_policy.{name} missing required keys: {missing}"
        )

    rounds = raw["max_debate_rounds"]
    timeout = raw["pipeline_timeout_seconds"]
    if not isinstance(rounds, int) or rounds < 0:
        raise WatchlistPolicyError(
            f"watchlist_policy.{name}.max_debate_rounds must be a non-negative int"
        )
    if not isinstance(timeout, int) or timeout <= 0:
        raise WatchlistPolicyError(
            f"watchlist_policy.{name}.pipeline_timeout_seconds must be a positive int"
        )

    default_codes_raw = raw.get("default_codes", []) or []
    if not isinstance(default_codes_raw, list):
        raise WatchlistPolicyError(
            f"watchlist_policy.{name}.default_codes must be a list"
        )
    default_codes: tuple[str, ...] = tuple(str(c) for c in default_codes_raw)

    return BucketConfig(
        cron=str(raw["cron"]),
        pipeline=str(raw["pipeline"]),
        max_debate_rounds=rounds,
        pipeline_timeout_seconds=timeout,
        default_codes=default_codes,
    )


def _coerce_overrides(raw: Any) -> dict[str, Category]:
    """Validate the ``overrides`` mapping.

    Stock codes are normalised to strings (YAML may parse pure-numeric
    codes like ``600519`` as ints). Category strings are checked against
    the literal set so a typo (``"fas"``) fails loudly at load time.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            f"watchlist_policy.overrides must be a mapping, got {type(raw).__name__}"
        )
    out: dict[str, Category] = {}
    for code, category in raw.items():
        code_str = str(code)
        if category not in _VALID_CATEGORIES:
            raise WatchlistPolicyError(
                f"watchlist_policy.overrides[{code_str}] must be 'fast' or 'slow', "
                f"got {category!r}"
            )
        out[code_str] = category  # type: ignore[assignment]
    return out


def _coerce_default(raw: Any) -> Category:
    if raw is None:
        return "slow"
    if raw not in _VALID_CATEGORIES:
        raise WatchlistPolicyError(
            f"watchlist_policy.default_category must be 'fast' or 'slow', got {raw!r}"
        )
    return raw  # type: ignore[return-value]


def load_policy(path: str | Path) -> WatchlistPolicy:
    """Load and validate ``watchlist_policy.yaml`` into a WatchlistPolicy.

    Performs structural validation up front so a malformed file fails
    on startup rather than at the first cron firing. Codes that appear
    in BOTH ``fast.default_codes`` and ``slow.default_codes`` are
    rejected — a code can only belong to one bucket.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        WatchlistPolicyError: If the YAML schema is invalid.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"watchlist policy file not found: {p}")

    try:
        with p.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        # Wrap PyYAML's internal exception so callers (main lifespan,
        # API handlers) can catch a single project-defined error type
        # instead of importing yaml just for the failure mode.
        raise WatchlistPolicyError(
            f"watchlist_policy.yaml is not valid YAML: {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise WatchlistPolicyError(
            f"watchlist_policy.yaml root must be a mapping, got {type(raw).__name__}"
        )

    if "fast" not in raw or "slow" not in raw:
        raise WatchlistPolicyError(
            "watchlist_policy.yaml must define both 'fast' and 'slow' buckets"
        )

    fast = _coerce_bucket("fast", raw["fast"])
    slow = _coerce_bucket("slow", raw["slow"])

    overlap = set(fast.default_codes) & set(slow.default_codes)
    if overlap:
        raise WatchlistPolicyError(
            f"codes appear in both fast.default_codes and slow.default_codes: "
            f"{sorted(overlap)}"
        )

    overrides = _coerce_overrides(raw.get("overrides"))
    default_category = _coerce_default(raw.get("default_category"))

    version_raw = raw.get("policy_version", 1)
    if not isinstance(version_raw, int):
        raise WatchlistPolicyError(
            "watchlist_policy.policy_version must be an int"
        )

    last_updated = raw.get("last_updated")
    if last_updated is not None and not isinstance(last_updated, str):
        last_updated = str(last_updated)

    policy = WatchlistPolicy(
        fast=fast,
        slow=slow,
        overrides=overrides,
        default_category=default_category,
        policy_version=version_raw,
        last_updated=last_updated,
        fast_default_set=frozenset(fast.default_codes),
        slow_default_set=frozenset(slow.default_codes),
    )
    log.info(
        "watchlist_policy_loaded",
        path=str(p),
        fast_default_count=len(fast.default_codes),
        slow_default_count=len(slow.default_codes),
        overrides_count=len(overrides),
        version=version_raw,
    )
    return policy


_NO_OVERRIDE: Category | None = None


def assign_category(code: str, policy: WatchlistPolicy) -> Category:

 succeeded in 0ms:
"""MongoDB persistence service via motor async driver."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from pymongo import ASCENDING, DESCENDING, UpdateOne

from backend.models.market import (
    FinancialData,
    IndexQuote,
    NewsArticle,
    StockQuote,
)

if TYPE_CHECKING:
    import pandas as pd

log = structlog.get_logger(component="database")

QuoteType = IndexQuote | StockQuote


class MongoDBService:
    """Async MongoDB persistence for market data, kline, and news.

    Uses motor's AsyncIOMotorDatabase for all operations.
    """

    def __init__(self, db: Any) -> None:
        """Initialize with a motor AsyncIOMotorDatabase instance."""
        self._db = db
        self._log = log

    async def initialize(self) -> None:
        """Create indexes on all collections."""
        market = self._db["market_realtime"]
        await market.create_index(
            [("code", ASCENDING), ("timestamp", DESCENDING)],
            unique=True,
            background=True,
        )

        kline = self._db["kline_daily"]
        await kline.create_index(
            [("code", ASCENDING), ("date", ASCENDING)],
            unique=True,
            background=True,
        )

        financial = self._db["financial_data"]
        await financial.create_index(
            [("code", ASCENDING), ("report_date", ASCENDING)],
            unique=True,
            background=True,
        )

        news = self._db["news_articles"]
        await news.create_index(
            [("publish_time", DESCENDING)],
            background=True,
        )
        await news.create_index(
            [("url", ASCENDING)],
            unique=True,
            background=True,
        )

        simulations = self._db["simulations"]
        await simulations.create_index(
            [("created_at", DESCENDING)],
            background=True,
        )

        signals = self._db["trading_signals"]
        await signals.create_index(
            [("stock_code", ASCENDING), ("trade_date", DESCENDING)],
            unique=True,
            background=True,
        )
        # Monitoring dashboard aggregates by date without stock_code, so
        # the compound index above cannot cover the trade_date-only scan.
        # A standalone descending index keeps count_signals_for_date /
        # count_signals_since O(log n) as evaluation data grows.
        await signals.create_index(
            [("trade_date", DESCENDING)],
            background=True,
        )

        index_prices = self._db["index_prices"]
        await index_prices.create_index(
            [("index_code", ASCENDING), ("date", ASCENDING)],
            unique=True,
            background=True,
        )

        cost_tracking = self._db["cost_tracking"]
        await cost_tracking.create_index(
            [
                ("date", DESCENDING),
                ("agent_name", ASCENDING),
                ("provider", ASCENDING),
            ],
            unique=True,
            background=True,
        )

        analysis_records = self._db["analysis_records"]
        await analysis_records.create_index(
            [("run_id", ASCENDING)],
            unique=True,
            background=True,
        )
        await analysis_records.create_index(
            [
                ("stock_code", ASCENDING),
                ("trade_date", DESCENDING),
                ("created_at", DESCENDING),
            ],
            background=True,
        )
        # Not unique: trading_signals upserts by (stock_code, trade_date),
        # so multiple analysis_records for the same trading day legitimately
        # share a signal_id pointing at the latest signal row.
        await analysis_records.create_index(
            [("signal_id", ASCENDING)],
            sparse=True,
            background=True,
        )
        await analysis_records.create_index(
            [("created_at", DESCENDING)],
            background=True,
        )
        # History-list filter shapes: {stock_code}, {trade_date}, and
        # {stock_code, trade_date}. The main (stock_code, trade_date,
        # created_at) index covers the last two, but stock_code-only
        # queries benefit from a (stock_code, created_at DESC) index
        # that also covers the sort, and trade_date-only queries
        # benefit from (trade_date DESC, created_at DESC).
        await analysis_records.create_index(
            [("stock_code", ASCENDING), ("created_at", DESCENDING)],
            background=True,
        )
        await analysis_records.create_index(
            [("trade_date", DESCENDING), ("created_at", DESCENDING)],
            background=True,
        )

        self._log.info("mongodb_indexes_created")

    async def save_market_snapshot(
        self, quotes: list[QuoteType]
    ) -> int:
        """Bulk upsert market quotes. Returns count of operations."""
        if not quotes:
            return 0

        coll = self._db["market_realtime"]
        ops = [
            UpdateOne(
                {"code": q.code, "timestamp": q.timestamp},
                {"$set": q.model_dump()},
                upsert=True,
            )
            for q in quotes
        ]

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_market_snapshot_failed", error=str(exc))
            return 0

    async def save_kline(self, code: str, df: pd.DataFrame) -> int:
        """Bulk upsert K-line DataFrame rows. Returns operation count."""
        if df is None or df.empty:
            return 0

        coll = self._db["kline_daily"]
        ops = []
        for _, row in df.iterrows():
            doc = row.to_dict()
            doc["code"] = code
            ops.append(
                UpdateOne(
                    {"code": code, "date": doc.get("date", "")},
                    {"$set": doc},
                    upsert=True,
                )
            )

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_kline_failed", error=str(exc))
            return 0


exec
/bin/bash -lc "sed -n '260,420p' backend/services/watchlist_policy.py && sed -n '200,420p' backend/data/database.py && sed -n '200,420p' backend/llm/cost_tracker.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
def assign_category(code: str, policy: WatchlistPolicy) -> Category:
    """Return ``'fast'`` or ``'slow'`` for a single stock code.

    Resolution order (first match wins):
      1. ``policy.overrides[code]``
      2. ``code in policy.fast_default_set``  → fast
      3. ``code in policy.slow_default_set``  → slow
      4. ``policy.default_category``

    Uses ``dict.get`` (single hash) and frozenset membership (O(1))
    so a per-tick partition over a large watchlist stays cheap.
    """
    override = policy.overrides.get(code)
    if override is not None:
        return override
    if code in policy.fast_default_set:
        return "fast"
    if code in policy.slow_default_set:
        return "slow"
    return policy.default_category


def partition_watchlist(
    codes: list[str], policy: WatchlistPolicy
) -> tuple[list[str], list[str]]:
    """Split ``codes`` into ``(fast_codes, slow_codes)`` lists.

    Order is preserved so downstream logging and rate-limiting stays
    deterministic across runs.
    """
    fast_codes: list[str] = []
    slow_codes: list[str] = []
    for code in codes:
        if assign_category(code, policy) == "fast":
            fast_codes.append(code)
        else:
            slow_codes.append(code)
    return fast_codes, slow_codes


def update_override(
    policy: WatchlistPolicy, code: str, category: Category | None
) -> WatchlistPolicy:
    """Return a new policy with ``code`` set to ``category`` (or removed).

    Pure function — does NOT touch disk. Callers that want persistence
    should follow with :func:`save_policy`. Passing ``category=None``
    removes any existing override for ``code`` so it falls back to the
    default rules.
    """
    if category is not None and category not in _VALID_CATEGORIES:
        raise WatchlistPolicyError(
            f"category must be 'fast', 'slow', or None; got {category!r}"
        )
    new_overrides = dict(policy.overrides)
    if category is None:
        new_overrides.pop(code, None)
    else:
        new_overrides[code] = category
    return WatchlistPolicy(
        fast=policy.fast,
        slow=policy.slow,
        overrides=new_overrides,
        default_category=policy.default_category,
        policy_version=policy.policy_version,
        last_updated=policy.last_updated,
        fast_default_set=policy.fast_default_set,
        slow_default_set=policy.slow_default_set,
    )


def save_policy(policy: WatchlistPolicy, path: str | Path) -> None:
    """Persist ``policy`` back to ``path`` as YAML.

    Round-trip safe: ``load_policy(save_policy(p))`` yields an equal
    policy. Comments in the source file are NOT preserved (PyYAML
    limitation) — operators editing the file by hand should expect the
    canonical re-emission.
    """
    p = Path(path)
    payload: dict[str, Any] = {
        "fast": _bucket_to_dict(policy.fast),
        "slow": _bucket_to_dict(policy.slow),
        "overrides": dict(policy.overrides),
        "default_category": policy.default_category,
        "policy_version": policy.policy_version,
    }
    if policy.last_updated is not None:
        payload["last_updated"] = policy.last_updated

    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True)
    tmp.replace(p)


def _bucket_to_dict(bucket: BucketConfig) -> dict[str, Any]:
    return {
        "cron": bucket.cron,
        "pipeline": bucket.pipeline,
        "max_debate_rounds": bucket.max_debate_rounds,
        "pipeline_timeout_seconds": bucket.pipeline_timeout_seconds,
        "default_codes": list(bucket.default_codes),
    }

    async def save_financial_data(self, data: FinancialData) -> None:
        """Upsert a single financial data document."""
        coll = self._db["financial_data"]
        try:
            await coll.update_one(
                {"code": data.code, "report_date": data.report_date},
                {"$set": data.model_dump()},
                upsert=True,
            )
        except Exception as exc:
            self._log.warning("save_financial_failed", error=str(exc))

    async def save_news(self, articles: list[NewsArticle]) -> int:
        """Bulk upsert news articles by URL. Returns operation count."""
        if not articles:
            return 0

        coll = self._db["news_articles"]
        ops = [
            UpdateOne(
                {"url": a.url},
                {"$set": a.model_dump()},
                upsert=True,
            )
            for a in articles
        ]

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_news_failed", error=str(exc))
            return 0

    async def query_latest_quotes(
        self, codes: list[str]
    ) -> list[dict[str, Any]]:
        """Find latest quote per code."""
        coll = self._db["market_realtime"]
        cursor = coll.find({"code": {"$in": codes}}).sort(
            "timestamp", DESCENDING
        )
        return await cursor.to_list(length=len(codes) * 2)

    async def query_kline(
        self,
        code: str,
        start_date: str = "",
        end_date: str = "",
    ) -> list[dict[str, Any]]:
        """Query K-line data for a code within a date range."""
        query: dict[str, Any] = {"code": code}
        if start_date or end_date:
            date_filter: dict[str, str] = {}
            if start_date:
                date_filter["$gte"] = start_date
            if end_date:
                date_filter["$lte"] = end_date
            query["date"] = date_filter

        coll = self._db["kline_daily"]
        cursor = coll.find(query).sort("date", ASCENDING)
        return await cursor.to_list(length=10000)

    async def query_news(
        self,
        limit: int = 50,
        stock_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query news articles, optionally filtered by stock code."""
        query: dict[str, Any] = {}
        if stock_code:
            query["stock_codes"] = stock_code

        coll = self._db["news_articles"]
        cursor = coll.find(query).sort("publish_time", DESCENDING).limit(limit)
        return await cursor.to_list(length=limit)

    # -- Trading signal persistence --

    async def save_signal(self, signal: dict[str, Any]) -> str:
        """Save a TradingSignal dict to 'trading_signals' collection.

        Uses upsert on (stock_code, trade_date) to prevent duplicates.
        Returns the document _id as string.
        """
        coll = self._db["trading_signals"]
        key = {
            "stock_code": signal["stock_code"],
            "trade_date": signal["trade_date"],
        }
        result = await coll.update_one(key, {"$set": signal}, upsert=True)
        if result.upserted_id is not None:
            return str(result.upserted_id)
        doc = await coll.find_one(key, {"_id": 1})
        return str(doc["_id"])

    async def query_signals(
        self, stock_code: str | None = None, days: int = 30
    ) -> list[dict[str, Any]]:
        """Query recent trading signals.

        Args:
            stock_code: Filter by stock code. None = all stocks.
            days: Lookback window in days.

        Returns:
            Signals sorted by trade_date DESC, then stock_code ASC.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        query: dict[str, Any] = {"trade_date": {"$gte": cutoff}}
        if stock_code:
            query["stock_code"] = stock_code

        coll = self._db["trading_signals"]
        cursor = coll.find(query).sort(
            [("trade_date", DESCENDING), ("stock_code", ASCENDING)]
        )
        return await cursor.to_list(length=1000)

    async def query_signals_for_trade_date(
        self, trade_date: str, stock_codes: list[str]
    ) -> list[dict[str, Any]]:
        """Query signals for a specific trading day and stock set."""
        if not stock_codes:
            return []

        coll = self._db["trading_signals"]
        cursor = coll.find(
            {
                "trade_date": trade_date,
                "stock_code": {"$in": stock_codes},
            }
        ).sort("stock_code", ASCENDING)
        return await cursor.to_list(length=len(stock_codes))

    async def get_signal_by_id(self, signal_id: str) -> dict[str, Any] | None:
        """Retrieve a single signal by MongoDB ObjectId string."""
        from bson import ObjectId

        coll = self._db["trading_signals"]
        return await coll.find_one({"_id": ObjectId(signal_id)})

    # -- Index price persistence --

    async def save_index_prices(
        self, index_code: str, prices: list[dict[str, Any]]
    ) -> int:
        """Bulk upsert index prices to 'index_prices' collection."""
        if not prices:
            return 0

        coll = self._db["index_prices"]
        ops = [
            UpdateOne(
                {"index_code": index_code, "date": p["date"]},
                {"$set": {**p, "index_code": index_code}},
                upsert=True,
            )
            for p in prices
        ]

        try:
            result = await coll.bulk_write(ops, ordered=False)
            return result.upserted_count + getattr(result, "modified_count", 0)
        except Exception as exc:
            self._log.warning("save_index_prices_failed", error=str(exc))
            return 0

    async def get_index_prices(
        self,
        index_code: str,
        start_date: str = "",
        end_date: str = "",
    ) -> list[dict[str, Any]]:
        """Query index prices for a code within a date range, sorted by date ASC."""
        query: dict[str, Any] = {"index_code": index_code}
        if start_date or end_date:
            date_filter: dict[str, str] = {}
            if start_date:
                date_filter["$gte"] = start_date
            if end_date:
                date_filter["$lte"] = end_date
            query["date"] = date_filter

        coll = self._db["index_prices"]
        cursor = coll.find(query).sort("date", ASCENDING)
        return await cursor.to_list(length=10000)

    # -- Cost tracking persistence --

    async def save_cost_entry(self, entry: dict[str, Any]) -> None:
        """Upsert daily cost entry to 'cost_tracking' collection."""
        coll = self._db["cost_tracking"]
        key = {
            "date": entry["date"],
            "agent_name": entry["agent_name"],
            "provider": entry["provider"],
        }
        try:
            await coll.update_one(key, {"$set": entry}, upsert=True)
        except Exception as exc:
            self._log.warning("save_cost_entry_failed", error=str(exc))

    async def get_cost_history(self, days: int = 30) -> list[dict[str, Any]]:
        """Query cost history from MongoDB."""
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        coll = self._db["cost_tracking"]
        cursor = coll.find({"date": {"$gte": cutoff}}).sort("date", DESCENDING)
        return await cursor.to_list(length=10000)

    # -- Analysis record persistence --

    async def save_analysis_record(
        self, record: dict[str, Any]
    ) -> str:
    by_agent: dict[str, float] = {}
    by_provider: dict[str, float] = {}
    daily_totals: dict[str, float] = {}

    for entry in entries:
        total_cost += entry.cost_rmb
        total_requests += entry.requests
        total_prompt += entry.prompt_tokens
        total_completion += entry.completion_tokens

        by_agent[entry.agent_name] = (
            by_agent.get(entry.agent_name, 0.0) + entry.cost_rmb
        )
        by_provider[entry.provider] = (
            by_provider.get(entry.provider, 0.0) + entry.cost_rmb
        )
        daily_totals[entry.date] = (
            daily_totals.get(entry.date, 0.0) + entry.cost_rmb
        )

    return CostSummary(
        period=period,
        days=days,
        entries=tuple(entries),
        total_cost_rmb=round(total_cost, 4),
        total_requests=total_requests,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        by_agent={k: round(v, 4) for k, v in by_agent.items()},
        by_provider={k: round(v, 4) for k, v in by_provider.items()},
        daily_totals={k: round(v, 4) for k, v in daily_totals.items()},
    )


async def flush_to_mongodb(
    redis_client: redis.asyncio.Redis,
    mongodb: MongoDBService,
    days: int = 1,
) -> int:
    """Persist cost entries from Redis to MongoDB for durable storage.

    Args:
        redis_client: Async Redis client.
        mongodb: MongoDBService instance.
        days: Number of days to flush (default: today only).

    Returns:
        Count of entries persisted.
    """
    try:
        summary = await aggregate_costs(redis_client, days=days)
    except Exception as exc:
        log.warning("cost_flush_aggregate_failed", error=str(exc))
        return 0

    count = 0
    for entry in summary.entries:
        try:
            await mongodb.save_cost_entry({
                "date": entry.date,
                "agent_name": entry.agent_name,
                "provider": entry.provider,
                "prompt_tokens": entry.prompt_tokens,
                "completion_tokens": entry.completion_tokens,
                "requests": entry.requests,
                "cost_rmb": entry.cost_rmb,
            })
            count += 1
        except Exception as exc:
            log.warning(
                "cost_flush_entry_failed",
                date=entry.date,
                agent=entry.agent_name,
                error=str(exc),
            )

    log.info("cost_flush_complete", entries=count)
    return count

exec
/bin/bash -lc 'grep -R "completed_at" -n backend tests scripts | head -50 && grep -R "analysis_records" -n backend | head -100' in /home/ps/papers/QuantMind
 succeeded in 0ms:
grep: backend/api/__pycache__/analysis.cpython-312.pyc: binary file matches
grep: backend/api/__pycache__/monitoring.cpython-312.pyc: binary file matches
grep: backend/agents/__pycache__/collector.cpython-312.pyc: binary file matches
grep: backend/agents/__pycache__/records.cpython-312.pyc: binary file matches
grep: backend/services/__pycache__/phase5b_exit_check.cpython-312.pyc: binary file matches
grep: backend/services/__pycache__/analysis_stream.cpython-312.pyc: binary file matches
grep: tests/__pycache__/test_analysis_history_endpoint.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_scheduler_fast_slow.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_phase5b_exit_check.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_record_persistence.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_stream.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_scheduler.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_api_analysis.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_scheduler_budget.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_run_failures.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_llm_preflight.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_detail_mapping.cpython-312-pytest-8.4.1.pyc: binary file matches
backend/api/monitoring.py:61:    created_at = latest.get("completed_at") or latest.get("created_at")
backend/api/analysis.py:249:        "completed_at": doc.get("completed_at"),
backend/api/analysis.py:265:        "timestamp": step.get("completed_at") or step.get("started_at") or "",
backend/agents/collector.py:136:        completed_at = datetime.now(tz=UTC)
backend/agents/collector.py:142:            completed_at=completed_at,
backend/agents/collector.py:157:                "timestamp": completed_at.isoformat(),
backend/agents/collector.py:178:        completed_at = datetime.now(tz=UTC)
backend/agents/collector.py:184:            completed_at=completed_at,
backend/agents/collector.py:199:                "timestamp": completed_at.isoformat(),
backend/agents/collector.py:298:        completed_at = datetime.now(tz=UTC) if status != "running" else None
backend/agents/collector.py:316:            completed_at=completed_at,
backend/agents/records.py:63:    completed_at: datetime | None = None
backend/agents/records.py:133:    completed_at: datetime | None = None
backend/agents/records.py:162:    completed_at: datetime | None = None
backend/services/phase5b_exit_check.py:8:  completed_at + watchlist policy categorisation)
backend/services/phase5b_exit_check.py:153:    completed = record.get("completed_at")
backend/services/analysis_stream.py:50:    completed_at: datetime | None = None
backend/services/analysis_stream.py:131:            job.completed_at = datetime.now(tz=UTC)
backend/services/analysis_stream.py:136:            job.completed_at = datetime.now(tz=UTC)
backend/services/analysis_stream.py:267:            if job.completed_at is not None
backend/services/analysis_stream.py:268:            and (now - job.completed_at) > self._retention
tests/test_analysis_scheduler.py:43:        completed_at=now,
tests/test_analysis_scheduler.py:185:            completed_at=datetime.now(tz=UTC),
tests/test_analysis_scheduler_fast_slow.py:65:        completed_at=now,
tests/test_phase5b_exit_check.py:75:        "completed_at": started_at + datetime.timedelta(seconds=duration_sec),
tests/test_phase5b_exit_check.py:318:                "completed_at": datetime.datetime(
tests/test_analysis_scheduler_budget.py:69:        completed_at=now,
tests/test_api_analysis.py:40:        completed_at=datetime.now(tz=UTC),
tests/test_analysis_stream.py:30:        completed_at=now,
tests/test_analysis_record_persistence.py:69:        "completed_at": now,
tests/test_llm_preflight.py:201:                    completed_at=now,
tests/test_analysis_detail_mapping.py:20:    completed_at: str = "2026-04-25T10:20:00+00:00",
tests/test_analysis_detail_mapping.py:33:        "completed_at": completed_at,
tests/test_analysis_run_failures.py:33:        completed_at=datetime.now(tz=UTC),
tests/test_analysis_history_endpoint.py:46:        "completed_at": iso,
grep: backend/api/__pycache__/analysis.cpython-312.pyc: binary file matches
grep: backend/data/__pycache__/database.cpython-312.pyc: binary file matches
grep: backend/agents/__pycache__/records.cpython-312.pyc: binary file matches
grep: backend/services/__pycache__/phase5b_exit_check.cpython-312.pyc: binary file matches
backend/api/analysis.py:235:    """Shape a stored analysis_records doc for the history list."""
backend/api/analysis.py:281:    """Shape a stored analysis_records doc for the detail endpoint.
backend/api/analysis.py:355:    """List AnalysisDebate history entries sourced from analysis_records."""
backend/api/analysis.py:361:    docs = await mongodb.query_analysis_records(
backend/data/database.py:109:        analysis_records = self._db["analysis_records"]
backend/data/database.py:110:        await analysis_records.create_index(
backend/data/database.py:115:        await analysis_records.create_index(
backend/data/database.py:124:        # so multiple analysis_records for the same trading day legitimately
backend/data/database.py:126:        await analysis_records.create_index(
backend/data/database.py:131:        await analysis_records.create_index(
backend/data/database.py:141:        await analysis_records.create_index(
backend/data/database.py:145:        await analysis_records.create_index(
backend/data/database.py:421:        """Upsert a full AnalysisRecord to `analysis_records` by run_id.
backend/data/database.py:427:        coll = self._db["analysis_records"]
backend/data/database.py:435:    async def query_analysis_records(
backend/data/database.py:454:        coll = self._db["analysis_records"]
backend/data/database.py:469:        coll = self._db["analysis_records"]
backend/data/database.py:506:        """Most recent analysis_records row, or None if empty."""
backend/data/database.py:507:        coll = self._db["analysis_records"]
backend/agents/records.py:108:    One per run. Persisted in `analysis_records` MongoDB collection keyed
backend/services/phase5b_exit_check.py:7:* fast / slow pipeline latency (from analysis_records.created_at /
backend/services/phase5b_exit_check.py:96:      the analysis_records collection. The function then re-scales the
backend/services/phase5b_exit_check.py:132:    """Partition analysis_records into ``fast`` and ``slow`` buckets.

exec
/bin/bash -lc "sed -n '400,540p' backend/data/database.py && sed -n '100,190p' backend/agents/records.py && sed -n '1,100p' tests/test_analysis_record_persistence.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
            "provider": entry["provider"],
        }
        try:
            await coll.update_one(key, {"$set": entry}, upsert=True)
        except Exception as exc:
            self._log.warning("save_cost_entry_failed", error=str(exc))

    async def get_cost_history(self, days: int = 30) -> list[dict[str, Any]]:
        """Query cost history from MongoDB."""
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        coll = self._db["cost_tracking"]
        cursor = coll.find({"date": {"$gte": cutoff}}).sort("date", DESCENDING)
        return await cursor.to_list(length=10000)

    # -- Analysis record persistence --

    async def save_analysis_record(
        self, record: dict[str, Any]
    ) -> str:
        """Upsert a full AnalysisRecord to `analysis_records` by run_id.

        Same run_id replays overwrite; distinct run_ids accumulate so
        re-runs on the same stock/date preserve every trail. Returns the
        document _id as string.
        """
        coll = self._db["analysis_records"]
        key = {"run_id": record["run_id"]}
        result = await coll.update_one(key, {"$set": record}, upsert=True)
        if result.upserted_id is not None:
            return str(result.upserted_id)
        doc = await coll.find_one(key, {"_id": 1})
        return str(doc["_id"])

    async def query_analysis_records(
        self,
        stock_code: str | None = None,
        trade_date: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Query recent analysis records.

        Sorted by created_at DESC so the most recent runs are first. When
        multiple runs share a (stock_code, trade_date) pair, all are
        returned (we never collapse runs into a single row).
        """
        query: dict[str, Any] = {}
        if stock_code:
            query["stock_code"] = stock_code
        if trade_date:
            query["trade_date"] = trade_date

        bounded = max(1, min(limit, 500))
        coll = self._db["analysis_records"]
        cursor = coll.find(query).sort("created_at", DESCENDING).limit(bounded)
        return await cursor.to_list(length=bounded)

    async def get_analysis_record_by_id(
        self, record_id: str
    ) -> dict[str, Any] | None:
        """Retrieve a single analysis record.

        Matches either the MongoDB ObjectId string or the run_id UUID.
        Returns None for any invalid id; never raises ObjectId errors.
        """
        from bson import ObjectId
        from bson.errors import InvalidId

        coll = self._db["analysis_records"]
        try:
            oid = ObjectId(record_id)
        except (InvalidId, TypeError, ValueError):
            return await coll.find_one({"run_id": record_id})
        doc = await coll.find_one({"_id": oid})
        if doc is None:
            doc = await coll.find_one({"run_id": record_id})
        return doc

    # -- Monitoring helpers (Session C) --

    async def count_signals_for_date(self, date: str) -> int:
        """Number of trading_signals rows with trade_date == date."""
        coll = self._db["trading_signals"]
        return await coll.count_documents({"trade_date": date})

    async def count_signals_since(self, cutoff: str) -> int:
        """Number of trading_signals rows with trade_date >= cutoff."""
        coll = self._db["trading_signals"]
        return await coll.count_documents({"trade_date": {"$gte": cutoff}})

    async def sum_cost_for_date(self, date: str) -> float:
        """Total CNY cost recorded in cost_tracking on a given date."""
        coll = self._db["cost_tracking"]
        pipeline = [
            {"$match": {"date": date}},
            {"$group": {"_id": None, "total": {"$sum": "$cost_cny"}}},
        ]
        async for doc in coll.aggregate(pipeline):
            total = doc.get("total")
            return float(total) if total is not None else 0.0
        return 0.0

    async def get_latest_analysis_record(
        self,
    ) -> dict[str, Any] | None:
        """Most recent analysis_records row, or None if empty."""
        coll = self._db["analysis_records"]
        cursor = coll.find({}).sort("created_at", DESCENDING).limit(1)
        docs = await cursor.to_list(length=1)
        return docs[0] if docs else None
    risk_score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    step: AgentStepRecord


class AnalysisRecord(BaseModel):
    """Complete multi-agent analysis run record.

    One per run. Persisted in `analysis_records` MongoDB collection keyed
    by `run_id`. History view and detail view both read from here.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    stock_code: str
    stock_name: str
    trade_date: str
    status: AnalysisRunStatus = "running"
    max_rounds: int = 2
    current_round: int = 0

    steps: list[AgentStepRecord] = Field(default_factory=list)
    analysts: list[AgentStepRecord] = Field(default_factory=list)
    intelligence_officer: AgentStepRecord | None = None
    debates: list[DebateRoundRecord] = Field(default_factory=list)
    risk_assessment: RiskAssessmentRecord | None = None
    decision: FundManagerRecord | None = None

    signal_id: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=UTC)
    )
    completed_at: datetime | None = None
    error: str | None = None


class AnalysisRunResult(BaseModel):
    """Bundle returned by run_analysis(): terminal signal + full record."""

    model_config = ConfigDict(frozen=True)

    signal: TradingSignal
    record: AnalysisRecord


class AnalysisSummary(BaseModel):
    """Compact row for the history list endpoint."""

    model_config = ConfigDict(frozen=True)

    id: str
    run_id: str
    stock_code: str
    stock_name: str
    trade_date: str
    status: AnalysisRunStatus
    action: Literal["买入", "持有", "卖出"] | None = None
    confidence: float | None = None
    risk_score: float | None = None
    signal_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
"""Tests for analysis_records persistence methods on MongoDBService."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

from backend.data.database import MongoDBService

_OID_1 = str(ObjectId())
_OID_2 = str(ObjectId())


def _make_records_collection(
    docs: list[dict] | None = None,
    upserted_id: ObjectId | None = None,
    find_one_doc: dict | None = None,
) -> AsyncMock:
    coll = AsyncMock()
    coll.create_index = AsyncMock()

    coll.update_one = AsyncMock(
        return_value=MagicMock(upserted_id=upserted_id)
    )
    coll.find_one = AsyncMock(return_value=find_one_doc)

    cursor = AsyncMock()
    cursor.to_list = AsyncMock(return_value=docs or [])
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    coll.find = MagicMock(return_value=cursor)

    return coll


def _make_mock_db(records_coll: AsyncMock) -> MagicMock:
    db = MagicMock()
    default = AsyncMock()
    default.create_index = AsyncMock()

    def getitem(name: str) -> AsyncMock:
        return records_coll if name == "analysis_records" else default

    db.__getitem__ = MagicMock(side_effect=getitem)
    return db


def _sample_record_dict(run_id: str = "run-abc") -> dict:
    now = datetime(2026, 4, 24, 9, 50, tzinfo=UTC).isoformat()
    return {
        "run_id": run_id,
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "trade_date": "2026-04-24",
        "status": "completed",
        "max_rounds": 2,
        "current_round": 2,
        "steps": [],
        "analysts": [],
        "intelligence_officer": None,
        "debates": [],
        "risk_assessment": None,
        "decision": None,
        "signal_id": None,
        "created_at": now,
        "completed_at": now,
        "error": None,
    }


class TestSaveAnalysisRecord:
    """Tests for save_analysis_record method."""

    @pytest.mark.asyncio
    async def test_inserts_and_returns_id_on_upsert(self) -> None:
        coll = _make_records_collection(upserted_id=ObjectId(_OID_1))
        service = MongoDBService(_make_mock_db(coll))

        record = _sample_record_dict()
        result_id = await service.save_analysis_record(record)

        assert isinstance(result_id, str)
        assert result_id == _OID_1
        coll.update_one.assert_called_once()
        call_args = coll.update_one.call_args
        assert call_args[0][0] == {"run_id": record["run_id"]}
        assert call_args[0][1] == {"$set": record}
        assert call_args[1]["upsert"] is True

    @pytest.mark.asyncio
    async def test_returns_existing_id_on_update(self) -> None:
        coll = _make_records_collection(
            upserted_id=None, find_one_doc={"_id": ObjectId(_OID_2)}
        )
        service = MongoDBService(_make_mock_db(coll))

        result_id = await service.save_analysis_record(_sample_record_dict())

exec
/bin/bash -lc "grep -R \"save_analysis_record\" -n backend tests | head -60 && sed -n '250,350p' backend/agents/collector.py && sed -n '1,140p' tests/test_phase5b_exit_check.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
grep: backend/api/__pycache__/analysis.cpython-312.pyc: binary file matches
grep: backend/data/__pycache__/analysis_scheduler.cpython-312.pyc: binary file matches
grep: backend/data/__pycache__/database.cpython-312.pyc: binary file matches
grep: tests/__pycache__/test_analysis_scheduler_fast_slow.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_record_persistence.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_stream.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_scheduler.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_scheduler_budget.cpython-312-pytest-8.4.1.pyc: binary file matches
grep: tests/__pycache__/test_analysis_run_failures.cpython-312-pytest-8.4.1.pyc: binary file matches
backend/api/analysis.py:147:                await mongodb.save_analysis_record(
backend/api/analysis.py:151:                log.warning("save_analysis_record_unavailable")
backend/api/analysis.py:170:                await mongodb.save_analysis_record(
backend/api/analysis.py:174:                log.warning("save_analysis_record_unavailable")
backend/api/analysis.py:453:                record_id = await mongodb.save_analysis_record(
backend/api/analysis.py:503:            record_id = await mongodb.save_analysis_record(
backend/api/analysis.py:507:            log.warning("jobs_save_analysis_record_unavailable")
backend/data/database.py:418:    async def save_analysis_record(
backend/data/analysis_scheduler.py:503:                await self._mongodb.save_analysis_record(
backend/data/analysis_scheduler.py:528:            await self._mongodb.save_analysis_record(
backend/data/analysis_scheduler.py:534:                "save_analysis_record_unavailable", code=stock_code
backend/data/analysis_scheduler.py:538:                "save_analysis_record_failed",
backend/data/analysis_scheduler.py:604:            await self._mongodb.save_analysis_record(
backend/data/analysis_scheduler.py:638:            await self._mongodb.save_analysis_record(
tests/test_analysis_scheduler.py:72:    mongodb.save_analysis_record = AsyncMock(return_value="record_id")
tests/test_analysis_scheduler.py:141:        Each successful run must call save_analysis_record with the
tests/test_analysis_scheduler.py:156:        assert mock_mongodb.save_analysis_record.call_count == 3
tests/test_analysis_scheduler.py:157:        for call in mock_mongodb.save_analysis_record.await_args_list:
tests/test_analysis_scheduler.py:207:        assert mock_mongodb.save_analysis_record.call_count == 3
tests/test_analysis_scheduler.py:208:        first_doc = mock_mongodb.save_analysis_record.await_args_list[0].args[0]
tests/test_analysis_scheduler_fast_slow.py:105:    m.save_analysis_record = AsyncMock(return_value="rec_id")
tests/test_analysis_scheduler_fast_slow.py:359:        mongodb.save_analysis_record.assert_awaited_once()
tests/test_analysis_scheduler_fast_slow.py:360:        record_payload = mongodb.save_analysis_record.await_args[0][0]
tests/test_analysis_scheduler_fast_slow.py:521:        mongodb.save_analysis_record.assert_awaited_once()
tests/test_analysis_scheduler_fast_slow.py:522:        record_payload = mongodb.save_analysis_record.await_args[0][0]
tests/test_analysis_scheduler_budget.py:80:    mongodb.save_analysis_record = AsyncMock(return_value="rec_id")
tests/test_analysis_scheduler_budget.py:115:        scheduler._mongodb.save_analysis_record.assert_awaited_once()
tests/test_analysis_scheduler_budget.py:116:        record_payload = scheduler._mongodb.save_analysis_record.await_args[0][0]
tests/test_analysis_scheduler_budget.py:188:        mongodb.save_analysis_record = AsyncMock(return_value="rec_id")
tests/test_analysis_scheduler_budget.py:214:        scheduler._mongodb.save_analysis_record = AsyncMock(
tests/test_analysis_stream.py:71:    mongodb.save_analysis_record = AsyncMock(return_value="record-xyz")
tests/test_analysis_stream.py:275:        save_analysis_record, (b) the terminal SSE event carries a
tests/test_analysis_stream.py:318:            mock_state.save_analysis_record.assert_awaited()
tests/test_analysis_record_persistence.py:75:    """Tests for save_analysis_record method."""
tests/test_analysis_record_persistence.py:83:        result_id = await service.save_analysis_record(record)
tests/test_analysis_record_persistence.py:100:        result_id = await service.save_analysis_record(_sample_record_dict())
tests/test_analysis_record_persistence.py:109:        await service.save_analysis_record(record)
tests/test_analysis_record_persistence.py:110:        await service.save_analysis_record({**record, "status": "failed"})
tests/test_analysis_run_failures.py:90:        mongodb.save_analysis_record = AsyncMock(return_value="rec-1")
tests/test_analysis_run_failures.py:114:        mongodb_mock.save_analysis_record.assert_awaited_once()
tests/test_analysis_run_failures.py:115:        saved_doc = mongodb_mock.save_analysis_record.call_args[0][0]
tests/test_analysis_run_failures.py:149:        mongodb_mock.save_analysis_record.assert_awaited_once()
                event_type=event.get("event_type"),
            )

    def finalize(
        self,
        *,
        status: str,
        signal: TradingSignal | None,
        error: str | None = None,
    ) -> AnalysisRecord:
        """Build the terminal AnalysisRecord from accumulated steps."""
        analysts = [s for s in self._steps if s.agent in ANALYST_AGENTS]

        intelligence = next(
            (s for s in self._steps if s.agent == "intelligence_officer"),
            None,
        )

        debates = self._build_debate_rounds()

        risk_step = next(
            (s for s in self._steps if s.agent == "risk_officer"), None
        )
        risk = (
            RiskAssessmentRecord(content=risk_step.content, step=risk_step)
            if risk_step is not None
            else None
        )

        fund_step = next(
            (s for s in self._steps if s.agent == "fund_manager"), None
        )
        decision = None
        if fund_step is not None and signal is not None:
            decision = FundManagerRecord(
                action=signal.action,
                target_price=signal.target_price,
                confidence=signal.confidence,
                risk_score=signal.risk_score,
                reasoning=signal.reasoning,
                step=fund_step,
            )

        current_round = max(
            (s.round for s in self._steps if s.round > 0),
            default=0,
        )

        completed_at = datetime.now(tz=UTC) if status != "running" else None

        return AnalysisRecord(
            run_id=self._run_id,
            stock_code=self._stock_code,
            stock_name=self._stock_name,
            trade_date=self._trade_date,
            status=status,  # type: ignore[arg-type]
            max_rounds=self._max_rounds,
            current_round=current_round,
            steps=list(self._steps),
            analysts=analysts,
            intelligence_officer=intelligence,
            debates=debates,
            risk_assessment=risk,
            decision=decision,
            signal_id=None,
            created_at=self._created_at,
            completed_at=completed_at,
            error=error,
        )

    def _build_debate_rounds(self) -> list[DebateRoundRecord]:
        """Group bull/bear steps by round number."""
        by_round: dict[int, dict[str, AgentStepRecord]] = {}
        for step in self._steps:
            if step.agent not in ("bull_researcher", "bear_researcher"):
                continue
            if step.round <= 0:
                continue
            bucket = by_round.setdefault(step.round, {})
            side = "bull" if step.agent == "bull_researcher" else "bear"
            bucket[side] = step
        rounds: list[DebateRoundRecord] = []
        for r in sorted(by_round.keys()):
            b = by_round[r]
            rounds.append(
                DebateRoundRecord(
                    round=r,
                    bull=b.get("bull"),
                    bear=b.get("bear"),
                )
            )
        return rounds


__all__ = [
    "ANALYST_AGENTS",
    "EventEmitter",
    "RunCollector",
    "classify_status",
    "extract_content",
]
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
        cost_entries = [
            {"date": "2026-05-02", "agent_name": "x", "cost_rmb": float("nan")},
        ]
        out = aggregate_per_run_costs(
            cost_entries, record_index={"r1": "2026-05-02"}
        )
        assert out["r1"]["cost_rmb"] == 0.0

    def test_no_runs_for_day_yields_empty(self) -> None:
        out = aggregate_per_run_costs(
            [{"date": "2026-05-02", "agent_name": "x", "cost_rmb": 1.0}],
            record_index={},
        )
        assert out == {}


# ----------------------------------------------------------------------
# Group 2: split_runs_by_category
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestSplitRunsByCategory:
    def test_partitions_by_policy(self) -> None:
        records = [
            {"stock_code": "600519"},
            {"stock_code": "000001"},
            {"stock_code": "300750"},  # falls through to default_category=slow
        ]
        buckets = split_runs_by_category(records, _policy())
        assert len(buckets["fast"]) == 1
        assert len(buckets["slow"]) == 2

    def test_drops_records_missing_stock_code(self) -> None:

exec
/bin/bash -lc "sed -n '140,420p' tests/test_phase5b_exit_check.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
    def test_drops_records_missing_stock_code(self) -> None:
        records = [
            {},  # no stock_code
            {"stock_code": "600519"},
            {"stock_code": ""},  # empty
        ]
        buckets = split_runs_by_category(records, _policy())
        assert len(buckets["fast"]) == 1
        assert len(buckets["slow"]) == 0


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

exec
/bin/bash -lc "sed -n '1,220p' tests/test_scripts_phase5b_exit_check.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""CLI smoke test for scripts/phase5b_exit_check.py.

The aggregation math is exhaustively covered by
tests/test_phase5b_exit_check.py; we only verify the script's argument
parser surface here, since the production path opens motor + redis
which can't be instantiated in unit tests.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "phase5b_exit_check.py"
)


def _load_script_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "_phase5b_exit_cli", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestExitCheckCLI:
    def test_missing_policy_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        module = _load_script_module()
        missing = tmp_path / "watchlist_policy.yaml"
        rc = module.main(["--policy-path", str(missing)])
        assert rc == 2
        err = capsys.readouterr().err
        assert "watchlist_policy.yaml not found" in err

    def test_arg_parser_defaults(self) -> None:
        module = _load_script_module()
        ns = module._parse_args(["--days", "3"])
        assert ns.days == 3
        assert ns.strict is False
        assert ns.policy_path == Path("config/watchlist_policy.yaml")

exec
/bin/bash -lc "sed -n '1,220p' tests/test_scripts_shadow_compare.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
"""CLI smoke tests for scripts/shadow_compare.py.

The math is exhaustively covered by tests/test_shadow_compare.py; here
we focus on argument plumbing, JSONL parsing, and exit codes.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "shadow_compare.py"


def _load_script_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "_shadow_compare_cli", _SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc(
    *,
    base_action: str = "买入",
    routed_action: str = "买入",
    base_conf: float = 0.7,
    routed_conf: float = 0.7,
    trade_date: str = "2026-05-02",
) -> dict:
    return {
        "run_id": "r1",
        "stock_code": "600519",
        "trade_date": trade_date,
        "baseline": {
            "action": base_action,
            "confidence": base_conf,
            "model": "kimi-k2.6",
            "latency_ms": 1000.0,
            "escalated": False,
            "parse_ok": True,
        },
        "routed": {
            "action": routed_action,
            "confidence": routed_conf,
            "model": "qwen3.6-plus",
            "latency_ms": 1500.0,
            "escalated": False,
            "parse_ok": True,
        },
    }


@pytest.mark.unit
class TestShadowCompareCLI:
    def test_jsonl_happy_path_returns_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "shadow.jsonl"
        path.write_text(
            "\n".join(json.dumps(_doc()) for _ in range(20)) + "\n",
            encoding="utf-8",
        )
        module = _load_script_module()
        rc = module.main(["--input", str(path)])
        assert rc == 0
        captured = capsys.readouterr().out
        assert "Total pairs: **20**" in captured
        assert "✅ action_match" in captured

    def test_strict_returns_one_on_failure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "shadow.jsonl"
        path.write_text(
            json.dumps(_doc(routed_action="持有")) + "\n",
            encoding="utf-8",
        )
        module = _load_script_module()
        rc = module.main(["--input", str(path), "--strict"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "❌ action_match" in out

    def test_malformed_jsonl_raises(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "shadow.jsonl"
        path.write_text("not json\n", encoding="utf-8")
        module = _load_script_module()
        with pytest.raises(SystemExit):
            module.main(["--input", str(path)])

    def test_missing_input_file_raises(self, tmp_path: Path) -> None:
        module = _load_script_module()
        missing = tmp_path / "does-not-exist.jsonl"
        with pytest.raises(SystemExit):
            module.main(["--input", str(missing)])

    def test_blank_jsonl_lines_skipped(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "shadow.jsonl"
        path.write_text(
            f"\n{json.dumps(_doc())}\n   \n",
            encoding="utf-8",
        )
        module = _load_script_module()
        rc = module.main(["--input", str(path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Total pairs: **1**" in out

exec
/bin/bash -lc 'pytest -q tests/test_shadow_compare.py tests/test_shadow_recorder.py tests/test_phase5b_exit_check.py tests/test_scripts_shadow_compare.py tests/test_scripts_phase5b_exit_check.py' in /home/ps/papers/QuantMind
 succeeded in 439ms:
........................................................................ [100%]
72 passed in 0.09s

exec
/bin/bash -lc "nl -ba scripts/phase5b_exit_check.py | sed -n '70,125p'; nl -ba backend/services/phase5b_exit_check.py | sed -n '80,125p'; nl -ba backend/services/phase5b_exit_check.py | sed -n '190,245p'; nl -ba backend/services/phase5b_exit_check.py | sed -n '275,330p'; nl -ba scripts/shadow_compare.py | sed -n '120,150p'" in /home/ps/papers/QuantMind
 succeeded in 0ms:
    70	    parser.add_argument(
    71	        "--strict",
    72	        action="store_true",
    73	        help="Exit non-zero on first failing gate.",
    74	    )
    75	    return parser.parse_args(argv)
    76	
    77	
    78	async def _gather_inputs(
    79	    args: argparse.Namespace,
    80	) -> tuple[
    81	    list[dict[str, Any]],
    82	    list[dict[str, Any]],
    83	    list[dict[str, Any]],
    84	]:
    85	    """Pull records / cost entries / shadow docs from live infra.
    86	
    87	    We open the Mongo client and Redis client locally and close them
    88	    before returning so the caller (sync ``main``) does not have to know
    89	    about async lifetimes.
    90	    """
    91	    from motor.motor_asyncio import AsyncIOMotorClient
    92	    from redis.asyncio import Redis
    93	
    94	    from backend.data.database import MongoDBService
    95	    from backend.llm.cost_tracker import aggregate_costs
    96	    from backend.services.shadow_recorder import query_shadow_decisions
    97	
    98	    client = AsyncIOMotorClient(args.mongo_uri)
    99	    redis: Redis | None = None
   100	    try:
   101	        db = client[args.mongo_db]
   102	        service = MongoDBService(db)
   103	
   104	        records_cursor = service._db["analysis_records"].find().sort(  # noqa: SLF001
   105	            "created_at", -1
   106	        ).limit(args.days * 200)
   107	        records = [doc async for doc in records_cursor]
   108	
   109	        shadow_docs = await query_shadow_decisions(service, days=args.days)
   110	
   111	        redis = Redis.from_url(args.redis_url, decode_responses=True)
   112	        summary = await aggregate_costs(redis, days=args.days)
   113	        cost_entries = [
   114	            {
   115	                "date": entry.date,
   116	                "agent_name": entry.agent_name,
   117	                "cost_rmb": entry.cost_rmb,
   118	            }
   119	            for entry in summary.entries
   120	        ]
   121	        return list(records), cost_entries, list(shadow_docs)
   122	    finally:
   123	        if redis is not None:
   124	            await redis.aclose()
   125	        client.close()
    80	def aggregate_per_run_costs(
    81	    cost_entries: Iterable[Mapping[str, Any]],
    82	    *,
    83	    record_index: Mapping[str, str],
    84	) -> dict[str, dict[str, float]]:
    85	    """Bucket Redis cost entries into per-run totals.
    86	
    87	    Inputs:
    88	
    89	    * ``cost_entries`` — ``llm:usage:{date}:{agent}:{provider}`` rows
    90	      flattened to dicts with keys ``date``, ``agent_name``, ``cost_rmb``.
    91	      Phase 5B-T03 splits agent names with ``/triage`` and
    92	      ``/escalation`` suffixes; we strip them to attribute spend back to
    93	      the parent agent. Per-run isn't directly recoverable from Redis
    94	      keys (they aggregate per day per agent), so callers pass an
    95	      auxiliary ``record_index`` mapping ``run_id → trade_date`` from
    96	      the analysis_records collection. The function then re-scales the
    97	      day's total by the number of runs we know happened on that day.
    98	    * ``record_index`` — ``run_id → trade_date`` for the runs in scope.
    99	
   100	    Returns ``{run_id: {"date": str, "cost_rmb": float}}``.
   101	    """
   102	    daily_totals: dict[str, float] = {}
   103	    for entry in cost_entries:
   104	        date = entry.get("date")
   105	        cost = entry.get("cost_rmb")
   106	        if not isinstance(date, str) or not isinstance(cost, (int, float)):
   107	            continue
   108	        cost_f = float(cost)
   109	        if not math.isfinite(cost_f) or cost_f < 0:
   110	            continue
   111	        daily_totals[date] = daily_totals.get(date, 0.0) + cost_f
   112	
   113	    by_day: dict[str, list[str]] = {}
   114	    for run_id, date in record_index.items():
   115	        by_day.setdefault(date, []).append(run_id)
   116	
   117	    per_run: dict[str, dict[str, float]] = {}
   118	    for date, run_ids in by_day.items():
   119	        n = len(run_ids)
   120	        if n == 0:
   121	            continue
   122	        share = daily_totals.get(date, 0.0) / n
   123	        for run_id in run_ids:
   124	            per_run[run_id] = {"date": date, "cost_rmb": round(share, 6)}
   125	    return per_run
   190	    # threshold is "per stock" worst-case rather than mean.
   191	    fast_costs = [
   192	        per_run_cost[r["run_id"]]["cost_rmb"]
   193	        for r in bucketed["fast"]
   194	        if r["run_id"] in per_run_cost
   195	    ]
   196	    slow_costs = [
   197	        per_run_cost[r["run_id"]]["cost_rmb"]
   198	        for r in bucketed["slow"]
   199	        if r["run_id"] in per_run_cost
   200	    ]
   201	    fast_latencies = [
   202	        sec
   203	        for r in bucketed["fast"]
   204	        if (sec := _record_latency_seconds(r)) is not None
   205	    ]
   206	    slow_latencies = [
   207	        sec
   208	        for r in bucketed["slow"]
   209	        if (sec := _record_latency_seconds(r)) is not None
   210	    ]
   211	    daily_totals = {
   212	        per["date"]: 0.0 for per in per_run_cost.values()
   213	    }
   214	    for per in per_run_cost.values():
   215	        daily_totals[per["date"]] += per["cost_rmb"]
   216	    daily_total_p95 = (
   217	        _percentile(list(daily_totals.values()), 95) if daily_totals else 0.0
   218	    )
   219	
   220	    cost_metric = CostMetric(
   221	        fast_p95_rmb=round(_percentile(fast_costs, 95), 4),
   222	        slow_p95_rmb=round(_percentile(slow_costs, 95), 4),
   223	        daily_total_rmb=round(daily_total_p95, 4),
   224	        fast_runs=len(fast_costs),
   225	        slow_runs=len(slow_costs),
   226	    )
   227	    latency_metric = LatencyMetric(
   228	        fast_p95_sec=round(_percentile(fast_latencies, 95), 2),
   229	        slow_p95_sec=round(_percentile(slow_latencies, 95), 2),
   230	        fast_runs=len(fast_latencies),
   231	        slow_runs=len(slow_latencies),
   232	    )
   233	    shadow_report = compute_shadow_report(shadow_list) if shadow_list else None
   234	
   235	    has_data = {
   236	        "fast_cost": cost_metric.fast_runs > 0,
   237	        "slow_cost": cost_metric.slow_runs > 0,
   238	        "daily_total": bool(daily_totals),
   239	        "fast_latency": latency_metric.fast_runs > 0,
   240	        "slow_latency": latency_metric.slow_runs > 0,
   241	        "shadow": shadow_report is not None and shadow_report.total_pairs > 0,
   242	    }
   243	
   244	    passes = {
   245	        "fast_cost": (
   275	    }
   276	
   277	    return ExitGateReport(
   278	        days=days,
   279	        cost=cost_metric,
   280	        latency=latency_metric,
   281	        shadow=shadow_report,
   282	        has_data=has_data,
   283	        passes=passes,
   284	    )
   285	
   286	
   287	def _percentile(values: list[float], q: float) -> float:
   288	    if not values:
   289	        return 0.0
   290	    q = max(0.0, min(100.0, q))
   291	    sorted_values = sorted(values)
   292	    if len(sorted_values) == 1:
   293	        return sorted_values[0]
   294	    pos = q / 100 * (len(sorted_values) - 1)
   295	    lower = math.floor(pos)
   296	    upper = math.ceil(pos)
   297	    if lower == upper:
   298	        return sorted_values[lower]
   299	    weight = pos - lower
   300	    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
   301	
   302	
   303	def render_markdown(report: ExitGateReport) -> str:
   304	    """Render an :class:`ExitGateReport` as a single markdown gate table."""
   305	
   306	    def fmt_pass(name: str) -> str:
   307	        ok = report.passes.get(name, False)
   308	        has = report.has_data.get(name.split("_")[0], True)
   309	        if not has and not ok:
   310	            return "⚠️ no-data"
   311	        return "✅" if ok else "❌"
   312	
   313	    lines = [
   314	        "# Phase 5B Exit Gate",
   315	        "",
   316	        f"Window: last **{report.days}** days.",
   317	        "",
   318	        "| Gate | Threshold | Observed | Status |",
   319	        "|------|-----------|---------:|:------:|",
   320	        f"| fast_cost | ≤ {FAST_COST_PER_STOCK_RMB} RMB / stock | "
   321	        f"p95 {report.cost.fast_p95_rmb:.4f} RMB ({report.cost.fast_runs} runs) | "
   322	        f"{fmt_pass('fast_cost')} |",
   323	        f"| slow_cost | ≤ {SLOW_COST_PER_STOCK_RMB} RMB / stock | "
   324	        f"p95 {report.cost.slow_p95_rmb:.4f} RMB ({report.cost.slow_runs} runs) | "
   325	        f"{fmt_pass('slow_cost')} |",
   326	        f"| daily_total | ≤ {DAILY_TOTAL_RMB} RMB / day | "
   327	        f"p95 {report.cost.daily_total_rmb:.4f} RMB | "
   328	        f"{fmt_pass('daily_total')} |",
   329	        f"| fast_latency | p95 ≤ {FAST_P95_LATENCY_SEC}s | "
   330	        f"{report.latency.fast_p95_sec:.2f}s ({report.latency.fast_runs} runs) | "
   120	
   121	def _load_docs(args: argparse.Namespace) -> Iterable[dict[str, Any]]:
   122	    if args.input is not None:
   123	        if not args.input.exists():
   124	            raise SystemExit(f"input file does not exist: {args.input}")
   125	        return _read_jsonl(args.input)
   126	    return asyncio.run(_read_mongo(args))
   127	
   128	
   129	def main(argv: list[str] | None = None) -> int:
   130	    args = _parse_args(argv)
   131	    docs = list(_load_docs(args))
   132	    report = compute_shadow_report(docs)
   133	    sys.stdout.write(render_markdown(report))
   134	    if args.strict and not all(report.passes.values()):
   135	        return 1
   136	    return 0
   137	
   138	
   139	if __name__ == "__main__":
   140	    raise SystemExit(main())

exec
/bin/bash -lc "sed -n '120,180p' backend/api/analysis.py; sed -n '440,515p' backend/api/analysis.py; sed -n '480,545p' backend/data/analysis_scheduler.py" in /home/ps/papers/QuantMind
 succeeded in 0ms:
        log.error("services_not_initialized", error=str(exc))
        _err("Analysis services not initialized", 503)
        return _ok(None)  # unreachable

    timeout = services.pipeline_config.analysis_timeout_seconds
    mongodb = getattr(request.app.state, "mongodb", None)
    try:
        outcome = await asyncio.wait_for(
            run_analysis(body.stock_code, services),
            timeout=timeout,
        )
        if not isinstance(outcome, AnalysisRunResult):  # safety guard
            raise TypeError(
                f"run_analysis must return AnalysisRunResult, got {type(outcome)!r}"
            )
        signal = outcome.signal
        record = outcome.record

        signal_dict = signal.model_dump(mode="json")
        signal_dict["created_at"] = datetime.now(UTC).isoformat()
        if mongodb:
            try:
                signal_id = await mongodb.save_signal(signal_dict)
                record = record.model_copy(update={"signal_id": signal_id})
            except Exception as persist_exc:
                log.warning("signal_persist_failed", error=str(persist_exc))
            try:
                await mongodb.save_analysis_record(
                    record.model_dump(mode="json")
                )
            except AttributeError:
                log.warning("save_analysis_record_unavailable")
            except Exception as persist_exc:
                log.warning(
                    "record_persist_failed", error=str(persist_exc)
                )
        return _ok(signal.model_dump(mode="json"))
    except TimeoutError:
        _err(f"Analysis timed out after {timeout}s", 504)
    except AnalysisRunError as exc:
        # Pipeline-internal failure (single agent crashed or graph
        # raised). Persist the failed record so /history surfaces it
        # alongside successful runs, then return a clean 500.
        log.error(
            "analysis_failed_run_error",
            stock_code=body.stock_code,
            error=str(exc),
        )
        if mongodb is not None:
            try:
                await mongodb.save_analysis_record(
                    exc.record.model_dump(mode="json")
                )
            except AttributeError:
                log.warning("save_analysis_record_unavailable")
            except Exception as persist_exc:
                log.warning(
                    "failed_record_persist_failed",
                    error=str(persist_exc),
                )
        _err(f"Analysis failed: {exc}", 500)
        if event.get("event_type") == "pipeline_completed":
            return
        await hub.push(job_id, event)

    try:
        outcome = await run_analysis(
            stock_code, services, run_id=job_id, emitter=emitter
        )
    except AnalysisRunError as exc:
        log.error("jobs_run_failed", job_id=job_id, error=str(exc))
        record_id: str | None = None
        if mongodb is not None:
            try:
                record_id = await mongodb.save_analysis_record(
                    exc.record.model_dump(mode="json")
                )
            except AttributeError:
                log.warning("jobs_save_failed_record_unavailable")
            except Exception as persist_exc:
                log.warning(
                    "jobs_failed_record_persist_failed",
                    error=str(persist_exc),
                )
        await hub.push(
            job_id,
            {
                "event_type": "error",
                "message": f"Analysis failed: {exc}",
                "run_id": job_id,
                "record_id": record_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        return
    except Exception as exc:
        log.error("jobs_run_failed_unexpected", job_id=job_id, error=str(exc))
        await hub.push(
            job_id,
            {
                "event_type": "error",
                "message": f"Analysis failed: {exc}",
                "run_id": job_id,
                "record_id": None,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        return

    signal = outcome.signal
    record = outcome.record

    signal_id: str | None = None
    record_id: str | None = None

    if mongodb is not None:
        signal_dict = signal.model_dump(mode="json")
        signal_dict["created_at"] = datetime.now(UTC).isoformat()
        try:
            signal_id = await mongodb.save_signal(signal_dict)
            record = record.model_copy(update={"signal_id": signal_id})
        except Exception as persist_exc:
            log.warning("jobs_signal_persist_failed", error=str(persist_exc))
        try:
            record_id = await mongodb.save_analysis_record(
                record.model_dump(mode="json")
            )
        except AttributeError:
            log.warning("jobs_save_analysis_record_unavailable")
        except Exception as persist_exc:
            log.warning("jobs_record_persist_failed", error=str(persist_exc))

    await hub.push(
        job_id,
        {
            "event_type": "pipeline_completed",
            "run_id": job_id,
                    # record the warning so operators can see it.
                    log.warning(
                        "cost_soft_breach_observed",
                        code=stock_code,
                        spent=state.spent_today,
                        soft_ceiling=state.soft_ceiling,
                    )

        services, timeout = self._resolve_services_and_timeout(
            category, policy
        )
        try:
            if timeout is None:
                result = await run_analysis(stock_code, services)
            else:
                result = await asyncio.wait_for(
                    run_analysis(stock_code, services), timeout=timeout
                )
        except TimeoutError:
            await self._persist_timeout_skip(stock_code, category, timeout)
            return None
        except AnalysisRunError as exc:
            try:
                await self._mongodb.save_analysis_record(
                    exc.record.model_dump(mode="json")
                )
            except Exception as persist_exc:
                log.warning(
                    "save_failed_record_failed",
                    code=stock_code,
                    error=str(persist_exc),
                )
            raise

        if not isinstance(result, AnalysisRunResult):  # safety guard
            raise TypeError(
                f"run_analysis must return AnalysisRunResult, got {type(result)!r}"
            )

        signal = result.signal
        record = result.record

        signal_dict = signal.model_dump(mode="json")
        signal_dict["created_at"] = datetime.now(UTC).isoformat()
        signal_id = await self._mongodb.save_signal(signal_dict)

        record_with_signal = record.model_copy(update={"signal_id": signal_id})
        try:
            await self._mongodb.save_analysis_record(
                record_with_signal.model_dump(mode="json")
            )
        except AttributeError:
            # MongoDB service predates A1.4 — skip with warning.
            log.warning(
                "save_analysis_record_unavailable", code=stock_code
            )
        except Exception as exc:
            log.warning(
                "save_analysis_record_failed",
                code=stock_code,
                error=str(exc),
            )

        await self._publish_signal(signal_dict)
        return signal


exec
/bin/bash -lc "grep -R \"motor\\|redis\" -n pyproject.toml requirements*.txt setup.cfg setup.py 2>/dev/null || true" in /home/ps/papers/QuantMind
 succeeded in 0ms:
pyproject.toml:18:    "motor>=3.6,<4",
pyproject.toml:19:    "redis>=5.2,<6",

exec
/bin/bash -lc 'cat config/watchlist_policy.yaml' in /home/ps/papers/QuantMind
 succeeded in 0ms:
# config/watchlist_policy.yaml — Phase 5B-T02 Fast/Slow watchlist split
#
# Two cron jobs share one process: a fast intraday loop (4 ticks/day) for
# T+1 short-term names and a slow once-a-day loop for long-horizon ones.
# A code can only belong to one bucket; ``overrides`` wins, otherwise the
# bucket named in ``default_category`` decides. Empty ``default_codes``
# means the bucket starts empty until the runtime watchlist populates it.
#
# All ``*_pipeline_timeout_seconds`` are HARD upper bounds enforced by
# ``asyncio.wait_for`` inside the scheduler — going over kills the run.
# Keep them aligned with the §2.7 SLA targets:
#   fast: p95 ≤ 8 min  → timeout 480s leaves no head-room past p95
#   slow: p95 ≤ 15 min → timeout 900s
#
# Cron strings use APScheduler 5-field syntax (no seconds).

fast:
  cron: "0 9,11,13,15 * * mon-fri"
  pipeline: fast_pipeline
  max_debate_rounds: 1
  pipeline_timeout_seconds: 480
  default_codes: []

slow:
  cron: "0 9 * * mon-fri"
  pipeline: slow_pipeline
  max_debate_rounds: 2
  pipeline_timeout_seconds: 900
  default_codes: []

# Per-code bucket overrides. Codes not listed fall to ``default_category``.
overrides:
  "300750": slow   # 宁德时代 — 长线深度
  "601318": slow   # 中国平安 — 长线深度

default_category: slow

policy_version: 1
last_updated: 2026-05-02

codex
The exit-check path can fail open on missing cost telemetry and fail closed on all live latency data because persisted timestamps are strings. It also does not consistently apply the requested look-back window to analysis records.

Full review comments:

- [P1] Parse persisted ISO timestamps for latency gates — /home/ps/papers/QuantMind/backend/services/phase5b_exit_check.py:153-154
  Actual `analysis_records` producers save records with `model_dump(mode="json")`, so Mongo contains ISO timestamp strings for `created_at`/`completed_at`. With this `datetime`-only check, every live record is treated as unparseable and the fast/slow latency gates report no-data even when valid completed runs exist.

- [P1] Treat missing cost telemetry as no-data — /home/ps/papers/QuantMind/backend/services/phase5b_exit_check.py:122-124
  When analysis records exist for a date but Redis returns no usage entries for that date (for example scan failure, expired keys, or the wrong Redis URL), this default creates 0-cost per-run rows; the later cost `has_data` checks become true and the fast/slow/daily cost gates can pass on absent telemetry. That contradicts the documented fail-closed behavior for no data.

- [P2] Filter analysis records by the requested window — /home/ps/papers/QuantMind/scripts/phase5b_exit_check.py:104-106
  `--days` is applied to Redis costs and shadow decisions, but this Mongo query only takes the most recent `days * 200` analysis records without a cutoff. In low-volume collections it mixes older latencies into the report, and in high-volume collections it can drop in-window runs, so the exit report no longer represents the requested look-back window.

- [P3] Look up has_data by full gate name — /home/ps/papers/QuantMind/backend/services/phase5b_exit_check.py:306-310
  For gates such as `fast_cost`, `slow_latency`, and `daily_total`, `name.split("_")[0]` looks up keys like `fast` or `daily`, which are not present in `report.has_data`, so no-data cases render as a plain ❌ instead of `⚠️ no-data`. This makes missing telemetry indistinguishable from an actual threshold breach in the markdown report.
