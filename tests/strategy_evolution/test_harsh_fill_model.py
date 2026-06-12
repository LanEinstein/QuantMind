"""AB-007 harsh fill model tests (anti-MockBroker-overfit shadow path)."""

from __future__ import annotations

import pytest

from backend.strategy_evolution.harsh_fill_model import (
    HarshFillConfig,
    HarshRejectReason,
    ShadowBar,
    ShadowOrder,
    simulate_harsh_fill,
)


def _order(
    *, buy: bool = True, volume: int = 1_000, price: float = 10.0
) -> ShadowOrder:
    return ShadowOrder(
        side_is_buy=buy, volume=volume, reference_price=price
    )


def _bar(**overrides: object) -> ShadowBar:
    base: dict[str, object] = {
        "adv_volume": 1_000_000.0,
        "limit_up": False,
        "limit_down": False,
        "quote_age_s": 5.0,
        "next_bar_open": None,
    }
    base.update(overrides)
    return ShadowBar(**base)  # type: ignore[arg-type]


class TestRejections:
    def test_limit_up_buy_no_fill(self) -> None:
        fill = simulate_harsh_fill(_order(buy=True), _bar(limit_up=True))
        assert not fill.filled
        assert fill.rejected_reason is HarshRejectReason.LIMIT_UP_NO_FILL

    def test_limit_down_sell_no_fill(self) -> None:
        fill = simulate_harsh_fill(
            _order(buy=False), _bar(limit_down=True)
        )
        assert fill.rejected_reason is (
            HarshRejectReason.LIMIT_DOWN_NO_FILL
        )

    def test_limit_up_sell_still_fills(self) -> None:
        fill = simulate_harsh_fill(
            _order(buy=False), _bar(limit_up=True)
        )
        assert fill.filled

    def test_stale_quote_rejected(self) -> None:
        fill = simulate_harsh_fill(_order(), _bar(quote_age_s=120.0))
        assert fill.rejected_reason is HarshRejectReason.STALE_QUOTE

    def test_zero_capacity_rejected(self) -> None:
        fill = simulate_harsh_fill(_order(), _bar(adv_volume=500.0))
        assert fill.rejected_reason is HarshRejectReason.ZERO_CAPACITY


class TestHarshOrEqualContract:
    """The anti-overfit guarantee: never better than the naive fill."""

    def test_volume_capped_by_adv_participation(self) -> None:
        fill = simulate_harsh_fill(
            _order(volume=100_000), _bar(adv_volume=1_000_000.0)
        )
        assert fill.filled_volume == 50_000  # 5% of ADV
        assert fill.filled_volume < 100_000

    def test_capacity_rounds_down_to_lot(self) -> None:
        fill = simulate_harsh_fill(
            _order(volume=10_000), _bar(adv_volume=30_500.0)
        )
        # 5% of 30500 = 1525 → lot-rounded 1500.
        assert fill.filled_volume == 1500

    def test_buy_price_is_adverse_or_equal(self) -> None:
        fill = simulate_harsh_fill(_order(buy=True), _bar())
        assert fill.fill_price >= 10.0

    def test_sell_price_is_adverse_or_equal(self) -> None:
        fill = simulate_harsh_fill(_order(buy=False), _bar())
        assert fill.fill_price <= 10.0

    def test_impact_grows_with_participation(self) -> None:
        small = simulate_harsh_fill(
            _order(volume=1_000), _bar(adv_volume=1_000_000.0)
        )
        large = simulate_harsh_fill(
            _order(volume=50_000), _bar(adv_volume=1_000_000.0)
        )
        assert large.fill_price > small.fill_price > 10.0

    def test_favourable_next_bar_gap_clamped(self) -> None:
        """A gap DOWN must not give the BUY a better-than-reference
        price — the delayed fill is harsh-or-equal by contract."""
        fill = simulate_harsh_fill(
            _order(buy=True), _bar(next_bar_open=9.0)
        )
        assert fill.fill_price >= 10.0

    def test_adverse_next_bar_gap_passes_through(self) -> None:
        fill = simulate_harsh_fill(
            _order(buy=True), _bar(next_bar_open=10.5)
        )
        assert fill.fill_price >= 10.5

    def test_delay_disabled_uses_reference(self) -> None:
        cfg = HarshFillConfig(delay_to_next_bar=False)
        fill = simulate_harsh_fill(
            _order(buy=True), _bar(next_bar_open=10.5), config=cfg
        )
        # Impact only — the next-bar gap is ignored.
        assert 10.0 < fill.fill_price < 10.5


class TestEvolutionSkipAudit:
    """Codex AB P2 — a skipped 22:00 run audits DEGRADED, not SUCCESS."""

    @pytest.mark.asyncio
    async def test_callback_skip_signal_audits_degraded(self) -> None:
        import datetime as dt
        from dataclasses import dataclass, field
        from typing import Any
        from zoneinfo import ZoneInfo

        from backend.audit.models import AuditOutcome
        from backend.broker.scheduler import BrokerScheduler

        @dataclass
        class _FakeAudit:
            rows: list[dict[str, Any]] = field(default_factory=list)

            async def write(self, **kwargs: Any) -> None:
                self.rows.append(kwargs)

        async def cb(now: dt.datetime) -> str:
            return "skipped_dispatcher_unwired"

        audit = _FakeAudit()
        sched = BrokerScheduler(
            broker=object(),  # type: ignore[arg-type]
            event_store=None,  # type: ignore[arg-type]
            snapshot_store=None,  # type: ignore[arg-type]
            audit_store=audit,  # type: ignore[arg-type]
            evolution_shadow_run_callback=cb,
            now_func=lambda: dt.datetime(
                2026, 6, 12, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai")
            ),
        )
        assert await sched.run_evolution_shadow() is True
        (row,) = audit.rows
        assert row["outcome"] is AuditOutcome.DEGRADED
        assert row["payload"]["status"] == "skipped_dispatcher_unwired"

    @pytest.mark.asyncio
    async def test_none_return_keeps_success_semantics(self) -> None:
        import datetime as dt
        from dataclasses import dataclass, field
        from typing import Any
        from zoneinfo import ZoneInfo

        from backend.audit.models import AuditOutcome
        from backend.broker.scheduler import BrokerScheduler

        @dataclass
        class _FakeAudit:
            rows: list[dict[str, Any]] = field(default_factory=list)

            async def write(self, **kwargs: Any) -> None:
                self.rows.append(kwargs)

        async def cb(now: dt.datetime) -> None:
            return None

        audit = _FakeAudit()
        sched = BrokerScheduler(
            broker=object(),  # type: ignore[arg-type]
            event_store=None,  # type: ignore[arg-type]
            snapshot_store=None,  # type: ignore[arg-type]
            audit_store=audit,  # type: ignore[arg-type]
            evolution_shadow_run_callback=cb,
            now_func=lambda: dt.datetime(
                2026, 6, 12, 22, 0, tzinfo=ZoneInfo("Asia/Shanghai")
            ),
        )
        assert await sched.run_evolution_shadow() is True
        (row,) = audit.rows
        assert row["outcome"] is AuditOutcome.SUCCESS
