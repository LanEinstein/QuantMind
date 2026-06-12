"""AA-004 position nameplate round-trip tests.

Covers: episode-open stamping (BUY creates / add-on keeps / close+reopen
refreshes), snapshot v3 carry, v1/v2 checksum backward compat, recovery
replay stamping from event payloads, and reconciliation-reset nulling.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from backend.broker.mock_broker import MockBroker
from backend.broker.models import BrokerConfig
from backend.broker.persistence.checksum import compute_snapshot_checksum
from backend.broker.persistence.snapshots import (
    BROKER_SNAPSHOT_SCHEMA_VERSION,
    BrokerSnapshotPosition,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = dt.datetime(2026, 6, 12, 10, 0, tzinfo=SHANGHAI)
PH = "a" * 64
STACK = "monitoring.intraday_triggers/v11"


def _broker(*, nameplate: bool = True) -> MockBroker:
    broker = MockBroker(
        config=BrokerConfig(initial_capital=1_000_000.0),
        now_func=lambda: NOW,
    )
    if nameplate:
        broker.set_entry_nameplate(
            policy_hash=PH, sell_stack_version=STACK
        )
    return broker


class TestEpisodeStamping:
    def test_new_position_gets_nameplate(self) -> None:
        broker = _broker()
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        pos = broker._positions["600519"]
        assert pos.entry_policy_hash == PH
        assert pos.entry_sell_stack_version == STACK
        assert pos.entry_style is None

    def test_addon_buy_keeps_original_stamp(self) -> None:
        broker = _broker()
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        broker.set_entry_nameplate(
            policy_hash="b" * 64, sell_stack_version="v99"
        )
        broker._apply_buy("600519", 13.00, 100, traded_date=NOW.date())
        pos = broker._positions["600519"]
        assert pos.entry_policy_hash == PH
        assert pos.entry_sell_stack_version == STACK

    def test_close_then_reopen_gets_fresh_stamp(self) -> None:
        broker = _broker()
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        broker._apply_sell("600519", 200)
        broker.set_entry_nameplate(
            policy_hash="b" * 64, sell_stack_version="v99"
        )
        broker._apply_buy("600519", 13.00, 100, traded_date=NOW.date())
        pos = broker._positions["600519"]
        assert pos.entry_policy_hash == "b" * 64
        assert pos.entry_sell_stack_version == "v99"

    def test_unwired_nameplate_stamps_none(self) -> None:
        broker = _broker(nameplate=False)
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        pos = broker._positions["600519"]
        assert pos.entry_policy_hash is None
        assert pos.entry_sell_stack_version is None

    @pytest.mark.asyncio
    async def test_public_position_carries_nameplate(self) -> None:
        broker = _broker()
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        (position,) = await broker.get_positions()
        assert position.entry_policy_hash == PH
        assert position.entry_style is None
        assert position.entry_sell_stack_version == STACK


class TestSeedAndResetCarry:
    @pytest.mark.asyncio
    async def test_seed_from_recovery_carries_nameplate(self) -> None:
        broker = _broker(nameplate=False)
        await broker.seed_from_recovery(
            cash=100_000.0,
            frozen_cash=0.0,
            initial_capital=1_000_000.0,
            positions=(
                BrokerSnapshotPosition(
                    code="600519",
                    volume=200,
                    today_bought_volume=0,
                    cost_price=12.34,
                    entry_policy_hash=PH,
                    entry_sell_stack_version=STACK,
                ),
            ),
        )
        (position,) = await broker.get_positions()
        assert position.entry_policy_hash == PH
        assert position.entry_sell_stack_version == STACK

    @pytest.mark.asyncio
    async def test_reconciliation_reset_nulls_nameplate(self) -> None:
        """A user-reported rewrite has no nameplate (origin tracking)."""
        from backend.models.reconciliation import ReportedPosition

        broker = _broker()
        broker._apply_buy("600519", 12.34, 200, traded_date=NOW.date())
        await broker.reset_to_snapshot(
            cash=50_000.0,
            positions=(
                ReportedPosition(
                    code="600519", volume=300, cost_price=12.00
                ),
            ),
            reset_at=NOW,
            reason="reset_to_user_snapshot",
        )
        (position,) = await broker.get_positions()
        assert position.entry_policy_hash is None
        assert position.entry_sell_stack_version is None


class TestSnapshotV3:
    def test_schema_version_is_3(self) -> None:
        assert BROKER_SNAPSHOT_SCHEMA_VERSION == 3

    def test_checksum_without_nameplate_is_v2_identical(self) -> None:
        """Legacy rows (no nameplate) keep their stored checksums valid."""
        plain = BrokerSnapshotPosition(
            code="600519",
            volume=200,
            today_bought_volume=0,
            cost_price=12.34,
        )
        # The v2 expectation, hand-derived with the pre-v3 payload shape.
        import hashlib
        import json

        v2_payload = {
            "cash": 100_000.0,
            "frozen_cash": 0.0,
            "initial_capital": 1_000_000.0,
            "positions": [
                {
                    "code": "600519",
                    "volume": 200,
                    "today_bought_volume": 0,
                    "cost_price": 12.34,
                }
            ],
        }
        expected = hashlib.sha256(
            json.dumps(
                v2_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()[:16]
        assert (
            compute_snapshot_checksum(
                100_000.0, 0.0, 1_000_000.0, (plain,)
            )
            == expected
        )

    def test_checksum_changes_with_nameplate(self) -> None:
        plain = BrokerSnapshotPosition(
            code="600519",
            volume=200,
            today_bought_volume=0,
            cost_price=12.34,
        )
        stamped = plain.model_copy(
            update={
                "entry_policy_hash": PH,
                "entry_sell_stack_version": STACK,
            }
        )
        assert compute_snapshot_checksum(
            100_000.0, 0.0, 1_000_000.0, (plain,)
        ) != compute_snapshot_checksum(
            100_000.0, 0.0, 1_000_000.0, (stamped,)
        )

    def test_v1_v2_rows_validate_with_default_none(self) -> None:
        row = BrokerSnapshotPosition.model_validate(
            {
                "code": "600519",
                "volume": 200,
                "today_bought_volume": 0,
                "cost_price": 12.34,
            }
        )
        assert row.entry_policy_hash is None
        assert row.entry_style is None
        assert row.entry_sell_stack_version is None


class TestRecoveryReplayNameplate:
    """Replay stamps the nameplate from the event payload (AA-004) so a
    restart rebuilds positions bit-identical to the live _apply_buy."""

    def _state(self) -> object:
        from backend.broker.persistence.recovery import RecoveredState

        return RecoveredState(
            cash=1_000_000.0, frozen_cash=0.0, initial_capital=1_000_000.0
        )

    def _fill_event(self, *, with_nameplate: bool, sequence: int = 1):
        from backend.broker.persistence.events import (
            BrokerEvent,
            BrokerEventType,
        )

        payload = {
            "code": "600519",
            "direction": "BUY",
            "volume": 200,
            "fill_price": 12.34,
            "commission": 5.0,
            "stamp_tax": 0.0,
            "transfer_fee": 0.0,
            "frozen_amount": 2_473.0,
        }
        if with_nameplate:
            payload["entry_policy_hash"] = PH
            payload["entry_sell_stack_version"] = STACK
        return BrokerEvent(
            sequence=sequence,
            occurred_at=NOW,
            event_type=BrokerEventType.ORDER_FILLED,
            payload=payload,
        )

    def test_order_filled_creation_stamps_from_payload(self) -> None:
        from backend.broker.persistence.recovery import _apply_event

        state = self._state()
        _apply_event(state, self._fill_event(with_nameplate=True))
        pos = state.positions["600519"]
        assert pos.entry_policy_hash == PH
        assert pos.entry_sell_stack_version == STACK

    def test_legacy_event_without_nameplate_stamps_none(self) -> None:
        from backend.broker.persistence.recovery import _apply_event

        state = self._state()
        _apply_event(state, self._fill_event(with_nameplate=False))
        pos = state.positions["600519"]
        assert pos.entry_policy_hash is None
        assert pos.entry_sell_stack_version is None

    def test_execution_report_applied_creation_stamps(self) -> None:
        from backend.broker.persistence.events import (
            BrokerEvent,
            BrokerEventType,
        )
        from backend.broker.persistence.recovery import _apply_event

        state = self._state()
        event = BrokerEvent(
            sequence=1,
            occurred_at=NOW,
            event_type=BrokerEventType.EXECUTION_REPORT_APPLIED,
            payload={
                "instruction_id": "QM-20260612-103000-000001-BUY-001",
                "side_is_buy": True,
                "cash_delta": -2_473.0,
                "positions_delta": [
                    {
                        "code": "600519",
                        "volume_delta": 200,
                        "cost_price": 12.365,
                    }
                ],
                "report_schema_version": 2,
                "commission": 5.0,
                "stamp_tax": 0.0,
                "transfer_fee": 0.0,
                "net": 2_473.0,
                "entry_policy_hash": PH,
                "entry_sell_stack_version": STACK,
            },
        )
        _apply_event(state, event)
        pos = state.positions["600519"]
        assert pos.entry_policy_hash == PH
        assert pos.entry_sell_stack_version == STACK

    def test_to_snapshot_positions_carries_nameplate(self) -> None:
        from backend.broker.persistence.recovery import _apply_event

        state = self._state()
        _apply_event(state, self._fill_event(with_nameplate=True))
        (row,) = state.to_snapshot_positions()
        assert row.entry_policy_hash == PH
        assert row.entry_sell_stack_version == STACK
