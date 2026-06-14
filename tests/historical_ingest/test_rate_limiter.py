"""Tests for the AE-001 injectable sliding-window rate limiter."""

from __future__ import annotations

from backend.data.historical_ingest.rate_limiter import RateLimiter


class _FakeClock:
    """Deterministic clock: ``sleep`` advances ``monotonic`` time."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def test_under_limit_never_sleeps() -> None:
    clock = _FakeClock()
    rl = RateLimiter(3, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(3):
        rl.acquire()
    assert clock.sleeps == []


def test_exceeding_limit_sleeps_until_window_frees() -> None:
    clock = _FakeClock()
    rl = RateLimiter(2, monotonic=clock.monotonic, sleep=clock.sleep)
    rl.acquire()  # t=0
    rl.acquire()  # t=0
    rl.acquire()  # over limit → must wait ~window
    assert clock.sleeps, "third acquire should have slept"
    assert abs(sum(clock.sleeps) - 60.0) < 1e-6


def test_zero_disables_throttle() -> None:
    clock = _FakeClock()
    rl = RateLimiter(0, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(100):
        rl.acquire()
    assert clock.sleeps == []


def test_calls_age_out_of_window() -> None:
    clock = _FakeClock()
    rl = RateLimiter(2, monotonic=clock.monotonic, sleep=clock.sleep)
    rl.acquire()
    rl.acquire()
    clock.t = 61.0  # both calls age out
    rl.acquire()  # should not need to sleep
    assert clock.sleeps == []
