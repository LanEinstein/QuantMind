"""Unit tests for backend.services.shadow_compare.

Covers the math: action match rate, |Δconfidence| stats, malformed-input
robustness, gate population, percentile correctness, and markdown
rendering.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.services.shadow_compare import (
    ACTION_MATCH_THRESHOLD,
    CONFIDENCE_DELTA_THRESHOLD,
    compute_shadow_report,
    render_markdown,
)


def _doc(
    *,
    trade_date: str = "2026-05-02",
    base_action: str = "买入",
    routed_action: str = "买入",
    base_conf: float = 0.7,
    routed_conf: float = 0.7,
    base_latency: float = 1000.0,
    routed_latency: float = 1500.0,
    base_parse_ok: bool = True,
    routed_parse_ok: bool = True,
    base_escalated: bool = False,
    routed_escalated: bool = False,
) -> dict[str, Any]:
    return {
        "run_id": "r1",
        "stock_code": "600519",
        "trade_date": trade_date,
        "baseline": {
            "action": base_action,
            "confidence": base_conf,
            "model": "kimi-k2.6",
            "latency_ms": base_latency,
            "escalated": base_escalated,
            "parse_ok": base_parse_ok,
        },
        "routed": {
            "action": routed_action,
            "confidence": routed_conf,
            "model": "qwen3.6-plus",
            "latency_ms": routed_latency,
            "escalated": routed_escalated,
            "parse_ok": routed_parse_ok,
        },
    }


# ----------------------------------------------------------------------
# Group 1: empty / malformed input
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyInput:
    def test_empty_returns_zero_total(self) -> None:
        report = compute_shadow_report([])
        assert report.total_pairs == 0
        assert report.passes == {
            "has_data": False,
            "action_match": False,
            "confidence_delta": False,
        }

    def test_skips_non_mapping(self) -> None:
        report = compute_shadow_report(["not a dict", 123, None])  # type: ignore[list-item]
        assert report.total_pairs == 0
        assert report.skipped == 3

    def test_skips_missing_leg(self) -> None:
        doc = _doc()
        del doc["routed"]
        report = compute_shadow_report([doc])
        assert report.total_pairs == 0
        assert report.skipped == 1

    def test_skips_missing_trade_date(self) -> None:
        doc = _doc()
        del doc["trade_date"]
        report = compute_shadow_report([doc])
        assert report.skipped == 1

    def test_skips_out_of_range_confidence(self) -> None:
        report = compute_shadow_report([_doc(routed_conf=1.5)])
        assert report.skipped == 1

    def test_skips_nan_confidence(self) -> None:
        report = compute_shadow_report([_doc(base_conf=float("nan"))])
        assert report.skipped == 1

    def test_skips_invalid_action_in_leg(self) -> None:
        # codex P5B-exit R4 MED: a leg with action "buy" used to be
        # coerced via ``str(...)`` and counted as a valid sample.
        doc = _doc()
        doc["routed"]["action"] = "buy"
        report = compute_shadow_report([doc])
        assert report.total_pairs == 0
        assert report.skipped == 1

    @pytest.mark.parametrize("bad_value", ["true", 1, 0, "false", None])
    def test_skips_non_bool_parse_ok(self, bad_value) -> None:  # noqa: ANN001
        doc = _doc()
        doc["baseline"]["parse_ok"] = bad_value
        report = compute_shadow_report([doc])
        assert report.skipped == 1

    def test_skips_both_legs_malformed(self) -> None:
        doc = _doc()
        doc["baseline"]["action"] = "invalid"
        doc["routed"]["confidence"] = "high"  # type: ignore[arg-type]
        report = compute_shadow_report([doc])
        assert report.total_pairs == 0
        assert report.skipped == 1

    def test_skips_non_finite_latency(self) -> None:
        doc = _doc()
        doc["routed"]["latency_ms"] = float("inf")
        report = compute_shadow_report([doc])
        assert report.skipped == 1

    def test_skips_negative_latency(self) -> None:
        doc = _doc(routed_latency=-100.0)
        report = compute_shadow_report([doc])
        assert report.skipped == 1

    def test_skips_empty_model_string(self) -> None:
        doc = _doc()
        doc["baseline"]["model"] = ""
        report = compute_shadow_report([doc])
        assert report.skipped == 1

    def test_parse_failed_pairs_excluded_from_gate(self) -> None:
        # codex P5B-shadow R1 P2: synthetic 持有/0.5 fallbacks that
        # carry parse_ok=False must NOT skew action match or |Δconf|.
        # 9 clean buys + 1 parse-failed → match rate over the 9
        # gateable pairs is 1.0 (passes), not 0.9.
        clean_docs = [_doc() for _ in range(9)]
        bad = _doc()
        bad["baseline"]["parse_ok"] = False
        bad["baseline"]["action"] = "持有"  # synthetic fallback
        bad["baseline"]["confidence"] = 0.5
        report = compute_shadow_report([*clean_docs, bad])
        assert report.total_pairs == 10
        assert report.parse_failed_pairs == 1
        assert report.action_match_rate == 1.0
        assert report.passes["action_match"] is True

    def test_all_parse_failed_yields_no_data(self) -> None:
        # When every recorded pair was synthetic, the gate has no
        # honest answer — fail closed.
        docs = []
        for _ in range(3):
            d = _doc()
            d["routed"]["parse_ok"] = False
            docs.append(d)
        report = compute_shadow_report(docs)
        assert report.total_pairs == 3
        assert report.parse_failed_pairs == 3
        assert report.passes["has_data"] is False
        assert report.passes["action_match"] is False

    @pytest.mark.parametrize(
        "trade_date",
        [
            "2026|05|02",
            "2026-05-02 | malicious row",
            "2026-05-02\n| ❌ |",
            "yesterday",
            "",
        ],
    )
    def test_skips_malformed_trade_date(self, trade_date: str) -> None:
        # codex P5B-exit R5 LOW: trade_date is rendered into a markdown
        # table; any non-YYYY-MM-DD value could inject pipes / newlines
        # and spoof report rows.
        doc = _doc(trade_date=trade_date)
        report = compute_shadow_report([doc])
        assert report.total_pairs == 0
        assert report.skipped == 1


# ----------------------------------------------------------------------
# Group 2: action match
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestActionMatch:
    def test_perfect_match_passes_gate(self) -> None:
        docs = [_doc() for _ in range(10)]
        report = compute_shadow_report(docs)
        assert report.action_match_rate == 1.0
        assert report.passes["action_match"] is True

    def test_partial_match_below_threshold(self) -> None:
        docs = [_doc(routed_action="持有") for _ in range(8)]
        docs += [_doc() for _ in range(2)]
        report = compute_shadow_report(docs)
        assert report.action_match_rate == 0.2
        assert report.passes["action_match"] is False

    def test_threshold_boundary(self) -> None:
        # 17 / 20 = 0.85 — exactly at threshold; ``>=`` passes.
        docs = [_doc() for _ in range(17)]
        docs += [_doc(routed_action="卖出") for _ in range(3)]
        report = compute_shadow_report(docs)
        assert report.action_match_rate == ACTION_MATCH_THRESHOLD
        assert report.passes["action_match"] is True

    def test_per_day_breakdown(self) -> None:
        report = compute_shadow_report(
            [
                _doc(trade_date="2026-05-02"),
                _doc(trade_date="2026-05-02", routed_action="持有"),
                _doc(trade_date="2026-05-03"),
            ]
        )
        assert report.by_day["2026-05-02"]["match_rate"] == 0.5
        assert report.by_day["2026-05-03"]["match_rate"] == 1.0


# ----------------------------------------------------------------------
# Group 3: confidence delta
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestConfidenceDelta:
    def test_zero_delta_passes(self) -> None:
        report = compute_shadow_report([_doc()])
        assert report.confidence_delta_mean_abs == 0.0
        assert report.passes["confidence_delta"] is True

    def test_large_delta_fails(self) -> None:
        report = compute_shadow_report(
            [_doc(base_conf=0.9, routed_conf=0.5) for _ in range(5)]
        )
        assert report.confidence_delta_mean_abs == 0.4
        assert report.passes["confidence_delta"] is False

    def test_just_under_threshold_passes(self) -> None:
        # mean |Δ| 0.149 < 0.15. routed - baseline = 0.149.
        report = compute_shadow_report(
            [_doc(base_conf=0.5, routed_conf=0.649) for _ in range(3)]
        )
        assert report.passes["confidence_delta"] is True

    def test_at_threshold_fails(self) -> None:
        # mean |Δ| == threshold should fail because gate is strict <.
        report = compute_shadow_report(
            [
                _doc(
                    base_conf=0.5,
                    routed_conf=0.5 + CONFIDENCE_DELTA_THRESHOLD,
                )
            ]
        )
        assert report.passes["confidence_delta"] is False

    def test_p50_p95(self) -> None:
        deltas = [0.0, 0.1, 0.2, 0.3, 0.4]
        docs = [
            _doc(base_conf=0.5, routed_conf=0.5 + d)
            for d in deltas
        ]
        report = compute_shadow_report(docs)
        # median = 0.2 ; p95 ≈ 0.38.
        assert report.confidence_delta_p50 == 0.2
        assert 0.37 < report.confidence_delta_p95 <= 0.4


# ----------------------------------------------------------------------
# Group 4: leg metrics
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestLegMetrics:
    def test_baseline_escalation_rate_zero(self) -> None:
        report = compute_shadow_report(
            [_doc(routed_escalated=True) for _ in range(2)]
        )
        assert report.baseline.escalation_rate == 0.0
        assert report.routed.escalation_rate == 1.0

    def test_parse_ok_aggregation(self) -> None:
        docs = [_doc(routed_parse_ok=False) for _ in range(2)]
        docs += [_doc() for _ in range(8)]
        report = compute_shadow_report(docs)
        assert report.routed.parse_ok_rate == 0.8
        assert report.baseline.parse_ok_rate == 1.0

    def test_avg_latency(self) -> None:
        docs = [
            _doc(base_latency=1000.0, routed_latency=2000.0),
            _doc(base_latency=2000.0, routed_latency=4000.0),
        ]
        report = compute_shadow_report(docs)
        assert report.baseline.avg_latency_ms == 1500.0
        assert report.routed.avg_latency_ms == 3000.0


# ----------------------------------------------------------------------
# Group 5: markdown rendering
# ----------------------------------------------------------------------


@pytest.mark.unit
class TestRenderMarkdown:
    def test_empty_report_renders_no_data_marker(self) -> None:
        out = render_markdown(compute_shadow_report([]))
        assert "Total pairs: **0**" in out
        assert "❌ has_data" in out
        assert "| — | — | 0 |" in out

    def test_happy_path_renders_pass_markers(self) -> None:
        out = render_markdown(compute_shadow_report([_doc()]))
        assert "✅ action_match" in out
        assert "✅ confidence_delta" in out
        assert "Δconfidence p50:" in out
