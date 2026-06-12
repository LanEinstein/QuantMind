"""AB-002 ObjectivePromotionEngine tests — deterministic gates."""

from __future__ import annotations

import datetime as dt
from typing import Any

from backend.services.shadow_chain import make_acceptance_report
from backend.strategy_evolution.backtest_oracle import OracleVerdict
from backend.strategy_evolution.experiment_registry import ExperimentKind
from backend.strategy_evolution.objective_promotion import (
    TIERED_WINDOW_GATES,
    AntiGamingStats,
    PromotionInputs,
    evaluate_promotion,
)

NOW = dt.datetime(2026, 6, 12, 22, 0, tzinfo=dt.UTC)
HASH = "d" * 64
EXP_ID = "e" * 64


def _report(
    *,
    pnl: float = 5_000.0,
    drawdown: float = 0.04,
    excess: float = 0.02,
    accuracy: float = 0.995,
) -> Any:
    return make_acceptance_report(
        metric_values={
            "instruction_completion_rate": 0.97,
            "execution_report_accuracy_rate": accuracy,
            "data_missing_rate": 0.005,
            "llm_timeout_rate": 0.03,
            "signal_generation_rate": 0.96,
            "max_drawdown_pct": drawdown,
            "pnl_cny": pnl,
            "csi300_excess_pct": excess,
        },
    )


def _inputs(**overrides: Any) -> PromotionInputs:
    base: dict[str, Any] = {
        "kind": ExperimentKind.THRESHOLD_PARAM,
        "family": "line2.drawdown_stop",
        "artifact_hash": HASH,
        "experiment_id": EXP_ID,
        "trading_days": 20,
        "sample_count": 40,
        # Strongly positive, low-noise excess — clears CI and DSR.
        "daily_excess": tuple(
            120.0 + (i % 5) * 4.0 for i in range(20)
        ),
        "champion_report": _report(
            pnl=4_000.0, drawdown=0.05, excess=0.02, accuracy=0.995
        ),
        "challenger_report": _report(
            pnl=9_000.0, drawdown=0.03, excess=0.03, accuracy=0.997
        ),
        "anti_gaming": AntiGamingStats(
            avg_exposure_ratio=0.45,
            signal_count=40,
            monthly_turnover=2.5,
        ),
        "n_trials": 3,
        "oracle_verdict": OracleVerdict.CONSISTENT,
        "evaluated_at": NOW,
    }
    base.update(overrides)
    return PromotionInputs(**base)


class TestHappyPath:
    def test_clean_challenger_promotes(self) -> None:
        decision = evaluate_promotion(_inputs())
        assert decision.promoted, decision.failed_gates
        assert decision.oracle_cross_checked
        assert len(decision.gates) == 9

    def test_same_inputs_same_decision(self) -> None:
        a = evaluate_promotion(_inputs())
        b = evaluate_promotion(_inputs())
        assert a == b  # bit-for-bit, incl. the bootstrap CI gate

    def test_inputs_digest_moves_with_inputs(self) -> None:
        a = evaluate_promotion(_inputs())
        b = evaluate_promotion(_inputs(n_trials=300))
        assert a.inputs_digest != b.inputs_digest


class TestTieredWindows:
    def test_threshold_window_boundaries(self) -> None:
        gate = TIERED_WINDOW_GATES[ExperimentKind.THRESHOLD_PARAM]
        assert (gate.min_trading_days, gate.min_samples) == (15, 30)
        short = evaluate_promotion(_inputs(trading_days=14))
        assert not short.promoted
        assert "window_trading_days" in short.failed_gates

    def test_prompt_window_boundaries(self) -> None:
        gate = TIERED_WINDOW_GATES[ExperimentKind.PROMPT]
        assert (gate.min_trading_days, gate.min_samples) == (20, 15)

    def test_strategy_code_keeps_45_days(self) -> None:
        gate = TIERED_WINDOW_GATES[ExperimentKind.STRATEGY_CODE]
        assert gate.min_trading_days == 45

    def test_small_sample_rejected(self) -> None:
        decision = evaluate_promotion(_inputs(sample_count=29))
        assert not decision.promoted
        assert "window_sample_count" in decision.failed_gates


class TestSignificanceGates:
    def test_noisy_excess_fails_ci(self) -> None:
        # Mean ~0, high variance — the CI straddles zero.
        noisy = tuple(
            (200.0 if i % 2 == 0 else -195.0) for i in range(20)
        )
        decision = evaluate_promotion(_inputs(daily_excess=noisy))
        assert not decision.promoted
        assert "excess_ci_significant" in decision.failed_gates

    def test_heavy_search_deflates_marginal_edge(self) -> None:
        # A modest edge that clears CI but cannot beat the expected max
        # Sharpe of 5000 random tries.
        marginal = tuple(
            6.0 + ((i * 7) % 13 - 6) * 14.0 for i in range(20)
        )
        few = evaluate_promotion(
            _inputs(daily_excess=marginal, n_trials=1)
        )
        many = evaluate_promotion(
            _inputs(daily_excess=marginal, n_trials=5000)
        )
        many_dsr = next(
            g for g in many.gates if g.name == "deflated_sharpe"
        )
        few_dsr = next(
            g for g in few.gates if g.name == "deflated_sharpe"
        )
        # Monotone: more trials can only weaken the verdict.
        assert few_dsr.passed or not many_dsr.passed
        assert not many_dsr.passed

    def test_tiny_series_fails_closed(self) -> None:
        decision = evaluate_promotion(_inputs(daily_excess=(5.0,)))
        assert not decision.promoted
        assert "excess_ci_significant" in decision.failed_gates
        assert "deflated_sharpe" in decision.failed_gates


class TestAcceptanceGate:
    def test_worse_drawdown_rejected(self) -> None:
        decision = evaluate_promotion(
            _inputs(
                challenger_report=_report(
                    pnl=9_000.0, drawdown=0.07, excess=0.03, accuracy=0.997
                ),
                champion_report=_report(pnl=4_000.0, drawdown=0.03),
            )
        )
        assert not decision.promoted
        assert "acceptance_not_degraded" in decision.failed_gates


class TestAntiGaming:
    def test_empty_portfolio_strategy_rejected(self) -> None:
        decision = evaluate_promotion(
            _inputs(
                anti_gaming=AntiGamingStats(
                    avg_exposure_ratio=0.0,
                    signal_count=40,
                    monthly_turnover=2.0,
                )
            )
        )
        assert not decision.promoted
        assert "anti_gaming_exposure" in decision.failed_gates

    def test_no_trade_strategy_rejected(self) -> None:
        decision = evaluate_promotion(
            _inputs(
                anti_gaming=AntiGamingStats(
                    avg_exposure_ratio=0.4,
                    signal_count=2,
                    monthly_turnover=0.01,
                )
            )
        )
        assert not decision.promoted
        assert "anti_gaming_signal_count" in decision.failed_gates
        assert "anti_gaming_turnover_band" in decision.failed_gates


class TestOracleGate:
    def test_divergent_oracle_hard_rejects(self) -> None:
        decision = evaluate_promotion(
            _inputs(oracle_verdict=OracleVerdict.DIVERGENT)
        )
        assert not decision.promoted
        assert "oracle_not_divergent" in decision.failed_gates

    def test_unavailable_oracle_passes_but_flagged(self) -> None:
        decision = evaluate_promotion(
            _inputs(oracle_verdict=OracleVerdict.ORACLE_UNAVAILABLE)
        )
        assert decision.promoted
        assert decision.oracle_cross_checked is False
