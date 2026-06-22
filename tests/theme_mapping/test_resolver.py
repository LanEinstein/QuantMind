"""AF-001 — ThemeResolver: anti-hindsight gates + tier-weighted theme_coverage."""

from __future__ import annotations

import pandas as pd
import pytest

from backend.theme_mapping.models import PolicyTheme, PolicyThemeRegistry
from backend.theme_mapping.resolver import ThemeResolver
from backend.theme_mapping.sector_pit import SectorMembershipPIT
from backend.theme_research.sop_schema import ThemeTier


def _membership(rows: list[tuple[str, str, str, str]]) -> SectorMembershipPIT:
    frame = pd.DataFrame(
        rows, columns=["ts_code", "l3_code", "in_date", "out_date"]
    ).astype(str)
    return SectorMembershipPIT.from_frame(frame)


def _registry() -> PolicyThemeRegistry:
    return PolicyThemeRegistry(
        version="t",
        frozen=True,
        themes=(
            PolicyTheme(
                "semis",
                "集成电路",
                ThemeTier.NATIONAL_EVENT,
                "20150519",
                "中国制造2025",
                ("850816.SI",),
            ),
            PolicyTheme(
                "newnrg", "新能源", ThemeTier.POLICY, "20200922", "双碳", ("857352.SI",)
            ),
            PolicyTheme(
                "highdiv",
                "高股息",
                ThemeTier.TECH,
                "20221121",
                "中特估",
                ("857821.SI",),
            ),
        ),
    )


def test_active_theme_ids_respect_effective_from() -> None:
    r = ThemeResolver(_registry(), _membership([]))
    assert r.active_theme_ids("20160101") == frozenset({"semis"})
    assert r.active_theme_ids("20210101") == frozenset({"semis", "newnrg"})
    assert r.active_theme_ids("20230101") == frozenset({"semis", "newnrg", "highdiv"})
    assert r.active_theme_ids("20100101") == frozenset()


def test_theme_coverage_is_max_tier_weight() -> None:
    m = _membership(
        [
            ("CHIP.SH", "850816.SI", "20100101", ""),  # national_event → 1.0
            ("SOLAR.SH", "857352.SI", "20100101", ""),  # policy → 0.75
            ("BANK.SH", "857821.SI", "20100101", ""),  # tech → 0.5
            ("OFF.SH", "850999.SI", "20100101", ""),  # off every theme → 0.0
        ]
    )
    r = ThemeResolver(_registry(), m)
    assert r.theme_coverage("CHIP.SH", "20230101") == 1.0
    assert r.theme_coverage("SOLAR.SH", "20230101") == 0.75
    assert r.theme_coverage("BANK.SH", "20230101") == 0.5
    assert r.theme_coverage("OFF.SH", "20230101") == 0.0  # has L3, off every theme
    assert r.theme_coverage("UNKNOWN.SH", "20230101") is None  # no PIT L3 = data gap


def test_anti_hindsight_no_coverage_before_effective_from() -> None:
    m = _membership([("SOLAR.SH", "857352.SI", "20100101", "")])
    r = ThemeResolver(_registry(), m)
    # member of the L3 since 2010, but the policy (双碳) only exists from 2020-09-22
    assert r.theme_coverage("SOLAR.SH", "20180101") == 0.0
    assert r.code_theme_ids("SOLAR.SH", "20180101") == ()
    assert r.theme_coverage("SOLAR.SH", "20210101") == 0.75


def test_pit_membership_gate_no_coverage_before_in_date() -> None:
    m = _membership([("CHIP.SH", "850816.SI", "20190101", "")])
    r = ThemeResolver(_registry(), m)
    # theme active in 2016, but the code only joined the L3 in 2019
    assert r.theme_coverage("CHIP.SH", "20160101") is None  # no PIT L3 before in_date
    assert r.theme_coverage("CHIP.SH", "20200101") == 1.0


def test_code_in_multiple_themes_takes_highest_tier() -> None:
    reg = PolicyThemeRegistry(
        version="t",
        frozen=True,
        themes=(
            PolicyTheme("a", "战新", ThemeTier.POLICY, "20150101", "p", ("850816.SI",)),
            PolicyTheme(
                "b", "卡脖子", ThemeTier.NATIONAL_EVENT, "20150101", "p", ("850816.SI",)
            ),
        ),
    )
    m = _membership([("DUAL.SH", "850816.SI", "20100101", "")])
    r = ThemeResolver(reg, m)
    assert set(r.code_theme_ids("DUAL.SH", "20200101")) == {"a", "b"}
    assert r.theme_coverage("DUAL.SH", "20200101") == 1.0  # max(0.75, 1.0)


def test_resolver_is_deterministic() -> None:
    m = _membership([("CHIP.SH", "850816.SI", "20100101", "")])
    r = ThemeResolver(_registry(), m)
    a = [r.theme_coverage("CHIP.SH", "20230101") for _ in range(5)]
    assert a == [1.0] * 5


def _draft_registry() -> PolicyThemeRegistry:
    return PolicyThemeRegistry(
        version="d",
        frozen=False,
        themes=(
            PolicyTheme(
                "semis",
                "集成电路",
                ThemeTier.NATIONAL_EVENT,
                "20150519",
                "中国制造2025",
                ("850816.SI",),
            ),
        ),
    )


def test_resolver_refuses_draft_registry_unless_opted_in() -> None:
    m = _membership([("CHIP.SH", "850816.SI", "20100101", "")])
    with pytest.raises(ValueError, match="non-frozen"):
        ThemeResolver(_draft_registry(), m)
    # explicit opt-in (development/testing only) is allowed
    r = ThemeResolver(_draft_registry(), m, allow_draft=True)
    assert r.theme_coverage("CHIP.SH", "20200101") == 1.0


@pytest.mark.parametrize("bad", ["2026-06-22", "2026", "", "2026062", "20260622 "])
def test_resolver_rejects_malformed_decision_date(bad: str) -> None:
    m = _membership([("CHIP.SH", "850816.SI", "20100101", "")])
    r = ThemeResolver(_registry(), m)
    for call in (
        r.active_theme_ids,
        lambda d: r.code_theme_ids("CHIP.SH", d),
        lambda d: r.theme_coverage("CHIP.SH", d),
    ):
        with pytest.raises(ValueError, match="YYYYMMDD"):
            call(bad)
