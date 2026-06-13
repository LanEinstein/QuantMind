"""O-005 deterministic forecast calibration ledger (zero LLM).

Closes the falsification loop for the MiroFish sector forecast (O-002):
without measuring whether the LLM's uncalibrated sector probabilities are
any good, a systematically biased forecast could quietly skew the
shortlist ordering (O-003) and erode real returns. This module scores
each forecast against realized sector returns once its horizon elapses —
purely arithmetic, append-only, replayable — and feeds the trailing
hit-rate / Brier back into the next EOD forecast evidence so the owner
can decide, on data, whether to keep or disable the advisory re-rank.

Pure measurement: zero LLM, zero decision/order fields, no change to the
selector's behaviour. Scoring the same forecast against the same realized
returns is bit-exact. A forecast whose horizon has not elapsed (or whose
realized returns are unavailable) is simply skipped and retried next day.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import structlog

log = structlog.get_logger(component="mirofish.forecast_ledger")

# How many most-recent scored forecasts the trailing note summarizes.
DEFAULT_TRAILING_WINDOW = 20

# Below this many scored forecasts the note reports INSUFFICIENT_DATA
# rather than a falsely precise hit-rate.
MIN_SAMPLES_FOR_NOTE = 3


@dataclass(frozen=True)
class ForecastEntryView:
    """The fields of one forecast entry the ledger needs to score."""

    sector: str
    score: float
    probability_up: float


@dataclass(frozen=True)
class DueForecast:
    """A persisted forecast surfaced for scoring."""

    trade_date: str  # ISO YYYY-MM-DD
    horizon_days: int
    entries: tuple[ForecastEntryView, ...]


@dataclass(frozen=True)
class SectorOutcome:
    """The realized verdict for one forecasted sector."""

    sector: str
    predicted_up: bool
    realized_return: float
    actual_up: bool
    hit: bool
    brier: float  # (probability_up - actual_outcome)^2, in [0, 1]


@dataclass(frozen=True)
class ForecastOutcome:
    """The scored result of one forecast (append-only ledger row)."""

    trade_date: str  # the forecast's date
    scored_as_of: str  # the date the realized window completed
    horizon_days: int
    sectors: tuple[SectorOutcome, ...]
    hit_rate: float  # fraction of sectors whose direction was correct
    mean_brier: float  # mean Brier over sectors (lower = better calibrated)

    def to_json(self) -> dict[str, object]:
        return {
            "trade_date": self.trade_date,
            "scored_as_of": self.scored_as_of,
            "horizon_days": self.horizon_days,
            "hit_rate": self.hit_rate,
            "mean_brier": self.mean_brier,
            "sectors": [
                {
                    "sector": s.sector,
                    "predicted_up": s.predicted_up,
                    "realized_return": s.realized_return,
                    "actual_up": s.actual_up,
                    "hit": s.hit,
                    "brier": s.brier,
                }
                for s in self.sectors
            ],
        }


# Async hooks (injected so the deterministic core stays pure + testable).

class ForecastReader(Protocol):
    """Surfaces persisted forecasts with ``trade_date`` before ``as_of``."""

    async def recent_forecasts(self, as_of: str) -> Sequence[DueForecast]: ...


# (forecast_date, horizon_days, sectors, as_of) -> {sector: realized_return}
# or None when the horizon window has not fully elapsed / data is missing.
RealizedReturnProvider = Callable[
    [str, int, Sequence[str], str],
    Awaitable[Mapping[str, float] | None],
]


def score_forecast(
    forecast: DueForecast,
    realized: Mapping[str, float],
    *,
    scored_as_of: str,
) -> ForecastOutcome | None:
    """Score ``forecast`` against ``realized`` sector returns (deterministic).

    A sector is scored only when its realized return is present and finite.
    ``predicted_up`` = forecast score > 0; ``actual_up`` = realized return
    > 0; ``hit`` = the two agree; Brier = ``(probability_up - actual)^2``.
    Returns ``None`` when no sector could be scored (nothing to record).
    """
    scored: list[SectorOutcome] = []
    for entry in forecast.entries:
        ret = realized.get(entry.sector)
        if ret is None or not _finite(ret):
            continue
        predicted_up = entry.score > 0.0
        actual_up = ret > 0.0
        actual_outcome = 1.0 if actual_up else 0.0
        scored.append(
            SectorOutcome(
                sector=entry.sector,
                predicted_up=predicted_up,
                realized_return=round(float(ret), 6),
                actual_up=actual_up,
                hit=predicted_up == actual_up,
                brier=round((entry.probability_up - actual_outcome) ** 2, 6),
            )
        )
    if not scored:
        return None
    hits = sum(1 for s in scored if s.hit)
    return ForecastOutcome(
        trade_date=forecast.trade_date,
        scored_as_of=scored_as_of,
        horizon_days=forecast.horizon_days,
        sectors=tuple(scored),
        hit_rate=round(hits / len(scored), 6),
        mean_brier=round(sum(s.brier for s in scored) / len(scored), 6),
    )


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


class ForecastOutcomeStore:
    """Append-only JSONL store of scored forecast outcomes.

    One row per scored forecast (keyed by the forecast's ``trade_date``).
    Append-only: a forecast is scored at most once (``is_scored`` guards
    re-scoring so a same-day EOD re-run is idempotent).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def is_scored(self, trade_date: str) -> bool:
        return trade_date in self._scored_dates()

    def append(self, outcome: ForecastOutcome) -> None:
        if self.is_scored(outcome.trade_date):
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(outcome.to_json(), ensure_ascii=False, sort_keys=True)
                    + "\n"
                )
        except Exception as exc:  # noqa: BLE001 — ledger write best-effort
            log.warning(
                "forecast_outcome_append_failed",
                trade_date=outcome.trade_date,
                error=str(exc),
            )

    def recent(self, n: int) -> list[ForecastOutcome]:
        rows = self._read_rows()
        return [_outcome_from_json(r) for r in rows[-n:]]

    def _scored_dates(self) -> set[str]:
        return {str(r.get("trade_date", "")) for r in self._read_rows()}

    def _read_rows(self) -> list[dict[str, object]]:
        if not self._path.is_file():
            return []
        rows: list[dict[str, object]] = []
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
        except Exception as exc:  # noqa: BLE001 — corrupt tail never crashes
            log.warning("forecast_outcome_read_failed", error=str(exc))
        return rows


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _outcome_from_json(row: Mapping[str, object]) -> ForecastOutcome:
    sectors = row.get("sectors")
    sector_views: list[SectorOutcome] = []
    if isinstance(sectors, list):
        for s in sectors:
            if not isinstance(s, dict):
                continue
            sector_views.append(
                SectorOutcome(
                    sector=str(s.get("sector", "")),
                    predicted_up=bool(s.get("predicted_up", False)),
                    realized_return=_as_float(s.get("realized_return")),
                    actual_up=bool(s.get("actual_up", False)),
                    hit=bool(s.get("hit", False)),
                    brier=_as_float(s.get("brier")),
                )
            )
    return ForecastOutcome(
        trade_date=str(row.get("trade_date", "")),
        scored_as_of=str(row.get("scored_as_of", "")),
        horizon_days=_as_int(row.get("horizon_days")),
        sectors=tuple(sector_views),
        hit_rate=_as_float(row.get("hit_rate")),
        mean_brier=_as_float(row.get("mean_brier")),
    )


def trailing_note(
    outcomes: Sequence[ForecastOutcome],
    *,
    min_samples: int = MIN_SAMPLES_FOR_NOTE,
) -> str:
    """One-line trailing calibration summary for the next forecast evidence.

    Reports INSUFFICIENT_DATA below ``min_samples`` rather than a falsely
    precise hit-rate. Aggregates over the per-sector verdicts of the
    supplied outcomes (each sector is one prediction).
    """
    sector_outcomes = [s for o in outcomes for s in o.sectors]
    n = len(sector_outcomes)
    if n < min_samples:
        return f"INSUFFICIENT_DATA (已评 {len(outcomes)} 份/{n} 预测)"
    hits = sum(1 for s in sector_outcomes if s.hit)
    mean_brier = sum(s.brier for s in sector_outcomes) / n
    return (
        f"近 {len(outcomes)} 份预测方向命中率 {hits / n:.0%} "
        f"({hits}/{n}),平均 Brier {mean_brier:.3f}"
    )


class ForecastLedger:
    """Scores due forecasts + returns the trailing calibration note (O-005)."""

    def __init__(
        self,
        *,
        forecast_reader: ForecastReader,
        realized_return_provider: RealizedReturnProvider,
        outcome_store: ForecastOutcomeStore,
        trailing_window: int = DEFAULT_TRAILING_WINDOW,
    ) -> None:
        self._reader = forecast_reader
        self._returns = realized_return_provider
        self._store = outcome_store
        self._trailing_window = trailing_window
        self._log = log

    async def score_due_and_summarize(self, as_of: str) -> str:
        """Score any newly-due forecasts, then return the trailing note.

        Never raises: a reader / return-provider failure degrades to "score
        nothing this run"; the note still reflects whatever is on file.
        """
        try:
            due = await self._reader.recent_forecasts(as_of)
        except Exception as exc:  # noqa: BLE001 — scoring never blocks the EOD
            self._log.warning("forecast_reader_failed", error=str(exc))
            due = ()
        # Append oldest → newest so the append-only tail (and thus
        # ``recent(n)``'s trailing window) keeps the NEWEST scored forecasts
        # when a backlog is caught up in one run (codex O-005 P3).
        for forecast in sorted(due, key=lambda f: f.trade_date):
            if self._store.is_scored(forecast.trade_date):
                continue
            try:
                realized = await self._returns(
                    forecast.trade_date,
                    forecast.horizon_days,
                    [e.sector for e in forecast.entries],
                    as_of,
                )
            except Exception as exc:  # noqa: BLE001 — per-forecast degrade
                self._log.warning(
                    "realized_return_failed",
                    trade_date=forecast.trade_date,
                    error=str(exc),
                )
                continue
            if not realized:
                continue  # horizon not elapsed / data missing → retry next day
            outcome = score_forecast(
                forecast, realized, scored_as_of=as_of
            )
            if outcome is not None:
                self._store.append(outcome)
                self._log.info(
                    "forecast_scored",
                    trade_date=forecast.trade_date,
                    hit_rate=outcome.hit_rate,
                    mean_brier=outcome.mean_brier,
                )
        return trailing_note(self._store.recent(self._trailing_window))


__all__ = [
    "DEFAULT_TRAILING_WINDOW",
    "MIN_SAMPLES_FOR_NOTE",
    "DueForecast",
    "ForecastEntryView",
    "ForecastLedger",
    "ForecastOutcome",
    "ForecastOutcomeStore",
    "ForecastReader",
    "RealizedReturnProvider",
    "SectorOutcome",
    "score_forecast",
    "trailing_note",
]
