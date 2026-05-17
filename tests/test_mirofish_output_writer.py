"""C-006 MiroFish output writer tests.

Locks:
* MIROFISH- prefix on every evidence_id
* event_driven path hard cap=1/day
* eod_review path uncapped but unique per trade_date via id collision
* writer never touches RiskCheckSummary (by construction)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.mirofish.output_writer import (
    EVENT_DRIVEN_DAILY_CAP,
    HIGH_SEVERITY_THRESHOLD,
    MiroFishEvidence,
    MiroFishEvidenceError,
    MiroFishEvidenceWriter,
    build_eod_evidence,
    build_event_evidence,
    is_high_severity_event,
    make_eod_evidence_id,
    make_event_evidence_id,
)
from backend.mirofish.schemas import EventDescription
from backend.models.evidence import (
    EVIDENCE_ID_PATTERN,
    EvidencePrefix,
    parse_evidence_prefix,
    validate_evidence_id,
)

# ---------------------------------------------------------------------------
# Fakes for Mongo / motor — only the surface we need
# ---------------------------------------------------------------------------


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, doc: dict[str, Any]) -> None:
        # Mimic the unique index on evidence_id: reject duplicates.
        for existing in self.docs:
            if existing["evidence_id"] == doc["evidence_id"]:
                raise RuntimeError("E11000 duplicate key on evidence_id")
        self.docs.append(doc)

    async def count_documents(self, filt: dict[str, Any]) -> int:
        n = 0
        for d in self.docs:
            if all(d.get(k) == v for k, v in filt.items()):
                n += 1
        return n


class _FakeDB:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection())


@dataclass
class _FakeMongo:
    _db: _FakeDB = field(default_factory=_FakeDB)
    # Default to "index present" so the writer happy path runs; the
    # codex cycle 3 P2 regression test flips this to False to assert
    # the fail-closed branch.
    evidence_event_cap_index_ok: bool = True


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestSeverityThreshold:
    def test_below_threshold_not_high(self) -> None:
        event = EventDescription(
            title="x", content="y", importance_score=HIGH_SEVERITY_THRESHOLD - 1
        )
        assert not is_high_severity_event(event)

    def test_at_threshold_is_high(self) -> None:
        event = EventDescription(
            title="x", content="y", importance_score=HIGH_SEVERITY_THRESHOLD
        )
        assert is_high_severity_event(event)

    def test_max_score_is_high(self) -> None:
        event = EventDescription(title="x", content="y", importance_score=10)
        assert is_high_severity_event(event)


class TestEvidenceIdBuilders:
    def test_event_evidence_id_format(self) -> None:
        eid = make_event_evidence_id("2026-05-16", seq=1)
        assert eid == "MIROFISH-EVENT-20260516-001"
        validate_evidence_id(eid)
        assert parse_evidence_prefix(eid) is EvidencePrefix.MIROFISH

    def test_event_evidence_seq_pads(self) -> None:
        assert make_event_evidence_id("2026-05-16", seq=42).endswith("-042")

    def test_eod_evidence_id_format(self) -> None:
        eid = make_eod_evidence_id("2026-05-16")
        assert eid == "MIROFISH-EOD-20260516"
        validate_evidence_id(eid)
        assert parse_evidence_prefix(eid) is EvidencePrefix.MIROFISH

    def test_id_matches_locked_pattern(self) -> None:
        """Every id must match the P0-8 §1.6.2 regex without exception."""
        import re

        rx = re.compile(EVIDENCE_ID_PATTERN)
        for eid in (
            make_event_evidence_id("2026-05-16"),
            make_eod_evidence_id("2026-05-16"),
        ):
            assert rx.fullmatch(eid) is not None


class TestBuilders:
    def test_build_event_evidence(self) -> None:
        event = EventDescription(
            title="降准消息",
            content="央行宣布全面降准",
            importance_score=9,
            stocks=("600519", "000333"),
        )
        evidence = build_event_evidence(
            event=event, trade_date="2026-05-16"
        )
        assert evidence.path == "event_driven"
        assert evidence.severity == 9
        assert evidence.evidence_id.startswith("MIROFISH-EVENT-")
        assert evidence.stock_codes == ("600519", "000333")
        assert "降准消息" in evidence.content
        assert "9/10" in evidence.content

    def test_build_eod_evidence_empty(self) -> None:
        evidence = build_eod_evidence(events=(), trade_date="2026-05-16")
        assert evidence.path == "eod_review"
        assert evidence.evidence_id == "MIROFISH-EOD-20260516"
        assert "无高重要度事件" in evidence.content

    def test_build_eod_evidence_aggregates_stocks(self) -> None:
        e1 = EventDescription(
            title="a", content="x", importance_score=9, stocks=("600519",)
        )
        e2 = EventDescription(
            title="b",
            content="x",
            importance_score=8,
            stocks=("000333", "600519"),
        )
        evidence = build_eod_evidence(
            events=(e1, e2), trade_date="2026-05-16"
        )
        assert set(evidence.stock_codes) == {"600519", "000333"}


# ---------------------------------------------------------------------------
# Writer tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def mongo() -> _FakeMongo:
    return _FakeMongo()


@pytest.fixture()
def writer(mongo: _FakeMongo) -> MiroFishEvidenceWriter:
    return MiroFishEvidenceWriter(mongo)  # type: ignore[arg-type]


class TestWriterPrefixGuard:
    @pytest.mark.asyncio
    async def test_rejects_non_mirofish_prefix(
        self, writer: MiroFishEvidenceWriter
    ) -> None:
        # Sneak a valid-but-wrong-prefix id past the builders.
        evidence = MiroFishEvidence(
            evidence_id="NEWS-2026-05-16-001",
            path="event_driven",
            severity=9,
            content="x",
            trade_date="2026-05-16",
            created_at=datetime(2026, 5, 16, 9, 0, tzinfo=UTC),
        )
        with pytest.raises(MiroFishEvidenceError) as exc_info:
            await writer.write(evidence)
        assert exc_info.value.reason == "prefix_violation"

    @pytest.mark.asyncio
    async def test_rejects_invalid_evidence_id_format(
        self, writer: MiroFishEvidenceWriter
    ) -> None:
        evidence = MiroFishEvidence(
            evidence_id="MIROFISH-",  # too short to satisfy regex
            path="event_driven",
            severity=9,
            content="x",
            trade_date="2026-05-16",
            created_at=datetime(2026, 5, 16, 9, 0, tzinfo=UTC),
        )
        with pytest.raises(MiroFishEvidenceError) as exc_info:
            await writer.write(evidence)
        assert exc_info.value.reason == "evidence_id_invalid"


class TestEventDrivenCap:
    @pytest.mark.asyncio
    async def test_first_write_succeeds(
        self,
        writer: MiroFishEvidenceWriter,
        mongo: _FakeMongo,
    ) -> None:
        event = EventDescription(
            title="t", content="c", importance_score=9
        )
        evidence = build_event_evidence(
            event=event, trade_date="2026-05-16"
        )
        ok = await writer.write(evidence)
        assert ok is True
        assert len(mongo._db["evidence_collection"].docs) == 1

    @pytest.mark.asyncio
    async def test_second_event_driven_write_blocked(
        self,
        writer: MiroFishEvidenceWriter,
        mongo: _FakeMongo,
    ) -> None:
        event_a = EventDescription(title="a", content="c", importance_score=9)
        event_b = EventDescription(title="b", content="c", importance_score=8)
        first = build_event_evidence(
            event=event_a, trade_date="2026-05-16", seq=1
        )
        second = build_event_evidence(
            event=event_b, trade_date="2026-05-16", seq=2
        )
        assert await writer.write(first) is True
        with pytest.raises(MiroFishEvidenceError) as exc_info:
            await writer.write(second)
        assert exc_info.value.reason == "daily_cap_reached"
        # Second write must not have hit Mongo.
        assert len(mongo._db["evidence_collection"].docs) == 1

    @pytest.mark.asyncio
    async def test_cap_is_per_trade_date(
        self,
        writer: MiroFishEvidenceWriter,
        mongo: _FakeMongo,
    ) -> None:
        event = EventDescription(
            title="t", content="c", importance_score=9
        )
        first = build_event_evidence(
            event=event, trade_date="2026-05-16"
        )
        second = build_event_evidence(
            event=event, trade_date="2026-05-17"
        )
        assert await writer.write(first) is True
        # New trade_date — cap resets.
        assert await writer.write(second) is True
        assert len(mongo._db["evidence_collection"].docs) == 2

    @pytest.mark.asyncio
    async def test_constants_locked(self) -> None:
        assert EVENT_DRIVEN_DAILY_CAP == 1
        assert HIGH_SEVERITY_THRESHOLD == 8


class TestCapIndexFailClosed:
    """codex cycle 3 P2: when the partial unique cap index is missing
    the writer must refuse event_driven writes rather than silently
    fall back to non-atomic count_documents."""

    @pytest.mark.asyncio
    async def test_event_driven_refused_when_cap_index_missing(
        self, mongo: _FakeMongo
    ) -> None:
        mongo.evidence_event_cap_index_ok = False
        writer = MiroFishEvidenceWriter(mongo)  # type: ignore[arg-type]
        event = EventDescription(
            title="t", content="c", importance_score=9
        )
        evidence = build_event_evidence(
            event=event, trade_date="2026-05-16"
        )
        with pytest.raises(MiroFishEvidenceError) as exc_info:
            await writer.write(evidence)
        assert exc_info.value.reason == "cap_index_missing"
        assert len(mongo._db["evidence_collection"].docs) == 0

    @pytest.mark.asyncio
    async def test_eod_path_unaffected_by_missing_cap_index(
        self, mongo: _FakeMongo
    ) -> None:
        """EOD path is uncapped, so it stays available even if the cap
        index is missing — only event_driven is refused."""
        mongo.evidence_event_cap_index_ok = False
        writer = MiroFishEvidenceWriter(mongo)  # type: ignore[arg-type]
        eod = build_eod_evidence(events=(), trade_date="2026-05-16")
        assert await writer.write(eod) is True


class TestEventDrivenCapRace:
    """codex cycle 2 P2: a duplicate-key error from the partial unique
    index on (trade_date, path='event_driven') must surface as the cap
    rejection, not a generic insert failure."""

    @pytest.mark.asyncio
    async def test_duplicate_key_error_surfaces_as_cap_reached(
        self,
        writer: MiroFishEvidenceWriter,
        mongo: _FakeMongo,
    ) -> None:
        # Bypass the pre-check by zeroing the count (no rows yet) and
        # patching insert_one to raise a duplicate-key-style error —
        # mimics two concurrent writers racing past the count check.
        event = EventDescription(
            title="t", content="c", importance_score=9
        )
        evidence = build_event_evidence(
            event=event, trade_date="2026-05-16"
        )
        coll = mongo._db[MiroFishEvidenceWriter.COLLECTION_NAME]

        class _DupKeyError(RuntimeError):
            code = 11000

        async def _raise(_doc: dict) -> None:
            raise _DupKeyError("E11000 duplicate key on partial index")

        coll.insert_one = _raise  # type: ignore[assignment]

        with pytest.raises(MiroFishEvidenceError) as exc_info:
            await writer.write(evidence)
        assert exc_info.value.reason == "daily_cap_reached"

    @pytest.mark.asyncio
    async def test_non_duplicate_insert_failure_returns_false(
        self,
        writer: MiroFishEvidenceWriter,
        mongo: _FakeMongo,
    ) -> None:
        """Non-duplicate insert failures stay as a soft False, not a cap.

        This keeps cap-rejection observably distinct from infra glitches
        in the audit log.
        """
        event = EventDescription(
            title="t", content="c", importance_score=9
        )
        evidence = build_event_evidence(
            event=event, trade_date="2026-05-16"
        )
        coll = mongo._db[MiroFishEvidenceWriter.COLLECTION_NAME]

        async def _raise(_doc: dict) -> None:
            raise RuntimeError("transient mongo blip")

        coll.insert_one = _raise  # type: ignore[assignment]

        ok = await writer.write(evidence)
        assert ok is False


class TestEodPath:
    @pytest.mark.asyncio
    async def test_eod_write_uncapped(
        self,
        writer: MiroFishEvidenceWriter,
        mongo: _FakeMongo,
    ) -> None:
        # Saturate the event-driven cap first…
        event = EventDescription(
            title="t", content="c", importance_score=9
        )
        await writer.write(
            build_event_evidence(event=event, trade_date="2026-05-16")
        )
        # …EOD still goes through (different path).
        eod = build_eod_evidence(events=(), trade_date="2026-05-16")
        assert await writer.write(eod) is True
        assert len(mongo._db["evidence_collection"].docs) == 2

    @pytest.mark.asyncio
    async def test_eod_double_write_blocked_by_unique_index(
        self, writer: MiroFishEvidenceWriter
    ) -> None:
        eod = build_eod_evidence(events=(), trade_date="2026-05-16")
        assert await writer.write(eod) is True
        # The fake collection mimics the unique index on evidence_id;
        # the second insert returns False (write rejected, no raise).
        again = build_eod_evidence(events=(), trade_date="2026-05-16")
        assert await writer.write(again) is False


class TestPersistedShape:
    @pytest.mark.asyncio
    async def test_persisted_row_carries_path_and_prefix(
        self,
        writer: MiroFishEvidenceWriter,
        mongo: _FakeMongo,
    ) -> None:
        event = EventDescription(
            title="降准", content="央行降准", importance_score=9
        )
        evidence = build_event_evidence(
            event=event, trade_date="2026-05-16"
        )
        await writer.write(evidence)
        doc = mongo._db["evidence_collection"].docs[0]
        assert doc["prefix"] == EvidencePrefix.MIROFISH.value
        assert doc["path"] == "event_driven"
        assert doc["evidence_id"].startswith("MIROFISH-EVENT-")
        # No decision fields written (P0-10 LLM negative list / P0-8
        # §1.6.4 — output never enters RiskCheckSummary).
        forbidden = {
            "risk_summary",
            "risk_check_summary",
            "status",
            "volume",
            "limit_price",
        }
        assert forbidden.isdisjoint(doc.keys())


class TestWriterIsolation:
    """Writer must not import the agents / llm / risk / data layers."""

    def test_writer_module_has_no_forbidden_imports(self) -> None:
        import ast
        from pathlib import Path

        tree = ast.parse(Path("backend/mirofish/output_writer.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                parts = mod.split(".")
                if parts[:2] in (
                    ["backend", "llm"],
                    ["backend", "agents"],
                    ["backend", "risk"],
                ):
                    pytest.fail(
                        f"backend/mirofish/output_writer.py imports {mod}"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[:2] in (
                        ["backend", "llm"],
                        ["backend", "agents"],
                        ["backend", "risk"],
                    ):
                        pytest.fail(
                            f"backend/mirofish/output_writer.py imports "
                            f"{alias.name}"
                        )
