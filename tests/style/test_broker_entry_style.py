"""AC-001 — broker position nameplate ``entry_style`` stamping.

Per-code pending style registered by the Line-1 runner before route, consumed
(popped) at episode open. Add-on keeps the original; close+reopen refreshes;
unregistered codes stamp None (legacy / Line-2 ADD path).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
from backend.style import StyleTag

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = dt.datetime(2026, 6, 12, 10, 0, tzinfo=SHANGHAI)


def _broker() -> MockBroker:
    return MockBroker(
        config=BrokerConfig(initial_capital=1_000_000.0), now_func=lambda: NOW
    )


class TestEntryStyleStamping:
    def test_registered_style_stamps_at_episode_open(self) -> None:
        broker = _broker()
        broker.set_pending_entry_style("600519", StyleTag.VALUE.value)
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        assert broker._positions["600519"].entry_style == "value"

    def test_unregistered_code_stamps_none(self) -> None:
        broker = _broker()
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        assert broker._positions["600519"].entry_style is None

    def test_pending_popped_at_open_not_reused(self) -> None:
        """A later buy of a DIFFERENT code must not inherit a stale style."""
        broker = _broker()
        broker.set_pending_entry_style("600519", StyleTag.VALUE.value)
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        # No registration for 000001 → None (600519's value was popped, not global)
        broker._apply_buy("000001", 5.0, 100, traded_date=NOW.date())
        assert broker._positions["000001"].entry_style is None

    def test_addon_buy_keeps_original_style(self) -> None:
        broker = _broker()
        broker.set_pending_entry_style("600519", StyleTag.VALUE.value)
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        # A stale re-register before the add-on must not overwrite the episode.
        broker.set_pending_entry_style("600519", StyleTag.SHORT_TERM.value)
        broker._apply_buy("600519", 13.0, 100, traded_date=NOW.date())
        assert broker._positions["600519"].entry_style == "value"

    def test_addon_consumes_stale_pending_no_leak(self) -> None:
        """A stale registration before a delivered add-on must not linger and
        stamp a later unrelated episode (codex P2)."""
        broker = _broker()
        broker.set_pending_entry_style("600519", StyleTag.VALUE.value)
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        # Stale registration, then an ADD-ON for the open episode.
        broker.set_pending_entry_style("600519", StyleTag.SHORT_TERM.value)
        broker._apply_buy("600519", 13.0, 100, traded_date=NOW.date())
        assert broker._positions["600519"].entry_style == "value"  # original kept
        # The stale pending was consumed → a future episode reuse does not leak.
        broker._apply_sell("600519", 300)
        broker._apply_buy("600519", 14.0, 100, traded_date=NOW.date())
        assert broker._positions["600519"].entry_style is None

    def test_close_then_reopen_gets_fresh_style(self) -> None:
        broker = _broker()
        broker.set_pending_entry_style("600519", StyleTag.VALUE.value)
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        broker._apply_sell("600519", 200)
        broker.set_pending_entry_style("600519", StyleTag.SHORT_TERM.value)
        broker._apply_buy("600519", 13.0, 100, traded_date=NOW.date())
        assert broker._positions["600519"].entry_style == "short_term"

    @pytest.mark.asyncio
    async def test_advance_day_discards_unconsumed_pending(self) -> None:
        """codex verify P2: a pending style that never filled today is discarded
        at the settlement reset, so it cannot stamp a later day's episode."""
        broker = _broker()
        broker.set_pending_entry_style("600519", StyleTag.VALUE.value)
        await broker.advance_day()  # same-day expiry bound
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        assert broker._positions["600519"].entry_style is None

    def test_set_none_clears_registration(self) -> None:
        broker = _broker()
        broker.set_pending_entry_style("600519", StyleTag.VALUE.value)
        broker.set_pending_entry_style("600519", None)
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        assert broker._positions["600519"].entry_style is None

    def test_entry_style_propagates_to_public_position(self) -> None:
        broker = _broker()
        broker.set_pending_entry_style("600519", StyleTag.VALUE.value)
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        positions = broker._build_positions()
        pos = next(p for p in positions if p.code == "600519")
        assert pos.entry_style == "value"

    def test_mock_broker_satisfies_style_sink_protocol(self) -> None:
        from backend.orchestration.line1_runner import StyleNameplateSink

        assert isinstance(_broker(), StyleNameplateSink)
