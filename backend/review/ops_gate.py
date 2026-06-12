"""Non-trading-day review lane ops gate (AA-003).

P1-2.A-amendment-2026-06-12 §1.4: the weekend / holiday review +
experiment lane runs ONLY when every operational precondition holds —
otherwise it skips with an audit row. Every unknown input fails its
check (fail-closed): a review/experiment lane must never run on top of
an unverified system state.

``activation_allowed`` is separate from the gate verdict: §1.4 forbids
any ACTIVATION action within 2h of the next market open, but reading /
aggregating / planning is still allowed — Phase AB consumes this flag.

Pure module: all inputs are injected values; no IO beyond the static
holiday calendar (config/holidays.yaml via backend.utils.trading_hours).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.utils.trading_hours import is_trading_day

SHANGHAI = ZoneInfo("Asia/Shanghai")

DISK_FREE_MIN_BYTES = 2 * 1024**3
"""Long backtest / parameter-search artifacts need headroom; 2 GiB is
far above any single run's footprint, so tripping this means the host
needs attention before any experiment work."""

LLM_BUDGET_MIN_REMAINING_CNY = 10.0
"""Weekend experiments share the SAME ``llm:usage:{utc_date}`` counter
as live trading (§1.4 — no cap bypass). Require ¥10 headroom under the
¥100 daily hard cap before starting a lane that may spend."""

ACTIVATION_BLACKOUT = timedelta(hours=2)
"""§1.4 — no activation action within 2h of the next market open."""

MARKET_OPEN = time(9, 30)


@dataclass(frozen=True)
class OpsGateInputs:
    """Injected observations; ``None`` always means unknown → fail."""

    open_ticket_count: int | None
    snapshot_checksum_valid: bool | None
    latest_snapshot_trade_date: str | None
    last_trading_date: str | None
    artifact_registry_ok: bool | None
    disk_free_bytes: int | None
    llm_budget_remaining_cny: float | None
    kline_max_date: str | None
    now: datetime
    next_open_at: datetime | None


@dataclass(frozen=True)
class OpsGateCheck:
    """One named precondition verdict."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class OpsGateResult:
    """Aggregate gate verdict + the AB activation-window flag."""

    passed: bool
    checks: tuple[OpsGateCheck, ...]
    activation_allowed: bool

    @property
    def failed_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.checks if not c.passed)


def next_market_open(
    now: datetime,
    *,
    is_trading_day_fn: Callable[[date], bool] = is_trading_day,
) -> datetime | None:
    """The next 09:30 session open strictly after ``now`` (≤30d scan)."""
    local = now.astimezone(SHANGHAI)
    for offset in range(31):
        candidate = local.date() + timedelta(days=offset)
        if not is_trading_day_fn(candidate):
            continue
        open_dt = datetime.combine(candidate, MARKET_OPEN, tzinfo=SHANGHAI)
        if open_dt > local:
            return open_dt
    return None


def evaluate_ops_gate(inputs: OpsGateInputs) -> OpsGateResult:
    """Evaluate every §1.4 precondition; unknown = failed (fail-closed)."""
    checks = (
        _check_bool(
            "no_open_reconciliation_ticket",
            None
            if inputs.open_ticket_count is None
            else inputs.open_ticket_count == 0,
            f"open_tickets={inputs.open_ticket_count}",
        ),
        _check_bool(
            "snapshot_checksum_valid",
            inputs.snapshot_checksum_valid,
            f"valid={inputs.snapshot_checksum_valid}",
        ),
        _check_bool(
            "snapshot_fresh",
            _both_known_equal(
                inputs.latest_snapshot_trade_date, inputs.last_trading_date
            ),
            f"snapshot={inputs.latest_snapshot_trade_date} "
            f"expected={inputs.last_trading_date}",
        ),
        _check_bool(
            "artifact_registry_ok",
            inputs.artifact_registry_ok,
            f"ok={inputs.artifact_registry_ok}",
        ),
        _check_bool(
            "disk_free",
            None
            if inputs.disk_free_bytes is None
            else inputs.disk_free_bytes >= DISK_FREE_MIN_BYTES,
            f"free={inputs.disk_free_bytes} min={DISK_FREE_MIN_BYTES}",
        ),
        _check_bool(
            "llm_budget_headroom",
            None
            if inputs.llm_budget_remaining_cny is None
            else (
                inputs.llm_budget_remaining_cny
                >= LLM_BUDGET_MIN_REMAINING_CNY
            ),
            f"remaining={inputs.llm_budget_remaining_cny} "
            f"min={LLM_BUDGET_MIN_REMAINING_CNY}",
        ),
        _check_bool(
            "market_data_fresh",
            None
            if inputs.kline_max_date is None
            or inputs.last_trading_date is None
            else inputs.kline_max_date >= inputs.last_trading_date,
            f"kline_max={inputs.kline_max_date} "
            f"expected>={inputs.last_trading_date}",
        ),
    )
    activation_allowed = (
        inputs.next_open_at is not None
        and inputs.next_open_at - inputs.now >= ACTIVATION_BLACKOUT
    )
    return OpsGateResult(
        passed=all(c.passed for c in checks),
        checks=checks,
        activation_allowed=activation_allowed,
    )


def _both_known_equal(a: str | None, b: str | None) -> bool | None:
    if a is None or b is None:
        return None
    return a == b


def _check_bool(
    name: str, verdict: bool | None, detail: str
) -> OpsGateCheck:
    # None (unknown) fails — the lane must not run on unverified state.
    return OpsGateCheck(name=name, passed=verdict is True, detail=detail)


__all__ = [
    "ACTIVATION_BLACKOUT",
    "DISK_FREE_MIN_BYTES",
    "LLM_BUDGET_MIN_REMAINING_CNY",
    "OpsGateCheck",
    "OpsGateInputs",
    "OpsGateResult",
    "evaluate_ops_gate",
    "next_market_open",
]
