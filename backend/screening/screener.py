"""Full-market quant screener (Phase L-002).

The Line-1 entry point of the 2026-05-24 two-line rearchitecture: read a
point-in-time market snapshot (``backend.marketdata_snapshot``), apply the
four exclusion rules + board whitelist + ST + forbidden-board bans as a
**hard fail-closed pre-filter**, compute an Alpha158 factor subset, rank
cross-sectionally, and truncate to a fixed top-N. The output is a small
deterministic candidate shortlist that the LLM agents (Phase M) debate —
**the LLM never sees the full market and never widens the shortlist**
(P0-9-amendment-2026-05-24 §2.3 / R0 §8).

Determinism + reproducibility: the screen runs purely on the snapshot's
raw bytes and a pinned feature-code version, and it writes a
``SignalInputManifest`` recording every consumed row so an offline replay
reproduces the exact shortlist. No LLM, no network, no
``backend.{llm,agents,mirofish}`` import (redline-check ``[L-002]`` + the
module-contract test enforce the closure).

Input contract — the snapshot payload is a UTF-8 CSV "market frame", one
row per code (so each row maps to one ``ConsumedRow`` for clean lineage):

    ts_code,name,listed_trading_days,closes,amounts

* ``ts_code``            — Tushare code, e.g. ``600519.SH``
* ``name``              — display name (ST markers detected from it)
* ``listed_trading_days`` — trading days since listing (int)
* ``closes``            — ``|``-separated daily closes, oldest→newest
* ``amounts``           — ``|``-separated daily traded amount in ¥, oldest→newest

The orchestration layer assembles this frame from Tushare
``daily`` / ``daily_basic`` / ``stock_basic`` before calling the screener;
the screener itself only reads bytes in and is fully testable on fixtures.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import structlog

# backend.data is a legitimate dependency of this Line-1 module (board
# classification / ST detection); screening is NOT a Phase X module. The
# per-line noqa keeps the global TID251 ban ACTIVE for backend.{llm,agents,
# mirofish} (this module's own red line), so those would still fail ruff.
from backend.data.stock_metadata import (  # noqa: TID251
    Board,
    ForbiddenCodeError,
    UnknownCodeError,
    classify_board,
    is_st_name,
)
from backend.marketdata_snapshot import (
    MarketDataSnapshot,
    SignalInputManifest,
    build_consumed_row,
)
from backend.screening.factors import (
    MOMENTUM_WINDOW,
    FactorVector,
    compute_factors,
)
from backend.services.universe_policy import BOARD_WHITELIST, ExclusionRules

log = structlog.get_logger(component="screening.screener")

# Pinned feature-code version — bump when factor maths changes so a stale
# replay manifest fails closed instead of silently recomputing differently.
FEATURE_CODE_VERSION: str = "screening.factors/v1"

# A code needs at least this many daily closes for every 20-day factor to
# be defined (20-day momentum needs close[-21]). Fewer bars → fail-closed
# ``insufficient_history`` exclusion, so every survivor is fully scorable.
MIN_HISTORY_BARS: int = MOMENTUM_WINDOW + 1  # 21

# Fixed top-N cap (P0-9-amendment-2026-05-24 §2.3 ~50-100). Over-cap is
# narrowed by a deterministic tie-breaker — never by asking the LLM.
DEFAULT_TOP_N_CAP: int = 100

# Composite-score weights over cross-sectional percentile ranks. Locked
# constants (runtime-immutable); higher composite = more attractive.
# volatility is inverted (lower vol ranks higher). RSI stays informational
# (weight 0) — it is surfaced in the factor vector but not scored, to keep
# the ranking a transparent momentum/trend/liquidity blend.
FACTOR_WEIGHTS: dict[str, float] = {
    "momentum_20d": 0.40,
    "ma_ratio_5_20": 0.25,
    "volatility_20d": 0.20,  # inverted below
    "avg_amount_20d": 0.15,
}


class ScreeningError(RuntimeError):
    """Raised on a structurally invalid snapshot (bad encoding / header)."""


class ExclusionReason(StrEnum):
    """Fail-closed exclusion reasons (superset of the Builder sub-reasons).

    The first four mirror :data:`backend.services.instruction_plan_builder
    .WATCHLIST_SUB_REASONS` so the same rejection cause is named
    identically in the screener pre-filter and the Builder last-line
    defense. ``malformed_row`` / ``insufficient_history`` are screener-only
    structural fail-closed reasons.
    """

    MALFORMED_ROW = "malformed_row"
    FORBIDDEN_BOARD = "forbidden_board"
    UNKNOWN_CODE = "unknown_code"
    BOARD_NOT_WHITELISTED = "board_not_whitelisted"
    IS_ST = "is_st"
    IPO_TOO_NEW = "ipo_too_new"
    SUB_NEW_TOO_NEW = "sub_new_too_new"
    INSUFFICIENT_HISTORY = "insufficient_history"
    LIQUIDITY_TOO_LOW = "liquidity_too_low"
    PRICE_TOO_HIGH = "price_too_high"
    UNSCORABLE_FACTOR = "unscorable_factor"


@dataclass(frozen=True)
class _ParsedRow:
    """Internal: one successfully-parsed CSV data row + its raw bytes."""

    code: str  # 6-digit (suffix stripped)
    name: str
    listed_trading_days: int | None
    closes: tuple[float, ...]
    amounts: tuple[float, ...]
    row_key: str
    row_bytes: bytes


@dataclass(frozen=True)
class CandidateRow:
    """One surviving, ranked candidate in the shortlist."""

    code: str
    name: str
    board: Board
    score: float
    last_price: float
    factors: FactorVector


@dataclass(frozen=True)
class ExcludedRow:
    """One excluded code + the first-match fail-closed reason."""

    code: str
    reason: ExclusionReason


@dataclass(frozen=True)
class ScreenResult:
    """Deterministic screen output + its reproducibility manifest."""

    candidates: tuple[CandidateRow, ...]
    excluded: tuple[ExcludedRow, ...]
    manifest: SignalInputManifest
    universe_size: int
    top_n_cap: int
    excluded_counts: dict[str, int] = field(default_factory=dict)


def _percentile_ranks(values: list[float], *, invert: bool = False) -> list[float]:
    """Cross-sectional percentile rank in [0, 1] (ties share the mean rank).

    ``invert=True`` flips the ranking so a *lower* raw value ranks higher
    (used for volatility). A single value gets 0.5 (neutral).
    """
    n = len(values)
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    # Average-rank for ties so equal factor values never break a tie by
    # input order (keeps the composite deterministic + fair).
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_idx = (i + j) / 2.0
        pct = avg_idx / (n - 1)
        for k in range(i, j + 1):
            ranks[order[k]] = (1.0 - pct) if invert else pct
        i = j + 1
    return ranks


class Screener:
    """Pure, deterministic full-market screener over one PIT snapshot."""

    def __init__(
        self,
        exclusion_rules: ExclusionRules,
        *,
        board_whitelist: frozenset[str] = BOARD_WHITELIST,
        top_n_cap: int = DEFAULT_TOP_N_CAP,
        weights: dict[str, float] | None = None,
    ) -> None:
        if top_n_cap <= 0:
            raise ValueError("top_n_cap must be positive")
        self._rules = exclusion_rules
        self._whitelist = board_whitelist
        self._top_n_cap = top_n_cap
        self._weights = dict(weights) if weights is not None else dict(FACTOR_WEIGHTS)

    def _config_hash(self) -> str:
        """Stable sha256 of the *effective* screening config.

        Recorded in the manifest so a replay can detect that a different
        whitelist / thresholds / weights / cap produced the consumed rows
        — otherwise the PIT lineage could look valid while recomputing a
        different shortlist (codex L-002 P2).
        """
        payload = {
            "feature_code_version": FEATURE_CODE_VERSION,
            "top_n_cap": self._top_n_cap,
            "min_history_bars": MIN_HISTORY_BARS,
            "board_whitelist": sorted(self._whitelist),
            "weights": {k: self._weights[k] for k in sorted(self._weights)},
            "exclusion_rules": {
                "ipo_min_trading_days": self._rules.ipo_min_trading_days,
                "sub_new_min_trading_days": self._rules.sub_new_min_trading_days,
                "min_avg_amount_20d_yuan": self._rules.min_avg_amount_20d_yuan,
                "max_unit_price_yuan": self._rules.max_unit_price_yuan,
            },
        }
        blob = json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    # -- parsing ---------------------------------------------------------

    def _parse(self, snapshot: MarketDataSnapshot) -> tuple[list[_ParsedRow], int]:
        """Parse the CSV frame; return (parsed rows, malformed count).

        Malformed rows are dropped (and counted) rather than crashing the
        screen — a single corrupt line must not lose the whole universe —
        but they never become candidates (fail-closed).
        """
        if snapshot.encoding != "csv":
            raise ScreeningError(
                f"screener requires a csv snapshot, got encoding={snapshot.encoding!r}"
            )
        text = snapshot.raw_payload.decode("utf-8")
        lines = text.splitlines()
        if not lines:
            raise ScreeningError("snapshot payload is empty")
        header = [h.strip() for h in lines[0].split(",")]
        expected = ["ts_code", "name", "listed_trading_days", "closes", "amounts"]
        if header != expected:
            raise ScreeningError(
                f"unexpected CSV header {header!r}; expected {expected!r}"
            )
        parsed_rows: list[_ParsedRow] = []
        malformed = 0
        for line in lines[1:]:
            if not line.strip():
                continue
            parsed = self._parse_line(line)
            if parsed is None:
                malformed += 1
                continue
            parsed_rows.append(parsed)

        # A duplicated code is structurally ambiguous: drop EVERY copy
        # fail-closed (not just the later one). Keeping the first copy would
        # both let an arbitrary version into the ranking and break manifest
        # replay (the CSV replay parser resolves a duplicate row_key to the
        # last row, so a first-copy hash would not reconstruct). codex L-002.
        counts: dict[str, int] = {}
        for r in parsed_rows:
            counts[r.code] = counts.get(r.code, 0) + 1
        rows = [r for r in parsed_rows if counts[r.code] == 1]
        malformed += len(parsed_rows) - len(rows)
        return rows, malformed

    @staticmethod
    def _parse_line(line: str) -> _ParsedRow | None:
        parts = line.split(",")
        if len(parts) != 5:
            return None
        ts_code, name, listed_raw, closes_raw, amounts_raw = (p.strip() for p in parts)
        code = ts_code.split(".")[0]
        if len(code) != 6 or not code.isdigit():
            return None
        listed: int | None
        try:
            listed = int(listed_raw) if listed_raw != "" else None
        except ValueError:
            listed = None
        closes = _parse_floats(closes_raw)
        amounts = _parse_floats(amounts_raw)
        if closes is None or amounts is None:
            return None
        return _ParsedRow(
            code=code,
            name=name,
            listed_trading_days=listed,
            closes=closes,
            amounts=amounts,
            row_key=ts_code,
            row_bytes=line.encode("utf-8"),
        )

    # -- exclusion -------------------------------------------------------

    def _exclude(self, row: _ParsedRow) -> ExclusionReason | None:
        """First-match fail-closed exclusion; ``None`` = passes the gate."""
        try:
            board = classify_board(row.code)
        except ForbiddenCodeError:
            return ExclusionReason.FORBIDDEN_BOARD
        except UnknownCodeError:
            return ExclusionReason.UNKNOWN_CODE
        if board.value not in self._whitelist:
            return ExclusionReason.BOARD_NOT_WHITELISTED
        if is_st_name(row.name):
            return ExclusionReason.IS_ST
        listed = row.listed_trading_days
        if listed is None or listed < self._rules.ipo_min_trading_days:
            return ExclusionReason.IPO_TOO_NEW
        if listed < self._rules.sub_new_min_trading_days:
            return ExclusionReason.SUB_NEW_TOO_NEW
        if len(row.closes) < MIN_HISTORY_BARS:
            return ExclusionReason.INSUFFICIENT_HISTORY
        factors = compute_factors(list(row.closes), list(row.amounts))
        if (
            factors.avg_amount_20d is None
            or factors.avg_amount_20d < self._rules.min_avg_amount_20d_yuan
        ):
            return ExclusionReason.LIQUIDITY_TOO_LOW
        last_price = row.closes[-1]
        if last_price <= 0 or last_price > self._rules.max_unit_price_yuan:
            return ExclusionReason.PRICE_TOO_HIGH
        # Every *scored* factor must be defined + finite or the row cannot
        # be ranked. Degenerate history (e.g. a zero/negative close making
        # momentum or the long MA undefined) survives the bar-count gate
        # but must fail closed here rather than crashing _rank (codex L-002
        # P1: float(None) would abort the whole full-market screen).
        for name in self._weights:
            value = getattr(factors, name)
            if value is None or not math.isfinite(value):
                return ExclusionReason.UNSCORABLE_FACTOR
        return None

    # -- public API ------------------------------------------------------

    def screen(self, snapshot: MarketDataSnapshot, signal_id: str) -> ScreenResult:
        """Screen ``snapshot`` into a ranked top-N shortlist + manifest.

        Deterministic: the same snapshot + signal_id always yields the
        same shortlist and the same consumed-row lineage.
        """
        rows, malformed = self._parse(snapshot)

        excluded: list[ExcludedRow] = []
        survivors: list[tuple[_ParsedRow, Board, FactorVector]] = []
        for row in rows:
            reason = self._exclude(row)
            if reason is not None:
                excluded.append(ExcludedRow(code=row.code, reason=reason))
                continue
            board = classify_board(row.code)
            factors = compute_factors(list(row.closes), list(row.amounts))
            survivors.append((row, board, factors))

        candidates = self._rank(survivors)

        # Lineage: every successfully-parsed row is a consumed input of the
        # screen decision (exclusions included), so an offline replay
        # reproduces the exact shortlist + exclusions bit-for-bit.
        consumed = tuple(
            build_consumed_row(snapshot.snapshot_id, r.row_key, r.row_bytes)
            for r in rows
        )
        manifest = SignalInputManifest(
            signal_id=signal_id,
            created_at=datetime.now(tz=UTC),
            snapshot_ids=(snapshot.snapshot_id,),
            consumed_rows=consumed,
            feature_code_version=FEATURE_CODE_VERSION,
            config_hashes={"screening_config": self._config_hash()},
            join_filter_params={
                "top_n_cap": self._top_n_cap,
                "min_history_bars": MIN_HISTORY_BARS,
            },
        )

        counts: dict[str, int] = {}
        for ex in excluded:
            counts[ex.reason.value] = counts.get(ex.reason.value, 0) + 1
        if malformed:
            counts[ExclusionReason.MALFORMED_ROW.value] = malformed

        log.info(
            "screen_complete",
            signal_id=signal_id,
            universe_size=len(rows) + malformed,
            survivors=len(survivors),
            candidates=len(candidates),
            top_n_cap=self._top_n_cap,
        )
        return ScreenResult(
            candidates=tuple(candidates),
            excluded=tuple(excluded),
            manifest=manifest,
            universe_size=len(rows) + malformed,
            top_n_cap=self._top_n_cap,
            excluded_counts=counts,
        )

    def _rank(
        self, survivors: list[tuple[_ParsedRow, Board, FactorVector]]
    ) -> list[CandidateRow]:
        if not survivors:
            return []
        # Cross-sectional percentile rank per scored factor (volatility
        # inverted), then a fixed-weight composite. Every survivor has all
        # factors defined (insufficient_history is excluded upstream).
        pct: dict[str, list[float]] = {}
        for name in self._weights:
            raw = [float(getattr(fv, name)) for (_, _, fv) in survivors]
            pct[name] = _percentile_ranks(raw, invert=(name == "volatility_20d"))

        scored: list[CandidateRow] = []
        for idx, (row, board, fv) in enumerate(survivors):
            score = sum(self._weights[name] * pct[name][idx] for name in self._weights)
            scored.append(
                CandidateRow(
                    code=row.code,
                    name=row.name,
                    board=board,
                    score=score,
                    last_price=row.closes[-1],
                    factors=fv,
                )
            )
        # Deterministic order: score desc, then code asc tie-break. Over-cap
        # is narrowed here (never by the LLM).
        scored.sort(key=lambda c: (-c.score, c.code))
        return scored[: self._top_n_cap]


def _parse_floats(raw: str) -> tuple[float, ...] | None:
    """Parse a ``|``-separated float list; ``None`` on any malformed token.

    Non-finite tokens (``nan`` / ``inf`` / ``-inf``) are rejected as
    malformed — corrupt/missing numeric data must fail closed, never slip
    past a liquidity/price comparison (every comparison with NaN is False)
    or crash the factor maths (codex L-002 P1).
    """
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
    "DEFAULT_TOP_N_CAP",
    "FACTOR_WEIGHTS",
    "FEATURE_CODE_VERSION",
    "MIN_HISTORY_BARS",
    "CandidateRow",
    "ExcludedRow",
    "ExclusionReason",
    "ScreenResult",
    "Screener",
    "ScreeningError",
]
