"""X-022 — P1-5 §2 red line 1 永锁 (Phase X must not add front-end pages).

The 11-page nav (MVP 7 + Phase B 收尾 4) is locked. Phase X self-evolution
is intentionally surfaced inside ``SystemStatus.vue`` as an additional
card (X-023) so Phase X does **not** consume a new page slot. This test
locks the *file-level* invariant: the number of ``.vue`` files under
``frontend/src/views/`` cannot change without an amendment.

Locking the *file* count (rather than just the nav entries) catches two
regression vectors at once:

* Adding a new top-level ``.vue`` (would consume a page slot).
* Adding a new settings sub-view without an amendment (would inflate
  the settings tab count beyond the 4 P1-5 locks).

The Simulation.vue file is intentionally kept in the tree (it has code
but is intentionally *not* in the menu; the menu-spec test in
``frontend/src/router/__tests__/menu.spec.ts`` enforces its absence
from ``NAV_GROUPS``). The file-count constant below counts it.

Numbers
~~~~~~~

* Top-level views: 14 (11 menu pages + Simulation.vue + ExecutionReportEntry.vue
  + ReconciliationCenter.vue; the latter two ARE the locked write-input
  pages — they are not in the standard "11 review pages" count but are
  legitimate menu entries).
* ``settings/`` sub-views: 5 (4 settings sub-pages + 1 SettingsLayout).
* TOTAL: 19 ``.vue`` files.

Bump either constant only with a docs/decisions amendment so the change
goes through the same gate as any other red-line shift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

VIEWS_ROOT = Path("frontend/src/views")

# Locked at session #25 (X-022) — matches the working tree as of
# Phase X-D landing. Bumping either number requires a P1-5 amendment.
LOCKED_TOP_LEVEL_VUE_FILES = 14
LOCKED_SETTINGS_VUE_FILES = 5
LOCKED_TOTAL_VUE_FILES = LOCKED_TOP_LEVEL_VUE_FILES + LOCKED_SETTINGS_VUE_FILES


@pytest.fixture(scope="module")
def top_level_vue_files() -> list[Path]:
    return sorted(p for p in VIEWS_ROOT.glob("*.vue"))


@pytest.fixture(scope="module")
def settings_vue_files() -> list[Path]:
    return sorted(p for p in (VIEWS_ROOT / "settings").glob("*.vue"))


@pytest.fixture(scope="module")
def all_vue_files() -> list[Path]:
    return sorted(p for p in VIEWS_ROOT.rglob("*.vue"))


def test_views_root_exists() -> None:
    assert VIEWS_ROOT.is_dir(), (
        f"P1-5 §2 红线 1 lock: {VIEWS_ROOT} must exist; the front-end "
        "directory shape is part of the locked menu invariant."
    )


def test_top_level_view_file_count_locked(
    top_level_vue_files: list[Path],
) -> None:
    """Top-level ``*.vue`` count is the public-page lock surface.

    Adding a new top-level .vue (without a corresponding amendment to
    P1-5 §2 红线 1) signals an attempted page-slot inflation; this test
    fires the regression before review.
    """
    actual_names = [p.name for p in top_level_vue_files]
    assert len(top_level_vue_files) == LOCKED_TOP_LEVEL_VUE_FILES, (
        f"P1-5 §2 红线 1 violated: top-level frontend/src/views/ now "
        f"contains {len(top_level_vue_files)} .vue files "
        f"(locked at {LOCKED_TOP_LEVEL_VUE_FILES}); current set = "
        f"{actual_names}. Phase X must NOT add a new top-level page — "
        "surface new state via SystemStatus.vue or extend an existing "
        "review-group page; bump this number only with an amendment."
    )


def test_settings_view_file_count_locked(
    settings_vue_files: list[Path],
) -> None:
    """Settings sub-views are similarly locked (4 sub-pages + 1 layout)."""
    actual_names = [p.name for p in settings_vue_files]
    assert len(settings_vue_files) == LOCKED_SETTINGS_VUE_FILES, (
        f"P1-5 §2 红线 1 violated: frontend/src/views/settings/ now "
        f"contains {len(settings_vue_files)} .vue files "
        f"(locked at {LOCKED_SETTINGS_VUE_FILES}); current set = "
        f"{actual_names}. Bump this number only with an amendment."
    )


def test_total_view_file_count_locked(all_vue_files: list[Path]) -> None:
    """Belt-and-braces: ``rglob`` everything under ``views/``."""
    assert len(all_vue_files) == LOCKED_TOTAL_VUE_FILES, (
        f"P1-5 §2 红线 1 violated: total .vue file count under "
        f"frontend/src/views/ = {len(all_vue_files)} "
        f"(locked at {LOCKED_TOTAL_VUE_FILES}); inspect git status for "
        "unexpected new .vue file then amend the lock if intentional."
    )


def test_simulation_vue_present_but_kept_out_of_nav(
    top_level_vue_files: list[Path],
) -> None:
    """Simulation.vue is intentionally kept in the codebase (P1-5 §2 note).

    The corresponding ``menu.spec.ts`` test asserts ``/simulation`` is
    NOT in ``NAV_GROUPS``; this test asserts the *file* still exists so
    the route at ``/simulation`` continues to resolve on direct visit.
    """
    names = {p.name for p in top_level_vue_files}
    assert "Simulation.vue" in names, (
        "Simulation.vue removed: P1-5 §2 noted it stays in code (留代码"
        "不进菜单). Removing it requires an amendment because the "
        "menu-spec test still pins the /simulation route as a non-menu "
        "entry."
    )


def test_known_locked_page_set_present(
    top_level_vue_files: list[Path],
) -> None:
    """Lock the *names* of the top-level pages (not just the count).

    A swap (e.g. delete Dashboard.vue + add ShinyNewPage.vue) would
    keep the count constant but materially change the menu shape; this
    test catches that regression too.
    """
    expected = {
        # Runtime group
        "Dashboard.vue",
        "SystemStatus.vue",
        # Decisions group
        "InstructionPlans.vue",
        # Ledger group
        "Portfolio.vue",
        "ExecutionReportEntry.vue",
        "ReconciliationCenter.vue",
        # Review group (core 3)
        "Performance.vue",
        "AcceptanceReports.vue",
        "RiskCenter.vue",
        # Review group (Phase B 收尾 4)
        "AgentDebate.vue",
        "DataQuality.vue",
        "FeishuMessages.vue",
        "CostBreakdown.vue",
        # Code-only (not in menu)
        "Simulation.vue",
    }
    actual = {p.name for p in top_level_vue_files}
    assert actual == expected, (
        "Top-level view set drift: "
        f"missing={sorted(expected - actual)} "
        f"extra={sorted(actual - expected)}"
    )
