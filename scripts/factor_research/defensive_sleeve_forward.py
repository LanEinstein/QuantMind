"""SLV-1 — pre-registered forward SURVIVAL validation of the frozen defensive sleeve.

Certification of the deployable sleeve (``defensive_sleeve_spec``, hash frozen
2026-07-04) moved FORWARD per the 2026-07-04 certification-rearch amendment: the
sleeve's load-bearing claim is a RISK property (mechanical MDD bound from the
defensive-universe filter + 60% cash buffer, bear-regime non-negative), which months
of forward data can verify — unlike a Sharpe/DSR claim which cannot be certified
in-sample (AP-0.5) nor forward within years. So this runner:

* evaluates the UNCHANGED frozen spec over the virgin forward window (every trading
  day strictly AFTER the locked ``test_end`` 2026-06-12 — data that postdates every
  development decision);
* monitors the PRE-REGISTERED kill-switch (``FORWARD_KILL_SWITCH``, committed inside
  the frozen spec BEFORE the window): realized MDD > 0.25 / forward bear-regime
  cumulative < −0.05 / ≥ 6 consecutive periods trailing the naive within-universe
  dv-top5 baseline (``eq_5``, full-gross — the raw committed comparison). ANY breach
  → status ``KILLED`` (fail-closed: a breach stops the sleeve even while ACCRUING);
* NEVER t-tests forward returns for significance — returns are monitored/disclosed,
  survival is the certification (< ``min_forward_periods`` complete periods →
  ``ACCRUING``, no verdict; ≥ that with no breach → ``SURVIVING``);
* emits the CURRENT target book (defensive gates + dv_ratio top-5 equal-weight at
  8% each + 60% cash) as a **display-only advisory** for the owner's manual
  execution (never an InstructionPlan; no order field exists here).

Registration is fail-closed: the first run (``--register``) freezes
``{spec_hash, test_end, forward_start, kill-switch, baseline}`` to a JSON file;
every later run verifies the frozen spec still hashes identically and the
kill-switch thresholds are unchanged — any drift aborts (a silently re-tuned spec
would void the pre-registration).

Anti-leak discipline (inverse of the dev firewall): the panel's REBALANCE dates
must all postdate ``test_end`` (asserted); pre-forward bars are read ONLY as
trailing FEATURE history (already-known data, the ``build_forward_panel_r4``
precedent), never as evaluation window.
"""

from __future__ import annotations

import argparse
import io
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from backend.marketdata_snapshot.store import SnapshotStore

from . import exit_veto_panel as xv
from .avoid_top_ablation import _classify_regimes
from .baselines import buy_and_hold_baseline
from .build_factor_panel import (
    _latest_snapshot_key,
    build_r3_inputs,
    forward_trade_dates,
)
from .defensive_d1_ablation import DefensiveArm, _arm_from_gate
from .defensive_d1_panel import (
    D1_PANEL_COLUMNS,
    _build_rows_d1,
    _ingest_series_d1,
    ingest_market_closes,
)
from .defensive_sleeve_science_gate import (
    CSI300_ETF,
    INITIAL_CAPITAL_YUAN,
    build_sleeve_ranker_table,
)
from .defensive_sleeve_science_gate import _run_arm as _sg_run_arm
from .defensive_sleeve_spec import (
    CONTAINER,
    FORWARD_KILL_SWITCH,
    HORIZON,
    PRODUCT,
    REBALANCE_FREQ,
    SELECTION_TOP_N,
    spec_hash,
)
from .gate_bar_source import PitBarSource
from .locked_split import LockedSplit

VENDOR: str = "tushare"

# Pre-forward trailing FEATURE history: rolling beta needs window+1 = 61 aligned
# pairs, vol/max need 21 bars, the liquidity screen a 20d window — 100 pre-forward
# trading days covers all with headroom (features only; never an evaluation bar).
FEATURE_BUFFER_TD: int = 100

BASELINE_LABEL: str = "sleeve_eq_5"
_BUF = f"sleeve_{CONTAINER.label}"

DEFAULT_REGISTRATION = "data/factor_research/defensive_sleeve_forward_registration.json"
DEFAULT_STATUS = "data/factor_research/defensive_sleeve_forward_status.json"


# --------------------------------------------------------------------------- #
# Pre-registration (fail-closed: drift in spec hash / thresholds aborts).      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ForwardRegistration:
    """The frozen forward pre-registration (committed before the window is read)."""

    product: str
    spec_hash: str
    test_end: str
    forward_start: str
    baseline: str
    kill_switch: dict[str, float | int]
    registered_on_utc: str

    @staticmethod
    def build(test_end: str, forward_start: str, now_utc: str) -> ForwardRegistration:
        return ForwardRegistration(
            product=PRODUCT,
            spec_hash=spec_hash(),
            test_end=test_end,
            forward_start=forward_start,
            baseline=BASELINE_LABEL,
            kill_switch=dict(asdict(FORWARD_KILL_SWITCH)),
            registered_on_utc=now_utc,
        )


def load_or_register(
    path: Path, *, test_end: str, forward_start: str, register: bool
) -> ForwardRegistration:
    """Load the frozen registration, or write it once under ``--register``.

    Fail-closed on ANY drift: a different current ``spec_hash`` or kill-switch
    than the registered one means the frozen spec was touched after
    pre-registration — the forward run is void and must abort loudly.
    """
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        reg = ForwardRegistration(**raw)
        if reg.spec_hash != spec_hash():
            raise ValueError(
                f"registered spec_hash {reg.spec_hash[:12]} != current "
                f"{spec_hash()[:12]} — frozen spec drifted after registration"
            )
        if reg.kill_switch != dict(asdict(FORWARD_KILL_SWITCH)):
            raise ValueError(
                "registered kill-switch differs from spec FORWARD_KILL_SWITCH "
                "— pre-registration void, aborting"
            )
        if reg.test_end != test_end:
            raise ValueError(
                f"registered test_end {reg.test_end} != locked split {test_end}"
            )
        if reg.forward_start != forward_start:
            raise ValueError(
                f"registered forward_start {reg.forward_start} != current first "
                f"forward day {forward_start} — a backfilled snapshot re-anchored "
                "the evaluation grid; pre-registration void, aborting"
            )
        return reg
    if not register:
        raise FileNotFoundError(
            f"no forward registration at {path} — first run needs --register"
        )
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    reg = ForwardRegistration.build(test_end, forward_start, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(reg), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return reg


# --------------------------------------------------------------------------- #
# Forward panel (features may look back; rebalances must postdate test_end).   #
# --------------------------------------------------------------------------- #


def forward_schedule(forward_dates: list[str]) -> list[str]:
    """The committed monthly cadence anchored at the first forward trading day."""
    return list(forward_dates[::REBALANCE_FREQ])


def build_sleeve_forward_panel(
    split: LockedSplit,
    store: SnapshotStore,
    *,
    inputs: object,
    forward_dates: list[str],
    rebalance_dates: list[str],
) -> pd.DataFrame:
    """D1-panel rows for ``rebalance_dates`` over the virgin forward window.

    Pre-forward bars enter ONLY as trailing feature history (the
    ``build_forward_panel_r4`` precedent — already-known data, not a leak);
    every REBALANCE date is asserted to postdate the locked ``test_end``.
    """
    test_end = split.test_dates[-1]
    stale = [d for d in rebalance_dates if d <= test_end]
    if stale:
        raise ValueError(
            f"forward rebalance dates must postdate test_end {test_end}; "
            f"got {stale[:3]} — fail-closed"
        )
    pre_forward = [*split.train_val_dates, *split.embargo_dates, *split.test_dates]
    buffer = list(pre_forward[-FEATURE_BUFFER_TD:])
    feature_dates = [*buffer, *forward_dates]
    series = _ingest_series_d1(store, feature_dates)
    market_closes = ingest_market_closes(store, feature_dates)
    rows = _build_rows_d1(
        series, sorted(set(rebalance_dates)), inputs=inputs, market_closes=market_closes
    )
    return pd.DataFrame(rows, columns=list(D1_PANEL_COLUMNS))


# --------------------------------------------------------------------------- #
# Kill-switch evaluation (pre-registered; breach = KILLED, fail-closed).       #
# --------------------------------------------------------------------------- #


def trailing_underperf_streak(
    sleeve_periods: tuple[float, ...], baseline_periods: tuple[float, ...]
) -> int:
    """Length of the CURRENT trailing run of sleeve period-return < baseline's."""
    streak = 0
    n = min(len(sleeve_periods), len(baseline_periods))
    for s, b in zip(reversed(sleeve_periods[:n]), reversed(baseline_periods[:n])):
        if s < b:
            streak += 1
        else:
            break
    return streak


def evaluate_kill_switch(
    *,
    sleeve_periods: tuple[float, ...],
    baseline_periods: tuple[float, ...],
    bench_periods: tuple[float, ...],
    realized_mdd: float,
) -> dict[str, object]:
    """The pre-registered forward kill-switch read (survival, never a t-test).

    * ``mdd`` — realized forward MDD (whole window, intra-period included);
    * ``bear_cum`` — sleeve cumulative return over forward BEAR periods (regimes
      classified from the CSI300-hold period returns, the science-gate classifier);
    * ``baseline_underperf`` — current trailing streak of complete periods where
      the sleeve return < the naive within-universe dv-top5 baseline (raw, as
      committed in the spec/plan).
    """
    ks = FORWARD_KILL_SWITCH
    breaches: list[str] = []
    if math.isfinite(realized_mdd) and realized_mdd > ks.mdd_kill:
        breaches.append("mdd")
    regimes = _classify_regimes(bench_periods)
    n = min(len(sleeve_periods), len(regimes))
    bear_returns = [sleeve_periods[i] for i in range(n) if regimes[i] == "bear"]
    bear_cum = float(sum(bear_returns))
    if bear_returns and bear_cum < ks.bear_cum_kill:
        breaches.append("bear_cum")
    streak = trailing_underperf_streak(sleeve_periods, baseline_periods)
    if streak >= ks.baseline_underperf_periods:
        breaches.append("baseline_underperf")
    return {
        "breaches": breaches,
        "realized_mdd": realized_mdd,
        "mdd_kill": ks.mdd_kill,
        "bear_period_n": len(bear_returns),
        "bear_cumulative": bear_cum,
        "bear_cum_kill": ks.bear_cum_kill,
        "underperf_streak": streak,
        "baseline_underperf_periods": ks.baseline_underperf_periods,
        "min_forward_periods": ks.min_forward_periods,
    }


def forward_status(kill: dict[str, object], n_periods: int) -> str:
    """``KILLED`` on any breach (even while accruing) / ``ACCRUING`` / ``SURVIVING``."""
    if kill["breaches"]:
        return "KILLED"
    if n_periods < FORWARD_KILL_SWITCH.min_forward_periods:
        return "ACCRUING"
    return "SURVIVING"


# --------------------------------------------------------------------------- #
# Display-only advisory (owner executes manually; never an InstructionPlan).   #
# --------------------------------------------------------------------------- #


def _roster_names(store: SnapshotStore, snapshot_root: str) -> dict[str, str]:
    """``ts_code → name`` from the latest listed roster snapshot (display only)."""
    key = _latest_snapshot_key(snapshot_root, "stock_basic_listed")
    snap = store.latest(vendor=VENDOR, endpoint="stock_basic_listed", trade_date=key)
    frame = pd.read_csv(io.BytesIO(snap.raw_payload), usecols=["ts_code", "name"])
    return dict(zip(frame["ts_code"].astype(str), frame["name"].astype(str)))


def _closes_on(store: SnapshotStore, day: str, codes: list[str]) -> dict[str, float]:
    """Close prices of ``codes`` on ``day`` from the stored ``daily`` snapshot."""
    snap = store.latest(vendor=VENDOR, endpoint="daily", trade_date=day)
    frame = pd.read_csv(io.BytesIO(snap.raw_payload), usecols=["ts_code", "close"])
    sub = frame[frame["ts_code"].isin(codes)]
    return {
        str(r.ts_code): float(r.close)
        for r in sub.itertuples()
        if math.isfinite(float(r.close))
    }


def sleeve_advisory(
    ranker_table: pd.DataFrame,
    *,
    asof: str,
    store: SnapshotStore,
    snapshot_root: str,
) -> dict[str, object]:
    """The current sleeve target book at ``asof`` (display-only, deterministic).

    Defensive gates + dv_ratio top-5 equal weight at ``CONTAINER.cap_percent``%
    each, remainder cash — exactly the frozen spec's book, with names/closes
    attached for the owner's manual execution.
    """
    day = ranker_table[ranker_table["date"].astype(str) == asof]
    if day.empty:
        raise ValueError(f"no defensive-universe names ranked at {asof} — fail-closed")
    top = day.nlargest(SELECTION_TOP_N, "ranker_score")
    codes = [str(c) for c in top["ts_code"]]
    names = _roster_names(store, snapshot_root)
    closes = _closes_on(store, asof, codes)
    holdings = [
        {
            "ts_code": code,
            "name": names.get(code, "?"),
            "dv_ratio": float(score),
            "close": closes.get(code),
            "target_weight_pct": float(CONTAINER.cap_percent),
        }
        for code, score in zip(codes, top["ranker_score"].astype(float))
    ]
    cash_pct = 100.0 - CONTAINER.cap_percent * len(holdings)
    return {
        "asof_trade_date": asof,
        "universe_size": int(len(day)),
        "holdings": holdings,
        "cash_weight_pct": cash_pct,
        "note": (
            "display-only research advisory (owner executes manually); "
            "target = defensive gates + dv_ratio top-5 equal weight; "
            "NEVER an instruction/order"
        ),
    }


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #


def _forward_arm(
    *,
    label: str,
    scores: dict[str, xv.ScoredDay],
    health: dict[str, dict[str, object]] | None,
    slots: int,
    cap_percent: int,
    bar_source: PitBarSource,
) -> DefensiveArm:
    """One survival-account arm — the science gate's arm construction, reused.

    Delegating to ``defensive_sleeve_science_gate._run_arm`` (not re-implemented)
    keeps the forward account measuring EXACTLY what the science gate certified.
    """
    return _arm_from_gate(
        label,
        slots,
        cap_percent,
        _sg_run_arm(
            scores=scores,
            health=health,  # type: ignore[arg-type]
            slots=slots,
            cap_percent=cap_percent,
            bar_source=bar_source,
        ),
    )


def _accrued_window_arms(
    *,
    store: SnapshotStore,
    ranker_table: pd.DataFrame,
    schedule: list[str],
    forward_dates: list[str],
) -> tuple[
    dict[str, DefensiveArm],
    tuple[float, ...],
    tuple[float, ...],
    tuple[float, ...],
    float,
]:
    """Backtest the accrued forward window on the SCHEDULE rebalances only.

    The advisory date must not inject an off-cadence rebalance into the survival
    account. Returns ``(arms, sleeve_periods, baseline_periods, bench_periods,
    realized_mdd)`` — all empty/NaN when the window cannot support a backtest yet.

    The whole accrued window is recomputed from its first day on every run —
    deterministic and simple; at the sleeve's monthly cadence the window stays
    small for years (a few hundred bars), so incremental state is not worth its
    corruption risk.
    """
    sched_tbl = ranker_table[ranker_table["date"].astype(str).isin(schedule)]
    if sched_tbl.empty or len(forward_dates) < 2:
        return {}, (), (), (), float("nan")
    universe = (*xv.panel_universe(sched_tbl), CSI300_ETF)
    bar_source = PitBarSource(
        store=store, trading_days=forward_dates, universe=universe
    )
    scores = xv.scores_by_day(sched_tbl)
    health = xv.build_health_overrides(sched_tbl)
    arms = {
        _BUF: _forward_arm(
            label=_BUF,
            scores=scores,
            health=health,
            slots=CONTAINER.slots,
            cap_percent=CONTAINER.cap_percent,
            bar_source=bar_source,
        ),
        BASELINE_LABEL: _forward_arm(
            label=BASELINE_LABEL,
            scores=scores,
            health=health,
            slots=5,
            cap_percent=100,
            bar_source=bar_source,
        ),
    }
    bh = buy_and_hold_baseline(
        bar_source=bar_source,
        asset_code=CSI300_ETF,
        initial_capital_yuan=INITIAL_CAPITAL_YUAN,
        horizon=HORIZON,
    )
    return (
        arms,
        tuple(arms[_BUF].period_returns),
        tuple(arms[BASELINE_LABEL].period_returns),
        tuple(bh.period_returns),
        arms[_BUF].max_drawdown_pct,
    )


def run_forward(
    *,
    snapshot_root: str,
    lock_path: str,
    registration_path: str,
    register: bool,
    advisory_asof: str = "",
) -> dict[str, object]:
    """One forward read: panel → backtest → kill-switch → advisory → status dict."""
    split = LockedSplit.load(lock_path, snapshot_root)
    test_end = split.test_dates[-1]
    forward_dates = forward_trade_dates(snapshot_root, test_end)
    if not forward_dates:
        raise ValueError(f"no forward trading days after test_end {test_end}")
    schedule = forward_schedule(forward_dates)
    reg = load_or_register(
        Path(registration_path),
        test_end=test_end,
        forward_start=forward_dates[0],
        register=register,
    )
    asof = advisory_asof or forward_dates[-1]
    if asof not in forward_dates:
        raise ValueError(f"advisory asof {asof} is not a stored forward trading day")

    store = SnapshotStore(snapshot_root)
    industry_asof = _latest_snapshot_key(snapshot_root, "index_member_all")
    # Statement readers can only load periods whose snapshots EXIST. A forward day
    # inside a fresh quarter (e.g. 2026-07-10 vs period 20260630) precedes most of
    # that period's announcements — the store legitimately has no snapshot yet, so
    # the period bound is clamped to the newest stored statement period (PIT-honest:
    # un-ingested late announcements are an INGESTION gap, disclosed in the status).
    statements_through = min(
        _latest_snapshot_key(snapshot_root, endpoint)
        for endpoint in (
            "fina_indicator_vip",
            "income_vip",
            "cashflow_vip",
            "balancesheet_vip",
        )
    )
    inputs = build_r3_inputs(
        store,
        snapshot_root,
        last_period_date=min(forward_dates[-1], statements_through),
        industry_asof=industry_asof,
    )
    panel = build_sleeve_forward_panel(
        split,
        store,
        inputs=inputs,
        forward_dates=forward_dates,
        rebalance_dates=sorted({*schedule, asof}),
    )
    ranker_table = build_sleeve_ranker_table(panel)
    if ranker_table.empty:
        raise ValueError("forward sleeve ranker table is empty — fail-closed")

    advisory = sleeve_advisory(
        ranker_table, asof=asof, store=store, snapshot_root=snapshot_root
    )

    arms, sleeve_periods, baseline_periods, bench_periods, realized_mdd = (
        _accrued_window_arms(
            store=store,
            ranker_table=ranker_table,
            schedule=schedule,
            forward_dates=forward_dates,
        )
    )

    kill = evaluate_kill_switch(
        sleeve_periods=sleeve_periods,
        baseline_periods=baseline_periods,
        bench_periods=bench_periods,
        realized_mdd=realized_mdd,
    )
    n_periods = len(sleeve_periods)
    status = forward_status(kill, n_periods)
    return {
        "product": PRODUCT,
        "spec_hash": spec_hash(),
        "registration": asdict(reg),
        "status": status,
        "forward": {
            "test_end": test_end,
            "start": forward_dates[0],
            "end": forward_dates[-1],
            "trading_days": len(forward_dates),
            "schedule_rebalances": schedule,
            "complete_periods": n_periods,
            "statement_periods_through": statements_through,
        },
        "kill_switch": kill,
        "arms": {
            label: asdict(arm) | {"period_returns": list(arm.period_returns)}
            for label, arm in arms.items()
        },
        "returns_note": (
            "returns are DISCLOSED for monitoring only — certification is "
            "kill-switch survival, never a significance test"
        ),
        "advisory": advisory,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument("--registration", default=DEFAULT_REGISTRATION)
    parser.add_argument(
        "--register",
        action="store_true",
        help="write the pre-registration if absent (first run only)",
    )
    parser.add_argument(
        "--advisory-asof",
        default="",
        help="trade date for the target-book advisory (default: latest forward day)",
    )
    parser.add_argument("--out", default=DEFAULT_STATUS)
    args = parser.parse_args()

    result = run_forward(
        snapshot_root=args.snapshot_root,
        lock_path=args.lock,
        registration_path=args.registration,
        register=args.register,
        advisory_asof=args.advisory_asof,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    brief = {
        "status": result["status"],
        "forward": result["forward"],
        "kill_switch": result["kill_switch"],
        "advisory": result["advisory"],
    }
    print(json.dumps(brief, indent=2, ensure_ascii=False))
    print(f"\n[written: {out}]")


if __name__ == "__main__":
    main()


__all__ = [
    "BASELINE_LABEL",
    "FEATURE_BUFFER_TD",
    "ForwardRegistration",
    "build_sleeve_forward_panel",
    "evaluate_kill_switch",
    "forward_schedule",
    "forward_status",
    "load_or_register",
    "run_forward",
    "sleeve_advisory",
    "trailing_underperf_streak",
]
