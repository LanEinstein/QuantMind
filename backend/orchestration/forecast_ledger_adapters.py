"""O-005 forecast-ledger adapters (orchestration seam).

Wires the deterministic :class:`ForecastLedger` to live data sources:

* :class:`MongoForecastReader` — surfaces recent ``MIROFISH-FORECAST``
  evidence rows (with their typed payload) as :class:`DueForecast`.
* :func:`make_realized_return_provider` — sums each forecasted sector's
  pinned daily returns over the forecast's horizon **trading-day** window;
  returns ``None`` until the window has fully elapsed (so a forecast is
  scored exactly once, the day its horizon completes) or when any day in
  the window is missing from the pinned store.

Both are read-only and deterministic given the pinned artifacts; the
ledger they feed performs pure arithmetic. Nothing here is a decision.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import structlog

from backend.data.trading_calendar import next_trading_day  # noqa: TID251
from backend.mirofish.forecast_ledger import (  # noqa: TID251
    DueForecast,
    ForecastEntryView,
    RealizedReturnProvider,
)

log = structlog.get_logger(component="orchestration.forecast_ledger_adapters")

# How many recent forecasts to surface for scoring each EOD run. Generous
# enough to catch any not-yet-scored forecast within a normal horizon +
# holiday slack, bounded so the query stays cheap.
_RECENT_FORECAST_LIMIT = 15


def _parse_entries(payload: Any) -> tuple[ForecastEntryView, ...]:
    if not isinstance(payload, Mapping):
        return ()
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return ()
    out: list[ForecastEntryView] = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        sector = item.get("sector")
        score = item.get("score")
        prob = item.get("probability_up")
        if not isinstance(sector, str) or not sector:
            continue
        if not _is_num(score) or not _is_num(prob):
            continue
        out.append(
            ForecastEntryView(
                sector=sector,
                score=float(score),  # type: ignore[arg-type]
                probability_up=float(prob),  # type: ignore[arg-type]
            )
        )
    return tuple(out)


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


class MongoForecastReader:
    """Reads recent forecast evidence rows as :class:`DueForecast`."""

    def __init__(self, *, mongodb: Any) -> None:
        self._mongodb = mongodb
        self._log = log

    async def recent_forecasts(self, as_of: str) -> Sequence[DueForecast]:
        if self._mongodb is None:
            return ()
        try:
            coll = self._mongodb._db["evidence_collection"]  # noqa: SLF001
        except Exception:  # noqa: BLE001 — fail-open
            return ()
        try:
            cursor = (
                coll.find(
                    {"path": "sector_forecast", "trade_date": {"$lt": as_of}}
                )
                .sort("trade_date", -1)
                .limit(_RECENT_FORECAST_LIMIT)
            )
            docs = await cursor.to_list(length=_RECENT_FORECAST_LIMIT)
        except Exception as exc:  # noqa: BLE001 — fail-open
            self._log.warning("forecast_reader_query_failed", error=str(exc))
            return ()
        out: list[DueForecast] = []
        for doc in docs:
            payload = doc.get("forecast")
            entries = _parse_entries(payload)
            if not entries:
                continue
            horizon = (
                payload.get("horizon_days") if isinstance(payload, Mapping) else None
            )
            if not isinstance(horizon, int) or horizon <= 0:
                continue
            out.append(
                DueForecast(
                    trade_date=str(doc.get("trade_date", "")),
                    horizon_days=horizon,
                    entries=entries,
                )
            )
        return out


def make_realized_return_provider(
    load_sector_returns: Callable[[str], Mapping[str, float]],
) -> RealizedReturnProvider:
    """Build a realized-return provider over pinned daily sector returns.

    ``load_sector_returns(trade_date)`` returns the pinned
    ``{sector: daily_pct_chg}`` for one day (``{}`` if absent). The
    provider sums each requested sector's daily return over the ``horizon``
    trading days AFTER the forecast date; it returns ``None`` until that
    whole window has elapsed by ``as_of`` or when any day in the window is
    missing — so a forecast is scored exactly once, deterministically.
    """

    async def _provider(
        forecast_date: str,
        horizon_days: int,
        sectors: Sequence[str],
        as_of: str,
    ) -> Mapping[str, float] | None:
        try:
            start = dt.date.fromisoformat(forecast_date)
            as_of_date = dt.date.fromisoformat(as_of)
        except ValueError:
            return None
        # The horizon window = the next ``horizon_days`` TRADING days after
        # the forecast date.
        window: list[str] = []
        day = start
        for _ in range(horizon_days):
            day = next_trading_day(day)
            window.append(day.isoformat())
        if not window:
            return None
        # Window must be fully in the past relative to as_of (inclusive of
        # its last day) — else the horizon has not elapsed yet.
        if dt.date.fromisoformat(window[-1]) > as_of_date:
            return None
        totals: dict[str, float] = {s: 0.0 for s in sectors}
        present_days: dict[str, int] = {s: 0 for s in sectors}
        for d in window:
            day_returns = load_sector_returns(d)
            if not day_returns:
                return None  # a missing whole day → cannot score yet (retry)
            for s in sectors:
                if s in day_returns:
                    totals[s] += float(day_returns[s])
                    present_days[s] += 1
        # Only return a sector whose return was present on EVERY day of the
        # window — a sector absent on any day is EXCLUDED (not zero-filled),
        # so the ledger records no false hit/Brier for it (codex O-005 P2).
        n = len(window)
        return {s: totals[s] for s in sectors if present_days[s] == n}

    return _provider


RealizedReturnFn = Callable[
    [str, int, Sequence[str], str], Awaitable[Mapping[str, float] | None]
]


__all__ = [
    "MongoForecastReader",
    "RealizedReturnFn",
    "make_realized_return_provider",
]
