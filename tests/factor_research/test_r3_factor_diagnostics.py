"""Tests for the round-3 factor diagnostics (R3-3).

Cover the pure inclusion-gate helpers (carry increment, collinearity flag) and a
smoke build of the full Markdown report over a synthetic r3 panel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from scripts.factor_research.benchmark_relative import CARRY_FACTORS
from scripts.factor_research.factor_lib import R3_FACTOR_NAMES
from scripts.factor_research.ingest_round2_data import (
    EP_BALANCESHEET,
    EP_CASHFLOW,
    EP_FINA,
    EP_INCOME,
)
from scripts.factor_research.r2_factor_diagnostics import FactorVerdict
from scripts.factor_research.r3_factor_diagnostics import (
    COLLINEARITY_CEILING,
    _collinearity_section,
    _max_carry_collinearity,
    build_report,
    build_statement_audits,
    carry_increment,
    redundant_factors,
)

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)


def _v(factor: str, t: float, *, aligned: bool, signal: bool) -> FactorVerdict:
    return FactorVerdict(
        factor=factor,
        best_horizon="fwd_ret_20d",
        best_ic=0.02,
        best_t=t,
        aligned=aligned,
        has_signal=signal,
    )


class TestCarryIncrement:
    def test_keeps_only_aligned_signal_neut_factors(self) -> None:
        vs = [
            _v("sue_neut", 4.0, aligned=True, signal=True),  # kept
            _v("accr_neut", 5.0, aligned=False, signal=True),  # dropped (misaligned)
            _v("asset_growth_neut", 1.0, aligned=True, signal=False),  # dropped (weak)
        ]
        assert carry_increment(vs) == ("sue",)

    def test_empty_when_none_pass(self) -> None:
        vs = [_v("sue_neut", 1.0, aligned=True, signal=False)]
        assert carry_increment(vs) == ()

    def test_redundant_factor_excluded_even_if_ic_passes(self) -> None:
        # codex R3-3 P2: a factor passing the IC gate but flagged redundant
        # (|corr| > ceiling) must NOT be carried.
        vs = [
            _v("sue_neut", 4.0, aligned=True, signal=True),
            _v("accr_neut", 5.0, aligned=True, signal=True),
        ]
        assert carry_increment(vs, redundant=frozenset({"accr"})) == ("sue",)


class TestCollinearity:
    def _corr(self) -> pd.DataFrame:
        cols = [*CARRY_FACTORS, *R3_FACTOR_NAMES]
        df = pd.DataFrame(0.1, index=cols, columns=cols)
        # accr is highly collinear with ep_ttm; asset_growth mildly with accr
        df.loc["accr", "ep_ttm"] = df.loc["ep_ttm", "accr"] = 0.85
        df.loc["accr", "asset_growth"] = df.loc["asset_growth", "accr"] = 0.75
        return df

    def test_max_carry_collinearity(self) -> None:
        name, val = _max_carry_collinearity(self._corr(), "accr")
        assert name == "ep_ttm"
        assert val == pytest.approx(0.85)

    def test_collinearity_section_flags_redundant(self) -> None:
        section = _collinearity_section(self._corr())
        assert "accr" in section
        assert "**YES**" in section  # accr↔ep_ttm 0.85 > 0.7 ceiling flagged
        assert "one balance-sheet-quality axis" in section  # accr↔ag 0.75 > 0.7

    def test_redundant_factors_set(self) -> None:
        # accr↔ep_ttm 0.85 > ceiling → accr redundant; sue/asset_growth vs carry
        # are 0.1 → not redundant (asset_growth's 0.75 is vs accr, NOT a carry).
        assert redundant_factors(self._corr()) == frozenset({"accr"})
        assert COLLINEARITY_CEILING == pytest.approx(0.7)


def _synthetic_r3_panel() -> pd.DataFrame:
    """A small but valid r3 panel: 3 dates × 25 codes, all factor columns present."""
    rows = []
    for di, date in enumerate(("20200110", "20200117", "20200124")):
        for i in range(25):
            row: dict[str, object] = {
                "date": date,
                "code": f"6000{i:02d}",
                "ts_code": f"6000{i:02d}.SH",
                "industry_l1": "801080.SI" if i % 2 == 0 else "801150.SI",
                "circ_mv": 1e5 + i * 1e4,
                "log_circ_mv": float(11 + i * 0.05),
                "fwd_ret_5d": (i - 12) * 0.001 + di * 0.0001,
                "fwd_ret_10d": (i - 12) * 0.0012,
                "fwd_ret_20d": (i - 12) * 0.0015,
            }
            for f in (*CARRY_FACTORS, *R3_FACTOR_NAMES):
                row[f] = float((i * 7 + di * 3 + hash(f) % 11) % 17) - 8.0
            rows.append(row)
    return pd.DataFrame(rows)


def test_build_report_smoke() -> None:
    panel = _synthetic_r3_panel()
    report = build_report(panel, audits=[], industry_coverage=0.66)
    assert "# Round-3 factor diagnostics" in report
    assert "Carry decision" in report
    for f in R3_FACTOR_NAMES:
        assert f in report
    # R3_CARRY line always lists the round-2 eleven
    assert "ret_5d" in report and "rev_yoy" in report


def test_build_statement_audits(tmp_path: Path) -> None:
    store = SnapshotStore(str(tmp_path))
    for endpoint, field in (
        (EP_FINA, "profit_dedt"),
        (EP_INCOME, "n_income"),
        (EP_CASHFLOW, "n_cashflow_act"),
        (EP_BALANCESHEET, "total_assets"),
    ):
        frame = pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "end_date": ["20231231"],
                "ann_date": ["20240330"],
                "report_type": ["1"],
                "update_flag": ["1"],
                field: [1.0e10],
            }
        )
        snap = MarketDataSnapshot.create(
            vendor="tushare",
            endpoint=endpoint,
            params={"period": "20231231"},
            trade_date="20231231",
            raw_payload=canonical_csv_bytes(frame),
            encoding="csv",
            compression="none",
            fetch_time_utc=FIXED_NOW,
            metadata={"rows": 1},
        )
        store.put(snap)
    audits = build_statement_audits(store, ["20231231"])
    assert len(audits) == 4
    labels = [lbl for lbl, _ in audits]
    assert any("profit_dedt" in lbl for lbl in labels)
    for _, audit in audits:
        assert audit.n_codes == 1
        assert audit.n_restated_code_periods == 0  # single vintage each
