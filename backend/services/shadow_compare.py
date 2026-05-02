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

    ``parse_failed_pairs`` counts pairs where one or both legs were
    written with ``parse_ok=False`` (the recorder still persists them
    so the parse-failure rate is observable). Those pairs do NOT
    contribute to ``action_match_rate`` or the confidence-delta
    statistics — a synthetic ``持有 / 0.5`` fallback would otherwise
    pollute the gate math (codex P5B-shadow R1 P2).
    """

    total_pairs: int
    skipped: int
    action_match_rate: float
    confidence_delta_p50: float
    confidence_delta_p95: float
    confidence_delta_mean_abs: float
    baseline: LegMetrics
    routed: LegMetrics
    parse_failed_pairs: int = 0
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
            parse_failed_pairs=0,
            by_day={},
            passes={
                "action_match": False,
                "confidence_delta": False,
                "has_data": False,
            },
        )

    # Single-pass accumulator (codex P5B-shadow R3 P3): track leg
    # metrics, by-day match counters, and gateable deltas in one
    # iteration over ``pairs`` instead of materialising 6 separate
    # comprehensions. Pairs whose either leg has parse_ok=False are
    # counted in parse_failed_pairs and excluded from gate math —
    # they're synthetic 持有/0.5 placeholders, not real decisions.
    parse_failed_pairs = 0
    matched = 0
    abs_delta_sum = 0.0
    deltas: list[float] = []

    base_parse_ok_count = 0
    base_escalated_count = 0
    base_latency_sum = 0.0
    routed_parse_ok_count = 0
    routed_escalated_count = 0
    routed_latency_sum = 0.0

    by_day_gate_counts: dict[str, dict[str, int]] = {}

    for pair in pairs:
        b = pair["baseline"]
        r = pair["routed"]
        # Leg-metric counters cover ALL recorded legs so per-leg
        # parse_ok_rate / escalation_rate / latency reflect the full
        # window — gate math runs on the parse_ok subset only.
        if b["parse_ok"]:
            base_parse_ok_count += 1
        if b["escalated"]:
            base_escalated_count += 1
        base_latency_sum += b["latency_ms"]
        if r["parse_ok"]:
            routed_parse_ok_count += 1
        if r["escalated"]:
            routed_escalated_count += 1
        routed_latency_sum += r["latency_ms"]

        if not (b["parse_ok"] and r["parse_ok"]):
            parse_failed_pairs += 1
            continue

        # Gateable pair contributes to action match + Δconfidence and
        # to the per-day breakdown.
        slot = by_day_gate_counts.setdefault(
            pair["trade_date"], {"matched": 0, "total": 0}
        )
        slot["total"] += 1
        if b["action"] == r["action"]:
            slot["matched"] += 1
            matched += 1
        delta = r["confidence"] - b["confidence"]
        deltas.append(delta)
        abs_delta_sum += abs(delta)

    n = len(pairs)
    baseline_metrics = LegMetrics(
        parse_ok_rate=round(base_parse_ok_count / n, 4) if n else 0.0,
        escalation_rate=round(base_escalated_count / n, 4) if n else 0.0,
        avg_latency_ms=round(base_latency_sum / n, 2) if n else 0.0,
    )
    routed_metrics = LegMetrics(
        parse_ok_rate=round(routed_parse_ok_count / n, 4) if n else 0.0,
        escalation_rate=round(routed_escalated_count / n, 4) if n else 0.0,
        avg_latency_ms=round(routed_latency_sum / n, 2) if n else 0.0,
    )

    by_day = {
        day: {
            "match_rate": round(slot["matched"] / slot["total"], 4),
            "samples": slot["total"],
        }
        for day, slot in sorted(by_day_gate_counts.items())
    }

    if not deltas:
        # All recorded pairs were parse_failed (or pairs was empty).
        # No honest gate answer — no_data, fail-closed.
        return ShadowReport(
            total_pairs=n,
            skipped=skipped,
            action_match_rate=0.0,
            confidence_delta_p50=0.0,
            confidence_delta_p95=0.0,
            confidence_delta_mean_abs=0.0,
            baseline=baseline_metrics,
            routed=routed_metrics,
            parse_failed_pairs=parse_failed_pairs,
            by_day=by_day,
            passes={
                "has_data": False,
                "action_match": False,
                "confidence_delta": False,
            },
        )

    total = len(deltas)
    # Sort once and derive both percentiles from the sorted list —
    # statistics.median + a fresh sorted() inside _percentile would
    # otherwise duplicate the work.
    deltas.sort()
    action_match_rate = matched / total
    confidence_delta_mean_abs = abs_delta_sum / total
    p50 = _percentile_sorted(deltas, 50)
    p95 = _percentile_sorted(deltas, 95)

    passes = {
        "has_data": True,
        "action_match": action_match_rate >= ACTION_MATCH_THRESHOLD,
        "confidence_delta": (
            confidence_delta_mean_abs < CONFIDENCE_DELTA_THRESHOLD
        ),
    }

    return ShadowReport(
        total_pairs=len(pairs),
        # ``total_pairs`` reflects every recorded pair (parse-failed
        # included for transparency); gate math runs on ``gateable``
        # which excludes synthetic legs.
        skipped=skipped,
        action_match_rate=round(action_match_rate, 4),
        confidence_delta_p50=round(p50, 4),
        confidence_delta_p95=round(p95, 4),
        confidence_delta_mean_abs=round(confidence_delta_mean_abs, 4),
        baseline=baseline_metrics,
        routed=routed_metrics,
        parse_failed_pairs=parse_failed_pairs,
        by_day=by_day,
        passes=passes,
    )


def _percentile_sorted(values: list[float], q: float) -> float:
    """Compute q-th percentile from an ALREADY-SORTED list (linear interp).

    Caller is responsible for sorting once and passing the same list
    in for both p50 and p95 (codex P5B-shadow R3 P3). ``q`` is clamped
    to ``[0, 100]`` so a typo cannot produce a meaningless out-of-
    range index.
    """
    if not values:
        return 0.0
    q = max(0.0, min(100.0, q))
    if len(values) == 1:
        return values[0]
    pos = q / 100 * (len(values) - 1)
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return values[lower]
    weight = pos - lower
    return values[lower] * (1 - weight) + values[upper] * weight


def render_markdown(report: ShadowReport) -> str:
    """Render the report as a markdown table for the summary doc / CI logs."""
    lines = [
        "# Shadow Comparison Report",
        "",
        f"- Total pairs: **{report.total_pairs}**",
        f"- Skipped (malformed): **{report.skipped}**",
        f"- Parse-failed pairs (excluded from gate math): "
        f"**{report.parse_failed_pairs}**",
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
