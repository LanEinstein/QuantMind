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
# stale replay manifest fails closed instead of silently recomputing.
FEATURE_CODE_VERSION: str = "monitoring.anomaly/v1"

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


class AnomalyDetector:
    """Pure, deterministic Line-2 anomaly scan over one PIT snapshot."""

    def __init__(self, config: AnomalyConfig | None = None) -> None:
        self._cfg = config or AnomalyConfig()

    def _config_hash(self) -> str:
        """Stable sha256 of the effective detector config (manifest lineage)."""
        payload = {
            "feature_code_version": FEATURE_CODE_VERSION,
            "window": self._cfg.window,
            "zscore_threshold": self._cfg.zscore_threshold,
            "volume_zscore_threshold": self._cfg.volume_zscore_threshold,
            "ewma_span": self._cfg.ewma_span,
            "ewma_k": self._cfg.ewma_k,
            "bollinger_k": self._cfg.bollinger_k,
        }
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
            feature_code_version=FEATURE_CODE_VERSION,
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
    "price_zscore",
    "volume_zscore",
]
