"""Value-score assembler — three foundations → live ``value_scores`` (AF-002).

Deterministic, pure, 0 LLM. Given a candidate cross-section + a decision date it
assembles each code's :class:`ValueScoreInputs` from:

* ``theme_coverage`` — AF-001 frozen :class:`ThemeResolver` (tier-weighted, or
  ``None`` on a data gap → dropped);
* ``fundamentals_score`` — AF-003 :func:`fundamentals_scores` fed by the AF-002
  PIT statement reader (ROE / GPM / accruals);
* ``valuation_score`` — AF-002 cheapness factor (dividend / PE / PB);

then runs :func:`compute_value_score`. The mid-tier (CAR / capacity / flow) and
the resonance/elasticity surface components are left ``None`` here — they need
the live event-study offsets + knowledge-graph resonance the offline assembler
does not have, so they are conservatively dropped rather than fabricated (a name
clears the value gate on theme + quality + cheapness, then AF-004 adds the
bottom-confirmation entry gate). Replays bit-exact from the same store snapshots.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from backend.fundamentals_pit.reader import (
    quality_metric_records,
    recent_quarter_ends,
)
from backend.marketdata_snapshot.store import SnapshotStore
from backend.quality_fundamentals.quality import fundamentals_scores
from backend.screening.value_score import (
    ValueScoreInputs,
    ValueScoreWeights,
    compute_value_score,
)
from backend.theme_mapping.resolver import ThemeResolver
from backend.value_assembly.valuation import valuation_scores

DEFAULT_STATEMENT_PERIODS = 8
"""Trailing quarter-ends read for the PIT fundamentals vintage (~2 years)."""


@runtime_checkable
class EntryGate(Protocol):
    """AF-004 bottom-confirmation hook (a code must be a confirmed bottom).

    ``confirmed`` is deterministic + PIT. When an :class:`EntryGate` is supplied
    and a code is NOT confirmed, the assembler forces its value score to 0.0 so a
    chasing / un-confirmed name can never clear the value gate (提前埋伏, not
    接飞刀). AF-002 ships no gate (every code keeps its composite); AF-004 plugs
    one in without touching the composite maths.
    """

    def confirmed(self, code: str, decision_date: str) -> bool: ...


@dataclass(frozen=True)
class ValueScoreAssembler:
    """Assemble live ``value_scores`` from the three value foundations."""

    store: SnapshotStore
    resolver: ThemeResolver
    weights: ValueScoreWeights = field(default_factory=ValueScoreWeights)
    n_statement_periods: int = DEFAULT_STATEMENT_PERIODS
    entry_gate: EntryGate | None = None

    def assemble(
        self, *, codes: Sequence[str], decision_date: str
    ) -> dict[str, float] | None:
        """Per-code three-tier value score ∈ [0, 1], or ``None`` on no signal.

        ``decision_date`` is the YYYYMMDD Line-1 frame trade date. Returns
        ``None`` when NO code yields a real value signal — a total value-data
        outage, or every code rejected by the AF-004 entry gate (no confirmed
        bottom today). ``None`` makes the runner fall back to the pure-quant
        selection path, BIT-IDENTICAL to the no-sleeve behaviour (a non-None map
        of all-0.0 would instead engage the value-selection ordering, codex
        AF-002 P2). Otherwise every code maps to a finite score (0.0 for a code
        with no present component or one the entry gate rejected).
        """
        code_list = list(dict.fromkeys(codes))
        theme = {
            code: self.resolver.theme_coverage(code, decision_date)
            for code in code_list
        }
        periods = recent_quarter_ends(decision_date, self.n_statement_periods)
        records = quality_metric_records(self.store, codes=code_list, periods=periods)
        fundamentals = fundamentals_scores(records, decision_date)
        valuation = valuation_scores(
            self.store, codes=code_list, decision_date=decision_date
        )

        out: dict[str, float] = {}
        any_signal = False
        for code in code_list:
            if self.entry_gate is not None and not self.entry_gate.confirmed(
                code, decision_date
            ):
                # Not a confirmed bottom → never value-eligible (提前埋伏 gate).
                out[code] = 0.0
                continue
            inputs = ValueScoreInputs(
                theme_coverage=theme.get(code),
                fundamentals_score=fundamentals.get(code),
                valuation_score=valuation.get(code),
            )
            result = compute_value_score(inputs, self.weights)
            out[code] = result.value_score
            if result.components_present:
                any_signal = True
        # No usable value signal anywhere → fall back to the pure-quant path.
        return out if any_signal else None


__all__ = ["EntryGate", "ValueScoreAssembler", "DEFAULT_STATEMENT_PERIODS"]
