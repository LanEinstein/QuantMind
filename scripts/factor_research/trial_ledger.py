"""Cumulative trial ledger with a pre-seeded legacy block (QGR-2 ⑤, codex P0).

The arena is reusable precisely *because* it accounts for adaptive reuse: every
configuration ever scored on the shared PIT dataset is recorded, and the
significance bar (DSR's ``SR0`` benchmark + MinBTL) deflates by the cumulative
effective trial count. The codex-P0 invariant: **changing the judgment criterion
does NOT reset the data-mining debt.** Rounds 1-4 ran on the same data while
chasing CSI300 excess; that multiple-testing happened and cannot be laundered by
re-framing the criterion as absolute net P&L. So this ledger is born with a
**legacy block** and the deflation N is ``max(legacy_cumulative, new ONC N)``.

The sourced legacy grids: ``2348 = 512 + 612 + 612 + 612`` (round-1 + rounds
2/3/4 grids — ``round4_locked_test.py:9`` / ``round2_search.py:450``), plus the
4 locked-test holdout reads (CLAUDE.md «已验证原则» #4) and the factor-diagnostic
/ ablation / sign-test passes. The diagnostics count is a *conservative lower
bound* (factors screened across R2-2/R3-3/R4-4 + R5 robustness); since the
deflation takes a ``max`` against the new ONC N, an undercount of the legacy
floor only ever makes the gate *easier* by a bounded amount — never harder — and
the dominant, exact term is the 2348 grid.

Append-only JSONL (mirrors :class:`backend.strategy_evolution.experiment_registry`
semantics on the research side); ``trial_id`` is content-addressed over the DESIGN
so a re-append is an idempotent skip and an outcome can never be re-laundered.
Pure stdlib; ``registered_at`` is an injected date string (no wall-clock).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Exact, sourced round grids (the dominant, precise term of the legacy debt).
_R1_GRID = 512
_R2_GRID = 612
_R3_GRID = 612
_R4_GRID = 612
LEGACY_GRID_TOTAL = _R1_GRID + _R2_GRID + _R3_GRID + _R4_GRID  # 2348

# Conservative lower-bound counts for the non-grid data-snooping passes.
_TEST_READS = 4  # round-1/2/3/4 locked-test holdout evaluations (one each).
# Factors screened in the IC/sign diagnostics (R2-2 ≈ 9, R3-3 ≈ 3, R4-4 ≈ 7) +
# R5 robustness/ablation/sentinel passes (≈ 5). A documented lower bound.
_DIAGNOSTIC_SCREENS = 9 + 3 + 7
_R5_ROBUSTNESS = 5


@dataclass(frozen=True)
class TrialRecord:
    """One append-only trial entry — a config (or aggregate) scored on the data.

    ``n_nominal_trials`` is the number of distinct configurations this record
    stands for (1 for a single config; a whole grid for a legacy aggregate).
    """

    round_label: str
    kind: str  # "grid" | "test_read" | "diagnostics" | "ablation" | "single"
    family: str
    description: str
    n_nominal_trials: int
    window_start: str
    window_end: str
    registered_at: str
    effective_n: int | None = None
    """ONC-deduplicated effective trial count for this batch (deflation input).
    ``None`` ⇒ use ``n_nominal_trials`` (each config a distinct trial — correct
    for the legacy grids, the conservative floor). A QGR search batch of near-
    duplicates passes its ``onc_effective_n`` here so the raw grid size cannot
    inflate the deflation N beyond its effective independent count."""

    def __post_init__(self) -> None:
        if self.n_nominal_trials < 0:
            raise ValueError("n_nominal_trials must be >= 0")
        if self.effective_n is not None and self.effective_n < 0:
            raise ValueError("effective_n must be >= 0")

    @property
    def effective_count(self) -> int:
        return self.n_nominal_trials if self.effective_n is None else self.effective_n

    @property
    def trial_id(self) -> str:
        """Content address over the DESIGN (outcome-free; idempotent re-append)."""
        payload = json.dumps(
            {
                "round_label": self.round_label,
                "kind": self.kind,
                "family": self.family,
                "description": self.description,
                "n_nominal_trials": self.n_nominal_trials,
                "window_start": self.window_start,
                "window_end": self.window_end,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def legacy_block() -> tuple[TrialRecord, ...]:
    """The pre-seeded R1-R4 mining debt (code-defined, never lost; QGR §4.1 ⑤)."""
    win = ("2015-01-05", "2026-06-12")
    when = "2026-06-21"  # QGR framework reset date (fixed; no wall-clock).
    grids = [
        ("round-1", _R1_GRID, "round-1 nominal weight grid"),
        ("round-2", _R2_GRID, "round-2 benchmark-relative grid"),
        ("round-3", _R3_GRID, "round-3 accruals/SUE grid"),
        ("round-4", _R4_GRID, "round-4 analyst-revision grid"),
    ]
    records: list[TrialRecord] = [
        TrialRecord(
            round_label=label,
            kind="grid",
            family=f"legacy.{label}",
            description=desc,
            n_nominal_trials=n,
            window_start=win[0],
            window_end=win[1],
            registered_at=when,
        )
        for label, n, desc in grids
    ]
    records.append(
        TrialRecord(
            round_label="round-1..4",
            kind="test_read",
            family="legacy.test_reads",
            description="four locked-test holdout evaluations (CLAUDE.md 原则 #4)",
            n_nominal_trials=_TEST_READS,
            window_start=win[0],
            window_end=win[1],
            registered_at=when,
        )
    )
    records.append(
        TrialRecord(
            round_label="round-2..4",
            kind="diagnostics",
            family="legacy.diagnostics",
            description="factor IC/sign diagnostic screens (R2-2/R3-3/R4-4)",
            n_nominal_trials=_DIAGNOSTIC_SCREENS,
            window_start=win[0],
            window_end=win[1],
            registered_at=when,
        )
    )
    records.append(
        TrialRecord(
            round_label="round-5",
            kind="ablation",
            family="legacy.robustness",
            description="R5 robustness/ablation/sentinel passes",
            n_nominal_trials=_R5_ROBUSTNESS,
            window_start=win[0],
            window_end=win[1],
            registered_at=when,
        )
    )
    return tuple(records)


class TrialLedger:
    """Append-only cumulative trial ledger with the legacy block (research side)."""

    def __init__(self, path: Path, *, legacy: tuple[TrialRecord, ...]) -> None:
        self._path = Path(path)
        self._legacy = legacy
        self._appended: list[TrialRecord] = []
        self._ids: set[str] = {r.trial_id for r in legacy}
        self._load()

    @classmethod
    def with_legacy(cls, path: str | Path) -> TrialLedger:
        """Construct a ledger pre-seeded with the R1-R4 legacy debt."""
        return cls(Path(path), legacy=legacy_block())

    def _load(self) -> None:
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                rec = TrialRecord(**json.loads(line))
                if rec.trial_id not in self._ids:
                    self._ids.add(rec.trial_id)
                    self._appended.append(rec)

    def append(self, record: TrialRecord) -> bool:
        """Append a new trial; ``False`` on an idempotent duplicate skip."""
        if record.trial_id in self._ids:
            return False
        self._ids.add(record.trial_id)
        self._appended.append(record)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), sort_keys=True, ensure_ascii=True))
            fh.write("\n")
        return True

    def records(self) -> tuple[TrialRecord, ...]:
        return self._legacy + tuple(self._appended)

    def cumulative_nominal_trials(self, *, family: str | None = None) -> int:
        """Cumulative NOMINAL trial count (legacy + appended; audit/accounting)."""
        return sum(
            r.n_nominal_trials
            for r in self.records()
            if family is None or r.family == family
        )

    def cumulative_effective_trials(self, *, family: str | None = None) -> int:
        """Cumulative EFFECTIVE trial count — legacy grids (nominal) + each
        appended batch's ONC-deduplicated effective count. This is the deflation
        accumulator: a near-duplicate grid contributes its effective N, not its
        raw size, so it cannot inflate the bar beyond its independent count."""
        return sum(
            r.effective_count
            for r in self.records()
            if family is None or r.family == family
        )

    def deflation_n_trials(self, *, onc_effective_n: int) -> int:
        """The N fed to DSR/MinBTL: ``max(legacy floor + appended effective, new
        ONC N)`` (§4.1 ⑤). The legacy debt is a floor that accumulated *effective*
        trials and the current batch's ONC N can only raise, never reset — and a
        raw near-duplicate grid never inflates it (codex P2)."""
        return max(self.cumulative_effective_trials(), max(0, onc_effective_n))


__all__ = [
    "LEGACY_GRID_TOTAL",
    "TrialLedger",
    "TrialRecord",
    "legacy_block",
]
