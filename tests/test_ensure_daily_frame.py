"""Production T-1 EOD frame assembly (U-D6c / C1).

Before this, ``application.state.line2_daily_frame`` was never assigned in
production, so the 09:35 line1 + line2 crons were silent no-ops (zero BUY).
``_ensure_daily_frame`` lazily assembles the real Tushare T-1 EOD frame on the
first cron fire of the day and caches it. These tests pin the four contracts:
assemble+cache, idempotent-per-date, race-free (single assembly under
concurrency), and fail-open (assembly error leaves the frame unset, no crash).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from backend.main import _ensure_daily_frame
from backend.utils.trading_hours import t_minus_1_eod_utc

# Monday 2026-06-01 09:35 Asia/Shanghai → prev trading day = Fri 2026-05-29.
SHANGHAI = dt.timezone(dt.timedelta(hours=8))
_MON_0935 = dt.datetime(2026, 6, 1, 9, 35, tzinfo=SHANGHAI)
_EXPECTED_AS_OF = dt.date(2026, 5, 29)
_EXPECTED_COMPACT = "20260529"


def _fake_frame(trade_date: str = _EXPECTED_COMPACT) -> SimpleNamespace:
    return SimpleNamespace(trade_date=trade_date, snapshot_id="snap-1")


def _app(frame: object = None, alerter: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(line2_daily_frame=frame, feishu_alerter=alerter)
    )


def _patches(assemble: AsyncMock):
    """Patch the three things _ensure_daily_frame imports at call time."""
    assembler = MagicMock()
    assembler.assemble = assemble
    assembler_cls = MagicMock(return_value=assembler)
    return (
        patch("backend.orchestration.line1_frame.Line1FrameAssembler", assembler_cls),
        patch("backend.data.tushare_client.TushareClient", MagicMock()),
        patch("backend.marketdata_snapshot.SnapshotStore", MagicMock()),
        assembler_cls,
    )


class TestEnsureDailyFrame:
    async def test_assembles_and_caches_when_missing(self) -> None:
        frame = _fake_frame()
        assemble = AsyncMock(return_value=SimpleNamespace(frame_snapshot=frame))
        p1, p2, p3, assembler_cls = _patches(assemble)
        app = _app(None)
        with p1, p2, p3:
            await _ensure_daily_frame(app, asyncio.Lock(), _MON_0935)
        assert app.state.line2_daily_frame is frame
        # as_of = prev trading day (Fri 5-29), signal id compact-dated.
        assemble.assert_awaited_once()
        kwargs = assemble.await_args.kwargs
        assert kwargs["as_of_date"] == _EXPECTED_AS_OF
        assert kwargs["signal_id"] == f"LINE1-FRAME-{_EXPECTED_COMPACT}"

    async def test_fetch_time_anchored_to_t_minus_1_eod(self) -> None:
        # The now_utc passed to the assembler must anchor to the T-1 15:00
        # close — strictly before any run-day 09:35 created_at (the invariant).
        frame = _fake_frame()
        assemble = AsyncMock(return_value=SimpleNamespace(frame_snapshot=frame))
        p1, p2, p3, assembler_cls = _patches(assemble)
        with p1, p2, p3:
            await _ensure_daily_frame(_app(None), asyncio.Lock(), _MON_0935)
        now_utc = assembler_cls.call_args.kwargs["now_utc"]
        assert now_utc() == t_minus_1_eod_utc(_EXPECTED_AS_OF)
        assert now_utc() < _MON_0935.astimezone(dt.UTC)

    async def test_idempotent_when_cached_for_same_date(self) -> None:
        frame = _fake_frame(_EXPECTED_COMPACT)
        assemble = AsyncMock()
        p1, p2, p3, _ = _patches(assemble)
        app = _app(frame)
        with p1, p2, p3:
            await _ensure_daily_frame(app, asyncio.Lock(), _MON_0935)
        assemble.assert_not_awaited()
        assert app.state.line2_daily_frame is frame

    async def test_reassembles_when_cache_is_stale(self) -> None:
        stale = _fake_frame("20260101")  # different trade_date
        fresh = _fake_frame(_EXPECTED_COMPACT)
        assemble = AsyncMock(return_value=SimpleNamespace(frame_snapshot=fresh))
        p1, p2, p3, _ = _patches(assemble)
        app = _app(stale)
        with p1, p2, p3:
            await _ensure_daily_frame(app, asyncio.Lock(), _MON_0935)
        assemble.assert_awaited_once()
        assert app.state.line2_daily_frame is fresh

    async def test_fail_open_on_assembly_error(self) -> None:
        assemble = AsyncMock(side_effect=RuntimeError("tushare down"))
        p1, p2, p3, _ = _patches(assemble)
        app = _app(None)
        with p1, p2, p3:
            # Must NOT raise — data corruption fails closed, infra glitch
            # fails open: a frame pull failure just skips the run.
            await _ensure_daily_frame(app, asyncio.Lock(), _MON_0935)
        assert app.state.line2_daily_frame is None

    async def test_fail_open_alerts_owner_when_alerter_wired(self) -> None:
        # A silent zero-trade day is the danger: assembly failure must reach
        # the owner via the Feishu alerter (dedup-keyed), not just a log line.
        assemble = AsyncMock(side_effect=RuntimeError("tushare partial pull"))
        alerter = SimpleNamespace(fire=AsyncMock())
        p1, p2, p3, _ = _patches(assemble)
        app = _app(None, alerter=alerter)
        with p1, p2, p3:
            await _ensure_daily_frame(app, asyncio.Lock(), _MON_0935)
        alerter.fire.assert_awaited_once()
        kwargs = alerter.fire.await_args.kwargs
        assert kwargs["alert_type"] == "health_critical"
        assert kwargs["dedup_key"] == f"frame_assembly_failed:{_EXPECTED_COMPACT}"

    async def test_fail_open_survives_alerter_failure(self) -> None:
        # Alerting is best-effort — an alerter that itself raises must not
        # break the fail-open skip.
        assemble = AsyncMock(side_effect=RuntimeError("tushare down"))
        alerter = SimpleNamespace(fire=AsyncMock(side_effect=RuntimeError("ws")))
        p1, p2, p3, _ = _patches(assemble)
        app = _app(None, alerter=alerter)
        with p1, p2, p3:
            await _ensure_daily_frame(app, asyncio.Lock(), _MON_0935)
        assert app.state.line2_daily_frame is None

    async def test_concurrent_calls_assemble_once(self) -> None:
        frame = _fake_frame()
        started = 0

        async def _slow_assemble(**_kw: object) -> object:
            nonlocal started
            started += 1
            await asyncio.sleep(0.02)
            return SimpleNamespace(frame_snapshot=frame)

        assemble = AsyncMock(side_effect=_slow_assemble)
        p1, p2, p3, _ = _patches(assemble)
        app = _app(None)
        lock = asyncio.Lock()
        with p1, p2, p3:
            await asyncio.gather(
                _ensure_daily_frame(app, lock, _MON_0935),
                _ensure_daily_frame(app, lock, _MON_0935),
                _ensure_daily_frame(app, lock, _MON_0935),
            )
        # The lock + idempotency check collapse the 3 concurrent crons into a
        # single Tushare pull.
        assert started == 1
        assert app.state.line2_daily_frame is frame
