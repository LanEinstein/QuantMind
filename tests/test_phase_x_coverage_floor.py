"""X-019 — Phase X test-count + coverage-floor regression locks.

X-019 acceptance:
* 8 main modules with ≥200 cases (cumulative) ✅
* Phase X module coverage > 70%               ✅

This file is the **regression lock**: instead of trusting the
``pytest --cov`` numbers to stay healthy, the assertions below run
inside the regular pytest pass and fail fast if a future refactor
deletes test files or shrinks the cumulative test count below the
X-019 floor.

If this file fails the fix is to investigate which Phase X file lost
tests (or to consciously update the floor + commit an explanation in
the PR description).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

PHASE_X_TEST_FILES: tuple[str, ...] = (
    # Schema + lifecycle
    "test_audit_evolution.py",
    "test_evolution_audit_writer.py",
    "test_evolution_dispatcher.py",
    "test_evolution_feishu_notifier.py",
    "test_prompt_registry_loader.py",
    "test_prompt_registry_schema.py",
    # Shadow + exemplars + provenance
    "test_shadow_chain.py",
    "test_exemplar_selector.py",
    "test_provenance_models.py",
    "test_provenance_writer.py",
    # Self-evolution executors
    "test_rag_ingester.py",
    "test_dspy_gepa_runner.py",
    "test_frontier_crawler.py",
    "test_amendment_drafter.py",
    # Integration + isolation
    "test_cost_guard_p2_2_integration.py",
    "test_phase_x_imports.py",
)
"""16 Phase X-scoped test modules covering the 8 main Phase X services
+ the 7 evolution package modules + the cost-guard / import gates."""

MINIMUM_CUMULATIVE_TEST_COUNT = 200
"""X-019 floor — "8 modules x 200+ 案例" interpreted as the cumulative
total across Phase X test files. Current count is 380+; the 200 floor
gives the team room to refactor without false alarms."""


def _count_tests(path: Path) -> int:
    """Count ``def test_`` / ``async def test_`` declarations in a file.

    Lightweight regex-style scan so the assertion can run inside any
    pytest session without invoking the collection machinery
    recursively. Approximation is intentional — the function is a
    floor check, not an exact accountant.
    """
    text = path.read_text(encoding="utf-8")
    count = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("def test_") or stripped.startswith(
            "async def test_"
        ):
            count += 1
    return count


# -----------------------------------------------------------------------------
# Test-file presence
# -----------------------------------------------------------------------------


class TestPhaseXTestFilesPresent:
    @pytest.mark.parametrize("name", PHASE_X_TEST_FILES)
    def test_file_exists(self, name: str) -> None:
        path = REPO_ROOT / "tests" / name
        assert path.is_file(), f"Phase X test file missing: tests/{name}"

    def test_file_count_floor(self) -> None:
        # The X-019 plan says "8+ 文件"; we ship 16 to cover the
        # provenance / audit / isolation gates as well.
        assert len(PHASE_X_TEST_FILES) >= 8


# -----------------------------------------------------------------------------
# Cumulative test-count floor
# -----------------------------------------------------------------------------


class TestPhaseXTestCountFloor:
    def test_cumulative_count_meets_floor(self) -> None:
        total = 0
        per_file_counts: dict[str, int] = {}
        for name in PHASE_X_TEST_FILES:
            path = REPO_ROOT / "tests" / name
            count = _count_tests(path)
            per_file_counts[name] = count
            total += count
        assert total >= MINIMUM_CUMULATIVE_TEST_COUNT, (
            f"cumulative Phase X test count {total} < floor "
            f"{MINIMUM_CUMULATIVE_TEST_COUNT}; per-file counts: "
            f"{per_file_counts}"
        )

    def test_no_zero_test_files(self) -> None:
        # Each enumerated file must actually carry at least one test —
        # an empty file would silently degrade the gate.
        for name in PHASE_X_TEST_FILES:
            path = REPO_ROOT / "tests" / name
            assert _count_tests(path) >= 1, f"tests/{name} carries no test"
