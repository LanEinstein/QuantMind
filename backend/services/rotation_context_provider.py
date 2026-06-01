"""Production ``RotationContextProvider`` (Phase V-004 wiring).

Sources the deterministic Line-2 incumbent health + builds the rotation SELL
context for :class:`backend.orchestration.rotation_runner.RotationRunner`,
reusing the proven Line-2 daily machinery (``build_line2_run_state`` +
``build_line2_code_contexts`` + ``Line2DailyProvider`` + ``AnomalyDetector``).
Lives in ``backend.services`` (not the import-isolated runner) so it may freely
import broker / risk / data / monitoring.

Health sourcing (conservative, deterministic over the T-1 EOD frame):

* ``protective_stop_active`` — the code has an imminent Line-2 hard SELL trigger
  (``evaluate_sell_intents`` produces an intent) → rotation YIELDS to it;
* ``anomaly_flag_active`` — the code has any anomaly signal (confirmation 6b);
* ``drawdown_from_local_high`` — from the frame's close series (confirmation 6c);
* ``suspended`` — from ``partition_by_suspension``;
* ``sell_limit_price`` — the frame's last close (PIT, deterministic);
* ``score_median_20d`` / ``score_mad_20d`` default 0 (confirmation 6a off until a
  score-history source exists — fail-closed, leaving 6b/6c).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from backend.budget_policy.policy import BudgetCandidate, BudgetTierPolicy
from backend.data.stock_metadata import get_lot_size
from backend.data.trading_calendar import count_trading_days, next_trading_day
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.monitoring.anomaly import AnomalyScanResult
from backend.monitoring.sell_signal import (
    SellIntent,
    evaluate_sell_intents,
    normalize_position_codes,
)
from backend.orchestration.rotation_runner import IncumbentHealth
from backend.screening.screener import ScreenResult
from backend.services.instruction_plan_builder import MonitoringAssemblyContext
from backend.services.line2_context_providers import Line2DailyProvider

log = structlog.get_logger(component="services.rotation_context_provider")

# A-share standard lot (mirrors config/risk.yaml position_limits.volume_lot_size);
# the rotation SELL size is floored to whole lots so RiskEngine check-3 accepts it.
_LOT = 100


def _bare(code: str) -> str:
    return code.split(".")[0].strip()


def _trade_date_str(d: Any) -> str:
    return d.strftime("%Y%m%d")


class ProductionRotationProvider:
    """Concrete RotationContextProvider over live broker + the T-1 EOD frame.

    Composes a :class:`Line2DailyProvider` for the SELL context + the Line-2
    ``AnomalyDetector`` scan for the deterministic incumbent health. Built once
    per daily run by the scheduler callback (``main.py``), exactly like the
    Line-2 daily provider.
    """

    def __init__(
        self,
        *,
        line2_provider: Line2DailyProvider,
        scan: AnomalyScanResult,
        frame: MarketDataSnapshot,
        rotations_today: int,
        daily_new_instruction_budget_remaining: int,
        drawdown_window: int = 20,
    ) -> None:
        self._line2 = line2_provider
        self._scan = scan
        self._frame = frame
        self._rotations_today = rotations_today
        self._cap_remaining = daily_new_instruction_budget_remaining
        self._drawdown_window = drawdown_window
        positions = tuple(line2_provider.held_positions)
        self._positions = normalize_position_codes(positions)
        self._names = dict(line2_provider.name_by_code)
        # Deterministic Line-2 SELL intents over the same scan (hard exits).
        self._sell_intents = {
            i.code: i
            for i in evaluate_sell_intents(
                scan, self._positions, name_by_code=self._names
            )
        }
        # Per-code anomaly presence (confirmation 6b).
        self._anomaly_codes = {s.code for s in scan.signals}
        # Per-code close series (drawdown + last price), parsed from the frame.
        self._closes = _closes_by_code(frame)

    @property
    def held_codes(self) -> frozenset[str]:
        return frozenset(_bare(p.code) for p in self._positions if p.volume > 0)

    def incumbent_health(self, code: str) -> IncumbentHealth:
        bare = _bare(code)
        pos = next((p for p in self._positions if _bare(p.code) == bare), None)
        raw_available = int(pos.available_volume) if pos is not None else 0
        # Lot-floor the SELL size like the Line-2 anomaly path — an odd-lot
        # residual (e.g. from a corporate action) would otherwise make
        # RiskEngine check-3 (volume % lot != 0) REJECT the rotation SELL and
        # silently strand the slot on the weak name (codex-review V-004 fix).
        available_volume = (raw_available // _LOT) * _LOT
        closes = self._closes.get(bare, ())
        last = closes[-1] if closes else 0.0
        intent = self._sell_intents.get(bare)
        # A held code with NO fresh bar in the T-1 frame is almost certainly
        # halted / delisted → it cannot be sold today, so veto the rotation
        # (condition 7). RiskEngine check-12 backstops a limit-down SELL, so
        # ``limit_down_unsellable`` stays conservatively False here; there is no
        # corporate-action feed (False, fail-open per the docstring).
        suspended = not closes
        return IncumbentHealth(
            name=self._names.get(bare, bare),
            available_volume=available_volume,
            # PIT, deterministic; a hard Line-2 SELL (if any) prices itself.
            sell_limit_price=intent.limit_price if intent is not None else last,
            # A Line-2 hard SELL trigger is imminent → rotation must yield to it.
            protective_stop_active=intent is not None,
            hard_exit_pending=False,
            anomaly_flag_active=bare in self._anomaly_codes,
            drawdown_from_local_high=_drawdown(closes, self._drawdown_window),
            suspended=suspended,
            limit_down_unsellable=False,
            corporate_action_unsafe=False,
            score_median_20d=0.0,
            score_mad_20d=0.0,
        )

    @property
    def rotations_today(self) -> int:
        return self._rotations_today

    @property
    def daily_new_instruction_budget_remaining(self) -> int:
        return self._cap_remaining

    @property
    def protective_action_needs_cap_today(self) -> bool:
        # Any imminent Line-2 hard SELL (anomaly stop) will consume today's cap →
        # rotation yields to the protective exit (§1.5 priority rule).
        return bool(self._sell_intents)

    def trading_days_between(self, earlier: str, later: str) -> int:
        a = datetime.strptime(earlier, "%Y%m%d").date()
        b = datetime.strptime(later, "%Y%m%d").date()
        # Trading days elapsed in the half-open interval [earlier, later).
        return count_trading_days(a, b)

    def trading_day_ahead(self, trade_date: str, n: int) -> str:
        cursor = datetime.strptime(trade_date, "%Y%m%d").date()
        for _ in range(max(0, n)):
            cursor = next_trading_day(cursor)
        return _trade_date_str(cursor)

    def build_rotation_sell_context(
        self,
        *,
        code: str,
        name: str,
        available_volume: int,
        limit_price: float,
        reason: str,
        signal_id: str,
        seq: int,
        now: datetime,
    ) -> MonitoringAssemblyContext:
        """Synthesise the rotation SELL intent + delegate to the Line-2 provider.

        The rotation SELL is a deterministic, monitoring-class SELL: it reuses
        ``Line2DailyProvider.build_sell_context`` (the same single construction
        point input as an anomaly SELL) with an ``AnomalyKind.ROTATION``-tagged
        intent so audit can tell the two apart."""
        from backend.monitoring.anomaly import AnomalyKind

        intent = SellIntent(
            code=_bare(code), name=name, available_volume=available_volume,
            limit_price=limit_price, anomaly_reason=reason,
            trigger_kind=AnomalyKind.ROTATION,
        )
        return self._line2.build_sell_context(
            intent, signal_id=signal_id, seq=seq, now=now
        )


def compute_qualified_codes(
    screen: ScreenResult, budget_policy: BudgetTierPolicy, available_cash: float
) -> frozenset[str]:
    """Affordable challenger codes — the SAME budget gate Line-1 BUYs pass.

    A rotation challenger must clear the affordability tier (it will be bought
    T+1), so the qualified set = the budget policy's affordable candidates over
    the screen (deterministic; mirrors ``Line1Runner.run`` step 2). Per-lot cost
    is ``last_price × board lot size`` (single source of truth)."""
    cands = [
        BudgetCandidate(
            code=c.code, per_lot_cost=c.last_price * get_lot_size(c.board)
        )
        for c in screen.candidates
    ]
    assessment = budget_policy.assess(available_cash, cands)
    if assessment.no_compliant_trade:
        return frozenset()
    return frozenset(a.code for a in assessment.affordable)


def _closes_by_code(frame: MarketDataSnapshot) -> dict[str, tuple[float, ...]]:
    """Parse code → close series from the screener/Line-2 CSV market frame.

    Frame row: ``ts_code,name,listed_trading_days,closes,amounts`` with ``closes``
    a ``|``-separated oldest→newest float list (same contract the screener +
    AnomalyDetector consume). Malformed rows are skipped (fail-open per code —
    a missing series just disables drawdown for that code, not the whole run).
    """
    out: dict[str, tuple[float, ...]] = {}
    if frame.encoding != "csv":
        return out
    text = frame.raw_payload.decode("utf-8", errors="replace")
    for line in text.splitlines()[1:]:  # skip header
        parts = line.split(",")
        if len(parts) < 4:
            continue
        code = _bare(parts[0])
        try:
            closes = tuple(float(tok) for tok in parts[3].split("|") if tok != "")
        except ValueError:
            continue
        if closes:
            out[code] = closes
    return out


def _drawdown(closes: tuple[float, ...], window: int) -> float:
    """Fractional drawdown of the last close from the local high over ``window``.

    ``(local_high − last) / local_high`` in [0, 1]; 0 when at/above the high or
    on insufficient/degenerate data (fail-closed — no spurious confirmation)."""
    if not closes:
        return 0.0
    recent = closes[-window:]
    local_high = max(recent)
    last = recent[-1]
    if local_high <= 0.0 or last >= local_high:
        return 0.0
    return (local_high - last) / local_high


__all__ = ["ProductionRotationProvider", "compute_qualified_codes"]
