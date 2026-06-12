"""AC-006 — per-style soft take-profit band + hard-protection style invariant.

The central red line (P0-8-amendment-2026-06-12 §1.5): the style label conditions
ONLY the take-profit band; every protective stop (drawdown / ATR trailing /
thesis break) is bit-identical across styles.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.broker.models import AccountInfo, Position
from backend.models.market import WatchlistMarketSnapshot
from backend.monitoring.intraday_triggers import (
    IntradayTriggerKind,
    StyleSoftConfig,
    evaluate_intraday_sell_intents,
)
from backend.monitoring.style_soft import style_take_profit_r_multiple
from backend.style import StyleTag

_NOW = datetime(2026, 6, 12, 10, 30, 0, tzinfo=UTC)


def _spot(
    code: str = "600519",
    *,
    price: float,
    prev_close: float,
    high: float | None = None,
) -> WatchlistMarketSnapshot:
    return WatchlistMarketSnapshot(
        code=code,
        name="测试",
        price=price,
        open=prev_close,
        high=high if high is not None else max(price, prev_close),
        low=min(price, prev_close),
        prev_close=prev_close,
        change_pct=(price - prev_close) / prev_close * 100 if prev_close else 0.0,
        volume=1_000_000.0,
        amount=3.0e8,
        turnover_rate=1.0,
        source="adata",
        snapshot_at=_NOW - timedelta(seconds=2),
    )


def _pos(cost: float = 10.0, volume: int = 1000, style: str | None = None) -> Position:
    return Position(
        code="600519",
        volume=volume,
        available_volume=volume,
        cost_price=cost,
        market_value=cost * volume,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        entry_style=style,
    )


def _account(total: float = 1_000_000.0) -> AccountInfo:
    return AccountInfo(
        total_assets=total,
        available_cash=total * 0.9,
        frozen_cash=0.0,
        market_value=total * 0.1,
        total_pnl=0.0,
        total_pnl_pct=0.0,
        initial_capital=total,
    )


# Gentle uptrend → a positive, finite ATR for the take-profit r-unit.
_CLOSES = tuple(round(10.0 + 0.05 * i, 4) for i in range(30))


class TestStyleSoftConfig:
    def test_default_widens_value(self) -> None:
        cfg = StyleSoftConfig()
        assert style_take_profit_r_multiple(1.0, StyleTag.VALUE, cfg) == 1.5
        assert style_take_profit_r_multiple(1.0, StyleTag.SHORT_TERM, cfg) == 1.0

    def test_none_config_is_identity(self) -> None:
        assert style_take_profit_r_multiple(1.0, StyleTag.VALUE, None) == 1.0

    def test_none_style_is_identity(self) -> None:
        assert style_take_profit_r_multiple(1.0, None, StyleSoftConfig()) == 1.0

    def test_string_style_accepted(self) -> None:
        assert style_take_profit_r_multiple(2.0, "value", StyleSoftConfig()) == 3.0

    def test_mult_below_one_rejected(self) -> None:
        """The value style may only WIDEN the band, never tighten it."""
        with pytest.raises(ValueError, match="never tightens"):
            StyleSoftConfig(value_take_profit_r_mult=0.8)


def _eval_drawdown(style: str | None):
    """Deep single-bar drawdown vs prev_close → DRAWDOWN_STOP fires."""
    return evaluate_intraday_sell_intents(
        {"600519": _spot(price=8.5, prev_close=10.0)},
        {"600519": tuple(10.0 for _ in range(30))},
        (_pos(cost=10.0, style=style),),
        account=_account(),
        style_by_code={"600519": style} if style else None,
        style_soft=StyleSoftConfig(),
    )


class TestProtectiveStopsAreStyleInvariant:
    """Adversarial: a protective SELL is bit-identical across any style label."""

    def test_drawdown_stop_identical_across_styles(self) -> None:
        value = _eval_drawdown(StyleTag.VALUE.value)
        short = _eval_drawdown(StyleTag.SHORT_TERM.value)
        none = _eval_drawdown(None)
        assert value and value[0].trigger_kind is IntradayTriggerKind.DRAWDOWN_STOP
        # Every field is identical — the protective intent carries NO style.
        assert value == short == none
        assert value[0].style is None

    def test_protective_intent_never_carries_style(self) -> None:
        for style in (StyleTag.VALUE.value, StyleTag.SHORT_TERM.value, None):
            for it in _eval_drawdown(style):
                if it.trigger_kind is not IntradayTriggerKind.TAKE_PROFIT:
                    assert it.style is None


def _eval_tp(style: str | None, *, price: float):
    return evaluate_intraday_sell_intents(
        {"600519": _spot(price=price, prev_close=_CLOSES[-1])},
        {"600519": _CLOSES},
        (_pos(cost=_CLOSES[-1], style=style),),
        account=_account(),
        style_by_code={"600519": style} if style else None,
        style_soft=StyleSoftConfig(),
    )


def _tp(intents):
    return [i for i in intents if i.trigger_kind is IntradayTriggerKind.TAKE_PROFIT]


class TestValueWidensTakeProfitBand:
    def test_value_band_is_higher_than_short_term(self) -> None:
        """At a price clearing both targets, VALUE's target + recorded r are
        1.5× the SHORT_TERM band (it harvests later / lets the winner run)."""
        price = _CLOSES[-1] * 2.0  # well above either target
        short = _tp(_eval_tp(StyleTag.SHORT_TERM.value, price=price))
        value = _tp(_eval_tp(StyleTag.VALUE.value, price=price))
        assert short and value
        assert value[0].stop_level > short[0].stop_level  # wider target
        assert value[0].effective_r_multiple == pytest.approx(
            1.5 * short[0].effective_r_multiple
        )
        assert short[0].style == "short_term"
        assert value[0].style == "value"

    def test_value_still_running_at_short_term_target(self) -> None:
        """A price that triggers the SHORT_TERM TP leaves VALUE still holding."""
        # Find a price that clears short (1.0R) but not value (1.5R).
        cost = _CLOSES[-1]
        short = _tp(_eval_tp(StyleTag.SHORT_TERM.value, price=cost * 1.5))
        assert short, "short-term should take profit"
        short_target = short[0].stop_level
        value_target = cost + (short_target - cost) * 1.5
        mid = (short_target + value_target) / 2
        short_mid = _tp(_eval_tp(StyleTag.SHORT_TERM.value, price=mid))
        value_mid = _tp(_eval_tp(StyleTag.VALUE.value, price=mid))
        assert short_mid, "short-term takes profit at the mid price"
        assert not value_mid, "value still running (wider band)"


class TestSoftExitReplayMetadata:
    def test_weight_trim_carries_widened_band_for_value(self) -> None:
        """codex P2: when a VALUE band skips TP, a lower-priority WEIGHT_TRIM
        records the widened band so a PIT replay reproduces the TP-skip."""
        cost = _CLOSES[-1]
        # Over-allocated holding at a price that clears short TP but not value's.
        short = _tp(_eval_tp(StyleTag.SHORT_TERM.value, price=cost * 1.5))
        assert short
        short_target = short[0].stop_level
        value_target = cost + (short_target - cost) * 1.5
        mid = (short_target + value_target) / 2
        # Big position → over the 15%×1.1 trim band → WEIGHT_TRIM fires.
        pos = Position(
            code="600519",
            volume=50_000,
            available_volume=50_000,
            cost_price=cost,
            market_value=cost * 50_000,
            unrealized_pnl=0.0,
            unrealized_pnl_pct=0.0,
            entry_style="value",
        )
        acct = AccountInfo(
            total_assets=1_000_000.0,
            available_cash=0.0,
            frozen_cash=0.0,
            market_value=cost * 50_000,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            initial_capital=1_000_000.0,
        )
        intents = evaluate_intraday_sell_intents(
            {"600519": _spot(price=mid, prev_close=cost)},
            {"600519": _CLOSES},
            (pos,),
            account=acct,
            style_by_code={"600519": "value"},
            style_soft=StyleSoftConfig(),
        )
        trims = [
            i for i in intents
            if i.trigger_kind is IntradayTriggerKind.WEIGHT_TRIM
        ]
        assert trims, "an over-allocated holding should trim when TP is skipped"
        # The trim records the widened (1.5×) band, not the base short-term band.
        assert trims[0].effective_r_multiple == pytest.approx(1.5)


class TestHardCapTrimIsStyleInvariant:
    def test_long_term_hard_cap_trim_identical_across_styles(self) -> None:
        """codex verify P2: a long-term hold's HARD-CAP trim records the
        style-invariant eff_r — the band only conditions an evaluated TP-skip,
        never the exemption-suppressed hard cap."""
        cost = _CLOSES[-1]
        acct = AccountInfo(
            total_assets=1_000_000.0,
            available_cash=0.0,
            frozen_cash=0.0,
            market_value=cost * 50_000,
            total_pnl=0.0,
            total_pnl_pct=0.0,
            initial_capital=1_000_000.0,
        )

        def run(style: str):
            pos = Position(
                code="600519",
                volume=50_000,
                available_volume=50_000,
                cost_price=cost,
                market_value=cost * 50_000,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
                entry_style=style,
            )
            return evaluate_intraday_sell_intents(
                {"600519": _spot(price=cost * 2.0, prev_close=cost)},
                {"600519": _CLOSES},
                (pos,),
                account=acct,
                # Long-term hold → TP exempt; only the hard-cap trim can fire.
                long_term_hold_codes=frozenset({"600519"}),
                style_by_code={"600519": style},
                style_soft=StyleSoftConfig(),
            )

        value = run("value")
        short = run("short_term")
        # The hard-cap trim is bit-identical across styles (no TP intent, no
        # style on it, base eff_r recorded regardless of the VALUE band).
        assert value and value[0].trigger_kind is IntradayTriggerKind.WEIGHT_TRIM
        assert value == short
        assert value[0].style is None
        assert value[0].effective_r_multiple == 1.0


class TestBitIdenticalWhenOff:
    def test_no_style_soft_reproduces_legacy(self) -> None:
        spots = {"600519": _spot(price=_CLOSES[-1] * 2.0, prev_close=_CLOSES[-1])}
        positions = (_pos(cost=_CLOSES[-1], style="value"),)
        legacy = evaluate_intraday_sell_intents(
            spots, {"600519": _CLOSES}, positions, account=_account()
        )
        with_none = evaluate_intraday_sell_intents(
            spots, {"600519": _CLOSES}, positions, account=_account(),
            style_by_code={"600519": "value"}, style_soft=None,
        )
        assert legacy == with_none
        for it in legacy:
            assert it.style is None
