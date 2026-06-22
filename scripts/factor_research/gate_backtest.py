"""Gate-arena event-loop runner (QGR-2 build-new ①, part 2).

Replays a candidate gate strategy through the *real* system mechanics via the
deterministic :func:`backend.backtest.harness.run_backtest` event loop and maps
its :class:`BacktestResult` → the arena's **primary metric** (QGR plan §4.1):
**absolute net P&L + max drawdown + turnover**, plus a non-overlapping
per-horizon return series the CPCV / DSR disclosure consumes.

The gate's signal enters through an injected :class:`ScoreProvider`
(:class:`PanelScoreProvider` here reads a per-day score table — QGR-4 wires the
real factor scoring layer); the BarSource is :class:`PitBarSource` (PIT-backed).
This is a **quant-mechanism proxy** — it does NOT include the LLM debate, the
full RiskEngine, or Line-2 intraday risk (QGR plan §4.4 / strategy.py §2.3); a
go-live decision still needs the real-pipeline shadow replay. Offline, pure,
deterministic; never touches the live path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from backend.backtest.event_loop import BarSource
from backend.backtest.friction import FrictionParams
from backend.backtest.harness import BacktestResult, BacktestSpec, run_backtest
from backend.backtest.strategy import (
    CodeHealth,
    DailySignals,
    StrategyConfig,
)
from backend.candidate_selector import (
    CandidateSelector,
    QuantCandidate,
    SelectorConfig,
)
from backend.slot_portfolio import load_rotation_policy_config
from backend.strategy_evolution.harsh_fill_model import HarshFillConfig

DEFAULT_MAX_POSITIONS = 5
DEFAULT_SINGLE_STOCK_CAP_PCT = 15
DEFAULT_ROTATION_CONFIG_PATH = "config/slot_rotation_policy.yaml"

# Friction mirroring config/broker.yaml (CLAUDE.md §2.7): commission 0.015%,
# min ¥5, stamp 0.1%, SZ 过户费 0.00341%, board slippage 1.5/1.5/3.5/1.5 bp.
# Production should load these from broker.yaml; pinned here for the offline arena.
_DEFAULT_SLIPPAGE_BPS = {"sh_main": 1.5, "sz_main": 1.5, "chuangye": 3.5, "etf": 1.5}


def default_friction() -> FrictionParams:
    """The MockBroker-parity friction the arena charges (mirrors broker.yaml)."""
    return FrictionParams(
        commission_rate=0.00015,
        min_commission_cents=500,
        stamp_tax_rate=0.001,
        transfer_fee_rate=0.0000341,
        slippage_bps_by_board=dict(_DEFAULT_SLIPPAGE_BPS),
    )


def default_selector(
    *, final_shortlist_size: int = 5, min_quant_slots: int = 3
) -> CandidateSelector:
    """A pure-quant selector (no advisory re-rank — the arena scores quant only)."""
    return CandidateSelector(
        SelectorConfig(
            version="qgr-arena-v1",
            final_shortlist_size=final_shortlist_size,
            min_quant_slots=min_quant_slots,
            max_percentile_shift=0.0,
            advisory_weight=0.0,
            feature_def_hash="",
        )
    )


def default_strategy_config(
    *,
    max_total_positions: int = DEFAULT_MAX_POSITIONS,
    single_stock_cap_percent: int = DEFAULT_SINGLE_STOCK_CAP_PCT,
    rotation_config_path: str = DEFAULT_ROTATION_CONFIG_PATH,
) -> StrategyConfig:
    """Build the live-parity strategy config (selector + ≤5-slot rotation)."""
    return StrategyConfig(
        selector=default_selector(),
        rotation=load_rotation_policy_config(rotation_config_path),
        max_total_positions=max_total_positions,
        single_stock_cap_percent=single_stock_cap_percent,
    )


def _within_day_percentiles(scores: Sequence[tuple[str, float]]) -> dict[str, float]:
    """Cross-sectional rank in [0, 1] (highest score → 1.0); ties share a rank.

    Single ``O(n log n)`` sort — a per-code ``sum(... < sc)`` scan is ``O(n²)`` and
    becomes billions of comparisons on a full A-share panel over a decade replay.
    Each code's percentile is ``(# strictly-lower scores) / (n − 1)`` (a tie group
    shares the strictly-lower count of its lowest member).
    """
    n = len(scores)
    if n == 0:
        return {}
    if n == 1:
        return {scores[0][0]: 1.0}
    ordered = sorted(scores, key=lambda kv: kv[1])
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j < n and ordered[j][1] == ordered[i][1]:
            j += 1
        rank = i / (n - 1)  # strictly-lower count for this tie group
        for k in range(i, j):
            out[ordered[k][0]] = rank
        i = j
    return out


class PanelScoreProvider:
    """Injected as-of score provider reading a per-day ``{code: score}`` table.

    For QGR-2 the per-code ``CodeHealth`` is derived (within-day percentile +
    composite score, ``qualified=True``, incumbent-only fields at healthy
    defaults), so default health does not trigger rotation; tests / QGR-4 may
    pass ``health_overrides`` to exercise the weakness-and-margin gate.
    """

    def __init__(
        self,
        scores_by_day: Mapping[str, Sequence[tuple[str, float]]],
        *,
        health_overrides: Mapping[str, Mapping[str, CodeHealth]] | None = None,
    ) -> None:
        self._scores = {d: list(v) for d, v in scores_by_day.items()}
        self._overrides = health_overrides or {}

    def signals_asof(self, day: str) -> DailySignals:
        scores = self._scores.get(day, [])
        pct = _within_day_percentiles(scores)
        overrides = self._overrides.get(day, {})
        health: dict[str, CodeHealth] = {}
        for code, sc in scores:
            health[code] = overrides.get(
                code,
                CodeHealth(line1_percentile=pct[code], composite_score=sc),
            )
        # Held codes that dropped out of today's score table but carry a health
        # override must still be merged — ``decide_day`` only evaluates incumbents
        # present in ``signals.health``, so an override-only weak holding would
        # otherwise never be considered for rotation.
        for code, override in overrides.items():
            health.setdefault(code, override)
        return DailySignals(
            trade_date=day,
            quant_candidates=tuple(
                QuantCandidate(code=code, score=sc) for code, sc in scores
            ),
            health=health,
        )


# Invariant kinds that MUST hold — a real accounting bug, never tolerated.
_HARD_INVARIANT_KINDS = frozenset(
    {"cash_conservation", "position_conservation", "fee_recompute"}
)
# Exposure-cap kinds — a *post-fill* sanity check, surfaced separately (a count)
# rather than conflated with the hard conservation guarantee. Two known proxy
# artifacts (the live RiskEngine enforces both caps PRE-trade, where the gate is
# faithful; the event-loop ``decide_day`` is a §4.4 proxy that does NOT):
#   • single_stock_cap — the harness divides the ≤15% cap by post-friction equity,
#     so a slot sized at the binding 15% can overshoot sub-percent on a gap-up.
#   • total_position_cap — ``decide_day`` equal-weights 5 slots at min(20%, 15%)
#     ≈ 75% gross, above the live 70% total cap (frozen backtest code; an
#     amendment would be needed to enforce it in the proxy). A real sizing bug
#     shows MANY large-overshoot violations + a degraded avg_exposure.
_CAP_INVARIANT_KINDS = frozenset({"single_stock_cap", "total_position_cap"})


@dataclass(frozen=True)
class GateBacktestResult:
    """Arena result — the frozen evaluation口径 (primary = net P&L + MDD + turnover)."""

    trading_days: int
    initial_capital_yuan: float
    final_equity_yuan: float
    net_pnl_yuan: float
    total_return: float
    max_drawdown_pct: float
    monthly_turnover: float
    fill_count: int
    signal_count: int
    horizon: int
    daily_returns: tuple[float, ...]
    period_returns: tuple[float, ...]
    invariants_ok: bool
    """Full closed-form verdict (CONSISTENT) — includes the strict post-fill cap."""
    conservation_ok: bool
    """The HARD guarantee: cash / position / fee conservation all hold."""
    exposure_cap_violations: int
    """Count of single-stock / total-position cap overshoots (proxy artifact;
    see ``_CAP_INVARIANT_KINDS``). A genuine sizing bug shows up as many of these
    plus a degraded ``avg_exposure`` — a friction-epsilon overshoot shows one or
    two on gappy days."""
    backtest_result: BacktestResult


def _chunk_compound(daily_returns: Sequence[float], horizon: int) -> tuple[float, ...]:
    """Non-overlapping ``horizon``-day compounded returns (drops the remainder)."""
    h = max(1, horizon)
    n_chunks = len(daily_returns) // h
    out: list[float] = []
    for j in range(n_chunks):
        comp = 1.0
        for r in daily_returns[j * h : (j + 1) * h]:
            comp *= 1.0 + r
        out.append(comp - 1.0)
    return tuple(out)


def run_gate_backtest(
    *,
    bar_source: BarSource,
    provider: PanelScoreProvider,
    strategy_config: StrategyConfig,
    initial_capital_yuan: float,
    horizon: int = 5,
    friction_params: FrictionParams | None = None,
    harsh_config: HarshFillConfig | None = None,
    frozen_cash_yuan: float = 0.0,
) -> GateBacktestResult:
    """Replay the gate strategy through the event loop → arena primary metric."""
    spec = BacktestSpec(
        initial_capital_cents=round(initial_capital_yuan * 100),
        frozen_cash_cents=round(frozen_cash_yuan * 100),
    )
    result = run_backtest(
        spec=spec,
        bar_source=bar_source,
        provider=provider,
        strategy_config=strategy_config,
        friction_params=friction_params or default_friction(),
        harsh_config=harsh_config,
    )
    initial = result.initial_capital_cents
    violations = result.invariant_report.violations
    conservation_ok = not any(v.kind in _HARD_INVARIANT_KINDS for v in violations)
    cap_violations = sum(1 for v in violations if v.kind in _CAP_INVARIANT_KINDS)
    return GateBacktestResult(
        trading_days=result.trading_days,
        initial_capital_yuan=initial / 100.0,
        final_equity_yuan=result.final_equity_cents / 100.0,
        net_pnl_yuan=result.pnl_cents / 100.0,
        total_return=(result.pnl_cents / initial) if initial > 0 else 0.0,
        max_drawdown_pct=result.max_drawdown_pct,
        monthly_turnover=result.monthly_turnover,
        fill_count=result.fill_count,
        signal_count=result.signal_count,
        horizon=horizon,
        daily_returns=result.daily_returns,
        period_returns=_chunk_compound(result.daily_returns, horizon),
        invariants_ok=result.invariant_report.consistent,
        conservation_ok=conservation_ok,
        exposure_cap_violations=cap_violations,
        backtest_result=result,
    )


__all__ = [
    "DEFAULT_MAX_POSITIONS",
    "GateBacktestResult",
    "PanelScoreProvider",
    "default_friction",
    "default_selector",
    "default_strategy_config",
    "run_gate_backtest",
]
