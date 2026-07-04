"""Augment the A0 crowding panel with the D2 defensive-universe filter columns.

Candidate D2 (``defensive-candidate-D2-reversal-on-defensive-universe-2026-07-03.md``)
ranks the SAME reversal survivors as A0 (``exit_veto_panel.build_ranker_table``), only
over a defensive universe. The reversal ranker consumes the existing A0 crowding panel
(``panel_train_val_crowding.csv``, 5d cadence — already carrying ``rev_1d`` / ``max_5d``
/ ``turn_spike`` / ``ideal_amplitude_20d`` / ``vol_20d`` / ``max_20d`` / ``industry_l1``
/ ``log_circ_mv``); the ONLY thing it is missing is the three RAW columns the defensive
filter reads: ``dv_ratio`` (dividend branch) and ``roe`` / ``gpm`` (quality branch).

This module is an INCREMENTAL column-adder, NOT a full panel rebuild: it takes each
``(date × ts_code)`` row of the A0 crowding panel verbatim (rows unchanged, byte-exact,
so A0's neutralized ranker table stays byte-reproducible) and left-joins the three new
columns computed on the SAME grid with the D1 PIT primitives:

  * ``dv_ratio`` — that day's ``daily_basic`` dividend yield (same read as
    ``defensive_d1_panel._ingest_day_d1``, PIT, no look-ahead);
  * ``roe`` / ``gpm`` — the ``FundamentalsPIT.asof`` roe / grossprofit_margin with the
    ``ann_date < d`` gate (identical construction to AF-003 / ``defensive_d1_panel``).

Firewall (inherited): every panel date is asserted train_val-only via
``LockedSplit.assert_all_not_test`` and a non-train_val date fails closed — the sealed
test window is physically unreachable. Fail-closed (``None``) on missing / non-finite
input so a thin day never fabricates a filter value.
"""

from __future__ import annotations

import argparse
import io
import math
from pathlib import Path

import pandas as pd
import structlog

from backend.marketdata_snapshot.store import SnapshotStore

from .fundamentals_pit import FundamentalsPIT
from .ingest_round2_data import report_periods
from .locked_split import LockedSplit

log = structlog.get_logger(component="factor_research.defensive_d2_panel")

VENDOR = "tushare"

# The three RAW columns D2 adds to the A0 crowding panel (the filter inputs).
D2_ADDED_COLUMNS: tuple[str, ...] = ("dv_ratio", "roe", "gpm")


def _finite_or_none(value: float | None) -> float | None:
    """Coerce a missing / non-finite fundamental to ``None`` (never fabricate)."""
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _dividend_yields(store: SnapshotStore, day: str) -> dict[str, float]:
    """``{ts_code: dv_ratio}`` from the day's ``daily_basic`` snapshot (PIT).

    Mirrors ``defensive_d1_panel._ingest_day_d1``'s ``daily_basic`` read (same endpoint,
    same ``dv_ratio`` column) so the D2 dividend leg is the identical as-known value. A
    day with no snapshot yields an empty map (every code that day fails-closed to NaN).
    """
    snap = store.latest(vendor=VENDOR, endpoint="daily_basic", trade_date=day)
    if snap is None:
        return {}
    frame = pd.read_csv(io.BytesIO(snap.raw_payload), usecols=["ts_code", "dv_ratio"])
    out: dict[str, float] = {}
    for ts_code, dv in zip(
        frame["ts_code"].astype(str), frame["dv_ratio"], strict=True
    ):
        dv_f = float(dv)
        if math.isfinite(dv_f):
            out[ts_code] = dv_f
    return out


def build_d2_supplement(
    panel: pd.DataFrame,
    store: SnapshotStore,
    fundamentals: FundamentalsPIT,
) -> pd.DataFrame:
    """One row per ``(date, ts_code)`` of ``panel`` with dv_ratio / roe / gpm.

    Iterates the panel's own ``(date × ts_code)`` grid (grouped by date so each day's
    ``daily_basic`` is read once); ``roe`` / ``gpm`` come from the PIT fundamentals asof
    the date (``ann_date < d``). Values are ``None`` where the input is missing.
    """
    rows: list[dict[str, object]] = []
    for i, (day, grp) in enumerate(panel.groupby("date", sort=True)):
        day = str(day)
        dv_map = _dividend_yields(store, day)
        for ts_code in grp["ts_code"].astype(str):
            record = fundamentals.asof(ts_code, day)
            rows.append(
                {
                    "date": day,
                    "ts_code": ts_code,
                    "dv_ratio": dv_map.get(ts_code),
                    "roe": _finite_or_none(
                        record.get("roe") if record is not None else None
                    ),
                    "gpm": _finite_or_none(
                        record.get("grossprofit_margin")
                        if record is not None
                        else None
                    ),
                }
            )
        if i % 50 == 0:
            log.info("d2_panel_supplement_progress", day=day, rows=len(rows))
    return pd.DataFrame(rows, columns=["date", "ts_code", *D2_ADDED_COLUMNS])


def augment_panel(
    panel: pd.DataFrame,
    store: SnapshotStore,
    fundamentals: FundamentalsPIT,
) -> pd.DataFrame:
    """Left-join the three D2 filter columns onto the A0 panel (rows unchanged).

    Row count and order are preserved (a left-merge on the unique ``(date, ts_code)``
    key) so A0's neutralized ranker table stays byte-reproducible from the merged frame.
    """
    clash = [col for col in D2_ADDED_COLUMNS if col in panel.columns]
    if clash:
        raise ValueError(
            f"panel already carries D2 column(s) {clash} — refusing to overwrite"
        )
    supplement = build_d2_supplement(panel, store, fundamentals)
    key = ["date", "ts_code"]
    if supplement.duplicated(key).any():
        raise ValueError(
            "D2 supplement has duplicate (date, ts_code) keys — fail-closed"
        )
    n_before = len(panel)
    merged = panel.merge(supplement, on=key, how="left", validate="one_to_one")
    if len(merged) != n_before:
        raise ValueError(
            f"merge changed row count {n_before} -> {len(merged)} — fail-closed"
        )
    return merged


def _coverage_by_year(panel: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Per-year dv_ratio / quality (roe ∧ gpm) coverage — honest disclosure."""
    out: dict[str, dict[str, float]] = {}
    years = panel["date"].astype(str).str[:4]
    for year, idx in panel.groupby(years).groups.items():
        sub = panel.loc[idx]
        n = len(sub)
        dv_cov = float(sub["dv_ratio"].notna().mean()) if n else 0.0
        qual_cov = (
            float((sub["roe"].notna() & sub["gpm"].notna()).mean()) if n else 0.0
        )
        out[str(year)] = {"rows": float(n), "dv_ratio": dv_cov, "quality": qual_cov}
    return out


def build_defensive_d2_panel(
    *,
    crowding_panel_path: str,
    snapshot_root: str,
    lock_path: str,
) -> pd.DataFrame:
    """Load + firewall the A0 crowding panel → augment with the D2 filter columns."""
    panel = pd.read_csv(
        crowding_panel_path, dtype={"date": str, "code": str, "ts_code": str}
    )
    split = LockedSplit.load(lock_path, snapshot_root)
    panel_dates = sorted(panel["date"].astype(str).unique())
    split.assert_all_not_test(panel_dates)
    non_tv = sorted(set(panel_dates) - set(split.train_val_dates))
    if non_tv:
        raise ValueError(
            f"crowding panel has non-train_val dates (e.g. {non_tv[:3]}) — fail-closed"
        )
    store = SnapshotStore(snapshot_root)
    last_date = panel_dates[-1]
    fundamentals = FundamentalsPIT.build(store, report_periods(2015, last_date))
    log.info(
        "d2_panel_fundamentals",
        codes=len(fundamentals.by_code),
        last_period_date=last_date,
    )
    return augment_panel(panel, store, fundamentals)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crowding-panel",
        default="data/factor_research/panel_train_val_crowding.csv",
    )
    parser.add_argument("--snapshot-root", default="data/marketdata_pit")
    parser.add_argument("--lock", default="config/research/test_set_lock.json")
    parser.add_argument("--out", default="data/factor_research/panel_train_val_d2.csv")
    args = parser.parse_args()

    panel = build_defensive_d2_panel(
        crowding_panel_path=args.crowding_panel,
        snapshot_root=args.snapshot_root,
        lock_path=args.lock,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out, index=False)
    coverage = _coverage_by_year(panel)
    log.info("d2_panel_written", out=str(out), rows=len(panel))
    print(f"[written: {out}] rows={len(panel)} dates={panel['date'].nunique()}")
    print("coverage by year (dv_ratio / quality[roe∧gpm]):")
    for year in sorted(coverage):
        c = coverage[year]
        print(
            f"  {year}: rows={int(c['rows']):6d}  "
            f"dv={c['dv_ratio']:.2%}  quality={c['quality']:.2%}"
        )


if __name__ == "__main__":
    main()


__all__ = [
    "D2_ADDED_COLUMNS",
    "augment_panel",
    "build_d2_supplement",
    "build_defensive_d2_panel",
]
