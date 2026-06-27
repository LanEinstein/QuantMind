"""Shared building blocks for the QGR-4 / batch-B event-loop ablations.

The exit-veto (QGR-4), regime-derisk (B1) and defensive-sleeve (B2) ablations share
a load-bearing preamble and a few mappings that were copy-pasted across forks (codex
DRY review). The most safety-critical is the **PIT firewall** — load the panel,
assert it is train_val-only, neutralise, build the ranker table, and assert the
ACTUAL daily bar-read window (incl. the HORIZON extension) is ⊆ train_val. A divergent
copy could silently read sealed-test bytes while still reporting a green run, so the
canonical, tested version lives here.

This module is consumed by new ablations (B2 onward); the two earlier committed forks
retain their inline copies (correct + verified) and a later cleanup can converge them.
Pure/offline; the only IO is reading the PIT store + panel CSV. Never the live path.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import pandas as pd

from backend.backtest.event_loop import BarSource
from backend.backtest.strategy import CodeHealth

from . import exit_veto_panel as xv
from .baselines import buy_and_hold_baseline
from .exit_veto_ablation import ArmResult, _resolve_window
from .honest_gates import onc_effective_n
from .locked_split import LockedSplit, load_daily_calendar
from .neutralize import neutralize_panel
from .trial_ledger import TrialLedger, TrialRecord

# A score/percentile that dominates any z-mean ranker score (a permanently-held or
# rotation-winning destination). Mirrors derisk_overlay_panel.CASH_SCORE in magnitude.
PROTECTED_COMPOSITE: float = 1_000_000.0


def strong_protected_health() -> CodeHealth:
    """A destination's health: strong ⇒ never independently weak (never evicted).

    line1_percentile 1.0 (> P40) and entry_percentile 1.0 (no deterioration since
    entry) fail the 7-condition weakness gate, so the rotation engine keeps the
    destination slot — the shared basis for B1's winning cash challenger and B2's
    permanent sleeve.
    """
    return CodeHealth(
        line1_percentile=1.0,
        composite_score=PROTECTED_COMPOSITE,
        qualified=True,
        entry_percentile=1.0,
    )


def firewalled_ranker_table(
    *,
    panel_path: str,
    lock_path: str,
    snapshot_root: str,
    factors: Sequence[str],
    min_obs: int,
    winsor_quantile: float,
    smoke_periods: int | None,
    log: Callable[[str], object],
) -> tuple[pd.DataFrame, list[str], list[str], LockedSplit]:
    """Load + firewall + neutralise → ``(ranker_table, rebs, daily_days, split)``.

    The PIT red line (no look-ahead): the panel is asserted train_val-only, and the
    actual daily bar-read window (the rebalance span extended by HORIZON so the final
    positions get marked/filled) is asserted ⊆ train_val — the HORIZON extension must
    never reach a sealed-test byte. ``smoke_periods`` restricts to the first N rebalance
    dates for an end-to-end smoke.
    """
    log("[firewall] load panel + assert train_val only")
    panel = pd.read_csv(
        panel_path, dtype={"date": str, "code": str, "ts_code": str}
    )
    split = LockedSplit.load(lock_path, snapshot_root)
    panel_dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(panel_dates)
    non_tv = sorted(set(panel_dates) - set(split.train_val_dates))
    if non_tv:
        raise ValueError(
            f"panel has non-train_val dates (e.g. {non_tv[:3]}) — fail-closed"
        )

    log("[firewall] neutralize survivors + build ranker table")
    neut = neutralize_panel(
        panel, list(factors), min_obs=min_obs, winsor_quantile=winsor_quantile
    )
    ranker_table = xv.build_ranker_table(neut)
    if smoke_periods is not None:
        keep = set(
            sorted(ranker_table["date"].astype(str).unique())[:smoke_periods]
        )
        ranker_table = ranker_table[
            ranker_table["date"].astype(str).isin(keep)
        ].copy()

    rebs = sorted(ranker_table["date"].astype(str).unique())
    calendar = load_daily_calendar(snapshot_root)
    daily_days = _resolve_window(rebs, calendar, train_val=set(split.train_val_dates))
    # Firewall the ACTUAL bar-read window, not just the rebalance dates.
    split.assert_all_not_test(daily_days)
    return ranker_table, rebs, daily_days, split


def ledger_n_trials(
    ledger_path: str,
    arms: Sequence[ArmResult],
    window: tuple[str, str],
    *,
    persist: bool,
    family: str,
    round_label: str,
    description: str,
    ledger_date: str,
) -> int:
    """Append one ablation family (ONC-deduped) → the non-zeroing deflation N.

    The non-zeroing accounting itself lives in :class:`TrialLedger` (the single source
    of truth — ``with_legacy`` + ``deflation_n_trials``); this only constructs the
    family-specific record. ``persist=False`` (a smoke / sub-window) computes the
    deflation N without polluting the content-addressed ledger.
    """
    ledger = TrialLedger.with_legacy(ledger_path)
    matrix = [list(a.period_returns) for a in arms]
    eff = onc_effective_n(matrix) if len(matrix) > 1 else len(matrix)
    if persist:
        ledger.append(
            TrialRecord(
                round_label=round_label,
                kind="ablation",
                family=family,
                description=description,
                n_nominal_trials=len(arms),
                window_start=window[0],
                window_end=window[1],
                registered_at=ledger_date,
                effective_n=eff,
            )
        )
    return ledger.deflation_n_trials(onc_effective_n=eff)


def hold_baseline_arm(
    bar_source: BarSource,
    code: str,
    label: str,
    *,
    initial_capital_yuan: float,
    horizon: int,
    mdd_cap: float,
) -> ArmResult:
    """Full-invested buy-and-hold of ``code`` → ``ArmResult`` (a deployable hurdle)."""
    bh = buy_and_hold_baseline(
        bar_source=bar_source,
        asset_code=code,
        initial_capital_yuan=initial_capital_yuan,
        horizon=horizon,
    )
    return ArmResult(
        label=label,
        net_pnl_yuan=bh.net_pnl_yuan,
        total_return=bh.total_return,
        max_drawdown_pct=bh.max_drawdown_pct,
        monthly_turnover=0.0,
        fill_count=bh.fill_count,
        avg_exposure=bh.invested_fraction,
        conservation_ok=bh.conservation_ok,
        exposure_cap_violations=bh.exposure_cap_violations,
        period_returns=bh.period_returns,
        mdd_within_cap=bh.max_drawdown_pct <= mdd_cap,
    )


__all__ = [
    "PROTECTED_COMPOSITE",
    "firewalled_ranker_table",
    "hold_baseline_arm",
    "ledger_n_trials",
    "strong_protected_health",
]
