"""Pure quote-staleness scoring used by :mod:`backend.data.data_quality`.

P0-8 §1.1.3 locks the staleness signal as a per-call evaluation against a
single threshold (``staleness_threshold_seconds=5`` by default). The
function intentionally returns a :class:`StalenessReport` (frozen
dataclass) instead of a bare ``bool`` so the DataQualityProvider can
surface the actual age in :pyattr:`DataQualityState.primary_quote_age_seconds`
/ :pyattr:`backup_quote_age_seconds` for the InstructionPlanBuilder's
HOLD reason payload.

This module is part of the data-quality boundary (P0-8 §2 redline 8,
P1-2.B §2 redline 8): no ``backend.llm`` / ``backend.agents`` /
``backend.risk`` imports, no IO, no logging — just arithmetic on
``datetime`` objects. Naive-vs-aware datetimes raise ``ValueError`` so
a tz-mix bug surfaces eagerly instead of producing a silent negative age.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StalenessReport:
    """Pure result of one staleness comparison.

    Attributes:
        quote_source: Provenance tag matching ``WatchlistMarketSnapshot.source``
            (``"adata"`` / ``"akshare"`` / ``"unknown"``).
        snapshot_at: The vendor-reported timestamp on the quote being judged.
        now: The "wall-clock" used for the age subtraction.
        age_seconds: ``(now - snapshot_at).total_seconds()``. May be negative
            for a clock-skewed future-dated quote — DataQualityProvider treats
            negative ages as ``is_stale=False`` (a quote from the future is
            fresh enough by construction) but surfaces the raw seconds for
            audit.
        threshold_seconds: The threshold this evaluation used (P0-8 default 5).
        is_stale: ``age_seconds > threshold_seconds``.
    """

    quote_source: str
    snapshot_at: datetime
    now: datetime
    age_seconds: float
    threshold_seconds: float
    is_stale: bool


def evaluate_staleness(
    *,
    snapshot_at: datetime,
    now: datetime,
    quote_source: str,
    threshold_seconds: float,
) -> StalenessReport:
    """Score a single quote's freshness against ``threshold_seconds``.

    Both ``snapshot_at`` and ``now`` must agree on tz-awareness (both
    aware or both naive). Mixing one of each raises ``ValueError`` so a
    drift between the scheduler (UTC) and a downstream consumer
    (Asia/Shanghai) never silently fails the comparison.

    Args:
        snapshot_at: Vendor timestamp on the quote.
        now: Evaluation wall-clock.
        quote_source: Source tag, copied through to the report.
        threshold_seconds: Maximum acceptable age. P0-8 locks the
            default at 5 for ``adata`` primary.

    Returns:
        StalenessReport: Result with ``is_stale`` and the raw age for audit.
    """
    snap_aware = snapshot_at.tzinfo is not None
    now_aware = now.tzinfo is not None
    if snap_aware != now_aware:
        raise ValueError(
            "evaluate_staleness requires snapshot_at and now to agree on "
            f"tz-awareness (snapshot_at aware={snap_aware}, now aware={now_aware})"
        )

    age = (now - snapshot_at).total_seconds()
    return StalenessReport(
        quote_source=quote_source,
        snapshot_at=snapshot_at,
        now=now,
        age_seconds=age,
        threshold_seconds=threshold_seconds,
        is_stale=age > threshold_seconds,
    )


__all__ = ["StalenessReport", "evaluate_staleness"]
