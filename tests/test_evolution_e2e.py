"""X-020 — Phase X end-to-end evolution_shadow_run integration.

Wires the full P2-2 self-evolution chain end to end so the 22:00
``evolution_shadow_run`` cron path is covered without needing a real
BrokerScheduler tick:

    GEPA challenger prompt
        → ShadowChain (45-day replay + bootstrap CI + verdict)
        → AmendmentDrafter (4 R7 sections + length flag)
        → EvolutionFeishuNotifier (mock OpenAPI)
        → EvolutionAuditWriter (7 Category-5 events)

Five paths covered (the X-020 acceptance):

1. **Happy path** — passing verdict → draft on disk + notify + 3 audit rows.
2. **cost_guard budget breach** — GEPA refused under daily ¥20 hard cap.
3. **RAG non-whitelist rejection** — ingester records rejection audit row.
4. **R1 sample limit breach** — GEPA refuses over-100 examples.
5. **R3 retrieval-precision floor breach** — assert_precision_floor raises.

Mock Feishu boundaries (no real OpenAPI call) and an in-memory audit
collection so the test stays deterministic. ``redis_client`` is
monkey-patched via the same ``backend.services.cost_guard`` probes the
X-017 integration test uses.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from backend.audit.store import AuditStore, InMemoryAuditCollection, read_jsonl
from backend.evolution.provenance.writer import ProvenanceWriter
from backend.evolution.rag_ingester import (
    RagIngester,
    RetrievalPrecisionTooLowError,
    assert_precision_floor,
)
from backend.integrations.feishu.alerter import FeishuAlerter
from backend.integrations.feishu.client import SendMessageResult
from backend.integrations.feishu.renderer import MessageRenderer
from backend.services import cost_guard as cg
from backend.services.amendment_drafter import AmendmentDrafter
from backend.services.dspy_gepa_runner import (
    GEPA_MAX_SAMPLES,
    DSPyGEPARunner,
    GEPASampleLimitExceededError,
    GEPATrainingExample,
)
from backend.services.evolution_audit_writer import EvolutionAuditWriter
from backend.services.evolution_dispatcher import (
    EvolutionDispatcher,
    PromptEvolutionTask,
)
from backend.services.evolution_feishu_notifier import EvolutionFeishuNotifier
from backend.services.shadow_chain import (
    ShadowAcceptanceReport,
    ShadowChain,
    make_acceptance_report,
)

# -----------------------------------------------------------------------------
# Test doubles — shared across the 5 paths
# -----------------------------------------------------------------------------


class _FakeFeishuClient:
    """Records every send_message + returns a deterministic message_id."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def send_message(
        self, chat_id: str, body: str, *, uuid: str
    ) -> SendMessageResult:
        self.sent.append((chat_id, body, uuid))
        return SendMessageResult(
            ok=True,
            code=0,
            msg="ok",
            message_id=f"mock-{len(self.sent):04d}",
            log_id=f"log-{uuid}",
        )


class _CannedReplayer:
    """Returns a passing OR failing 45-day challenger replay."""

    def __init__(self, *, passing: bool, base: dict[str, float]) -> None:
        self._passing = passing
        self._base = base

    def replay(
        self,
        *,
        as_of: dt.date,
        challenger_artifact_id: str,
    ) -> tuple[Any, Sequence[float]]:
        bump = 0.05 if self._passing else -0.05
        challenger_metrics = {
            "pnl_cny": self._base["pnl_cny"]
            + (100.0 if self._passing else -100.0),
            "csi300_excess_pct": self._base["csi300_excess_pct"] + bump,
            "max_drawdown_pct": (
                max(self._base["max_drawdown_pct"] - 0.01, 0.0)
                if self._passing
                else self._base["max_drawdown_pct"] + 0.02
            ),
            "execution_report_accuracy_rate": (
                min(self._base["execution_report_accuracy_rate"] + 0.005, 1.0)
                if self._passing
                else self._base["execution_report_accuracy_rate"] - 0.01
            ),
            "instruction_completion_rate": self._base["instruction_completion_rate"],
            "data_missing_rate": self._base["data_missing_rate"],
            "llm_timeout_rate": self._base["llm_timeout_rate"],
            "signal_generation_rate": self._base["signal_generation_rate"],
        }
        report = make_acceptance_report(metric_values=challenger_metrics)
        pnl_series = [
            self._base["pnl_cny"] / 45 + (1.0 if self._passing else -1.0)
            for _ in range(45)
        ]
        return report, pnl_series


class _StubCompiler:
    """Returns a fixed new prompt — challenger ≠ champion."""

    def __init__(self, *, new_prompt: str) -> None:
        self.new_prompt = new_prompt
        self.calls = 0

    async def compile(
        self,
        *,
        seed_prompt: str,
        examples: Sequence[GEPATrainingExample],
        reflection_lm: str,
        max_iterations: int,
    ) -> str:
        self.calls += 1
        return self.new_prompt


# -----------------------------------------------------------------------------
# Cost-guard probe stubs (parametrise per test via monkeypatch fixtures)
# -----------------------------------------------------------------------------


@pytest.fixture
def at_zero_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _zero(_client: object) -> float:
        return 0.0

    async def _zero_provider(_client: object, *, provider: str) -> float:
        return 0.0

    monkeypatch.setattr(cg, "get_daily_spent", _zero)
    monkeypatch.setattr(cg, "get_month_spent", _zero)
    monkeypatch.setattr(cg, "get_daily_spent_for_provider", _zero_provider)


@pytest.fixture
def at_hard_breach(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _over(_client: object) -> float:
        return 25.0

    async def _zero(_client: object) -> float:
        return 0.0

    async def _zero_provider(_client: object, *, provider: str) -> float:
        return 0.0

    monkeypatch.setattr(cg, "get_daily_spent", _over)
    monkeypatch.setattr(cg, "get_month_spent", _zero)
    monkeypatch.setattr(cg, "get_daily_spent_for_provider", _zero_provider)


class _StubRedis:
    """Sentinel value handed to the runner; probes are monkeypatched."""


# -----------------------------------------------------------------------------
# Common fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
def base_metrics() -> dict[str, float]:
    return {
        "pnl_cny": 1000.0,
        "csi300_excess_pct": 0.05,
        "max_drawdown_pct": 0.05,
        "execution_report_accuracy_rate": 0.99,
        "instruction_completion_rate": 0.96,
        "data_missing_rate": 0.005,
        "llm_timeout_rate": 0.04,
        "signal_generation_rate": 0.95,
    }


@pytest.fixture
def champion_report(base_metrics: dict[str, float]) -> ShadowAcceptanceReport:
    base = make_acceptance_report(metric_values=base_metrics)
    return ShadowAcceptanceReport(
        report_id=base.report_id,
        computed_at=base.computed_at,
        trade_date=base.trade_date,
        window_start=base.window_start,
        window_end=base.window_end,
        trading_days_in_window=base.trading_days_in_window,
        outcome=base.outcome,
        metrics=base.metrics,
        notes=base.notes,
        reset_state=base.reset_state,
        bootstrap_pnl_ci_95pct=(900.0, 1100.0),
        challenger_artifact_id="champion",
        champion_baseline_id="champion",
    )


def _build_dispatcher(
    *,
    base_metrics: dict[str, float],
    tmp_path: Path,
    passing: bool,
    new_prompt: str,
) -> tuple[
    EvolutionDispatcher,
    EvolutionAuditWriter,
    Path,
    _FakeFeishuClient,
]:
    audit_jsonl = tmp_path / "audit.jsonl"
    audit_writer = EvolutionAuditWriter(
        store=AuditStore(
            InMemoryAuditCollection(), jsonl_path=audit_jsonl
        )
    )
    drafter = AmendmentDrafter(
        audit=audit_writer, pending_dir=tmp_path / "pending"
    )
    fake_feishu = _FakeFeishuClient()
    alerter = FeishuAlerter(
        feishu=fake_feishu,  # type: ignore[arg-type]
        renderer=MessageRenderer(),
        alert_chat_id="oc_alert",
        decision_chat_id="oc_decision",
        dedup_window=timedelta(seconds=1),
    )
    notifier = EvolutionFeishuNotifier(
        alerter=alerter, renderer=MessageRenderer(), audit=audit_writer
    )
    chain = ShadowChain(
        replayer=_CannedReplayer(passing=passing, base=base_metrics)
    )
    gepa = DSPyGEPARunner(
        compiler=_StubCompiler(new_prompt=new_prompt),
        log_dir=tmp_path / "gepa",
    )
    dispatcher = EvolutionDispatcher(
        shadow_chain=chain,
        drafter=drafter,
        notifier=notifier,
        audit=audit_writer,
        gepa_runner=gepa,
    )
    return dispatcher, audit_writer, audit_jsonl, fake_feishu


# =============================================================================
# Path 1 — Happy path: complete chain → drafted_and_notified
# =============================================================================


class TestE2EHappyPath:
    @pytest.mark.asyncio
    async def test_22h_cron_full_chain(
        self,
        tmp_path: Path,
        base_metrics: dict[str, float],
        champion_report: ShadowAcceptanceReport,
        at_zero_spend: None,
    ) -> None:
        dispatcher, _, audit_jsonl, fake_feishu = _build_dispatcher(
            base_metrics=base_metrics,
            tmp_path=tmp_path,
            passing=True,
            new_prompt="evolved fundamental_analyst prompt body",
        )
        task = PromptEvolutionTask(
            agent="fundamental_analyst",
            seed_prompt="legacy fundamental_analyst prompt",
            examples=(
                GEPATrainingExample(
                    inputs={"market": "sh"}, outputs={"score": 0.7}
                ),
            ),
            champion_baseline_id="PROMPT-fundamental_analyst-v3",
            champion_body_length=len("legacy fundamental_analyst prompt"),
        )
        outcome = await dispatcher.run_prompt_evolution(
            task=task,
            champion_report=champion_report,
            as_of=dt.date(2026, 5, 18),
            correlation_id="corr-x-020-happy",
            redis_client=_StubRedis(),  # type: ignore[arg-type]
        )

        assert outcome.status == "drafted_and_notified"
        assert outcome.shadow_passed is True
        # 1) Amendment file landed on disk under pending/.
        assert outcome.draft_result is not None
        amendment_path = outcome.draft_result.amendment_path
        assert amendment_path.is_file()
        body = amendment_path.read_text(encoding="utf-8")
        for section in (
            "## diff",
            "## shadow evidence",
            "## readability check",
            "## rollback",
        ):
            assert section in body, (
                f"missing R7 section {section} in drafted body"
            )
        # 2) Mock Feishu OpenAPI captured exactly one outbound message.
        assert len(fake_feishu.sent) == 1
        chat_id, message_body, message_uuid = fake_feishu.sent[0]
        assert chat_id == "oc_alert"
        assert "fundamental_analyst" in message_body or "PROMPT-" in message_body
        assert message_uuid  # non-empty UUID
        # 3) Audit chain — 3 of the 7 Category-5 event types fired.
        events = read_jsonl(audit_jsonl)
        types = {e.event_type.value for e in events}
        assert "shadow_evolution_run_completed" in types
        assert "evolution_amendment_drafted" in types
        assert "evolution_feishu_notified" in types
        # 4) The reasoning_namespace stays in the "evolution" track.
        for ev in events:
            if ev.event_type.value.startswith("evolution_") or (
                ev.event_type.value in (
                    "shadow_evolution_run_completed",
                    "prompt_version_pinned",
                    "prompt_version_rolled_back",
                )
            ):
                assert ev.reason_namespace == "evolution"


# =============================================================================
# Path 2 — cost_guard budget breach: GEPA refuses, chain stops
# =============================================================================


class TestE2ECostGuardBudgetBreach:
    @pytest.mark.asyncio
    async def test_daily_breach_blocks_gepa(
        self,
        tmp_path: Path,
        base_metrics: dict[str, float],
        champion_report: ShadowAcceptanceReport,
        at_hard_breach: None,
    ) -> None:
        from backend.services.dspy_gepa_runner import GEPABudgetError

        dispatcher, _, audit_jsonl, fake_feishu = _build_dispatcher(
            base_metrics=base_metrics,
            tmp_path=tmp_path,
            passing=True,
            new_prompt="evolved prompt body",
        )
        task = PromptEvolutionTask(
            agent="fundamental_analyst",
            seed_prompt="legacy prompt",
            examples=(
                GEPATrainingExample(inputs={"x": 1}, outputs={"y": 2}),
            ),
            champion_baseline_id="PROMPT-fundamental_analyst-v3",
            champion_body_length=10,
        )
        with pytest.raises(GEPABudgetError):
            await dispatcher.run_prompt_evolution(
                task=task,
                champion_report=champion_report,
                as_of=dt.date(2026, 5, 18),
                correlation_id="corr-x-020-budget",
                redis_client=_StubRedis(),  # type: ignore[arg-type]
            )
        # No Feishu fired. No amendment drafted. No audit emissions.
        assert fake_feishu.sent == []
        events = read_jsonl(audit_jsonl)
        assert all(
            ev.event_type.value
            not in (
                "shadow_evolution_run_completed",
                "evolution_amendment_drafted",
                "evolution_feishu_notified",
            )
            for ev in events
        )


# =============================================================================
# Path 3 — RAG non-whitelist rejection emits audit row
# =============================================================================


class TestE2ERagNonWhitelistRejection:
    @pytest.mark.asyncio
    async def test_non_whitelisted_source_rejected(
        self, tmp_path: Path
    ) -> None:
        # Build an ingester with a stub-typed CrawledDocument from a
        # non-whitelisted source. The rejection path emits a
        # rag_document_rejected_non_whitelist audit row without
        # touching the filesystem rag tree.
        from dataclasses import replace
        from datetime import UTC, datetime

        from backend.evolution.rag_ingester import CrawledDocument

        audit_jsonl = tmp_path / "audit.jsonl"
        audit_writer = EvolutionAuditWriter(
            store=AuditStore(
                InMemoryAuditCollection(), jsonl_path=audit_jsonl
            )
        )
        rag_root = tmp_path / "rag"
        rag_root.mkdir(parents=True)
        provenance_path = rag_root / "provenance.jsonl"
        provenance_path.touch()
        writer = ProvenanceWriter(path=provenance_path)
        ingester = RagIngester(
            writer=writer,
            audit=audit_writer,
            rag_root=rag_root,
        )

        # Start from a valid arxiv document, then patch the ``source``
        # to something off-list. ``dataclasses.replace`` keeps the
        # frozen-dataclass invariant intact.
        good_doc = CrawledDocument(
            doc_id="ARXIV-2509.13196",
            source="arxiv",
            source_url="https://arxiv.org/abs/2509.13196",
            source_domain="arxiv.org",
            title="t",
            authors=("a",),
            published_at=datetime(2026, 5, 18, tzinfo=UTC),
            license="cc-by",
            external_id="2509.13196",
            raw_text="body",
        )
        bad_doc = replace(good_doc, source="x_fake_source")  # type: ignore[arg-type]

        result = await ingester.ingest(
            bad_doc, correlation_id="corr-x-020-rag"
        )
        assert result.accepted is False
        assert result.reason == "non_whitelisted_source"

        events = read_jsonl(audit_jsonl)
        types = {e.event_type.value for e in events}
        assert "rag_document_rejected_non_whitelist" in types
        # No on-disk payload written.
        # (rag_root/{source}/* should be empty.)
        for path in rag_root.rglob("*.md"):
            raise AssertionError(f"unexpected payload written: {path}")


# =============================================================================
# Path 4 — R1 sample-limit breach fail-closes GEPA
# =============================================================================


class TestE2ER1SampleLimitBreach:
    @pytest.mark.asyncio
    async def test_over_100_samples_rejected(
        self, tmp_path: Path
    ) -> None:
        runner = DSPyGEPARunner(
            compiler=_StubCompiler(new_prompt="x"),
            log_dir=tmp_path / "gepa",
        )
        too_many = tuple(
            GEPATrainingExample(inputs={"i": i}, outputs={"o": i})
            for i in range(GEPA_MAX_SAMPLES + 1)
        )
        with pytest.raises(GEPASampleLimitExceededError) as exc_info:
            await runner.run(
                agent="fund_manager",
                seed_prompt="seed",
                examples=too_many,
            )
        assert str(GEPA_MAX_SAMPLES) in str(exc_info.value)
        assert "R1" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_at_exactly_100_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Boundary: ``GEPA_MAX_SAMPLES`` itself is accepted — the
        # rejection clause is strict ``>``, not ``>=``.
        # Codex X-024 R1 claim 11: runner now requires redis_client +
        # budget guard; e2e boundary test stubs both to focus on the
        # sample-count rule.
        async def noop_budget(_client: object, *, agent_name: str) -> None:
            return None

        monkeypatch.setattr(
            "backend.services.dspy_gepa_runner.assert_budget_allows",
            noop_budget,
        )
        runner = DSPyGEPARunner(
            compiler=_StubCompiler(new_prompt="x"),
            log_dir=tmp_path / "gepa",
        )
        at_limit = tuple(
            GEPATrainingExample(inputs={"i": i}, outputs={"o": i})
            for i in range(GEPA_MAX_SAMPLES)
        )
        result = await runner.run(
            agent="fund_manager",
            seed_prompt="seed",
            examples=at_limit,
            redis_client=object(),  # type: ignore[arg-type]
        )
        assert result.samples_used == GEPA_MAX_SAMPLES


# =============================================================================
# Path 5 — R3 retrieval-precision floor breach fail-closes batch
# =============================================================================


class TestE2ER3PrecisionFloor:
    def test_precision_under_floor_fail_closes(self) -> None:
        with pytest.raises(RetrievalPrecisionTooLowError) as exc_info:
            assert_precision_floor(0.79)
        assert "0.79" in str(exc_info.value)

    def test_precision_at_floor_accepts(self) -> None:
        # 0.80 is exactly the floor; the implementation uses ``<`` so
        # the floor itself does NOT raise.
        assert_precision_floor(0.80)

    def test_precision_above_floor_accepts(self) -> None:
        assert_precision_floor(0.95)

    def test_negative_precision_rejected_as_bug(self) -> None:
        # ``-0.1`` is a caller bug (precision is in [0, 1]); the helper
        # raises so the caller fixes the upstream calculation.
        with pytest.raises(RetrievalPrecisionTooLowError, match="caller bug"):
            assert_precision_floor(-0.1)


# =============================================================================
# Path 6 — shadow chain fails: drafted=False + notifier never invoked
# =============================================================================


class TestE2EShadowFailShortCircuit:
    @pytest.mark.asyncio
    async def test_failing_replayer_skips_draft_and_notify(
        self,
        tmp_path: Path,
        base_metrics: dict[str, float],
        champion_report: ShadowAcceptanceReport,
        at_zero_spend: None,
    ) -> None:
        # Even though the GEPA + cost_guard layer passes, the
        # ShadowChain returns ``verdict.passed=False`` — the dispatcher
        # must short-circuit: emit shadow_evolution_run_completed audit
        # but NOT touch the drafter or notifier.
        dispatcher, _, audit_jsonl, fake_feishu = _build_dispatcher(
            base_metrics=base_metrics,
            tmp_path=tmp_path,
            passing=False,
            new_prompt="evolved-but-worse prompt body",
        )
        task = PromptEvolutionTask(
            agent="risk_officer",
            seed_prompt="legacy risk_officer prompt",
            examples=(
                GEPATrainingExample(inputs={"x": 1}, outputs={"y": 2}),
            ),
            champion_baseline_id="PROMPT-risk_officer-v2",
            champion_body_length=10,
        )
        outcome = await dispatcher.run_prompt_evolution(
            task=task,
            champion_report=champion_report,
            as_of=dt.date(2026, 5, 18),
            correlation_id="corr-x-020-shadowfail",
            redis_client=_StubRedis(),  # type: ignore[arg-type]
        )
        assert outcome.status == "shadow_failed"
        assert outcome.draft_result is None
        assert outcome.notify_result is None
        # Mock Feishu was never called.
        assert fake_feishu.sent == []
        events = read_jsonl(audit_jsonl)
        types = {e.event_type.value for e in events}
        assert "shadow_evolution_run_completed" in types
        assert "evolution_amendment_drafted" not in types
        assert "evolution_feishu_notified" not in types
