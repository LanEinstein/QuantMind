"""rqalpha differential backtest oracle (R-002).

rqalpha is the ONLY sanctioned authoritative A-share execution backtest
(P2-2-amendment-2026-05-24 + backtest dossier §107): a **test-time
differential oracle** that cross-checks MockBroker shadow results —
NEVER a second execution truth, NEVER on the realtime path. MockBroker
stays the single mirror; a divergence here is a bug signal in one of
the two engines, surfaced for human investigation.

LICENSE (read 2026-06-12, acceptance requirement — GitHub reports
``NOASSERTION``): rqalpha is dual-licensed — Apache 2.0 for
non-commercial use, with COMMERCIAL USE FORBIDDEN without written
Ricequant authorization (public@ricequant.com). QuantMind is a personal
simulation-research system with real order placement permanently
forbidden → non-commercial, Apache 2.0 terms apply. Consequences baked
into this module:

* rqalpha is an OPTIONAL runtime dependency — never vendored, no code
  copied (the Y-001 "NOASSERTION 不抄" discipline). It is never imported
  in this (main-env) module: it runs out-of-process in an isolated venv
  (``QUANTMIND_RQALPHA_VENV_PYTHON``) whose numpy/pandas are newer than
  the main env's. An absent / non-executable venv (or any subprocess
  failure) degrades to ``ORACLE_UNAVAILABLE`` (fail-closed: unavailable
  is NOT a pass — the promotion gate must treat it as "not
  cross-checked");
* if QuantMind ever commercialises, rqalpha must be re-licensed or
  removed (tracked here so the constraint is greppable).

Realtime isolation is enforced four ways (R-002-amendment-2026-06-14):
ruff TID251 bans this package from importing the trading stack; the
redline ``[R-002]`` grep confines the string ``rqalpha`` to the
allowlist ``{backtest_oracle.py, backend/backtest/rqalpha_entry/*}``
(the venv entry, run only by subprocess, never imported);
``tests/strategy_evolution/test_module_contract.py`` AST-verifies no
main-env module imports the oracle module *or* the entry; and the
oracle never touches the realtime path (test-time / 22:00-cron only).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger(component="strategy_evolution.backtest_oracle")

DEFAULT_VENV_ENV_VAR = "QUANTMIND_RQALPHA_VENV_PYTHON"
"""Env var holding the isolated oracle-venv python path (owner-set; R-002
amendment 2026-06-14). Absent / non-executable -> ORACLE_UNAVAILABLE."""

DEFAULT_VENV_PYTHON = "/home/ps/rqalpha-smoke-venv/bin/python"
"""Fallback venv python when the env var is unset (the owner-verified path)."""

DEFAULT_SUBPROCESS_TIMEOUT_S = 180.0
"""Hard wall-clock bound for one oracle backtest. A 45-day window is
seconds-to-minutes; a hang -> kill the process group -> ORACLE_UNAVAILABLE."""

_ENTRY_MODULE = "rqalpha_entry"
"""Top-level module name the venv runs (``python -m rqalpha_entry``); the
subprocess ``PYTHONPATH`` points at ``backend/backtest`` so it resolves there
WITHOUT importing any ``backend.*`` (the venv has no backend install)."""

EQUITY_TOLERANCE_BPS = 25.0
"""Max per-day |equity diff| in basis points of the MockBroker equity
before the day counts as divergent. The two engines legitimately differ
in friction detail (slippage model, rounding); 25bp is far above any
rounding noise yet far below a wrong-fill error (a single mispriced
A-share lot moves >100bp on a ¥100k account)."""

DIVERGENT_DAY_RATIO_CEILING = 0.05
"""A run is CONSISTENT only when ≤5% of compared days diverge — a lone
boundary day (e.g. a limit-up no-fill modelled differently) does not
fail the cross-check, a systematic drift does."""

MIN_OVERLAP_RATIO = 0.9
"""Codex R-002 P1 — the shared dates must cover ≥90% of the MockBroker
curve (the authoritative shadow window). A truncated / wrong-calendar
oracle curve that overlaps on a single quiet day must NOT produce
CONSISTENT while most of the window went un-cross-checked."""


class OracleVerdict(StrEnum):
    """Differential cross-check outcome (fail-closed semantics)."""

    CONSISTENT = "consistent"
    DIVERGENT = "divergent"
    ORACLE_UNAVAILABLE = "oracle_unavailable"
    """rqalpha not installed / runner failed. NOT a pass: the promotion
    gate must treat this as "not cross-checked" (fail-closed)."""

    INSUFFICIENT_OVERLAP = "insufficient_overlap"
    """The two equity series share no comparable dates."""


class EquityDay(BaseModel):
    """One day of an equity series from either engine."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    total_equity: float = Field(gt=0.0)


class BacktestRunResult(BaseModel):
    """Engine-agnostic backtest output the differential consumes."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    engine: str = Field(min_length=1, max_length=64)
    engine_version: str = Field(min_length=1, max_length=64)
    strategy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    equity_curve: tuple[EquityDay, ...]
    fill_count: int = Field(ge=0)
    engine_fingerprint: Mapping[str, str] | None = None
    """Engine library versions (numpy/pandas/BLAS/...) captured from the run.
    The oracle venv's numpy/pandas are *newer* than the main env's, so a
    divergence could be a version artefact — pinning the fingerprint here makes
    that attributable instead of masquerading as logic drift (R-002 amendment
    §2.4). ``None`` for engines that do not report it (e.g. the MockBroker)."""


class DayDiff(BaseModel):
    """Per-day differential row."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    trade_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    mock_equity: float
    oracle_equity: float
    diff_bps: float
    divergent: bool


class DifferentialReport(BaseModel):
    """Outcome of one MockBroker-vs-oracle cross-check."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    strategy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: OracleVerdict
    compared_days: int = Field(ge=0)
    divergent_days: int = Field(ge=0)
    max_abs_diff_bps: float = Field(ge=0.0)
    fill_count_mock: int = Field(ge=0)
    fill_count_oracle: int = Field(ge=0)
    day_diffs: tuple[DayDiff, ...] = Field(default_factory=tuple)
    detail: str = Field(default="", max_length=512)


@dataclass(frozen=True)
class BacktestSpec:
    """What to backtest — engine-agnostic, content-addressed.

    ``strategy_hash`` is the LiveArtifactRegistry STRATEGY_CODE
    identifier; ``strategy_source_path`` points at the artifact the
    hash was computed over (the runner re-verifies, fail-closed).
    """

    strategy_hash: str
    strategy_source_path: str
    start_date: str
    end_date: str
    initial_capital: float


class OracleUnavailableError(RuntimeError):
    """rqalpha (or the configured oracle engine) cannot run."""


@runtime_checkable
class BacktestRunner(Protocol):
    """Injected oracle engine — production wires rqalpha, tests fake."""

    async def run(self, spec: BacktestSpec) -> BacktestRunResult: ...


def compare_equity_curves(
    *,
    strategy_hash: str,
    mock: BacktestRunResult,
    oracle: BacktestRunResult,
    tolerance_bps: float = EQUITY_TOLERANCE_BPS,
    divergent_ratio_ceiling: float = DIVERGENT_DAY_RATIO_CEILING,
) -> DifferentialReport:
    """Pure differential: per-day equity diff over the shared dates.

    Days present on only one side are excluded from the bps comparison
    (calendar disagreement is an engine-config issue, not an execution
    divergence), but the shared dates must cover ≥
    :data:`MIN_OVERLAP_RATIO` of the MockBroker curve — a truncated
    oracle run must not pass on the sliver it did compute (codex P1).

    Raises:
        ValueError: either input's ``strategy_hash`` differs from the
            requested one — comparing curves of different artifacts is
            a caller bug that must fail loud, never CONSISTENT.
    """
    for label, result in (("mock", mock), ("oracle", oracle)):
        if result.strategy_hash != strategy_hash:
            raise ValueError(
                f"{label} result is for strategy "
                f"{result.strategy_hash[:12]}, not the requested "
                f"{strategy_hash[:12]} — refusing cross-artifact compare"
            )
    mock_by_date = {d.trade_date: d.total_equity for d in mock.equity_curve}
    oracle_by_date = {
        d.trade_date: d.total_equity for d in oracle.equity_curve
    }
    shared = sorted(set(mock_by_date) & set(oracle_by_date))
    overlap_ratio = (
        len(shared) / len(mock_by_date) if mock_by_date else 0.0
    )
    if not shared or overlap_ratio < MIN_OVERLAP_RATIO:
        return DifferentialReport(
            strategy_hash=strategy_hash,
            verdict=OracleVerdict.INSUFFICIENT_OVERLAP,
            compared_days=len(shared),
            divergent_days=0,
            max_abs_diff_bps=0.0,
            fill_count_mock=mock.fill_count,
            fill_count_oracle=oracle.fill_count,
            detail=(
                f"shared dates cover {overlap_ratio:.1%} of the "
                f"MockBroker window (< {MIN_OVERLAP_RATIO:.0%}); the "
                f"cross-check did not see the full run"
            ),
        )

    diffs: list[DayDiff] = []
    for day in shared:
        mock_eq = mock_by_date[day]
        oracle_eq = oracle_by_date[day]
        diff_bps = abs(oracle_eq - mock_eq) / mock_eq * 10_000.0
        diffs.append(
            DayDiff(
                trade_date=day,
                mock_equity=mock_eq,
                oracle_equity=oracle_eq,
                diff_bps=diff_bps,
                divergent=diff_bps > tolerance_bps,
            )
        )
    divergent_days = sum(1 for d in diffs if d.divergent)
    ratio = divergent_days / len(diffs)
    verdict = (
        OracleVerdict.CONSISTENT
        if ratio <= divergent_ratio_ceiling
        else OracleVerdict.DIVERGENT
    )
    return DifferentialReport(
        strategy_hash=strategy_hash,
        verdict=verdict,
        compared_days=len(diffs),
        divergent_days=divergent_days,
        max_abs_diff_bps=max(d.diff_bps for d in diffs),
        fill_count_mock=mock.fill_count,
        fill_count_oracle=oracle.fill_count,
        day_diffs=tuple(d for d in diffs if d.divergent),
        detail=(
            f"{divergent_days}/{len(diffs)} days beyond "
            f"{tolerance_bps}bps"
        ),
    )


async def run_differential_check(
    *,
    spec: BacktestSpec,
    mock_result: BacktestRunResult,
    oracle_runner: BacktestRunner,
) -> DifferentialReport:
    """Run the oracle and compare against the MockBroker result.

    An oracle failure (engine missing, run crash) degrades to
    ``ORACLE_UNAVAILABLE`` — never a silent pass, never an exception
    that could freeze the evolution lane (X-005 decoupling). Hash
    discipline (codex P1): a ``mock_result`` for a different artifact
    is a caller bug → raise; an oracle result for a different artifact
    (cached / misconfigured runner) degrades to ORACLE_UNAVAILABLE.

    Raises:
        ValueError: ``mock_result.strategy_hash`` differs from the spec.
    """
    if mock_result.strategy_hash != spec.strategy_hash:
        raise ValueError(
            f"mock_result is for strategy "
            f"{mock_result.strategy_hash[:12]}, not the requested "
            f"{spec.strategy_hash[:12]}"
        )
    try:
        oracle_result = await oracle_runner.run(spec)
    except OracleUnavailableError as exc:
        log.warning(
            "backtest_oracle_unavailable",
            strategy_hash=spec.strategy_hash[:12],
            error=str(exc),
        )
        return DifferentialReport(
            strategy_hash=spec.strategy_hash,
            verdict=OracleVerdict.ORACLE_UNAVAILABLE,
            compared_days=0,
            divergent_days=0,
            max_abs_diff_bps=0.0,
            fill_count_mock=mock_result.fill_count,
            fill_count_oracle=0,
            detail=str(exc)[:512],
        )
    except Exception as exc:  # noqa: BLE001 — oracle is best-effort
        log.warning(
            "backtest_oracle_run_failed",
            strategy_hash=spec.strategy_hash[:12],
            error=str(exc),
        )
        return DifferentialReport(
            strategy_hash=spec.strategy_hash,
            verdict=OracleVerdict.ORACLE_UNAVAILABLE,
            compared_days=0,
            divergent_days=0,
            max_abs_diff_bps=0.0,
            fill_count_mock=mock_result.fill_count,
            fill_count_oracle=0,
            detail=f"oracle run raised: {exc}"[:512],
        )
    if oracle_result.strategy_hash != spec.strategy_hash:
        log.warning(
            "backtest_oracle_hash_mismatch",
            requested=spec.strategy_hash[:12],
            returned=oracle_result.strategy_hash[:12],
        )
        return DifferentialReport(
            strategy_hash=spec.strategy_hash,
            verdict=OracleVerdict.ORACLE_UNAVAILABLE,
            compared_days=0,
            divergent_days=0,
            max_abs_diff_bps=0.0,
            fill_count_mock=mock_result.fill_count,
            fill_count_oracle=oracle_result.fill_count,
            detail=(
                f"oracle returned a result for strategy "
                f"{oracle_result.strategy_hash[:12]}, not the requested "
                f"{spec.strategy_hash[:12]} (cached/misconfigured runner)"
            ),
        )
    return compare_equity_curves(
        strategy_hash=spec.strategy_hash,
        mock=mock_result,
        oracle=oracle_result,
    )


@dataclass(frozen=True)
class SubprocessOutcome:
    """Result of one oracle-subprocess launch (immutable)."""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool


@runtime_checkable
class SubprocessRunner(Protocol):
    """Injectable subprocess seam — production spawns the venv, tests fake."""

    async def __call__(
        self,
        *,
        argv: list[str],
        env: Mapping[str, str],
        cwd: str,
        timeout_s: float,
    ) -> SubprocessOutcome: ...


@runtime_checkable
class ExportManifestLike(Protocol):
    """What :meth:`PitExporter.export` returns — only the pinned sha is read."""

    @property
    def bars_sha256(self) -> str: ...


@runtime_checkable
class PitExporter(Protocol):
    """Injectable PIT same-source exporter (Option B).

    Writes the self-contained ``spec.json`` + ``bars.csv`` the venv entry reads
    into ``workdir`` and returns a manifest carrying the pinned ``bars_sha256``.
    Production wires ``backend.backtest.pit_export.SnapshotPitExporter`` (which
    reads the K-002 PIT store); the runner stays import-clean of ``backend.data``
    by depending on this Protocol only.
    """

    def export(self, spec: BacktestSpec, workdir: Path) -> ExportManifestLike: ...


async def _default_subprocess_runner(
    *,
    argv: list[str],
    env: Mapping[str, str],
    cwd: str,
    timeout_s: float,
) -> SubprocessOutcome:
    """Spawn the venv subprocess in its own session; kill the group on timeout.

    ``start_new_session=True`` puts the child in a fresh process group so a
    hung rqalpha (and any thread it spawned) is killed wholesale via
    :func:`os.killpg` — a bare ``proc.kill()`` would orphan grandchildren.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=dict(env),
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except TimeoutError:
            pass
        return SubprocessOutcome(
            returncode=None, stdout=b"", stderr=b"", timed_out=True
        )
    return SubprocessOutcome(
        returncode=proc.returncode,
        stdout=stdout or b"",
        stderr=stderr or b"",
        timed_out=False,
    )


def _default_entry_pythonpath() -> Path:
    """``<repo>/backend/backtest`` — where the venv resolves ``rqalpha_entry``."""
    # this file: <repo>/backend/strategy_evolution/backtest_oracle.py
    return Path(__file__).resolve().parents[1] / "backtest"


class RqalphaBacktestRunner:
    """Production rqalpha adapter — runs rqalpha in an isolated venv subprocess.

    R-002-amendment-2026-06-14: rqalpha's numpy/pandas are newer than the main
    env's, so it MUST run out-of-process in its own venv
    (``QUANTMIND_RQALPHA_VENV_PYTHON``). :meth:`run` exports the same-source PIT
    data (Option B, via the injected :class:`PitExporter`), launches
    ``python -m rqalpha_entry`` in the venv, and parses ``result.json``. Every
    failure — venv absent / non-executable, export failure, timeout, non-zero
    exit, missing / corrupt / mis-hashed result — raises
    :class:`OracleUnavailableError`, so the differential degrades to
    ``ORACLE_UNAVAILABLE`` (fail-closed: unavailable is never a pass). rqalpha
    stays an OPTIONAL, never-vendored dependency (NOASSERTION license).
    """

    ENGINE = "rqalpha"

    def __init__(
        self,
        *,
        exporter: PitExporter,
        venv_python: str | None = None,
        timeout_s: float = DEFAULT_SUBPROCESS_TIMEOUT_S,
        subprocess_runner: SubprocessRunner | None = None,
        entry_pythonpath: Path | None = None,
    ) -> None:
        self._exporter = exporter
        self._venv_python = venv_python or os.environ.get(
            DEFAULT_VENV_ENV_VAR, DEFAULT_VENV_PYTHON
        )
        self._timeout_s = timeout_s
        self._run_subprocess = subprocess_runner or _default_subprocess_runner
        self._pythonpath = entry_pythonpath or _default_entry_pythonpath()

    async def run(self, spec: BacktestSpec) -> BacktestRunResult:
        venv = Path(self._venv_python)
        if not venv.exists() or not os.access(venv, os.X_OK):
            raise OracleUnavailableError(
                f"oracle venv python not found / not executable: {venv} "
                f"(set {DEFAULT_VENV_ENV_VAR}); rqalpha is an optional "
                "never-vendored dependency"
            )
        with tempfile.TemporaryDirectory(prefix="qm_rqalpha_") as tmp:
            workdir = Path(tmp)
            try:
                manifest = self._exporter.export(spec, workdir)
            except Exception as exc:  # noqa: BLE001 - export failure => unavailable
                raise OracleUnavailableError(
                    f"PIT same-source export failed: {exc}"
                ) from exc

            outcome = await self._run_subprocess(
                argv=[
                    self._venv_python,
                    "-m",
                    _ENTRY_MODULE,
                    "--workdir",
                    str(workdir),
                ],
                env=self._subprocess_env(),
                cwd=str(workdir),
                timeout_s=self._timeout_s,
            )
            if outcome.timed_out:
                raise OracleUnavailableError(
                    f"oracle subprocess exceeded {self._timeout_s}s "
                    "(process group killed)"
                )
            if outcome.returncode != 0:
                raise OracleUnavailableError(
                    f"oracle subprocess exit {outcome.returncode}: "
                    f"{_tail(outcome.stderr)}"
                )
            return self._parse_result(workdir, spec, manifest.bars_sha256)

    # -- internal ------------------------------------------------------
    def _subprocess_env(self) -> dict[str, str]:
        """Clean env: pin PYTHONPATH to the entry parent + single-thread BLAS.

        ``PYTHONPATH`` is *replaced* (not extended) so the venv never resolves
        ``backend.*``; ``OMP/OPENBLAS/MKL_NUM_THREADS=1`` makes the run
        deterministic and keeps the version fingerprint comparable (amendment
        §2.4)."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self._pythonpath)
        env["OMP_NUM_THREADS"] = "1"
        env["OPENBLAS_NUM_THREADS"] = "1"
        env["MKL_NUM_THREADS"] = "1"
        return env

    def _parse_result(
        self, workdir: Path, spec: BacktestSpec, expected_bars_sha256: str
    ) -> BacktestRunResult:
        result_path = workdir / "result.json"
        checksum_path = workdir / "result.json.sha256"
        if not result_path.exists():
            raise OracleUnavailableError(
                "oracle subprocess produced no result.json"
            )
        # The sidecar is REQUIRED (the entry writes it before publishing
        # result.json). A present result.json without its sidecar means a
        # half-written / crashed run -> fail-closed, never adopt unverified.
        if not checksum_path.exists():
            raise OracleUnavailableError(
                "oracle result.json has no checksum sidecar "
                "(half-written / crashed run)"
            )
        raw = result_path.read_bytes()
        expected = checksum_path.read_text(encoding="utf-8").strip()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise OracleUnavailableError(
                f"result.json checksum {actual[:12]} != sidecar "
                f"{expected[:12]} (half-written / corrupt)"
            )
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OracleUnavailableError(
                f"oracle result.json is not valid JSON: {exc}"
            ) from exc
        try:
            result = self._to_run_result(doc)
        except Exception as exc:  # noqa: BLE001 - malformed result => unavailable
            raise OracleUnavailableError(
                f"oracle result.json failed validation: {exc}"
            ) from exc
        if result.engine != self.ENGINE:
            raise OracleUnavailableError(
                f"oracle result reports engine {result.engine!r}, not "
                f"{self.ENGINE!r} — a cross-engine differential must come "
                "from the rqalpha subprocess (refusing a mislabelled result)"
            )
        if result.strategy_hash != spec.strategy_hash:
            raise OracleUnavailableError(
                f"oracle ran strategy {result.strategy_hash[:12]}, not the "
                f"requested {spec.strategy_hash[:12]}"
            )
        if doc.get("bars_sha256") != expected_bars_sha256:
            raise OracleUnavailableError(
                "oracle ran against different bars than were exported "
                f"({str(doc.get('bars_sha256'))[:12]} != "
                f"{expected_bars_sha256[:12]}) — PIT same-source broken"
            )
        log.info(
            "backtest_oracle_run_ok",
            strategy_hash=spec.strategy_hash[:12],
            fill_count=result.fill_count,
            compared_days=len(result.equity_curve),
            engine_fingerprint=dict(result.engine_fingerprint or {}),
        )
        return result

    @staticmethod
    def _to_run_result(doc: Mapping[str, Any]) -> BacktestRunResult:
        curve = tuple(
            EquityDay(
                trade_date=str(row["trade_date"]),
                total_equity=float(row["total_equity"]),
            )
            for row in doc["equity_curve"]
        )
        fingerprint = doc.get("env_fingerprint")
        return BacktestRunResult(
            engine=str(doc["engine"]),
            engine_version=str(doc["engine_version"]),
            strategy_hash=str(doc["strategy_hash"]),
            equity_curve=curve,
            fill_count=int(doc["fill_count"]),
            engine_fingerprint=(
                {str(k): str(v) for k, v in fingerprint.items()}
                if isinstance(fingerprint, Mapping)
                else None
            ),
        )


def _tail(data: bytes, *, limit: int = 400) -> str:
    """Last ``limit`` chars of a stderr blob for an error message."""
    text = data.decode("utf-8", errors="replace")
    return text[-limit:]


__all__ = [
    "DEFAULT_SUBPROCESS_TIMEOUT_S",
    "DEFAULT_VENV_ENV_VAR",
    "DEFAULT_VENV_PYTHON",
    "DIVERGENT_DAY_RATIO_CEILING",
    "EQUITY_TOLERANCE_BPS",
    "MIN_OVERLAP_RATIO",
    "BacktestRunResult",
    "BacktestRunner",
    "BacktestSpec",
    "DayDiff",
    "DifferentialReport",
    "EquityDay",
    "ExportManifestLike",
    "OracleUnavailableError",
    "OracleVerdict",
    "PitExporter",
    "RqalphaBacktestRunner",
    "SubprocessOutcome",
    "SubprocessRunner",
    "compare_equity_curves",
    "run_differential_check",
]
