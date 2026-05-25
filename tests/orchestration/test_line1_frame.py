"""U-B1 — Line-1 market-frame assembler tests.

Proves the assembler turns fake Tushare pulls into a screener-consumable
PIT frame with raw-per-endpoint snapshots + a derived child snapshot whose
metadata links the parent lineage, with the ¥-unit conversion, the listing
age, idempotent re-runs, and fail-closed handling of bad inputs — all on an
in-memory fake (zero network).
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from backend.data.trading_calendar import count_trading_days, prev_trading_day
from backend.marketdata_snapshot.store import SnapshotStore
from backend.orchestration.line1_frame import (
    DERIVED_ENDPOINT,
    DERIVED_KIND,
    DERIVED_VENDOR,
    SCREENER_FRAME_HEADER,
    Line1FrameAssembler,
    Line1FrameError,
)
from backend.screening.screener import Screener
from backend.services.universe_policy import ExclusionRules

_HISTORY = 22  # > MIN_HISTORY_BARS (21) so survivors are scorable

# (ts_code, name, list_date, close, amount_qianyuan)
_OLD_A = ("600000.SH", "浦发银行", "20100101", 10.0, 300_000.0)
_OLD_B = ("000001.SZ", "平安银行", "20100101", 12.0, 400_000.0)
_NEW = ("600519.SH", "次新股", None, 50.0, 250_000.0)  # list_date set per-test


class _FakeTushare:
    """In-memory FrameDataSource: daily/daily_basic return a fixed frame for
    every trade_date (the assembler keys series by the requested date, not
    the payload's own trade_date column), stock_basic returns the roster."""

    def __init__(self, spec: list[tuple[str, str, str | None, float, float]]) -> None:
        self._spec = spec
        self.daily_calls = 0
        self.daily_basic_calls = 0
        self.stock_basic_calls = 0

    async def daily(self, trade_date: str) -> pd.DataFrame:
        self.daily_calls += 1
        return pd.DataFrame(
            {
                "ts_code": [s[0] for s in self._spec],
                "trade_date": [trade_date] * len(self._spec),
                "close": [s[3] for s in self._spec],
                "amount": [s[4] for s in self._spec],
            }
        )

    async def daily_basic(self, trade_date: str) -> pd.DataFrame:
        self.daily_basic_calls += 1
        return pd.DataFrame(
            {
                "ts_code": [s[0] for s in self._spec],
                "trade_date": [trade_date] * len(self._spec),
                "close": [s[3] for s in self._spec],
                "pe": [15.0] * len(self._spec),
            }
        )

    async def stock_basic(self) -> pd.DataFrame:
        self.stock_basic_calls += 1
        return pd.DataFrame(
            {
                "ts_code": [s[0] for s in self._spec],
                "name": [s[1] for s in self._spec],
                "list_date": [s[2] for s in self._spec],
            }
        )


def _as_of() -> dt.date:
    # A guaranteed trading day with >= _HISTORY trading days of history
    # behind it in the static 2026 calendar.
    return prev_trading_day(dt.date(2026, 5, 18))


def _store(tmp_path) -> SnapshotStore:  # noqa: ANN001
    return SnapshotStore(tmp_path / "snapshots")


def _frame_rows(payload: bytes) -> dict[str, list[str]]:
    """Parse the derived frame into ``{ts_code: [name, listed, closes, amounts]}``."""
    text = payload.decode("utf-8").splitlines()
    assert text[0] == SCREENER_FRAME_HEADER
    out: dict[str, list[str]] = {}
    for line in text[1:]:
        parts = line.split(",")
        out[parts[0]] = parts[1:]
    return out


async def test_assemble_produces_screener_consumable_frame(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    client = _FakeTushare([_OLD_A, _OLD_B])
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)

    result = await assembler.assemble(as_of_date=_as_of(), signal_id="SIG-ub1")

    # The derived frame is a csv snapshot the screener can consume directly.
    assert result.frame_snapshot.encoding == "csv"
    assert result.code_count == 2
    screen = Screener(ExclusionRules()).screen(result.frame_snapshot, "SIG-ub1")
    picked = {c.code for c in screen.candidates}
    assert {"600000", "000001"} <= picked  # suffix stripped by the screener


async def test_raw_payloads_each_stored_as_own_snapshot(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    client = _FakeTushare([_OLD_A, _OLD_B])
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)

    result = await assembler.assemble(as_of_date=_as_of(), signal_id="SIG-ub1")

    # daily + daily_basic per trade_date + stock_basic once.
    assert len(result.raw_snapshot_ids) == _HISTORY * 2 + 1
    # Every parent id resolves + passes its checksum (verify-before-adopt).
    for sid in result.raw_snapshot_ids:
        snap = store.get(sid)
        assert snap.vendor == "tushare"
    assert client.stock_basic_calls == 1  # roster fetched once, not per date


async def test_derived_frame_records_parent_lineage(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    client = _FakeTushare([_OLD_A, _OLD_B])
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)

    result = await assembler.assemble(as_of_date=_as_of(), signal_id="SIG-ub1")

    meta = result.frame_snapshot.metadata
    assert meta["kind"] == DERIVED_KIND
    assert meta["signal_id"] == "SIG-ub1"
    assert meta["parent_snapshot_ids"] == [str(s) for s in result.raw_snapshot_ids]
    assert result.frame_snapshot.vendor == DERIVED_VENDOR
    assert result.frame_snapshot.endpoint == DERIVED_ENDPOINT


async def test_amount_converted_thousand_yuan_to_yuan(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    client = _FakeTushare([_OLD_A])  # amount 300_000 千元 → 3e8 元
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)

    result = await assembler.assemble(as_of_date=_as_of(), signal_id="SIG-ub1")

    rows = _frame_rows(result.frame_snapshot.raw_payload)
    amounts = rows["600000.SH"][3].split("|")
    assert len(amounts) == _HISTORY
    assert all(float(a) == 300_000.0 * 1000.0 for a in amounts)


async def test_listed_trading_days_from_list_date(tmp_path) -> None:  # noqa: ANN001
    as_of = _as_of()
    recent_list = prev_trading_day(prev_trading_day(prev_trading_day(as_of)))
    new_code = (_NEW[0], _NEW[1], recent_list.strftime("%Y%m%d"), _NEW[3], _NEW[4])
    store = _store(tmp_path)
    client = _FakeTushare([_OLD_A, new_code])
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)

    result = await assembler.assemble(as_of_date=as_of, signal_id="SIG-ub1")

    rows = _frame_rows(result.frame_snapshot.raw_payload)
    # Old code: listed long ago → a large trading-day count.
    assert int(rows["600000.SH"][1]) > 1000
    # New code: 3 trading days before as_of → count[list_date, as_of) + 1.
    expected = count_trading_days(recent_list, as_of) + 1
    assert int(rows["600519.SH"][1]) == expected


async def test_idempotent_rerun_reuses_snapshots(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    client = _FakeTushare([_OLD_A, _OLD_B])
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)
    as_of = _as_of()

    first = await assembler.assemble(as_of_date=as_of, signal_id="SIG-1")
    daily_calls_after_first = client.daily_calls
    second = await assembler.assemble(as_of_date=as_of, signal_id="SIG-2")

    # Re-run reuses every persisted snapshot — no SnapshotOverwriteError,
    # no re-fetch, identical derived id + bytes (PIT replay stability).
    assert client.daily_calls == daily_calls_after_first
    assert second.frame_snapshot.snapshot_id == first.frame_snapshot.snapshot_id
    assert second.frame_snapshot.raw_payload == first.frame_snapshot.raw_payload


async def test_non_trading_day_as_of_fails_closed(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    client = _FakeTushare([_OLD_A])
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)
    # 2026-05-17 is a Sunday → not a trading day.
    with pytest.raises(ValueError, match="not a trading day"):
        await assembler.assemble(as_of_date=dt.date(2026, 5, 17), signal_id="X")


async def test_nan_rows_skipped_and_unrostered_dropped(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)

    class _NaNClient(_FakeTushare):
        async def daily(self, trade_date: str) -> pd.DataFrame:
            self.daily_calls += 1
            # _OLD_A clean; a NaN-close code; an unrostered code.
            return pd.DataFrame(
                {
                    "ts_code": ["600000.SH", "600001.SH", "600002.SH"],
                    "close": [10.0, float("nan"), 20.0],
                    "amount": [300_000.0, 300_000.0, 300_000.0],
                }
            )

    # roster has 600000 + 600001 but NOT 600002 (unrostered → dropped).
    client = _NaNClient([_OLD_A, ("600001.SH", "测试", "20100101", 9.0, 300_000.0)])
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)
    result = await assembler.assemble(as_of_date=_as_of(), signal_id="SIG")

    rows = _frame_rows(result.frame_snapshot.raw_payload)
    assert "600000.SH" in rows  # clean code present
    assert "600002.SH" not in rows  # unrostered → dropped
    # 600001 had a NaN close every day → no finite values appended → it has
    # no series at all, so it is dropped (fail-closed) rather than emitted.
    assert "600001.SH" not in rows


async def test_empty_daily_pull_fails_closed(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)

    class _EmptyDailyClient(_FakeTushare):
        async def daily(self, trade_date: str) -> pd.DataFrame:
            self.daily_calls += 1
            return pd.DataFrame()  # whole-pull empty (data not landed)

    client = _EmptyDailyClient([_OLD_A])
    assembler = Line1FrameAssembler(client=client, store=store, history_days=_HISTORY)
    with pytest.raises(Line1FrameError, match="empty or missing required columns"):
        await assembler.assemble(as_of_date=_as_of(), signal_id="SIG")


async def test_changed_window_stores_new_derived_version(tmp_path) -> None:  # noqa: ANN001
    store = _store(tmp_path)
    spec = [_OLD_A, _OLD_B]
    as_of = _as_of()

    first = await Line1FrameAssembler(
        client=_FakeTushare(spec), store=store, history_days=_HISTORY
    ).assemble(as_of_date=as_of, signal_id="SIG-1")
    # Same as_of, smaller window → different bytes + different parent set →
    # must persist as a NEW version, not mask behind the stale snapshot.
    second = await Line1FrameAssembler(
        client=_FakeTushare(spec), store=store, history_days=_HISTORY - 1
    ).assemble(as_of_date=as_of, signal_id="SIG-2")

    assert first.frame_snapshot.version == 1
    assert second.frame_snapshot.version == 2
    assert second.frame_snapshot.raw_payload != first.frame_snapshot.raw_payload
    assert (
        second.frame_snapshot.metadata["parent_snapshot_ids"]
        == [str(s) for s in second.raw_snapshot_ids]
    )
