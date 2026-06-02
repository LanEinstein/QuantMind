"""W-002 — Line2ThesisReviewRunner (advisory loop, evidence-only, isolation)."""

from __future__ import annotations

import ast
import datetime
import pathlib
from dataclasses import dataclass

import pytest

from backend.models.position_thesis import ThesisHealth
from backend.orchestration.line2_thesis_review_runner import Line2ThesisReviewRunner
from backend.position_thesis.derivation import build_position_thesis
from backend.services.thesis_advisory import (
    ThesisAdvisoryVerdict,
    build_thesis_review_evidence,
)

_NOW = datetime.datetime(2026, 6, 2, 17, 30, tzinfo=datetime.UTC)


def _thesis(code: str):
    return build_position_thesis(
        instruction_id=f"QM-20260601-093500-{code}-BUY-001",
        signal_id="SIG-1",
        stock_code=code,
        stock_name="标的",
        created_at=_NOW,
        trade_date="2026-06-01",
        pillars=("a", "b", "c"),
        entry_price=10.0,
        entry_score=2.0,
        snapshot_id="snap-1",
    )


@dataclass
class _Provider:
    held: frozenset[str]
    theses: dict
    contexts: dict

    @property
    def held_codes(self) -> frozenset[str]:
        return self.held

    def open_theses(self):  # noqa: ANN201
        return self.theses

    def evidence_context_for(self, code: str) -> str:
        return self.contexts.get(code, "")


class _Client:
    """Returns a verdict for every code except those in ``skip``."""

    def __init__(self, *, health=ThesisHealth.BROKEN, skip=frozenset()) -> None:
        self.health = health
        self.skip = skip
        self.reviewed: list[str] = []

    async def review(self, thesis, evidence_context, *, now):  # noqa: ANN001, ANN201
        self.reviewed.append(thesis.stock_code)
        if thesis.stock_code in self.skip:
            return None
        return ThesisAdvisoryVerdict(
            code=thesis.stock_code,
            instruction_id=thesis.instruction_id,
            health=self.health,
            reason_text="reason",
            evidence_id=f"DEBATE-thesis-20260602-{thesis.stock_code}",
            trade_date="2026-06-02",
        )


class _Writer:
    def __init__(self) -> None:
        self.written: list = []

    async def write(self, evidence) -> bool:  # noqa: ANN001
        self.written.append(evidence)
        return True


class TestRun:
    @pytest.mark.asyncio
    async def test_reviews_held_theses_and_writes_evidence(self) -> None:
        provider = _Provider(
            held=frozenset({"600519", "000001"}),
            theses={"600519": _thesis("600519"), "000001": _thesis("000001")},
            contexts={"600519": "ctx1", "000001": "ctx2"},
        )
        client = _Client()
        writer = _Writer()
        runner = Line2ThesisReviewRunner(client=client, evidence_writer=writer)
        result = await runner.run(provider=provider, now=_NOW)
        assert result.reviewed == 2
        assert len(result.verdicts) == 2
        assert len(writer.written) == 2
        assert {ev.evidence_id for ev in writer.written} == {
            "DEBATE-thesis-20260602-600519",
            "DEBATE-thesis-20260602-000001",
        }

    @pytest.mark.asyncio
    async def test_skips_thesis_for_non_held_position(self) -> None:
        # A thesis exists for 000001 but the position is no longer held → skip.
        provider = _Provider(
            held=frozenset({"600519"}),
            theses={"600519": _thesis("600519"), "000001": _thesis("000001")},
            contexts={},
        )
        client = _Client()
        runner = Line2ThesisReviewRunner(client=client)
        result = await runner.run(provider=provider, now=_NOW)
        assert client.reviewed == ["600519"]  # 000001 never reviewed
        assert result.reviewed == 1

    @pytest.mark.asyncio
    async def test_gated_skip_writes_no_evidence(self) -> None:
        provider = _Provider(
            held=frozenset({"600519"}),
            theses={"600519": _thesis("600519")},
            contexts={},
        )
        client = _Client(skip=frozenset({"600519"}))  # budget/dedup skip → None
        writer = _Writer()
        runner = Line2ThesisReviewRunner(client=client, evidence_writer=writer)
        result = await runner.run(provider=provider, now=_NOW)
        assert result.reviewed == 0
        assert result.skipped_codes == ("600519",)
        assert writer.written == []

    @pytest.mark.asyncio
    async def test_evidence_is_debate_prefixed_and_decisionless(self) -> None:
        verdict = ThesisAdvisoryVerdict(
            code="600519",
            instruction_id="QM-20260601-093500-600519-BUY-001",
            health=ThesisHealth.WEAKENING,
            reason_text="r",
            evidence_id="DEBATE-thesis-20260602-600519",
            trade_date="2026-06-02",
        )
        mongo = build_thesis_review_evidence(verdict).to_mongo()
        assert mongo["prefix"] == "DEBATE"
        for forbidden in ("side", "volume", "limit_price", "risk_summary", "status"):
            assert forbidden not in mongo


def test_runner_import_clean() -> None:
    """The runner must not import backend.{llm,agents,monitoring,broker,risk}."""
    src = pathlib.Path(
        "backend/orchestration/line2_thesis_review_runner.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {
        "llm", "agents", "agents_team", "mirofish", "monitoring",
        "broker", "risk", "data", "api",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if len(parts) >= 2 and parts[0] == "backend" and parts[1] in banned:
                raise AssertionError(
                    f"thesis_review_runner imports forbidden backend.{parts[1]}"
                )
