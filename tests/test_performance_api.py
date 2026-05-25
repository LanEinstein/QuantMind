"""Unit tests for the performance analytics API endpoints."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.performance import (
    build_model_contributions,
    compute_core_metrics,
    compute_drawdown_curve,
    compute_equity_curve,
)
from backend.broker.models import (
    AccountInfo,
    OrderDirection,
    Trade,
)
from backend.main import app

# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------


def _make_account(
    initial_capital: float = 1_000_000,
) -> AccountInfo:
    return AccountInfo(
        total_assets=initial_capital,
        available_cash=initial_capital,
        frozen_cash=0,
        market_value=0,
        total_pnl=0,
        total_pnl_pct=0,
        initial_capital=initial_capital,
    )


def _make_trades(
    count: int = 5,
    start_date: date | None = None,
) -> tuple[Trade, ...]:
    """Generate deterministic test trades."""
    base = start_date or date(2026, 3, 1)
    trades: list[Trade] = []
    for i in range(count):
        d = base + timedelta(days=i)
        # Skip weekends
        while d.weekday() >= 5:
            d += timedelta(days=1)

        pnl = 500.0 if i % 3 != 0 else -300.0
        trades.append(
            Trade(
                trade_id=f"t-{i:04d}",
                order_id=f"o-{i:04d}",
                code=f"{600000 + i}",
                price=50.0,
                volume=100,
                amount=5000.0,
                direction=OrderDirection.SELL if i % 2 else OrderDirection.BUY,
                commission=1.5,
                stamp_tax=2.5,
                slippage_cost=0.5,
                net_amount=pnl,
                traded_at=datetime(d.year, d.month, d.day, 10, 0, tzinfo=UTC),
            )
        )
    return tuple(trades)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_broker() -> MagicMock:
    broker = MagicMock()
    broker.get_account = AsyncMock(return_value=_make_account())
    broker.get_trades = AsyncMock(return_value=_make_trades())
    return broker


@pytest.fixture()
def mock_registry(mock_broker: MagicMock) -> MagicMock:
    registry = MagicMock()
    registry.get_broker.return_value = mock_broker
    return registry


@pytest.fixture()
async def client(mock_registry: MagicMock) -> AsyncClient:
    app.state.broker_registry = mock_registry
    app.state.redis = AsyncMock()
    # Make redis.scan return empty to skip cost tracker
    app.state.redis.scan = AsyncMock(return_value=(0, []))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Unit tests: pure computation functions
# ---------------------------------------------------------------------------


class TestComputeEquityCurve:
    def test_empty_trades(self) -> None:
        result = compute_equity_curve(
            (), 1_000_000, date(2026, 3, 1), date(2026, 3, 5)
        )
        assert len(result) > 0
        # All points should be normalized to 100
        assert all(p["portfolio"] == 100.0 for p in result)

    def test_with_trades(self) -> None:
        trades = _make_trades(3, start_date=date(2026, 3, 2))
        result = compute_equity_curve(
            trades, 1_000_000, date(2026, 3, 1), date(2026, 3, 7)
        )
        assert len(result) > 0
        # First point normalized to 100
        assert result[0]["portfolio"] == 100.0

    def test_skips_weekends(self) -> None:
        result = compute_equity_curve(
            (), 1_000_000, date(2026, 3, 2), date(2026, 3, 8)
        )
        dates = [p["date"] for p in result]
        for d in dates:
            parsed = date.fromisoformat(d)
            assert parsed.weekday() < 5, f"{d} is a weekend"


class TestComputeDrawdownCurve:
    def test_no_drawdown_on_flat(self) -> None:
        equity = [
            {"date": "2026-03-02", "portfolio": 100.0, "benchmark": 100.0},
            {"date": "2026-03-03", "portfolio": 100.0, "benchmark": 100.0},
        ]
        result = compute_drawdown_curve(equity)
        assert all(p["drawdown"] == 0.0 for p in result)

    def test_drawdown_after_decline(self) -> None:
        equity = [
            {"date": "2026-03-02", "portfolio": 100.0, "benchmark": 100.0},
            {"date": "2026-03-03", "portfolio": 110.0, "benchmark": 100.0},
            {"date": "2026-03-04", "portfolio": 99.0, "benchmark": 100.0},
        ]
        result = compute_drawdown_curve(equity)
        assert result[2]["drawdown"] < 0  # Drawdown from peak of 110


class TestComputeCoreMetrics:
    def test_empty_data(self) -> None:
        metrics = compute_core_metrics([], ())
        assert metrics["annualized_return"] == 0.0
        assert metrics["sharpe_ratio"] == 0.0

    def test_with_data(self) -> None:
        equity = [
            {"date": "2026-03-02", "portfolio": 100.0},
            {"date": "2026-03-03", "portfolio": 101.0},
            {"date": "2026-03-04", "portfolio": 102.0},
            {"date": "2026-03-05", "portfolio": 101.5},
            {"date": "2026-03-06", "portfolio": 103.0},
        ]
        trades = _make_trades(4)
        metrics = compute_core_metrics(equity, trades)
        assert metrics["annualized_return"] > 0
        assert metrics["win_rate"] > 0

    def test_win_rate_calculation(self) -> None:
        trades = _make_trades(6)
        winning = sum(1 for t in trades if t.net_amount > 0)
        total = sum(1 for t in trades if t.net_amount != 0)
        expected = winning / total if total > 0 else 0

        equity = [
            {"date": f"2026-03-{2 + i:02d}", "portfolio": 100.0 + i}
            for i in range(6)
        ]
        metrics = compute_core_metrics(equity, trades)
        assert abs(metrics["win_rate"] - expected) < 0.01


class TestBuildModelContributions:
    def test_builds_three_models(self) -> None:
        costs = {"deepseek": 1.5, "qwen": 2.0}
        result = build_model_contributions(costs)
        assert len(result) == 3
        labels = {m["model"] for m in result}
        assert labels == {"deepseek", "qwen", "kimi"}

    def test_zero_cost_for_missing(self) -> None:
        result = build_model_contributions({})
        for m in result:
            assert m["cost_value"] == 0.0


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


class TestGetPerformance:
    async def test_returns_performance_data(self, client: AsyncClient) -> None:
        resp = await client.get("/api/performance")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "equity_curve" in data
        assert "metrics" in data
        assert "drawdown_curve" in data
        assert "model_contributions" in data

    async def test_with_date_range(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/performance",
            params={"start": "2026-03-01", "end": "2026-03-31"},
        )
        assert resp.status_code == 200

    async def test_metrics_structure(self, client: AsyncClient) -> None:
        resp = await client.get("/api/performance")
        metrics = resp.json()["data"]["metrics"]
        expected_keys = {
            "annualized_return",
            "sharpe_ratio",
            "max_drawdown",
            "win_rate",
            "profit_loss_ratio",
            "monthly_turnover",
        }
        assert set(metrics.keys()) == expected_keys

    async def test_no_broker_returns_503(self, client: AsyncClient) -> None:
        original = app.state.broker_registry
        del app.state.broker_registry
        resp = await client.get("/api/performance")
        assert resp.status_code == 503
        app.state.broker_registry = original


class TestExportPerformance:
    async def test_export_csv(self, client: AsyncClient) -> None:
        resp = await client.get("/api/performance/export/daily")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]

    async def test_invalid_report_type(self, client: AsyncClient) -> None:
        resp = await client.get("/api/performance/export/invalid")
        assert resp.status_code == 422

    async def test_csv_contains_headers(self, client: AsyncClient) -> None:
        resp = await client.get("/api/performance/export/daily")
        content = resp.text
        assert "QuantMind Performance Report" in content
        assert "Core Metrics" in content
        assert "Date" in content
