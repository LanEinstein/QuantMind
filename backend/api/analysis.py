"""FastAPI routes for multi-agent stock analysis history (GET-only).

P1-5 redline: ``/api/analysis/*`` is read-only. The manual-trigger POSTs
(``/stock``, ``/jobs``) and the live-debate SSE subscription
(``/stream/{job_id}``) were destructively deleted in Phase A; analysis
is now driven exclusively by the Fast/Slow scheduler. The SSE pattern
will be re-introduced in Phase B/G as LLM-stream-only per P1-5 §1.5.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request

log = structlog.get_logger(component="api_analysis")

router = APIRouter()


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


@router.get("/api/analysis/signals")
async def list_signals(
    request: Request,
    stock_code: str | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """List historical trading signals for review."""
    mongodb = getattr(request.app.state, "mongodb", None)
    if not mongodb:
        _err("MongoDB not available", 503)
        return _ok(None)
    signals = await mongodb.query_signals(stock_code=stock_code, days=days)
    for s in signals:
        if "_id" in s:
            s["_id"] = str(s["_id"])
    return _ok(signals)


@router.get("/api/analysis/signal-accuracy")
async def signal_accuracy(
    request: Request,
    lookback_days: int = Query(default=30, ge=1, le=365),
    horizon_days: int = Query(default=5, ge=1, le=30),
) -> dict[str, Any]:
    """Evaluate accuracy of past trading signals."""
    from backend.services.signal_evaluator import SignalEvaluator

    mongodb = getattr(request.app.state, "mongodb", None)
    history_data = getattr(request.app.state, "history_data", None)
    if not mongodb or not history_data:
        _err("Required services not available", 503)
        return _ok(None)

    evaluator = SignalEvaluator(mongodb=mongodb, history_data=history_data)
    report = await evaluator.evaluate(
        lookback_days=lookback_days, horizon_days=horizon_days
    )
    return _ok(report)


# -- Full analysis record history & detail --
#
# Route ordering: these MUST stay after the concrete /signals and
# /signal-accuracy routes so the /{record_id} wildcard does not swallow
# them. Do not re-order without updating tests in test_api_analysis.py.


def _summarize_record(doc: dict[str, Any]) -> dict[str, Any]:
    """Shape a stored analysis_records doc for the history list."""
    decision = doc.get("decision") or {}
    return {
        "id": str(doc.get("_id")) if doc.get("_id") is not None else None,
        "run_id": doc.get("run_id"),
        "stock_code": doc.get("stock_code"),
        "stock_name": doc.get("stock_name"),
        "trade_date": doc.get("trade_date"),
        "status": doc.get("status"),
        "action": decision.get("action"),
        "confidence": decision.get("confidence"),
        "risk_score": decision.get("risk_score"),
        "signal_id": doc.get("signal_id"),
        "created_at": doc.get("created_at"),
        "completed_at": doc.get("completed_at"),
    }


def _step_to_argument(
    step: dict[str, Any] | None, role: str
) -> dict[str, Any] | None:
    """Adapt a persisted AgentStepRecord to the frontend DebateArgument."""
    if step is None:
        return None
    return {
        "role": role,
        "round": step.get("round", 0),
        "content": step.get("content", "") or "",
        "evidence": [],
        "model": step.get("model_label") or "Kimi",
        "timestamp": step.get("completed_at") or step.get("started_at") or "",
    }


def _confidence_to_score_label(confidence: float | None) -> str:
    """Map confidence ∈ [0,1] to the frontend's bull/neutral/bear label."""
    if confidence is None:
        return "中性"
    if confidence >= 0.6:
        return "偏多"
    if confidence <= 0.4:
        return "偏空"
    return "中性"


def _detail_from_record(doc: dict[str, Any]) -> dict[str, Any]:
    """Shape a stored analysis_records doc for the detail endpoint."""
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    if out.get("signal_id") is not None:
        out["signal_id"] = str(out["signal_id"])

    debates = out.get("debates") or []
    transformed_debates: list[dict[str, Any]] = []
    for d in debates:
        if not isinstance(d, dict):
            continue
        transformed_debates.append(
            {
                "round": d.get("round", 0),
                "bull": _step_to_argument(d.get("bull"), "bull"),
                "bear": _step_to_argument(d.get("bear"), "bear"),
            }
        )
    out["debates"] = transformed_debates

    risk = out.get("risk_assessment")
    if isinstance(risk, dict):
        step = risk.get("step") or {}
        out["risk_assessment"] = {
            "model": step.get("model_label") or "Kimi",
            "checks": risk.get("checks") or [],
            "position_limit": risk.get("position_limit", "") or "",
            "raw_text": risk.get("content", "") or "",
        }

    decision = out.get("decision")
    if isinstance(decision, dict):
        step = decision.get("step") or {}
        confidence = decision.get("confidence")
        confidence_value = float(confidence) if confidence is not None else 0.5
        score = round(confidence_value * 100)
        out["decision"] = {
            "model": step.get("model_label") or "Kimi",
            "score": score,
            "score_label": _confidence_to_score_label(confidence_value),
            "action": decision.get("action"),
            "target_price": decision.get("target_price"),
            "stop_loss": decision.get("stop_loss"),
            "position_pct": decision.get("position_pct"),
            "reasoning": decision.get("reasoning", "") or "",
            "confidence": confidence_value,
            "risk_score": float(decision.get("risk_score") or 0.5),
        }

    return out


@router.get("/api/analysis/history")
async def list_analysis_history(
    request: Request,
    stock_code: str | None = None,
    trade_date: str | None = None,
    limit: int = Query(default=20, ge=1, le=500),
) -> dict[str, Any]:
    """List AnalysisDebate history entries sourced from analysis_records."""
    mongodb = getattr(request.app.state, "mongodb", None)
    if not mongodb:
        _err("MongoDB not available", 503)
        return _ok(None)

    docs = await mongodb.query_analysis_records(
        stock_code=stock_code,
        trade_date=trade_date,
        limit=limit,
    )
    return _ok([_summarize_record(d) for d in docs])


@router.get("/api/analysis/{record_id}")
async def get_analysis_record(
    request: Request, record_id: str
) -> dict[str, Any]:
    """Fetch a complete analysis record by ObjectId or run_id."""
    mongodb = getattr(request.app.state, "mongodb", None)
    if not mongodb:
        _err("MongoDB not available", 503)
        return _ok(None)

    doc = await mongodb.get_analysis_record_by_id(record_id)
    if doc is None:
        _err(f"Analysis record '{record_id}' not found", 404)
        return _ok(None)

    return _ok(_detail_from_record(doc))
