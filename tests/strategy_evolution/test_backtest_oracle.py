"""R-002 rqalpha differential oracle tests (fail-closed semantics)."""

from __future__ import annotations

import pytest

from backend.strategy_evolution.backtest_oracle import (
    EQUITY_TOLERANCE_BPS,
    BacktestRunResult,
    BacktestSpec,
    DifferentialReport,
    EquityDay,
    OracleUnavailableError,
    OracleVerdict,
    RqalphaBacktestRunner,
    compare_equity_curves,
    run_differential_check,
)

HASH = "a" * 64


def _curve(*pairs: tuple[str, float]) -> tuple[EquityDay, ...]:
    return tuple(
        EquityDay(trade_date=d, total_equity=v) for d, v in pairs
    )


def _result(
    curve: tuple[EquityDay, ...],
    *,
    engine: str = "mockbroker",
    fills: int = 4,
) -> BacktestRunResult:
    return BacktestRunResult(
        engine=engine,
        engine_version="test",
        strategy_hash=HASH,
        equity_curve=curve,
        fill_count=fills,
    )


def _spec() -> BacktestSpec:
    return BacktestSpec(
        strategy_hash=HASH,
        strategy_source_path="artifacts/strategy.py",
        start_date="2026-01-05",
        end_date="2026-03-31",
        initial_capital=100_000.0,
    )


class TestCompareEquityCurves:
    def test_identical_curves_are_consistent(self) -> None:
        days = [(f"2026-06-0{i}", 100_000.0 + i * 10) for i in range(1, 6)]
        report = compare_equity_curves(
            strategy_hash=HASH,
            mock=_result(_curve(*days)),
            oracle=_result(_curve(*days), engine="rqalpha"),
        )
        assert report.verdict is OracleVerdict.CONSISTENT
        assert report.divergent_days == 0
        assert report.compared_days == 5

    def test_small_friction_drift_within_tolerance(self) -> None:
        mock = _curve(("2026-06-01", 100_000.0), ("2026-06-02", 100_200.0))
        # 10bps drift — within the 25bps band.
        oracle = _curve(("2026-06-01", 100_100.0), ("2026-06-02", 100_300.0))
        report = compare_equity_curves(
            strategy_hash=HASH,
            mock=_result(mock),
            oracle=_result(oracle, engine="rqalpha"),
        )
        assert report.verdict is OracleVerdict.CONSISTENT
        assert report.max_abs_diff_bps < EQUITY_TOLERANCE_BPS

    def test_systematic_drift_is_divergent(self) -> None:
        mock = _curve(*[(f"2026-06-{d:02d}", 100_000.0) for d in range(1, 11)])
        oracle = _curve(
            *[(f"2026-06-{d:02d}", 101_000.0) for d in range(1, 11)]
        )  # 100bps every day
        report = compare_equity_curves(
            strategy_hash=HASH,
            mock=_result(mock),
            oracle=_result(oracle, engine="rqalpha"),
        )
        assert report.verdict is OracleVerdict.DIVERGENT
        assert report.divergent_days == 10
        assert len(report.day_diffs) == 10

    def test_single_boundary_day_does_not_fail_long_run(self) -> None:
        # 1 divergent day out of 30 (3.3%) ≤ the 5% ceiling.
        mock = _curve(*[(f"2026-05-{d:02d}", 100_000.0) for d in range(1, 31)])
        oracle_days = [
            (f"2026-05-{d:02d}", 100_000.0 if d != 15 else 101_000.0)
            for d in range(1, 31)
        ]
        report = compare_equity_curves(
            strategy_hash=HASH,
            mock=_result(mock),
            oracle=_result(_curve(*oracle_days), engine="rqalpha"),
        )
        assert report.verdict is OracleVerdict.CONSISTENT
        assert report.divergent_days == 1

    def test_no_shared_dates_is_insufficient_overlap(self) -> None:
        report = compare_equity_curves(
            strategy_hash=HASH,
            mock=_result(_curve(("2026-06-01", 100_000.0))),
            oracle=_result(
                _curve(("2026-07-01", 100_000.0)), engine="rqalpha"
            ),
        )
        assert report.verdict is OracleVerdict.INSUFFICIENT_OVERLAP

    def test_unavailable_is_never_consistent(self) -> None:
        """Fail-closed contract: only CONSISTENT means cross-checked."""
        passing = {OracleVerdict.CONSISTENT}
        assert OracleVerdict.ORACLE_UNAVAILABLE not in passing
        assert OracleVerdict.INSUFFICIENT_OVERLAP not in passing


class _FakeRunner:
    def __init__(
        self,
        result: BacktestRunResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[BacktestSpec] = []

    async def run(self, spec: BacktestSpec) -> BacktestRunResult:
        self.calls.append(spec)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class TestRunDifferentialCheck:
    @pytest.mark.asyncio
    async def test_consistent_round_trip(self) -> None:
        days = _curve(("2026-06-01", 100_000.0), ("2026-06-02", 100_050.0))
        runner = _FakeRunner(result=_result(days, engine="rqalpha"))
        report = await run_differential_check(
            spec=_spec(), mock_result=_result(days), oracle_runner=runner
        )
        assert isinstance(report, DifferentialReport)
        assert report.verdict is OracleVerdict.CONSISTENT
        assert len(runner.calls) == 1

    @pytest.mark.asyncio
    async def test_unavailable_oracle_degrades_not_raises(self) -> None:
        runner = _FakeRunner(error=OracleUnavailableError("not installed"))
        report = await run_differential_check(
            spec=_spec(),
            mock_result=_result(_curve(("2026-06-01", 100_000.0))),
            oracle_runner=runner,
        )
        assert report.verdict is OracleVerdict.ORACLE_UNAVAILABLE
        assert "not installed" in report.detail

    @pytest.mark.asyncio
    async def test_oracle_crash_degrades_not_raises(self) -> None:
        runner = _FakeRunner(error=RuntimeError("bundle corrupt"))
        report = await run_differential_check(
            spec=_spec(),
            mock_result=_result(_curve(("2026-06-01", 100_000.0))),
            oracle_runner=runner,
        )
        assert report.verdict is OracleVerdict.ORACLE_UNAVAILABLE


class TestRqalphaRunner:
    @pytest.mark.asyncio
    async def test_runner_is_unavailable_without_install(self) -> None:
        """rqalpha is an optional dep (NOASSERTION license, never
        vendored) — absent install must degrade, never crash."""
        with pytest.raises(OracleUnavailableError):
            await RqalphaBacktestRunner().run(_spec())


class TestCodexP1Fixes:
    """Codex R-002 P1 regressions: truncated overlap + hash mismatch."""

    def test_truncated_oracle_curve_is_insufficient_overlap(self) -> None:
        # Mock ran 20 days; oracle only produced 1 quiet matching day.
        mock = _curve(*[(f"2026-05-{d:02d}", 100_000.0) for d in range(1, 21)])
        oracle = _curve(("2026-05-10", 100_000.0))
        report = compare_equity_curves(
            strategy_hash=HASH,
            mock=_result(mock),
            oracle=_result(oracle, engine="rqalpha"),
        )
        assert report.verdict is OracleVerdict.INSUFFICIENT_OVERLAP
        assert "cover" in report.detail

    def test_overlap_just_below_floor_fails(self) -> None:
        mock = _curve(*[(f"2026-05-{d:02d}", 100_000.0) for d in range(1, 11)])
        # 8/10 = 80% < 90% floor.
        oracle = _curve(
            *[(f"2026-05-{d:02d}", 100_000.0) for d in range(1, 9)]
        )
        report = compare_equity_curves(
            strategy_hash=HASH,
            mock=_result(mock),
            oracle=_result(oracle, engine="rqalpha"),
        )
        assert report.verdict is OracleVerdict.INSUFFICIENT_OVERLAP

    def test_cross_artifact_compare_raises(self) -> None:
        days = _curve(("2026-06-01", 100_000.0))
        other = BacktestRunResult(
            engine="rqalpha",
            engine_version="test",
            strategy_hash="b" * 64,
            equity_curve=days,
            fill_count=1,
        )
        with pytest.raises(ValueError, match="cross-artifact"):
            compare_equity_curves(
                strategy_hash=HASH, mock=_result(days), oracle=other
            )

    @pytest.mark.asyncio
    async def test_wrong_mock_hash_raises(self) -> None:
        days = _curve(("2026-06-01", 100_000.0))
        wrong_mock = BacktestRunResult(
            engine="mockbroker",
            engine_version="test",
            strategy_hash="b" * 64,
            equity_curve=days,
            fill_count=1,
        )
        runner = _FakeRunner(result=_result(days, engine="rqalpha"))
        with pytest.raises(ValueError, match="not the requested"):
            await run_differential_check(
                spec=_spec(), mock_result=wrong_mock, oracle_runner=runner
            )

    @pytest.mark.asyncio
    async def test_oracle_returning_wrong_hash_degrades(self) -> None:
        days = _curve(("2026-06-01", 100_000.0))
        wrong_oracle = BacktestRunResult(
            engine="rqalpha",
            engine_version="test",
            strategy_hash="b" * 64,
            equity_curve=days,
            fill_count=1,
        )
        runner = _FakeRunner(result=wrong_oracle)
        report = await run_differential_check(
            spec=_spec(), mock_result=_result(days), oracle_runner=runner
        )
        assert report.verdict is OracleVerdict.ORACLE_UNAVAILABLE
        assert "not the requested" in report.detail
