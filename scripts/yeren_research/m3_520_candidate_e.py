"""One-shot walk-forward for the 520 rule under the frozen preregistration.

Every convention here is locked in
`docs/research/yeren-system/m3-520-preregistration-2026-08-21.md` (owner
confirmed `stop_days=3`, S8 primary cohort (c), and the fee model, 2026-08-21;
two pre-run corrections in the same document's section 9 fixed the P&L price
basis and the ST exclusion timing). Nothing in this module may be changed to
make the result look better; a wrong convention gets fixed with a dated note
in the preregistration document, not a silent edit here.

Research-only. No playbook, simulator order, broker instruction, or
execution service is created. `real_broker_orders=False` always.
"""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore
from scripts.yeren_research.m3_520 import (
    RuleFeatures,
    RuleSpec,
    SecuritySeries,
    Trade,
    compute_features,
    matched_horizon_placebo,
    simulate_trades,
)
from scripts.yeren_research.m3_520_adjustment_audit import audit_convention_and_events
from scripts.yeren_research.m3_520_executability_audit import load_constraints
from scripts.yeren_research.market import load_trade_dates
from scripts.yeren_research.pit_priced_panel import PricedSeries, load_priced_panel

VENDOR = "tushare"
NO_UP_LIMIT = 9_999.0
NO_DOWN_LIMIT = 0.02
PRICE_TOLERANCE = 1e-6
# Covers C's observed max exit delay (45 trading days) with margin, so the
# constraint prefetch (a fixed, bounded set) covers every retry a down-limit
# block can plausibly cause. See module docstring: a lookup miss beyond this
# degrades to "unverified fill", it does not crash or fabricate a price.
RETRY_LOOKAHEAD_DAYS = 60
# A chain of consecutive up-limit-voided entries revealing one more real
# entry each pass would need this many refinement passes to fully resolve.
# Real chains this deep have not been observed anywhere in the three prior
# audits; this is a termination guarantee for a fixed-point loop, not a
# tuned parameter -- if a chain ever did run this deep, the loop simply
# stops with whatever it found, which degrades to more "missing limit row"
# flags, not a crash or a fabricated fill.
MAX_PREFETCH_REFINE_PASSES = 8


@dataclass(frozen=True)
class CostModel:
    """A股 statutory + convention costs, locked in preregistration §4.

    The ¥5 minimum commission is a per-order absolute floor; evaluating it
    needs some notional, and P1–P3 (position sizing) are unfrozen. Rather
    than invent a portfolio size, `lot_shares` uses the one number that is
    not a guess: 100 shares is A股's statutory minimum tradable unit, so
    "the smallest order this rule could ever place" is a fact, not a
    researcher assumption about typical position size (preregistration §9.3).
    """

    commission_rate: float = 0.00025
    transfer_fee_rate: float = 0.00001
    stamp_duty_rate: float = 0.001
    slippage_rate: float = 0.001
    min_commission: float = 5.0
    lot_shares: int = 100

    def net_return_pct(self, entry_price: float, exit_price: float) -> float:
        buy_notional = entry_price * self.lot_shares
        sell_notional = exit_price * self.lot_shares
        buy_commission = max(self.commission_rate * buy_notional, self.min_commission)
        sell_commission = max(self.commission_rate * sell_notional, self.min_commission)
        paid = buy_notional + buy_commission + self.transfer_fee_rate * buy_notional
        paid += self.slippage_rate * buy_notional
        received = (
            sell_notional - sell_commission - self.transfer_fee_rate * sell_notional
        )
        received -= self.stamp_duty_rate * sell_notional
        received -= self.slippage_rate * sell_notional
        return (received / paid - 1.0) * 100.0


@dataclass(frozen=True)
class TradeE:
    """One candidate-E trade: adjusted-price P&L, raw-price fillability."""

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
    entry_index: int
    exit_index: int
    status: Literal["closed", "open_at_window_end", "no_fill_fact"]
    cohort: Literal[
        "primary", "disclosure_only", "unresolved", "flagged_unverified_fill"
    ]
    entry_delay_days: int
    exit_delay_days: int
    entry_limit_row_missing: bool
    exit_limit_row_missing: bool


def _last_index(dates: np.ndarray, end_date: int) -> int:
    return int(np.searchsorted(dates, end_date, side="right")) - 1


def _mae_pct(
    adjusted_closes: np.ndarray, entry_index: int, exit_index: int, entry_price: float
) -> float:
    path = adjusted_closes[entry_index : exit_index + 1]
    path = path[np.isfinite(path)]
    if not len(path):
        return float("nan")
    return float((np.min(path) / entry_price - 1.0) * 100.0)


def _fill_delay(
    calendar_index: dict[int, int], signal_date: int, fill_date: int
) -> int:
    """Trading days beyond the immediate next open a fill took to happen.

    Zero for an ordinary next-open fill. Positive for either a suspension gap
    or a down-limit retry (both just mean fewer tradable opens existed
    between the signal and the fill); the two causes are not told apart
    because the consequence to the study is the same either way.
    """

    return max(
        0, calendar_index.get(fill_date, 0) - calendar_index.get(signal_date, 0) - 1
    )


def _gate_established(
    features: RuleFeatures, entry_index: int, held_last_index: int
) -> bool:
    """Did 5SMA ever stand above 20SMA while the position was held.

    Mirrors `m3_520_gate_diagnostic.crossed_during_trade`'s convention
    (entry always starts below; a single bar above proves the up-cross) on
    explicit indices instead of a `Trade` object, since candidate E's own
    trade type carries extra fields that convention was not written for.
    """

    if held_last_index < entry_index:
        return False
    window_short = features.ma_short[entry_index : held_last_index + 1]
    window_mid = features.ma_mid[entry_index : held_last_index + 1]
    above = np.isfinite(window_short) & np.isfinite(window_mid)
    above &= window_short > window_mid
    return bool(above.any())


def build_universe(
    series: tuple[PricedSeries, ...],
) -> tuple[tuple[PricedSeries, ...], dict[str, object]]:
    """Apply the two static preregistration exclusions: misaligned factors, BJ.

    ST is excluded per entry signal date (§9.2), not here — a security's ST
    status changes over time and a blanket drop would erase healthy periods.
    """

    _, misaligned = audit_convention_and_events(series)
    excluded_bj = frozenset(item.code for item in series if item.code.endswith(".BJ"))
    working = tuple(
        item
        for item in series
        if item.code not in misaligned and item.code not in excluded_bj
    )
    return working, {
        "securities_loaded": len(series),
        "securities_excluded_for_misalignment": len(misaligned),
        "securities_excluded_for_bj_exchange": len(excluded_bj - misaligned),
        "securities_in_working_universe": len(working),
    }


def _namechange_trade_dates(pit_root: Path) -> tuple[str, ...]:
    dates: set[str] = set()
    with (pit_root / "index.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("endpoint") == "namechange":
                dates.add(record["trade_date"])
    return tuple(sorted(dates))


def load_st_timeline(pit_root: Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """PIT ST-status intervals per code, straight from `namechange` names.

    Each interval's `name` field already carries the ST/*ST prefix for the
    period it names, so the prefix test alone is PIT-correct; no need to
    interpret `change_reason`. Returns, per code, sorted interval start
    dates and a same-length ST boolean, for a `searchsorted` lookup.
    """

    store = SnapshotStore(pit_root)
    frames: list[pd.DataFrame] = []
    for trade_date in _namechange_trade_dates(pit_root):
        snapshot = store.latest(
            vendor=VENDOR, endpoint="namechange", trade_date=trade_date
        )
        if snapshot is None:
            continue
        frame = pd.read_csv(
            io.BytesIO(snapshot.raw_payload),
            usecols=["ts_code", "name", "start_date"],
            dtype={"ts_code": "string", "name": "string"},
        )
        frames.append(frame[["ts_code", "name", "start_date"]])
    if not frames:
        return {}
    combined = pd.concat(frames, ignore_index=True)
    combined["start_date"] = pd.to_numeric(combined["start_date"], errors="coerce")
    combined = combined.dropna(subset=["start_date", "ts_code", "name"])
    combined["start_date"] = combined["start_date"].astype("int64")
    combined = combined.drop_duplicates(subset=["ts_code", "start_date", "name"])
    combined.sort_values(["ts_code", "start_date"], kind="mergesort", inplace=True)
    timeline: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for code, group in combined.groupby("ts_code", sort=False, observed=True):
        starts = group["start_date"].to_numpy(dtype=np.int64)
        is_st = group["name"].str.startswith(("ST", "*ST")).to_numpy(dtype=bool)
        timeline[str(code)] = (starts, is_st)
    return timeline


def build_st_mask(
    dates: np.ndarray, code: str, timeline: dict[str, tuple[np.ndarray, np.ndarray]]
) -> np.ndarray:
    """Per-bar ST flag for one security's date array, PIT looked up."""

    entry = timeline.get(code)
    if entry is None:
        return np.zeros(len(dates), dtype=bool)
    starts, is_st = entry
    positions = np.searchsorted(starts, dates, side="right") - 1
    mask = np.zeros(len(dates), dtype=bool)
    valid = positions >= 0
    mask[valid] = is_st[positions[valid]]
    return mask


def _adjusted_series(item: PricedSeries) -> SecuritySeries:
    return SecuritySeries(
        code=item.code,
        dates=item.dates,
        opens=item.adjusted_opens,
        closes=item.adjusted_closes,
    )


def _touched_dates_with_buffer(
    universe: tuple[PricedSeries, ...],
    *,
    spec: RuleSpec,
    start_date: int,
    end_date: int,
    st_timeline: dict[str, tuple[np.ndarray, np.ndarray]],
    limits: dict[tuple[str, int], dict[str, object]] | None = None,
) -> set[tuple[str, int]]:
    """Every (code, date) a replay with the real entry filters could touch.

    ST voiding is always applied (cheap, no circularity: the timeline is
    already loaded). Up-limit voiding is applied only when `limits` is
    already known, since evaluating it needs the very data being prefetched.
    `evaluate_window` therefore calls this twice: once with `limits=None`
    for a first-pass (ST-only) touched set, then again with that pass's
    loaded limits — an entry the first pass thought was mid-trade can in the
    real replay have actually been void (ST or up-limit), freeing the slot
    for a real, earlier entry the first pass never looked at. The union of
    both passes is what actually gets prefetched.

    Adds up to `RETRY_LOOKAHEAD_DAYS` of each exit's subsequent available
    bars too, to cover down-limit retries before they happen.
    """

    touched: set[tuple[str, int]] = set()
    for item in universe:
        series = _adjusted_series(item)
        features = compute_features(series.closes, spec)
        st_mask = build_st_mask(item.dates, item.code, st_timeline)
        entry_signal = features.entry_signal & ~st_mask
        if limits is not None:
            blocked = np.zeros(len(item.dates), dtype=bool)
            for signal_index in np.flatnonzero(entry_signal):
                entry_index = signal_index + 1
                if entry_index >= len(item.dates):
                    continue
                raw_open = float(item.opens[entry_index])
                up_limit = limits.get(
                    (item.code, int(item.dates[entry_index])), {}
                ).get("up_limit")
                if (
                    isinstance(up_limit, float)
                    and up_limit < NO_UP_LIMIT
                    and raw_open >= up_limit - PRICE_TOLERANCE
                ):
                    blocked[signal_index] = True
            entry_signal = entry_signal & ~blocked
        # simulate_trades' "early_turn" kind only reads early_exit_signal;
        # substitute the same union used by the real replay so this touch
        # pass walks through signals on the same bars the constrained pass
        # will, not a subtly different (typically later) exit sequence.
        unioned = RuleFeatures(
            ma_short=features.ma_short,
            ma_mid=features.ma_mid,
            ma_long=features.ma_long,
            entry_signal=entry_signal,
            early_exit_signal=features.early_exit_signal | features.full_cross_signal,
            full_cross_signal=features.full_cross_signal,
        )
        for trade in simulate_trades(
            series,
            unioned,
            start_date=start_date,
            end_date=end_date,
            exit_kind="early_turn",
        ):
            touched.add((item.code, int(item.dates[trade.entry_index])))
            last = min(trade.exit_index + RETRY_LOOKAHEAD_DAYS, len(item.dates) - 1)
            for index in range(trade.exit_index - 1, last + 1):
                if 0 <= index < len(item.dates):
                    touched.add((item.code, int(item.dates[index])))
    return touched


def simulate_trades_e(
    series: PricedSeries,
    features: RuleFeatures,
    *,
    entry_signal: np.ndarray,
    start_date: int,
    end_date: int,
    limits: dict[tuple[str, int], dict[str, object]],
    calendar_index: dict[int, int],
    costs: CostModel,
    execution_basis: Literal["next_open", "signal_close"] = "next_open",
) -> tuple[tuple[TradeE, ...], dict[str, int]]:
    """Replay 520 with raw-price fillability gating adjusted-price P&L.

    `entry_signal` is the caller's array (typically
    `features.entry_signal & ~st_mask`) so ST voiding stays outside this
    function's own concerns. The exit trigger is
    `early_exit_signal | full_cross_signal`, kept unmodified from round
    one's per-bar definitions — S8's gate is a post-hoc cohort tag (§1.3),
    not a change to which bar closes the trade, so the mechanical trigger
    fires exactly as it always would and is only reclassified after the
    fact.

    `execution_basis="signal_close"` reprices the very same decisions at the
    signal bar's adjusted close instead of the next bar's adjusted open;
    every gating decision, index, status, and cohort stays byte-identical.
    This exists solely for the disclosure-only sensitivity run locked in
    `m3-520-exec-timing-sensitivity-preregistration-2026-08-22.md`: it
    embeds look-ahead (the signal forms AT that close) and must never be
    used as an executable convention.
    """

    counts = {
        "entry_signals_without_any_next_bar": 0,
        "entry_void_up_limit": 0,
        "exit_no_fill_fact": 0,
    }
    if series.dates[0] > end_date:
        return (), counts
    last_index = _last_index(series.dates, end_date)
    if last_index < 0:
        return (), counts
    # The exit trigger is the earlier of the two author-described conditions:
    # "5MA turns down" (early_exit_signal) and the hard backstop "5MA crosses
    # below 20MA" (full_cross_signal, preregistration §1.2's "完全离场点").
    # A turn-down is not guaranteed to coincide with a down-cross day, so
    # dropping either one can leave a position open past its exit.
    exit_indices = np.flatnonzero(
        features.early_exit_signal | features.full_cross_signal
    )
    entry_indices = np.flatnonzero(
        entry_signal & (series.dates >= start_date) & (series.dates <= end_date)
    )

    trades: list[TradeE] = []
    next_signal_index = 0
    for signal_index in entry_indices:
        signal_index = int(signal_index)
        if signal_index < next_signal_index:
            continue
        entry_index = signal_index + 1
        if entry_index > last_index:
            counts["entry_signals_without_any_next_bar"] += 1
            continue
        raw_open = float(series.opens[entry_index])
        if not np.isfinite(raw_open) or raw_open <= 0:
            continue
        entry_limits = limits.get((series.code, int(series.dates[entry_index])), {})
        up_limit = entry_limits.get("up_limit")
        entry_limit_row_missing = up_limit is None
        if (
            isinstance(up_limit, float)
            and up_limit < NO_UP_LIMIT
            and raw_open >= up_limit - PRICE_TOLERANCE
        ):
            counts["entry_void_up_limit"] += 1
            continue
        entry_price = float(series.adjusted_opens[entry_index])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue

        # Deliberately `<= last_index`, not `<`: a signal that fires ON the
        # final in-window bar has zero fillable candidates and must fall
        # into the no-fill-fact branch below, not be silently dropped into
        # open_at_window_end the way round one's `simulate_trades` does
        # (the exact 663-trade gap the C-unit audit found and preregistration
        # §3 rule 5 requires this module to fix).
        exit_position = int(np.searchsorted(exit_indices, entry_index, side="left"))
        exit_signal_index = (
            int(exit_indices[exit_position])
            if exit_position < len(exit_indices)
            and exit_indices[exit_position] <= last_index
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
            candidate = exit_signal_index + 1
            filled_index = None
            while candidate <= last_index:
                exit_limits = limits.get(
                    (series.code, int(series.dates[candidate])), {}
                )
                down_limit = exit_limits.get("down_limit")
                raw_open_exit = float(series.opens[candidate])
                if not np.isfinite(raw_open_exit) or raw_open_exit <= 0:
                    # Bad or absent price data at this bar -- never a valid
                    # fill, regardless of what the limit lookup says.
                    candidate += 1
                    continue
                if down_limit is None:
                    exit_limit_row_missing = True
                    filled_index = candidate
                    break
                if (
                    isinstance(down_limit, float)
                    and down_limit > NO_DOWN_LIMIT
                    and raw_open_exit <= down_limit + PRICE_TOLERANCE
                ):
                    candidate += 1
                    continue
                filled_index = candidate
                break
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
            cohort = "unresolved"
        else:
            gross = (exit_price / entry_price - 1.0) * 100.0
            net = costs.net_return_pct(entry_price, exit_price)
            # A closed trade is sold at exit_index_out's open, so that bar's
            # close belongs to a period the position no longer existed in;
            # an open_at_window_end position is still held through its mark
            # bar's close. Same convention as m3_520_gate_diagnostic's
            # held_bar_range.
            held_last_index = (
                exit_index_out - 1 if status == "closed" else exit_index_out
            )
            mae = _mae_pct(
                series.adjusted_closes, entry_index, held_last_index, entry_price
            )
            if execution_basis == "signal_close":
                # Sensitivity revaluation: same decisions, priced at the
                # decision bar's close. Gating above already ran on the
                # next-open rules and is deliberately left untouched.
                entry_price = float(series.adjusted_closes[signal_index])
                if status == "closed":
                    exit_price = float(series.adjusted_closes[exit_signal_index])
                gross = (exit_price / entry_price - 1.0) * 100.0
                net = costs.net_return_pct(entry_price, exit_price)
                mae = _mae_pct(
                    series.adjusted_closes, entry_index, held_last_index, entry_price
                )
            if status == "open_at_window_end":
                cohort = "unresolved"
            else:
                gate = _gate_established(features, entry_index, held_last_index)
                cohort = "primary" if gate else "disclosure_only"
                if entry_limit_row_missing or exit_limit_row_missing:
                    cohort = "flagged_unverified_fill"

        trades.append(
            TradeE(
                code=series.code,
                entry_signal_date=int(series.dates[signal_index]),
                entry_date=int(series.dates[entry_index]),
                exit_signal_date=exit_signal_date_out,
                exit_date=int(series.dates[exit_index_out]),
                entry_price=entry_price,
                exit_price=exit_price,
                gross_return_pct=gross,
                net_return_pct=net,
                mae_pct=mae,
                entry_index=entry_index,
                exit_index=exit_index_out,
                status=status,  # type: ignore[arg-type]
                cohort=cohort,  # type: ignore[arg-type]
                entry_delay_days=_fill_delay(
                    calendar_index,
                    int(series.dates[signal_index]),
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
        if status in ("open_at_window_end", "no_fill_fact"):
            break
        next_signal_index = exit_index_out + 1
    return tuple(trades), counts


def _percentile(values: np.ndarray, q: float) -> float | None:
    return float(np.percentile(values, q)) if len(values) else None


def _cohort_stats(trades: list[TradeE]) -> dict[str, object]:
    gross = np.asarray([t.gross_return_pct for t in trades], dtype=float)
    net = np.asarray([t.net_return_pct for t in trades], dtype=float)
    mae = np.asarray([t.mae_pct for t in trades], dtype=float)
    mae = mae[np.isfinite(mae)]
    return {
        "trades": len(trades),
        "mean_gross_return_pct": float(gross.mean()) if len(gross) else None,
        "mean_net_return_pct": float(net.mean()) if len(net) else None,
        "median_net_return_pct": _percentile(net, 50),
        "win_rate_net_pct": float((net > 0).mean() * 100.0) if len(net) else None,
        "worst_net_pct": float(net.min()) if len(net) else None,
        "mae_median_pct": _percentile(mae, 50),
        "mae_worst_pct": float(mae.min()) if len(mae) else None,
        "mae_drawdown_definition": (
            "single-trade close-only maximum adverse excursion; "
            "not a portfolio drawdown (preregistration §5)"
        ),
    }


def _placebo_series_by_code(
    universe: tuple[PricedSeries, ...],
) -> dict[str, SecuritySeries]:
    return {item.code: _adjusted_series(item) for item in universe}


def evaluate_window(
    universe: tuple[PricedSeries, ...],
    *,
    spec: RuleSpec,
    costs: CostModel,
    st_timeline: dict[str, tuple[np.ndarray, np.ndarray]],
    pit_root: Path,
    calendar_index: dict[int, int],
    start_date: int,
    end_date: int,
    placebo_reps: int,
    seed: int,
    execution_basis: Literal["next_open", "signal_close"] = "next_open",
    run_placebo: bool = True,
    include_trades: bool = False,
) -> dict[str, object]:
    # Constraint prefetch refined to a fixed point: an entry the previous
    # pass thought was mid-trade may really have been up-limit-voided,
    # freeing the slot for a real, later entry the previous pass never
    # touched -- and that later entry can itself turn out to be voided too,
    # revealing a third. Iterate until a pass finds nothing new, capped so a
    # pathological chain cannot loop forever; see _touched_dates_with_buffer.
    touched = _touched_dates_with_buffer(
        universe,
        spec=spec,
        start_date=start_date,
        end_date=end_date,
        st_timeline=st_timeline,
    )
    limits = load_constraints(pit_root, touched)
    prefetch_converged = False
    for _ in range(MAX_PREFETCH_REFINE_PASSES):
        refined = _touched_dates_with_buffer(
            universe,
            spec=spec,
            start_date=start_date,
            end_date=end_date,
            st_timeline=st_timeline,
            limits=limits,
        )
        if refined <= touched:
            prefetch_converged = True
            break
        touched |= refined
        limits = load_constraints(pit_root, touched)

    all_trades: list[TradeE] = []
    void_st_total = 0
    void_up_limit_total = 0
    entry_no_next_bar_total = 0
    exit_no_fill_fact_total = 0
    for item in universe:
        features = compute_features(item.adjusted_closes, spec)
        st_mask = build_st_mask(item.dates, item.code, st_timeline)
        in_window = (item.dates >= start_date) & (item.dates <= end_date)
        void_st_total += int(
            np.count_nonzero(features.entry_signal & st_mask & in_window)
        )
        entry_signal = features.entry_signal & ~st_mask
        trades, counts = simulate_trades_e(
            item,
            features,
            entry_signal=entry_signal,
            start_date=start_date,
            end_date=end_date,
            limits=limits,
            calendar_index=calendar_index,
            costs=costs,
            execution_basis=execution_basis,
        )
        all_trades.extend(trades)
        void_up_limit_total += counts["entry_void_up_limit"]
        entry_no_next_bar_total += counts["entry_signals_without_any_next_bar"]
        exit_no_fill_fact_total += counts["exit_no_fill_fact"]

    primary = [t for t in all_trades if t.status == "closed" and t.cohort == "primary"]
    disclosure = [
        t for t in all_trades if t.status == "closed" and t.cohort == "disclosure_only"
    ]
    flagged = [t for t in all_trades if t.cohort == "flagged_unverified_fill"]
    open_end = [t for t in all_trades if t.status == "open_at_window_end"]
    delayed_over_5d = sum(
        1 for t in all_trades if t.entry_delay_days > 5 or t.exit_delay_days > 5
    )

    if run_placebo:
        placebo_trades = tuple(
            Trade(
                code=t.code,
                entry_signal_date=t.entry_signal_date,
                entry_date=t.entry_date,
                exit_signal_date=t.exit_signal_date,
                exit_date=t.exit_date,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                return_pct=t.gross_return_pct,
                mae_pct=t.mae_pct,
                entry_index=t.entry_index,
                exit_index=t.exit_index,
                status="closed",
            )
            for t in primary
        )
        placebo = matched_horizon_placebo(
            placebo_trades,
            _placebo_series_by_code(universe),
            start_date=start_date,
            end_date=end_date,
            reps=placebo_reps,
            seed=seed,
        )

        primary_stats = _cohort_stats(primary)
        criteria = {
            "mean_net_return_positive": (
                primary_stats["mean_net_return_pct"] is not None
                and primary_stats["mean_net_return_pct"] > 0
            ),
            "placebo_upper_tail_p_le_0_05": (
                placebo["upper_tail_p_value"] is not None
                and placebo["upper_tail_p_value"] <= 0.05
            ),
        }
        criteria["pass"] = (
            criteria["mean_net_return_positive"]
            and criteria["placebo_upper_tail_p_le_0_05"]
        )
    else:
        # The sensitivity driver compares two repricings of the same trades;
        # a next-open-based placebo would be a cross-basis comparison with
        # no meaning, and significance was already settled by candidate E.
        placebo = None
        criteria = None

    result = {
        "start_date": start_date,
        "end_date": end_date,
        "primary_cohort_s8_c": _cohort_stats(primary),
        "disclosure_cohort_s8_a": _cohort_stats(disclosure),
        "flagged_unverified_fill": {"trades": len(flagged)},
        "open_at_window_end": {
            "trades": len(open_end),
            "mean_marked_gross_return_pct": (
                float(np.mean([t.gross_return_pct for t in open_end]))
                if open_end
                else None
            ),
        },
        "signals_excluded": {
            "st_at_entry_signal_date": void_st_total,
            "up_limit_at_entry_open": void_up_limit_total,
            "entry_signal_without_any_next_bar": entry_no_next_bar_total,
        },
        "exit_no_fill_fact": exit_no_fill_fact_total,
        "delayed_fills_over_5_trading_days": delayed_over_5d,
        "constraint_prefetch_converged": prefetch_converged,
        "placebo": placebo,
        "judgment_criteria": criteria,
    }
    if include_trades:
        result["primary_trades"] = tuple(primary)
    return result


PREREGISTERED_START_DATE = "20150105"
PREREGISTERED_SPLIT_DATE = "20221230"
PREREGISTERED_END_DATE = "20260819"
PREREGISTERED_STOP_DAYS = 3
PREREGISTERED_PLACEBO_REPS = 200
PREREGISTERED_SEED = 52020260820
# Frozen preregistration §4 values; the owner-actual-rate rerun passes its
# own per `m3-520-fee-rerun-preregistration-2026-08-22.md`.
PREREGISTERED_COMMISSION_RATE = 0.00025
PREREGISTERED_MIN_COMMISSION = 5.0
FEE_RERUN_PREREGISTRATION = (
    "docs/research/yeren-system/m3-520-fee-rerun-preregistration-2026-08-22.md"
)


def run_candidate_e(
    pit_root: Path,
    *,
    start_date: str = PREREGISTERED_START_DATE,
    split_date: str = PREREGISTERED_SPLIT_DATE,
    end_date: str | None = None,
    stop_days: int = PREREGISTERED_STOP_DAYS,
    placebo_reps: int = PREREGISTERED_PLACEBO_REPS,
    seed: int = PREREGISTERED_SEED,
    commission_rate: float = PREREGISTERED_COMMISSION_RATE,
    min_commission: float = PREREGISTERED_MIN_COMMISSION,
) -> dict[str, object]:
    """Run the walk-forward.

    The defaults are the exact frozen preregistration parameters — the only
    invocation that counts as *the* candidate-E result is the one with every
    keyword argument left at its default. Any override (a different
    `stop_days`, `end_date`, `placebo_reps`, or `seed`) is legitimate for
    smoke-testing this module, but the report's own
    `preregistered_parameters_used` flag records whether this run actually
    was the frozen one, so a deviation is visible in the output itself
    rather than resting on the operator remembering not to pass flags.
    """

    calendar = load_trade_dates(pit_root)
    # Omitting end_date resolves to the frozen preregistered date, not
    # calendar[-1] -- otherwise data appended after 2026-08-21 would widen
    # the "default" run's sample without any flag having been passed.
    study_end = end_date or PREREGISTERED_END_DATE
    is_frozen_run = (
        start_date == PREREGISTERED_START_DATE
        and split_date == PREREGISTERED_SPLIT_DATE
        and study_end == PREREGISTERED_END_DATE
        and stop_days == PREREGISTERED_STOP_DAYS
        and placebo_reps == PREREGISTERED_PLACEBO_REPS
        and seed == PREREGISTERED_SEED
        and commission_rate == PREREGISTERED_COMMISSION_RATE
        and min_commission == PREREGISTERED_MIN_COMMISSION
    )
    calendar_index = {int(day): position for position, day in enumerate(calendar)}
    series, coverage = load_priced_panel(
        pit_root, start_date=start_date, end_date=study_end
    )
    universe, universe_report = build_universe(series)
    spec = RuleSpec(stop_days=stop_days)
    costs = CostModel(
        commission_rate=commission_rate, min_commission=min_commission
    )
    st_timeline = load_st_timeline(pit_root)

    oos_start = next(day for day in calendar if day > split_date)
    windows = {
        "in_sample": (int(start_date), int(min(split_date, study_end))),
        "out_of_sample": (int(oos_start), int(study_end)),
    }
    results = {
        name: evaluate_window(
            universe,
            spec=spec,
            costs=costs,
            st_timeline=st_timeline,
            pit_root=pit_root,
            calendar_index=calendar_index,
            start_date=window[0],
            end_date=window[1],
            placebo_reps=placebo_reps,
            seed=seed if name == "in_sample" else seed + 100,
        )
        for name, window in windows.items()
    }
    return {
        "study": "m3-520-candidate-e-walkforward",
        "preregistration": (
            "docs/research/yeren-system/m3-520-preregistration-2026-08-21.md"
            if is_frozen_run
            else FEE_RERUN_PREREGISTRATION
        ),
        "cost_variant": (
            "frozen-preregistration-2026-08-21"
            if (
                commission_rate == PREREGISTERED_COMMISSION_RATE
                and min_commission == PREREGISTERED_MIN_COMMISSION
            )
            else "owner-actual-rate-fee-rerun-2026-08-22"
        ),
        "preregistered_parameters_used": is_frozen_run,
        "stop_days": stop_days,
        "cost_model": {
            "commission_rate": costs.commission_rate,
            "transfer_fee_rate": costs.transfer_fee_rate,
            "stamp_duty_rate": costs.stamp_duty_rate,
            "slippage_rate": costs.slippage_rate,
            "min_commission": costs.min_commission,
            "lot_shares": costs.lot_shares,
        },
        "panel_load": coverage,
        "universe": universe_report,
        "pit_disclaimer": (
            "single terminal-vintage reconstruction truncated by trade_date; "
            "algorithm-layer as-of only, knowledge-time-layer PIT is not provable"
        ),
        "windows": results,
        "real_broker_orders": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pit-root", type=Path, default=Path("data/marketdata_pit"))
    parser.add_argument("--start-date", default=PREREGISTERED_START_DATE)
    parser.add_argument("--split-date", default=PREREGISTERED_SPLIT_DATE)
    parser.add_argument(
        "--end-date",
        default=None,
        help=(
            "Omit for the frozen preregistered end date "
            f"({PREREGISTERED_END_DATE}, resolved via the calendar). Passing "
            "any value other than that produces a non-frozen smoke run — see "
            "preregistered_parameters_used in the output."
        ),
    )
    parser.add_argument("--stop-days", type=int, default=PREREGISTERED_STOP_DAYS)
    parser.add_argument("--placebo-reps", type=int, default=PREREGISTERED_PLACEBO_REPS)
    parser.add_argument("--seed", type=int, default=PREREGISTERED_SEED)
    parser.add_argument(
        "--commission-rate",
        type=float,
        default=PREREGISTERED_COMMISSION_RATE,
        help=(
            "Per-side commission rate. Default is the frozen preregistration "
            "value; the owner-actual-rate rerun passes 0.00015 per "
            "m3-520-fee-rerun-preregistration-2026-08-22.md."
        ),
    )
    parser.add_argument(
        "--min-commission",
        type=float,
        default=PREREGISTERED_MIN_COMMISSION,
        help="Per-order commission floor in CNY (owner confirmed: 5.0).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_candidate_e(
        args.pit_root,
        start_date=args.start_date,
        split_date=args.split_date,
        end_date=args.end_date,
        stop_days=args.stop_days,
        placebo_reps=args.placebo_reps,
        seed=args.seed,
        commission_rate=args.commission_rate,
        min_commission=args.min_commission,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
