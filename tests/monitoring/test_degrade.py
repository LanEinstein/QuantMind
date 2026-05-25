"""Tests for backend.monitoring.degrade (suspension + anomaly trigger key, N-004)."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.models.market import WatchlistMarketSnapshot
from backend.monitoring.anomaly import AnomalyDirection, AnomalyKind, AnomalySignal
from backend.monitoring.degrade import (
    DegradeReason,
    anomaly_trigger_key,
    partition_by_suspension,
)

_NOW = datetime(2026, 5, 15, 2, 30, tzinfo=UTC)


def _spot(
    code: str,
    *,
    price: float = 4.5,
    prev_close: float = 4.5,
    volume: float = 1e6,
    amount: float = 3e8,
    change_pct: float = 0.0,
) -> WatchlistMarketSnapshot:
    return WatchlistMarketSnapshot(
        code=code, name="X", price=price, open=price, high=price, low=price,
        prev_close=prev_close, change_pct=change_pct, volume=volume,
        amount=amount, turnover_rate=0.5, source="adata", snapshot_at=_NOW,
    )


def test_suspended_position_degrades_cleanly() -> None:
    # price=0 → halted (no last-traded price on the spot view).
    spot = {"600519": _spot("600519", price=0.0)}
    part = partition_by_suspension(["600519"], spot)
    assert part.active_codes == ()
    assert len(part.degrades) == 1
    assert part.degrades[0].reason is DegradeReason.SUSPENDED
    assert part.degrades[0].code == "600519"


def test_active_position_not_degraded() -> None:
    spot = {"600519": _spot("600519")}
    part = partition_by_suspension(["600519"], spot)
    assert part.active_codes == ("600519",)
    assert part.degrades == ()


def test_zero_volume_and_amount_during_session_degrades() -> None:
    spot = {"600519": _spot("600519", volume=0.0, amount=0.0)}
    part = partition_by_suspension(["600519"], spot)
    assert part.degrades and part.degrades[0].reason is DegradeReason.SUSPENDED


def test_missing_spot_stays_active() -> None:
    # No spot snapshot → NOT a confirmed suspension; stays active (the data
    # quality freeze covers missing quotes downstream).
    part = partition_by_suspension(["600519"], {})
    assert part.active_codes == ("600519",)
    assert part.degrades == ()


def test_mixed_held_set_partitioned() -> None:
    spot = {
        "600519": _spot("600519"),               # active
        "000001": _spot("000001", price=0.0),    # suspended
    }
    part = partition_by_suspension(["600519", "000001"], spot)
    assert part.active_codes == ("600519",)
    assert [d.code for d in part.degrades] == ["000001"]


def test_code_suffix_normalised() -> None:
    spot = {"600519": _spot("600519", price=0.0)}
    part = partition_by_suspension(["600519.SH"], spot)
    assert part.degrades and part.degrades[0].code == "600519"


def test_anomaly_trigger_key_stable_per_code_kind() -> None:
    sig_a = AnomalySignal(
        code="600519", kind=AnomalyKind.PRICE_ZSCORE,
        direction=AnomalyDirection.DOWN, score=5.0, threshold=3.0,
        last_price=10.0, detail="x",
    )
    sig_b = AnomalySignal(
        code="600519", kind=AnomalyKind.PRICE_ZSCORE,
        direction=AnomalyDirection.UP, score=9.9, threshold=3.0,
        last_price=11.0, detail="y",
    )
    # Same (code, kind) → same key regardless of direction/score (dedup intent).
    assert anomaly_trigger_key(sig_a) == "600519:price_zscore"
    assert anomaly_trigger_key(sig_b) == "600519:price_zscore"
    sig_c = AnomalySignal(
        code="600519", kind=AnomalyKind.VOLUME_ZSCORE,
        direction=AnomalyDirection.UP, score=5.0, threshold=3.0,
        last_price=10.0, detail="z",
    )
    assert anomaly_trigger_key(sig_c) == "600519:volume_zscore"
