"""PIT-clean crash/risk regime detector for the batch-B de-risk overlays.

A deterministic, look-ahead-free market-regime classifier driven by the CSI300
ETF (``510300.SH``; ``index_daily`` is not ingested) read from the K-002 PIT
store. Each trading day ``T``'s regime uses **only closes on/before ``T``** — the
trailing drawdown from a trailing peak and the trailing realised volatility — so
the schedule that gates de-risking on date ``T`` (filling on ``T+1``) never reads
a future bar (main-force-intent §2.1/§2.3; lowbase-transition §6.5).

The thresholds (``DD_THRESHOLD`` / ``PEAK_LOOKBACK`` / ``VOL_*``) are
**pre-committed** (batch-B1 spec §2): regime parameters are an overfitting DOF
minefield, so they are committed up front and NEVER tuned on results (qgr-2 §4 —
"gates never guide the search"). The headline detector is the single-parameter
trailing drawdown; the volatility variant is disclosure-only.

Pure functions of injected closes (the one IO — reading the PIT store — is a thin
reader at the edge); no wall-clock, no RNG. Never imports the live path.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.data.historical_ingest.job import VENDOR
from backend.data.historical_ingest.serialization import parse_csv_bytes
from backend.marketdata_snapshot.store import SnapshotStore

CSI300_ETF: str = "510300.SH"
# Pre-committed regime thresholds (spec §2; NEVER tuned on results).
DD_THRESHOLD: float = -0.10  # trailing drawdown from peak ≤ −10% ⇒ crash regime
PEAK_LOOKBACK: int = 60  # trailing trading days the peak is taken over (incl. T)
VOL_WINDOW: int = 20  # trailing window for realised vol (disclosure variant)
VOL_THRESHOLD: float = 0.30  # annualised realised vol ≥ 30% (disclosure variant)
TRADING_DAYS_PER_YEAR: int = 252


@dataclass(frozen=True)
class RegimeState:
    """One trading day's deterministic, look-ahead-free market-regime read."""

    day: str
    close: float
    drawdown_from_peak: float
    realized_vol_annualized: float
    high_risk: bool
    """Headline pre-committed detector: trailing drawdown ≤ ``DD_THRESHOLD``."""
    high_risk_vol_variant: bool
    """Disclosure variant: ``high_risk`` OR realised vol ≥ ``VOL_THRESHOLD``."""


def read_market_closes(
    store: SnapshotStore,
    trading_days: Sequence[str],
    *,
    market_code: str = CSI300_ETF,
) -> dict[str, float]:
    """``{day: raw_close}`` for ``market_code`` across ``trading_days`` (PIT).

    Raw (un-adjusted) closes are the standard market-regime signal — drawdown and
    volatility are ratio/return statistics, so the ETF's flat split factor is
    irrelevant. **Caveat (codex PLAUSIBLE #2):** an ETF cash distribution drops the
    raw close ~1–2% on its ex-date, a one-off the trailing window forgets within
    ``PEAK_LOOKBACK`` days; the headline −10% drawdown gate is robust to a sub-2%
    dividend drop (it only matters if the market is already within ~1.5% of the
    threshold), and no distribution-adjusted ETF series exists in the PIT store
    (ETFs carry no ``adj_factor``). The vol variant is more sensitive but is
    disclosure-only. A day with no row for the code is omitted (the classifier
    carries the trailing window forward over the present days only).
    """
    out: dict[str, float] = {}
    for day in trading_days:
        snap = store.latest(vendor=VENDOR, endpoint="fund_daily", trade_date=day)
        if snap is None:
            continue
        df = parse_csv_bytes(snap.raw_payload)
        row = df.loc[df["ts_code"].astype(str) == market_code]
        if row.empty:
            continue
        try:
            close = float(str(row.iloc[0]["close"]).strip())
        except (ValueError, TypeError):
            continue
        if math.isfinite(close) and close > 0:
            out[day] = close
    return out


def _trailing_drawdown(closes: Sequence[float], peak_lookback: int) -> float:
    """Drawdown of the last close from the trailing ``peak_lookback`` peak (≤0)."""
    window = closes[-peak_lookback:]
    peak = max(window)
    if peak <= 0:
        return 0.0
    return window[-1] / peak - 1.0


def _trailing_realized_vol(closes: Sequence[float], vol_window: int) -> float:
    """Annualised std of trailing daily log returns over ``vol_window`` (≥0)."""
    window = closes[-(vol_window + 1) :]
    if len(window) < 2:
        return 0.0
    rets = [
        math.log(window[i] / window[i - 1])
        for i in range(1, len(window))
        if window[i] > 0 and window[i - 1] > 0
    ]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)


def classify_regimes(
    closes_by_day: Mapping[str, float],
    *,
    dd_threshold: float = DD_THRESHOLD,
    peak_lookback: int = PEAK_LOOKBACK,
    vol_window: int = VOL_WINDOW,
    vol_threshold: float = VOL_THRESHOLD,
) -> dict[str, RegimeState]:
    """``{day: RegimeState}`` — each day classified from ONLY ≤-that-day closes.

    A single forward pass keeps the running close history so every day's drawdown
    / volatility uses a trailing window of present-and-prior closes; a future
    close can never enter the statistic (causal by construction — unit-tested).
    """
    history: list[float] = []
    out: dict[str, RegimeState] = {}
    for day in sorted(closes_by_day):
        history.append(closes_by_day[day])
        dd = _trailing_drawdown(history, peak_lookback)
        vol = _trailing_realized_vol(history, vol_window)
        high_risk = dd <= dd_threshold
        out[day] = RegimeState(
            day=day,
            close=history[-1],
            drawdown_from_peak=dd,
            realized_vol_annualized=vol,
            high_risk=high_risk,
            high_risk_vol_variant=high_risk or vol >= vol_threshold,
        )
    return out


def high_risk_dates(
    regimes: Mapping[str, RegimeState],
    candidate_dates: Sequence[str],
    *,
    use_vol_variant: bool = False,
) -> tuple[str, ...]:
    """The subset of ``candidate_dates`` flagged high-risk (ascending).

    ``candidate_dates`` are the rebalance dates the de-risk schedule may treat; a
    date with no regime read (market series gap) is treated as not-high-risk
    (fail-open to "do not de-risk" — a missing market bar must not silently force
    a cash rotation). The vol variant is disclosure-only (spec §2).
    """
    out: list[str] = []
    for d in sorted(set(candidate_dates)):
        state = regimes.get(d)
        if state is None:
            continue
        flag = state.high_risk_vol_variant if use_vol_variant else state.high_risk
        if flag:
            out.append(d)
    return tuple(out)


__all__ = [
    "CSI300_ETF",
    "DD_THRESHOLD",
    "PEAK_LOOKBACK",
    "VOL_THRESHOLD",
    "VOL_WINDOW",
    "RegimeState",
    "classify_regimes",
    "high_risk_dates",
    "read_market_closes",
]
