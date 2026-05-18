"""X-021 — Phase X self-evolution GET endpoints (P1-5 §2 red line 1+2).

Three read-only surfaces over the Phase X self-evolution lane so the
front-end ``SystemStatus`` page (X-023) and any operator-facing CLI can
see the state of the 22:00 evolution shadow-run without granting any
write permissions:

* ``GET /api/evolution/pending``    — drafted-but-not-yet-promoted
                                      amendments in ``docs/decisions/pending/``.
* ``GET /api/evolution/runs``       — recent ``shadow_evolution_run_completed``
                                      audit events (Mongo primary + JSONL
                                      fallback, mirroring ``backend.api.audit``).
* ``GET /api/evolution/precision``  — ``data/rag/provenance.jsonl`` per-source
                                      ingest counts + R3 floor (0.80) so an
                                      operator can spot a sub-floor source.

Red lines
~~~~~~~~~

* **GET only.** No POST/PUT/PATCH/DELETE handler may appear in this
  module — P1-5 §2 红线 1 caps the public write surface at two endpoints
  (``POST /api/execution-reports`` + ``POST /api/reconciliation-tickets``).
* **Strict response envelopes.** Each handler returns a
  ``{status, data, error}`` envelope where ``data`` is built from a
  ``frozen=True, strict=True, extra='forbid'`` Pydantic model so a
  schema drift becomes a ``ValidationError`` instead of a silent shape
  change (P0-3 §2 red line 12 mirrors).
* **No LLM-author reachable code paths.** This module only *reads*
  filesystem + audit substrate; no LLM client is constructed here.
* **Import-gate friendly.** The Phase X 23-module isolation guard
  (X-018) bans ``backend.{api,broker,risk,llm,agents,mirofish,data}``
  imports inside Phase X modules; we sit on the consumer side
  (``backend/api/``) and freely import the Phase X constants we want to
  surface as a single-source of truth.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo import DESCENDING

from backend.audit.models import (
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)
from backend.audit.store import read_jsonl
from backend.evolution.provenance.models import (
    WHITELIST_SOURCES,
    RagProvenanceEntry,
)
from backend.evolution.rag_ingester import RAG_RETRIEVAL_PRECISION_FLOOR
from backend.services.amendment_drafter import PENDING_DIR

log = logging.getLogger("backend.api.evolution")

router = APIRouter(tags=["evolution"])


# ---------------------------------------------------------------------------
# Locked constants
# ---------------------------------------------------------------------------

# SystemStatus.vue (X-023) thresholds: 0=green, 1-3=yellow, >3=red.
PENDING_YELLOW_THRESHOLD = 1
"""Lower bound for the yellow band — at least one pending amendment is
waiting on the operator."""

PENDING_RED_THRESHOLD = 4
"""``count >= PENDING_RED_THRESHOLD`` flips the indicator to red so the
operator notices the queue is backing up before it becomes a
weeks-long pile-up."""

_RUNS_DEFAULT_LIMIT = 50
_RUNS_MAX_LIMIT = 200
"""Bounded pagination — matches the ``backend.api.audit`` cap shape so
no single request can pull the full 180-day TTL window."""

_DEFAULT_AUDIT_JSONL_PATH = Path("logs/audit.jsonl")
"""Default location — overridable via ``request.app.state.audit_jsonl_path``
so tests can point at a temp file."""

_DEFAULT_PROVENANCE_PATH = Path("data/rag/provenance.jsonl")
"""Default location — overridable via
``request.app.state.evolution_provenance_path`` for tests."""

# Default rolling window for the /precision endpoint (codex cycle 1 P2
# fix). The RAG batch precision guard is evaluated per-batch at write
# time inside ``backend.evolution.rag_ingester.assert_precision_floor``;
# the monitor surface here aggregates a rolling slice so historical data
# does not dilute or falsely flag the current week. Seven days = the
# operator's "what happened recently" window; bounds keep the query
# cheap (a year-long ledger fits easily but the operator pays the cost).
PRECISION_DEFAULT_WINDOW_DAYS = 7
PRECISION_MIN_WINDOW_DAYS = 1
PRECISION_MAX_WINDOW_DAYS = 365


# ---------------------------------------------------------------------------
# Strict response models
# ---------------------------------------------------------------------------


def _strict_config() -> ConfigDict:
    return ConfigDict(frozen=True, strict=True, extra="forbid")


class PendingAmendment(BaseModel):
    """One ``docs/decisions/pending/{id}.md`` entry."""

    model_config = _strict_config()

    amendment_id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=240)
    mtime: datetime
    size_bytes: int = Field(ge=0, le=10_000_000)


class PendingResponse(BaseModel):
    """Body of ``GET /api/evolution/pending``."""

    model_config = _strict_config()

    pending_dir: str
    count: int = Field(ge=0)
    yellow_threshold: int = Field(ge=0)
    red_threshold: int = Field(ge=0)
    items: list[PendingAmendment]
    timestamp: datetime


class ShadowRunEvent(BaseModel):
    """One ``shadow_evolution_run_completed`` audit row, projected."""

    model_config = _strict_config()

    event_id: str
    timestamp: datetime
    challenger_artifact_id: str
    champion_baseline_id: str
    passed: bool
    metrics_summary: dict[str, Any]
    outcome: Literal["success", "failure", "blocked", "degraded"]
    actor: str
    correlation_id: str | None = None


class RunsResponse(BaseModel):
    """Body of ``GET /api/evolution/runs``."""

    model_config = _strict_config()

    source: Literal["mongo", "jsonl_fallback"]
    events: list[ShadowRunEvent]
    count: int = Field(ge=0)
    limit: int = Field(ge=1)
    timestamp: datetime


class PerSourcePrecision(BaseModel):
    """Per-source ingest accounting for the R3 floor monitor."""

    model_config = _strict_config()

    source: str
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    acceptance_rate: float = Field(ge=0.0, le=1.0)
    floor_met: bool


class PrecisionResponse(BaseModel):
    """Body of ``GET /api/evolution/precision``."""

    model_config = _strict_config()

    floor: float = Field(ge=0.0, le=1.0)
    overall: PerSourcePrecision
    per_source: list[PerSourcePrecision]
    provenance_path: str
    window_days: int = Field(
        ge=PRECISION_MIN_WINDOW_DAYS, le=PRECISION_MAX_WINDOW_DAYS
    )
    window_start: datetime
    timestamp: datetime


# ---------------------------------------------------------------------------
# Envelope helpers
# ---------------------------------------------------------------------------


def _ok(data: BaseModel) -> dict[str, Any]:
    """Standard envelope (``status / data / error``).

    ``model_dump(mode='json')`` so ``datetime`` becomes ISO 8601 strings
    on the wire — matches the rest of the read-only API surface.
    """
    return {"status": "ok", "data": data.model_dump(mode="json"), "error": None}


# ---------------------------------------------------------------------------
# /pending
# ---------------------------------------------------------------------------


def _get_pending_dir(request: Request) -> Path:
    """Resolve the pending-amendments dir, honouring ``app.state`` overrides.

    Tests set ``app.state.evolution_pending_dir`` to a temp Path so the
    handler doesn't touch the repo's real ``docs/decisions/pending/``.
    """
    override = getattr(request.app.state, "evolution_pending_dir", None)
    if isinstance(override, Path):
        return override
    if isinstance(override, str) and override:
        return Path(override)
    return PENDING_DIR


@router.get("/api/evolution/pending")
async def list_pending_amendments(request: Request) -> dict[str, Any]:
    """Return the drafted amendments that still need operator review.

    Empty directory ⇒ ``items=[]`` + ``count=0`` (the green case).
    Missing directory ⇒ same as empty so a fresh checkout does not 500.
    Files are ordered newest-first by mtime so the operator sees the
    most recent draft at the top.
    """
    pending_dir = _get_pending_dir(request)
    items: list[PendingAmendment] = []
    if pending_dir.exists() and pending_dir.is_dir():
        for path in pending_dir.iterdir():
            if not path.is_file() or path.suffix != ".md":
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                log.warning(
                    "evolution_pending_stat_failed path=%s error=%s",
                    path,
                    exc,
                )
                continue
            items.append(
                PendingAmendment(
                    amendment_id=path.stem,
                    filename=path.name,
                    mtime=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    size_bytes=stat.st_size,
                )
            )

    items.sort(key=lambda x: x.mtime, reverse=True)

    body = PendingResponse(
        pending_dir=str(pending_dir),
        count=len(items),
        yellow_threshold=PENDING_YELLOW_THRESHOLD,
        red_threshold=PENDING_RED_THRESHOLD,
        items=items,
        timestamp=datetime.now(tz=UTC),
    )
    return _ok(body)


# ---------------------------------------------------------------------------
# /runs
# ---------------------------------------------------------------------------


def _get_audit_jsonl_path(request: Request) -> Path:
    """Mirror the ``backend.api.audit`` override mechanism."""
    override = getattr(request.app.state, "audit_jsonl_path", None)
    if isinstance(override, Path):
        return override
    if isinstance(override, str) and override:
        return Path(override)
    return _DEFAULT_AUDIT_JSONL_PATH


def _get_audit_collection(request: Request) -> Any | None:
    """Return the ``audit_events`` Mongo handle or ``None``."""
    mongodb = getattr(request.app.state, "mongodb", None)
    if mongodb is None:
        return None
    db = getattr(mongodb, "_db", None)
    if db is None:
        return None
    try:
        return db["audit_events"]
    except Exception:  # pragma: no cover — defensive
        return None


def _hydrate_audit(doc: dict[str, Any]) -> AuditEvent | None:
    """Mirror of ``backend.api.audit._hydrate`` — UTC-normalises BSON dates."""
    payload = dict(doc)
    payload.pop("_id", None)
    ts = payload.get("timestamp")
    if isinstance(ts, datetime) and ts.tzinfo is None:
        payload["timestamp"] = ts.replace(tzinfo=UTC)
    try:
        return AuditEvent.model_validate(payload, strict=False)
    except Exception as exc:  # noqa: BLE001 — operator visibility
        log.warning(
            "evolution_audit_doc_invalid event_id=%s error=%s",
            doc.get("event_id"),
            exc,
        )
        return None


def _project_run(event: AuditEvent) -> ShadowRunEvent | None:
    """Project one ``shadow_evolution_run_completed`` audit row into the
    wire shape.

    Defensive: an audit row with a malformed ``payload`` is reported via
    a warning and skipped rather than raising — the consumer dashboard
    must always render *something*.
    """
    payload = event.payload or {}
    challenger = payload.get("challenger_artifact_id")
    champion = payload.get("champion_baseline_id")
    passed_raw = payload.get("passed")
    metrics_raw = payload.get("metrics_summary")

    if not isinstance(challenger, str) or not challenger:
        log.warning(
            "evolution_run_payload_missing_challenger event_id=%s", event.event_id
        )
        return None
    if not isinstance(champion, str) or not champion:
        log.warning(
            "evolution_run_payload_missing_champion event_id=%s", event.event_id
        )
        return None
    if not isinstance(passed_raw, bool):
        log.warning(
            "evolution_run_payload_passed_not_bool event_id=%s", event.event_id
        )
        return None
    metrics: dict[str, Any] = metrics_raw if isinstance(metrics_raw, dict) else {}

    return ShadowRunEvent(
        event_id=str(event.event_id),
        timestamp=event.timestamp.astimezone(UTC),
        challenger_artifact_id=challenger,
        champion_baseline_id=champion,
        passed=passed_raw,
        metrics_summary=metrics,
        outcome=event.outcome.value,
        actor=event.actor.value,
        correlation_id=event.correlation_id,
    )


async def _query_mongo_runs(
    collection: Any, limit: int
) -> list[AuditEvent]:
    """Pull the most recent ``shadow_evolution_run_completed`` rows."""
    query = {"event_type": AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED.value}
    cursor = collection.find(query).sort("timestamp", DESCENDING).limit(limit)
    out: list[AuditEvent] = []
    async for doc in cursor:
        hydrated = _hydrate_audit(doc)
        if hydrated is not None:
            out.append(hydrated)
    return out


def _query_jsonl_runs(path: Path, limit: int) -> list[AuditEvent]:
    """JSONL fallback when Mongo is unreachable."""
    raw = read_jsonl(path)
    filtered = [
        e
        for e in raw
        if e.event_type is AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED
    ]
    filtered.sort(key=lambda e: e.timestamp, reverse=True)
    return filtered[:limit]


@router.get("/api/evolution/runs")
async def list_evolution_runs(
    request: Request,
    limit: int = Query(default=_RUNS_DEFAULT_LIMIT, ge=1, le=_RUNS_MAX_LIMIT),
) -> dict[str, Any]:
    """Return the most-recent shadow-run rows (newest first).

    Mongo path uses an indexed query on ``event_type``; JSONL fallback
    scans the file and filters in Python. Whichever path served the
    response is signalled via ``data.source`` so the front-end can
    surface a "degraded view" banner if needed.
    """
    collection = _get_audit_collection(request)
    source: Literal["mongo", "jsonl_fallback"] = "mongo"
    raw_events: list[AuditEvent] = []
    if collection is not None:
        try:
            raw_events = await _query_mongo_runs(collection, limit)
        except Exception as exc:  # noqa: BLE001 — fall back to JSONL
            log.warning("evolution_runs_mongo_failed error=%s", exc)
            source = "jsonl_fallback"
            raw_events = _query_jsonl_runs(_get_audit_jsonl_path(request), limit)
    else:
        source = "jsonl_fallback"
        raw_events = _query_jsonl_runs(_get_audit_jsonl_path(request), limit)

    projected: list[ShadowRunEvent] = []
    for event in raw_events:
        wire = _project_run(event)
        if wire is not None:
            projected.append(wire)

    body = RunsResponse(
        source=source,
        events=projected,
        count=len(projected),
        limit=limit,
        timestamp=datetime.now(tz=UTC),
    )
    return _ok(body)


# ---------------------------------------------------------------------------
# /precision
# ---------------------------------------------------------------------------


def _get_provenance_path(request: Request) -> Path:
    override = getattr(request.app.state, "evolution_provenance_path", None)
    if isinstance(override, Path):
        return override
    if isinstance(override, str) and override:
        return Path(override)
    return _DEFAULT_PROVENANCE_PATH


def _read_provenance_entries(path: Path) -> list[RagProvenanceEntry]:
    """Parse the JSONL ledger; corrupt lines are logged + skipped."""
    out: list[RagProvenanceEntry] = []
    if not path.exists():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            out.append(RagProvenanceEntry.model_validate_json(line))
        except Exception as exc:  # noqa: BLE001 — operator visibility
            log.warning(
                "evolution_provenance_invalid_line path=%s error=%s", path, exc
            )
    return out


def _bucketise(entries: list[RagProvenanceEntry]) -> dict[str, dict[str, int]]:
    """Build a ``{source: {total, accepted, rejected}}`` dict.

    Pre-seeded with every whitelisted source so a source that has not
    yet seen a single ingest still shows up in the response with zero
    counts (``floor_met`` defaults to True under the "0/0=1.0" rule —
    no ingest activity is not a regression).
    """
    buckets: dict[str, dict[str, int]] = {
        source: {"total": 0, "accepted": 0, "rejected": 0}
        for source in WHITELIST_SOURCES
    }
    for entry in entries:
        bucket = buckets.setdefault(
            entry.source, {"total": 0, "accepted": 0, "rejected": 0}
        )
        bucket["total"] += 1
        if entry.is_rejection:
            bucket["rejected"] += 1
        else:
            bucket["accepted"] += 1
    return buckets


def _compute_acceptance_rate(accepted: int, total: int) -> float:
    """A zero-ingest source returns 1.0 — "no batch, no precision miss".

    Once any batch has landed the rate is the standard ``accepted/total``
    ratio. Bounded into ``[0.0, 1.0]`` so the strict response model
    accepts it.
    """
    if total <= 0:
        return 1.0
    return max(0.0, min(1.0, accepted / total))


@router.get("/api/evolution/precision")
async def get_evolution_precision(
    request: Request,
    window_days: int = Query(
        default=PRECISION_DEFAULT_WINDOW_DAYS,
        ge=PRECISION_MIN_WINDOW_DAYS,
        le=PRECISION_MAX_WINDOW_DAYS,
        description=(
            "Rolling window in days for the precision aggregate (default 7). "
            "Codex cycle 1 P2: a lifetime aggregate would dilute a "
            "sub-floor week with months of good data."
        ),
    ),
) -> dict[str, Any]:
    """Return per-source ingest accounting + the R3 floor invariant.

    The R3 floor (``0.80``) is fail-closed at write time inside
    :func:`backend.evolution.rag_ingester.assert_precision_floor`; this
    endpoint is the *monitor* surface that surfaces the resulting
    distribution to the operator so they can spot a drifting source
    before the next 22:00 cron breaches the gate.

    Filtered to the rolling ``window_days`` ending at request time
    (codex cycle 1 P2). Historical ingest entries outside the window
    are excluded so a bad current week is not masked by months of
    earlier acceptance.
    """
    path = _get_provenance_path(request)
    raw_entries = _read_provenance_entries(path)
    now = datetime.now(tz=UTC)
    window_start = now - timedelta(days=window_days)
    entries = [e for e in raw_entries if e.ingested_at >= window_start]
    buckets = _bucketise(entries)

    per_source: list[PerSourcePrecision] = []
    overall_total = 0
    overall_accepted = 0
    overall_rejected = 0
    for source in sorted(buckets.keys()):
        bucket = buckets[source]
        total = bucket["total"]
        accepted = bucket["accepted"]
        rejected = bucket["rejected"]
        rate = _compute_acceptance_rate(accepted, total)
        per_source.append(
            PerSourcePrecision(
                source=source,
                total=total,
                accepted=accepted,
                rejected=rejected,
                acceptance_rate=rate,
                floor_met=rate >= RAG_RETRIEVAL_PRECISION_FLOOR,
            )
        )
        overall_total += total
        overall_accepted += accepted
        overall_rejected += rejected

    overall_rate = _compute_acceptance_rate(overall_accepted, overall_total)
    body = PrecisionResponse(
        floor=RAG_RETRIEVAL_PRECISION_FLOOR,
        overall=PerSourcePrecision(
            source="__overall__",
            total=overall_total,
            accepted=overall_accepted,
            rejected=overall_rejected,
            acceptance_rate=overall_rate,
            floor_met=overall_rate >= RAG_RETRIEVAL_PRECISION_FLOOR,
        ),
        per_source=per_source,
        provenance_path=str(path),
        window_days=window_days,
        window_start=window_start,
        timestamp=now,
    )
    return _ok(body)


# ---------------------------------------------------------------------------
# Convenience for the redline-check.sh GET-only verifier — every public
# route must appear here so an accidental write handler stands out.
# ---------------------------------------------------------------------------

_GET_ONLY_PATHS: frozenset[str] = frozenset(
    {
        "/api/evolution/pending",
        "/api/evolution/runs",
        "/api/evolution/precision",
    }
)


# Re-exports for tests + redline-check
_EXPORTED_AUDIT_TYPES = (AuditActor, AuditOutcome)


__all__ = [
    "PENDING_RED_THRESHOLD",
    "PENDING_YELLOW_THRESHOLD",
    "PRECISION_DEFAULT_WINDOW_DAYS",
    "PRECISION_MAX_WINDOW_DAYS",
    "PRECISION_MIN_WINDOW_DAYS",
    "PendingAmendment",
    "PendingResponse",
    "PerSourcePrecision",
    "PrecisionResponse",
    "RunsResponse",
    "ShadowRunEvent",
    "router",
]
