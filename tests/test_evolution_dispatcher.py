"""X-008 — EvolutionDispatcher unit tests.

Covers 4-lane routing, shadow-fail short-circuit, drafted-and-notified
happy path, missing-runner skipped_no_shadow guard, and the audit /
notify side-effects.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.evolution.crawlers import (
    ArxivCrawler,
)
from backend.evolution.frontier_crawler import FrontierCrawler
from backend.evolution.provenance.writer import ProvenanceWriter
from backend.evolution.rag_ingester import RagIngester
from backend.integrations.feishu.alerter import FeishuAlerter
from backend.integrations.feishu.client import SendMessageResult
from backend.integrations.feishu.renderer import MessageRenderer
from backend.models.risk_proposal import RiskParameterProposal
from backend.services.amendment_drafter import AmendmentDrafter, DiffBlock
from backend.services.dspy_gepa_runner import (
    DSPyGEPARunner,
    GEPATrainingExample,
)
from backend.services.evolution_audit_writer import EvolutionAuditWriter
from backend.services.evolution_dispatcher import (
    EvolutionDispatcher,
    PromptEvolutionTask,
    RiskProposalShadowTask,
)
from backend.services.evolution_feishu_notifier import EvolutionFeishuNotifier
from backend.services.shadow_chain import (
    ShadowAcceptanceReport,
    ShadowChain,
    make_acceptance_report,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeFeishu:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def send_message(self, chat_id: str, body: str, *, uuid: str):
        self.calls.append((chat_id, body, uuid))
        return SendMessageResult(
            ok=True, code=0, msg="ok",
            message_id="mid-1", log_id="lid-1",
        )


class CannedReplayer:
    """Returns a passing OR failing challenger report on demand."""

    def __init__(self, *, passing: bool, base_metric_values: dict) -> None:
        self._passing = passing
        self._base = base_metric_values

    def replay(self, *, as_of, challenger_artifact_id):
        bump = 0.05 if self._passing else -0.05
        challenger_metrics = {
            "pnl_cny": self._base["pnl_cny"] + (100.0 if self._passing else -100.0),
            "csi300_excess_pct": self._base["csi300_excess_pct"] + bump,
            "max_drawdown_pct": max(
                self._base["max_drawdown_pct"] - 0.01, 0.0
            )
            if self._passing
            else self._base["max_drawdown_pct"] + 0.02,
            "execution_report_accuracy_rate": min(
                self._base["execution_report_accuracy_rate"] + 0.005, 1.0
            )
            if self._passing
            else self._base["execution_report_accuracy_rate"] - 0.01,
            "instruction_completion_rate": self._base["instruction_completion_rate"],
            "data_missing_rate": self._base["data_missing_rate"],
            "llm_timeout_rate": self._base["llm_timeout_rate"],
            "signal_generation_rate": self._base["signal_generation_rate"],
        }
        rep = make_acceptance_report(metric_values=challenger_metrics)
        pnl_series = [
            self._base["pnl_cny"] / 45 + bump * 100 for _ in range(45)
        ]
        return rep, pnl_series


class StubCompiler:
    def __init__(self, *, new_prompt: str) -> None:
        self.new_prompt = new_prompt

    async def compile(
        self,
        *,
        seed_prompt: str,
        examples: Sequence[GEPATrainingExample],
        reflection_lm: str,
        max_iterations: int,
    ) -> str:
        return self.new_prompt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_budget(monkeypatch: pytest.MonkeyPatch) -> object:
    """Noop budget guard for dispatcher tests that don't exercise budget.

    Codex X-024 R1 claim 11: ``DSPyGEPARunner.run`` now fail-closes
    when ``redis_client`` is ``None``. Dispatcher tests that focus on
    shadow/draft/notify orchestration opt in via this fixture, which
    monkeypatches ``assert_budget_allows`` to a no-op coroutine and
    returns a sentinel object suitable as ``redis_client``.
    """

    async def noop(_client: object, *, agent_name: str) -> None:
        return None

    monkeypatch.setattr(
        "backend.services.dspy_gepa_runner.assert_budget_allows", noop
    )
    monkeypatch.setattr(
        "backend.evolution.frontier_crawler.assert_budget_allows", noop
    )
    return object()


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


@pytest.fixture
def alerter() -> FeishuAlerter:
    return FeishuAlerter(
        feishu=FakeFeishu(),  # type: ignore[arg-type]
        renderer=MessageRenderer(),
        alert_chat_id="oc_alert",
        decision_chat_id="oc_decision",
        dedup_window=timedelta(seconds=1),
    )


@pytest.fixture
def audit_writer(tmp_path: Path) -> EvolutionAuditWriter:
    return EvolutionAuditWriter(
        store=AuditStore(
            InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl"
        )
    )


@pytest.fixture
def drafter(
    audit_writer: EvolutionAuditWriter, tmp_path: Path
) -> AmendmentDrafter:
    return AmendmentDrafter(
        audit=audit_writer, pending_dir=tmp_path / "pending"
    )


@pytest.fixture
def notifier(
    alerter: FeishuAlerter,
    audit_writer: EvolutionAuditWriter,
) -> EvolutionFeishuNotifier:
    return EvolutionFeishuNotifier(
        alerter=alerter, renderer=MessageRenderer(), audit=audit_writer
    )


def _passing_dispatcher(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    *,
    gepa_new_prompt: str = "new evolved prompt body",
) -> EvolutionDispatcher:
    chain = ShadowChain(
        replayer=CannedReplayer(passing=True, base_metric_values=base_metrics)
    )
    gepa = DSPyGEPARunner(
        compiler=StubCompiler(new_prompt=gepa_new_prompt),
        log_dir=Path("/tmp/gepa-test-logs"),
    )
    return EvolutionDispatcher(
        shadow_chain=chain,
        drafter=drafter,
        notifier=notifier,
        audit=audit_writer,
        gepa_runner=gepa,
    )


def _failing_dispatcher(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    *,
    gepa_new_prompt: str = "different prompt",
) -> EvolutionDispatcher:
    chain = ShadowChain(
        replayer=CannedReplayer(passing=False, base_metric_values=base_metrics)
    )
    gepa = DSPyGEPARunner(
        compiler=StubCompiler(new_prompt=gepa_new_prompt),
        log_dir=Path("/tmp/gepa-test-logs"),
    )
    return EvolutionDispatcher(
        shadow_chain=chain,
        drafter=drafter,
        notifier=notifier,
        audit=audit_writer,
        gepa_runner=gepa,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_lane_passing(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    champion_report: ShadowAcceptanceReport,
    tmp_path: Path,
    stub_budget: object,
) -> None:
    dispatcher = _passing_dispatcher(
        base_metrics, drafter, notifier, audit_writer
    )
    task = PromptEvolutionTask(
        agent="fund_manager",
        seed_prompt="old prompt",
        examples=(GEPATrainingExample(inputs={"a": 1}, outputs={"b": 2}),),
        champion_baseline_id="PROMPT-fund_manager-v3",
        champion_body_length=len("old prompt"),
    )
    outcome = await dispatcher.run_prompt_evolution(
        task=task,
        champion_report=champion_report,
        as_of=dt.date(2026, 5, 18),
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    assert outcome.status == "drafted_and_notified"
    assert outcome.shadow_passed is True
    assert outcome.draft_result is not None
    assert outcome.notify_result is not None
    assert outcome.notify_result.sent is True
    audit_jsonl = (tmp_path / "audit.jsonl").read_text()
    assert "shadow_evolution_run_completed" in audit_jsonl
    assert "evolution_amendment_drafted" in audit_jsonl
    assert "evolution_feishu_notified" in audit_jsonl


@pytest.mark.asyncio
async def test_prompt_lane_shadow_fails(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    champion_report: ShadowAcceptanceReport,
    tmp_path: Path,
    stub_budget: object,
) -> None:
    dispatcher = _failing_dispatcher(
        base_metrics, drafter, notifier, audit_writer
    )
    task = PromptEvolutionTask(
        agent="fund_manager",
        seed_prompt="old",
        examples=(),
        champion_baseline_id="PROMPT-fund_manager-v3",
        champion_body_length=3,
    )
    outcome = await dispatcher.run_prompt_evolution(
        task=task,
        champion_report=champion_report,
        as_of=dt.date(2026, 5, 18),
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    assert outcome.status == "shadow_failed"
    assert outcome.draft_result is None
    audit_jsonl = (tmp_path / "audit.jsonl").read_text()
    assert "shadow_evolution_run_completed" in audit_jsonl
    # No amendment drafted.
    assert "evolution_amendment_drafted" not in audit_jsonl


@pytest.mark.asyncio
async def test_prompt_lane_identical_prompt_ignored(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    champion_report: ShadowAcceptanceReport,
    stub_budget: object,
) -> None:
    dispatcher = _passing_dispatcher(
        base_metrics,
        drafter,
        notifier,
        audit_writer,
        gepa_new_prompt="seed identical",
    )
    task = PromptEvolutionTask(
        agent="fund_manager",
        seed_prompt="seed identical",
        examples=(),
        champion_baseline_id="PROMPT-fund_manager-v3",
        champion_body_length=14,
    )
    outcome = await dispatcher.run_prompt_evolution(
        task=task,
        champion_report=champion_report,
        as_of=dt.date(2026, 5, 18),
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    assert outcome.status == "ignored"
    assert outcome.gepa_result is not None
    assert outcome.draft_result is None


@pytest.mark.asyncio
async def test_prompt_lane_no_gepa_skipped(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    champion_report: ShadowAcceptanceReport,
) -> None:
    chain = ShadowChain(
        replayer=CannedReplayer(passing=True, base_metric_values=base_metrics)
    )
    dispatcher = EvolutionDispatcher(
        shadow_chain=chain,
        drafter=drafter,
        notifier=notifier,
        audit=audit_writer,
        gepa_runner=None,
    )
    task = PromptEvolutionTask(
        agent="fund_manager",
        seed_prompt="seed",
        examples=(),
        champion_baseline_id="PROMPT-fund_manager-v3",
        champion_body_length=4,
    )
    outcome = await dispatcher.run_prompt_evolution(
        task=task, champion_report=champion_report, as_of=dt.date(2026, 5, 18)
    )
    assert outcome.status == "skipped_no_shadow"


@pytest.mark.asyncio
async def test_rag_lane_uses_frontier_crawler(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    tmp_path: Path,
) -> None:
    rag_root = tmp_path / "rag"
    for source in (
        "arxiv", "semanticscholar", "openreview",
        "github_releases", "akshare",
    ):
        (rag_root / source).mkdir(parents=True)
    provenance = rag_root / "provenance.jsonl"
    provenance.touch()

    ingester = RagIngester(
        writer=ProvenanceWriter(path=provenance),
        audit=audit_writer,
        rag_root=rag_root,
    )

    sem = asyncio.Semaphore(2)

    async def fetch_arxiv(*, as_of):
        return [
            {
                "external_id": "2509.13196",
                "url": "https://arxiv.org/abs/2509.13196",
                "title": "T",
                "authors": ["A"],
                "published_at": datetime(2026, 5, 18, tzinfo=UTC),
                "body": "hello",
                "categories": ["cs.LG"],
            }
        ]

    crawler = ArxivCrawler(fetcher=fetch_arxiv, semaphore=sem)
    frontier = FrontierCrawler(crawlers=(crawler,), ingester=ingester)

    chain = ShadowChain(
        replayer=CannedReplayer(passing=True, base_metric_values=base_metrics)
    )
    dispatcher = EvolutionDispatcher(
        shadow_chain=chain,
        drafter=drafter,
        notifier=notifier,
        audit=audit_writer,
        frontier_crawler=frontier,
    )
    outcome = await dispatcher.run_rag_ingest()
    assert outcome.status == "ignored"
    assert outcome.crawl_result is not None
    assert outcome.crawl_result.ingested == 1


@pytest.mark.asyncio
async def test_rag_lane_no_crawler_skipped(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
) -> None:
    chain = ShadowChain(
        replayer=CannedReplayer(passing=True, base_metric_values=base_metrics)
    )
    dispatcher = EvolutionDispatcher(
        shadow_chain=chain,
        drafter=drafter,
        notifier=notifier,
        audit=audit_writer,
        frontier_crawler=None,
    )
    outcome = await dispatcher.run_rag_ingest()
    assert outcome.status == "skipped_no_shadow"


@pytest.mark.asyncio
async def test_risk_proposal_lane_passing(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    champion_report: ShadowAcceptanceReport,
) -> None:
    chain = ShadowChain(
        replayer=CannedReplayer(passing=True, base_metric_values=base_metrics)
    )
    dispatcher = EvolutionDispatcher(
        shadow_chain=chain,
        drafter=drafter,
        notifier=notifier,
        audit=audit_writer,
    )
    proposal = RiskParameterProposal(
        proposal_id="RPP-20260518-220000-000000-001",
        proposed_by="fund_manager",
        proposal_text="Tighten single-stock cap to 12%",
        target_field="PositionLimitsConfig.max_single_stock_pct",
        proposed_value=0.12,
        current_value=0.15,
        evidence_collection_ids=("RISK-001",),
        proposed_at=datetime(2026, 5, 18, 14, 0, tzinfo=UTC),
    )
    task = RiskProposalShadowTask(
        proposal=proposal,
        champion_baseline_id="RISK-CONFIG-v3",
        diff_label="risk config diff",
        diff_body="diff body text",
    )
    outcome = await dispatcher.run_risk_proposal_shadow_pass(
        task=task, champion_report=champion_report, as_of=dt.date(2026, 5, 18)
    )
    assert outcome.status == "drafted_and_notified"
    assert outcome.shadow_passed is True


@pytest.mark.asyncio
async def test_exemplar_lane_passing(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    champion_report: ShadowAcceptanceReport,
) -> None:
    chain = ShadowChain(
        replayer=CannedReplayer(passing=True, base_metric_values=base_metrics)
    )
    dispatcher = EvolutionDispatcher(
        shadow_chain=chain,
        drafter=drafter,
        notifier=notifier,
        audit=audit_writer,
    )
    outcome = await dispatcher.run_exemplar_schema_refresh(
        challenger_schema_id="EXEMPLAR-SCHEMA-v2",
        champion_baseline_id="EXEMPLAR-SCHEMA-v1",
        champion_report=champion_report,
        as_of=dt.date(2026, 5, 18),
        diff=DiffBlock(label="schema diff", body=""),
        champion_body_length=100,
        challenger_body_length=110,
    )
    assert outcome.status == "drafted_and_notified"


@pytest.mark.asyncio
async def test_metrics_summary_keys(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    champion_report: ShadowAcceptanceReport,
    tmp_path: Path,
    stub_budget: object,
) -> None:
    dispatcher = _passing_dispatcher(
        base_metrics, drafter, notifier, audit_writer
    )
    task = PromptEvolutionTask(
        agent="fund_manager",
        seed_prompt="old",
        examples=(),
        champion_baseline_id="PROMPT-fund_manager-v3",
        champion_body_length=3,
    )
    await dispatcher.run_prompt_evolution(
        task=task,
        champion_report=champion_report,
        as_of=dt.date(2026, 5, 18),
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    audit_text = (tmp_path / "audit.jsonl").read_text()
    # one of the strict-better metric deltas should land in the payload
    assert "pnl_cny__delta" in audit_text


@pytest.mark.asyncio
async def test_correlation_id_propagates(
    base_metrics: dict[str, float],
    drafter: AmendmentDrafter,
    notifier: EvolutionFeishuNotifier,
    audit_writer: EvolutionAuditWriter,
    champion_report: ShadowAcceptanceReport,
    tmp_path: Path,
    stub_budget: object,
) -> None:
    dispatcher = _passing_dispatcher(
        base_metrics, drafter, notifier, audit_writer
    )
    task = PromptEvolutionTask(
        agent="fund_manager",
        seed_prompt="old",
        examples=(),
        champion_baseline_id="PROMPT-fund_manager-v3",
        champion_body_length=3,
    )
    await dispatcher.run_prompt_evolution(
        task=task,
        champion_report=champion_report,
        as_of=dt.date(2026, 5, 18),
        correlation_id="corr-2026-05-18-22",
        redis_client=stub_budget,  # type: ignore[arg-type]
    )
    audit_text = (tmp_path / "audit.jsonl").read_text()
    assert "corr-2026-05-18-22" in audit_text
