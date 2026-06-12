"""AD-001 — EquityPoint-sourced KPI computation + endpoint guards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from backend.services.equity_kpis import (
    ANNUALIZED_WINDOW_FLOOR,
    compute_equity_kpis,
    compute_max_drawdown,
    compute_sharpe,
    count_policy_segments,
)


@dataclass
class _Pt:
    total_equity: float
    pnl_pct: float
    trade_date: str
    policy_hash: str | None = None
    quality: str = "FRESH"


def _series(
    values: list[float], *, hashes: list[str | None] | None = None
) -> list[_Pt]:
    base = values[0]
    pts: list[_Pt] = []
    for i, v in enumerate(values):
        pts.append(
            _Pt(
                total_equity=v,
                pnl_pct=v / base - 1.0,
                trade_date=f"2026-06-{i + 1:02d}",
                policy_hash=(hashes[i] if hashes else "h1"),
            )
        )
    return pts


class TestPureMaths:
    def test_max_drawdown(self) -> None:
        assert compute_max_drawdown([100, 120, 90, 110]) == pytest.approx(-0.25)

    def test_max_drawdown_monotonic_up_is_zero(self) -> None:
        assert compute_max_drawdown([100, 110, 120]) == 0.0

    def test_sharpe_zero_for_flat(self) -> None:
        assert compute_sharpe([100, 100, 100]) == 0.0

    def test_sharpe_positive_for_steady_growth(self) -> None:
        assert compute_sharpe([100, 101, 102, 103, 104]) > 0

    def test_policy_segment_count(self) -> None:
        pts = _series([100, 101, 102, 103], hashes=["a", "a", "b", "b"])
        assert count_policy_segments(pts) == 2


class TestComputeEquityKpis:
    def test_empty_series_clean_shape(self) -> None:
        out = compute_equity_kpis([])
        assert out["sample_trading_days"] == 0
        assert out["total_return"] == 0.0
        assert out["annualized_reliable"] is False
        assert out["hs300_excess"] is None

    def test_short_window_flagged_unreliable(self) -> None:
        out = compute_equity_kpis(_series([100, 105, 110]))
        assert out["sample_trading_days"] == 3
        assert out["annualized_reliable"] is False
        assert out["total_return"] == pytest.approx(0.10)

    def test_long_window_reliable(self) -> None:
        values = [100 + i for i in range(ANNUALIZED_WINDOW_FLOOR)]
        out = compute_equity_kpis(_series(values))
        assert out["annualized_reliable"] is True

    def test_hs300_excess(self) -> None:
        # Portfolio +10%, benchmark +4% → excess +6%.
        out = compute_equity_kpis(
            _series([100, 110]),
            benchmark_prices=[
                {"date": "2026-06-01", "close": 100.0},
                {"date": "2026-06-02", "close": 104.0},
            ],
        )
        assert out["hs300_excess"] == pytest.approx(0.06)

    def test_data_quality_counts(self) -> None:
        pts = _series([100, 101, 102])
        pts[1].quality = "STALE"
        out = compute_equity_kpis(pts)
        assert out["data_quality"]["FRESH"] == 2
        assert out["data_quality"]["STALE"] == 1


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self, series: list[Any]) -> None:
        self._series = series

    async def list_eod_series(self, start: str, end: str) -> list[Any]:
        return self._series


class _State:
    def __init__(self, repo: Any) -> None:
        self.equity_point_repository = repo
        self.policy_hash = "h1"
        self.policy_segment_store = None
        self.mongodb = None


class _App:
    def __init__(self, repo: Any) -> None:
        self.state = _State(repo)


class _Req:
    def __init__(self, repo: Any) -> None:
        self.app = _App(repo)


class TestEquityKpisEndpoint:
    @pytest.mark.asyncio
    async def test_unwired_repo_returns_clean_200(self) -> None:
        from backend.api.performance import get_equity_kpis

        resp = await get_equity_kpis(
            _Req(None),  # type: ignore[arg-type]
            start="2026-06-01",
            end="2026-06-30",
        )
        assert resp["status"] == "ok"
        assert resp["data"]["repository_status"] == "unavailable"
        assert resp["data"]["kpis"]["sample_trading_days"] == 0

    @pytest.mark.asyncio
    async def test_populated_series(self) -> None:
        from backend.api.performance import get_equity_kpis

        resp = await get_equity_kpis(
            _Req(_FakeRepo(_series([100, 110]))),  # type: ignore[arg-type]
            start="2026-06-01",
            end="2026-06-30",
            benchmark="000300",
        )
        data = resp["data"]
        assert data["repository_status"] == "ok"
        assert data["kpis"]["total_return"] == pytest.approx(0.10)
        assert len(data["equity_series"]) == 2
        assert data["equity_series"][0]["policy_hash"] == "h1"
