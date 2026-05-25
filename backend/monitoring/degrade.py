"""Line-2 clean degradation — suspension handling + anomaly-LLM trigger keys.

Two N-004 concerns, both deterministic (zero LLM in the decision path):

1. **Suspension → clean degrade**. A halted (停牌) held position must NOT
   produce a SELL/ADD order that would only bounce — instead it degrades
   *cleanly*: it is partitioned out of the active scan set and recorded as a
   :class:`PositionDegrade`. Suspension is read from the spot
   :class:`WatchlistMarketSnapshot` via the pure ``backend.data.suspension``
   detector (R0 §8 / ``backend/monitoring/CLAUDE.md`` red line 5).

2. **Anomaly-LLM trigger key**. Line-2 polling is pure-quant; an LLM fires
   only on a *deduplicated* trigger bounded by a daily cap, reserving on the
   unified ``llm:usage`` counter. This module owns the stable per-day trigger
   key ``{code}:{kind}``; the dedup + cap + reservation live in
   ``cost_guard.reserve_anomaly_llm_slot`` (the only ¥20-budget authority).
   The actual LLM call is orchestrated OUTSIDE this module so monitoring stays
   free of any ``backend.{llm,agents,mirofish}`` import.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

import structlog

# backend.data is a legitimate Line-2 dependency (suspension detector + the
# WatchlistMarketSnapshot model). The per-line noqa keeps the global TID251 ban
# ACTIVE for backend.{llm,agents,mirofish} — this module's own red line.
from backend.data.suspension import is_suspended  # noqa: TID251
from backend.models.market import WatchlistMarketSnapshot
from backend.monitoring.anomaly import AnomalyKind, AnomalySignal

log = structlog.get_logger(component="monitoring.degrade")


class DegradeReason(StrEnum):
    """Why a held position degraded cleanly (no order produced)."""

    SUSPENDED = "suspended"


@dataclass(frozen=True)
class PositionDegrade:
    """A held position that degraded cleanly — recorded, never routed."""

    code: str
    reason: DegradeReason
    detail: str = ""


@dataclass(frozen=True)
class SuspensionPartition:
    """Held codes split into tradable (scan) vs cleanly-degraded (suspended)."""

    active_codes: tuple[str, ...]
    degrades: tuple[PositionDegrade, ...]


def partition_by_suspension(
    held_codes: Iterable[str],
    spot_by_code: Mapping[str, WatchlistMarketSnapshot],
) -> SuspensionPartition:
    """Split held codes into active vs cleanly-degraded (suspended) sets.

    A held code whose spot snapshot :func:`is_suspended` is removed from the
    active scan set and recorded as a :class:`PositionDegrade` — Line-2 never
    attempts a SELL/ADD on a halted instrument (it would only bounce). A held
    code with **no** spot snapshot stays active: a missing quote is the
    DataQualityState freeze's concern (it fail-closes the order downstream),
    not a confirmed suspension — only a CONFIRMED halt degrades here.
    """
    active: list[str] = []
    degrades: list[PositionDegrade] = []
    for code in sorted({c.split(".")[0].strip() for c in held_codes}):
        spot = spot_by_code.get(code)
        if spot is not None and is_suspended(spot):
            degrades.append(
                PositionDegrade(
                    code=code,
                    reason=DegradeReason.SUSPENDED,
                    detail="halted instrument — clean degrade, no SELL/ADD order",
                )
            )
        else:
            active.append(code)
    log.info(
        "suspension_partition",
        active=len(active),
        degraded=len(degrades),
    )
    return SuspensionPartition(active_codes=tuple(active), degrades=tuple(degrades))


def anomaly_trigger_key(signal: AnomalySignal) -> str:
    """Stable per-day dedup key for an anomaly-triggered LLM enrichment call.

    ``{code}:{kind}`` — one optional LLM explanation per (code, detector) per
    UTC day. Direction / score are intentionally excluded so a flapping score
    does not defeat the dedup. Consumed by
    ``cost_guard.reserve_anomaly_llm_slot``.
    """
    kind = (
        signal.kind
        if isinstance(signal.kind, AnomalyKind)
        else AnomalyKind(signal.kind)
    )
    return f"{signal.code}:{kind.value}"


__all__ = [
    "DegradeReason",
    "PositionDegrade",
    "SuspensionPartition",
    "anomaly_trigger_key",
    "partition_by_suspension",
]
