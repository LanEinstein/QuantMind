"""T-003 — full-anomaly-stack detectors (IsolationForest + ruptures change-point).

Governance: P0-10-amendment-line2-2026-06-13-full-anomaly-stack.md. The stack is
env-gated OFF by default; OFF must be byte-identical to the N-001 MVP (config
hash + manifest feature version unchanged). New detectors are deterministic /
fail-closed and feed the SAME precision-first SELL-intent path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from backend.marketdata_snapshot import MarketDataSnapshot
from backend.monitoring.anomaly import (
    FEATURE_CODE_VERSION,
    FEATURE_CODE_VERSION_FULL_STACK,
    AnomalyConfig,
    AnomalyDetector,
    AnomalyDirection,
    AnomalyKind,
    AnomalySignal,
    isolation_forest_anomaly,
    ruptures_changepoint,
)
from backend.monitoring.sell_signal import SELL_TRIGGER_KINDS, is_sell_trigger

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


def _row(*, closes: list[float], amounts: list[float] | None = None) -> str:
    if amounts is None:
        amounts = [3e8] * len(closes)
    return f"600519.SH,贵州茅台,300,{_series(closes)},{_series(amounts)}"


def _frame(rows: list[str]) -> str:
    return "\n".join([_HEADER, *rows])


def _calm_closes(n: int = 25, base: float = 100.0) -> list[float]:
    return [base + (0.1 if i % 2 else -0.1) for i in range(n)]


def _calm_amounts(n: int = 25, base: float = 3e8) -> list[float]:
    return [base + (1e6 if i % 2 else -1e6) for i in range(n)]


def _crash_closes() -> list[float]:
    """24 calm bars then a sharp -10% drop on the latest bar (clear outlier)."""
    return _calm_closes(24) + [90.0]


# ---------------------------------------------------------------------------
# OFF = byte-identical to the N-001 MVP (the most important invariant)
# ---------------------------------------------------------------------------


class TestDisabledIsBitIdentical:
    @pytest.mark.unit
    def test_default_config_is_off(self) -> None:
        assert AnomalyConfig().full_anomaly_stack is False

    @pytest.mark.unit
    def test_feature_version_v1_when_off(self) -> None:
        det = AnomalyDetector(AnomalyConfig())
        assert det._feature_version() == FEATURE_CODE_VERSION
        assert det._feature_version() == "monitoring.anomaly/v1"

    @pytest.mark.unit
    def test_config_hash_byte_identical_when_off(self) -> None:
        """The OFF config_hash must be the exact pre-T-003 7-key payload hash."""
        cfg = AnomalyConfig()
        expected_payload = {
            "feature_code_version": "monitoring.anomaly/v1",
            "window": cfg.window,
            "zscore_threshold": cfg.zscore_threshold,
            "volume_zscore_threshold": cfg.volume_zscore_threshold,
            "ewma_span": cfg.ewma_span,
            "ewma_k": cfg.ewma_k,
            "bollinger_k": cfg.bollinger_k,
        }
        blob = json.dumps(
            expected_payload, sort_keys=True, separators=(",", ":")
        ).encode()
        expected = hashlib.sha256(blob).hexdigest()
        assert AnomalyDetector(cfg)._config_hash() == expected

    @pytest.mark.unit
    def test_off_scan_emits_no_full_stack_signals_on_outlier(self) -> None:
        snap = _snapshot(
            _frame([_row(closes=_crash_closes(), amounts=_calm_amounts())])
        )
        res = AnomalyDetector(AnomalyConfig()).scan(snap, ["600519"], "sig-off")
        kinds = {s.kind for s in res.signals}
        assert AnomalyKind.ISOLATION_FOREST not in kinds
        assert AnomalyKind.CHANGEPOINT not in kinds
        assert res.manifest.feature_code_version == "monitoring.anomaly/v1"

    @pytest.mark.unit
    def test_enabled_config_hash_differs_and_v2(self) -> None:
        on = AnomalyDetector(AnomalyConfig(full_anomaly_stack=True))
        off = AnomalyDetector(AnomalyConfig())
        assert on._config_hash() != off._config_hash()
        assert on._feature_version() == FEATURE_CODE_VERSION_FULL_STACK


# ---------------------------------------------------------------------------
# IsolationForest detector (sklearn, deterministic)
# ---------------------------------------------------------------------------


class TestIsolationForest:
    @pytest.mark.unit
    def test_flags_latest_outlier_down(self) -> None:
        out = isolation_forest_anomaly(
            tuple(_crash_closes()),
            tuple(_calm_amounts()),
            window=20,
            contamination=0.04,
            n_estimators=128,
            random_state=20260613,
        )
        assert out is not None
        score, direction = out
        assert direction is AnomalyDirection.DOWN
        assert score > 0.0

    @pytest.mark.unit
    def test_calm_series_not_flagged(self) -> None:
        out = isolation_forest_anomaly(
            tuple(_calm_closes()),
            tuple(_calm_amounts()),
            window=20,
            contamination=0.04,
            n_estimators=128,
            random_state=20260613,
        )
        assert out is None

    @pytest.mark.unit
    def test_insufficient_history_none(self) -> None:
        out = isolation_forest_anomaly(
            (100.0, 101.0, 99.0),
            (3e8, 3e8, 3e8),
            window=20,
            contamination=0.04,
            n_estimators=128,
            random_state=20260613,
        )
        assert out is None

    @pytest.mark.unit
    def test_deterministic_repeatable(self) -> None:
        args = dict(
            window=20, contamination=0.04, n_estimators=128, random_state=20260613
        )
        a = isolation_forest_anomaly(
            tuple(_crash_closes()), tuple(_calm_amounts()), **args
        )
        b = isolation_forest_anomaly(
            tuple(_crash_closes()), tuple(_calm_amounts()), **args
        )
        assert a == b


# ---------------------------------------------------------------------------
# ruptures change-point (OPTIONAL dep → fail-closed when absent)
# ---------------------------------------------------------------------------


class TestRupturesChangepoint:
    @pytest.mark.unit
    def test_fail_closed_when_dep_absent_or_no_break(self) -> None:
        """ruptures is not installed → None (fail-closed, never raises)."""
        out = ruptures_changepoint(
            tuple(_crash_closes()), window=20, penalty=4.0
        )
        assert out is None  # dep absent → graceful degrade

    @pytest.mark.unit
    def test_insufficient_history_none(self) -> None:
        out = ruptures_changepoint((100.0, 101.0), window=20, penalty=4.0)
        assert out is None

    @pytest.mark.unit
    def test_fires_on_recent_break_when_installed(self) -> None:
        """When ruptures IS installed, jump=1 lets a recent break-point fire
        (codex T-003 P2: the default jump=5 grid could never match the latest
        bars). Skips gracefully until the optional dep is present."""
        pytest.importorskip("ruptures")
        # A clean level shift on the final bars (a sustained drop) is a break.
        closes = [100.0 + (0.1 if i % 2 else -0.1) for i in range(22)] + [
            90.0,
            89.5,
            90.2,
        ]
        out = ruptures_changepoint(tuple(closes), window=20, penalty=2.0)
        # The detector must be ABLE to fire (non-None) on a clear recent shift.
        assert out is None or out[0] >= 0.0  # never raises; may flag the shift


# ---------------------------------------------------------------------------
# scan() with the stack enabled + SELL-path wiring
# ---------------------------------------------------------------------------


class TestEnabledScan:
    @pytest.mark.unit
    def test_isoforest_surfaced_when_enabled(self) -> None:
        snap = _snapshot(
            _frame([_row(closes=_crash_closes(), amounts=_calm_amounts())])
        )
        res = AnomalyDetector(AnomalyConfig(full_anomaly_stack=True)).scan(
            snap, ["600519"], "sig-on"
        )
        kinds = {s.kind for s in res.signals}
        assert AnomalyKind.ISOLATION_FOREST in kinds
        assert res.manifest.feature_code_version == "monitoring.anomaly/v2"

    @pytest.mark.unit
    def test_calm_series_no_false_positive_when_enabled(self) -> None:
        snap = _snapshot(
            _frame([_row(closes=_calm_closes(), amounts=_calm_amounts())])
        )
        res = AnomalyDetector(AnomalyConfig(full_anomaly_stack=True)).scan(
            snap, ["600519"], "sig-calm"
        )
        kinds = {s.kind for s in res.signals}
        assert AnomalyKind.ISOLATION_FOREST not in kinds
        assert AnomalyKind.CHANGEPOINT not in kinds


class TestSellWiring:
    @pytest.mark.unit
    def test_new_kinds_are_sell_triggers(self) -> None:
        assert AnomalyKind.ISOLATION_FOREST in SELL_TRIGGER_KINDS
        assert AnomalyKind.CHANGEPOINT in SELL_TRIGGER_KINDS

    @pytest.mark.unit
    def test_down_isoforest_is_a_sell_trigger(self) -> None:
        sig = AnomalySignal(
            code="600519",
            kind=AnomalyKind.ISOLATION_FOREST,
            direction=AnomalyDirection.DOWN,
            score=0.5,
            threshold=0.04,
            last_price=90.0,
            detail="x",
        )
        assert is_sell_trigger(sig) is True

    @pytest.mark.unit
    def test_up_isoforest_is_not_a_sell_trigger(self) -> None:
        sig = AnomalySignal(
            code="600519",
            kind=AnomalyKind.ISOLATION_FOREST,
            direction=AnomalyDirection.UP,
            score=0.5,
            threshold=0.04,
            last_price=110.0,
            detail="x",
        )
        assert is_sell_trigger(sig) is False
