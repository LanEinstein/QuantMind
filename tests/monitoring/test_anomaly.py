"""Tests for backend.monitoring.anomaly (Line-2 pure-quant detection, N-001)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from backend.marketdata_snapshot import MarketDataSnapshot
from backend.monitoring.anomaly import (
    AnomalyConfig,
    AnomalyDetector,
    AnomalyDetectorError,
    AnomalyDirection,
    AnomalyKind,
    SkipReason,
    bollinger_breakout,
    ewma_deviation,
    price_zscore,
    volume_zscore,
)

_HEADER = "ts_code,name,listed_trading_days,closes,amounts"


def _snapshot(csv_text: str) -> MarketDataSnapshot:
    raw = csv_text.encode("utf-8")
    return MarketDataSnapshot(
        vendor="tushare",
        endpoint="monitor_frame",
        params={"trade_date": "20260522"},
        trade_date="20260522",
        raw_payload=raw,
        size=len(raw),
        encoding="csv",
        compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 22, tzinfo=UTC),
    )


def _series(values: list[float]) -> str:
    return "|".join(repr(v) for v in values)


def _row(
    *,
    ts_code: str = "600519.SH",
    name: str = "贵州茅台",
    closes: list[float],
    amounts: list[float] | None = None,
) -> str:
    if amounts is None:
        amounts = [3e8] * len(closes)
    return f"{ts_code},{name},300,{_series(closes)},{_series(amounts)}"


def _frame(rows: list[str]) -> str:
    return "\n".join([_HEADER, *rows])


# A calm, slightly-rising 30-bar baseline: tiny alternating noise so σ > 0 but
# no detector trips. 30 bars ≥ all detector minimums.
def _calm_closes(n: int = 30, base: float = 100.0) -> list[float]:
    return [base + (0.1 if i % 2 else -0.1) for i in range(n)]


def _calm_amounts(n: int = 30, base: float = 3e8) -> list[float]:
    return [base + (1e6 if i % 2 else -1e6) for i in range(n)]


# ---------------------------------------------------------------------------
# scan() — held-set filtering + skip reasons
# ---------------------------------------------------------------------------


def test_empty_held_yields_no_signals() -> None:
    snap = _snapshot(_frame([_row(closes=_calm_closes())]))
    result = AnomalyDetector().scan(snap, [], "LINE2-MON-1")
    assert result.signals == ()
    assert result.scanned_codes == ()
    assert result.skipped == {}


def test_only_held_codes_scanned() -> None:
    rows = [
        _row(ts_code="600519.SH", closes=_calm_closes()),
        _row(ts_code="000001.SZ", closes=_calm_closes()),
    ]
    snap = _snapshot(_frame(rows))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-2")
    assert result.scanned_codes == ("600519",)
    # the non-held code is never consumed for lineage
    assert all(cr.row_key == "600519.SH" for cr in result.manifest.consumed_rows)


def test_held_code_absent_from_snapshot_skipped() -> None:
    snap = _snapshot(_frame([_row(ts_code="600519.SH", closes=_calm_closes())]))
    result = AnomalyDetector().scan(snap, ["000001"], "LINE2-MON-3")
    assert result.skipped == {"000001": SkipReason.NOT_IN_SNAPSHOT.value}
    assert result.scanned_codes == ()


def test_code_suffix_normalised_in_held_set() -> None:
    snap = _snapshot(_frame([_row(ts_code="600519.SH", closes=_calm_closes())]))
    result = AnomalyDetector().scan(snap, ["600519.SH"], "LINE2-MON-3b")
    assert result.scanned_codes == ("600519",)


def test_non_positive_last_close_skipped_no_price() -> None:
    closes = _calm_closes()
    closes[-1] = 0.0  # halted / missing quote
    snap = _snapshot(_frame([_row(closes=closes)]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-4")
    assert result.skipped == {"600519": SkipReason.NO_PRICE.value}
    assert result.signals == ()


def test_non_positive_interior_close_skipped_no_price() -> None:
    # M4 regression (production-hardening 2026-06-25): an INTERIOR non-positive
    # close (a corrupt/halted bar) with a valid LAST close must still be screened
    # NO_PRICE. _returns yields 0.0 for that step, which would inflate the
    # baseline σ and MASK a real anomaly; the prior screen only checked the last
    # bar and would have scanned the poisoned series. Intentionally stricter than
    # the Line-1 screener (which screens only the last bar).
    closes = _calm_closes()
    closes[len(closes) // 2] = 0.0  # corrupt interior bar; last bar still valid
    assert closes[-1] > 0  # guard: the LAST bar is fine, only an interior one
    snap = _snapshot(_frame([_row(closes=closes)]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-4b")
    assert result.skipped == {"600519": SkipReason.NO_PRICE.value}
    assert result.signals == ()
    assert result.scanned_codes == ()


def test_negative_interior_close_skipped_no_price() -> None:
    # Same screen also catches a negative interior bar (clearly corrupt data).
    closes = _calm_closes()
    closes[5] = -1.0
    snap = _snapshot(_frame([_row(closes=closes)]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-4c")
    assert result.skipped == {"600519": SkipReason.NO_PRICE.value}


def test_insufficient_history_skipped() -> None:
    snap = _snapshot(_frame([_row(closes=[100.0, 101.0, 102.0])]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-5")
    assert result.skipped == {"600519": SkipReason.INSUFFICIENT_HISTORY.value}


def test_duplicate_held_row_marked_malformed() -> None:
    rows = [
        _row(ts_code="600519.SH", closes=_calm_closes()),
        _row(ts_code="600519.SH", closes=_calm_closes()),
    ]
    snap = _snapshot(_frame(rows))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-6")
    assert result.skipped == {"600519": SkipReason.MALFORMED_ROW.value}
    assert result.signals == ()


def test_valid_plus_malformed_duplicate_marked_malformed() -> None:
    # One valid copy + one malformed copy of the SAME held code. The valid
    # copy must NOT be scanned: replay resolves the duplicate row_key to the
    # later (malformed) line, so scanning the valid copy would drift the
    # manifest hash (codex N-001 P2). Fail-closed → malformed skip.
    rows = [
        _row(ts_code="600519.SH", closes=_calm_closes()),
        "600519.SH,茅台,300,1.0|abc,3e8|3e8",  # malformed duplicate, later
    ]
    snap = _snapshot(_frame(rows))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-dup")
    assert result.skipped == {"600519": SkipReason.MALFORMED_ROW.value}
    assert result.scanned_codes == ()
    # No consumed-row lineage recorded for an ambiguous duplicate.
    assert result.manifest.consumed_rows == ()


def test_ewma_span_larger_than_window_does_not_block_other_detectors() -> None:
    # window=20, ewma_span=60: a 25-bar spike has enough history for price
    # z-score + Bollinger but not EWMA. The row must still be scanned (not
    # globally skipped as insufficient_history) and the spike detected by the
    # cheaper detectors (codex N-001 P2).
    closes = _calm_closes(25)
    closes[-1] = closes[-2] * 1.10
    snap = _snapshot(_frame([_row(closes=closes, amounts=_calm_amounts(25))]))
    cfg = AnomalyConfig(window=20, ewma_span=60)
    result = AnomalyDetector(cfg).scan(snap, ["600519"], "LINE2-MON-ewma")
    assert result.scanned_codes == ("600519",)
    assert "600519" not in result.skipped
    kinds = {s.kind for s in result.signals}
    assert AnomalyKind.PRICE_ZSCORE in kinds
    # EWMA cannot run (62 bars needed) — it simply contributes no signal.
    assert AnomalyKind.EWMA_DEVIATION not in kinds


def test_calm_series_no_false_positive() -> None:
    snap = _snapshot(_frame([_row(closes=_calm_closes(), amounts=_calm_amounts())]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-7")
    assert result.signals == ()
    assert result.scanned_codes == ("600519",)


# ---------------------------------------------------------------------------
# detector firings
# ---------------------------------------------------------------------------


def test_price_zscore_up_spike_detected() -> None:
    closes = _calm_closes()
    closes[-1] = closes[-2] * 1.10  # +10% jump vs a ~0.1% noise baseline
    snap = _snapshot(_frame([_row(closes=closes, amounts=_calm_amounts())]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-8")
    kinds = {s.kind for s in result.signals}
    assert AnomalyKind.PRICE_ZSCORE in kinds
    pz = next(s for s in result.signals if s.kind == AnomalyKind.PRICE_ZSCORE)
    assert pz.direction is AnomalyDirection.UP
    assert pz.score > pz.threshold == 3.0


def test_price_zscore_down_crash_detected() -> None:
    closes = _calm_closes()
    closes[-1] = closes[-2] * 0.90  # -10% crash
    snap = _snapshot(_frame([_row(closes=closes, amounts=_calm_amounts())]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-9")
    pz = next(s for s in result.signals if s.kind == AnomalyKind.PRICE_ZSCORE)
    assert pz.direction is AnomalyDirection.DOWN


def test_volume_zscore_spike_detected() -> None:
    amounts = _calm_amounts()
    amounts[-1] = 5e9  # ~16x the calm baseline
    snap = _snapshot(_frame([_row(closes=_calm_closes(), amounts=amounts)]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-10")
    vz = next(s for s in result.signals if s.kind == AnomalyKind.VOLUME_ZSCORE)
    assert vz.direction is AnomalyDirection.UP
    assert vz.score > 3.0


def test_bollinger_breakout_detected() -> None:
    # Flat-ish then a large jump on the last bar → outside MA ± 2σ.
    closes = [100.0 + (0.05 if i % 2 else -0.05) for i in range(25)]
    closes[-1] = 105.0
    snap = _snapshot(_frame([_row(closes=closes, amounts=_calm_amounts(25))]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-11")
    bb = next(s for s in result.signals if s.kind == AnomalyKind.BOLLINGER_BREAKOUT)
    assert bb.direction is AnomalyDirection.UP
    assert bb.score > bb.threshold == 2.0


def test_ewma_deviation_detected() -> None:
    closes = _calm_closes()
    closes[-1] = 108.0  # large jump off the EWMA expectation
    snap = _snapshot(_frame([_row(closes=closes, amounts=_calm_amounts())]))
    result = AnomalyDetector().scan(snap, ["600519"], "LINE2-MON-12")
    assert any(s.kind == AnomalyKind.EWMA_DEVIATION for s in result.signals)


# ---------------------------------------------------------------------------
# determinism + lineage (PIT reproducibility)
# ---------------------------------------------------------------------------


def test_scan_deterministic() -> None:
    closes = _calm_closes()
    closes[-1] = closes[-2] * 1.10
    snap = _snapshot(_frame([_row(closes=closes, amounts=_calm_amounts())]))
    det = AnomalyDetector()
    r1 = det.scan(snap, ["600519"], "LINE2-MON-13")
    r2 = det.scan(snap, ["600519"], "LINE2-MON-13")
    assert r1.signals == r2.signals


def test_manifest_records_consumed_held_rows() -> None:
    rows = [
        _row(ts_code="600519.SH", closes=_calm_closes()),
        _row(ts_code="000001.SZ", closes=_calm_closes()),
    ]
    snap = _snapshot(_frame(rows))
    result = AnomalyDetector().scan(snap, ["600519", "000001"], "LINE2-MON-14")
    assert result.manifest.signal_id == "LINE2-MON-14"
    assert result.manifest.snapshot_ids == (snap.snapshot_id,)
    assert len(result.manifest.consumed_rows) == 2
    assert result.manifest.feature_code_version == "monitoring.anomaly/v1"
    assert "anomaly_config" in result.manifest.config_hashes


def test_config_hash_changes_with_threshold() -> None:
    snap = _snapshot(_frame([_row(closes=_calm_closes())]))
    h_a = AnomalyDetector(AnomalyConfig(zscore_threshold=3.0)).scan(
        snap, ["600519"], "a"
    ).manifest.config_hashes["anomaly_config"]
    h_b = AnomalyDetector(AnomalyConfig(zscore_threshold=2.5)).scan(
        snap, ["600519"], "b"
    ).manifest.config_hashes["anomaly_config"]
    assert h_a != h_b


# ---------------------------------------------------------------------------
# structural fail-closed
# ---------------------------------------------------------------------------


def test_bad_encoding_raises() -> None:
    raw = b"not csv"
    snap = MarketDataSnapshot(
        vendor="x", endpoint="y", params={}, trade_date="20260522",
        raw_payload=raw, size=len(raw), encoding="parquet", compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 22, tzinfo=UTC),
    )
    with pytest.raises(AnomalyDetectorError):
        AnomalyDetector().scan(snap, ["600519"], "z")


def test_bad_header_raises() -> None:
    snap = _snapshot("wrong,header\n600519.SH,x")
    with pytest.raises(AnomalyDetectorError):
        AnomalyDetector().scan(snap, ["600519"], "z")


def test_malformed_floats_row_skipped() -> None:
    snap = _snapshot(_frame(["600519.SH,茅台,300,1.0|abc|2.0,3e8|3e8"]))
    result = AnomalyDetector().scan(snap, ["600519"], "z")
    # unparseable floats → row dropped → code absent
    assert result.skipped == {"600519": SkipReason.NOT_IN_SNAPSHOT.value}


# ---------------------------------------------------------------------------
# pure detector unit tests
# ---------------------------------------------------------------------------


def test_price_zscore_none_on_short_series() -> None:
    assert price_zscore((1.0, 2.0, 3.0), 20) is None


def test_price_zscore_none_on_flat_baseline() -> None:
    flat = tuple([100.0] * 25)
    assert price_zscore(flat, 20) is None  # zero-variance baseline → safe None


def test_volume_zscore_none_on_short_series() -> None:
    assert volume_zscore((1.0, 2.0), 20) is None


def test_bollinger_none_within_bands() -> None:
    assert bollinger_breakout(tuple(_calm_closes()), 20, 2.0) is None


def test_ewma_none_on_short_series() -> None:
    assert ewma_deviation((1.0, 2.0, 3.0), 10) is None
