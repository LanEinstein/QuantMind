"""MD-1 — the P-B stop-loss ablation on the frozen SLV-1 replay.

Preregistration: ``docs/research/pb-stop-ablation-preregistration-2026-08-23.md``
(committed BEFORE this implementation; trigger/criteria/K frozen there).

ONE question: does adding the P-B stop (owner principle: "abnormal decline →
hard stop, no confirmation wait") to a replay of SLV-1's committed rule reduce
MDD without destroying net P&L?

Two arms, byte-identical except the overlay:

* ``pb_base``    — SLV-1 rule (D1 gates → dv_ratio top-5 eq → buf40_5, 20d) in
  the frozen gate event loop (reproduces the science-gate construction).
* ``pb_stopped`` — same scores/health/machinery, plus the stop overlay via the
  harness's default-preserving ``decide_fn`` seam: a held name whose trailing
  20-trading-day adjusted return ranks in the bottom decile of the full-market
  cross-section is force-sold at the next open, and a flagged name cannot be
  bought while flagged (prevents the sell→rebuy loop a falling price would
  otherwise cause, since falling prices RAISE dv_ratio).

Verdict (preregistered §4): fewer than 15 EXECUTED stop-sell fills →
INSUFFICIENT_EVENTS. Else ADOPT_SIGNAL iff MDD improves by ≥ 2pp AND net P&L
retains ≥ 80% of baseline; otherwise NO_ADOPT. All verdicts seal the study;
an ADOPT_SIGNAL only feeds the post-certification SLV-1.1 owner decision.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scripts.yeren_research.pit_priced_panel import PricedSeries

    from .exit_veto_ablation import ArmResult

import numpy as np
import pandas as pd

from backend.backtest.strategy import (
    DailySignals,
    DayDecision,
    OrderIntent,
    PortfolioView,
    StrategyConfig,
    decide_day,
)
from backend.marketdata_snapshot.store import SnapshotStore

from . import exit_veto_panel as xv
from .arena_ablation import ledger_n_trials
from .avoid_top_ablation import (
    _classify_regimes,
    _crash_slice_table,
    _regime_table,
)
from .defensive_d1_ablation import DefensiveArm, _arm_from_gate, _strategy_config
from .defensive_sleeve_science_gate import build_sleeve_ranker_table
from .defensive_sleeve_spec import CONTAINER, HORIZON
from .gate_backtest import (
    GateBacktestResult,
    PanelScoreProvider,
    run_gate_backtest,
)
from .gate_bar_source import PitBarSource
from .locked_split import LockedSplit, load_daily_calendar
from .multi_strategy_compare import compare_strategies

INITIAL_CAPITAL_YUAN: float = 1_000_000.0

# Preregistration §3/§4 — frozen before implementation.
STOP_WINDOW: int = 20
STOP_QUANTILE: float = 0.10
MIN_STOP_FILLS: int = 15
MDD_IMPROVEMENT_MIN: float = 0.02
NET_RETENTION_MIN: float = 0.80
CHURN_REBALANCE_WINDOW: int = 2

LEDGER_FAMILY: str = "ds.defensive_sleeve.pb_stop"
LEDGER_ROUND: str = "md1-pb-stop"
LEDGER_DATE: str = "2026-08-23"

MISALIGNED_ARTIFACT = Path(
    "data/yeren_research/inventory/m3-520-adjustment-audit-2026-08-21.json"
)

_BASE = "pb_base"
_STOP = "pb_stopped"


def load_misaligned_codes(artifact: Path = MISALIGNED_ARTIFACT) -> frozenset[str]:
    """The settled 78 securities whose stored factors contradict pct_chg."""
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    return frozenset(payload["convention_and_events"]["misaligned_security_codes"])


def stop_flags_from_series(
    series: Iterable[PricedSeries],
    *,
    window: int = STOP_WINDOW,
    quantile: float = STOP_QUANTILE,
    exclude: frozenset[str] = frozenset(),
) -> tuple[dict[str, frozenset[str]], dict[str, object]]:
    """``{trade_date: flagged codes}`` — the preregistered §3.1 trigger.

    Cross-section = all loaded securities minus ``.BJ`` and the excluded set;
    a code needs ``window`` prior bars to carry a trailing return that day
    (younger codes are absent from that day's cross-section; count disclosed).
    """
    dates_list: list[np.ndarray] = []
    codes_list: list[np.ndarray] = []
    rets_list: list[np.ndarray] = []
    short_history = 0
    for item in series:
        if item.code.endswith(".BJ") or item.code in exclude:
            continue
        adjc = item.adjusted_closes
        if len(adjc) <= window:
            short_history += 1
            continue
        rets = adjc[window:] / adjc[:-window] - 1.0
        dates_list.append(item.dates[window:])
        codes_list.append(np.full(len(rets), item.code, dtype=object))
        rets_list.append(rets)
    if not rets_list:
        raise ValueError("no securities carry a trailing return — fail-closed")
    frame = pd.DataFrame(
        {
            "date": np.concatenate(dates_list),
            "code": np.concatenate(codes_list),
            "ret": np.concatenate(rets_list),
        }
    )
    frame = frame[np.isfinite(frame["ret"])]
    frame["pct"] = frame.groupby("date")["ret"].rank(pct=True, method="average")
    flagged = frame[frame["pct"] <= quantile]
    flags: dict[str, frozenset[str]] = {
        str(date): frozenset(grp["code"].astype(str))
        for date, grp in flagged.groupby("date", sort=True)
    }
    stats = {
        "cross_section_rows": int(len(frame)),
        "flagged_rows": int(len(flagged)),
        "days_with_flags": len(flags),
        "securities_without_trailing_history": short_history,
    }
    return flags, stats


def make_stop_decide(
    flags: Mapping[str, frozenset[str]],
    *,
    intents_log: list[tuple[str, tuple[str, ...]]],
    base_decide: Callable[..., DayDecision] = decide_day,
) -> Callable[..., DayDecision]:
    """Wrap the base decision with the preregistered §3.2 stop overlay.

    Base decision first; then force-sell held flagged names the base did not
    already sell, and veto buys of currently-flagged names. Stop-sell intents
    are logged so EXECUTED stop fills can be counted against actual fills.
    """

    def decide(
        *,
        signals: DailySignals,
        view: PortfolioView,
        bars: Mapping[str, object],
        config: StrategyConfig,
    ) -> DayDecision:
        base = base_decide(signals=signals, view=view, bars=bars, config=config)
        flagged = flags.get(signals.trade_date, frozenset())
        if not flagged:
            return base
        held = {h.code: h.volume for h in view.holdings}
        base_sells = set(base.sell_codes)
        stop_sells = tuple(
            sorted(c for c in held if c in flagged and c not in base_sells)
        )
        kept_orders = tuple(
            o for o in base.orders if not (o.side_is_buy and o.code in flagged)
        )
        stop_orders = tuple(
            OrderIntent(code=c, side_is_buy=False, volume=held[c]) for c in stop_sells
        )
        if stop_sells:
            intents_log.append((signals.trade_date, stop_sells))
        return DayDecision(
            trade_date=signals.trade_date,
            orders=kept_orders + stop_orders,
            shortlist=base.shortlist,
            sell_codes=tuple(base.sell_codes) + stop_sells,
            buy_codes=tuple(c for c in base.buy_codes if c not in flagged),
            scores=base.scores,
        )

    return decide


def executed_stop_fills(
    intents_log: Sequence[tuple[str, tuple[str, ...]]],
    result: GateBacktestResult,
    daily_days: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """The (fill_date, code) stop-sells that actually FILLED (T+1 open).

    An intent decided on day T fills on the next trading day; the harness
    replaces pending orders daily, so an unfilled stop intent is simply
    re-issued while the name stays held+flagged. Counting fills (not intents)
    is the preregistered §4 event basis.
    """
    day_index = {d: i for i, d in enumerate(daily_days)}
    expected: set[tuple[str, str]] = set()
    for decision_day, codes in intents_log:
        idx = day_index.get(decision_day)
        if idx is None or idx + 1 >= len(daily_days):
            continue
        fill_day = daily_days[idx + 1]
        for code in codes:
            expected.add((fill_day, code))
    out: list[tuple[str, str]] = []
    for fill in result.backtest_result.fills:
        if fill.side_is_buy:
            continue
        key = (str(fill.trade_date), str(fill.code))
        if key in expected:
            out.append(key)
    return tuple(sorted(out))


def churn_count(
    stop_fills: Sequence[tuple[str, str]],
    result: GateBacktestResult,
    rebalance_dates: Sequence[str],
    daily_days: Sequence[str],
    *,
    window_rebalances: int = CHURN_REBALANCE_WINDOW,
) -> int:
    """Disclosure: stop-sold names re-BOUGHT within the next N rebalance dates.

    A buy decided on the N-th rebalance date FILLS on the next trading day, so
    the horizon extends one trading day past that rebalance (codex P2).
    """
    rebs = sorted(rebalance_dates)
    day_index = {d: i for i, d in enumerate(daily_days)}
    buys = sorted(
        (str(f.trade_date), str(f.code))
        for f in result.backtest_result.fills
        if f.side_is_buy
    )
    churn = 0
    for sell_day, code in stop_fills:
        later_rebs = [d for d in rebs if d > sell_day][:window_rebalances]
        if not later_rebs:
            continue
        last_reb = later_rebs[-1]
        idx = day_index.get(last_reb)
        horizon_end = (
            daily_days[idx + 1]
            if idx is not None and idx + 1 < len(daily_days)
            else last_reb
        )
        if any(
            b_code == code and sell_day < b_day <= horizon_end
            for b_day, b_code in buys
        ):
            churn += 1
    return churn


def verdict(
    *,
    stop_fill_count: int,
    base_mdd: float,
    stop_mdd: float,
    base_net: float,
    stop_net: float,
) -> dict[str, object]:
    """The preregistered §4 verdict — all operators frozen there."""
    mdd_ok = stop_mdd <= base_mdd - MDD_IMPROVEMENT_MIN
    net_ok = stop_net >= NET_RETENTION_MIN * base_net
    if stop_fill_count < MIN_STOP_FILLS:
        label = "INSUFFICIENT_EVENTS"
    elif mdd_ok and net_ok:
        label = "ADOPT_SIGNAL"
    else:
        label = "NO_ADOPT"
    return {
        "verdict": label,
        "stop_fill_count": stop_fill_count,
        "min_stop_fills": MIN_STOP_FILLS,
        "mdd_base": base_mdd,
        "mdd_stopped": stop_mdd,
        "mdd_improvement": base_mdd - stop_mdd,
        "mdd_improvement_min": MDD_IMPROVEMENT_MIN,
        "criterion_mdd_improved": mdd_ok,
        "net_base": base_net,
        "net_stopped": stop_net,
        "net_retention": (stop_net / base_net) if base_net else float("nan"),
        "net_retention_min": NET_RETENTION_MIN,
        "criterion_net_retained": net_ok,
    }


def run_ablation(
    *,
    panel_path: str,
    snapshot_root: str,
    lock_path: str,
    ledger_path: str,
    smoke_periods: int | None = None,
    log: Callable[[str], object] = print,
) -> dict[str, object]:
    """MD-1 two-arm ablation → a JSON-able result dict."""
    log("[1/6] load + firewall panel (train_val only)")
    panel = pd.read_csv(panel_path, dtype={"date": str, "code": str, "ts_code": str})
    split = LockedSplit.load(lock_path, snapshot_root)
    panel_dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(panel_dates)
    non_tv = sorted(set(panel_dates) - set(split.train_val_dates))
    if non_tv:
        raise ValueError(
            f"panel has non-train_val dates (e.g. {non_tv[:3]}) — fail-closed"
        )

    log("[2/6] SLV-1 rule ranker table (dv_ratio top-5, science-gate reuse)")
    table = build_sleeve_ranker_table(panel)
    if smoke_periods is not None:
        keep = set(sorted(table["date"].astype(str).unique())[:smoke_periods])
        table = table[table["date"].astype(str).isin(keep)].copy()
    if table.empty:
        raise ValueError("sleeve ranker table is empty — fail-closed")
    rebs = sorted(table["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    allowed = set(split.train_val_dates) | set(split.embargo_dates)
    cal = sorted(calendar)
    last_idx = cal.index(rebs[-1]) if rebs[-1] in cal else len(cal) - 1
    end = cal[min(last_idx + HORIZON, len(cal) - 1)]
    daily_days = [d for d in cal if rebs[0] <= d <= end and d in allowed]
    split.assert_all_not_test(daily_days)
    universe = tuple(sorted(xv.panel_universe(table)))
    log(f"      rebal={len(rebs)} daily={len(daily_days)} universe={len(universe)}")

    log("[3/6] stop flags from the full-market priced panel (heavy)")
    from scripts.yeren_research.pit_priced_panel import load_priced_panel

    # min_rows follows the trigger's own need (20 prior bars + 1), NOT the
    # loader's 30-bar default — a 21-29-bar newly listed security carries valid
    # trailing returns that belong in the frozen cross-section (codex P1).
    series, coverage = load_priced_panel(
        Path(snapshot_root),
        start_date="20150105",
        end_date=daily_days[-1],
        min_rows=STOP_WINDOW + 1,
    )
    flags, flag_stats = stop_flags_from_series(
        series, exclude=load_misaligned_codes()
    )
    del series
    flag_stats["priced_panel_joined_rows"] = coverage["joined_rows"]
    log(f"      {flag_stats}")

    log("[4/6] run both arms (byte-identical except the stop overlay)")
    store = SnapshotStore(snapshot_root)
    bar_source = PitBarSource(store=store, trading_days=daily_days, universe=universe)
    scores = xv.scores_by_day(table)
    health = xv.build_health_overrides(table)
    slots, cap = CONTAINER.slots, CONTAINER.cap_percent

    def _arm(decide_fn) -> GateBacktestResult:  # noqa: ANN001
        return run_gate_backtest(
            bar_source=bar_source,
            provider=PanelScoreProvider(scores, health_overrides=health),
            strategy_config=_strategy_config(slots, cap),
            initial_capital_yuan=INITIAL_CAPITAL_YUAN,
            horizon=HORIZON,
            decide_fn=decide_fn,
        )

    base_res = _arm(decide_day)
    intents_log: list[tuple[str, tuple[str, ...]]] = []
    stop_res = _arm(make_stop_decide(flags, intents_log=intents_log))
    arms: dict[str, DefensiveArm] = {
        _BASE: _arm_from_gate(_BASE, slots, cap, base_res),
        _STOP: _arm_from_gate(_STOP, slots, cap, stop_res),
    }
    for label in (_BASE, _STOP):
        a = arms[label]
        log(
            f"      {label:12s} netPnL={a.net_pnl_yuan:+,.0f} "
            f"MDD={a.max_drawdown_pct:.2%} turn={a.monthly_turnover:.2f} "
            f"exp={a.avg_exposure:.2f} fills={a.fill_count} cons={a.conservation_ok}"
        )

    log("[5/6] stop-fill accounting + disclosures + ledger")
    stop_fills = executed_stop_fills(intents_log, stop_res, daily_days)
    per_year: dict[str, int] = {}
    for fill_day, _code in stop_fills:
        per_year[fill_day[:4]] = per_year.get(fill_day[:4], 0) + 1
    churn = churn_count(stop_fills, stop_res, rebs, daily_days)
    intent_instances = sum(len(codes) for _, codes in intents_log)
    n_trials = ledger_n_trials(
        ledger_path,
        cast("Sequence[ArmResult]", [arms[_BASE], arms[_STOP]]),
        (rebs[0], rebs[-1]),
        persist=smoke_periods is None,
        family=LEDGER_FAMILY,
        round_label=LEDGER_ROUND,
        description="MD-1 P-B stop-loss ablation on the SLV-1 replay",
        ledger_date=LEDGER_DATE,
    )
    bench = arms[_BASE].period_returns
    nmin = min(len(bench), len(arms[_STOP].period_returns))
    cmp = compare_strategies(
        candidate_returns=[list(arms[_STOP].period_returns[:nmin])],
        benchmark_returns=list(bench[:nmin]),
        labels=[_STOP],
        family=LEDGER_FAMILY,
    )
    regimes = _classify_regimes(bench)
    n_periods = len(bench)
    period_dates = [
        daily_days[min(j * HORIZON, len(daily_days) - 1)] for j in range(n_periods)
    ]
    ordered = [arms[_BASE], arms[_STOP]]
    regime_tbl = _regime_table(ordered, regimes)  # type: ignore[arg-type]
    crash_tbl = _crash_slice_table(ordered, period_dates)  # type: ignore[arg-type]
    corr = float(
        np.corrcoef(
            np.asarray(bench[:nmin]), np.asarray(arms[_STOP].period_returns[:nmin])
        )[0, 1]
    )

    log("[6/6] preregistered verdict")
    read = verdict(
        stop_fill_count=len(stop_fills),
        base_mdd=arms[_BASE].max_drawdown_pct,
        stop_mdd=arms[_STOP].max_drawdown_pct,
        base_net=arms[_BASE].net_pnl_yuan,
        stop_net=arms[_STOP].net_pnl_yuan,
    )
    return {
        "preregistration": (
            "docs/research/pb-stop-ablation-preregistration-2026-08-23.md"
        ),
        "trigger": {
            "window_trading_days": STOP_WINDOW,
            "cross_section_quantile": STOP_QUANTILE,
        },
        "window": {
            "start": rebs[0],
            "end": rebs[-1],
            "rebalance_dates": len(rebs),
            "daily_days": len(daily_days),
            "universe": len(universe),
            "smoke_periods": smoke_periods,
        },
        "flag_stats": flag_stats,
        "n_trials_deflation": n_trials,
        "conservation_ok": all(a.conservation_ok for a in arms.values()),
        "arms": {
            label: asdict(arms[label])
            | {"period_returns": None, "n_periods": len(arms[label].period_returns)}
            for label in (_BASE, _STOP)
        },
        "stop_fills": {
            "executed": len(stop_fills),
            "per_year": dict(sorted(per_year.items())),
            "churn_within_2_rebalances": churn,
            "intent_instances": intent_instances,
            "detail": [list(x) for x in stop_fills],
        },
        "period_return_correlation": corr,
        "spa_p_value": cmp.spa_p_value,
        "regimes": regime_tbl,
        "crash_slices": crash_tbl,
        "read": read,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel", default="data/factor_research/panel_train_val_defensive_d1.csv"
    )
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument(
        "--ledger", default="data/factor_research/mfi_trial_ledger.jsonl"
    )
    parser.add_argument(
        "--out", default="data/factor_research/pb_stop_ablation_result.json"
    )
    parser.add_argument(
        "--smoke-periods",
        type=int,
        default=None,
        help="restrict to the first N rebalance dates (engineering smoke; no ledger)",
    )
    args = parser.parse_args()

    result = run_ablation(
        panel_path=args.panel,
        snapshot_root=args.snapshot_root,
        lock_path=args.lock,
        ledger_path=args.ledger,
        smoke_periods=args.smoke_periods,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result["read"], indent=2, ensure_ascii=False))
    print(f"\n[written: {out}]")


if __name__ == "__main__":
    main()


__all__ = [
    "churn_count",
    "executed_stop_fills",
    "make_stop_decide",
    "run_ablation",
    "stop_flags_from_series",
    "verdict",
]
