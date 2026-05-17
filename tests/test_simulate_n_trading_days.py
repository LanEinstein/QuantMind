"""J-005 — Unit + integration tests for the N-day pinned-clock harness.

Coverage:

* Calendar walker yields N trading days skipping weekends + holidays.
* 8 pinned ticks per trading day in the documented order.
* ``--days`` outside the locked ALLOWED_DAYS profile is rejected.
* Tick callback is invoked once per tick and errors are captured.
* :class:`SimulationOutcome` ``ok`` flag triggers on the 4 failure
  modes (days mismatch, reset fired, callback errors, real LLM call).
* CLI ``main`` exits 0 on PASS and 1 on FAIL.
* End-to-end: 5-day harness with stub LLM observer asserts 0 real
  LLM calls + 0 reset triggers fired.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from contextlib import suppress
from typing import Any
from unittest import mock

import pytest

from backend.llm.router import QUANTMIND_LLM_STUB_ENV
from scripts.simulate_n_trading_days import (
    ALLOWED_DAYS,
    PINNED_TICKS_BY_HOUR_MINUTE,
    SimulationOutcome,
    iter_trading_days,
    main,
    pinned_ticks_for_day,
    run_simulation,
)

# ---------------------------------------------------------------------------
# Calendar walker
# ---------------------------------------------------------------------------


def test_iter_trading_days_rejects_zero() -> None:
    with pytest.raises(ValueError):
        list(iter_trading_days(dt.date(2026, 5, 18), 0))


def test_iter_trading_days_yields_one_when_start_is_trading_day() -> None:
    days = list(iter_trading_days(dt.date(2026, 5, 18), 1))  # Mon
    assert days == [dt.date(2026, 5, 18)]


def test_iter_trading_days_rolls_to_next_trading_day_when_weekend() -> None:
    days = list(iter_trading_days(dt.date(2026, 5, 16), 1))  # Sat
    assert days == [dt.date(2026, 5, 18)]


def test_iter_trading_days_skips_weekends() -> None:
    days = list(iter_trading_days(dt.date(2026, 5, 18), 5))
    assert len(days) == 5
    # Mon May 18 + Tue 19 + Wed 20 + Thu 21 + Fri 22 (all trading days)
    expected = [
        dt.date(2026, 5, 18),
        dt.date(2026, 5, 19),
        dt.date(2026, 5, 20),
        dt.date(2026, 5, 21),
        dt.date(2026, 5, 22),
    ]
    assert days == expected


def test_iter_trading_days_skips_holidays() -> None:
    # 2026 Spring Festival holidays around Feb 16-23 should be skipped.
    days = list(iter_trading_days(dt.date(2026, 2, 13), 3))  # Fri Feb 13
    # Fri Feb 13, then skip Spring Festival, land on Tue Feb 24.
    assert dt.date(2026, 2, 13) in days
    assert all(d.weekday() < 5 for d in days)
    # No date 2026-02-16..2026-02-23 in result.
    for blocked in (
        dt.date(2026, 2, 16),
        dt.date(2026, 2, 17),
        dt.date(2026, 2, 18),
        dt.date(2026, 2, 19),
        dt.date(2026, 2, 20),
        dt.date(2026, 2, 23),
    ):
        assert blocked not in days


def test_iter_trading_days_45_completes() -> None:
    """45 trading days from a known Monday should land in July 2026."""
    days = list(iter_trading_days(dt.date(2026, 5, 18), 45))
    assert len(days) == 45
    assert days[0] == dt.date(2026, 5, 18)
    # 45 trading days forward from May 18 ~ July 20 (depending on holidays)
    assert days[-1] > days[0]


# ---------------------------------------------------------------------------
# Pinned ticks
# ---------------------------------------------------------------------------


def test_pinned_ticks_count_per_day_is_8() -> None:
    assert len(PINNED_TICKS_BY_HOUR_MINUTE) == 8


def test_pinned_ticks_for_day_emits_8_ticks_in_order() -> None:
    day = dt.date(2026, 5, 18)
    ticks = pinned_ticks_for_day(day)
    assert len(ticks) == 8
    labels = [label for _, label in ticks]
    assert labels == [
        "morning_open",
        "intraday_mtm_sample",
        "morning_close",
        "afternoon_open",
        "intraday_mtm_sample",
        "afternoon_close",
        "eod_pipeline",
        "advance_day",
    ]
    # Times monotonically increasing.
    times = [when for when, _ in ticks]
    assert times == sorted(times)


def test_pinned_ticks_use_shanghai_timezone() -> None:
    when, _ = pinned_ticks_for_day(dt.date(2026, 5, 18))[0]
    assert when.tzinfo is not None
    assert when.hour == 9
    assert when.minute == 30


# ---------------------------------------------------------------------------
# run_simulation
# ---------------------------------------------------------------------------


def test_allowed_days_locked() -> None:
    assert ALLOWED_DAYS == (1, 5, 10, 45)


@pytest.mark.asyncio
async def test_run_simulation_rejects_invalid_days_value() -> None:
    with pytest.raises(ValueError, match="--days"):
        await run_simulation(days=3, start_date=dt.date(2026, 5, 18))


@pytest.mark.asyncio
async def test_run_simulation_no_callback_walks_calendar_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No callback wired ⇒ harness still completes and reports OK.

    Production preflight contract requires LLM stub mode to be on
    (Codex cycle 1 P1 fix); set the env so the default invariant
    holds.
    """
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    outcome = await run_simulation(days=1, start_date=dt.date(2026, 5, 18))
    assert outcome.requested_days == 1
    assert outcome.trading_days_walked == 1
    assert outcome.tick_count == 8
    assert outcome.tick_callback_errors == ()
    assert outcome.reset_triggers_fired == ()
    assert outcome.ok


@pytest.mark.asyncio
async def test_run_simulation_fails_when_stub_disabled_default_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex cycle 1 P1 regression — without ``allow_real_llm`` and
    with ``QUANTMIND_LLM_STUB`` falsy in the env, ``ok`` must be
    False so the harness cannot silently bless a real-LLM run."""
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "0")
    outcome = await run_simulation(days=1, start_date=dt.date(2026, 5, 18))
    assert outcome.llm_router_stubbed is False
    assert outcome.allow_real_llm is False
    assert outcome.ok is False


@pytest.mark.asyncio
async def test_run_simulation_passes_when_allow_real_llm_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator opt-in path: ``allow_real_llm=True`` relaxes the
    stub-mode invariant so the harness reports PASS even though
    ``llm_router_stubbed=False``."""
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "0")
    outcome = await run_simulation(
        days=1,
        start_date=dt.date(2026, 5, 18),
        allow_real_llm=True,
    )
    assert outcome.llm_router_stubbed is False
    assert outcome.allow_real_llm is True
    assert outcome.ok is True


@pytest.mark.asyncio
async def test_run_simulation_invokes_callback_for_every_tick() -> None:
    fired: list[tuple[str, str]] = []

    async def _cb(when: dt.datetime, label: str) -> None:
        fired.append((when.isoformat(), label))

    outcome = await run_simulation(
        days=1,
        start_date=dt.date(2026, 5, 18),
        tick_callback=_cb,
    )
    assert len(fired) == 8 == outcome.tick_count


@pytest.mark.asyncio
async def test_run_simulation_5_days_yields_40_ticks() -> None:
    fired_count = 0

    async def _cb(_when: dt.datetime, _label: str) -> None:
        nonlocal fired_count
        fired_count += 1

    outcome = await run_simulation(
        days=5,
        start_date=dt.date(2026, 5, 18),
        tick_callback=_cb,
    )
    assert outcome.trading_days_walked == 5
    assert fired_count == 40 == outcome.tick_count


@pytest.mark.asyncio
async def test_run_simulation_callback_error_recorded_but_walk_continues() -> None:
    async def _failing_cb(_when: dt.datetime, label: str) -> None:
        if label == "eod_pipeline":
            raise RuntimeError("simulated EOD failure")

    outcome = await run_simulation(
        days=1,
        start_date=dt.date(2026, 5, 18),
        tick_callback=_failing_cb,
    )
    assert outcome.tick_count == 8  # still ran every tick
    assert len(outcome.tick_callback_errors) == 1
    assert "simulated EOD failure" in outcome.tick_callback_errors[0]
    assert outcome.ok is False


@pytest.mark.asyncio
async def test_run_simulation_reset_trigger_observer_marks_failure() -> None:
    """A substrate bug that fires a reset trigger flips ok to False."""

    def _fake_reset_observer() -> list[str]:
        return ["LLM_FULL_STOP_1H"]

    outcome = await run_simulation(
        days=1,
        start_date=dt.date(2026, 5, 18),
        reset_trigger_observer=_fake_reset_observer,
    )
    assert outcome.reset_triggers_fired == ("LLM_FULL_STOP_1H",)
    assert outcome.ok is False


@pytest.mark.asyncio
async def test_run_simulation_real_llm_call_observer_marks_failure() -> None:
    def _real_llm_observer() -> int:
        return 3

    outcome = await run_simulation(
        days=1,
        start_date=dt.date(2026, 5, 18),
        real_llm_call_observer=_real_llm_observer,
    )
    assert outcome.real_llm_calls_observed == 3
    assert outcome.ok is False


@pytest.mark.asyncio
async def test_run_simulation_reads_llm_stub_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    outcome = await run_simulation(days=1, start_date=dt.date(2026, 5, 18))
    assert outcome.llm_router_stubbed is True


@pytest.mark.asyncio
async def test_run_simulation_reports_stub_disabled_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(QUANTMIND_LLM_STUB_ENV, raising=False)
    outcome = await run_simulation(days=1, start_date=dt.date(2026, 5, 18))
    assert outcome.llm_router_stubbed is False


# ---------------------------------------------------------------------------
# SimulationOutcome.ok
# ---------------------------------------------------------------------------


def _base_outcome(**overrides: Any) -> SimulationOutcome:
    defaults = dict(
        requested_days=1,
        start_date="2026-05-18",
        end_date="2026-05-18",
        trading_days_walked=1,
        tick_count=8,
        llm_router_stubbed=True,
        real_llm_calls_observed=0,
        reset_triggers_fired=(),
        tick_callback_errors=(),
        elapsed_seconds=0.1,
    )
    defaults.update(overrides)
    return SimulationOutcome(**defaults)


def test_ok_requires_days_walked_matches_requested() -> None:
    outcome = _base_outcome(requested_days=5, trading_days_walked=4)
    assert outcome.ok is False


def test_ok_fails_when_reset_triggered() -> None:
    outcome = _base_outcome(reset_triggers_fired=("MOCK_BROKER_CORRUPTION",))
    assert outcome.ok is False


def test_ok_fails_when_callback_errors_recorded() -> None:
    outcome = _base_outcome(tick_callback_errors=("some failure",))
    assert outcome.ok is False


def test_ok_fails_when_real_llm_call_observed() -> None:
    outcome = _base_outcome(real_llm_calls_observed=1)
    assert outcome.ok is False


def test_ok_passes_when_all_invariants_clean() -> None:
    outcome = _base_outcome()
    assert outcome.ok is True


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------


def test_cli_main_pass_smoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(QUANTMIND_LLM_STUB_ENV, raising=False)
    rc = main(["--days", "1", "--start", "2026-05-18", "--json"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    envelope = json.loads(captured.out)
    assert envelope["verdict"] == "PASS"
    assert envelope["trading_days_walked"] == 1
    assert envelope["tick_count"] == 8
    assert envelope["llm_router_stubbed"] is True


def test_cli_main_rejects_invalid_days(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with suppress(SystemExit):
        main(["--days", "3", "--start", "2026-05-18"])
    captured = capsys.readouterr()
    # argparse choices error lands on stderr.
    assert "invalid choice" in captured.err or "choices" in captured.err


def test_cli_main_rejects_invalid_start_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    with pytest.raises(SystemExit) as exc:
        main(["--days", "1", "--start", "not-a-date"])
    assert "invalid --start" in str(exc.value)


def test_cli_main_table_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")
    rc = main(["--days", "1", "--start", "2026-05-18"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "simulate_n_trading_days verdict: PASS" in captured.out


def test_cli_main_sets_stub_env_when_not_allow_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(QUANTMIND_LLM_STUB_ENV, raising=False)
    # Make sure setdefault behaviour applies.
    main(["--days", "1", "--start", "2026-05-18", "--json"])
    assert os.environ.get(QUANTMIND_LLM_STUB_ENV) == "1"


# ---------------------------------------------------------------------------
# End-to-end smoke — stub LLM router actually returns stub across 5 days
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e2e_5_day_walk_with_stub_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Walk 5 trading days; on each tick, attempt an LLM completion via
    the real LLMRouter (with stub env set). Assert every response is the
    canned stub and no real provider was touched."""
    monkeypatch.setenv(QUANTMIND_LLM_STUB_ENV, "1")

    from backend.llm.router import LLMRouter, StubChatCompletion

    router = LLMRouter(config_path="config/agent_models.yaml")
    real_call_count = 0

    async def _exploding_provider(*args: Any, **kwargs: Any) -> None:
        nonlocal real_call_count
        real_call_count += 1
        raise AssertionError("real provider called despite stub mode")

    monkeypatch.setattr(router, "_call_provider", _exploding_provider)

    stub_responses: list[StubChatCompletion] = []

    async def _cb(_when: dt.datetime, _label: str) -> None:
        resp = await router.complete(
            agent_name="fundamental_analyst",
            messages=[{"role": "user", "content": "tick"}],
        )
        assert isinstance(resp, StubChatCompletion)
        stub_responses.append(resp)

    def _no_reset_observer() -> list[str]:
        return []

    def _real_llm_count() -> int:
        return real_call_count

    outcome = await run_simulation(
        days=5,
        start_date=dt.date(2026, 5, 18),
        tick_callback=_cb,
        reset_trigger_observer=_no_reset_observer,
        real_llm_call_observer=_real_llm_count,
    )
    assert outcome.ok
    assert outcome.trading_days_walked == 5
    assert outcome.tick_count == 40
    assert outcome.real_llm_calls_observed == 0
    assert outcome.reset_triggers_fired == ()
    assert len(stub_responses) == 40


# ---------------------------------------------------------------------------
# Silence unused-import warning
# ---------------------------------------------------------------------------


_ = asyncio
_ = mock
