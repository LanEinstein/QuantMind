"""FastAPI routes for MiroFish simulation result browsing."""

from __future__ import annotations

import re
from typing import Any

import structlog
from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query, Request

log = structlog.get_logger(component="api_simulation")

router = APIRouter()

_OID_RE = re.compile(r"^[0-9a-fA-F]{24}$")


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data, "error": None}


def _err(message: str, status_code: int = 500) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"status": "error", "data": None, "error": message},
    )


def _validate_object_id(value: str, name: str = "id") -> ObjectId:
    """Validate and convert a hex string to ObjectId."""
    if not _OID_RE.match(value):
        _err(f"Invalid {name}: must be 24-character hex string", 422)
    return ObjectId(value)


def _doc_to_result(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a MongoDB document to an API-friendly result dict."""
    return {
        **{k: v for k, v in doc.items() if k != "_id"},
        "id": str(doc["_id"]),
    }


def _doc_to_history_item(doc: dict[str, Any]) -> dict[str, Any]:
    """Flatten a simulation document into a lightweight history item."""
    event = doc.get("event", {})
    config = doc.get("simulation_config", {})
    return {
        "id": str(doc["_id"]),
        "event_title": event.get("title", doc.get("event_summary", "")),
        "importance_score": event.get("importance_score", 0),
        "agent_count": config.get("agent_count", 0),
        "rounds": config.get("rounds", 0),
        "recommended_action": doc.get("recommended_action", ""),
        "cost_rmb": doc.get("cost_rmb", 0.0),
        "duration_seconds": doc.get("duration_seconds", 0.0),
        "created_at": doc.get("created_at", ""),
    }


# CRITICAL: Static routes must come BEFORE the parameterized /{sim_id} route.


@router.get("/api/simulation/latest")
async def get_latest_simulation(request: Request) -> dict[str, Any]:
    """Return the most recent simulation result."""
    try:
        coll = request.app.state.mongodb._db["simulations"]
    except AttributeError:
        _err("Database not initialized", 503)
        return _ok(None)  # unreachable

    doc = await coll.find_one(sort=[("created_at", -1)])
    if doc is None:
        _err("No simulations found", 404)
    return _ok(_doc_to_result(doc))


@router.get("/api/simulation/history")
async def get_simulation_history(
    request: Request,
    search: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """List past simulations with optional search filter."""
    try:
        coll = request.app.state.mongodb._db["simulations"]
    except AttributeError:
        _err("Database not initialized", 503)
        return _ok(None)  # unreachable

    query: dict[str, Any] = {}
    if search:
        escaped = re.escape(search)
        query["event.title"] = {"$regex": escaped, "$options": "i"}

    projection = {
        "_id": 1,
        "event": 1,
        "event_summary": 1,
        "simulation_config": 1,
        "recommended_action": 1,
        "cost_rmb": 1,
        "duration_seconds": 1,
        "created_at": 1,
    }

    cursor = (
        coll.find(query, projection)
        .sort("created_at", -1)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    return _ok([_doc_to_history_item(d) for d in docs])


@router.get("/api/simulation/compare")
async def compare_simulations(
    request: Request,
    a: str = Query(..., description="First simulation ID"),
    b: str = Query(..., description="Second simulation ID"),
) -> dict[str, Any]:
    """Return two simulation results for side-by-side comparison."""
    oid_a = _validate_object_id(a, "a")
    oid_b = _validate_object_id(b, "b")

    try:
        coll = request.app.state.mongodb._db["simulations"]
    except AttributeError:
        _err("Database not initialized", 503)
        return _ok(None)  # unreachable

    doc_a = await coll.find_one({"_id": oid_a})
    doc_b = await coll.find_one({"_id": oid_b})

    if doc_a is None:
        _err(f"Simulation '{a}' not found", 404)
    if doc_b is None:
        _err(f"Simulation '{b}' not found", 404)

    return _ok({
        "a": _doc_to_result(doc_a),
        "b": _doc_to_result(doc_b),
    })


@router.get("/api/simulation/{sim_id}")
async def get_simulation_by_id(
    request: Request,
    sim_id: str,
) -> dict[str, Any]:
    """Return a specific simulation result by ID."""
    oid = _validate_object_id(sim_id)

    try:
        coll = request.app.state.mongodb._db["simulations"]
    except AttributeError:
        _err("Database not initialized", 503)
        return _ok(None)  # unreachable

    doc = await coll.find_one({"_id": oid})
    if doc is None:
        _err(f"Simulation '{sim_id}' not found", 404)
    return _ok(_doc_to_result(doc))
