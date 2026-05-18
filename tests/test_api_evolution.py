"""X-021 — backend/api/evolution.py 3 GET endpoint tests.

Coverage matrix (one section per endpoint plus invariants):

* :func:`/api/evolution/pending`
  - empty pending dir ⇒ 0 items + count=0 (green case)
  - missing pending dir ⇒ no 500
  - multiple drafts ⇒ newest-first sort by mtime + correct count
  - non-``.md`` files ignored
  - thresholds returned (0=green, 1=yellow, 4=red) so the front-end
    SystemStatus.vue card can render the colour bands
* :func:`/api/evolution/runs`
  - Mongo happy path filters by ``event_type=shadow_evolution_run_completed``
  - Mongo failure → JSONL fallback
  - non-shadow-run audit rows in JSONL are excluded by the filter
  - rows with malformed payload are dropped + the handler still 200s
  - limit bounds enforced (422)
* :func:`/api/evolution/precision`
  - empty provenance ledger ⇒ floor metadata still emitted
  - missing provenance file ⇒ same as empty (no 500)
  - accepted-only entries ⇒ acceptance_rate=1.0 ⇒ floor_met=True
  - mixed accepted + rejected ⇒ acceptance_rate computed; floor=0.80
    means 4/5=0.8 passes, 3/5=0.6 fails
  - per-source breakdown covers all five WHITELIST_SOURCES
* Invariants
  - GET-only AST guard
  - Pydantic strict response models actually fail-closed on extra keys
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from backend.api.evolution import (
    PENDING_RED_THRESHOLD,
    PENDING_YELLOW_THRESHOLD,
    PRECISION_DEFAULT_WINDOW_DAYS,
    PRECISION_MAX_WINDOW_DAYS,
    PendingAmendment,
    PendingResponse,
    PerSourcePrecision,
    PrecisionResponse,
    RunsResponse,
    ShadowRunEvent,
)
from backend.api.evolution import (
    router as evolution_router,
)
from backend.audit.models import (
    AuditActor,
    AuditEvent,
    AuditEventType,
    AuditOutcome,
)
from backend.evolution.provenance.models import (
    WHITELIST_SOURCES,
    RagProvenanceEntry,
    SanitizationApplied,
)
from backend.evolution.rag_ingester import RAG_RETRIEVAL_PRECISION_FLOOR

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def now_utc() -> datetime:
    return datetime(2026, 5, 18, 22, 0, tzinfo=UTC)


def _build_app(
    *,
    pending_dir: Path | None = None,
    provenance_path: Path | None = None,
    mongodb: Any | None = None,
    audit_jsonl_path: Path | None = None,
) -> FastAPI:
    app = FastAPI()
    if pending_dir is not None:
        app.state.evolution_pending_dir = pending_dir
    if provenance_path is not None:
        app.state.evolution_provenance_path = provenance_path
    if mongodb is not None:
        app.state.mongodb = mongodb
    if audit_jsonl_path is not None:
        app.state.audit_jsonl_path = audit_jsonl_path
    app.include_router(evolution_router)
    return app


def _shadow_event(
    *,
    ts: datetime,
    challenger: str = "QM-CHAMP-20260518T220000Z",
    champion: str = "QM-BASE-v1.0",
    passed: bool = True,
    metrics: dict[str, Any] | None = None,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    actor: AuditActor = AuditActor.SCHEDULER,
    correlation_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        timestamp=ts,
        event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
        actor=actor,
        resource_type="shadow_evolution_run",
        resource_id=challenger,
        payload={
            "challenger_artifact_id": challenger,
            "champion_baseline_id": champion,
            "passed": passed,
            "metrics_summary": metrics or {},
        },
        outcome=outcome,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# /pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_empty_dir_returns_zero(tmp_path: Path) -> None:
    pending = tmp_path / "pending"
    pending.mkdir()
    app = _build_app(pending_dir=pending)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/pending")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["count"] == 0
    assert body["data"]["items"] == []
    assert body["data"]["yellow_threshold"] == PENDING_YELLOW_THRESHOLD
    assert body["data"]["red_threshold"] == PENDING_RED_THRESHOLD


@pytest.mark.asyncio
async def test_pending_missing_dir_does_not_500(tmp_path: Path) -> None:
    pending = tmp_path / "missing-on-purpose"
    app = _build_app(pending_dir=pending)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/pending")
    assert resp.status_code == 200
    assert resp.json()["data"]["count"] == 0


@pytest.mark.asyncio
async def test_pending_sorts_newest_first_and_counts(tmp_path: Path) -> None:
    pending = tmp_path / "pending"
    pending.mkdir()
    old = pending / "OLD-2026-05-15.md"
    old.write_text("old draft", encoding="utf-8")
    new = pending / "NEW-2026-05-18.md"
    new.write_text("new draft", encoding="utf-8")
    # Force mtime ordering (avoid filesystem mtime resolution flake).
    import os as _os

    _os.utime(old, (1_700_000_000, 1_700_000_000))
    _os.utime(new, (1_800_000_000, 1_800_000_000))

    app = _build_app(pending_dir=pending)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/pending")
    body = resp.json()
    assert body["data"]["count"] == 2
    ids = [item["amendment_id"] for item in body["data"]["items"]]
    assert ids == ["NEW-2026-05-18", "OLD-2026-05-15"]


@pytest.mark.asyncio
async def test_pending_ignores_non_markdown(tmp_path: Path) -> None:
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "draft.md").write_text("amendment", encoding="utf-8")
    (pending / "README.txt").write_text("ignore me", encoding="utf-8")
    (pending / ".hidden").write_text("ignore me too", encoding="utf-8")

    app = _build_app(pending_dir=pending)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/pending")
    body = resp.json()
    assert body["data"]["count"] == 1
    assert body["data"]["items"][0]["amendment_id"] == "draft"


@pytest.mark.asyncio
async def test_pending_rejects_symlinks(tmp_path: Path) -> None:
    # Codex X-027 R4 claim 6: ``path.is_file()`` follows symlinks, so a
    # ``foo.md -> /etc/passwd`` would be served. The fix explicitly
    # tests ``is_symlink()`` before ``is_file()``.
    pending = tmp_path / "pending"
    pending.mkdir()
    target = tmp_path / "outside.md"
    target.write_text("sensitive content", encoding="utf-8")
    link = pending / "draft.md"
    link.symlink_to(target)

    app = _build_app(pending_dir=pending)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/pending")
    body = resp.json()
    # The symlinked entry is rejected; the listing is empty.
    assert body["data"]["count"] == 0
    assert body["data"]["items"] == []


@pytest.mark.asyncio
async def test_pending_emits_pending_dir_for_observability(tmp_path: Path) -> None:
    pending = tmp_path / "pending"
    pending.mkdir()
    app = _build_app(pending_dir=pending)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/pending")
    assert resp.json()["data"]["pending_dir"].endswith("pending")


# ---------------------------------------------------------------------------
# /runs
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, _key: str, _direction: int) -> _FakeCursor:
        return self

    def limit(self, n: int) -> _FakeCursor:
        return _FakeCursor(self._docs[:n])

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeCollection:
    def __init__(self, docs: list[AuditEvent], *, fail: bool = False) -> None:
        self._docs = [self._serialize(e) for e in docs]
        self._fail = fail
        self.last_query: dict[str, Any] | None = None

    @staticmethod
    def _serialize(e: AuditEvent) -> dict[str, Any]:
        return {
            "event_id": str(e.event_id),
            "timestamp": e.timestamp,
            "event_type": e.event_type.value,
            "actor": e.actor.value,
            "actor_detail": e.actor_detail,
            "resource_type": e.resource_type,
            "resource_id": e.resource_id,
            "payload": e.payload,
            "outcome": e.outcome.value,
            "correlation_id": e.correlation_id,
            "reason_namespace": e.reason_namespace,
        }

    def find(self, query: dict[str, Any]) -> _FakeCursor:
        if self._fail:
            raise RuntimeError("mongo down")
        self.last_query = query
        docs = list(self._docs)
        if "event_type" in query:
            docs = [d for d in docs if d["event_type"] == query["event_type"]]
        docs.sort(key=lambda d: d["timestamp"], reverse=True)
        return _FakeCursor(docs)


class _FakeMongoDB:
    def __init__(self, collection: _FakeCollection) -> None:
        self._db = {"audit_events": collection}


@pytest.mark.asyncio
async def test_runs_mongo_happy_path(now_utc: datetime) -> None:
    events = [
        _shadow_event(ts=now_utc - timedelta(days=2), passed=True),
        _shadow_event(
            ts=now_utc - timedelta(days=1),
            passed=False,
            outcome=AuditOutcome.FAILURE,
            challenger="QM-CHAMP-20260517",
        ),
        _shadow_event(ts=now_utc, passed=True, challenger="QM-CHAMP-20260518"),
    ]
    coll = _FakeCollection(events)
    app = _build_app(mongodb=_FakeMongoDB(coll))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/runs", params={"limit": 10})
    body = resp.json()
    assert resp.status_code == 200
    assert body["data"]["source"] == "mongo"
    assert body["data"]["count"] == 3
    # Most-recent first.
    assert body["data"]["events"][0]["challenger_artifact_id"] == "QM-CHAMP-20260518"
    # Query was filtered to the shadow-run event type.
    assert coll.last_query == {
        "event_type": "shadow_evolution_run_completed",
    }


@pytest.mark.asyncio
async def test_runs_mongo_failure_falls_back_to_jsonl(
    now_utc: datetime, tmp_path: Path
) -> None:
    failing = _FakeCollection([], fail=True)
    jsonl_path = tmp_path / "audit.jsonl"
    line = _shadow_event(ts=now_utc, challenger="QM-FALLBACK").model_dump_json()
    jsonl_path.write_text(line + "\n", encoding="utf-8")
    app = _build_app(
        mongodb=_FakeMongoDB(failing),
        audit_jsonl_path=jsonl_path,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/runs")
    body = resp.json()
    assert body["data"]["source"] == "jsonl_fallback"
    assert body["data"]["count"] == 1
    assert body["data"]["events"][0]["challenger_artifact_id"] == "QM-FALLBACK"


@pytest.mark.asyncio
async def test_runs_jsonl_excludes_non_shadow_rows(
    now_utc: datetime, tmp_path: Path
) -> None:
    jsonl_path = tmp_path / "audit.jsonl"
    shadow = _shadow_event(ts=now_utc)
    other = AuditEvent(
        timestamp=now_utc,
        event_type=AuditEventType.PROMPT_VERSION_PINNED,
        actor=AuditActor.SYSTEM,
        resource_type="prompt_version",
        resource_id="fundamental:v1",
        payload={},
        outcome=AuditOutcome.SUCCESS,
    )
    jsonl_path.write_text(
        "\n".join([shadow.model_dump_json(), other.model_dump_json()]) + "\n",
        encoding="utf-8",
    )
    # Mongo unwired ⇒ JSONL fallback path.
    app = _build_app(audit_jsonl_path=jsonl_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/runs")
    body = resp.json()
    assert body["data"]["source"] == "jsonl_fallback"
    assert body["data"]["count"] == 1
    assert (
        body["data"]["events"][0]["challenger_artifact_id"]
        == shadow.payload["challenger_artifact_id"]
    )


@pytest.mark.asyncio
async def test_runs_drops_rows_with_malformed_payload(
    now_utc: datetime, tmp_path: Path
) -> None:
    """A payload missing ``challenger_artifact_id`` must be dropped, not 500."""
    jsonl_path = tmp_path / "audit.jsonl"
    bad = AuditEvent(
        timestamp=now_utc,
        event_type=AuditEventType.SHADOW_EVOLUTION_RUN_COMPLETED,
        actor=AuditActor.SCHEDULER,
        resource_type="shadow_evolution_run",
        resource_id="X-MAL",
        payload={"passed": True},  # missing challenger / champion
        outcome=AuditOutcome.SUCCESS,
    )
    ok = _shadow_event(ts=now_utc, challenger="QM-OK")
    jsonl_path.write_text(
        "\n".join([bad.model_dump_json(), ok.model_dump_json()]) + "\n",
        encoding="utf-8",
    )
    app = _build_app(audit_jsonl_path=jsonl_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/runs")
    body = resp.json()
    assert resp.status_code == 200
    assert body["data"]["count"] == 1
    assert body["data"]["events"][0]["challenger_artifact_id"] == "QM-OK"


@pytest.mark.asyncio
async def test_runs_limit_bounds_enforced() -> None:
    app = _build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        low = await client.get("/api/evolution/runs", params={"limit": 0})
        high = await client.get("/api/evolution/runs", params={"limit": 9999})
    assert low.status_code == 422
    assert high.status_code == 422


# ---------------------------------------------------------------------------
# /precision
# ---------------------------------------------------------------------------


def _provenance(
    *,
    source: str,
    doc_seq: str = "001",
    rejected: bool = False,
    ingested_at: datetime | None = None,
) -> RagProvenanceEntry:
    prefix_to_source = {
        "arxiv": "ARXIV",
        "semanticscholar": "S2",
        "openreview": "OPENREVIEW",
        "github_releases": "GH-REL",
        "akshare": "AKSHARE",
    }
    prefix = prefix_to_source[source]
    sanit = SanitizationApplied(
        html_stripped=False,
        control_chars_removed=0,
        injection_markers_flagged=0,
        unicode_normalized_nfkc=False,
        max_consecutive_whitespace_collapsed=False,
    )
    # Default ingestion time is "now" so the entry always falls inside
    # the default rolling window (codex cycle 1 P2 fix); callers that
    # want to test out-of-window behaviour pass ``ingested_at``
    # explicitly.
    ts = ingested_at if ingested_at is not None else datetime.now(tz=UTC)
    return RagProvenanceEntry(
        doc_id=f"{prefix}-{doc_seq}",
        source=source,  # type: ignore[arg-type]
        source_url="https://example.com/x",
        source_domain="example.com",
        title=f"doc {doc_seq}",
        authors=("alice",),
        published_at=ts - timedelta(days=1),
        ingested_at=ts,
        content_sha256="0" * 64,
        content_length_chars=512,
        whitelist_rule_version="v1.0",
        license="cc-by",
        external_id=f"ext-{doc_seq}",
        language_detected="en",
        sanitization_applied=sanit,
        ingester_version="x-011",
        rejection_reason="injection_marker" if rejected else None,
    )


def _write_jsonl(path: Path, entries: list[RagProvenanceEntry]) -> None:
    path.write_text(
        "\n".join(e.model_dump_json() for e in entries) + ("\n" if entries else ""),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_precision_empty_returns_floor_with_zero_counts(
    tmp_path: Path,
) -> None:
    prov = tmp_path / "provenance.jsonl"
    prov.write_text("", encoding="utf-8")
    app = _build_app(provenance_path=prov)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/precision")
    body = resp.json()
    assert resp.status_code == 200
    assert body["data"]["floor"] == RAG_RETRIEVAL_PRECISION_FLOOR
    assert body["data"]["overall"]["total"] == 0
    # Zero-batch source ⇒ acceptance_rate=1.0 (no regression).
    assert body["data"]["overall"]["acceptance_rate"] == 1.0
    assert body["data"]["overall"]["floor_met"] is True
    # All five WHITELIST_SOURCES present in the per-source breakdown.
    sources = [row["source"] for row in body["data"]["per_source"]]
    assert set(sources) == set(WHITELIST_SOURCES)


@pytest.mark.asyncio
async def test_precision_missing_file_does_not_500(tmp_path: Path) -> None:
    prov = tmp_path / "does-not-exist.jsonl"
    app = _build_app(provenance_path=prov)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/precision")
    assert resp.status_code == 200
    assert resp.json()["data"]["overall"]["total"] == 0


@pytest.mark.asyncio
async def test_precision_accepted_only_passes_floor(tmp_path: Path) -> None:
    prov = tmp_path / "provenance.jsonl"
    _write_jsonl(
        prov,
        [
            _provenance(source="arxiv", doc_seq="001"),
            _provenance(source="arxiv", doc_seq="002"),
            _provenance(source="arxiv", doc_seq="003"),
        ],
    )
    app = _build_app(provenance_path=prov)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/precision")
    body = resp.json()
    arxiv_row = next(
        r for r in body["data"]["per_source"] if r["source"] == "arxiv"
    )
    assert arxiv_row["total"] == 3
    assert arxiv_row["accepted"] == 3
    assert arxiv_row["rejected"] == 0
    assert arxiv_row["acceptance_rate"] == 1.0
    assert arxiv_row["floor_met"] is True


@pytest.mark.asyncio
async def test_precision_5_of_5_passes_4_of_5_borderline_3_of_5_fails(
    tmp_path: Path,
) -> None:
    """4/5 = 0.80 == floor passes (>=); 3/5 = 0.60 fails."""
    prov = tmp_path / "provenance.jsonl"
    # arxiv: 4 accepted + 1 rejected = 0.80
    # semanticscholar: 3 accepted + 2 rejected = 0.60
    entries = [
        _provenance(source="arxiv", doc_seq=f"a{i}") for i in range(1, 5)
    ] + [_provenance(source="arxiv", doc_seq="a5", rejected=True)] + [
        _provenance(source="semanticscholar", doc_seq=f"s{i}") for i in range(1, 4)
    ] + [
        _provenance(source="semanticscholar", doc_seq=f"s{i}", rejected=True)
        for i in (4, 5)
    ]
    _write_jsonl(prov, entries)
    app = _build_app(provenance_path=prov)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/precision")
    body = resp.json()
    by_source = {r["source"]: r for r in body["data"]["per_source"]}
    assert by_source["arxiv"]["acceptance_rate"] == pytest.approx(0.80, abs=1e-9)
    assert by_source["arxiv"]["floor_met"] is True
    assert by_source["semanticscholar"]["acceptance_rate"] == pytest.approx(
        0.60, abs=1e-9
    )
    assert by_source["semanticscholar"]["floor_met"] is False
    # Overall: 7 accepted / 10 total = 0.70 fails the floor.
    assert body["data"]["overall"]["total"] == 10
    assert body["data"]["overall"]["acceptance_rate"] == pytest.approx(0.70)
    assert body["data"]["overall"]["floor_met"] is False


@pytest.mark.asyncio
async def test_precision_default_window_excludes_old_entries(
    tmp_path: Path,
) -> None:
    """Codex cycle 1 P2 regression.

    An old accepted entry (8 days ago) and a fresh rejected entry
    (today): under the 7-day default window only the rejection counts,
    so the source reports ``acceptance_rate=0.0`` and ``floor_met=False``.
    Without the rolling-window filter the old accepted row would mask
    the current breach.
    """
    prov = tmp_path / "provenance.jsonl"
    now = datetime.now(tz=UTC)
    _write_jsonl(
        prov,
        [
            _provenance(
                source="arxiv",
                doc_seq="OLD",
                ingested_at=now - timedelta(days=8),
            ),
            _provenance(
                source="arxiv",
                doc_seq="NEW",
                rejected=True,
                ingested_at=now - timedelta(hours=1),
            ),
        ],
    )
    app = _build_app(provenance_path=prov)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/precision")
    body = resp.json()
    arxiv_row = next(
        r for r in body["data"]["per_source"] if r["source"] == "arxiv"
    )
    assert arxiv_row["total"] == 1
    assert arxiv_row["accepted"] == 0
    assert arxiv_row["rejected"] == 1
    assert arxiv_row["acceptance_rate"] == 0.0
    assert arxiv_row["floor_met"] is False
    assert body["data"]["window_days"] == PRECISION_DEFAULT_WINDOW_DAYS


@pytest.mark.asyncio
async def test_precision_window_days_honours_query_param(tmp_path: Path) -> None:
    """``?window_days=30`` widens the window so 8-day-old entries count."""
    prov = tmp_path / "provenance.jsonl"
    now = datetime.now(tz=UTC)
    _write_jsonl(
        prov,
        [
            _provenance(
                source="arxiv",
                doc_seq="OLD",
                ingested_at=now - timedelta(days=8),
            ),
            _provenance(
                source="arxiv",
                doc_seq="NEW",
                rejected=True,
                ingested_at=now - timedelta(hours=1),
            ),
        ],
    )
    app = _build_app(provenance_path=prov)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/evolution/precision", params={"window_days": 30}
        )
    body = resp.json()
    arxiv_row = next(
        r for r in body["data"]["per_source"] if r["source"] == "arxiv"
    )
    assert arxiv_row["total"] == 2
    assert arxiv_row["accepted"] == 1
    assert arxiv_row["rejected"] == 1
    assert arxiv_row["acceptance_rate"] == 0.5
    assert arxiv_row["floor_met"] is False
    assert body["data"]["window_days"] == 30


@pytest.mark.asyncio
async def test_precision_window_days_bounds_enforced(tmp_path: Path) -> None:
    app = _build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        low = await client.get(
            "/api/evolution/precision", params={"window_days": 0}
        )
        high = await client.get(
            "/api/evolution/precision",
            params={"window_days": PRECISION_MAX_WINDOW_DAYS + 1},
        )
    assert low.status_code == 422
    assert high.status_code == 422


@pytest.mark.asyncio
async def test_precision_window_start_emitted(tmp_path: Path) -> None:
    """The response carries ``window_start`` so the operator can pin
    *which* slice of history the numbers describe."""
    prov = tmp_path / "provenance.jsonl"
    prov.write_text("", encoding="utf-8")
    app = _build_app(provenance_path=prov)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/precision")
    body = resp.json()
    # ``window_start`` should be ~window_days days before ``timestamp``.
    ts = datetime.fromisoformat(body["data"]["timestamp"].replace("Z", "+00:00"))
    ws = datetime.fromisoformat(body["data"]["window_start"].replace("Z", "+00:00"))
    delta = (ts - ws).total_seconds()
    expected = PRECISION_DEFAULT_WINDOW_DAYS * 86400
    assert abs(delta - expected) < 5.0, (
        f"window_start should trail timestamp by ~{expected}s; got delta={delta}"
    )


@pytest.mark.asyncio
async def test_precision_corrupt_line_is_logged_not_500(tmp_path: Path) -> None:
    prov = tmp_path / "provenance.jsonl"
    good = _provenance(source="arxiv", doc_seq="001")
    prov.write_text(
        good.model_dump_json() + "\n{not-json-at-all}\n",
        encoding="utf-8",
    )
    app = _build_app(provenance_path=prov)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/evolution/precision")
    assert resp.status_code == 200
    body = resp.json()
    # The good line still counts.
    arxiv_row = next(
        r for r in body["data"]["per_source"] if r["source"] == "arxiv"
    )
    assert arxiv_row["total"] == 1


# ---------------------------------------------------------------------------
# Strict response invariants (Pydantic frozen + strict + extra='forbid')
# ---------------------------------------------------------------------------


def test_pending_response_rejects_extra_keys() -> None:
    """``extra='forbid'`` keeps the wire schema honest under upgrades."""
    with pytest.raises(ValidationError):
        PendingResponse(  # type: ignore[call-arg]
            pending_dir="x",
            count=0,
            yellow_threshold=1,
            red_threshold=4,
            items=[],
            timestamp=datetime.now(tz=UTC),
            __not_a_field__="oops",  # type: ignore[arg-type]
        )


def test_pending_amendment_rejects_negative_size() -> None:
    with pytest.raises(ValidationError):
        PendingAmendment(
            amendment_id="x",
            filename="x.md",
            mtime=datetime.now(tz=UTC),
            size_bytes=-1,
        )


def test_runs_response_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        RunsResponse(  # type: ignore[call-arg]
            source="mongo",
            events=[],
            count=0,
            limit=10,
            timestamp=datetime.now(tz=UTC),
            __not_a_field__="oops",  # type: ignore[arg-type]
        )


def test_shadow_run_event_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        ShadowRunEvent(  # type: ignore[call-arg]
            event_id="x",
            timestamp=datetime.now(tz=UTC),
            challenger_artifact_id="QM-A",
            champion_baseline_id="QM-B",
            passed=True,
            metrics_summary={},
            outcome="success",
            actor="scheduler",
            __not_a_field__="oops",  # type: ignore[arg-type]
        )


def test_precision_response_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        PrecisionResponse(  # type: ignore[call-arg]
            floor=0.80,
            overall=PerSourcePrecision(
                source="__overall__",
                total=0,
                accepted=0,
                rejected=0,
                acceptance_rate=1.0,
                floor_met=True,
            ),
            per_source=[],
            provenance_path="x",
            window_days=PRECISION_DEFAULT_WINDOW_DAYS,
            window_start=datetime.now(tz=UTC),
            timestamp=datetime.now(tz=UTC),
            __not_a_field__="oops",  # type: ignore[arg-type]
        )


def test_per_source_precision_rejects_rate_outside_unit_interval() -> None:
    with pytest.raises(ValidationError):
        PerSourcePrecision(
            source="arxiv",
            total=0,
            accepted=0,
            rejected=0,
            acceptance_rate=1.5,  # type: ignore[arg-type]
            floor_met=True,
        )


# ---------------------------------------------------------------------------
# GET-only AST guard (P1-5 §2 red line 1)
# ---------------------------------------------------------------------------


def test_evolution_router_is_get_only() -> None:
    """No write handlers in backend/api/evolution.py (red line: GET only)."""
    source = Path("backend/api/evolution.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"post", "put", "patch", "delete"}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call):
                    func = deco.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr in forbidden
                    ):
                        found.append(f"{node.name}:{func.attr}")
    assert not found, f"evolution API must be GET-only; found {found}"


def test_evolution_paths_locked() -> None:
    """The three locked paths appear in the in-module declared set."""
    from backend.api.evolution import _GET_ONLY_PATHS

    assert _GET_ONLY_PATHS == frozenset(
        {
            "/api/evolution/pending",
            "/api/evolution/runs",
            "/api/evolution/precision",
        }
    )


def test_router_includes_three_get_routes() -> None:
    """Quick assertion that fastapi sees three GET routes."""
    paths = {(r.path, tuple(sorted(r.methods))) for r in evolution_router.routes}  # type: ignore[attr-defined]
    assert ("/api/evolution/pending", ("GET",)) in paths
    assert ("/api/evolution/runs", ("GET",)) in paths
    assert ("/api/evolution/precision", ("GET",)) in paths


def test_main_includes_evolution_router() -> None:
    """``backend/main.py`` must wire the router so / not in main = unmounted."""
    from backend.main import app as main_app

    found = [
        getattr(r, "path", None) for r in main_app.routes  # type: ignore[attr-defined]
    ]
    assert "/api/evolution/pending" in found
    assert "/api/evolution/runs" in found
    assert "/api/evolution/precision" in found
