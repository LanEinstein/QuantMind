"""Tests for the cyq_perf PIT chip-distribution reader (QGR-3 ⑧ bottom gate).

cyq_perf is Tushare's MODEL-derived chip-distribution summary (cost-band
percentiles + winner_rate), not a raw market observation — the reader fails
closed on the degenerate all-zero rows the model emits for un-fittable names
(fresh listings), so the bottom-confirmation gate never anchors on a fabricated
cost band.
"""

from __future__ import annotations

from types import SimpleNamespace

from scripts.factor_research.cyq_perf_pit import ChipRecord, read_cyq_perf


def _csv(rows: list[str]) -> bytes:
    header = (
        "cost_15pct,cost_50pct,cost_5pct,cost_85pct,cost_95pct,his_high,"
        "his_low,trade_date,ts_code,weight_avg,winner_rate"
    )
    return (header + "\n" + "\n".join(rows) + "\n").encode("utf-8")


class _Store:
    def __init__(self, payload: bytes | None) -> None:
        self._payload = payload

    def latest(self, *, vendor: str, endpoint: str, trade_date: str):  # noqa: ANN201
        if self._payload is None:
            return None
        return SimpleNamespace(raw_payload=self._payload)


class TestReadCyqPerf:
    def test_parses_cost_band_and_winner_rate(self) -> None:
        store = _Store(
            _csv(["1.6,1.9,1.2,2.3,2.3,2.4,1.0,20180102,601288.SH,1.89,95.46"])
        )
        out = read_cyq_perf(store, "20180102")
        assert "601288.SH" in out
        rec = out["601288.SH"]
        assert isinstance(rec, ChipRecord)
        assert rec.cost_50pct == 1.9
        assert rec.cost_15pct == 1.6
        assert rec.cost_85pct == 2.3
        assert rec.winner_rate == 95.46
        assert rec.weight_avg == 1.89

    def test_skips_degenerate_zero_cost_row(self) -> None:
        # 300394.SZ-style: model could not fit → cost_5/15/50pct all 0 (fail-closed).
        store = _Store(
            _csv(
                [
                    "0.0,0.0,0.0,5.2,5.2,520.0,0.0,20180102,300394.SZ,1.58,70.67",
                    "10.0,10.3,9.8,10.7,10.9,15.6,6.5,20180102,603488.SH,10.32,70.13",
                ]
            )
        )
        out = read_cyq_perf(store, "20180102")
        assert "300394.SZ" not in out  # cost_50pct == 0 → dropped
        assert "603488.SH" in out

    def test_winner_rate_out_of_range_becomes_none_but_band_kept(self) -> None:
        store = _Store(
            _csv(["1.6,1.9,1.2,2.3,2.3,2.4,1.0,20180102,601288.SH,1.89,150.0"])
        )
        rec = read_cyq_perf(store, "20180102")["601288.SH"]
        assert rec.cost_50pct == 1.9  # valid band kept
        assert rec.winner_rate is None  # 150% impossible → None, not fabricated

    def test_malformed_cells_skipped(self) -> None:
        store = _Store(
            _csv(["x,y,z,2.3,2.3,2.4,1.0,20180102,601288.SH,1.89,95.46"])
        )
        assert read_cyq_perf(store, "20180102") == {}

    def test_missing_snapshot_returns_empty(self) -> None:
        # pre-2018 has no cyq_perf snapshot → empty (gate fails closed to None).
        assert read_cyq_perf(_Store(None), "20150105") == {}
