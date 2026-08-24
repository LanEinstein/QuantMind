"""Research-only parameterisation and validation for the 520 arbitrage rule.

The module deliberately stops at an offline study.  It does not create a
playbook, simulator order, broker instruction, or execution service.
"""

from __future__ import annotations

import argparse
import gc
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.market import load_trade_dates

VENDOR = "tushare"
ExitKind = Literal["early_turn", "full_cross"]


@dataclass(frozen=True)
class RuleSpec:
    """The smallest deterministic reading of the card's observable language."""

    short_window: int = 5
    mid_window: int = 20
    long_window: int = 30
    stop_days: int = 3

    def __post_init__(self) -> None:
        if (
            min(self.short_window, self.mid_window, self.long_window, self.stop_days)
            < 1
        ):
            raise ValueError("520 windows and stop_days must be positive")
        if not self.short_window < self.mid_window < self.long_window:
            raise ValueError("520 windows must be strictly increasing")


@dataclass(frozen=True)
class SecuritySeries:
    """Chronological raw daily observations for one security."""

    code: str
    dates: np.ndarray
    opens: np.ndarray
    closes: np.ndarray


@dataclass(frozen=True)
class RuleFeatures:
    """Close-time features; every boolean is computable without future rows."""

    ma_short: np.ndarray
    ma_mid: np.ndarray
    ma_long: np.ndarray
    entry_signal: np.ndarray
    early_exit_signal: np.ndarray
    full_cross_signal: np.ndarray


@dataclass(frozen=True)
class Trade:
    """One accepted entry, with close-only mark-to-market for unfinished trades."""

    code: str
    entry_signal_date: int
    entry_date: int
    exit_signal_date: int | None
    exit_date: int
    entry_price: float
    exit_price: float
    return_pct: float
    mae_pct: float
    entry_index: int
    exit_index: int
    status: Literal["closed", "open_at_window_end"]


def _rolling_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Compute a causal simple moving average and preserve missing windows."""

    values = np.asarray(values, dtype=float)
    result = np.full(values.shape, np.nan, dtype=float)
    if len(values) < window:
        return result
    if np.isfinite(values).all():
        cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=float)))
        result[window - 1 :] = (
            cumulative[window:] - cumulative[:-window]
        ) / window
        return result
    for index in range(window - 1, len(values)):
        sample = values[index - window + 1 : index + 1]
        if np.isfinite(sample).all():
            result[index] = float(sample.mean())
    return result


def compute_features(closes: np.ndarray, spec: RuleSpec) -> RuleFeatures:
    """Turn the card into close-time signals without using a future price."""

    ma_short = _rolling_mean(closes, spec.short_window)
    ma_mid = _rolling_mean(closes, spec.mid_window)
    ma_long = _rolling_mean(closes, spec.long_window)
    short_slope = np.full(len(closes), np.nan, dtype=float)
    mid_slope = np.full(len(closes), np.nan, dtype=float)
    long_slope = np.full(len(closes), np.nan, dtype=float)
    short_slope[1:] = ma_short[1:] - ma_short[:-1]
    mid_slope[1:] = ma_mid[1:] - ma_mid[:-1]
    long_slope[1:] = ma_long[1:] - ma_long[:-1]

    prior_stop = np.ones(len(closes), dtype=bool)
    for offset in range(1, spec.stop_days + 1):
        condition = np.zeros(len(closes), dtype=bool)
        condition[offset:] = short_slope[:-offset] <= 0
        prior_stop &= condition

    gap = ma_mid - ma_short
    narrowing = np.zeros(len(closes), dtype=bool)
    narrowing[1:] = gap[1:] < gap[:-1]
    finite = (
        np.isfinite(ma_short)
        & np.isfinite(ma_mid)
        & np.isfinite(ma_long)
        & np.isfinite(short_slope)
        & np.isfinite(mid_slope)
        & np.isfinite(long_slope)
    )
    entry_signal = (
        finite
        & prior_stop
        & (short_slope > 0)
        & (mid_slope < 0)
        & (long_slope < 0)
        & (ma_short < ma_mid)
        & narrowing
    )
    early_exit_signal = np.isfinite(short_slope) & (short_slope < 0)
    full_cross_signal = np.zeros(len(closes), dtype=bool)
    full_cross_signal[1:] = (
        np.isfinite(ma_short[1:])
        & np.isfinite(ma_mid[1:])
        & np.isfinite(ma_short[:-1])
        & np.isfinite(ma_mid[:-1])
        & (ma_short[1:] < ma_mid[1:])
        & (ma_short[:-1] >= ma_mid[:-1])
    )
    return RuleFeatures(
        ma_short=ma_short,
        ma_mid=ma_mid,
        ma_long=ma_long,
        entry_signal=entry_signal,
        early_exit_signal=early_exit_signal,
        full_cross_signal=full_cross_signal,
    )


def _last_index(dates: np.ndarray, end_date: int) -> int:
    """Return the last observation available inside a study window."""

    index = int(np.searchsorted(dates, end_date, side="right")) - 1
    if index < 0:
        raise ValueError(f"no observations on or before {end_date}")
    return index


def _mae_pct(
    closes: np.ndarray, entry_index: int, exit_index: int, entry_price: float
) -> float:
    """Use close-to-close adverse excursion; intraday execution is not inferred."""

    path = closes[entry_index : exit_index + 1]
    path = path[np.isfinite(path)]
    if not len(path):
        return float("nan")
    return float((np.min(path) / entry_price - 1.0) * 100.0)


def simulate_trades(
    series: SecuritySeries,
    features: RuleFeatures,
    *,
    start_date: int,
    end_date: int,
    exit_kind: ExitKind,
) -> tuple[Trade, ...]:
    """Simulate one position per security using next-available-open execution."""

    if series.dates[0] > end_date:
        return ()
    last_index = _last_index(series.dates, end_date)
    exit_signal = (
        features.early_exit_signal
        if exit_kind == "early_turn"
        else features.full_cross_signal
    )
    exit_indices = np.flatnonzero(exit_signal)
    entry_indices = np.flatnonzero(
        features.entry_signal
        & (series.dates >= start_date)
        & (series.dates <= end_date)
    )
    trades: list[Trade] = []
    next_signal_index = 0
    for signal_index in entry_indices:
        signal_index = int(signal_index)
        if signal_index < next_signal_index:
            continue
        entry_index = signal_index + 1
        if entry_index > last_index or not np.isfinite(series.opens[entry_index]):
            continue
        entry_price = float(series.opens[entry_index])
        if entry_price <= 0:
            continue

        exit_position = int(np.searchsorted(exit_indices, entry_index, side="left"))
        exit_signal_index = (
            int(exit_indices[exit_position])
            if exit_position < len(exit_indices)
            and exit_indices[exit_position] < last_index
            else None
        )
        exit_index = None
        if exit_signal_index is not None:
            candidate_index = exit_signal_index + 1
            if (
                np.isfinite(series.opens[candidate_index])
                and series.opens[candidate_index] > 0
            ):
                exit_index = candidate_index

        if exit_index is None:
            mark_index = last_index
            exit_price = float(series.closes[mark_index])
            if not np.isfinite(exit_price) or exit_price <= 0:
                continue
            trades.append(
                Trade(
                    code=series.code,
                    entry_signal_date=int(series.dates[signal_index]),
                    entry_date=int(series.dates[entry_index]),
                    exit_signal_date=None,
                    exit_date=int(series.dates[mark_index]),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    return_pct=(exit_price / entry_price - 1.0) * 100.0,
                    mae_pct=_mae_pct(
                        series.closes, entry_index, mark_index, entry_price
                    ),
                    entry_index=entry_index,
                    exit_index=mark_index,
                    status="open_at_window_end",
                )
            )
            break

        exit_price = float(series.opens[exit_index])
        trades.append(
            Trade(
                code=series.code,
                entry_signal_date=int(series.dates[signal_index]),
                entry_date=int(series.dates[entry_index]),
                exit_signal_date=int(series.dates[exit_signal_index]),
                exit_date=int(series.dates[exit_index]),
                entry_price=entry_price,
                exit_price=exit_price,
                return_pct=(exit_price / entry_price - 1.0) * 100.0,
                mae_pct=_mae_pct(series.closes, entry_index, exit_index, entry_price),
                entry_index=entry_index,
                exit_index=exit_index,
                status="closed",
            )
        )
        next_signal_index = exit_index + 1
    return tuple(trades)


def load_daily_panel(
    pit_root: Path,
    *,
    start_date: str,
    end_date: str,
) -> tuple[tuple[SecuritySeries, ...], dict[str, object]]:
    """Load only stored daily OHLC rows and retain the PIT calendar boundary."""

    calendar = load_trade_dates(pit_root)
    requested_dates = tuple(day for day in calendar if start_date <= day <= end_date)
    store = SnapshotStore(pit_root)
    frames: list[pd.DataFrame] = []
    missing_dates: list[str] = []
    for trade_date in requested_dates:
        snapshot = store.latest(
            vendor=VENDOR, endpoint="daily", trade_date=trade_date
        )
        if snapshot is None:
            missing_dates.append(trade_date)
            continue
        frame = pd.read_csv(
            io.BytesIO(snapshot.raw_payload),
            usecols=["trade_date", "ts_code", "open", "close"],
            dtype={"trade_date": "int32", "ts_code": "string"},
        )
        frame = frame[frame["ts_code"].str.endswith((".SH", ".SZ", ".BJ"), na=False)]
        frames.append(frame)
    if not frames:
        raise ValueError("no daily snapshots in requested range")

    panel = pd.concat(frames, ignore_index=True)
    panel["open"] = pd.to_numeric(panel["open"], errors="coerce")
    panel["close"] = pd.to_numeric(panel["close"], errors="coerce")
    panel.sort_values(["ts_code", "trade_date"], kind="mergesort", inplace=True)
    duplicate_rows = int(panel.duplicated(["ts_code", "trade_date"]).sum())
    series_list: list[SecuritySeries] = []
    for code, group in panel.groupby("ts_code", sort=False, observed=True):
        if len(group) < 30:
            continue
        series_list.append(
            SecuritySeries(
                code=str(code),
                dates=group["trade_date"].to_numpy(dtype=np.int32, copy=True),
                opens=group["open"].to_numpy(dtype=float, copy=True),
                closes=group["close"].to_numpy(dtype=float, copy=True),
            )
        )
    row_count = len(panel)
    del panel, frames
    gc.collect()
    return tuple(series_list), {
        "requested_date_count": len(requested_dates),
        "loaded_date_count": len(requested_dates) - len(missing_dates),
        "missing_dates": missing_dates,
        "row_count": row_count,
        "security_count_with_30_rows": len(series_list),
        "duplicate_code_date_rows": duplicate_rows,
        "first_requested_date": requested_dates[0] if requested_dates else None,
        "last_requested_date": requested_dates[-1] if requested_dates else None,
    }


def _percentile(values: np.ndarray, q: float) -> float | None:
    return float(np.percentile(values, q)) if len(values) else None


def _trade_metrics(trades: tuple[Trade, ...]) -> dict[str, object]:
    """Summarise gross trade outcomes without inventing portfolio sizing."""

    closed = tuple(trade for trade in trades if trade.status == "closed")
    marked = np.asarray([trade.return_pct for trade in closed], dtype=float)
    marked = marked[np.isfinite(marked)]
    mae = np.asarray([trade.mae_pct for trade in closed], dtype=float)
    mae = mae[np.isfinite(mae)]
    marked_open = tuple(trade for trade in trades if trade.status != "closed")
    open_returns = np.asarray([trade.return_pct for trade in marked_open], dtype=float)
    result: dict[str, object] = {
        "accepted_entries": len(trades),
        "completed_trades": len(closed),
        "open_at_window_end": len(marked_open),
        "mean_return_pct": float(marked.mean()) if len(marked) else None,
        "median_return_pct": _percentile(marked, 50),
        "p10_return_pct": _percentile(marked, 10),
        "p90_return_pct": _percentile(marked, 90),
        "win_rate_pct": float((marked > 0).mean() * 100.0) if len(marked) else None,
        "worst_trade_pct": float(marked.min()) if len(marked) else None,
        "mae_median_pct": _percentile(mae, 50),
        "mae_worst_pct": float(mae.min()) if len(mae) else None,
        "target_8_to_10_pct_share": (
            float(((marked >= 8.0) & (marked <= 10.0)).mean() * 100.0)
            if len(marked)
            else None
        ),
        "open_marked_mean_return_pct": (
            float(open_returns.mean()) if len(open_returns) else None
        ),
        "drawdown_definition": (
            "completed-trade close-only maximum adverse excursion; "
            "not a portfolio drawdown"
        ),
    }
    return result


def matched_horizon_placebo(
    trades: tuple[Trade, ...],
    series_by_code: dict[str, SecuritySeries],
    *,
    start_date: int,
    end_date: int,
    reps: int,
    seed: int,
    warmup_bars: int = 31,
) -> dict[str, object]:
    """Randomise entry timing while matching security and realised holding length."""

    completed = tuple(trade for trade in trades if trade.status == "closed")
    if not completed or reps <= 0:
        return {
            "reps": max(reps, 0),
            "matched_trades": len(completed),
            "placebo_mean_return_pct_median": None,
            "actual_mean_return_pct": None,
            "upper_tail_p_value": None,
        }

    rng = np.random.default_rng(seed)
    placebo_means: list[float] = []
    actual_mean = float(np.mean([trade.return_pct for trade in completed]))
    bounds = {
        code: (
            max(
                warmup_bars,
                int(np.searchsorted(item.dates, start_date, side="left")),
            ),
            _last_index(item.dates, end_date),
        )
        for code, item in series_by_code.items()
        if item.dates[0] <= end_date
    }
    for _ in range(reps):
        sampled_returns: list[float] = []
        for trade in completed:
            series = series_by_code[trade.code]
            horizon = trade.exit_index - trade.entry_index
            first, last_window = bounds[trade.code]
            last = last_window - horizon
            if last < first:
                continue
            entry_index = int(rng.integers(first, last + 1))
            if not (
                np.isfinite(series.opens[entry_index])
                and series.opens[entry_index] > 0
                and np.isfinite(series.opens[entry_index + horizon])
                and series.opens[entry_index + horizon] > 0
            ):
                continue
            sampled_returns.append(
                float(
                    (
                        series.opens[entry_index + horizon]
                        / series.opens[entry_index]
                        - 1.0
                    )
                    * 100.0,
                )
            )
        if sampled_returns:
            placebo_means.append(float(np.mean(sampled_returns)))

    distribution = np.asarray(placebo_means, dtype=float)
    if not len(distribution):
        p_value = None
    else:
        p_value = float(
            (1 + np.sum(distribution >= actual_mean)) / (len(distribution) + 1)
        )
    return {
        "reps": reps,
        "matched_trades": len(completed),
        "usable_placebo_reps": len(distribution),
        "actual_mean_return_pct": actual_mean,
        "placebo_mean_return_pct_median": _percentile(distribution, 50),
        "placebo_mean_return_pct_p10": _percentile(distribution, 10),
        "placebo_mean_return_pct_p90": _percentile(distribution, 90),
        "upper_tail_p_value": p_value,
        "seed": seed,
        "design": (
            "same security, same window, same completed holding bars; "
            "random entry timing"
        ),
    }


def evaluate_window(
    series: tuple[SecuritySeries, ...],
    *,
    spec: RuleSpec,
    start_date: int,
    end_date: int,
    placebo_reps: int,
    seed: int,
) -> dict[str, object]:
    """Evaluate both author-described exit readings over one fixed window."""

    trades_by_kind: dict[ExitKind, list[Trade]] = {
        "early_turn": [],
        "full_cross": [],
    }
    series_by_code = {item.code: item for item in series}
    signal_count = 0
    for item in series:
        features = compute_features(item.closes, spec)
        signal_count += int(
            np.count_nonzero(
                features.entry_signal
                & (item.dates >= start_date)
                & (item.dates <= end_date)
            )
        )
        for exit_kind in ("early_turn", "full_cross"):
            trades_by_kind[exit_kind].extend(
                simulate_trades(
                    item,
                    features,
                    start_date=start_date,
                    end_date=end_date,
                    exit_kind=exit_kind,
                )
            )

    output: dict[str, object] = {
        "start_date": start_date,
        "end_date": end_date,
        "entry_signal_count": signal_count,
    }
    for offset, exit_kind in enumerate(("early_turn", "full_cross")):
        trades = tuple(trades_by_kind[exit_kind])
        output[exit_kind] = {
            "metrics": _trade_metrics(trades),
            "placebo": matched_horizon_placebo(
                trades,
                series_by_code,
                start_date=start_date,
                end_date=end_date,
                reps=placebo_reps,
                seed=seed + offset,
            ),
        }
    return output


def run_study(
    pit_root: Path,
    *,
    start_date: str = "20150105",
    split_date: str = "20221230",
    end_date: str | None = None,
    stop_days: int = 3,
    placebo_reps: int = 200,
    seed: int = 52020260820,
) -> dict[str, object]:
    """Run the pre-set chronological split and return JSON-ready results."""

    calendar = load_trade_dates(pit_root)
    study_end = end_date or calendar[-1]
    series, load_stats = load_daily_panel(
        pit_root, start_date=start_date, end_date=study_end
    )
    spec = RuleSpec(stop_days=stop_days)
    split_end = min(split_date, study_end)
    oos_start_index = next(
        (index for index, day in enumerate(calendar) if day > split_date), None
    )
    if oos_start_index is None or calendar[oos_start_index] > study_end:
        raise ValueError("split_date leaves no out-of-sample trading date")
    oos_start = calendar[oos_start_index]
    windows = {
        "in_sample": evaluate_window(
            series,
            spec=spec,
            start_date=int(start_date),
            end_date=int(split_end),
            placebo_reps=placebo_reps,
            seed=seed,
        ),
        "out_of_sample": evaluate_window(
            series,
            spec=spec,
            start_date=int(oos_start),
            end_date=int(study_end),
            placebo_reps=placebo_reps,
            seed=seed + 100,
        ),
    }
    return {
        "study": "m3-520-parameterization",
        "price_source": "daily raw open/close; no forward-adjusted price used",
        "execution": (
            "signal at close; next available row open; "
            "exit cannot occur on entry day"
        ),
        "rule": {
            "short_window": spec.short_window,
            "mid_window": spec.mid_window,
            "long_window": spec.long_window,
            "stop_days": spec.stop_days,
            "entry": (
                "5MA turns up after stop_days non-rising 5MA observations, "
                "20MA and 30MA still fall, 5MA remains below 20MA and the gap narrows"
            ),
            "early_exit": "first subsequent close-time 5MA turn down",
            "full_exit": "first subsequent close-time 5MA below 20MA",
        },
        "split": {
            "in_sample": f"{start_date}..{split_end}",
            "out_of_sample": f"{oos_start}..{study_end}",
            "selection_rule": "fixed calendar split; no return-based tuning",
        },
        "load": load_stats,
        "windows": windows,
        "real_broker_orders": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit-root", type=Path, default=Path("data/marketdata_pit"))
    parser.add_argument("--start-date", default="20150105")
    parser.add_argument("--split-date", default="20221230")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--stop-days", type=int, default=3)
    parser.add_argument("--placebo-reps", type=int, default=200)
    parser.add_argument("--seed", type=int, default=52020260820)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_study(
        args.pit_root,
        start_date=args.start_date,
        split_date=args.split_date,
        end_date=args.end_date,
        stop_days=args.stop_days,
        placebo_reps=args.placebo_reps,
        seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
