"""X-013 — AmendmentDrafter unit tests.

Covers R7 4-section enforcement, length_inflation flag (>50%), audit
emission, and ``docs/decisions/pending/{amendment_id}.md`` write.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.audit.store import AuditStore, InMemoryAuditCollection
from backend.services.amendment_drafter import (
    ARTIFACT_TYPES,
    DEFAULT_LENGTH_INFLATION_THRESHOLD,
    MANDATORY_SECTIONS,
    AmendmentDrafter,
    AmendmentSchemaError,
    DiffBlock,
)
from backend.services.evolution_audit_writer import EvolutionAuditWriter
from backend.services.shadow_chain import (
    ChallengerVerdict,
    MetricComparison,
    ShadowAcceptanceReport,
    make_acceptance_report,
)


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
def drafter(tmp_path: Path) -> AmendmentDrafter:
    pending = tmp_path / "pending"
    audit = EvolutionAuditWriter(
        store=AuditStore(InMemoryAuditCollection(), jsonl_path=tmp_path / "audit.jsonl")
    )
    return AmendmentDrafter(audit=audit, pending_dir=pending)


@pytest.mark.asyncio
async def test_happy_path_writes_file_and_emits_audit(
    drafter: AmendmentDrafter,
    tmp_path: Path,
) -> None:
    result = await drafter.draft(
        amendment_id="PROMPT-fund_manager-v4",
        artifact_type="prompt",
        artifact_id="PROMPT-fund_manager-v4",
        champion_baseline_id="PROMPT-fund_manager-v3",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="prompt diff", body="```diff\n+ new line\n```"),
        champion_body_length=1000,
        challenger_body_length=1100,
    )
    assert result.amendment_path.is_file()
    body = result.amendment_path.read_text()
    for section in MANDATORY_SECTIONS:
        assert section in body
    audit_jsonl = (tmp_path / "audit.jsonl").read_text()
    assert "evolution_amendment_drafted" in audit_jsonl
    assert result.flags == ()


@pytest.mark.asyncio
async def test_length_inflation_flag_above_50pct(
    drafter: AmendmentDrafter,
) -> None:
    result = await drafter.draft(
        amendment_id="A2",
        artifact_type="prompt",
        artifact_id="A2",
        champion_baseline_id="A1",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="prompt diff", body=""),
        champion_body_length=1000,
        challenger_body_length=1600,
    )
    assert "length_inflation" in result.flags
    body = result.amendment_path.read_text()
    assert "length_inflation" in body


@pytest.mark.asyncio
async def test_length_inflation_not_flagged_at_threshold(
    drafter: AmendmentDrafter,
) -> None:
    # exactly 50% inflation should NOT trigger (must exceed).
    result = await drafter.draft(
        amendment_id="A3",
        artifact_type="prompt",
        artifact_id="A3",
        champion_baseline_id="A1",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="x", body=""),
        champion_body_length=1000,
        challenger_body_length=1500,
    )
    assert "length_inflation" not in result.flags


@pytest.mark.asyncio
async def test_zero_champion_with_content_flags(
    drafter: AmendmentDrafter,
) -> None:
    result = await drafter.draft(
        amendment_id="A4",
        artifact_type="prompt",
        artifact_id="A4",
        champion_baseline_id="A1",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="x", body=""),
        champion_body_length=0,
        challenger_body_length=10,
    )
    assert "length_inflation" in result.flags


@pytest.mark.asyncio
async def test_artifact_type_invalid(
    drafter: AmendmentDrafter,
) -> None:
    with pytest.raises(AmendmentSchemaError):
        await drafter.draft(
            amendment_id="A5",
            artifact_type="not_a_type",  # type: ignore[arg-type]
            artifact_id="A5",
            champion_baseline_id="A1",
            shadow_report=_shadow_report(),
            verdict=_verdict(),
            diff=DiffBlock(label="x", body=""),
            champion_body_length=1,
            challenger_body_length=1,
        )


@pytest.mark.asyncio
async def test_negative_lengths_rejected(
    drafter: AmendmentDrafter,
) -> None:
    with pytest.raises(AmendmentSchemaError):
        await drafter.draft(
            amendment_id="A6",
            artifact_type="prompt",
            artifact_id="A6",
            champion_baseline_id="A1",
            shadow_report=_shadow_report(),
            verdict=_verdict(),
            diff=DiffBlock(label="x", body=""),
            champion_body_length=-1,
            challenger_body_length=10,
        )


def test_mandatory_sections_lock() -> None:
    assert MANDATORY_SECTIONS == (
        "## diff",
        "## shadow evidence",
        "## readability check",
        "## rollback",
    )


def test_threshold_lock() -> None:
    assert DEFAULT_LENGTH_INFLATION_THRESHOLD == 0.50


def test_artifact_types_lock() -> None:
    assert ARTIFACT_TYPES == {
        "prompt",
        "rag_document",
        "risk_parameter_proposal",
        "exemplar_schema",
    }


@pytest.mark.asyncio
async def test_diff_body_inlined_verbatim(
    drafter: AmendmentDrafter,
) -> None:
    diff_body = "```diff\n- old prompt\n+ new prompt\n```"
    result = await drafter.draft(
        amendment_id="A7",
        artifact_type="prompt",
        artifact_id="A7",
        champion_baseline_id="A1",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="prompt diff", body=diff_body),
        champion_body_length=10,
        challenger_body_length=10,
    )
    body = result.amendment_path.read_text()
    assert diff_body in body


@pytest.mark.asyncio
async def test_verdict_fail_still_drafts(
    drafter: AmendmentDrafter,
) -> None:
    # The drafter records the verdict — failing-challenger drafts are
    # still useful for the operator to inspect post-mortem.
    result = await drafter.draft(
        amendment_id="A8",
        artifact_type="prompt",
        artifact_id="A8",
        champion_baseline_id="A1",
        shadow_report=_shadow_report(),
        verdict=_verdict(passed=False),
        diff=DiffBlock(label="x", body=""),
        champion_body_length=1,
        challenger_body_length=1,
    )
    body = result.amendment_path.read_text()
    assert "outcome: FAIL" in body


@pytest.mark.asyncio
async def test_pending_dir_created_on_demand(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "deep" / "nested" / "pending"
    audit = EvolutionAuditWriter(
        store=AuditStore(
            InMemoryAuditCollection(),
            jsonl_path=tmp_path / "audit.jsonl",
        )
    )
    drafter = AmendmentDrafter(audit=audit, pending_dir=nested)
    result = await drafter.draft(
        amendment_id="A9",
        artifact_type="rag_document",
        artifact_id="A9",
        champion_baseline_id="A0",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="x", body=""),
        champion_body_length=10,
        challenger_body_length=10,
    )
    assert nested.is_dir()
    assert result.amendment_path.parent == nested


@pytest.mark.asyncio
async def test_audit_payload_records_artifact_path(
    drafter: AmendmentDrafter,
    tmp_path: Path,
) -> None:
    await drafter.draft(
        amendment_id="A10",
        artifact_type="exemplar_schema",
        artifact_id="A10",
        champion_baseline_id="A9",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="x", body=""),
        champion_body_length=10,
        challenger_body_length=10,
    )
    audit_jsonl = (tmp_path / "audit.jsonl").read_text()
    assert "A10.md" in audit_jsonl


@pytest.mark.asyncio
async def test_correlation_id_propagated(
    drafter: AmendmentDrafter,
    tmp_path: Path,
) -> None:
    await drafter.draft(
        amendment_id="A11",
        artifact_type="prompt",
        artifact_id="A11",
        champion_baseline_id="A10",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="x", body=""),
        champion_body_length=10,
        challenger_body_length=10,
        correlation_id="run-xyz",
    )
    audit_jsonl = (tmp_path / "audit.jsonl").read_text()
    assert "run-xyz" in audit_jsonl


@pytest.mark.asyncio
async def test_filename_uses_amendment_id(
    drafter: AmendmentDrafter,
) -> None:
    result = await drafter.draft(
        amendment_id="my-amendment-123",
        artifact_type="prompt",
        artifact_id="my-amendment-123",
        champion_baseline_id="prev",
        shadow_report=_shadow_report(),
        verdict=_verdict(),
        diff=DiffBlock(label="x", body=""),
        champion_body_length=1,
        challenger_body_length=1,
    )
    assert result.amendment_path.name == "my-amendment-123.md"


@pytest.mark.asyncio
async def test_amendment_id_path_traversal_rejected(
    drafter: AmendmentDrafter,
) -> None:
    # Codex review P1-2 regression — amendment_id with a path
    # separator must fail BEFORE the file is written.
    for bad in ["../etc/passwd", "foo/bar", r"foo\bar", "..", "."]:
        with pytest.raises(AmendmentSchemaError):
            await drafter.draft(
                amendment_id=bad,
                artifact_type="prompt",
                artifact_id="A",
                champion_baseline_id="B",
                shadow_report=_shadow_report(),
                verdict=_verdict(),
                diff=DiffBlock(label="x", body=""),
                champion_body_length=1,
                challenger_body_length=1,
            )
