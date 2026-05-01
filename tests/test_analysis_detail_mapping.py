"""Tests for `_detail_from_record` DTO mapping (R1 codex fix W4).

Backend `analysis_records` persist the RunCollector shape (DebateRoundRecord
wraps AgentStepRecord, RiskAssessmentRecord has a `step` field, etc.);
the frontend `AnalysisDetail` contract expects a flatter presentation
shape. This module checks the adaptation is correct.
"""

from __future__ import annotations

from backend.api.analysis import _detail_from_record


def _persisted_step(
    *,
    agent: str,
    round_: int = 1,
    content: str = "",
    model_label: str = "Kimi",
    completed_at: str = "2026-04-25T10:20:00+00:00",
) -> dict:
    return {
        "agent": agent,
        "round": round_,
        "content": content,
        "model_label": model_label,
        "model_id": "kimi-k2.6",
        "tokens_input": 0,
        "tokens_output": 0,
        "cost_cny": 0.0,
        "evidence": [],
        "started_at": "2026-04-25T10:15:00+00:00",
        "completed_at": completed_at,
        "status": "completed",
        "error": None,
    }


class TestDetailFromRecord:
    def test_debates_are_flattened_to_debate_argument(self) -> None:
        doc = {
            "_id": "abc",
            "run_id": "r1",
            "stock_code": "600519",
            "debates": [
                {
                    "round": 1,
                    "bull": _persisted_step(
                        agent="bull_researcher",
                        content="bull case",
                        model_label="Kimi",
                    ),
                    "bear": _persisted_step(
                        agent="bear_researcher",
                        content="bear case",
                        model_label="Kimi",
                    ),
                }
            ],
            "risk_assessment": None,
            "decision": None,
        }
        out = _detail_from_record(doc)

        assert out["id"] == "abc"
        debate_round = out["debates"][0]
        assert debate_round["round"] == 1
        bull = debate_round["bull"]
        assert bull["role"] == "bull"
        assert bull["content"] == "bull case"
        assert bull["model"] == "Kimi"
        assert bull["timestamp"] == "2026-04-25T10:20:00+00:00"
        assert bull["evidence"] == []

        bear = debate_round["bear"]
        assert bear["role"] == "bear"
        assert bear["content"] == "bear case"

    def test_empty_debate_side_becomes_null(self) -> None:
        doc = {
            "run_id": "r1",
            "debates": [{"round": 1, "bull": None, "bear": None}],
            "risk_assessment": None,
            "decision": None,
        }
        out = _detail_from_record(doc)
        assert out["debates"][0]["bull"] is None
        assert out["debates"][0]["bear"] is None

    def test_risk_assessment_mapped_to_frontend_shape(self) -> None:
        step = _persisted_step(agent="risk_officer", content="全面评估")
        doc = {
            "run_id": "r1",
            "debates": [],
            "risk_assessment": {
                "content": "持仓不超过 15%",
                "checks": [{"label": "杠杆", "passed": True}],
                "step": step,
            },
            "decision": None,
        }
        out = _detail_from_record(doc)
        risk = out["risk_assessment"]
        assert risk["model"] == "Kimi"
        assert risk["checks"] == [{"label": "杠杆", "passed": True}]
        assert risk["raw_text"] == "持仓不超过 15%"
        # position_limit defaults to empty string when not in record
        assert risk["position_limit"] == ""

    def test_decision_confidence_drives_score_label(self) -> None:
        step = _persisted_step(agent="fund_manager", content="")
        # Bullish confidence → 偏多
        doc_bull = {
            "run_id": "r1",
            "debates": [],
            "risk_assessment": None,
            "decision": {
                "action": "买入",
                "target_price": 2200.0,
                "confidence": 0.8,
                "risk_score": 0.3,
                "reasoning": "基本面强劲",
                "step": step,
            },
        }
        out_bull = _detail_from_record(doc_bull)
        assert out_bull["decision"]["score"] == 80
        assert out_bull["decision"]["score_label"] == "偏多"
        assert out_bull["decision"]["action"] == "买入"
        assert out_bull["decision"]["confidence"] == 0.8
        # Fields not in pipeline output stay null, not fabricated
        assert out_bull["decision"]["stop_loss"] is None
        assert out_bull["decision"]["position_pct"] is None

        # Neutral confidence → 中性
        doc_neutral = {
            "run_id": "r1",
            "debates": [],
            "risk_assessment": None,
            "decision": {
                "action": "持有",
                "target_price": None,
                "confidence": 0.5,
                "risk_score": 0.5,
                "reasoning": "观望",
                "step": step,
            },
        }
        assert _detail_from_record(doc_neutral)["decision"]["score_label"] == "中性"

        # Low confidence → 偏空
        doc_bear = {
            "run_id": "r1",
            "debates": [],
            "risk_assessment": None,
            "decision": {
                "action": "卖出",
                "target_price": None,
                "confidence": 0.2,
                "risk_score": 0.7,
                "reasoning": "利空",
                "step": step,
            },
        }
        assert _detail_from_record(doc_bear)["decision"]["score_label"] == "偏空"

    def test_handles_missing_debates_and_decision(self) -> None:
        """Partial record (graph failed before debates) should still map."""
        doc = {
            "_id": "abc",
            "run_id": "r1",
            "stock_code": "600519",
            "status": "failed",
            "debates": [],
            "risk_assessment": None,
            "decision": None,
        }
        out = _detail_from_record(doc)
        assert out["status"] == "failed"
        assert out["debates"] == []
        assert out["risk_assessment"] is None
        assert out["decision"] is None
