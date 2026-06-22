"""Tests for the cumulative trial ledger + legacy block (QGR-2 ⑤, codex P0).

The data-mining debt from rounds 1-4 must NOT reset when the judgment criterion
changes (絶対净盈 vs CSI300 excess). The ledger pre-seeds a sourced legacy block
(R1-R4 grids = 2348 + test reads + diagnostics) and the deflation N fed to
DSR/MinBTL is ``max(legacy_cumulative, new ONC effective N)`` — never zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.factor_research.trial_ledger import (
    LEGACY_GRID_TOTAL,
    TrialLedger,
    TrialRecord,
    legacy_block,
)


def test_legacy_grid_total_is_sourced_2348() -> None:
    # 512 + 612 + 612 + 612 (round4_locked_test.py:9).
    assert LEGACY_GRID_TOTAL == 2348
    grid = sum(
        r.n_nominal_trials for r in legacy_block() if r.kind == "grid"
    )
    assert grid == 2348


def test_legacy_block_includes_test_reads_and_diagnostics() -> None:
    kinds = {r.kind for r in legacy_block()}
    assert "grid" in kinds
    assert "test_read" in kinds  # the 4 holdout evaluations
    assert "diagnostics" in kinds
    test_reads = sum(
        r.n_nominal_trials for r in legacy_block() if r.kind == "test_read"
    )
    assert test_reads == 4


def test_fresh_ledger_starts_at_legacy_floor(tmp_path: Path) -> None:
    ledger = TrialLedger.with_legacy(tmp_path / "ledger.jsonl")
    cumulative = ledger.cumulative_nominal_trials()
    assert cumulative >= LEGACY_GRID_TOTAL  # never starts from zero


def test_changing_criteria_does_not_reset_debt(tmp_path: Path) -> None:
    # The whole point of codex P0: a fresh QGR ledger still carries the legacy
    # debt even though the criterion is now absolute net P&L.
    ledger = TrialLedger.with_legacy(tmp_path / "ledger.jsonl")
    assert ledger.cumulative_nominal_trials() >= LEGACY_GRID_TOTAL


def test_append_is_idempotent_and_grows_cumulative(tmp_path: Path) -> None:
    ledger = TrialLedger.with_legacy(tmp_path / "ledger.jsonl")
    base = ledger.cumulative_nominal_trials()
    rec = TrialRecord(
        round_label="qgr-4",
        kind="grid",
        family="qgr.reversal_gate",
        description="reversal+liquidity gate search",
        n_nominal_trials=120,
        window_start="2015-01-05",
        window_end="2025-06-12",
        registered_at="2026-06-22",
    )
    assert ledger.append(rec) is True
    assert ledger.cumulative_nominal_trials() == base + 120
    # same design re-appended → idempotent skip, no double count.
    assert ledger.append(rec) is False
    assert ledger.cumulative_nominal_trials() == base + 120


def test_append_persists_across_reload(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = TrialLedger.with_legacy(path)
    ledger.append(
        TrialRecord(
            round_label="qgr-4",
            kind="grid",
            family="qgr.momentum",
            description="1d momentum gate",
            n_nominal_trials=50,
            window_start="2015-01-05",
            window_end="2025-06-12",
            registered_at="2026-06-22",
        )
    )
    reloaded = TrialLedger.with_legacy(path)
    assert reloaded.cumulative_nominal_trials() == ledger.cumulative_nominal_trials()


def test_deflation_n_takes_max_of_legacy_and_onc(tmp_path: Path) -> None:
    ledger = TrialLedger.with_legacy(tmp_path / "ledger.jsonl")
    cumulative = ledger.cumulative_nominal_trials()
    # a small new-batch ONC effective N never lowers the bar below the legacy debt.
    assert ledger.deflation_n_trials(onc_effective_n=5) == cumulative
    # a (hypothetically) larger ONC batch would raise it.
    bigger = cumulative + 100
    assert ledger.deflation_n_trials(onc_effective_n=bigger) == bigger


def test_near_duplicate_grid_deflates_by_effective_not_raw(tmp_path: Path) -> None:
    # codex P2: appending a 10k near-duplicate grid with ONC effective N = 2 must
    # NOT push the deflation N up by 10k — only its effective count counts.
    ledger = TrialLedger.with_legacy(tmp_path / "ledger.jsonl")
    floor = ledger.cumulative_effective_trials()
    ledger.append(
        TrialRecord(
            round_label="qgr-4",
            kind="grid",
            family="qgr.dupes",
            description="10k near-duplicate configs",
            n_nominal_trials=10_000,
            window_start="2015-01-05",
            window_end="2025-06-12",
            registered_at="2026-06-22",
            effective_n=2,  # ONC-deduplicated
        )
    )
    # nominal accounting still records the raw grid for the audit trail...
    assert ledger.cumulative_nominal_trials() == floor + 10_000
    # ...but the deflation N only grows by the effective 2, not 10k.
    assert ledger.cumulative_effective_trials() == floor + 2
    assert ledger.deflation_n_trials(onc_effective_n=1) == floor + 2


def test_count_trials_by_family(tmp_path: Path) -> None:
    ledger = TrialLedger.with_legacy(tmp_path / "ledger.jsonl")
    ledger.append(
        TrialRecord(
            round_label="qgr-4",
            kind="grid",
            family="qgr.reversal",
            description="x",
            n_nominal_trials=30,
            window_start="2015-01-05",
            window_end="2025-06-12",
            registered_at="2026-06-22",
        )
    )
    assert ledger.cumulative_nominal_trials(family="qgr.reversal") == 30


def test_negative_trials_rejected() -> None:
    with pytest.raises(ValueError):
        TrialRecord(
            round_label="x",
            kind="grid",
            family="f",
            description="d",
            n_nominal_trials=-1,
            window_start="2015-01-05",
            window_end="2025-06-12",
            registered_at="2026-06-22",
        )
