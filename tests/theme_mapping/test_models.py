"""AF-001 — PolicyTheme / PolicyThemeRegistry validation (fail-closed)."""

from __future__ import annotations

import pytest

from backend.theme_mapping.models import (
    PolicyTheme,
    PolicyThemeRegistry,
    tier_from_name,
)
from backend.theme_research.sop_schema import ThemeTier


def _theme(**over) -> PolicyTheme:
    base = dict(
        theme_id="semis",
        name_cn="集成电路自主可控",
        tier=ThemeTier.NATIONAL_EVENT,
        effective_from="20150519",
        policy_source="《中国制造2025》(2015-05-19)",
        sw_l3_codes=("850816.SI",),
    )
    base.update(over)
    return PolicyTheme(**base)  # type: ignore[arg-type]


def test_valid_theme_builds() -> None:
    t = _theme()
    assert t.is_active("20150519") and t.is_active("20200101")
    assert not t.is_active("20150518")  # day before effective_from = no tilt


def test_tier_from_name_known_and_unknown() -> None:
    assert tier_from_name("national_event") is ThemeTier.NATIONAL_EVENT
    assert tier_from_name("stock") is ThemeTier.STOCK
    with pytest.raises(ValueError, match="unknown theme tier"):
        tier_from_name("megatrend")


@pytest.mark.parametrize(
    "over, match",
    [
        ({"theme_id": "  "}, "theme_id"),
        ({"name_cn": ""}, "name_cn"),
        ({"effective_from": "2015-05-19"}, "YYYYMMDD"),
        ({"effective_from": "201505"}, "YYYYMMDD"),
        ({"policy_source": ""}, "policy_source"),
        ({"sw_l3_codes": ()}, "non-empty"),
        ({"sw_l3_codes": ("850816",)}, "NNNNNN.SI"),
        ({"sw_l3_codes": ("ABCDEF.SI",)}, "NNNNNN.SI"),
    ],
)
def test_malformed_theme_fails_closed(over: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _theme(**over)


def test_registry_rejects_empty_and_duplicates() -> None:
    with pytest.raises(ValueError, match="at least one theme"):
        PolicyThemeRegistry(version="v", frozen=True, themes=())
    with pytest.raises(ValueError, match="duplicate theme_id"):
        PolicyThemeRegistry(
            version="v",
            frozen=True,
            themes=(_theme(), _theme(sw_l3_codes=("850817.SI",))),
        )


def test_registry_active_filters_by_effective_from() -> None:
    early = _theme(theme_id="a", effective_from="20150519")
    late = _theme(theme_id="b", effective_from="20251028")
    reg = PolicyThemeRegistry(version="v", frozen=True, themes=(early, late))
    assert {t.theme_id for t in reg.active("20200101")} == {"a"}
    assert {t.theme_id for t in reg.active("20260101")} == {"a", "b"}
    assert reg.active("20100101") == ()


def test_l3_to_themes_inverts_mapping() -> None:
    a = _theme(theme_id="a", sw_l3_codes=("850816.SI", "850818.SI"))
    b = _theme(theme_id="b", sw_l3_codes=("850818.SI",))
    reg = PolicyThemeRegistry(version="v", frozen=True, themes=(a, b))
    inv = reg.l3_to_themes()
    assert inv["850816.SI"] == ("a",)
    assert inv["850818.SI"] == ("a", "b")  # shared L3, registry order
