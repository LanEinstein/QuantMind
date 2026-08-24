"""The frozen right-side-wave rule: features, fillability, one replay.

Kept apart from the study driver so the entry chain, the activation ledger and
the exit resolution can be tested on tiny hand-built fixtures without loading a
panel.  Every convention is frozen in
`docs/research/yeren-system/m3-right-side-wave-preregistration-2026-08-23.md`.

Research-only.  No playbook, simulator order, broker instruction, or execution
service is created.  `real_broker_orders=False` always.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from scripts.yeren_research.m3_520 import _rolling_mean
from scripts.yeren_research.m3_520_candidate_e import CostModel
from scripts.yeren_research.pit_limit_panel import blocks_buy, blocks_sell
from scripts.yeren_research.pit_priced_panel import PricedSeries

# Statutory daily price-limit regimes (preregistration section 4, L1).  These
# are exchange rules with effective dates, not tuned parameters.
CHINEXT_20PCT_FROM_DATE = 20200824
STAR_PREFIXES = ("688", "689")
CHINEXT_PREFIXES = ("300", "301")
MAIN_BOARD_LIMIT = 0.10
WIDE_BOARD_LIMIT = 0.20


@dataclass(frozen=True)
class WaveSpec:
    """The frozen right-side-wave rule.  Defaults are the preregistered values."""

    short_window: int = 5
    mid_window: int = 20
    long_window: int = 30
    lookback_bars: int = 250
    range_position_max: float = 1.0 / 3.0
    activation_limit_fraction: float = 0.5
    entry_window_bars: int = 10

    def __post_init__(self) -> None:
        if not self.short_window < self.mid_window < self.long_window:
            raise ValueError("wave windows must be strictly increasing")
        if self.lookback_bars < self.long_window:
            raise ValueError("lookback must cover the longest moving average")
        if not 0.0 < self.range_position_max < 1.0:
            raise ValueError("range position threshold must sit inside (0, 1)")
        if self.entry_window_bars < 1:
            raise ValueError("entry window must span at least one bar")


@dataclass(frozen=True)
class WaveFeatures:
    """Close-time features; every boolean is computable without a future row."""

    ma_short: np.ndarray
    ma_mid: np.ndarray
    ma_long: np.ndarray
    range_position: np.ndarray
    activation: np.ndarray
    structure: np.ndarray
    pullback: np.ndarray
    exit_signal: np.ndarray


@dataclass(frozen=True)
class WaveTrade:
    """One accepted entry: adjusted-price P&L, raw-price fillability."""

    code: str
    entry_signal_date: int
    entry_date: int
    exit_signal_date: int | None
    exit_date: int
    entry_price: float
    exit_price: float
    gross_return_pct: float
    net_return_pct: float
    mae_pct: float
    holding_bars: int
    entry_index: int
    exit_index: int
    status: Literal["closed", "open_at_window_end", "no_fill_fact"]
    entry_delay_days: int
    exit_delay_days: int
    entry_limit_row_missing: bool
    exit_limit_row_missing: bool


def statutory_limit_pct(code: str, dates: np.ndarray) -> np.ndarray:
    """The daily price-limit fraction in force for this security on each bar.

    ST securities (5%) are excluded by the universe filter and Beijing-exchange
    securities (30%) by the exchange filter, so only the 10% and 20% regimes
    can reach this function.
    """

    prefix = code[:3]
    if prefix in STAR_PREFIXES:
        return np.full(len(dates), WIDE_BOARD_LIMIT)
    if prefix in CHINEXT_PREFIXES:
        return np.where(
            dates >= CHINEXT_20PCT_FROM_DATE, WIDE_BOARD_LIMIT, MAIN_BOARD_LIMIT
        )
    return np.full(len(dates), MAIN_BOARD_LIMIT)


def _rolling_extremes(values: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    rolling = pd.Series(values).rolling(window, min_periods=window)
    return rolling.min().to_numpy(), rolling.max().to_numpy()


def compute_wave_features(series: PricedSeries, spec: WaveSpec) -> WaveFeatures:
    """Turn the frozen entry/exit chain into close-time boolean arrays."""

    closes = series.adjusted_closes
    ma_short = _rolling_mean(closes, spec.short_window)
    ma_mid = _rolling_mean(closes, spec.mid_window)
    ma_long = _rolling_mean(closes, spec.long_window)
    length = len(closes)
    mid_slope = np.full(length, np.nan)
    long_slope = np.full(length, np.nan)
    mid_slope[1:] = ma_mid[1:] - ma_mid[:-1]
    long_slope[1:] = ma_long[1:] - ma_long[:-1]

    low, high = _rolling_extremes(closes, spec.lookback_bars)
    span = high - low
    with np.errstate(invalid="ignore", divide="ignore"):
        range_position = np.where(span > 0.0, (closes - low) / span, np.nan)

    # Activation: a bullish body whose gain reaches half of that day's own
    # statutory limit, struck from the bottom third of the trailing year.
    threshold_pct = (
        100.0 * spec.activation_limit_fraction * statutory_limit_pct(
            series.code, series.dates
        )
    )
    activation = (
        np.isfinite(series.pct_chg)
        & (series.pct_chg >= threshold_pct)
        & np.isfinite(series.closes)
        & np.isfinite(series.opens)
        & (series.closes > series.opens)
        & np.isfinite(range_position)
        & (range_position <= spec.range_position_max)
    )
    structure = (
        np.isfinite(ma_short)
        & np.isfinite(ma_mid)
        & np.isfinite(ma_long)
        & np.isfinite(mid_slope)
        & np.isfinite(long_slope)
        & (ma_short > ma_mid)
        & (mid_slope > 0.0)
        & (long_slope > 0.0)
    )
    pullback = np.isfinite(closes) & np.isfinite(ma_short) & (closes <= ma_short)
    exit_signal = np.isfinite(ma_short) & np.isfinite(ma_mid) & (ma_short < ma_mid)
    return WaveFeatures(
        ma_short=ma_short,
        ma_mid=ma_mid,
        ma_long=ma_long,
        range_position=range_position,
        activation=activation,
        structure=structure,
        pullback=pullback,
        exit_signal=exit_signal,
    )


def _last_index(dates: np.ndarray, end_date: int) -> int:
    return int(np.searchsorted(dates, end_date, side="right")) - 1


def _mae_pct(
    adjusted_closes: np.ndarray, entry_index: int, held_last: int, entry_price: float
) -> float:
    path = adjusted_closes[entry_index : held_last + 1]
    path = path[np.isfinite(path)]
    if not len(path):
        return float("nan")
    return float((np.min(path) / entry_price - 1.0) * 100.0)


def _fill_delay(
    calendar_index: dict[int, int], signal_date: int, fill_date: int
) -> int:
    """Trading days beyond the immediate next open that a fill took."""

    return max(
        0, calendar_index.get(fill_date, 0) - calendar_index.get(signal_date, 0) - 1
    )


def _resolve_exit(
    series: PricedSeries,
    *,
    exit_signal_index: int,
    last_index: int,
    down_limits: np.ndarray,
) -> tuple[int | None, bool]:
    """First bar after the exit signal whose raw open is actually sellable."""

    limit_row_missing = False
    candidate = exit_signal_index + 1
    while candidate <= last_index:
        raw_open = float(series.opens[candidate])
        if not np.isfinite(raw_open) or raw_open <= 0:
            candidate += 1
            continue
        down_limit = float(down_limits[candidate])
        if not np.isfinite(down_limit):
            # No stored floor for this bar: fill, but mark the trade so the
            # report can separate verified from unverified fills.
            return candidate, True
        if blocks_sell(raw_open, down_limit):
            candidate += 1
            continue
        return candidate, limit_row_missing
    return None, limit_row_missing


def simulate_wave_trades(
    series: PricedSeries,
    features: WaveFeatures,
    *,
    spec: WaveSpec,
    st_mask: np.ndarray,
    up_limits: np.ndarray,
    down_limits: np.ndarray,
    start_date: int,
    end_date: int,
    calendar_index: dict[int, int],
    costs: CostModel,
) -> tuple[tuple[WaveTrade, ...], dict[str, int]]:
    """Replay the frozen loop for one security inside one window.

    The activation ledger is the subtle part: an entry *decision* at bar ``i``
    consumes every activation dated at or before ``i`` whether or not the fill
    at ``i+1`` succeeds (preregistration E7).  Without that, an entry blocked
    by an up-limit gap would simply retry the next day at a higher price --
    the chasing that card 1 forbids.
    """

    counts = {
        "entry_signals_st_voided": 0,
        "entry_signals_without_any_next_bar": 0,
        "entry_void_up_limit": 0,
        "entry_void_unusable_price": 0,
        "exit_no_fill_fact": 0,
    }
    trades: list[WaveTrade] = []
    if len(series.dates) == 0 or series.dates[0] > end_date:
        return (), counts
    last_index = _last_index(series.dates, end_date)
    if last_index < 0:
        return (), counts
    exit_indices = np.flatnonzero(features.exit_signal)

    index = int(np.searchsorted(series.dates, start_date, side="left"))
    consumed_through = -1
    while index <= last_index:
        if not (features.structure[index] and features.pullback[index]):
            index += 1
            continue
        window_start = max(0, index - spec.entry_window_bars, consumed_through + 1)
        if not features.activation[window_start:index].any():
            index += 1
            continue
        if st_mask[index]:
            # ST voids the decision but does not spend the activation: the
            # security was never eligible, so nothing about the setup was used.
            counts["entry_signals_st_voided"] += 1
            index += 1
            continue
        consumed_through = index

        entry_index = index + 1
        if entry_index > last_index:
            counts["entry_signals_without_any_next_bar"] += 1
            index += 1
            continue
        raw_open = float(series.opens[entry_index])
        entry_price = float(series.adjusted_opens[entry_index])
        if (
            not np.isfinite(raw_open)
            or raw_open <= 0
            or not np.isfinite(entry_price)
            or entry_price <= 0
        ):
            counts["entry_void_unusable_price"] += 1
            index += 1
            continue
        if blocks_buy(raw_open, float(up_limits[entry_index])):
            counts["entry_void_up_limit"] += 1
            index += 1
            continue
        entry_limit_row_missing = not np.isfinite(up_limits[entry_index])

        # Preregistration section 6 puts the earliest exit signal on a bar
        # AFTER the entry day, so the fill bar's own close cannot close the
        # trade; searching from entry_index + 1 is what the frozen text says.
        position = int(
            np.searchsorted(exit_indices, entry_index + 1, side="left")
        )
        exit_signal_index = (
            int(exit_indices[position])
            if position < len(exit_indices) and exit_indices[position] <= last_index
            else None
        )
        exit_limit_row_missing = False
        if exit_signal_index is None:
            status: str = "open_at_window_end"
            exit_index_out = last_index
            exit_signal_date_out = None
            exit_price = float(series.adjusted_closes[exit_index_out])
        elif exit_signal_index + 1 > last_index:
            status = "no_fill_fact"
            exit_index_out = exit_signal_index
            exit_signal_date_out = int(series.dates[exit_signal_index])
            exit_price = float("nan")
        else:
            filled_index, exit_limit_row_missing = _resolve_exit(
                series,
                exit_signal_index=exit_signal_index,
                last_index=last_index,
                down_limits=down_limits,
            )
            exit_signal_date_out = int(series.dates[exit_signal_index])
            if filled_index is None:
                status = "open_at_window_end"
                exit_index_out = last_index
                exit_price = float(series.adjusted_closes[exit_index_out])
            else:
                status = "closed"
                exit_index_out = filled_index
                exit_price = float(series.adjusted_opens[exit_index_out])

        if status == "no_fill_fact":
            counts["exit_no_fill_fact"] += 1
            gross = net = mae = float("nan")
        else:
            gross = (exit_price / entry_price - 1.0) * 100.0
            # Back-adjusted prices carry an arbitrary scale (close x adj), so
            # feeding them straight to the cost model would evaluate the CNY 5
            # floor against a fictitious notional. Dividing both legs by the
            # ENTRY bar's factor restores real money: the entry leg becomes the
            # raw entry price, and the exit leg becomes what the originally
            # bought lot is really worth after any corporate action in between.
            # The ratio, hence the gross return, is untouched.
            scale = float(series.adj[entry_index])
            net = costs.net_return_pct(entry_price / scale, exit_price / scale)
            # A closed trade is sold at exit_index_out's open, so that bar's
            # close belongs to a period the position no longer existed in.
            held_last = exit_index_out - 1 if status == "closed" else exit_index_out
            mae = _mae_pct(series.adjusted_closes, entry_index, held_last, entry_price)

        trades.append(
            WaveTrade(
                code=series.code,
                entry_signal_date=int(series.dates[index]),
                entry_date=int(series.dates[entry_index]),
                exit_signal_date=exit_signal_date_out,
                exit_date=int(series.dates[exit_index_out]),
                entry_price=entry_price,
                exit_price=exit_price,
                gross_return_pct=gross,
                net_return_pct=net,
                mae_pct=mae,
                holding_bars=exit_index_out - entry_index,
                entry_index=entry_index,
                exit_index=exit_index_out,
                status=status,  # type: ignore[arg-type]
                entry_delay_days=_fill_delay(
                    calendar_index,
                    int(series.dates[index]),
                    int(series.dates[entry_index]),
                ),
                exit_delay_days=(
                    _fill_delay(
                        calendar_index,
                        exit_signal_date_out,
                        int(series.dates[exit_index_out]),
                    )
                    if exit_signal_date_out is not None
                    else 0
                ),
                entry_limit_row_missing=entry_limit_row_missing,
                exit_limit_row_missing=exit_limit_row_missing,
            )
        )
        if status != "closed":
            break
        index = exit_index_out + 1
    return tuple(trades), counts

