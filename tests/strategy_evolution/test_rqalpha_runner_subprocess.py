"""AE-002 — RqalphaBacktestRunner subprocess plumbing (fail-closed, no venv).

These exercise every failure path of the out-of-process oracle with a fake
subprocess + fake exporter (the real venv is owner-gated and exercised by the
``@pytest.mark.skipif`` integration test). The contract: every failure —
venv absent, export error, timeout, non-zero exit, missing / corrupt /
mis-hashed result — degrades to ``ORACLE_UNAVAILABLE`` (never a silent pass),
and stdout pollution cannot corrupt the file-based JSON result.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from backend.strategy_evolution.backtest_oracle import (
    BacktestSpec,
    OracleUnavailableError,
    OracleVerdict,
    RqalphaBacktestRunner,
    SubprocessOutcome,
    run_differential_check,
)

HASH = "a" * 64
BARS_SHA = "f" * 64
EXECUTABLE_VENV = sys.executable  # exists + executable -> venv check passes


def _spec() -> BacktestSpec:
    return BacktestSpec(
        strategy_hash=HASH,
        strategy_source_path="strategy.json",
        start_date="20230104",
        end_date="20230106",
        initial_capital=1_000_000.0,
    )


class _FakeManifest:
    bars_sha256 = BARS_SHA


class _FakeExporter:
    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self._raise = raise_exc
        self.calls: list[Path] = []

    def export(self, spec: BacktestSpec, workdir: Path) -> _FakeManifest:
        self.calls.append(workdir)
        if self._raise is not None:
            raise self._raise
        return _FakeManifest()


def _valid_result(*, strategy_hash: str = HASH, bars_sha: str = BARS_SHA) -> dict:
    return {
        "schema_version": 1,
        "engine": "rqalpha",
        "engine_version": "6.1.5",
        "strategy_hash": strategy_hash,
        "bars_sha256": bars_sha,
        "equity_curve": [
            {"trade_date": "2023-01-04", "total_equity": 1_000_000.0},
            {"trade_date": "2023-01-05", "total_equity": 1_001_000.0},
        ],
        "fill_count": 2,
        "env_fingerprint": {"numpy": "2.4.6", "pandas": "2.3.3"},
    }


def _fake_runner(
    *,
    returncode: int | None = 0,
    timed_out: bool = False,
    result: dict | None = None,
    write_result: bool = True,
    write_checksum: bool = True,
    bad_checksum: bool = False,
    raw_override: bytes | None = None,
    stdout: bytes = b"",
):
    async def _runner(*, argv, env, cwd, timeout_s) -> SubprocessOutcome:
        if not timed_out and write_result:
            workdir = Path(cwd)
            if raw_override is not None:
                raw = raw_override
            else:
                payload = result if result is not None else _valid_result()
                raw = json.dumps(payload).encode("utf-8")
            (workdir / "result.json").write_bytes(raw)
            if write_checksum:
                digest = (
                    "deadbeef" if bad_checksum else hashlib.sha256(raw).hexdigest()
                )
                (workdir / "result.json.sha256").write_text(digest, encoding="utf-8")
        return SubprocessOutcome(
            returncode=returncode, stdout=stdout, stderr=b"boom", timed_out=timed_out
        )

    return _runner


def _make_runner(**kw) -> RqalphaBacktestRunner:
    return RqalphaBacktestRunner(
        exporter=kw.pop("exporter", _FakeExporter()),
        venv_python=kw.pop("venv_python", EXECUTABLE_VENV),
        subprocess_runner=kw.pop("subprocess_runner", _fake_runner()),
        timeout_s=kw.pop("timeout_s", 5.0),
    )


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_valid_result_parsed(self) -> None:
        result = await _make_runner().run(_spec())
        assert result.engine == "rqalpha"
        assert result.strategy_hash == HASH
        assert result.fill_count == 2
        assert len(result.equity_curve) == 2
        assert result.engine_fingerprint == {"numpy": "2.4.6", "pandas": "2.3.3"}

    @pytest.mark.asyncio
    async def test_stdout_pollution_does_not_break_json(self) -> None:
        runner = _make_runner(
            subprocess_runner=_fake_runner(stdout=b"WARNING junk not json\n")
        )
        result = await runner.run(_spec())
        assert result.fill_count == 2

    @pytest.mark.asyncio
    async def test_engine_fingerprint_surfaced(self) -> None:
        result = await _make_runner().run(_spec())
        assert result.engine_fingerprint == {"numpy": "2.4.6", "pandas": "2.3.3"}

    @pytest.mark.asyncio
    async def test_round_trip_through_differential_is_consistent(self) -> None:
        from backend.strategy_evolution.backtest_oracle import (
            BacktestRunResult,
            EquityDay,
        )

        mock = BacktestRunResult(
            engine="mockbroker",
            engine_version="test",
            strategy_hash=HASH,
            equity_curve=(
                EquityDay(trade_date="2023-01-04", total_equity=1_000_000.0),
                EquityDay(trade_date="2023-01-05", total_equity=1_001_000.0),
            ),
            fill_count=2,
        )
        report = await run_differential_check(
            spec=_spec(), mock_result=mock, oracle_runner=_make_runner()
        )
        assert report.verdict is OracleVerdict.CONSISTENT


class TestFailClosed:
    @pytest.mark.asyncio
    async def test_venv_missing_degrades(self) -> None:
        runner = _make_runner(venv_python="/nonexistent/python")
        with pytest.raises(OracleUnavailableError, match="venv"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_export_failure_degrades(self) -> None:
        runner = _make_runner(
            exporter=_FakeExporter(raise_exc=RuntimeError("no PIT data"))
        )
        with pytest.raises(OracleUnavailableError, match="export failed"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_timeout_degrades(self) -> None:
        runner = _make_runner(subprocess_runner=_fake_runner(timed_out=True))
        with pytest.raises(OracleUnavailableError, match="exceeded"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_nonzero_exit_degrades(self) -> None:
        runner = _make_runner(subprocess_runner=_fake_runner(returncode=1))
        with pytest.raises(OracleUnavailableError, match="exit 1"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_missing_result_degrades(self) -> None:
        runner = _make_runner(subprocess_runner=_fake_runner(write_result=False))
        with pytest.raises(OracleUnavailableError, match="no result.json"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_checksum_mismatch_degrades(self) -> None:
        runner = _make_runner(subprocess_runner=_fake_runner(bad_checksum=True))
        with pytest.raises(OracleUnavailableError, match="checksum"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_missing_checksum_sidecar_degrades(self) -> None:
        # A result.json without its sidecar = half-written / crashed run; the
        # integrity guard must NOT self-disable -> fail-closed (codex review).
        runner = _make_runner(subprocess_runner=_fake_runner(write_checksum=False))
        with pytest.raises(OracleUnavailableError, match="no checksum sidecar"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_mislabelled_engine_degrades(self) -> None:
        # A result with valid hashes but engine != "rqalpha" must not be
        # accepted — a cross-engine differential must come from rqalpha.
        bad = _valid_result()
        bad["engine"] = "mockbroker"
        runner = _make_runner(subprocess_runner=_fake_runner(result=bad))
        with pytest.raises(OracleUnavailableError, match="engine"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_malformed_json_degrades(self) -> None:
        runner = _make_runner(
            subprocess_runner=_fake_runner(raw_override=b"not json{{{")
        )
        with pytest.raises(OracleUnavailableError, match="not valid JSON"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_strategy_hash_mismatch_degrades(self) -> None:
        runner = _make_runner(
            subprocess_runner=_fake_runner(result=_valid_result(strategy_hash="b" * 64))
        )
        with pytest.raises(OracleUnavailableError, match="not the requested"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_bars_sha_mismatch_degrades(self) -> None:
        runner = _make_runner(
            subprocess_runner=_fake_runner(result=_valid_result(bars_sha="0" * 64))
        )
        with pytest.raises(OracleUnavailableError, match="different bars"):
            await runner.run(_spec())

    @pytest.mark.asyncio
    async def test_every_failure_degrades_via_differential_not_raises(self) -> None:
        """run_differential_check turns the runner's UNAVAILABLE into a verdict,
        never an exception that could freeze the evolution lane."""
        from backend.strategy_evolution.backtest_oracle import (
            BacktestRunResult,
            EquityDay,
        )

        mock = BacktestRunResult(
            engine="mockbroker",
            engine_version="test",
            strategy_hash=HASH,
            equity_curve=(
                EquityDay(trade_date="2023-01-04", total_equity=1_000_000.0),
            ),
            fill_count=1,
        )
        runner = _make_runner(venv_python="/nonexistent/python")
        report = await run_differential_check(
            spec=_spec(), mock_result=mock, oracle_runner=runner
        )
        assert report.verdict is OracleVerdict.ORACLE_UNAVAILABLE
