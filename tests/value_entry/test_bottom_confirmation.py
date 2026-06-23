"""AF-004 bottom-confirmation + anti-chase gate — deterministic, fail-closed."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from backend.data.historical_ingest.serialization import canonical_csv_bytes
from backend.marketdata_snapshot.snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore
from backend.value_entry.bottom_confirmation import (
    BottomConfirmation,
    BottomConfirmationConfig,
    ChipCost,
    PriceWindow,
)

FIXED_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
DECISION = "20240401"


def _put_cyq(store: SnapshotStore, frame: pd.DataFrame) -> None:
    snap = MarketDataSnapshot.create(
        vendor="tushare",
        endpoint="cyq_perf",
        params={"trade_date": DECISION},
        trade_date=DECISION,
        raw_payload=canonical_csv_bytes(frame),
        encoding="csv",
        compression="none",
        fetch_time_utc=FIXED_NOW,
        metadata={"rows": int(len(frame))},
    )
    store.put(snap)


def _flat_low_volume_window() -> PriceWindow:
    # 20 bars: stabilised just above the low, recent turnover shrinks vs the run.
    closes = tuple([10.0] * 14 + [9.8, 9.9, 10.0, 10.1, 10.2, 10.3])
    amounts = tuple([1000.0] * 15 + [400.0, 380.0, 360.0, 350.0, 340.0])
    return PriceWindow(closes=closes, amounts=amounts)


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(str(tmp_path))


def test_confirmed_bottom_passes(store: SnapshotStore) -> None:
    _put_cyq(
        store,
        pd.DataFrame(
            {
                "ts_code": ["GOOD.SH"],
                "cost_50pct": ["10.0"],  # last close 10.3 ~ above cost, not extended
                "winner_rate": ["40.0"],  # not euphoric
                "his_high": ["20.0"],  # far below 52w high
            }
        ),
    )
    gate = BottomConfirmation.from_store(
        store, decision_date=DECISION, windows={"GOOD.SH": _flat_low_volume_window()}
    )
    sig = gate.evaluate("GOOD.SH", DECISION)
    assert sig is not None and sig.confirmed
    assert gate.confirmed("GOOD.SH", DECISION)


def test_chasing_high_winner_rate_rejected(store: SnapshotStore) -> None:
    _put_cyq(
        store,
        pd.DataFrame(
            {
                "ts_code": ["HOT.SH"],
                "cost_50pct": ["10.0"],
                "winner_rate": ["95.0"],  # euphoric → reject
                "his_high": ["20.0"],
            }
        ),
    )
    gate = BottomConfirmation.from_store(
        store, decision_date=DECISION, windows={"HOT.SH": _flat_low_volume_window()}
    )
    sig = gate.evaluate("HOT.SH", DECISION)
    assert sig is not None
    assert not sig.winner_rate_ok
    assert not sig.confirmed


def test_near_52w_high_rejected(store: SnapshotStore) -> None:
    _put_cyq(
        store,
        pd.DataFrame(
            {
                "ts_code": ["TOP.SH"],
                "cost_50pct": ["10.0"],
                "winner_rate": ["40.0"],
                "his_high": ["10.4"],  # last 10.3 within 10% of his_high → chasing
            }
        ),
    )
    gate = BottomConfirmation.from_store(
        store, decision_date=DECISION, windows={"TOP.SH": _flat_low_volume_window()}
    )
    sig = gate.evaluate("TOP.SH", DECISION)
    assert sig is not None and not sig.below_recent_high and not sig.confirmed


def test_no_volume_shrink_rejected(store: SnapshotStore) -> None:
    _put_cyq(
        store,
        pd.DataFrame(
            {
                "ts_code": ["VOL.SH"],
                "cost_50pct": ["10.0"],
                "winner_rate": ["40.0"],
                "his_high": ["20.0"],
            }
        ),
    )
    # Rising turnover into the recent window (放量) → not 缩量.
    closes = tuple([10.0] * 14 + [9.8, 9.9, 10.0, 10.1, 10.2, 10.3])
    amounts = tuple([300.0] * 15 + [900.0, 950.0, 1000.0, 1100.0, 1200.0])
    gate = BottomConfirmation.from_store(
        store,
        decision_date=DECISION,
        windows={"VOL.SH": PriceWindow(closes=closes, amounts=amounts)},
    )
    sig = gate.evaluate("VOL.SH", DECISION)
    assert sig is not None and not sig.volume_shrinking and not sig.confirmed


def test_breakdown_fresh_low_rejected(store: SnapshotStore) -> None:
    _put_cyq(
        store,
        pd.DataFrame(
            {
                "ts_code": ["FALL.SH"],
                "cost_50pct": ["9.0"],
                "winner_rate": ["20.0"],
                "his_high": ["20.0"],
            }
        ),
    )
    # Falling knife — last close is the window minimum (fresh low).
    closes = tuple([12.0 - 0.1 * i for i in range(20)])  # monotone down → last is min
    amounts = tuple([400.0] * 20)
    gate = BottomConfirmation.from_store(
        store,
        decision_date=DECISION,
        windows={"FALL.SH": PriceWindow(closes=closes, amounts=amounts)},
    )
    sig = gate.evaluate("FALL.SH", DECISION)
    assert sig is not None and not sig.no_breakdown and not sig.confirmed


def test_missing_chip_data_fail_closed(store: SnapshotStore) -> None:
    # No cyq_perf snapshot at all → every code fails (no chip, no 埋伏).
    gate = BottomConfirmation.from_store(
        store, decision_date=DECISION, windows={"X.SH": _flat_low_volume_window()}
    )
    assert gate.evaluate("X.SH", DECISION) is None
    assert not gate.confirmed("X.SH", DECISION)


def test_short_window_fail_closed(store: SnapshotStore) -> None:
    _put_cyq(
        store,
        pd.DataFrame(
            {
                "ts_code": ["SHORT.SH"],
                "cost_50pct": ["10.0"],
                "winner_rate": ["40.0"],
                "his_high": ["20.0"],
            }
        ),
    )
    # Fewer than long_window bars → insufficient data → None (fail-closed).
    gate = BottomConfirmation.from_store(
        store,
        decision_date=DECISION,
        windows={"SHORT.SH": PriceWindow(closes=(10.0, 10.1), amounts=(1.0, 1.0))},
    )
    assert gate.evaluate("SHORT.SH", DECISION) is None


def test_deterministic_replay(store: SnapshotStore) -> None:
    _put_cyq(
        store,
        pd.DataFrame(
            {
                "ts_code": ["GOOD.SH"],
                "cost_50pct": ["10.0"],
                "winner_rate": ["40.0"],
                "his_high": ["20.0"],
            }
        ),
    )
    gate = BottomConfirmation.from_store(
        store, decision_date=DECISION, windows={"GOOD.SH": _flat_low_volume_window()}
    )
    assert gate.evaluate("GOOD.SH", DECISION) == gate.evaluate("GOOD.SH", DECISION)


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        BottomConfirmationConfig(short_window=0)
    with pytest.raises(ValueError):
        BottomConfirmationConfig(long_window=3, short_window=5)
    with pytest.raises(ValueError):
        BottomConfirmationConfig(winner_rate_max=150.0)
    with pytest.raises(ValueError):
        BottomConfirmationConfig(max_runup_pct=-0.1)


def test_chip_cost_dataclass_used_directly() -> None:
    # The gate is usable with directly-supplied chips (no store), for callers
    # that already hold the chip facts.
    gate = BottomConfirmation(
        windows={"D.SH": _flat_low_volume_window()},
        cyq_by_code={
            "D.SH": ChipCost(cost_50pct=10.0, winner_rate=40.0, his_high=20.0)
        },
    )
    assert gate.confirmed("D.SH", DECISION)


def test_stale_decision_date_fail_closed(store: SnapshotStore) -> None:
    # A gate built at DECISION must never confirm on a different date (stale
    # chips/windows would otherwise leak across dates, codex AF-004 P2).
    _put_cyq(
        store,
        pd.DataFrame(
            {
                "ts_code": ["GOOD.SH"],
                "cost_50pct": ["10.0"],
                "winner_rate": ["40.0"],
                "his_high": ["20.0"],
            }
        ),
    )
    gate = BottomConfirmation.from_store(
        store, decision_date=DECISION, windows={"GOOD.SH": _flat_low_volume_window()}
    )
    assert gate.confirmed("GOOD.SH", DECISION)
    assert gate.evaluate("GOOD.SH", "20240402") is None  # different date → fail-closed
    assert not gate.confirmed("GOOD.SH", "20240402")


def test_direct_path_invalid_chip_fail_closed() -> None:
    # The direct-construction path bypasses from_store's CSV finiteness filter →
    # evaluate must still reject a non-finite / out-of-range chip (codex P2).
    bad_chips = [
        ChipCost(cost_50pct=10.0, winner_rate=float("-inf"), his_high=20.0),
        ChipCost(cost_50pct=10.0, winner_rate=40.0, his_high=float("inf")),
        ChipCost(cost_50pct=float("nan"), winner_rate=40.0, his_high=20.0),
        ChipCost(cost_50pct=10.0, winner_rate=150.0, his_high=20.0),  # out of [0,100]
    ]
    for chip in bad_chips:
        gate = BottomConfirmation(
            windows={"D.SH": _flat_low_volume_window()},
            cyq_by_code={"D.SH": chip},
        )
        assert gate.evaluate("D.SH", DECISION) is None


def test_negative_turnover_fail_closed() -> None:
    # A negative turnover could masquerade as 缩量 → fail-closed (codex P2).
    closes = tuple([10.0] * 14 + [9.8, 9.9, 10.0, 10.1, 10.2, 10.3])
    amounts = tuple([1000.0] * 18 + [-5.0, 340.0])  # one negative amount
    gate = BottomConfirmation(
        windows={"NEG.SH": PriceWindow(closes=closes, amounts=amounts)},
        cyq_by_code={
            "NEG.SH": ChipCost(cost_50pct=10.0, winner_rate=40.0, his_high=20.0)
        },
    )
    assert gate.evaluate("NEG.SH", DECISION) is None


def test_config_nonfinite_rejected() -> None:
    with pytest.raises(ValueError):
        BottomConfirmationConfig(max_volume_shrink_ratio=float("inf"))
    with pytest.raises(ValueError):
        BottomConfirmationConfig(max_runup_pct=float("nan"))
