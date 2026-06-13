"""O-003 forecast → advisory provider (orchestration seam).

Bridges the persisted MiroFish sector forecast (O-002, a
``MIROFISH-FORECAST-`` evidence row) into the Line-1
:class:`CandidateSelector` bounded re-rank, without letting the selector
import the MiroFish/LLM stacks.

PIT discipline: a run on trade day ``T`` consumes the most recent
forecast whose ``trade_date`` is strictly **before** ``T`` (the T-1
17:00 EOD forecast is fully observable at the 09:35 selection) — never a
same-day or future forecast.

Fail-open by construction: any gap (no forecast yet, malformed payload,
Mongo error, missing industry map) yields ``None`` so
:meth:`CandidateSelector.select` runs the pure-quant path. MiroFish can
only reorder an already-qualified set, never change its membership
(red-line: removing MiroFish must not change the qualified set).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

import structlog

from backend.candidate_selector.advisory_mapping import build_advisory_signals
from backend.candidate_selector.selector import AdvisorySignal

log = structlog.get_logger(component="orchestration.forecast_advisory")

# Cap on how stale a forecast may be before it is ignored (trading days
# of look-back). A forecast older than this is treated as absent so a
# stalled EOD pipeline cannot steer selection with a week-old view.
MAX_FORECAST_AGE_DAYS = 5

IndustryMapLoader = Callable[[str], Awaitable[Mapping[str, str]]]
"""Async loader: a forecast's trade_date → the PIT-pinned code→sector map
that was used to build that forecast (co-dated for replay reproducibility)."""


class _EvidenceReader(Protocol):
    def __getitem__(self, name: str) -> Any: ...


def _normalize_code(code: str) -> str:
    """Bare 6-digit form so forecast/industry/candidate codes join cleanly."""
    return code.split(".", 1)[0].strip()


def _parse_sector_scores(payload: Any) -> dict[str, float]:
    """Extract ``{sector: score}`` from a forecast evidence payload.

    Fail-closed: a malformed payload / entry yields no scores for that
    entry (never raises). Only finite scores in [-1, 1] survive (the
    mapping helper re-validates, but we keep the provider self-defending).
    """
    if not isinstance(payload, Mapping):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return {}
    scores: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        sector = entry.get("sector")
        score = entry.get("score")
        if not isinstance(sector, str) or not sector:
            continue
        if not isinstance(score, int | float) or isinstance(score, bool):
            continue
        scores[sector] = float(score)
    return scores


class ForecastAdvisoryProvider:
    """Reads the latest T-1 MiroFish forecast → per-code advisory signals."""

    def __init__(
        self,
        *,
        mongodb: Any,
        industry_map_loader: IndustryMapLoader,
        max_age_days: int = MAX_FORECAST_AGE_DAYS,
    ) -> None:
        self._mongodb = mongodb
        self._industry_map_loader = industry_map_loader
        self._max_age_days = max_age_days
        self._log = log

    async def __call__(
        self, codes: Sequence[str], *, trade_date: str
    ) -> Sequence[AdvisorySignal] | None:
        """Resolve advisory signals for ``codes`` on selection day ``trade_date``.

        Returns ``None`` (pure-quant fallback) on any gap. ``trade_date``
        is the selection day (``T``); the consumed forecast must be from
        strictly before it. The code→sector map is loaded co-dated with
        the chosen forecast (PIT-pinned), so a replay re-derives the same
        signals even if live industry labels later drift.
        """
        if self._mongodb is None or not codes:
            return None
        doc = await self._latest_forecast_doc(trade_date)
        if doc is None:
            return None
        forecast_date = str(doc.get("trade_date", ""))
        sector_scores = _parse_sector_scores(doc.get("forecast"))
        if not sector_scores:
            return None
        try:
            raw_map = await self._industry_map_loader(forecast_date)
        except Exception as exc:  # noqa: BLE001 — fail-open to pure quant
            self._log.warning("forecast_sector_map_failed", error=str(exc))
            return None
        if not raw_map:
            return None
        sector_by_code = {
            _normalize_code(code): sector for code, sector in raw_map.items()
        }
        signals = build_advisory_signals(
            sector_scores=sector_scores,
            sector_by_code=sector_by_code,
            candidate_codes=[_normalize_code(c) for c in codes],
        )
        # Map the bare-code signals back to the caller's original code form.
        bare_to_original = {_normalize_code(c): c for c in codes}
        remapped = tuple(
            AdvisorySignal(
                code=bare_to_original.get(s.code, s.code),
                advisory_score=s.advisory_score,
            )
            for s in signals
        )
        if not remapped:
            return None
        self._log.info(
            "forecast_advisory_built",
            trade_date=trade_date,
            signals=len(remapped),
            sectors=len(sector_scores),
        )
        return remapped

    async def _latest_forecast_doc(self, trade_date: str) -> Any:
        """Most recent forecast doc with ``trade_date`` strictly before T."""
        try:
            coll = self._mongodb._db["evidence_collection"]  # noqa: SLF001
        except Exception:  # noqa: BLE001 — fail-open
            return None
        try:
            cursor = (
                coll.find(
                    {
                        "path": "sector_forecast",
                        "trade_date": {"$lt": trade_date},
                    }
                )
                .sort("trade_date", -1)
                .limit(1)
            )
            docs = await cursor.to_list(length=1)
        except Exception as exc:  # noqa: BLE001 — fail-open to pure quant
            self._log.warning("forecast_query_failed", error=str(exc))
            return None
        if not docs:
            return None
        doc = docs[0]
        if not self._within_age(str(doc.get("trade_date", "")), trade_date):
            self._log.info(
                "forecast_too_stale",
                forecast_date=doc.get("trade_date"),
                trade_date=trade_date,
            )
            return None
        return doc

    def _within_age(self, forecast_date: str, trade_date: str) -> bool:
        """True if ``forecast_date`` is within ``max_age_days`` calendar days.

        Calendar-day proxy (not a trading-calendar diff) — generous enough
        that a normal T-1 forecast always passes and only a multi-day
        stall is rejected. Malformed dates fail-closed to "too stale".
        """
        import datetime as dt

        try:
            f = dt.date.fromisoformat(forecast_date)
            t = dt.date.fromisoformat(trade_date)
        except ValueError:
            return False
        delta = (t - f).days
        return 0 < delta <= self._max_age_days


__all__ = [
    "MAX_FORECAST_AGE_DAYS",
    "ForecastAdvisoryProvider",
    "IndustryMapLoader",
]
