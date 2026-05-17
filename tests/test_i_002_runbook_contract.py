"""J-006 — Acceptance contract tests for the I-002 runbook + playbook.

The runbook + incident playbook are documentation, not code, so the
tests here verify the contract surfaces that other Phase J tasks
reference + the operator-facing structure (section count, incident
count, line budget). Anything richer (procedure correctness) is
validated by the J-005 simulator harness when an operator rehearses
the steps.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNBOOK = _REPO_ROOT / "docs" / "runbook" / "i-002-production-runbook.md"
_PLAYBOOK = _REPO_ROOT / "docs" / "runbook" / "i-002-incident-playbook.md"


# ---------------------------------------------------------------------------
# Existence + size budget
# ---------------------------------------------------------------------------


def test_runbook_exists() -> None:
    assert _RUNBOOK.exists()


def test_playbook_exists() -> None:
    assert _PLAYBOOK.exists()


def test_combined_line_count_meets_800() -> None:
    """J-006 acceptance: docs/runbook/i-002-*.md combined ≥ 800 lines."""
    total = sum(
        len(p.read_text(encoding="utf-8").splitlines())
        for p in (_RUNBOOK, _PLAYBOOK)
    )
    assert total >= 800, f"combined runbook+playbook is {total} lines (< 800)"


def test_runbook_substantive_byte_size() -> None:
    """A non-trivial runbook is at least 10 KB."""
    assert _RUNBOOK.stat().st_size >= 10_000


# ---------------------------------------------------------------------------
# Required section coverage in the production runbook
# ---------------------------------------------------------------------------


_REQUIRED_RUNBOOK_SECTIONS = (
    "Pre-flight checklist",
    "Cold-start sequence",
    "Daily 16:30 acceptance verification",
    "5 reset trigger response manual",
    "Mongo backup cadence",
    "Redis persistence",
    "LLM provider switchover",
    "Cost overrun handling",
    "Accidental restart recovery",
    "Stopping the long-run",
    "Authorization expiry mid-run",
    "End-of-run procedure",
)


@pytest.mark.parametrize("phrase", _REQUIRED_RUNBOOK_SECTIONS)
def test_runbook_covers_required_section(phrase: str) -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert phrase in text, f"missing required section: {phrase!r}"


def test_runbook_lists_5_reset_triggers() -> None:
    """All 5 P0-6 §1 trigger names appear in the runbook."""
    text = _RUNBOOK.read_text(encoding="utf-8")
    for trigger in (
        "MARKET_DATA_OUTAGE_30MIN",
        "LLM_FULL_STOP_1H",
        "MOCK_BROKER_CORRUPTION",
        "STATE_MACHINE_ILLEGAL_TRANSITION",
        "LONG_CONN_OUTAGE_4H",
    ):
        assert trigger in text, f"runbook missing trigger reference: {trigger}"


def test_runbook_explicitly_calls_out_reconciliation_freeze_exclusion() -> None:
    """Reconciliation freeze is NOT a reset — must be documented."""
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "Reconciliation freeze" in text or "reconciliation freeze" in text
    # The phrasing should warn that freeze PAUSES not RESETS.
    assert re.search(
        r"(NOT a reset|does not reset|PAUSE|pause)",
        text,
    ) is not None


def test_runbook_references_j_007_owner_authorization() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "QUANTMIND_OWNER_PROD_AUTHORIZATION" in text
    assert "QUANTMIND_PROD_RUN" in text


def test_runbook_references_j_001_dashboard_cli() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "scripts/acceptance_dashboard.py" in text


def test_runbook_references_j_002_smoke_script() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "scripts/smoke_test_cold_start.py" in text


def test_runbook_references_j_005_simulator() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    assert "scripts/simulate_n_trading_days.py" in text


# ---------------------------------------------------------------------------
# Incident playbook structure
# ---------------------------------------------------------------------------


def _count_incident_headings(text: str) -> int:
    """An incident is a level-2 heading starting with 'Incident #N'."""
    return len(re.findall(r"^## Incident #\d+", text, flags=re.MULTILINE))


def test_playbook_has_at_least_10_incidents() -> None:
    """J-006 acceptance: ≥10 incident scenarios with step-by-step recovery."""
    text = _PLAYBOOK.read_text(encoding="utf-8")
    incident_count = _count_incident_headings(text)
    assert incident_count >= 10, (
        f"incident playbook has only {incident_count} scenarios (< 10)"
    )


def test_each_incident_has_recovery_section() -> None:
    """Every incident must include a Recovery step + Verification."""
    text = _PLAYBOOK.read_text(encoding="utf-8")
    incidents = re.findall(
        r"^## Incident #\d+.*?(?=^## Incident #\d+|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    for idx, body in enumerate(incidents, start=1):
        assert "**Recovery." in body or "Recovery." in body, (
            f"incident #{idx} missing Recovery section"
        )
        assert "Verification" in body, (
            f"incident #{idx} missing Verification section"
        )


def test_playbook_covers_owner_authorization_typo_path() -> None:
    """Incident playbook must walk an operator through fixing a malformed
    auth env var (J-007 surface)."""
    text = _PLAYBOOK.read_text(encoding="utf-8")
    assert "OwnerProdAuthorizationError" in text
    assert "QUANTMIND_OWNER_PROD_AUTHORIZATION" in text


def test_playbook_links_back_to_production_runbook() -> None:
    text = _PLAYBOOK.read_text(encoding="utf-8")
    assert "i-002-production-runbook.md" in text
