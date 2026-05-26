"""X-017 — cost_guard P2-2 integration tests.

This file is the **dedicated** assertion that every P2-2 self-evolution
LLM out-bound flows through :func:`backend.services.cost_guard.assert_budget_allows`
and inherits the daily hard cap (¥100 since P1-7-amendment-2026-05-26).

The 4 cost-guard constants locked by P1-7 §1.1-1.4. P1-7-amendment-2026-05-26
raised ONLY the daily hard cap ¥20 → ¥100 (the others never drift):

* ``_DEFAULT_DAILY_BUDGET_RMB`` = 100.0  (was 20.0, amendment 2026-05-26)
* ``_DEFAULT_SOFT_CEIL_PCT``    = 0.70
* ``_DEFAULT_MONTHLY_BUDGET_RMB`` = 440.0
* ``_DEFAULT_KIMI_DAILY_CAP_RMB`` = 4.0

P2-2 §2 red line 6 / X-009 / X-010 / X-013 rule: separate budget pools
are **forbidden**. The DSPy GEPA runner, the FrontierCrawler, and the
AmendmentDrafter each read the same Redis-backed daily counter so an
expensive self-evolution night cannot starve the daytime decision path.

Coverage:

* The 4 cost_guard constants are pinned to their P1-7 values.
* ``assert_budget_allows`` raises :class:`DailyBudgetExceededError` once
  Redis reports spend ≥ ¥20.
* DSPy GEPA runner wraps the runner-specific ``GEPABudgetError`` over
  ``DailyBudgetExceededError`` (no separate pool).
* AmendmentDrafter wraps ``AmendmentBudgetError`` over the same.
* FrontierCrawler records the budget breach as a crawler error AND keeps
  ingesting raw documents (codex review P2-3: summariser is a side
  channel, raw ingest must continue).
* The Kimi sub-cap (¥4) is a Kimi-only stop — DeepSeek/Qwen daily flows
  through ``assert_budget_allows`` and stays unaffected.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.evolution.crawlers.arxiv import ArxivCrawler
from backend.evolution.crawlers.base import RawRecord
from backend.evolution.provenance.writer import ProvenanceWriter
from backend.evolution.rag_ingester import RagIngester
from backend.services import cost_guard as cg
from backend.services.cost_guard import (
    DailyBudgetExceededError,
    KimiDailyCapExceededError,
    assert_budget_allows,
    assert_kimi_budget_allows,
)
from backend.services.evolution_audit_writer import EvolutionAuditWriter
from backend.services.shadow_chain import (
    ChallengerVerdict,
    MetricComparison,
    ShadowAcceptanceReport,
    make_acceptance_report,
)

# -----------------------------------------------------------------------------
# Stub Redis: cost_guard never mutates the Redis client; we only need
# something that is a *value* the helpers can be handed. The actual probe
# functions are monkeypatched below.
# -----------------------------------------------------------------------------


class _StubRedis:
    """Marker object — cost_probe is monkeypatched, so methods unused."""


@pytest.fixture
def stub_redis() -> _StubRedis:
    return _StubRedis()


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
    async def _over_daily(_client: object) -> float:
        return 125.0  # > ¥100 daily hard cap (P1-7-amendment-2026-05-26)

    async def _zero(_client: object) -> float:
        return 0.0

    async def _zero_provider(_client: object, *, provider: str) -> float:
        return 0.0

    monkeypatch.setattr(cg, "get_daily_spent", _over_daily)
    monkeypatch.setattr(cg, "get_month_spent", _zero)
    monkeypatch.setattr(cg, "get_daily_spent_for_provider", _zero_provider)


@pytest.fixture
def at_kimi_breach(monkeypatch: pytest.MonkeyPatch) -> None:
    """Daily total OK but Kimi-specific spend over ¥4."""

    async def _zero(_client: object) -> float:
        return 0.0

    async def _kimi_over(_client: object, *, provider: str) -> float:
        return 5.0 if provider == "kimi" else 0.0

    monkeypatch.setattr(cg, "get_daily_spent", _zero)
    monkeypatch.setattr(cg, "get_month_spent", _zero)
    monkeypatch.setattr(cg, "get_daily_spent_for_provider", _kimi_over)


# -----------------------------------------------------------------------------
# 4 locked P1-7 constants (CLAUDE.md §2.10)
# -----------------------------------------------------------------------------


class TestP17ConstantsLocked:
    def test_daily_budget_is_100(self) -> None:
        # P1-7-amendment-2026-05-26 raised the daily hard cap ¥20 → ¥100.
        assert cg._DEFAULT_DAILY_BUDGET_RMB == 100.0

    def test_soft_pct_is_0_70(self) -> None:
        assert cg._DEFAULT_SOFT_CEIL_PCT == 0.70

    def test_monthly_budget_is_440(self) -> None:
        assert cg._DEFAULT_MONTHLY_BUDGET_RMB == 440.0

    def test_kimi_daily_cap_is_4(self) -> None:
        assert cg._DEFAULT_KIMI_DAILY_CAP_RMB == 4.0

    def test_monthly_milestone_fractions_are_50_80_100(self) -> None:
        assert cg.MONTHLY_MILESTONE_FRACTIONS == (0.50, 0.80, 1.00)

    def test_kimi_provider_name_locked(self) -> None:
        assert cg.KIMI_PROVIDER_NAME == "kimi"


# -----------------------------------------------------------------------------
# assert_budget_allows behaviour
# -----------------------------------------------------------------------------


class TestAssertBudgetAllows:
    @pytest.mark.asyncio
    async def test_under_budget_returns_state(
        self, stub_redis: _StubRedis, at_zero_spend: None
    ) -> None:
        state = await assert_budget_allows(
            stub_redis,  # type: ignore[arg-type]
            agent_name="x_017_test",
        )
        assert state.status == "ok"
        assert state.daily_budget == 100.0

    @pytest.mark.asyncio
    async def test_over_budget_raises(
        self, stub_redis: _StubRedis, at_hard_breach: None
    ) -> None:
        with pytest.raises(DailyBudgetExceededError) as exc_info:
            await assert_budget_allows(
                stub_redis,  # type: ignore[arg-type]
                agent_name="x_017_test",
            )
        assert "Daily budget 100.00" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_kimi_under_returns_state(
        self, stub_redis: _StubRedis, at_zero_spend: None
    ) -> None:
        state = await assert_kimi_budget_allows(
            stub_redis,  # type: ignore[arg-type]
            agent_name="x_017_test",
        )
        assert state.status == "ok"
        assert state.kimi_daily_cap == 4.0

    @pytest.mark.asyncio
    async def test_kimi_breach_raises_kimi_specific(
        self, stub_redis: _StubRedis, at_kimi_breach: None
    ) -> None:
        # Kimi over its ¥4 cap; daily total still ¥0 -> only Kimi raises.
        with pytest.raises(KimiDailyCapExceededError):
            await assert_kimi_budget_allows(
                stub_redis,  # type: ignore[arg-type]
                agent_name="x_017_test",
            )
        # ... but the daily guard still allows because daily total = 0.
        state = await assert_budget_allows(
            stub_redis,  # type: ignore[arg-type]
            agent_name="x_017_test",
        )
        assert state.status == "ok"


# -----------------------------------------------------------------------------
# X-009 — DSPyGEPARunner integration
# -----------------------------------------------------------------------------


class _StubCompiler:
    """Records calls so the test asserts compile was/was not invoked."""

    def __init__(self) -> None:
        self.calls = 0

    async def compile(
        self,
        *,
        seed_prompt: str,
        examples: object,
        reflection_lm: str,
        max_iterations: int,
    ) -> str:
        self.calls += 1
        return "evolved-prompt"


@pytest.fixture
def gepa_runner(tmp_path: Path) -> tuple[object, _StubCompiler]:
    from backend.services.dspy_gepa_runner import DSPyGEPARunner

    compiler = _StubCompiler()
    runner = DSPyGEPARunner(compiler=compiler, log_dir=tmp_path / "gepa")
    return runner, compiler


class TestDSPyGEPABudgetIntegration:
    @pytest.mark.asyncio
    async def test_under_budget_compile_runs(
        self,
        gepa_runner: tuple[object, _StubCompiler],
        stub_redis: _StubRedis,
        at_zero_spend: None,
    ) -> None:
        from backend.services.dspy_gepa_runner import GEPATrainingExample

        runner, compiler = gepa_runner
        await runner.run(  # type: ignore[attr-defined]
            agent="fund_manager",
            seed_prompt="seed",
            examples=(
                GEPATrainingExample(inputs={"x": 1}, outputs={"y": 2}),
            ),
            redis_client=stub_redis,
        )
        assert compiler.calls == 1

    @pytest.mark.asyncio
    async def test_breach_raises_gepa_budget_error(
        self,
        gepa_runner: tuple[object, _StubCompiler],
        stub_redis: _StubRedis,
        at_hard_breach: None,
    ) -> None:
        from backend.services.dspy_gepa_runner import (
            GEPABudgetError,
            GEPATrainingExample,
        )

        runner, compiler = gepa_runner
        with pytest.raises(GEPABudgetError) as exc_info:
            await runner.run(  # type: ignore[attr-defined]
                agent="fund_manager",
                seed_prompt="seed",
                examples=(
                    GEPATrainingExample(inputs={"x": 1}, outputs={"y": 2}),
                ),
                redis_client=stub_redis,
            )
        # The original cause is the shared daily-budget error -> no
        # separate budget pool (P2-2 §2 red line 6).
        assert isinstance(exc_info.value.__cause__, DailyBudgetExceededError)
        # Compile was NOT invoked once the cost guard refused.
        assert compiler.calls == 0


# -----------------------------------------------------------------------------
# X-010 — FrontierCrawler integration (summariser side-channel)
# -----------------------------------------------------------------------------


def _arxiv_raw() -> RawRecord:
    return {
        "external_id": "2509.13196",
        "url": "https://arxiv.org/abs/2509.13196",
        "title": "Over-prompting dilemma",
        "authors": ["Jane Doe"],
        "published_at": datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        "body": "Body text here.",
        "categories": ["cs.LG"],
    }


async def _identity(records: Sequence[RawRecord]) -> Sequence[RawRecord]:
    return records


def _build_ingester(tmp_path: Path) -> RagIngester:
    rag_root = tmp_path / "rag"
    for source in (
        "arxiv",
        "semanticscholar",
        "openreview",
        "github_releases",
        "akshare",
    ):
        (rag_root / source).mkdir(parents=True)
    provenance = rag_root / "provenance.jsonl"
    provenance.touch()
    writer = ProvenanceWriter(path=provenance)
    audit = EvolutionAuditWriter(
        store=AuditStore(
            InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl"
        )
    )
    return RagIngester(writer=writer, audit=audit, rag_root=rag_root)


def _build_one_arxiv_crawler() -> ArxivCrawler:
    sem = asyncio.Semaphore(1)
    return ArxivCrawler(
        fetcher=lambda *, as_of: _identity([_arxiv_raw()]),  # noqa: ARG005
        semaphore=sem,
        rate_limit_sleep_sec=0.0,
    )


class TestFrontierCrawlerBudgetIntegration:
    @pytest.mark.asyncio
    async def test_under_budget_summariser_called(
        self,
        tmp_path: Path,
        stub_redis: _StubRedis,
        at_zero_spend: None,
    ) -> None:
        from backend.evolution.frontier_crawler import FrontierCrawler

        summariser_calls: list[object] = []

        async def fake_summariser(doc: object) -> str:
            summariser_calls.append(doc)
            return "summary"

        ingester = _build_ingester(tmp_path)
        frontier = FrontierCrawler(
            crawlers=(_build_one_arxiv_crawler(),),
            ingester=ingester,
            summariser=fake_summariser,
        )
        result = await frontier.run(redis_client=stub_redis)  # type: ignore[arg-type]
        assert result.fetched == 1
        assert result.ingested == 1
        assert len(summariser_calls) == 1
        assert result.crawler_errors == ()

    @pytest.mark.asyncio
    async def test_breach_skips_summariser_but_keeps_raw_ingest(
        self,
        tmp_path: Path,
        stub_redis: _StubRedis,
        at_hard_breach: None,
    ) -> None:
        # Codex review P2-3: budget breach must not block raw ingest.
        from backend.evolution.frontier_crawler import FrontierCrawler

        summariser_calls: list[object] = []

        async def fake_summariser(doc: object) -> str:
            summariser_calls.append(doc)
            return "summary"

        ingester = _build_ingester(tmp_path)
        frontier = FrontierCrawler(
            crawlers=(_build_one_arxiv_crawler(),),
            ingester=ingester,
            summariser=fake_summariser,
        )
        result = await frontier.run(redis_client=stub_redis)  # type: ignore[arg-type]
        # Raw ingestion still happens.
        assert result.fetched == 1
        assert result.ingested == 1
        # Summariser is skipped.
        assert summariser_calls == []
        # Crawler errors carry the cost_guard rejection so the operator
        # sees why summarisation stopped tonight.
        assert any("cost_guard" in err for err in result.crawler_errors)


# -----------------------------------------------------------------------------
# X-013 — AmendmentDrafter integration
# -----------------------------------------------------------------------------


def _verdict(*, passed: bool = True) -> ChallengerVerdict:
    strict = tuple(
        MetricComparison(
            name=name,
            rule="strict_better",
            direction="at_most" if name == "max_drawdown_pct" else "at_least",
            champion_value=0.5,
            challenger_value=0.6 if passed else 0.4,
            passed=passed,
            delta=0.1 if passed else -0.1,
        )
        for name in (
            "pnl_cny",
            "csi300_excess_pct",
            "max_drawdown_pct",
            "execution_report_accuracy_rate",
        )
    )
    no_regress = tuple(
        MetricComparison(
            name=name,
            rule="no_regression",
            direction="at_least",
            champion_value=0.95,
            challenger_value=0.96,
            passed=True,
            delta=0.01,
        )
        for name in (
            "instruction_completion_rate",
            "data_missing_rate",
            "llm_timeout_rate",
            "signal_generation_rate",
        )
    )
    return ChallengerVerdict(
        champion_passed_all_gates=True,
        challenger_passed_all_gates=passed,
        strict_better=strict,
        no_regression=no_regress,
    )


def _shadow_report() -> ShadowAcceptanceReport:
    base = make_acceptance_report(
        metric_values={
            "instruction_completion_rate": 0.96,
            "execution_report_accuracy_rate": 0.99,
            "data_missing_rate": 0.005,
            "llm_timeout_rate": 0.04,
            "signal_generation_rate": 0.95,
            "max_drawdown_pct": 0.06,
            "pnl_cny": 1234.0,
            "csi300_excess_pct": 0.01,
        }
    )
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
        bootstrap_pnl_ci_95pct=(123.4, 456.7),
        challenger_artifact_id="PROMPT-fund_manager-v4",
        champion_baseline_id="PROMPT-fund_manager-v3",
    )


@pytest.fixture
def drafter_with_audit(tmp_path: Path) -> object:
    from backend.services.amendment_drafter import AmendmentDrafter

    audit = EvolutionAuditWriter(
        store=AuditStore(
            InMemoryAuditCollection(),
            jsonl_path=tmp_path / "audit.jsonl",
        )
    )
    return AmendmentDrafter(audit=audit, pending_dir=tmp_path / "pending")


class TestAmendmentDrafterBudgetIntegration:
    @pytest.mark.asyncio
    async def test_under_budget_draft_written(
        self,
        drafter_with_audit: object,
        stub_redis: _StubRedis,
        at_zero_spend: None,
    ) -> None:
        from backend.services.amendment_drafter import DiffBlock

        result = await drafter_with_audit.draft(  # type: ignore[attr-defined]
            amendment_id="PROMPT-fund_manager-v4",
            artifact_type="prompt",
            artifact_id="PROMPT-fund_manager-v4",
            champion_baseline_id="PROMPT-fund_manager-v3",
            shadow_report=_shadow_report(),
            verdict=_verdict(),
            diff=DiffBlock(label="prompt diff", body="```diff\n+ new line\n```"),
            champion_body_length=1000,
            challenger_body_length=1100,
            redis_client=stub_redis,
        )
        assert result.amendment_path.is_file()

    @pytest.mark.asyncio
    async def test_breach_raises_amendment_budget_error(
        self,
        drafter_with_audit: object,
        stub_redis: _StubRedis,
        at_hard_breach: None,
    ) -> None:
        from backend.services.amendment_drafter import (
            AmendmentBudgetError,
            DiffBlock,
        )

        with pytest.raises(AmendmentBudgetError) as exc_info:
            await drafter_with_audit.draft(  # type: ignore[attr-defined]
                amendment_id="PROMPT-fund_manager-v5",
                artifact_type="prompt",
                artifact_id="PROMPT-fund_manager-v5",
                champion_baseline_id="PROMPT-fund_manager-v4",
                shadow_report=_shadow_report(),
                verdict=_verdict(),
                diff=DiffBlock(label="prompt diff", body="```diff\n+ x\n```"),
                champion_body_length=1000,
                challenger_body_length=1100,
                redis_client=stub_redis,
            )
        # Same shared DailyBudgetExceededError cause -> no separate pool.
        assert isinstance(exc_info.value.__cause__, DailyBudgetExceededError)


# -----------------------------------------------------------------------------
# 3-module unified pool — same Redis counter underlies all three callers
# -----------------------------------------------------------------------------


class TestUnifiedBudgetPool:
    """All three P2-2 modules read the same Redis daily counter."""

    @pytest.mark.asyncio
    async def test_single_breach_blocks_all_three(
        self,
        tmp_path: Path,
        stub_redis: _StubRedis,
        at_hard_breach: None,
    ) -> None:
        from backend.evolution.frontier_crawler import FrontierCrawler
        from backend.services.amendment_drafter import (
            AmendmentBudgetError,
            AmendmentDrafter,
            DiffBlock,
        )
        from backend.services.dspy_gepa_runner import (
            DSPyGEPARunner,
            GEPABudgetError,
            GEPATrainingExample,
        )

        # 1) GEPA runner refuses.
        compiler = _StubCompiler()
        runner = DSPyGEPARunner(compiler=compiler, log_dir=tmp_path / "gepa")
        with pytest.raises(GEPABudgetError):
            await runner.run(
                agent="fund_manager",
                seed_prompt="seed",
                examples=(GEPATrainingExample(inputs={}, outputs={}),),
                redis_client=stub_redis,  # type: ignore[arg-type]
            )

        # 2) FrontierCrawler skips summariser but still ingests.
        summariser_called: list[object] = []

        async def fake_summariser(doc: object) -> str:
            summariser_called.append(doc)
            return "summary"

        ingester = _build_ingester(tmp_path)
        frontier = FrontierCrawler(
            crawlers=(_build_one_arxiv_crawler(),),
            ingester=ingester,
            summariser=fake_summariser,
        )
        result = await frontier.run(redis_client=stub_redis)  # type: ignore[arg-type]
        assert summariser_called == []
        assert result.ingested == 1

        # 3) AmendmentDrafter refuses.
        audit = EvolutionAuditWriter(
            store=AuditStore(
                InMemoryAuditCollection(),
                jsonl_path=tmp_path / "audit2.jsonl",
            )
        )
        drafter = AmendmentDrafter(
            audit=audit, pending_dir=tmp_path / "pending2"
        )
        with pytest.raises(AmendmentBudgetError):
            await drafter.draft(
                amendment_id="amend-x-017-all3",
                artifact_type="prompt",
                artifact_id="PROMPT-fund_manager-v6",
                champion_baseline_id="PROMPT-fund_manager-v5",
                shadow_report=_shadow_report(),
                verdict=_verdict(),
                diff=DiffBlock(label="d", body="- a\n+ b\n"),
                champion_body_length=10,
                challenger_body_length=12,
                redis_client=stub_redis,  # type: ignore[arg-type]
            )


@pytest.fixture
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Strip any user-defined QUANTMIND_* overrides so the assertions
    can rely on the 4 P1-7 defaults."""
    for var in (
        "QUANTMIND_DAILY_BUDGET",
        "QUANTMIND_SOFT_CEIL_PCT",
        "QUANTMIND_MONTHLY_BUDGET",
        "QUANTMIND_KIMI_DAILY_CAP",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


class TestConstantsSurviveEnvAbsence:
    """If the env vars are missing, the defaults still match the lock."""

    @pytest.mark.asyncio
    async def test_daily_default_used(
        self,
        stub_redis: _StubRedis,
        at_zero_spend: None,
        _isolate_env: None,
    ) -> None:
        state = await assert_budget_allows(
            stub_redis,  # type: ignore[arg-type]
            agent_name="x_017_test",
        )
        assert state.daily_budget == 100.0
        assert state.soft_ceiling == round(100.0 * 0.70, 4)

    @pytest.mark.asyncio
    async def test_kimi_default_used(
        self,
        stub_redis: _StubRedis,
        at_zero_spend: None,
        _isolate_env: None,
    ) -> None:
        state = await assert_kimi_budget_allows(
            stub_redis,  # type: ignore[arg-type]
            agent_name="x_017_test",
        )
        assert state.kimi_daily_cap == 4.0
