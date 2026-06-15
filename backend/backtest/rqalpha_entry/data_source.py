"""Custom rqalpha data source fed from the PIT export (rqalpha venv side, AE-002).

Option B (R-002-amendment-2026-06-14 §2.2): rqalpha must see the **same**
point-in-time bars the live MockBroker shadow saw, otherwise a divergence is a
"different data" artefact, not an execution-logic bug. So instead of rqalpha's
own bundle (米筐 data, different source), the main env exports the PIT snapshot's
forward-adjusted (qfq) bars as a content-addressed ``bars.csv`` and this data
source serves them. It implements only the slice of
:class:`rqalpha.interface.AbstractDataSource` a daily-frequency stock backtest
exercises; everything tick / future / dividend related returns empty (the qfq
prices already embed corporate-action adjustment, so rqalpha applies none).
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
import pandas as pd
from rqalpha.const import TRADING_CALENDAR_TYPE
from rqalpha.interface import AbstractDataSource
from rqalpha.model.instrument import Instrument
from rqalpha.utils.datetime_func import convert_date_to_int

BAR_DTYPE = np.dtype(
    [
        ("datetime", "u8"),
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
        ("volume", "f8"),
        ("total_turnover", "f8"),
        ("limit_up", "f8"),
        ("limit_down", "f8"),
    ]
)
"""rqalpha day-bar record layout (``datetime`` is the YYYYMMDD*1e6 int)."""


class PitExportDataSource(AbstractDataSource):
    """Serve qfq daily bars + instruments from the PIT export.

    Args:
        bars: ``order_book_id -> structured array`` (``BAR_DTYPE``), sorted by
            ``datetime`` ascending.
        instruments: ``order_book_id -> Instrument``.
        trading_days: sorted list of :class:`datetime.date` (the export window).
    """

    def __init__(
        self,
        *,
        bars: dict[str, np.ndarray],
        instruments: dict[str, Instrument],
        trading_days: list[_dt.date],
    ) -> None:
        self._bars = bars
        self._instruments = instruments
        self._days = sorted(trading_days)

    # -- calendar / instruments ----------------------------------------
    def get_trading_calendars(self) -> dict[Any, pd.DatetimeIndex]:
        idx = pd.DatetimeIndex([pd.Timestamp(d) for d in self._days])
        return {TRADING_CALENDAR_TYPE.CN_STOCK: idx}

    def get_instruments(
        self,
        id_or_syms: Iterable[str] | None = None,
        types: Iterable[Any] | None = None,
    ) -> Iterable[Instrument]:
        vals = list(self._instruments.values())
        if id_or_syms is not None:
            ids = set(id_or_syms)
            vals = [i for i in vals if i.order_book_id in ids]
        if types is not None:
            wanted = {t.name if hasattr(t, "name") else t for t in types}
            vals = [i for i in vals if i.type in wanted]
        return vals

    def available_data_range(self, frequency: str) -> tuple[_dt.date, _dt.date]:
        return self._days[0], self._days[-1]

    # -- bars ----------------------------------------------------------
    def get_bar(
        self, instrument: Instrument, dt: _dt.date | _dt.datetime, frequency: str
    ) -> np.ndarray | None:
        bars = self._bars.get(instrument.order_book_id)
        if bars is None or len(bars) == 0:
            return None
        di = np.uint64(convert_date_to_int(dt))
        pos = int(bars["datetime"].searchsorted(di))
        if pos >= len(bars) or bars["datetime"][pos] != di:
            return None
        return bars[pos]

    def history_bars(
        self,
        instrument: Instrument,
        bar_count: int | None,
        frequency: str,
        fields: str | list[str] | None,
        dt: _dt.datetime,
        skip_suspended: bool = True,
        include_now: bool = False,
        adjust_type: str = "pre",
        adjust_orig: _dt.datetime | None = None,
    ) -> np.ndarray | None:
        bars = self._bars.get(instrument.order_book_id)
        if bars is None or len(bars) == 0:
            return bars
        di = np.uint64(convert_date_to_int(dt))
        i = int(bars["datetime"].searchsorted(di, side="right"))
        left = 0 if bar_count is None else max(0, i - bar_count)
        out = bars[left:i]
        return out if fields is None else out[fields]

    def get_open_auction_bar(
        self, instrument: Instrument, dt: _dt.date | _dt.datetime
    ) -> dict[str, Any]:
        keys = [
            "datetime",
            "open",
            "limit_up",
            "limit_down",
            "volume",
            "total_turnover",
        ]
        bar = self.get_bar(instrument, dt, "1d")
        res: dict[str, Any] = (
            dict.fromkeys(keys, np.nan)
            if bar is None
            else {k: bar[k] for k in keys}
        )
        res["last"] = res["open"]
        return res

    def get_settle_price(self, instrument: Instrument, dt: _dt.date) -> float:
        bar = self.get_bar(instrument, dt, "1d")
        return float("nan") if bar is None else float(bar["close"])

    # -- stubs (not exercised by a daily stock order-replay) -----------
    def is_suspended(self, order_book_id: str, dates: Sequence[Any]) -> list[bool]:
        return [False] * len(dates)

    def is_st_stock(self, order_book_id: str, dates: Sequence[Any]) -> list[bool]:
        return [False] * len(dates)

    def get_dividend(self, instrument: Instrument) -> None:
        return None

    def get_split(self, instrument: Instrument) -> None:
        return None

    def get_share_transformation(self, order_book_id: str) -> None:
        return None

    def current_snapshot(
        self, instrument: Instrument, frequency: str, dt: _dt.datetime
    ) -> None:
        return None

    def get_yield_curve(
        self, start_date: _dt.date, end_date: _dt.date, tenor: Any = None
    ) -> None:
        return None


__all__ = ["BAR_DTYPE", "PitExportDataSource"]
