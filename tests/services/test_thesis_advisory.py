"""W-002 — thesis_advisory service (parse + evidence + cost-gated reviewer)."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from backend.models.position_thesis import ThesisHealth
from backend.position_thesis.derivation import build_position_thesis
from backend.services import cost_guard
from backend.services.thesis_advisory import (
    ThesisAdvisoryReviewer,
    ThesisAdvisoryVerdict,
    ThesisReviewEvidence,
    build_thesis_review_evidence,
    make_thesis_review_evidence_id,
    parse_advisory_health,
)

_NOW = datetime.datetime(2026, 6, 2, 17, 30, tzinfo=datetime.UTC)


def _thesis(code: str = "600519"):
    return build_position_thesis(
        instruction_id=f"QM-20260601-093500-{code}-BUY-001",
        signal_id="SIG-1",
        stock_code=code,
        stock_name="贵州茅台",
        created_at=_NOW,
        trade_date="2026-06-01",
        pillars=("龙头护城河", "估值合理", "动量确认"),
        entry_price=10.0,
        entry_score=2.0,
        snapshot_id="snap-1",
    )


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, float] = {}
        self.sets: dict[str, set[str]] = {}

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.store[key] = float(self.store.get(key, 0.0)) + float(amount)
        return self.store[key]

    async def incr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) + 1
        return int(self.store[key])

    async def decr(self, key: str) -> int:
        self.store[key] = int(self.store.get(key, 0)) - 1
        return int(self.store[key])

    async def sadd(self, key: str, member: str) -> int:
        s = self.sets.setdefault(key, set())
        if member in s:
            return 0
        s.add(member)
        return 1

    async def srem(self, key: str, member: str) -> int:
        self.sets.get(key, set()).discard(member)
        return 1

    async def get(self, key: str):  # noqa: ANN201
        v = self.store.get(key)
        return None if v is None else str(v)

    async def expire(self, key: str, ttl: int) -> bool:
        return True


class _FakeRouter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[tuple[str, list]] = []

    async def complete(self, agent_name: str, messages: list) -> object:
        self.calls.append((agent_name, messages))
        msg = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


@pytest.fixture(autouse=True)
def _zero_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _spent(_redis, *, today=None):  # noqa: ANN001
        return 0.0

    monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)


class TestParse:
    @pytest.mark.unit
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("THESIS_BROKEN 供应过剩,逻辑破坏", ThesisHealth.BROKEN),
            ("THESIS_WEAKENING 增速放缓", ThesisHealth.WEAKENING),
            ("THESIS_INTACT 一切如常", ThesisHealth.INTACT),
            ("无标签的随便文本", ThesisHealth.WEAKENING),  # fail-safe default
        ],
    )
    def test_parse_health(self, text: str, expected: ThesisHealth) -> None:
        health, reason = parse_advisory_health(text)
        assert health is expected
        assert "THESIS_" not in reason.upper()  # label stripped from reason

    @pytest.mark.unit
    def test_empty_text_defaults_weakening(self) -> None:
        health, reason = parse_advisory_health("")
        assert health is ThesisHealth.WEAKENING
        assert reason

    @pytest.mark.unit
    def test_leading_token_wins_over_mentioned_token(self) -> None:
        # codex W-002 P3: a rationale that merely MENTIONS another label must not
        # flip the verdict — the leading (earliest) token is authoritative.
        health, reason = parse_advisory_health(
            "THESIS_INTACT 当前估值与动量仍支撑,尚未达到 THESIS_BROKEN 的程度"
        )
        assert health is ThesisHealth.INTACT
        assert "THESIS_" not in reason.upper()


class TestEvidence:
    @pytest.mark.unit
    def test_evidence_id_uses_debate_prefix(self) -> None:
        eid = make_thesis_review_evidence_id("600519", "2026-06-02")
        assert eid == "DEBATE-thesis-20260602-600519"

    @pytest.mark.unit
    def test_build_evidence_no_decision_fields(self) -> None:
        verdict = ThesisAdvisoryVerdict(
            code="600519",
            instruction_id="QM-20260601-093500-600519-BUY-001",
            health=ThesisHealth.BROKEN,
            reason_text="逻辑破坏",
            evidence_id="DEBATE-thesis-20260602-600519",
            trade_date="2026-06-02",
        )
        ev = build_thesis_review_evidence(verdict)
        assert isinstance(ev, ThesisReviewEvidence)
        mongo = ev.to_mongo()
        assert mongo["prefix"] == "DEBATE"
        # Evidence-only: no order/decision field anywhere in the document.
        for forbidden in ("side", "volume", "limit_price", "risk_summary"):
            assert forbidden not in mongo


class TestReviewer:
    @pytest.mark.asyncio
    async def test_review_returns_evidence_only_verdict(self) -> None:
        redis = _FakeRedis()
        router = _FakeRouter("THESIS_BROKEN 主业受供应链冲击")
        reviewer = ThesisAdvisoryReviewer(router=router, redis_client=redis)
        verdict = await reviewer.review(_thesis(), "近期供应链负面", now=_NOW)
        assert verdict is not None
        assert verdict.health is ThesisHealth.BROKEN
        assert verdict.evidence_id == "DEBATE-thesis-20260602-600519"
        # The LLM call was budget-reserved on the unified counter then settled.
        assert "llm:usage:2026-06-02:reserved" in redis.store
        # Verdict has NO order field (dataclass fields only).
        assert not hasattr(verdict, "volume")
        assert not hasattr(verdict, "limit_price")

    @pytest.mark.asyncio
    async def test_review_skips_when_deduped(self) -> None:
        redis = _FakeRedis()
        router = _FakeRouter("THESIS_INTACT ok")
        reviewer = ThesisAdvisoryReviewer(router=router, redis_client=redis)
        first = await reviewer.review(_thesis(), "ctx", now=_NOW)
        second = await reviewer.review(_thesis(), "ctx", now=_NOW)
        assert first is not None
        assert second is None  # deduped — no second LLM call
        assert len(router.calls) == 1

    @pytest.mark.asyncio
    async def test_review_skips_on_budget_exhausted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _spent(_redis, *, today=None):  # noqa: ANN001
            return 100.0  # at the ¥100 hard cap

        monkeypatch.setattr(cost_guard, "get_daily_spent", _spent)
        redis = _FakeRedis()
        router = _FakeRouter("THESIS_BROKEN x")
        reviewer = ThesisAdvisoryReviewer(router=router, redis_client=redis)
        verdict = await reviewer.review(_thesis(), "ctx", now=_NOW)
        assert verdict is None
        assert router.calls == []  # the LLM never fired

    @pytest.mark.asyncio
    async def test_review_llm_failure_returns_none(self) -> None:
        class _BoomRouter:
            async def complete(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
                raise RuntimeError("provider down")

        redis = _FakeRedis()
        reviewer = ThesisAdvisoryReviewer(router=_BoomRouter(), redis_client=redis)
        verdict = await reviewer.review(_thesis(), "ctx", now=_NOW)
        assert verdict is None  # advisory never crashes the run
