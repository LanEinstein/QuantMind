"""FastAPI routes for multi-agent stock analysis."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.agents.graph import AnalysisRunError, run_analysis
from backend.agents.models import AnalysisServices, PipelineConfig
from backend.agents.records import AnalysisRunResult
from backend.services.analysis_stream import AnalysisStreamHub


def _llm_preflight_or_503(request: Request) -> None:
    """Reject analysis requests when every LLM provider is unavailable.

    Inspects the router's api-key presence snapshot. When at least one
    provider is available, we continue and rely on router fallback for
    partial outages. When *all* providers are missing, we return 503 and
    fire an ``llm_all_providers_failed`` alert.

    Tolerates routers that don't expose preflight() (e.g. unit-test
    AsyncMock stubs): returns silently in that case and lets the
    pipeline surface any downstream failure.
    """
    router_obj = getattr(request.app.state, "llm_router", None)
    if router_obj is None:
        _err("LLM router not initialized", 503)
    preflight_fn = getattr(router_obj, "preflight", None)
    if not callable(preflight_fn):
        return
    try:
        snapshot = preflight_fn()
    except Exception as exc:
        log.warning("llm_preflight_probe_failed", error=str(exc))
        return

    if not isinstance(snapshot, dict) or not snapshot:
        return
    if any(snapshot.values()):
        return

    alerter = getattr(request.app.state, "alerter", None)
    if alerter is not None:
        import asyncio

        try:
            asyncio.create_task(
                alerter.fire(
                    "llm_all_providers_failed",
                    "All LLM providers are unavailable",
                    severity="critical",
                    context={"providers": snapshot},
                )
            )
        except Exception:  # pragma: no cover
            pass
    _err("All LLM providers are unavailable", 503)

log = structlog.get_logger(component="api_analysis")

router = APIRouter()

_CODE_RE = re.compile(r"^\d{6}$")


class AnalysisRequest(BaseModel):
    """Request body for stock analysis endpoint."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    max_debate_rounds: int = Field(default=2, ge=1, le=5)


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


@router.post("/api/analysis/stock")
async def analyze_stock(request: Request, body: AnalysisRequest) -> dict[str, Any]:
    """Run the full multi-agent analysis pipeline for a stock.

    Triggers 9 LLM agents: 5 analysts, 2 debaters, risk officer, fund manager.
    Returns a TradingSignal with action/target_price/confidence/risk_score.
    """
    if not _CODE_RE.match(body.stock_code):
        _err(f"Invalid stock code '{body.stock_code}': must be 6 digits", 422)

    _llm_preflight_or_503(request)

    try:
        services = AnalysisServices(
            llm_router=request.app.state.llm_router,
            market_data=request.app.state.market_data,
            history_data=request.app.state.history_data,
            news_crawler=request.app.state.news_crawler,
            mongodb=getattr(request.app.state, "mongodb", None),
            pipeline_config=PipelineConfig(
                max_debate_rounds=body.max_debate_rounds
            ),
        )
    except AttributeError as exc:
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
    except Exception as exc:
        log.error("analysis_failed", error=str(exc))
        _err(f"Analysis failed: {exc}", 500)
    return _ok(None)  # unreachable


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
        return _ok(None)  # unreachable
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
        return _ok(None)  # unreachable

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
        "evidence": [],  # not yet populated; placeholder per A1.5 plan §5.4
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
    """Shape a stored analysis_records doc for the detail endpoint.

    Backend persistence (DebateRoundRecord with bull/bear=AgentStepRecord,
    RiskAssessmentRecord, FundManagerRecord) is intentionally close to
    the run instrumentation; the frontend's ``AnalysisDetail`` declares a
    flatter, presentation-oriented shape (DebateArgument with
    role/model/timestamp, RiskAssessment with model/raw_text/position_limit,
    FundManagerDecision with score/score_label). This function performs
    that mapping so the API contract matches the typed frontend.
    """
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    if out.get("signal_id") is not None:
        out["signal_id"] = str(out["signal_id"])

    # debates[].bull / .bear: AgentStepRecord → DebateArgument
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

    # risk_assessment: RiskAssessmentRecord → RiskAssessment
    risk = out.get("risk_assessment")
    if isinstance(risk, dict):
        step = risk.get("step") or {}
        out["risk_assessment"] = {
            "model": step.get("model_label") or "Kimi",
            "checks": risk.get("checks") or [],
            "position_limit": risk.get("position_limit", "") or "",
            "raw_text": risk.get("content", "") or "",
        }

    # decision: FundManagerRecord → FundManagerDecision
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
            # stop_loss / position_pct are not produced by the current
            # pipeline; surface as null instead of fabricating values.
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
        return _ok(None)  # unreachable

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
    """Fetch a complete analysis record by ObjectId or run_id.

    Invalid / missing ids return 404 as structured envelope error — never
    a 500 from an ObjectId parse exception.
    """
    mongodb = getattr(request.app.state, "mongodb", None)
    if not mongodb:
        _err("MongoDB not available", 503)
        return _ok(None)  # unreachable

    doc = await mongodb.get_analysis_record_by_id(record_id)
    if doc is None:
        _err(f"Analysis record '{record_id}' not found", 404)
        return _ok(None)  # unreachable

    return _ok(_detail_from_record(doc))


# -- Live analysis jobs & SSE streaming --
#
# POST /api/analysis/jobs      — creates a background run, returns job_id
# GET  /api/analysis/stream/{id} — text/event-stream subscription
#
# The two-step design is required because browsers' native EventSource can
# only send GET, so the POST-body parameters (stock_code, debate rounds)
# must be bound to a pre-allocated job before the stream opens.


class AnalysisJobRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    stock_code: str
    max_debate_rounds: int = Field(default=2, ge=1, le=5)


def _get_hub(request: Request) -> AnalysisStreamHub:
    hub = getattr(request.app.state, "analysis_stream_hub", None)
    if hub is None:
        _err("Analysis stream hub not initialized", 503)
    return hub  # type: ignore[return-value]


def _build_services(request: Request, rounds: int) -> AnalysisServices:
    return AnalysisServices(
        llm_router=request.app.state.llm_router,
        market_data=request.app.state.market_data,
        history_data=request.app.state.history_data,
        news_crawler=request.app.state.news_crawler,
        mongodb=getattr(request.app.state, "mongodb", None),
        pipeline_config=PipelineConfig(max_debate_rounds=rounds),
    )


async def _run_job(
    *,
    job_id: str,
    stock_code: str,
    services: AnalysisServices,
    hub: AnalysisStreamHub,
    mongodb: Any,
) -> None:
    """Run the pipeline for a job, pushing events to the hub."""

    async def emitter(event: dict[str, Any]) -> None:
        event.setdefault("run_id", job_id)
        # The pipeline_completed event is re-emitted below with signal_id
        # patched in, so skip the one from the collector.
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
            "record_id": record_id,
            "signal_id": signal_id,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )


@router.post("/api/analysis/jobs")
async def create_analysis_job(
    request: Request, body: AnalysisJobRequest
) -> dict[str, Any]:
    """Launch a live agent-debate run that streams progress via SSE."""
    if not _CODE_RE.match(body.stock_code):
        _err(f"Invalid stock code '{body.stock_code}': must be 6 digits", 422)

    _llm_preflight_or_503(request)

    hub = _get_hub(request)

    # Admission control: each job kicks off ~9 LLM calls and runs for
    # 30-90s. Capping concurrent live jobs at the hub default keeps a
    # bot or buggy client from burning the daily LLM budget in seconds.
    if hub.active_job_count() >= hub.max_active_jobs:
        _err(
            "Too many active analysis jobs — try again shortly",
            429,
        )

    try:
        services = _build_services(request, body.max_debate_rounds)
    except AttributeError as exc:
        log.error("jobs_services_not_initialized", error=str(exc))
        _err("Analysis services not initialized", 503)
        return _ok(None)  # unreachable

    job = hub.create_job(
        stock_code=body.stock_code,
        max_debate_rounds=body.max_debate_rounds,
    )

    task = asyncio.create_task(
        _run_job(
            job_id=job.job_id,
            stock_code=body.stock_code,
            services=services,
            hub=hub,
            mongodb=getattr(request.app.state, "mongodb", None),
        )
    )
    hub.attach_task(job.job_id, task)

    return _ok({"job_id": job.job_id, "status": job.status})


@router.get("/api/analysis/stream/{job_id}")
async def stream_analysis_job(
    request: Request, job_id: str
) -> StreamingResponse:
    """Subscribe to an existing job's SSE event stream.

    The hub returns ``(job, queue, snapshot)`` atomically — the snapshot
    contains every event already buffered at subscribe time, while the
    queue receives only events pushed after subscription. This avoids
    both duplication (no event lives in both snapshot and queue) and
    the late-terminal-event miss that could leave a subscriber idling
    forever.
    """
    hub = _get_hub(request)
    # Per-job subscriber cap. The frontend opens at most one EventSource;
    # any pile-up beyond a small constant is almost certainly a buggy
    # or hostile client trying to fan out the same expensive run.
    if hub.subscriber_count(job_id) >= hub.max_subscribers_per_job:
        _err(
            f"Too many subscribers for job '{job_id}'",
            429,
        )
    subscription = hub.subscribe(job_id)
    if subscription is None:
        _err(f"Analysis job '{job_id}' not found", 404)

    job, queue, snapshot = subscription  # type: ignore[misc]

    terminal_events = {"pipeline_completed", "error"}

    async def event_stream() -> AsyncIterator[bytes]:
        # Replay snapshot (everything buffered at subscribe time).
        for ev in snapshot:
            yield _sse_chunk(ev)

        # Job already terminated — no queue, snapshot is complete.
        if queue is None:
            return

        # Snapshot ended on a terminal event but queue still exists
        # (rare race: terminal event arrived between subscribe atomic
        # registration and the .terminated flag being checked). Drain
        # the queue lazily anyway in case of further pushes; bail out
        # immediately if we already saw the terminal in snapshot.
        if snapshot and snapshot[-1].get("event_type") in terminal_events:
            try:
                hub.unsubscribe(job_id, queue)
            finally:
                return

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    # Heartbeat keeps proxies from closing the idle stream.
                    yield b": keep-alive\n\n"
                    continue
                if item is None:
                    break
                yield _sse_chunk(item)
                # Second line of defense: if the terminal event reached
                # the queue but its None sentinel did not (e.g. the
                # subscriber was saturated at finalize time), break on
                # the event_type itself to avoid heartbeating forever.
                if item.get("event_type") in terminal_events:
                    break
        finally:
            hub.unsubscribe(job_id, queue)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )


def _sse_chunk(event: dict[str, Any]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False)
    return f"data: {payload}\n\n".encode()
