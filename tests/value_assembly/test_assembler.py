"""AF-002 value-score assembler — three foundations → live value_scores."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.fundamentals_pit.reader import (
    EP_BALANCESHEET,
    EP_CASHFLOW,
    EP_FINA,
    EP_INCOME,
)
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from backend.theme_mapping.models import PolicyTheme, PolicyThemeRegistry
from backend.theme_mapping.resolver import ThemeResolver
from backend.theme_mapping.sector_pit import SectorMembershipPIT
from backend.theme_research.sop_schema import ThemeTier
from backend.value_assembly.assembler import ValueScoreAssembler

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
DECISION = "20240401"  # a (synthetic) trading day with a daily_basic snapshot


def _put(
    store: SnapshotStore, endpoint: str, trade_date: str, frame: pd.DataFrame
) -> None:
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint=endpoint,
        params={"trade_date": trade_date},
        trade_date=trade_date,
        raw_payload=canonical_csv_bytes(frame),
        encoding="csv",
        compression="none",
        fetch_time_utc=FIXED_NOW,
        metadata={"rows": int(len(frame))},
    )
    store.put(snap)


def _resolver() -> ThemeResolver:
    registry = PolicyThemeRegistry(
        version="t",
        frozen=True,
        themes=(
            PolicyTheme(
                "semis",
                "集成电路",
                ThemeTier.NATIONAL_EVENT,
                "20150519",
                "中国制造2025",
                ("850816.SI",),
            ),
        ),
    )
    membership = SectorMembershipPIT.from_frame(
        pd.DataFrame(
            [
                ("ONTHEME.SH", "850816.SI", "20100101", ""),
                ("OFFTHEME.SH", "850999.SI", "20100101", ""),
            ],
            columns=["ts_code", "l3_code", "in_date", "out_date"],
        ).astype(str)
    )
    return ThemeResolver(registry, membership)


def _seed_fundamentals(store: SnapshotStore) -> None:
    period = "20231231"
    _put(
        store,
        EP_FINA,
        period,
        pd.DataFrame(
            {
                "ts_code": ["ONTHEME.SH", "OFFTHEME.SH"],
                "end_date": [period, period],
                "ann_date": ["20240320", "20240320"],
                "update_flag": ["0", "0"],
                "roe": [25.0, 5.0],
                "grossprofit_margin": [55.0, 20.0],
            }
        ),
    )
    for ep, field, vals in (
        (EP_INCOME, "n_income", [2.0e9, 4.0e8]),
        (EP_CASHFLOW, "n_cashflow_act", [2.3e9, 1.0e8]),
        (EP_BALANCESHEET, "total_assets", [1.0e10, 1.0e10]),
    ):
        _put(
            store,
            ep,
            period,
            pd.DataFrame(
                {
                    "ts_code": ["ONTHEME.SH", "OFFTHEME.SH"],
                    "end_date": [period, period],
                    "ann_date": ["20240320", "20240320"],
                    "report_type": ["1", "1"],
                    "update_flag": ["0", "0"],
                    field: vals,
                }
            ),
        )


def _seed_daily_basic(store: SnapshotStore) -> None:
    _put(
        store,
        "daily_basic",
        DECISION,
        pd.DataFrame(
            {
                "ts_code": ["ONTHEME.SH", "OFFTHEME.SH"],
                "dv_ratio": ["4.0", "0.5"],
                "pe_ttm": ["8.0", "60.0"],
                "pb": ["1.0", "6.0"],
            }
        ),
    )


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    s = SnapshotStore(str(tmp_path))
    _seed_fundamentals(s)
    _seed_daily_basic(s)
    return s


def test_on_theme_quality_cheap_outscores_off(store: SnapshotStore) -> None:
    asm = ValueScoreAssembler(store=store, resolver=_resolver())
    out = asm.assemble(codes=["ONTHEME.SH", "OFFTHEME.SH"], decision_date=DECISION)
    assert out["ONTHEME.SH"] > out["OFFTHEME.SH"]
    assert 0.0 <= out["OFFTHEME.SH"] <= out["ONTHEME.SH"] <= 1.0


def test_total_outage_is_none(store: SnapshotStore) -> None:
    asm = ValueScoreAssembler(store=store, resolver=_resolver())
    out = asm.assemble(codes=["GHOST.SH"], decision_date=DECISION)
    # No theme L3, no fundamentals, no daily_basic row → no value signal anywhere
    # → None so the runner falls back to the pure-quant path (bit-identical).
    assert out is None


def test_mixed_keeps_dict_with_zero_for_dataless_code(store: SnapshotStore) -> None:
    asm = ValueScoreAssembler(store=store, resolver=_resolver())
    out = asm.assemble(codes=["ONTHEME.SH", "GHOST.SH"], decision_date=DECISION)
    assert out is not None  # ONTHEME has signal → dict returned
    assert out["ONTHEME.SH"] > 0.0
    assert out["GHOST.SH"] == 0.0  # no component → conservative 0.0


def test_deterministic_replay(store: SnapshotStore) -> None:
    asm = ValueScoreAssembler(store=store, resolver=_resolver())
    codes = ["ONTHEME.SH", "OFFTHEME.SH"]
    assert asm.assemble(codes=codes, decision_date=DECISION) == asm.assemble(
        codes=codes, decision_date=DECISION
    )


def test_entry_gate_all_rejected_is_none(store: SnapshotStore) -> None:
    class _RejectAll:
        def confirmed(self, code: str, decision_date: str) -> bool:
            return False

    asm = ValueScoreAssembler(
        store=store, resolver=_resolver(), entry_gate=_RejectAll()
    )
    # No confirmed bottom today → no value signal → None (pure-quant fallback).
    assert asm.assemble(codes=["ONTHEME.SH"], decision_date=DECISION) is None


def test_entry_gate_selective(store: SnapshotStore) -> None:
    class _RejectOne:
        def confirmed(self, code: str, decision_date: str) -> bool:
            return code != "OFFTHEME.SH"  # reject OFFTHEME only

    asm = ValueScoreAssembler(
        store=store, resolver=_resolver(), entry_gate=_RejectOne()
    )
    out = asm.assemble(codes=["ONTHEME.SH", "OFFTHEME.SH"], decision_date=DECISION)
    assert out is not None
    assert out["ONTHEME.SH"] > 0.0  # confirmed → real score
    assert out["OFFTHEME.SH"] == 0.0  # rejected → forced 0.0


def test_entry_gate_passthrough_when_confirmed(store: SnapshotStore) -> None:
    class _AllowAll:
        def confirmed(self, code: str, decision_date: str) -> bool:
            return True

    base = ValueScoreAssembler(store=store, resolver=_resolver())
    gated = ValueScoreAssembler(
        store=store, resolver=_resolver(), entry_gate=_AllowAll()
    )
    codes = ["ONTHEME.SH"]
    # An allow-all gate must be bit-identical to no gate.
    assert gated.assemble(codes=codes, decision_date=DECISION) == base.assemble(
        codes=codes, decision_date=DECISION
    )
