"""V-004 — production RotationContextProvider sourcing + affordability gate.

Exercises the deterministic incumbent-health sourcing over a real T-1 frame +
real AnomalyDetector scan: a calm holding has no protective stop; a crashing
holding yields a Line-2 hard SELL → protective_stop_active + the
yield-to-protective-stop flag. Plus the affordability gate + pure helpers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.broker.models import Position
from backend.budget_policy.policy import BudgetTierPolicy, load_budget_tier_config
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.monitoring.anomaly import AnomalyDetector
from backend.screening.screener import Screener
from backend.services.rotation_context_provider import (
    ProductionRotationProvider,
    _closes_by_code,
    _drawdown,
    compute_qualified_codes,
)
from backend.services.universe_policy import load_policy

_HEADER = "ts_code,name,listed_trading_days,closes,amounts"
_CRASH = "510300"
_CALM = "510500"
_NAMES = {_CRASH: "沪深300ETF", _CALM: "中证500ETF"}


def _crash(n: int = 30) -> list[float]:
    closes = [4.5 + (0.001 if i % 2 else -0.001) for i in range(n)]
    closes[-1] = closes[-2] * 0.96
    return closes


def _flat(n: int = 30) -> list[float]:
    return [6.0 + (0.001 if i % 2 else -0.001) for i in range(n)]


def _row(code: str, name: str, closes: list[float]) -> str:
    cs = "|".join(repr(v) for v in closes)
    am = "|".join(repr(3e8) for _ in closes)
    return f"{code},{name},400,{cs},{am}"


def _frame() -> MarketDataSnapshot:
    body = "\n".join(
        [_HEADER, _row(_CRASH, _NAMES[_CRASH], _crash()),
         _row(_CALM, _NAMES[_CALM], _flat())]
    )
    raw = body.encode("utf-8")
    return MarketDataSnapshot(
        vendor="quantmind", endpoint="line1_screener_frame",
        params={"as_of": "20260514"}, trade_date="20260514", raw_payload=raw,
        size=len(raw), encoding="csv", compression="none",
        raw_payload_sha256=hashlib.sha256(raw).hexdigest(),
        fetch_time_utc=datetime(2026, 5, 14, 9, 0, 0, tzinfo=UTC),
    )


@dataclass
class _FakeLine2:
    positions: tuple[Position, ...]

    @property
    def held_positions(self) -> tuple[Position, ...]:
        return self.positions

    @property
    def name_by_code(self) -> dict[str, str]:
        return _NAMES

    def build_sell_context(self, *a, **k):  # pragma: no cover - not exercised here
        raise AssertionError("build_sell_context not used in these unit tests")


def _positions() -> tuple[Position, ...]:
    return (
        Position(code=_CRASH, volume=300, available_volume=300, cost_price=4.55,
                 market_value=1290.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0),
        Position(code=_CALM, volume=100, available_volume=100, cost_price=6.0,
                 market_value=600.0, unrealized_pnl=0.0, unrealized_pnl_pct=0.0),
    )


def _provider() -> ProductionRotationProvider:
    frame = _frame()
    scan = AnomalyDetector().scan(frame, [_CRASH, _CALM], "LINE2-MON-20260514-rotation")
    return ProductionRotationProvider(
        line2_provider=_FakeLine2(_positions()), scan=scan, frame=frame,
        rotations_today=0, daily_new_instruction_budget_remaining=5,
    )


class TestIncumbentHealthSourcing:
    def test_held_codes(self) -> None:
        assert _provider().held_codes == frozenset({_CRASH, _CALM})

    def test_crashing_holding_has_protective_stop(self) -> None:
        # The crashing ETF triggers a Line-2 hard SELL → protective stop active
        # → rotation must YIELD to it (condition 1 false).
        p = _provider()
        h = p.incumbent_health(_CRASH)
        assert h.protective_stop_active
        assert p.protective_action_needs_cap_today
        assert h.available_volume == 300

    def test_calm_holding_no_protective_stop(self) -> None:
        h = _provider().incumbent_health(_CALM)
        assert not h.protective_stop_active
        assert h.drawdown_from_local_high == 0.0  # flat → no drawdown

    def test_calendar_methods(self) -> None:
        p = _provider()
        # 20260518 is a Monday, 20260514 a Thursday → 2 trading days in [Thu,Mon).
        assert p.trading_days_between("20260514", "20260518") == 2
        assert p.trading_day_ahead("20260514", 1) == "20260515"  # Fri


class TestAffordability:
    def test_compute_qualified_codes(self) -> None:
        frame = _frame()
        screen = Screener(
            load_policy(Path("config/universe_policy.yaml")).exclusion_rules
        ).screen(frame, "SIG-20260514-line1")
        budget = BudgetTierPolicy(load_budget_tier_config("config/risk.yaml"))
        qualified = compute_qualified_codes(screen, budget, available_cash=100_000.0)
        # Both ETFs are affordable at ¥100k → both qualify.
        assert _CALM in qualified

    def test_no_cash_no_qualified(self) -> None:
        frame = _frame()
        screen = Screener(
            load_policy(Path("config/universe_policy.yaml")).exclusion_rules
        ).screen(frame, "SIG-20260514-line1")
        budget = BudgetTierPolicy(load_budget_tier_config("config/risk.yaml"))
        qualified = compute_qualified_codes(screen, budget, available_cash=1.0)
        assert qualified == frozenset()


class TestPureHelpers:
    def test_closes_by_code_parses_frame(self) -> None:
        closes = _closes_by_code(_frame())
        assert _CALM in closes and len(closes[_CALM]) == 30

    def test_drawdown(self) -> None:
        assert _drawdown((10.0, 9.0, 8.0), 20) == 0.2
        assert _drawdown((8.0, 9.0, 10.0), 20) == 0.0  # at the high
        assert _drawdown((), 20) == 0.0

    def test_closes_skips_non_csv(self) -> None:
        frame = _frame()
        nonshaped = frame.model_copy(update={"encoding": "json"})
        assert _closes_by_code(nonshaped) == {}
