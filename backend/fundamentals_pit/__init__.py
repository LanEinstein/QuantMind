"""Backend point-in-time financial-statement reader (AF-002).

A backend-resident, import-isolated mirror of the research-side
``scripts/factor_research/statements_pit.py`` PIT vintage reader (the research
module lives under ``scripts/`` and must not be imported by ``backend/``). It
reads the period-keyed Tushare statement snapshots from the
:class:`~backend.marketdata_snapshot.store.SnapshotStore` and exposes the
earnings-quality metric records the AF-003 ``quality_fundamentals`` composite
consumes — strictly PIT (announcement-date keyed), deterministic, 0 LLM.
"""

from __future__ import annotations

from backend.fundamentals_pit.reader import (
    BackendStatementPIT,
    StatementVintage,
    quality_metric_records,
    recent_quarter_ends,
)

__all__ = [
    "BackendStatementPIT",
    "StatementVintage",
    "quality_metric_records",
    "recent_quarter_ends",
]
