"""PIT-backed event-loop ``BarSource`` for the gate arena (QGR-2 ①, part 1).

Bridges the K-002 byte-exact PIT store → the deterministic
:mod:`backend.backtest` event loop, so a candidate gate strategy can be replayed
through the *real* system mechanics (≤5 slots / 5-td min hold / rotation / T+1 /
board slippage / **涨停不可成交**) and scored on absolute net P&L + MDD + turnover.

Reconstruction mirrors :mod:`backend.backtest.pit_export` (the rqalpha oracle's
same-source export), extended to carry the **real ``stk_limit`` price limits**
(QGR-1 ingested them) instead of the synthetic ±21% the oracle used:

* **qfq as-of the window end** — ``qfq(d) = raw(d)·factor(d)/factor(asof)`` using
  only factors on/before ``asof`` (a later split never leaks backward; R0 §3).
  The ±limit prices are qfq-scaled by the *same* per-day multiplier, so the
  event loop's integer ``at_limit_up`` / ``at_limit_down`` comparison is
  adjustment-invariant (``open`` and the limit move together).
* **ADV in shares** — trailing mean of Tushare daily ``vol`` (which is in 手 =
  100 shares) ×100, the harsh-fill capacity reference.
* **board / 过户费** — :func:`backend.data.stock_metadata.classify_board` +
  the SZ-side transfer-fee rule (mirrors ``cost_calculator``).

Offline + deterministic (Decimal arithmetic, no RNG, no wall-clock); reads only
the PIT store and never the live path. Lazy per-day parse with a cache, filtered
to an explicit ``universe`` so memory is bounded by ``|universe|·|days|``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_EVEN, Decimal

from backend.backtest.event_loop import DayBar
from backend.data.historical_ingest.job import VENDOR
from backend.data.historical_ingest.serialization import parse_csv_bytes
from backend.data.stock_metadata import (
    Board,
    ForbiddenCodeError,
    UnknownCodeError,
    classify_board,
)
from backend.marketdata_snapshot.store import SnapshotStore

_CENTS = Decimal("1")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")
_SHARES_PER_LOT = Decimal("100")  # Tushare daily ``vol`` is in 手 (100 shares).
# Wide synthetic fallback limits when stk_limit has no row (e.g. a brand-new
# listing) — keeps a close-priced fill from being spuriously blocked; real
# coverage is ~complete (data inventory §3), so this is a rare backstop.
_FALLBACK_UP = Decimal("1.21")
_FALLBACK_DOWN = Decimal("0.79")
_SZ_TRANSFER_FEE_BOARDS = frozenset({Board.SZ_MAIN, Board.CHUANGYE})
DEFAULT_ADV_WINDOW = 20


def _dec(value: object) -> Decimal:
    return Decimal(str(value).strip())


def _to_cents(price_yuan: Decimal) -> int:
    return int((price_yuan * _HUNDRED).quantize(_CENTS, rounding=ROUND_HALF_EVEN))


def _code6(ts_code: str) -> str:
    return ts_code.partition(".")[0]


def _transfer_fee_applies(code6: str, board: Board) -> bool:
    return board in _SZ_TRANSFER_FEE_BOARDS or code6.startswith("159")


class PitBarSource:
    """Look-ahead-free :class:`backend.backtest.event_loop.BarSource` over PIT.

    Args:
        store: the K-002 byte-exact PIT snapshot store.
        trading_days: ascending ``YYYYMMDD`` days the backtest will replay.
        universe: ``ts_code`` set the gate may ever hold/buy (bounds memory).
        asof: qfq adjustment anchor (defaults to the last trading day). Only
            factors on/before ``asof`` are used.
        adv_window: trailing window (days) for the ADV capacity reference.
    """

    def __init__(
        self,
        *,
        store: SnapshotStore,
        trading_days: Iterable[str],
        universe: Iterable[str],
        asof: str | None = None,
        adv_window: int = DEFAULT_ADV_WINDOW,
    ) -> None:
        self._store = store
        self._days: tuple[str, ...] = tuple(sorted(set(trading_days)))
        if not self._days:
            raise ValueError("trading_days must be non-empty")
        self._universe: frozenset[str] = frozenset(universe)
        self._asof = asof or self._days[-1]
        self._adv_window = max(1, adv_window)
        self._boards: dict[str, Board] = {}
        # ETFs live in the ``fund_daily`` endpoint, not ``daily`` (and are absent
        # from ``adj_factor`` — funds use no split factor here, mult 1.0). The
        # price reader merges both endpoints so the ETF beta baselines have bars.
        self._etf_codes: frozenset[str] = frozenset(
            c for c in self._universe if self._board_of(c) is Board.ETF
        )
        # One construction pass over adj_factor + daily/fund vol → asof factors + ADV.
        self._factor_by_day: dict[str, dict[str, Decimal]] = {}
        self._asof_factor: dict[str, Decimal] = {}
        self._adv_shares: dict[str, dict[str, float]] = {}
        self._bar_cache: dict[str, dict[str, DayBar]] = {}
        self._precompute()

    def trading_days(self) -> tuple[str, ...]:
        return self._days

    def bars_on(self, day: str) -> Mapping[str, DayBar]:
        if day not in set(self._days):
            raise KeyError(f"{day} is not a backtest trading day")
        if day not in self._bar_cache:
            self._bar_cache[day] = self._build_day(day)
        return self._bar_cache[day]

    # -- construction -------------------------------------------------
    def _precompute(self) -> None:
        # As-of ADV: a single forward pass keeps a per-code running history and
        # sets each day's ADV to the trailing mean of volumes ≤ that day. A
        # second pass over the FULL history would let an early bar read future
        # volume (look-ahead into the harsh-fill capacity gate). ADV includes the
        # current day's volume (standard trailing-window approximation; never a
        # later day's).
        running: dict[str, list[float]] = {}
        for day in self._days:
            if day > self._asof:
                continue
            self._factor_by_day[day] = self._read_factors(day)
            for code, factor in self._factor_by_day[day].items():
                self._asof_factor[code] = factor  # last day ≤ asof wins
            adv_today: dict[str, float] = {}
            for code, vol in self._read_vol_shares(day).items():
                hist = running.setdefault(code, [])
                hist.append(vol)
                window = hist[-self._adv_window :]
                adv_today[code] = sum(window) / len(window)
            self._adv_shares[day] = adv_today

    def _board_of(self, ts_code: str) -> Board | None:
        if ts_code not in self._boards:
            try:
                self._boards[ts_code] = classify_board(_code6(ts_code))
            except (ForbiddenCodeError, UnknownCodeError):
                return None
        return self._boards.get(ts_code)

    def _read_price_rows(self, day: str) -> dict[str, object]:
        """Per-code price row for the universe, merging stocks (``daily``) +
        ETFs (``fund_daily``). A code appears in exactly one endpoint; ``daily``
        wins on the (non-existent) overlap."""
        out: dict[str, object] = {}
        for endpoint in ("daily", "fund_daily"):
            snap = self._store.latest(
                vendor=VENDOR, endpoint=endpoint, trade_date=day
            )
            if snap is None:
                continue
            df = parse_csv_bytes(snap.raw_payload)
            for _, row in df.iterrows():
                code = str(row["ts_code"])
                if code in self._universe and code not in out:
                    out[code] = row
        return out

    def _read_factors(self, day: str) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        snap = self._store.latest(vendor=VENDOR, endpoint="adj_factor", trade_date=day)
        if snap is not None:
            df = parse_csv_bytes(snap.raw_payload)
            for _, row in df.iterrows():
                code = str(row["ts_code"])
                if code in self._universe and code not in self._etf_codes:
                    out[code] = _dec(row["adj_factor"])
        # ETFs are absent from adj_factor → flat factor 1.0 (raw fund_daily prices).
        for code in self._etf_codes:
            out[code] = _ONE
        return out

    def _read_vol_shares(self, day: str) -> dict[str, float]:
        return {
            code: float(_dec(row.get("vol", 0)) * _SHARES_PER_LOT)  # type: ignore[attr-defined]
            for code, row in self._read_price_rows(day).items()
        }

    # -- per-day bar assembly -----------------------------------------
    def _build_day(self, day: str) -> dict[str, DayBar]:
        rows = self._read_price_rows(day)
        if not rows:
            return {}
        limits = self._read_limits(day)
        factors = self._factor_by_day.get(day, {})
        adv = self._adv_shares.get(day, {})
        out: dict[str, DayBar] = {}
        for ts_code, row in rows.items():
            board = self._board_of(ts_code)
            if board is None:
                continue
            factor = factors.get(ts_code)
            asof_factor = self._asof_factor.get(ts_code)
            if factor is None or asof_factor is None or asof_factor == 0:
                continue
            mult = factor / asof_factor
            bar = self._make_bar(
                ts_code=ts_code,
                day=day,
                row=row,
                mult=mult,
                board=board,
                limit=limits.get(ts_code),
                adv=adv.get(ts_code, 0.0),
            )
            if bar is not None:
                out[ts_code] = bar
        return out

    def _read_limits(self, day: str) -> dict[str, dict[str, Decimal]]:
        snap = self._store.latest(vendor=VENDOR, endpoint="stk_limit", trade_date=day)
        if snap is None:
            return {}
        df = parse_csv_bytes(snap.raw_payload)
        out: dict[str, dict[str, Decimal]] = {}
        for _, row in df.iterrows():
            code = str(row["ts_code"])
            if code not in self._universe:
                continue
            try:
                up = _dec(row["up_limit"])
                down = _dec(row["down_limit"])
            except (KeyError, ArithmeticError, ValueError):
                continue
            # A blank/NaN limit parses to Decimal('NaN') without raising; treat it
            # as missing (→ synthetic fallback in _make_bar) so one bad PIT row
            # does not crash bars_on for the whole day.
            if not (up.is_finite() and down.is_finite() and up > 0 and down > 0):
                continue
            out[code] = {"up": up, "down": down}
        return out

    def _make_bar(
        self,
        *,
        ts_code: str,
        day: str,
        row: object,
        mult: Decimal,
        board: Board,
        limit: dict[str, Decimal] | None,
        adv: float,
    ) -> DayBar | None:
        get = row.get  # type: ignore[attr-defined]
        try:
            o = _to_cents(_dec(get("open")) * mult)
            h = _to_cents(_dec(get("high")) * mult)
            low = _to_cents(_dec(get("low")) * mult)
            c = _to_cents(_dec(get("close")) * mult)
        except (ArithmeticError, ValueError, TypeError):
            return None
        if min(o, h, low, c) <= 0:
            return None
        if limit is not None:
            up = _to_cents(limit["up"] * mult)
            down = _to_cents(limit["down"] * mult)
        else:
            up = _to_cents(_dec(get("close")) * mult * _FALLBACK_UP)
            down = _to_cents(_dec(get("close")) * mult * _FALLBACK_DOWN)
        return DayBar(
            code=ts_code,
            trade_date=day,
            open_cents=o,
            high_cents=h,
            low_cents=low,
            close_cents=c,
            adv_volume=adv,
            limit_up_cents=up,
            limit_down_cents=down,
            board=board.value,
            transfer_fee_applies=_transfer_fee_applies(_code6(ts_code), board),
        )


__all__ = ["DEFAULT_ADV_WINDOW", "PitBarSource"]
