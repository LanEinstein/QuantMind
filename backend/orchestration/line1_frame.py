"""Line-1 market-frame assembler (Phase U-B1).

Turns real Tushare full-market pulls into the canonical CSV "market frame"
that :class:`backend.screening.screener.Screener` consumes — while keeping
the point-in-time provenance contract (R0 §3) intact.

Provenance design (Codex round-1 P0 #2): the **raw** Tushare payload for
every endpoint × trade_date is persisted as its own
:class:`~backend.marketdata_snapshot.MarketDataSnapshot` (raw bytes +
checksum). The assembled screener CSV is then stored as a **derived child**
snapshot whose ``metadata.parent_snapshot_ids`` link back to those raw
payloads. Storing only the derived CSV would violate R0 §3 — the derived
frame cannot reconstruct a vendor restatement, but the preserved raw bytes
can. Re-runs for the same ``as_of`` **reuse** the existing snapshots (the
store enforces unique ``(vendor, endpoint, trade_date, version)``), so the
assembler is idempotent.

T-1 EOD (Codex P0 #3): Tushare ``daily`` lands after the close, so Line-1
runs at 09:00 against the **previous** trading day's frame. ``daily.amount``
is in 千元 (thousand yuan); it is multiplied by 1000 so the frame's amounts
are in yuan, matching the screener's ``min_avg_amount_20d_yuan`` threshold.

Scope (U-B1): the **stock** universe (``daily`` + ``stock_basic``). ETF
coverage (``fund_daily`` + ``fund_basic``) is a documented follow-on; ETFs
simply do not appear as Line-1 candidates until it lands. ``daily_basic`` is
fetched + snapshotted for PIT completeness (future valuation factors) but is
not consumed by the current screener frame.
"""

from __future__ import annotations

import datetime as dt
import io
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

import pandas as pd
import structlog

# Orchestration is the composition root — importing backend.data and
# backend.marketdata_snapshot here is legitimate (it wires them together).
# It imports NO backend.{llm,agents,agents_team,mirofish}; the assembler is
# pure-quant data plumbing with zero LLM involvement.
from backend.data.trading_calendar import (  # noqa: TID251
    count_trading_days,
    is_trading_day,
    prev_trading_day,
)
from backend.marketdata_snapshot import MarketDataSnapshot
from backend.marketdata_snapshot.store import SnapshotStore

log = structlog.get_logger(component="orchestration.line1_frame")

DERIVED_VENDOR = "quantmind"
"""Vendor tag for the assembled (non-raw) screener frame snapshot."""
DERIVED_ENDPOINT = "line1_screener_frame"
"""Endpoint tag for the derived screener-frame snapshot."""
DERIVED_KIND = "derived_screener_frame"
"""``metadata.kind`` marker distinguishing the derived child from raw pulls."""

SCREENER_FRAME_HEADER = "ts_code,name,listed_trading_days,closes,amounts"
"""Canonical CSV header the screener requires (``screener.py`` input contract)."""

_RAW_VENDOR = "tushare"
DEFAULT_HISTORY_DAYS = 30
"""Trading days of daily history per run. > MIN_HISTORY_BARS (21) so every
fully-covered code is scorable by the screener's 20-day factors."""

_AMOUNT_THOUSAND_YUAN_TO_YUAN = 1000.0

_DAILY_REQUIRED_COLS = frozenset({"ts_code", "close", "amount"})
"""Columns a ``daily`` pull must carry; absence/empty = fail-closed."""
_ROSTER_REQUIRED_COLS = frozenset({"ts_code", "name", "list_date"})
"""Columns ``stock_basic`` must carry; absence/empty = fail-closed."""


class Line1FrameError(RuntimeError):
    """Raised when a pull is too degraded to build a trustworthy frame.

    Fail-closed (R0 §3 spirit): an empty / columnless full-market pull must
    surface loudly rather than silently compress a trade date out of every
    series — a 29-of-30-day frame still clears the screener's 21-bar minimum
    and would emit candidates from a partial pull.
    """


@runtime_checkable
class FrameDataSource(Protocol):
    """Narrow async view of the Tushare client the assembler needs.

    Declared as a Protocol so unit tests inject an in-memory fake without a
    live token or network (the real :class:`backend.data.tushare_client.
    TushareClient` satisfies it).
    """

    async def daily(self, trade_date: str) -> pd.DataFrame: ...
    async def daily_basic(self, trade_date: str) -> pd.DataFrame: ...
    async def stock_basic(self) -> pd.DataFrame: ...


@dataclass(frozen=True)
class Line1FrameResult:
    """Outcome of one assemble run.

    ``frame_snapshot`` is the derived CSV snapshot ready for
    ``Screener.screen``; ``raw_snapshot_ids`` are the parent raw payloads it
    was assembled from (the lineage recorded in the frame's metadata);
    ``trade_dates`` is the consumed window oldest→newest; ``code_count`` is
    the number of rows in the frame.
    """

    frame_snapshot: MarketDataSnapshot
    raw_snapshot_ids: tuple[UUID, ...]
    trade_dates: tuple[str, ...]
    code_count: int


class Line1FrameAssembler:
    """Assemble a screener-ready PIT market frame from Tushare pulls."""

    def __init__(
        self,
        *,
        client: FrameDataSource,
        store: SnapshotStore,
        history_days: int = DEFAULT_HISTORY_DAYS,
        now_utc: Callable[[], datetime] | None = None,
    ) -> None:
        if history_days < 1:
            raise ValueError("history_days must be >= 1")
        self._client = client
        self._store = store
        self._history_days = history_days
        self._now_utc = now_utc or (lambda: datetime.now(UTC))

    async def assemble(
        self, *, as_of_date: dt.date, signal_id: str
    ) -> Line1FrameResult:
        """Build (or reuse) the screener frame for ``as_of_date`` (T-1 EOD).

        Raises:
            ValueError: ``as_of_date`` is not a trading day (the caller must
                pass the previous EOD trading day; fail-closed rather than
                snapshot an empty non-trading-day pull).
        """
        if not is_trading_day(as_of_date):
            raise ValueError(
                f"as_of_date {as_of_date.isoformat()} is not a trading day; "
                "pass the previous EOD trading day (T-1)"
            )
        as_of_str = as_of_date.strftime("%Y%m%d")
        trade_dates = self._trading_window(as_of_date)

        raw_ids: list[UUID] = []
        daily_by_date: dict[str, pd.DataFrame] = {}
        for td in trade_dates:
            daily_snap, daily_df = await self._fetch_or_load(
                endpoint="daily",
                trade_date=td,
                fetch=lambda td=td: self._client.daily(td),
                required_cols=_DAILY_REQUIRED_COLS,
            )
            raw_ids.append(daily_snap.snapshot_id)
            daily_by_date[td] = daily_df
            # daily_basic snapshotted for PIT completeness (future factors);
            # not consumed by the current screener frame, so not validated.
            basic_snap, _ = await self._fetch_or_load(
                endpoint="daily_basic",
                trade_date=td,
                fetch=lambda td=td: self._client.daily_basic(td),
            )
            raw_ids.append(basic_snap.snapshot_id)

        roster_snap, roster_df = await self._fetch_or_load(
            endpoint="stock_basic",
            trade_date=as_of_str,
            fetch=self._client.stock_basic,
            required_cols=_ROSTER_REQUIRED_COLS,
        )
        raw_ids.append(roster_snap.snapshot_id)

        frame_bytes, code_count = self._build_frame(
            trade_dates=trade_dates,
            as_of_date=as_of_date,
            daily_by_date=daily_by_date,
            roster_df=roster_df,
        )
        frame_snap = self._store_derived_frame(
            as_of_str=as_of_str,
            signal_id=signal_id,
            frame_bytes=frame_bytes,
            parent_ids=raw_ids,
        )
        log.info(
            "line1_frame_assembled",
            signal_id=signal_id,
            as_of=as_of_str,
            trade_dates=len(trade_dates),
            raw_snapshots=len(raw_ids),
            code_count=code_count,
            frame_snapshot_id=str(frame_snap.snapshot_id),
        )
        return Line1FrameResult(
            frame_snapshot=frame_snap,
            raw_snapshot_ids=tuple(raw_ids),
            trade_dates=tuple(trade_dates),
            code_count=code_count,
        )

    # -- window ---------------------------------------------------------

    def _trading_window(self, as_of: dt.date) -> list[str]:
        """The ``history_days`` trading days ending at ``as_of``, oldest→newest."""
        days: list[dt.date] = [as_of]
        cursor = as_of
        for _ in range(self._history_days - 1):
            cursor = prev_trading_day(cursor)
            days.append(cursor)
        days.reverse()
        return [d.strftime("%Y%m%d") for d in days]

    # -- fetch-or-reuse -------------------------------------------------

    async def _fetch_or_load(
        self,
        *,
        endpoint: str,
        trade_date: str,
        fetch: Callable[[], Awaitable[pd.DataFrame]],
        required_cols: frozenset[str] | None = None,
    ) -> tuple[MarketDataSnapshot, pd.DataFrame]:
        """Reuse an existing raw snapshot for the key, else fetch + persist.

        Idempotent: the store rejects a duplicate
        ``(vendor, endpoint, trade_date, version)``, so a same-day re-run
        must read the already-persisted PIT bytes rather than re-fetch.

        ``required_cols`` (when set) is validated on BOTH the fetch path
        (before persisting, so a bad pull is never stored) and the reuse
        path — a degraded payload fails closed (:class:`Line1FrameError`).
        """
        existing = self._store.latest(
            vendor=_RAW_VENDOR, endpoint=endpoint, trade_date=trade_date
        )
        if existing is not None:
            df = _read_csv_bytes(existing.raw_payload)
            _validate_pull(endpoint, trade_date, df, required_cols)
            return existing, df
        df = await fetch()
        _validate_pull(endpoint, trade_date, df, required_cols)
        raw = df.to_csv(index=False).encode("utf-8")
        snap = MarketDataSnapshot.create(
            vendor=_RAW_VENDOR,
            endpoint=endpoint,
            params={"trade_date": trade_date},
            trade_date=trade_date,
            raw_payload=raw,
            encoding="csv",
            compression="none",
            fetch_time_utc=self._now_utc(),
        )
        self._store.put(snap)
        return snap, df

    # -- frame build ----------------------------------------------------

    def _build_frame(
        self,
        *,
        trade_dates: list[str],
        as_of_date: dt.date,
        daily_by_date: dict[str, pd.DataFrame],
        roster_df: pd.DataFrame,
    ) -> tuple[bytes, int]:
        """Pivot the per-date daily rows into one CSV row per code."""
        name_by_code, list_date_by_code = _parse_roster(roster_df)

        closes_by_code: dict[str, list[float]] = defaultdict(list)
        amounts_by_code: dict[str, list[float]] = defaultdict(list)
        for td in trade_dates:  # oldest → newest preserves chronology
            df = daily_by_date[td]
            for code, close, amount in _iter_daily_rows(df):
                closes_by_code[code].append(close)
                amounts_by_code[code].append(
                    amount * _AMOUNT_THOUSAND_YUAN_TO_YUAN
                )

        lines = [SCREENER_FRAME_HEADER]
        # Deterministic order: sort by ts_code so the derived bytes are
        # bit-stable across re-runs on identical inputs (PIT replay).
        for code in sorted(closes_by_code):
            if code not in name_by_code:
                # No roster entry → no name / listing age; the screener would
                # exclude it as ipo_too_new anyway. Drop to keep rows clean.
                continue
            name = name_by_code[code]
            listed = _listed_trading_days(
                list_date_by_code.get(code), as_of_date
            )
            closes = closes_by_code[code]
            amounts = amounts_by_code[code]
            lines.append(
                f"{code},{name},{listed},"
                f"{_join_floats(closes)},{_join_floats(amounts)}"
            )
        code_count = len(lines) - 1
        return ("\n".join(lines)).encode("utf-8"), code_count

    # -- derived snapshot ----------------------------------------------

    def _store_derived_frame(
        self,
        *,
        as_of_str: str,
        signal_id: str,
        frame_bytes: bytes,
        parent_ids: list[UUID],
    ) -> MarketDataSnapshot:
        """Persist (or reuse) the derived CSV child snapshot with lineage.

        Reuse only when the existing snapshot's bytes AND parent lineage are
        identical to the freshly-computed ones (genuine idempotent re-run).
        If the inputs changed (a raw restatement or a different
        ``history_days``), the new bytes/parents are persisted as a **bigger
        version** rather than masked behind the stale snapshot — otherwise
        Line-1 would screen new data under old, false lineage (Codex P2).
        """
        parent_strs = [str(pid) for pid in parent_ids]
        existing = self._store.latest(
            vendor=DERIVED_VENDOR, endpoint=DERIVED_ENDPOINT, trade_date=as_of_str
        )
        if (
            existing is not None
            and existing.raw_payload == frame_bytes
            and existing.metadata.get("parent_snapshot_ids") == parent_strs
        ):
            return existing
        version = (existing.version + 1) if existing is not None else 1
        snap = MarketDataSnapshot.create(
            vendor=DERIVED_VENDOR,
            endpoint=DERIVED_ENDPOINT,
            params={"as_of": as_of_str, "signal_id": signal_id},
            trade_date=as_of_str,
            raw_payload=frame_bytes,
            encoding="csv",
            compression="none",
            fetch_time_utc=self._now_utc(),
            version=version,
            metadata={
                "kind": DERIVED_KIND,
                "signal_id": signal_id,
                "history_days": self._history_days,
                "parent_snapshot_ids": parent_strs,
            },
        )
        self._store.put(snap)
        return snap


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def _validate_pull(
    endpoint: str,
    trade_date: str,
    df: pd.DataFrame,
    required_cols: frozenset[str] | None,
) -> None:
    """Fail closed on an empty / columnless pull for a consumed endpoint.

    A per-code gap (a suspended stock missing from one date's rows) is
    normal and handled downstream; this guards the *whole-pull* failure
    where a trade date returns nothing and would otherwise be silently
    compressed out of every series (Codex P2).
    """
    if required_cols is None:
        return
    if df.empty or not required_cols <= set(df.columns):
        raise Line1FrameError(
            f"{endpoint} pull for {trade_date} is empty or missing required "
            f"columns {sorted(required_cols)} (have {sorted(df.columns)}) — "
            "fail closed rather than build a frame from a partial pull"
        )


def _read_csv_bytes(raw: bytes) -> pd.DataFrame:
    """Parse stored CSV bytes back to a DataFrame (offline reuse path)."""
    return pd.read_csv(io.BytesIO(raw), dtype={"ts_code": str})


def _iter_daily_rows(df: pd.DataFrame):  # noqa: ANN201 - internal generator
    """Yield ``(ts_code, close, amount)`` for finite-valued rows only.

    A NaN/missing close or amount is treated like a missing trade date
    (suspension): the value is skipped rather than emitted, so a corrupt
    cell never silently slips a non-finite token into the frame.
    """
    if df.empty or "ts_code" not in df.columns:
        return
    for row in df.itertuples(index=False):
        code = str(getattr(row, "ts_code", "")).strip()
        if not code:
            continue
        close = getattr(row, "close", None)
        amount = getattr(row, "amount", None)
        if close is None or amount is None:
            continue
        close_f = float(close)
        amount_f = float(amount)
        if not math.isfinite(close_f) or not math.isfinite(amount_f):
            continue
        yield code, close_f, amount_f


def _parse_roster(
    roster_df: pd.DataFrame,
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Build code→name and code→list_date(YYYYMMDD) maps from stock_basic."""
    name_by_code: dict[str, str] = {}
    list_date_by_code: dict[str, str | None] = {}
    if roster_df.empty or "ts_code" not in roster_df.columns:
        return name_by_code, list_date_by_code
    for row in roster_df.itertuples(index=False):
        code = str(getattr(row, "ts_code", "")).strip()
        if not code:
            continue
        raw_name = getattr(row, "name", "")
        name = "" if raw_name is None or _is_na(raw_name) else str(raw_name)
        # Commas would break the screener's 5-field CSV split — strip them.
        name_by_code[code] = name.replace(",", "").strip()
        list_date_by_code[code] = _normalize_list_date(
            getattr(row, "list_date", None)
        )
    return name_by_code, list_date_by_code


def _normalize_list_date(value: object) -> str | None:
    """Coerce a stock_basic ``list_date`` to ``YYYYMMDD`` or ``None``.

    Round-tripping through CSV can turn ``"20180102"`` into ``20180102`` or
    ``20180102.0``; normalise back to the 8-digit string.
    """
    if value is None or _is_na(value):
        return None
    if isinstance(value, float):
        value = int(value)
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text if len(text) == 8 and text.isdigit() else None


def _listed_trading_days(list_date: str | None, as_of: dt.date) -> str:
    """Trading days listed through ``as_of`` (inclusive), as a CSV token.

    Empty string when the listing date is unknown/invalid so the screener
    fails it closed as ``ipo_too_new``. ``as_of`` is a validated trading day,
    so the inclusive count is the half-open ``[list_date, as_of)`` count + 1.
    """
    if list_date is None:
        return ""
    try:
        ld = dt.date(int(list_date[:4]), int(list_date[4:6]), int(list_date[6:]))
    except ValueError:
        return ""
    if ld > as_of:
        return "0"
    return str(count_trading_days(ld, as_of) + 1)


def _join_floats(values: list[float]) -> str:
    """``|``-join floats with ``repr`` for deterministic full precision."""
    return "|".join(repr(v) for v in values)


def _is_na(value: object) -> bool:
    """True for pandas/NumPy NaN without importing numpy."""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


__all__ = [
    "DEFAULT_HISTORY_DAYS",
    "DERIVED_ENDPOINT",
    "DERIVED_KIND",
    "DERIVED_VENDOR",
    "SCREENER_FRAME_HEADER",
    "FrameDataSource",
    "Line1FrameAssembler",
    "Line1FrameError",
    "Line1FrameResult",
]
