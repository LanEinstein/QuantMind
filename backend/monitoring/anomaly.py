"""Line-2 position-monitoring anomaly detection (Phase N-001).

The deterministic, **zero-LLM** front of the second line (R0 §1 / §8): poll
the held positions against a point-in-time :class:`MarketDataSnapshot` and
flag price / volume anomalies with three well-understood, explainable
detectors (MVP — IsolationForest / HMM / ruptures / OFI are Phase T, added
*on demand* because monitoring needs precision over model diversity to avoid
alert fatigue; see ``backend/monitoring/CLAUDE.md`` red line 2):

* **z-score** of the latest daily return vs a trailing baseline window;
* **volume z-score** of the latest traded amount vs the same baseline;
* **EWMA control chart** — the latest close vs an exponentially-weighted
  mean/variance of the prior closes (West & Cline incremental update);
* **Bollinger band breakout** — the latest close outside ``MA ± k·σ``.

The detector is a **pure function over the snapshot bytes** + a pinned
feature-code version, mirroring :mod:`backend.screening.factors` so an
offline replay reproduces the exact anomaly verdicts (R0 §3 PIT contract).
It reads the SAME canonical CSV market-frame the Line-1 screener consumes
(``ts_code,name,listed_trading_days,closes,amounts``) so both lines share
one snapshot per ``trade_date``; only the held-position rows are consumed,
recorded as the :class:`SignalInputManifest` lineage.

Module red line (``backend/monitoring/CLAUDE.md`` import isolation): pure
quant — **no** ``backend.{llm,agents,mirofish}`` import. A signal here is a
deterministic observation; the SELL/ADD InstructionPlan it may trigger is
constructed downstream by ``instruction_plan_builder`` (single construction
point, R0 §4 / P0-10-amendment-2026-05-25).
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import structlog

from backend.marketdata_snapshot import (
    MarketDataSnapshot,
    SignalInputManifest,
    build_consumed_row,
)

log = structlog.get_logger(component="monitoring.anomaly")

# Pinned feature-code version — bump when the detector maths changes so a
# stale replay manifest fails closed instead of silently recomputing. The
# base (v1) covers the four MVP detectors; v2 additionally covers the T-003
# full-anomaly-stack detectors and is used ONLY when the stack is enabled, so
# the disabled path stays byte-identical to N-001 (replay manifests unchanged).
FEATURE_CODE_VERSION: str = "monitoring.anomaly/v1"
FEATURE_CODE_VERSION_FULL_STACK: str = "monitoring.anomaly/v2"

# Canonical CSV market-frame header (single source of truth with the Line-1
# screener — both lines read the same K snapshot per trade_date).
_EXPECTED_HEADER: tuple[str, ...] = (
    "ts_code",
    "name",
    "listed_trading_days",
    "closes",
    "amounts",
)


class AnomalyDetectorError(RuntimeError):
    """Raised on a structurally invalid snapshot (bad encoding / header)."""


class AnomalyKind(StrEnum):
    """Which detector fired. Surfaced verbatim in the signal + audit."""

    PRICE_ZSCORE = "price_zscore"
    VOLUME_ZSCORE = "volume_zscore"
    EWMA_DEVIATION = "ewma_deviation"
    BOLLINGER_BREAKOUT = "bollinger_breakout"
    # T-003 full-anomaly-stack kinds (env-gated; off by default).
    ISOLATION_FOREST = "isolation_forest"
    CHANGEPOINT = "changepoint"
    ROTATION = "rotation"
    """Not an anomaly-detector output — the deterministic ≤5-slot rotation SELL
    (Phase V-004) reuses this enum to tag its monitoring-class SELL intent so it
    flows through the SAME single construction point as a Line-2 SELL. NEVER a
    member of ``SELL_TRIGGER_KINDS`` (``evaluate_sell_intents`` never emits it);
    it is constructed only by the rotation runner's context provider."""


class AnomalyDirection(StrEnum):
    """Direction of the flagged move (sign of the deviation)."""

    UP = "up"
    DOWN = "down"


class SkipReason(StrEnum):
    """Why a held code produced no detector verdict (never a crash)."""

    NOT_IN_SNAPSHOT = "not_in_snapshot"
    MALFORMED_ROW = "malformed_row"
    NO_PRICE = "no_price"  # non-positive last close — halted / missing quote
    INSUFFICIENT_HISTORY = "insufficient_history"


@dataclass(frozen=True)
class AnomalyConfig:
    """Locked detector windows + thresholds (runtime-immutable).

    Conservative defaults: a 20-day baseline (mirrors the screener horizon)
    and ≥3σ thresholds keep the false-positive rate low (precision over
    recall — alert-fatigue red line). ``min_bars`` is the floor below which
    NO detector can run; individual detectors additionally self-gate.
    """

    window: int = 20
    zscore_threshold: float = 3.0
    volume_zscore_threshold: float = 3.0
    ewma_span: int = 10
    ewma_k: float = 3.0
    bollinger_k: float = 2.0
    # T-003 full anomaly stack (IsolationForest + ruptures change-point).
    # OFF by default → the detector is bit-for-bit identical to the N-001 MVP
    # (config_hash + manifest feature version unchanged). Enable per
    # P0-10-amendment-line2-2026-06-13 (owner + 45d shadow + restart).
    full_anomaly_stack: bool = False
    isoforest_contamination: float = 0.04
    isoforest_estimators: int = 128
    isoforest_random_state: int = 20260613
    ruptures_penalty: float = 4.0

    @property
    def min_bars(self) -> int:
        """Fewest closes below which NO detector can run (each detector also
        self-gates and returns ``None`` on its own insufficient history).

        The two cheapest detectors are Bollinger (needs ``window`` closes) and
        the EWMA chart (needs ``ewma_span + 2``); the floor is the smaller of
        the two so a row is NOT globally skipped just because EWMA history is
        short when ``ewma_span > window`` — price z-score / Bollinger may still
        have enough bars (codex N-001 P2).
        """
        return min(self.window, self.ewma_span + 2)


@dataclass(frozen=True)
class AnomalySignal:
    """One fired detector verdict on one held code (explainable)."""

    code: str
    kind: AnomalyKind
    direction: AnomalyDirection
    score: float  # the |z| / deviation-in-σ / band-distance magnitude
    threshold: float
    last_price: float
    detail: str


@dataclass(frozen=True)
class AnomalyScanResult:
    """Deterministic scan output + its reproducibility manifest."""

    signals: tuple[AnomalySignal, ...]
    manifest: SignalInputManifest
    scanned_codes: tuple[str, ...]
    skipped: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _ParsedRow:
    code: str
    closes: tuple[float, ...]
    amounts: tuple[float, ...]
    row_key: str
    row_bytes: bytes


# ---------------------------------------------------------------------------
# Pure detector functions (oldest → newest series; None = cannot evaluate)
# ---------------------------------------------------------------------------


def _returns(closes: tuple[float, ...]) -> list[float]:
    """Simple daily returns; a non-positive prior close yields 0.0 (a halted
    / corrupt bar must not divide by zero — the row is screened for price
    upstream)."""
    out: list[float] = []
    for prev, cur in zip(closes, closes[1:], strict=False):
        out.append((cur / prev - 1.0) if prev > 0 else 0.0)
    return out


def price_zscore(
    closes: tuple[float, ...], window: int
) -> tuple[float, AnomalyDirection] | None:
    """z-score of the latest return vs the ``window`` returns before it.

    Returns ``(z, direction)`` or ``None`` when there is insufficient
    history or a degenerate (zero-variance) baseline — a flat baseline
    cannot yield a finite z, so we fail safe rather than emit ``inf``.
    """
    rets = _returns(closes)
    if len(rets) < window + 1:
        return None
    baseline = rets[-(window + 1):-1]
    test = rets[-1]
    sigma = statistics.pstdev(baseline)
    if sigma <= 0.0:
        return None
    mu = statistics.fmean(baseline)
    z = (test - mu) / sigma
    if not math.isfinite(z):
        return None
    direction = AnomalyDirection.UP if test >= mu else AnomalyDirection.DOWN
    return abs(z), direction


def volume_zscore(
    amounts: tuple[float, ...], window: int
) -> tuple[float, AnomalyDirection] | None:
    """z-score of the latest traded amount vs the ``window`` amounts before it."""
    if len(amounts) < window + 1:
        return None
    baseline = amounts[-(window + 1):-1]
    test = amounts[-1]
    sigma = statistics.pstdev(baseline)
    if sigma <= 0.0:
        return None
    mu = statistics.fmean(baseline)
    z = (test - mu) / sigma
    if not math.isfinite(z):
        return None
    direction = AnomalyDirection.UP if test >= mu else AnomalyDirection.DOWN
    return abs(z), direction


def ewma_deviation(
    closes: tuple[float, ...], span: int, *, exclude_last: bool = True
) -> tuple[float, AnomalyDirection] | None:
    """EWMA control-chart deviation of the latest close, in σ units.

    Computes the exponentially-weighted mean + variance over the prior
    closes (West & Cline incremental update) and measures how many σ the
    latest close sits from that expectation. ``None`` on insufficient
    history or a degenerate (zero-variance) baseline.
    """
    if len(closes) < span + 2:
        return None
    base = closes[:-1] if exclude_last else closes
    alpha = 2.0 / (span + 1.0)
    mean = base[0]
    var = 0.0
    for x in base[1:]:
        diff = x - mean
        incr = alpha * diff
        mean += incr
        var = (1.0 - alpha) * (var + diff * incr)
    if var <= 0.0:
        return None
    sigma = math.sqrt(var)
    resid = closes[-1] - mean
    z = resid / sigma
    if not math.isfinite(z):
        return None
    direction = AnomalyDirection.UP if resid >= 0 else AnomalyDirection.DOWN
    return abs(z), direction


def bollinger_breakout(
    closes: tuple[float, ...], window: int, k: float
) -> tuple[float, AnomalyDirection] | None:
    """Latest close outside ``MA ± k·σ`` over the trailing ``window``.

    Returns ``(distance_in_σ, direction)`` when the close breaks out
    (``distance > k`` by construction), else ``None``. Standard Bollinger:
    the band uses the window *including* the current bar (conservative).
    """
    if len(closes) < window or window <= 0:
        return None
    w = closes[-window:]
    sigma = statistics.pstdev(w)
    if sigma <= 0.0:
        return None
    ma = statistics.fmean(w)
    last = closes[-1]
    upper = ma + k * sigma
    lower = ma - k * sigma
    if last > upper:
        return (last - ma) / sigma, AnomalyDirection.UP
    if last < lower:
        return (ma - last) / sigma, AnomalyDirection.DOWN
    return None


# ---------------------------------------------------------------------------
# T-003 full-anomaly-stack detectors (env-gated; deterministic / fail-closed).
# See P0-10-amendment-line2-2026-06-13-full-anomaly-stack.md.
# ---------------------------------------------------------------------------


def _isoforest_features(
    closes: tuple[float, ...], amounts: tuple[float, ...], window: int
) -> list[list[float]] | None:
    """Build the per-bar [return, volume-z, |return|] feature rows (or None).

    Needs ``window + 1`` return-bars (so the IsolationForest baseline has
    ``window`` rows + the latest test row). Volume is z-normalised over the
    same span; a degenerate (flat) volume baseline yields a 0.0 column rather
    than dividing by zero. Returns oldest→newest; the last row is the test bar.
    """
    rets = _returns(closes)
    if len(rets) < window + 1 or len(amounts) < window + 2:
        return None
    ret_window = rets[-(window + 1):]
    amt_window = amounts[-(window + 1):]
    amt_mu = statistics.fmean(amt_window)
    amt_sigma = statistics.pstdev(amt_window)
    rows: list[list[float]] = []
    for ret, amt in zip(ret_window, amt_window, strict=True):
        vz = (amt - amt_mu) / amt_sigma if amt_sigma > 0.0 else 0.0
        rows.append([ret, vz, abs(ret)])
    return rows


def isolation_forest_anomaly(
    closes: tuple[float, ...],
    amounts: tuple[float, ...],
    *,
    window: int,
    contamination: float,
    n_estimators: int,
    random_state: int,
) -> tuple[float, AnomalyDirection] | None:
    """Multivariate IsolationForest flag on the latest bar (deterministic).

    Fits a fixed-``random_state`` IsolationForest over the ``window``-bar PIT
    feature matrix and flags the latest bar iff it is predicted an outlier.
    ``score`` is the absolute anomaly score (``-decision_function``). Returns
    ``None`` on insufficient history, a degenerate baseline, OR if scikit-learn
    is unavailable (fail-closed — never raises, never blocks the core detectors).
    Direction is the sign of the latest return (UP if ``≥0`` else DOWN).
    """
    rows = _isoforest_features(closes, amounts, window)
    if rows is None:
        return None
    try:
        import numpy as np  # noqa: PLC0415 — lazy: keep the OFF/hot path light
        from sklearn.ensemble import (  # type: ignore[import-untyped]  # noqa: PLC0415
            IsolationForest,
        )

        x = np.asarray(rows, dtype=float)
        clf = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            random_state=random_state,
            bootstrap=False,
        )
        clf.fit(x)
        predictions = clf.predict(x)
        scores = clf.decision_function(x)
    except Exception as exc:  # noqa: BLE001 — fail-closed (missing dep / fit error)
        log.warning("isoforest_unavailable", error=str(exc))
        return None
    # Precision-first: flag the latest bar ONLY when it is both an outlier
    # (``predict == -1``) AND the single MOST anomalous point in the window
    # (``argmin`` of the decision function). The contamination parameter always
    # forces ~k points to ``-1``; requiring the latest to be the global minimum
    # avoids flagging a calm position just because it sits on the boundary.
    if predictions[-1] != -1 or int(np.argmin(scores)) != len(scores) - 1:
        return None
    magnitude = abs(float(scores[-1]))
    if not math.isfinite(magnitude):
        return None
    last_ret = rows[-1][0]
    direction = AnomalyDirection.UP if last_ret >= 0 else AnomalyDirection.DOWN
    return magnitude, direction


def ruptures_changepoint(
    closes: tuple[float, ...], *, window: int, penalty: float
) -> tuple[float, AnomalyDirection] | None:
    """Flag a structural change-point in the latest bar of the return series.

    Lazy-imports ``ruptures`` (an OPTIONAL dependency — mirrors the R-002
    backtest-oracle optional-dep pattern); when it is absent the detector
    fail-closes to ``None`` so the system runs fully on the core detectors
    until the dep is installed. Flags
    iff a detected break-point lands on the most recent bar; ``score`` is the
    absolute mean-shift across that break in σ units. Returns ``None`` on
    insufficient history / no recent break / any error (never raises).
    """
    rets = _returns(closes)
    if len(rets) < window:
        return None
    series = rets[-window:]
    try:
        import numpy as np  # noqa: PLC0415
        import ruptures  # type: ignore[import-not-found]  # noqa: PLC0415

        signal = np.asarray(series, dtype=float).reshape(-1, 1)
        # jump=1 searches EVERY bar (Pelt's default jump=5 puts candidate
        # break-points on a 5-bar grid, which the recency check below could
        # never match for the latest bar — codex T-003 P2).
        algo = ruptures.Pelt(model="rbf", min_size=2, jump=1).fit(signal)
        breaks = algo.predict(pen=penalty)
    except Exception as exc:  # noqa: BLE001 — fail-closed (missing dep / fit error)
        log.warning("ruptures_unavailable", error=str(exc))
        return None
    # ``breaks`` ends with len(series); a real break-point is any earlier index.
    interior = [b for b in breaks if 0 < b < len(series)]
    if not interior:
        return None
    last_break = interior[-1]
    # Only flag when the break is RECENT (within the final 2 bars) — a stale
    # mid-window break is not actionable for a held position (precision-first).
    if last_break < len(series) - 2:
        return None
    before = series[:last_break]
    after = series[last_break:]
    if not before or not after:
        return None
    sigma = statistics.pstdev(series)
    if sigma <= 0.0:
        return None
    shift = (statistics.fmean(after) - statistics.fmean(before)) / sigma
    if not math.isfinite(shift):
        return None
    direction = AnomalyDirection.UP if shift >= 0 else AnomalyDirection.DOWN
    return abs(shift), direction


class AnomalyDetector:
    """Pure, deterministic Line-2 anomaly scan over one PIT snapshot."""

    def __init__(self, config: AnomalyConfig | None = None) -> None:
        self._cfg = config or AnomalyConfig()

    def _feature_version(self) -> str:
        """v2 when the full stack is enabled, else the byte-identical v1."""
        return (
            FEATURE_CODE_VERSION_FULL_STACK
            if self._cfg.full_anomaly_stack
            else FEATURE_CODE_VERSION
        )

    def _config_hash(self) -> str:
        """Stable sha256 of the effective detector config (manifest lineage).

        When the full stack is DISABLED the payload is byte-identical to the
        N-001 MVP (no new keys, base feature version) so replay manifests are
        unchanged; the T-003 keys are added ONLY when the stack is enabled.
        """
        payload = {
            "feature_code_version": self._feature_version(),
            "window": self._cfg.window,
            "zscore_threshold": self._cfg.zscore_threshold,
            "volume_zscore_threshold": self._cfg.volume_zscore_threshold,
            "ewma_span": self._cfg.ewma_span,
            "ewma_k": self._cfg.ewma_k,
            "bollinger_k": self._cfg.bollinger_k,
        }
        if self._cfg.full_anomaly_stack:
            payload["full_anomaly_stack"] = True
            payload["isoforest_contamination"] = self._cfg.isoforest_contamination
            payload["isoforest_estimators"] = self._cfg.isoforest_estimators
            payload["isoforest_random_state"] = self._cfg.isoforest_random_state
            payload["ruptures_penalty"] = self._cfg.ruptures_penalty
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()

    def scan(
        self,
        snapshot: MarketDataSnapshot,
        held_codes: Iterable[str],
        signal_id: str,
    ) -> AnomalyScanResult:
        """Scan the held positions for anomalies; deterministic + replayable.

        Only the rows for ``held_codes`` are consumed (Line-2 monitors what
        we own, not the full market). The same snapshot + held set + signal_id
        always yields the same signals and the same consumed-row lineage.
        """
        held = {self._normalise_code(c) for c in held_codes}
        rows_by_code, malformed_codes = self._parse(snapshot, held)

        signals: list[AnomalySignal] = []
        scanned: list[str] = []
        skipped: dict[str, str] = {}
        consumed = []

        for code in sorted(held):
            if code in malformed_codes:
                skipped[code] = SkipReason.MALFORMED_ROW.value
                continue
            row = rows_by_code.get(code)
            if row is None:
                skipped[code] = SkipReason.NOT_IN_SNAPSHOT.value
                continue
            consumed.append(
                build_consumed_row(snapshot.snapshot_id, row.row_key, row.row_bytes)
            )
            if not row.closes or row.closes[-1] <= 0:
                skipped[code] = SkipReason.NO_PRICE.value
                continue
            if len(row.closes) < self._cfg.min_bars:
                skipped[code] = SkipReason.INSUFFICIENT_HISTORY.value
                continue
            scanned.append(code)
            signals.extend(self._detect(row))

        manifest = SignalInputManifest(
            signal_id=signal_id,
            created_at=datetime.now(tz=UTC),
            snapshot_ids=(snapshot.snapshot_id,),
            consumed_rows=tuple(consumed),
            feature_code_version=self._feature_version(),
            config_hashes={"anomaly_config": self._config_hash()},
            join_filter_params={"held_codes": sorted(held)},
        )
        log.info(
            "anomaly_scan_complete",
            signal_id=signal_id,
            held=len(held),
            scanned=len(scanned),
            signals=len(signals),
            skipped=len(skipped),
        )
        return AnomalyScanResult(
            signals=tuple(signals),
            manifest=manifest,
            scanned_codes=tuple(scanned),
            skipped=skipped,
        )

    def _detect(self, row: _ParsedRow) -> list[AnomalySignal]:
        """Run all four detectors on one held code; emit each that fires."""
        cfg = self._cfg
        last_price = row.closes[-1]
        out: list[AnomalySignal] = []

        pz = price_zscore(row.closes, cfg.window)
        if pz is not None and pz[0] > cfg.zscore_threshold:
            out.append(
                AnomalySignal(
                    code=row.code,
                    kind=AnomalyKind.PRICE_ZSCORE,
                    direction=pz[1],
                    score=round(pz[0], 4),
                    threshold=cfg.zscore_threshold,
                    last_price=last_price,
                    detail=(
                        f"price return z={pz[0]:.2f} {pz[1].value} "
                        f"> {cfg.zscore_threshold} over {cfg.window}d baseline"
                    ),
                )
            )

        vz = volume_zscore(row.amounts, cfg.window)
        if vz is not None and vz[0] > cfg.volume_zscore_threshold:
            out.append(
                AnomalySignal(
                    code=row.code,
                    kind=AnomalyKind.VOLUME_ZSCORE,
                    direction=vz[1],
                    score=round(vz[0], 4),
                    threshold=cfg.volume_zscore_threshold,
                    last_price=last_price,
                    detail=(
                        f"volume z={vz[0]:.2f} {vz[1].value} "
                        f"> {cfg.volume_zscore_threshold} over {cfg.window}d baseline"
                    ),
                )
            )

        ew = ewma_deviation(row.closes, cfg.ewma_span)
        if ew is not None and ew[0] > cfg.ewma_k:
            out.append(
                AnomalySignal(
                    code=row.code,
                    kind=AnomalyKind.EWMA_DEVIATION,
                    direction=ew[1],
                    score=round(ew[0], 4),
                    threshold=cfg.ewma_k,
                    last_price=last_price,
                    detail=(
                        f"EWMA(span={cfg.ewma_span}) deviation {ew[0]:.2f}σ "
                        f"{ew[1].value} > {cfg.ewma_k}σ"
                    ),
                )
            )

        bb = bollinger_breakout(row.closes, cfg.window, cfg.bollinger_k)
        if bb is not None:
            out.append(
                AnomalySignal(
                    code=row.code,
                    kind=AnomalyKind.BOLLINGER_BREAKOUT,
                    direction=bb[1],
                    score=round(bb[0], 4),
                    threshold=cfg.bollinger_k,
                    last_price=last_price,
                    detail=(
                        f"Bollinger breakout {bb[0]:.2f}σ {bb[1].value} "
                        f"(>{cfg.bollinger_k}σ band, {cfg.window}d)"
                    ),
                )
            )

        # T-003 full-anomaly-stack detectors — only when enabled. Each
        # self-gates / fail-closes to None (insufficient history, degenerate
        # baseline, or a missing optional dep) so an enabled stack never blocks
        # the core detectors above.
        if cfg.full_anomaly_stack:
            out.extend(self._detect_full_stack(row, last_price))
        return out

    def _detect_full_stack(
        self, row: _ParsedRow, last_price: float
    ) -> list[AnomalySignal]:
        """T-003 IsolationForest + ruptures change-point on one held code."""
        cfg = self._cfg
        out: list[AnomalySignal] = []

        iso = isolation_forest_anomaly(
            row.closes,
            row.amounts,
            window=cfg.window,
            contamination=cfg.isoforest_contamination,
            n_estimators=cfg.isoforest_estimators,
            random_state=cfg.isoforest_random_state,
        )
        if iso is not None:
            out.append(
                AnomalySignal(
                    code=row.code,
                    kind=AnomalyKind.ISOLATION_FOREST,
                    direction=iso[1],
                    score=round(iso[0], 4),
                    threshold=cfg.isoforest_contamination,
                    last_price=last_price,
                    detail=(
                        f"IsolationForest outlier score={iso[0]:.3f} "
                        f"{iso[1].value} (contamination={cfg.isoforest_contamination}, "
                        f"{cfg.window}d multivariate)"
                    ),
                )
            )

        cp = ruptures_changepoint(
            row.closes, window=cfg.window, penalty=cfg.ruptures_penalty
        )
        if cp is not None:
            out.append(
                AnomalySignal(
                    code=row.code,
                    kind=AnomalyKind.CHANGEPOINT,
                    direction=cp[1],
                    score=round(cp[0], 4),
                    threshold=cfg.ruptures_penalty,
                    last_price=last_price,
                    detail=(
                        f"ruptures change-point mean-shift {cp[0]:.2f}σ "
                        f"{cp[1].value} (pen={cfg.ruptures_penalty}, {cfg.window}d)"
                    ),
                )
            )
        return out

    # -- parsing ---------------------------------------------------------

    @staticmethod
    def _normalise_code(code: str) -> str:
        """Strip a ``.SH`` / ``.SZ`` suffix → 6-digit code (held set + rows)."""
        return code.split(".")[0].strip()

    def _parse(
        self, snapshot: MarketDataSnapshot, held: set[str]
    ) -> tuple[dict[str, _ParsedRow], set[str]]:
        """Parse only the held rows from the CSV frame.

        Returns ``(rows_by_code, malformed_codes)``. A duplicated held code
        is structurally ambiguous → marked malformed (fail-closed, mirrors
        the screener's duplicate handling).
        """
        if snapshot.encoding != "csv":
            raise AnomalyDetectorError(
                f"anomaly scan requires a csv snapshot, got "
                f"encoding={snapshot.encoding!r}"
            )
        text = snapshot.raw_payload.decode("utf-8")
        lines = text.splitlines()
        if not lines:
            raise AnomalyDetectorError("snapshot payload is empty")
        header = tuple(h.strip() for h in lines[0].split(","))
        if header != _EXPECTED_HEADER:
            raise AnomalyDetectorError(
                f"unexpected CSV header {header!r}; expected {_EXPECTED_HEADER!r}"
            )

        # First pass: count RAW occurrences of each held code across all data
        # lines, BEFORE the parse-None filter. A held code appearing more than
        # once is structurally ambiguous → fail-closed (malformed), even when
        # one copy is unparseable: the replay CsvRowParser resolves a duplicate
        # row_key to the LAST line, so scanning the valid (earlier) copy while
        # replay resolves to a later malformed copy would silently drift the
        # manifest hash. Counting raw occurrences first marks such codes
        # malformed and drops every copy (codex N-001 P2).
        raw_counts: dict[str, int] = {}
        parsed_rows: list[_ParsedRow] = []
        for line in lines[1:]:
            if not line.strip():
                continue
            code = self._normalise_code(line.split(",", 1)[0])
            if code in held:
                raw_counts[code] = raw_counts.get(code, 0) + 1
            parsed = self._parse_line(line)
            if parsed is None or parsed.code not in held:
                continue
            parsed_rows.append(parsed)

        malformed = {code for code, count in raw_counts.items() if count > 1}
        rows: dict[str, _ParsedRow] = {
            p.code: p for p in parsed_rows if p.code not in malformed
        }
        return rows, malformed

    def _parse_line(self, line: str) -> _ParsedRow | None:
        parts = line.split(",")
        if len(parts) != 5:
            return None
        ts_code, _name, _listed, closes_raw, amounts_raw = (p.strip() for p in parts)
        code = self._normalise_code(ts_code)
        if len(code) != 6 or not code.isdigit():
            return None
        closes = _parse_floats(closes_raw)
        amounts = _parse_floats(amounts_raw)
        if closes is None or amounts is None:
            return None
        return _ParsedRow(
            code=code,
            closes=closes,
            amounts=amounts,
            row_key=ts_code,
            row_bytes=line.encode("utf-8"),
        )


def _parse_floats(raw: str) -> tuple[float, ...] | None:
    """Parse a ``|``-separated float list; ``None`` on any malformed / non-finite
    token (corrupt data fails closed, never slips past a comparison)."""
    if raw == "":
        return ()
    out: list[float] = []
    for tok in raw.split("|"):
        try:
            value = float(tok)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        out.append(value)
    return tuple(out)


__all__ = [
    "FEATURE_CODE_VERSION",
    "FEATURE_CODE_VERSION_FULL_STACK",
    "AnomalyConfig",
    "AnomalyDetector",
    "AnomalyDetectorError",
    "AnomalyDirection",
    "AnomalyKind",
    "AnomalyScanResult",
    "AnomalySignal",
    "SkipReason",
    "bollinger_breakout",
    "ewma_deviation",
    "isolation_forest_anomaly",
    "price_zscore",
    "ruptures_changepoint",
    "volume_zscore",
]
