"""AF-005 — per-sleeve check#6 (SHORT ≤5 / VALUE ≤3) + dormant byte-identical.

The value sleeve splits the position book into two independent sub-accounts. When
a :class:`SleeveLimit` is supplied (sleeve active) check#6 caps each sleeve
independently; ``sleeve_limit=None`` (dormant — every current caller) keeps the
single ≤5 pool, byte-identical. A held position's sleeve is derived from its
existing ``entry_style`` nameplate (no new Position field).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.broker.models import (
    AccountInfo,
    Order,
    OrderDirection,
    OrderType,
    Position,
    RiskConfig,
    load_risk_config,
)
from backend.risk.engine import RiskEngine
from backend.risk.sleeve import SleeveLimit

SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def config() -> RiskConfig:
    return load_risk_config(Path("config/risk.yaml"))


def _now() -> dt.datetime:
    return dt.datetime(2026, 3, 23, 10, 0, tzinfo=SHANGHAI)


def _order(
    code: str = "000001", direction: OrderDirection = OrderDirection.BUY
) -> Order:
    return Order(
        order_id="t",
        code=code,
        price=100.0,
        volume=100,
        direction=direction,
        order_type=OrderType.LIMIT,
        created_at=_now(),
        updated_at=_now(),
    )


def _account() -> AccountInfo:
    return AccountInfo(
        total_assets=1_000_000.0,
        available_cash=500_000.0,
        frozen_cash=0.0,
        market_value=0.0,
        total_pnl=0.0,
        total_pnl_pct=0.0,
        initial_capital=1_000_000.0,
    )


def _pos(code: str, entry_style: str | None = None) -> Position:
    return Position(
        code=code,
        volume=100,
        available_volume=100,
        cost_price=100.0,
        market_value=10_000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        entry_style=entry_style,
    )


def _limit(order_sleeve: str) -> SleeveLimit:
    return SleeveLimit(
        order_sleeve=order_sleeve,
        value_style_token="value",
        value_cap=3,
        short_cap=5,
    )


def _validate(
    config: RiskConfig,
    order: Order,
    positions: tuple[Position, ...],
    sleeve_limit: SleeveLimit | None,
):
    return RiskEngine(config).validate_order(
        order,
        _account(),
        positions,
        prev_close=100.0,
        now=_now(),
        sleeve_limit=sleeve_limit,
    )


# ---- dormant: None → single ≤5 pool, byte-identical -----------------------


def test_dormant_none_keeps_single_pool(config: RiskConfig) -> None:
    # 5 held (mixed styles) + new BUY → rejected by the single ≤5 pool when None.
    positions = (
        *(_pos(f"60051{i}", "value") for i in range(2)),
        *(_pos(f"60052{i}", "short") for i in range(3)),
    )
    r = _validate(config, _order(), positions, None)
    assert not r.passed and r.rule_name == "total_position_limit"


# ---- value sleeve cap (≤3) ------------------------------------------------


def test_value_order_rejected_when_value_full(config: RiskConfig) -> None:
    # 3 VALUE held + a new VALUE BUY → rejected (value sleeve full), even though
    # the combined count is only 3 (well under the short cap).
    positions = tuple(_pos(f"60051{i}", "value") for i in range(3))
    r = _validate(config, _order(), positions, _limit("value"))
    assert not r.passed
    assert r.rule_name == "total_position_limit"
    assert "value" in r.message


def test_value_order_admitted_when_short_full_value_has_room(
    config: RiskConfig,
) -> None:
    # 5 SHORT + 2 VALUE held + a new VALUE BUY → passes (value has room; a full
    # short sleeve never blocks a value entry — independent sub-accounts).
    positions = (
        *(_pos(f"60052{i}", "short") for i in range(5)),
        *(_pos(f"60051{i}", "value") for i in range(2)),
    )
    r = _validate(config, _order(), positions, _limit("value"))
    assert r.passed


# ---- short sleeve cap (≤5) ------------------------------------------------


def test_short_order_rejected_when_short_full(config: RiskConfig) -> None:
    positions = tuple(_pos(f"60052{i}", "short") for i in range(5))
    r = _validate(config, _order(), positions, _limit("short"))
    assert not r.passed
    assert "short" in r.message


def test_short_order_admitted_when_value_full_short_has_room(
    config: RiskConfig,
) -> None:
    # 3 VALUE (value full) + 4 SHORT + a new SHORT BUY → passes (short has room).
    positions = (
        *(_pos(f"60051{i}", "value") for i in range(3)),
        *(_pos(f"60052{i}", "short") for i in range(4)),
    )
    r = _validate(config, _order(), positions, _limit("short"))
    assert r.passed


def test_legacy_none_entry_style_counts_as_short(config: RiskConfig) -> None:
    # 5 held with entry_style None (legacy) → all SHORT → a new SHORT BUY is
    # rejected (short cap), a new VALUE BUY passes (value sleeve empty).
    positions = tuple(_pos(f"60052{i}", None) for i in range(5))
    assert not _validate(config, _order(), positions, _limit("short")).passed
    assert _validate(config, _order(), positions, _limit("value")).passed


# ---- exit / add never trapped --------------------------------------------


def test_sell_skips_sleeve_cap(config: RiskConfig) -> None:
    positions = tuple(_pos(f"60051{i}", "value") for i in range(3))
    r = _validate(
        config,
        _order(code="600510", direction=OrderDirection.SELL),
        positions,
        _limit("value"),
    )
    assert r.passed


def test_add_to_held_skips_sleeve_cap(config: RiskConfig) -> None:
    positions = tuple(_pos(f"60051{i}", "value") for i in range(3))
    r = _validate(config, _order(code="600510"), positions, _limit("value"))
    assert r.passed  # adding to a held code never increases the count


# ---- SleeveLimit validation ----------------------------------------------


def test_sleeve_limit_validation() -> None:
    with pytest.raises(ValueError):
        SleeveLimit(
            order_sleeve="bogus", value_style_token="value", value_cap=3, short_cap=5
        )
    with pytest.raises(ValueError):
        SleeveLimit(
            order_sleeve="value", value_style_token="", value_cap=3, short_cap=5
        )
    with pytest.raises(ValueError):
        SleeveLimit(
            order_sleeve="value", value_style_token="value", value_cap=-1, short_cap=5
        )
    # bool must not pose as an int cap
    with pytest.raises(ValueError):
        SleeveLimit(
            order_sleeve="value", value_style_token="value", value_cap=True, short_cap=5
        )
    with pytest.raises(ValueError):
        SleeveLimit(
            order_sleeve="short", value_style_token="value", value_cap=3, short_cap="5"
        )  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SleeveLimit(
            order_sleeve="short", value_style_token="value", value_cap=3, short_cap=-2
        )
