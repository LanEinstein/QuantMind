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


class _FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send_message(self, chat_id: str, text: str, *, uuid: str):  # noqa: ANN201
        from types import SimpleNamespace

        self.calls.append((chat_id, text, uuid))
        return SimpleNamespace(ok=True, message_id="m1", code=None)


class TestDigest:
    def _provider(self) -> _Provider:
        return _Provider(
            held=frozenset({"600519"}),
            theses={"600519": _thesis("600519")},
            contexts={},
        )

    def _runner(self, sender, outbox, *, writer=None) -> Line2ThesisReviewRunner:  # noqa: ANN001
        from backend.integrations.feishu.renderer import MessageRenderer

        return Line2ThesisReviewRunner(
            client=_Client(),
            # codex W-003 P2: a verdict only digests when its evidence persisted.
            evidence_writer=writer if writer is not None else _Writer(),
            renderer=MessageRenderer(),
            digest_sender=sender,
            digest_chat_id="oc_decision",
            digest_outbox=outbox,
        )

    @pytest.mark.asyncio
    async def test_digest_sent_once_after_reviews(self) -> None:
        from backend.orchestration.instruction_dispatcher import (
            InMemoryOutboxRepository,
        )

        sender = _FakeSender()
        runner = self._runner(sender, InMemoryOutboxRepository())
        await runner.run(provider=self._provider(), now=_NOW)
        assert len(sender.calls) == 1
        assert "持仓复盘概览" in sender.calls[0][1]

    @pytest.mark.asyncio
    async def test_digest_idempotent_across_reruns(self) -> None:
        from backend.orchestration.instruction_dispatcher import (
            InMemoryOutboxRepository,
        )

        sender = _FakeSender()
        outbox = InMemoryOutboxRepository()
        runner = self._runner(sender, outbox)
        await runner.run(provider=self._provider(), now=_NOW)
        await runner.run(provider=self._provider(), now=_NOW)  # same trade_date
        assert len(sender.calls) == 1  # at-most-once

    @pytest.mark.asyncio
    async def test_digest_skipped_when_unwired(self) -> None:
        sender = _FakeSender()
        # No renderer / outbox / chat → digest silently skipped.
        runner = Line2ThesisReviewRunner(client=_Client(), digest_sender=sender)
        await runner.run(provider=self._provider(), now=_NOW)
        assert sender.calls == []

    @pytest.mark.asyncio
    async def test_digest_excludes_unpersisted_verdict(self) -> None:
        # codex W-003 P2: an evidence write FAILURE → the verdict has no durable
        # trail → it must NOT appear in (or, here, trigger) the digest.
        from backend.orchestration.instruction_dispatcher import (
            InMemoryOutboxRepository,
        )

        class _FailWriter:
            async def write(self, evidence) -> bool:  # noqa: ANN001
                return False  # persistence failed

        sender = _FakeSender()
        runner = self._runner(
            sender, InMemoryOutboxRepository(), writer=_FailWriter()
        )
        await runner.run(provider=self._provider(), now=_NOW)
        assert sender.calls == []  # nothing persisted → no digest

    @pytest.mark.asyncio
    async def test_digest_redacts_order_tokens_in_reason(self) -> None:
        # codex W-003 P2: an LLM reason echoing a QM- id / execution verb must be
        # redacted before it reaches the decision chat.
        from backend.orchestration.instruction_dispatcher import (
            InMemoryOutboxRepository,
        )

        class _EvilClient(_Client):
            async def review(self, thesis, evidence_context, *, now):  # noqa: ANN001, ANN201
                v = await super().review(thesis, evidence_context, now=now)
                return ThesisAdvisoryVerdict(
                    code=v.code,
                    instruction_id=v.instruction_id,
                    health=v.health,
                    reason_text="已执行 QM-20260601-093500-600519-BUY-001 卖出",
                    evidence_id=v.evidence_id,
                    trade_date=v.trade_date,
                )

        from backend.integrations.feishu.renderer import MessageRenderer

        sender = _FakeSender()
        runner = Line2ThesisReviewRunner(
            client=_EvilClient(),
            evidence_writer=_Writer(),
            renderer=MessageRenderer(),
            digest_sender=sender,
            digest_chat_id="oc_decision",
            digest_outbox=InMemoryOutboxRepository(),
        )
        await runner.run(provider=self._provider(), now=_NOW)
        assert len(sender.calls) == 1
        text = sender.calls[0][1]
        assert "QM-" not in text
        assert "已执行" not in text


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
