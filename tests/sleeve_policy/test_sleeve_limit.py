"""AF-005 — sleeve_limit_for: SleevePolicy → RiskEngine check#6 SleeveLimit."""

from __future__ import annotations

from backend.services.sleeve_resolver import sleeve_limit_for
from backend.sleeve_policy.policy import (
    GlidePoint,
    SleeveCaps,
    SleevePolicy,
    SleevePolicyConfig,
)
from backend.style.models import StyleTag


def _cfg(enabled: bool = True) -> SleevePolicyConfig:
    return SleevePolicyConfig(
        enabled=enabled,
        activate_total_equity_yuan=50000.0,
        short_working_floor_yuan=40000.0,
        caps=SleeveCaps(short_max_positions=5, value_max_positions=3),
        glide_path=(
            GlidePoint(50000.0, 0.20),
            GlidePoint(100000.0, 0.40),
            GlidePoint(300000.0, 0.60),
        ),
    )


def test_dormant_switch_off_is_none() -> None:
    policy = SleevePolicy(_cfg(enabled=False))
    assert sleeve_limit_for(policy, StyleTag.VALUE, 1_000_000.0) is None


def test_below_trigger_not_latched_is_none() -> None:
    policy = SleevePolicy(_cfg(enabled=True))
    assert sleeve_limit_for(policy, StyleTag.VALUE, 9_000.0) is None


def test_active_value_order() -> None:
    policy = SleevePolicy(_cfg(enabled=True))
    limit = sleeve_limit_for(policy, StyleTag.VALUE, 200_000.0)
    assert limit is not None
    assert limit.order_sleeve == "value"
    assert limit.value_style_token == "value"
    assert limit.value_cap == 3
    assert limit.short_cap == 5
    assert limit.cap_for_order() == 3


def test_active_short_order() -> None:
    policy = SleevePolicy(_cfg(enabled=True))
    limit = sleeve_limit_for(policy, StyleTag.SHORT_TERM, 200_000.0)
    assert limit is not None
    assert limit.order_sleeve == "short"
    assert limit.cap_for_order() == 5


def test_latched_below_trigger_still_active() -> None:
    policy = SleevePolicy(_cfg(enabled=True))
    # A prior-latched account that dipped below the trigger stays active.
    limit = sleeve_limit_for(policy, StyleTag.VALUE, 30_000.0, latched=True)
    assert limit is not None
    assert limit.order_sleeve == "value"
