"""C-006 MiroFish evidence writer.

MiroFish has two output paths into ``evidence_collection``:

1. **event_driven** — triggered when news fan-in produces a high-severity
   event (severity ≥ ``HIGH_SEVERITY_THRESHOLD`` = 8 on the 0-10 scale
   used by :class:`backend.mirofish.schemas.EventDescription`). Hard
   cap of 1 write per trading day (P0-9 §2 redline 1 — "硬 cap=1
   严禁占用主路径"). The second event-driven attempt of a day must be
   rejected without touching Mongo so the cap stays honest.

2. **eod_review** — 17:00 mon-fri Asia/Shanghai cron summary. The EOD
   path is uncapped because it runs at a fixed cadence anyway, and
   the daily summary is the "scheduled introspection" half of P0-8
   §1.5 ("事件驱动 + 17:00 复盘双路径").

Both paths write to ``evidence_collection`` with the locked ``MIROFISH-``
prefix (P0-8 §2 redline 14). Direct mutation of ``evidence_collection``
outside this writer is a hard violation — the writer is the *only*
public entry point.

Output **never** flows into ``RiskCheckSummary`` (P0-8 §1.6.4 / §2
redline 11 — "MiroFish 加分非核心 ... 输出仅入 evidence_collection 不入
RiskCheckSummary"). The writer enforces this by construction: it only
writes to ``evidence_collection`` and has no risk-summary plumbing.

P0-8 §2 redline 14 also forbids inventing new ``evidence_id`` prefixes.
Every id this module emits starts with ``MIROFISH-`` and is validated
through :func:`backend.models.evidence.validate_evidence_id` before it
touches Mongo, so a stray rename would fail the redline-check.sh
prefix-allowlist guard too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import structlog

from backend.models.evidence import EvidencePrefix, validate_evidence_id

if TYPE_CHECKING:
    from backend.data.database import MongoDBService
    from backend.mirofish.schemas import EventDescription, SimulationResult

log = structlog.get_logger(component="mirofish.output_writer")

MiroFishPath = Literal["event_driven", "eod_review"]

# P0-8 §1.5 — events with importance_score >= this threshold are
# considered "HIGH severity" and qualify to trigger the event_driven
# path (subject to the daily cap below).
HIGH_SEVERITY_THRESHOLD = 8

# P0-9 §2 redline 1 — hard cap on event-driven MiroFish writes per
# trading day. The EOD review path is uncapped because it is itself
# a scheduled-cadence path.
EVENT_DRIVEN_DAILY_CAP = 1


class MiroFishEvidenceError(RuntimeError):
    """Raised when an evidence write is rejected before reaching Mongo.

    Caller can distinguish "cap reached" (``reason='daily_cap_reached'``)
    from prefix / payload validation errors using the ``reason`` field.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class MiroFishEvidence:
    """Frozen DTO for one ``evidence_collection`` write.

    All callers must construct this through :func:`build_event_evidence`
    or :func:`build_eod_evidence` so the ``MIROFISH-`` prefix and the
    structured payload stay consistent.
    """

    evidence_id: str
    path: MiroFishPath
    severity: int
    content: str
    trade_date: str  # YYYY-MM-DD Asia/Shanghai
    stock_codes: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )
    event_title: str = ""
    simulation_summary: str = ""

    def to_mongo(self) -> dict[str, object]:
        """Project the DTO to a Mongo-insertable dict."""
        return {
            "evidence_id": self.evidence_id,
            "prefix": EvidencePrefix.MIROFISH.value,
            "path": self.path,
            "severity": self.severity,
            "content": self.content,
            "trade_date": self.trade_date,
            "stock_codes": list(self.stock_codes),
            "sectors": list(self.sectors),
            "created_at": self.created_at,
            "event_title": self.event_title,
            "simulation_summary": self.simulation_summary,
        }


def is_high_severity_event(event: EventDescription) -> bool:
    """Return ``True`` if ``event.importance_score`` qualifies as HIGH.

    Pure predicate so callers (intelligence_officer, scheduler triggers)
    use a single source of truth instead of hard-coding the threshold.
    """
    return event.importance_score >= HIGH_SEVERITY_THRESHOLD


def make_event_evidence_id(trade_date: str, seq: int = 1) -> str:
    """Build the locked ``MIROFISH-EVENT-{YYYYMMDD}-{seq:03d}`` id.

    Uses a compact format so the suffix stays inside the 128-char cap
    of :data:`backend.models.evidence.EVIDENCE_ID_PATTERN`.
    """
    yyyymmdd = trade_date.replace("-", "")
    return f"MIROFISH-EVENT-{yyyymmdd}-{seq:03d}"


def make_eod_evidence_id(trade_date: str) -> str:
    """Build the EOD review evidence id (unique per trade_date)."""
    yyyymmdd = trade_date.replace("-", "")
    return f"MIROFISH-EOD-{yyyymmdd}"


def _format_event_content(
    event: EventDescription, result: SimulationResult | None
) -> str:
    """Render an event-driven evidence body suitable for LLM read-by-ref.

    The body is human readable but order-stable so callers can grep it
    for QA. It must not embed any decision fields (P0-10 LLM negative
    list) — only event metadata and the simulator's recommended_action
    string.
    """
    parts: list[str] = [
        f"事件: {event.title}",
        f"重要度: {event.importance_score}/10",
    ]
    if event.sectors:
        parts.append(f"板块: {', '.join(event.sectors)}")
    if event.stocks:
        parts.append(f"个股: {', '.join(event.stocks)}")
    if result is not None:
        parts.append(f"建议: {result.recommended_action}")
        if result.hidden_variables:
            top = result.hidden_variables[0]
            parts.append(
                f"主要隐变量: {top.variable} (概率 {top.probability:.0%})"
            )
    return "\n".join(parts)


def _format_eod_content(events: tuple[EventDescription, ...]) -> str:
    """Render the EOD review body — short event roll-up + count."""
    if not events:
        return "今日无高重要度事件;盘后复盘留档"
    top = events[0]
    parts = [
        f"今日 MiroFish 高重要度事件数: {len(events)}",
        f"重点事件: {top.title} (重要度 {top.importance_score}/10)",
    ]
    if top.sectors:
        parts.append(f"板块: {', '.join(top.sectors)}")
    return "\n".join(parts)


def build_event_evidence(
    *,
    event: EventDescription,
    trade_date: str,
    result: SimulationResult | None = None,
    seq: int = 1,
) -> MiroFishEvidence:
    """Build an event-driven evidence DTO without writing it.

    Separating construction from persistence lets the cap-check and the
    write-retry logic each get a clean handle on the same DTO.
    """
    evidence_id = make_event_evidence_id(trade_date, seq=seq)
    return MiroFishEvidence(
        evidence_id=evidence_id,
        path="event_driven",
        severity=event.importance_score,
        content=_format_event_content(event, result),
        trade_date=trade_date,
        stock_codes=event.stocks,
        sectors=event.sectors,
        event_title=event.title,
        simulation_summary=result.event_summary if result is not None else "",
    )


def build_eod_evidence(
    *,
    events: tuple[EventDescription, ...],
    trade_date: str,
    severity: int = 0,
) -> MiroFishEvidence:
    """Build an EOD review evidence DTO (one per trade_date)."""
    return MiroFishEvidence(
        evidence_id=make_eod_evidence_id(trade_date),
        path="eod_review",
        severity=severity,
        content=_format_eod_content(events),
        trade_date=trade_date,
        stock_codes=tuple(
            sorted({code for ev in events for code in ev.stocks})
        ),
        sectors=tuple(
            sorted({sector for ev in events for sector in ev.sectors})
        ),
        event_title=events[0].title if events else "",
        simulation_summary="EOD 复盘",
    )


class MiroFishEvidenceWriter:
    """Single entry point for ``evidence_collection`` MiroFish writes.

    The writer is intentionally narrow: no read APIs, no update / delete
    APIs, no batched writes. Each call inserts exactly one document
    after validating the evidence_id prefix and (for event-driven path)
    the daily cap.

    The daily cap is enforced **atomically** by a unique partial index
    on ``(trade_date, path)`` filtered to ``path='event_driven'`` (set
    up in :meth:`MongoDBService.initialize`). The pre-check
    ``count_documents`` is a fast-path optimisation — it avoids the
    write round-trip when we already know the cap is full. The
    DB-level uniqueness is the source of truth for concurrent writers:
    if two callers race, exactly one insert succeeds and the other
    surfaces as ``DuplicateKeyError``, which the writer translates to
    ``MiroFishEvidenceError(reason='daily_cap_reached')`` (codex cycle
    2 P2).
    """

    COLLECTION_NAME = "evidence_collection"

    def __init__(self, mongodb: MongoDBService) -> None:
        self._mongodb = mongodb
        self._log = log

    async def write(self, evidence: MiroFishEvidence) -> bool:
        """Persist one MiroFish evidence row. Returns ``True`` on insert.

        Raises :class:`MiroFishEvidenceError` (with ``reason``) when the
        write is rejected before reaching Mongo *or* when Mongo's
        partial unique index rejects a racing event_driven insert.
        Returns ``False`` only for non-cap insert failures (callers
        may log and continue).
        """
        # Hard contract: prefix must be MIROFISH- and pass the locked
        # P0-8 regex. Defence-in-depth even though ``build_*`` helpers
        # already shape the id.
        if not evidence.evidence_id.startswith(
            f"{EvidencePrefix.MIROFISH.value}-"
        ):
            raise MiroFishEvidenceError(
                f"non-MIROFISH evidence_id {evidence.evidence_id!r}",
                reason="prefix_violation",
            )
        try:
            validate_evidence_id(evidence.evidence_id)
        except ValueError as exc:
            raise MiroFishEvidenceError(
                str(exc), reason="evidence_id_invalid"
            ) from exc

        # Event-driven cap pre-check (fast-path; the DB partial unique
        # index is the canonical guard against concurrent inserts).
        if evidence.path == "event_driven":
            # codex cycle 3 P2: if the partial unique index could not be
            # created at startup, the only thing left guarding the cap
            # is a non-atomic count_documents. Refuse the event-driven
            # write outright so the P0-9 §2 redline 1 stays fail-closed
            # instead of silently downgrading to racy behaviour.
            if not getattr(
                self._mongodb, "evidence_event_cap_index_ok", False
            ):
                self._log.warning(
                    "mirofish_event_cap_index_missing",
                    trade_date=evidence.trade_date,
                )
                raise MiroFishEvidenceError(
                    "event_driven cap index not enforced; refusing write",
                    reason="cap_index_missing",
                )
            count = await self._count_event_driven_today(
                evidence.trade_date
            )
            if count >= EVENT_DRIVEN_DAILY_CAP:
                self._log.info(
                    "mirofish_event_driven_cap_reached",
                    trade_date=evidence.trade_date,
                    cap=EVENT_DRIVEN_DAILY_CAP,
                    existing=count,
                )
                raise MiroFishEvidenceError(
                    "event_driven cap reached", reason="daily_cap_reached"
                )

        coll = self._mongodb._db[self.COLLECTION_NAME]
        try:
            await coll.insert_one(evidence.to_mongo())
        except Exception as exc:
            # Distinguish "cap raced and lost" from a generic insert
            # failure. Mongo surfaces unique-index violations with
            # ``DuplicateKeyError`` in pymongo / motor; we match on
            # the error code + message to keep the fake-mongo test
            # surface free of the real motor dependency.
            if evidence.path == "event_driven" and _is_duplicate_key_error(
                exc
            ):
                self._log.info(
                    "mirofish_event_driven_cap_race_lost",
                    trade_date=evidence.trade_date,
                    evidence_id=evidence.evidence_id,
                )
                raise MiroFishEvidenceError(
                    "event_driven cap reached (DB race)",
                    reason="daily_cap_reached",
                ) from exc
            self._log.warning(
                "mirofish_evidence_insert_failed",
                evidence_id=evidence.evidence_id,
                error=str(exc),
            )
            return False
        self._log.info(
            "mirofish_evidence_written",
            evidence_id=evidence.evidence_id,
            path=evidence.path,
        )
        return True

    async def _count_event_driven_today(self, trade_date: str) -> int:
        """Count existing event_driven rows for ``trade_date``."""
        coll = self._mongodb._db[self.COLLECTION_NAME]
        try:
            return await coll.count_documents(
                {"trade_date": trade_date, "path": "event_driven"}
            )
        except Exception as exc:
            # Fail-closed: when the cap query itself fails we behave as
            # if the cap is already reached. The day's event-driven
            # path is degraded but the EOD path still runs at 17:00.
            self._log.warning(
                "mirofish_cap_count_failed",
                trade_date=trade_date,
                error=str(exc),
            )
            return EVENT_DRIVEN_DAILY_CAP


def _is_duplicate_key_error(exc: Exception) -> bool:
    """Return ``True`` if ``exc`` looks like a Mongo duplicate-key error.

    Matches both pymongo's ``DuplicateKeyError`` (subclass of
    ``WriteError`` carrying code ``11000`` / ``11001``) and any
    library-agnostic shape that surfaces the error message text. Pure
    helper so the writer stays decoupled from the live motor /
    pymongo classes during unit tests with the fake Mongo collection.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in (11000, 11001):
        return True
    cls_name = type(exc).__name__
    if cls_name == "DuplicateKeyError":
        return True
    msg = str(exc).lower()
    return "duplicate key" in msg or "e11000" in msg


__all__ = [
    "EVENT_DRIVEN_DAILY_CAP",
    "HIGH_SEVERITY_THRESHOLD",
    "MiroFishEvidence",
    "MiroFishEvidenceError",
    "MiroFishEvidenceWriter",
    "MiroFishPath",
    "build_eod_evidence",
    "build_event_evidence",
    "is_high_severity_event",
    "make_eod_evidence_id",
    "make_event_evidence_id",
]
