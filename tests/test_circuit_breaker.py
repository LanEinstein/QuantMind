"""Tests for CircuitBreaker."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from backend.broker.models import CircuitBreakerConfig
from backend.risk.circuit_breaker import CircuitBreaker

SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def breaker() -> CircuitBreaker:
    config = CircuitBreakerConfig(
        daily_loss_limit_pct=0.05,
        consecutive_loss_count=3,
        cooldown_minutes=60,
    )
    return CircuitBreaker(config)


class TestCircuitBreaker:
    def test_initial_not_halted(self, breaker: CircuitBreaker) -> None:
        now = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        assert breaker.is_halted(now) is False

    def test_daily_loss_triggers_halt(self, breaker: CircuitBreaker) -> None:
        now = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.03, now)
        breaker.record_trade_result(-0.03, now)  # total -6% > -5%
        assert breaker.is_halted(now) is True

    def test_single_large_loss_triggers(
        self, breaker: CircuitBreaker
    ) -> None:
        now = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.06, now)
        assert breaker.is_halted(now) is True

    def test_consecutive_losses_trigger(
        self, breaker: CircuitBreaker
    ) -> None:
        now = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.01, now)
        breaker.record_trade_result(-0.01, now)
        breaker.record_trade_result(-0.01, now)  # 3rd consecutive
        assert breaker.is_halted(now) is True

    def test_win_resets_consecutive(self, breaker: CircuitBreaker) -> None:
        now = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.01, now)
        breaker.record_trade_result(-0.01, now)
        breaker.record_trade_result(0.01, now)  # win resets
        breaker.record_trade_result(-0.01, now)  # only 1 consecutive
        assert breaker.is_halted(now) is False

    def test_cooldown_not_expired(self, breaker: CircuitBreaker) -> None:
        halt_time = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.06, halt_time)
        check_time = dt.datetime(2026, 3, 23, 10, 30, tzinfo=SHANGHAI)
        assert breaker.is_halted(check_time) is True

    def test_cooldown_expired(self, breaker: CircuitBreaker) -> None:
        halt_time = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.06, halt_time)
        check_time = dt.datetime(2026, 3, 23, 11, 1, tzinfo=SHANGHAI)
        assert breaker.is_halted(check_time) is False

    def test_cooldown_exact_boundary(self, breaker: CircuitBreaker) -> None:
        halt_time = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.06, halt_time)
        check_time = dt.datetime(2026, 3, 23, 11, 0, tzinfo=SHANGHAI)
        assert breaker.is_halted(check_time) is False

    def test_reset_clears_state(self, breaker: CircuitBreaker) -> None:
        now = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.06, now)
        assert breaker.is_halted(now) is True
        breaker.reset()
        assert breaker.is_halted(now) is False

    def test_zero_pnl_not_a_loss(self, breaker: CircuitBreaker) -> None:
        now = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        breaker.record_trade_result(-0.01, now)
        breaker.record_trade_result(-0.01, now)
        breaker.record_trade_result(0.0, now)  # zero is not a loss
        breaker.record_trade_result(-0.01, now)
        assert breaker.is_halted(now) is False

    def test_multiple_resets_idempotent(
        self, breaker: CircuitBreaker
    ) -> None:
        breaker.reset()
        breaker.reset()
        now = dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)
        assert breaker.is_halted(now) is False
