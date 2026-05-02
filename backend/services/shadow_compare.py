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
import re
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

ACTION_MATCH_THRESHOLD = 0.85
CONFIDENCE_DELTA_THRESHOLD = 0.15

# Strict ``YYYY-MM-DD`` shape — anything else (pipes, newlines, control
# chars from a malicious shadow doc) gets the row dropped before it
# reaches the markdown renderer (codex P5B-exit R5 LOW: report
# injection). We don't validate the calendar (Feb 30 etc.); the gate
# math doesn't care, and a bogus-but-shaped date just clusters by its
# own bucket.
_TRADE_DATE_RE: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Mirrors backend.services.shadow_recorder._VALID_ACTIONS — duplicated
# rather than imported because the analyser must cope with documents
# the recorder may not have produced (offline JSONL replay, future
# schema migrations).
_VALID_LEG_ACTIONS: frozenset[str] = frozenset({"买入", "持有", "卖出"})


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
    """Return a leg dict only if every required field is present and typed.

    Mongo deserialises documents loosely (an int may arrive where a float
    was written), so we coerce numeric fields. Action and the two boolean
    flags are validated strictly: a free-form ``str(action)`` would let a
    rogue / legacy doc with action ``"buy"`` be counted as valid, and a
    truthy-string ``"false"`` for ``parse_ok`` would silently flip to
    ``True`` (codex P5B-exit R4 MED). Anything off-contract drops the
    leg, which propagates to the ``skipped`` counter upstream.
    """
    if not isinstance(raw, Mapping):
        return None
    required = ("action", "confidence", "model", "parse_ok", "escalated")
    if not all(k in raw for k in required):
        return None

    action = raw["action"]
    if not isinstance(action, str) or action not in _VALID_LEG_ACTIONS:
        return None

    parse_ok = raw["parse_ok"]
    escalated = raw["escalated"]
    if not isinstance(parse_ok, bool) or not isinstance(escalated, bool):
        return None

    confidence = raw["confidence"]
    # Pre-empt the ``bool`` ⊂ ``int`` Python quirk: True/False would
    # otherwise pass the float coercion check unchanged.
    if isinstance(confidence, bool) or not isinstance(
        confidence, (int, float)
    ):
        return None

    model = raw["model"]
    if not isinstance(model, str) or not model:
        return None

    latency_raw = raw.get("latency_ms", 0.0)
    if isinstance(latency_raw, bool) or not isinstance(
        latency_raw, (int, float)
    ):
        return None

    try:
        return {
            "action": action,
            "confidence": float(confidence),
            "model": model,
            "latency_ms": float(latency_raw),
            "parse_ok": parse_ok,
            "escalated": escalated,
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
        if not isinstance(trade_date, str) or not _TRADE_DATE_RE.match(
            trade_date
        ):
            # Non-conformant trade_date is dropped to avoid markdown
            # injection in the per-day breakdown (codex P5B-exit R5 LOW).
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
