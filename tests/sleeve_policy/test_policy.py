"""AF-005 — value-sleeve capital allocation (activation latch, glide path, caps)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.sleeve_policy.policy import (
    DEFAULT_SLEEVE_POLICY_PATH,
    GlidePoint,
    Sleeve,
    SleeveCaps,
    SleevePolicy,
    SleevePolicyConfig,
    SleevePolicyError,
    load_sleeve_policy_config,
)
from backend.style.models import StyleTag


def _cfg(enabled: bool = True, floor: float = 40000.0) -> SleevePolicyConfig:
    return SleevePolicyConfig(
        enabled=enabled,
        activate_total_equity_yuan=50000.0,
        short_working_floor_yuan=floor,
        caps=SleeveCaps(short_max_positions=5, value_max_positions=3),
        glide_path=(
            GlidePoint(50000.0, 0.20),
            GlidePoint(100000.0, 0.40),
            GlidePoint(300000.0, 0.60),
        ),
    )


# ── activation ────────────────────────────────────────────────────────────
def test_disabled_master_switch_is_always_dormant() -> None:
    p = SleevePolicy(_cfg(enabled=False))
    assert p.is_value_sleeve_active(1_000_000.0) is False
    assert p.is_value_sleeve_active(1_000_000.0, latched=True) is False
    assert p.value_target_capital_yuan(1_000_000.0, latched=True) == 0.0
    assert p.cap_for(Sleeve.VALUE, 1_000_000.0) == 0  # no value sub-account


def test_activation_trigger_and_one_way_latch() -> None:
    p = SleevePolicy(_cfg(enabled=True))
    assert p.is_value_sleeve_active(49_999.0) is False
    assert p.is_value_sleeve_active(50_000.0) is True  # trigger inclusive
    # latched: a dip below the trigger stays active (never force-liquidate)
    assert p.is_value_sleeve_active(30_000.0, latched=True) is True
    assert p.is_value_sleeve_active(30_000.0, latched=False) is False


# ── glide path + working floor ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "equity, expected",
    [
        (50_000.0, 10_000.0),  # 20% weight, floor binds (50k-40k=10k)
        (60_000.0, 12_000.0),  # 20% weight (12k) < headroom (20k)
        (100_000.0, 40_000.0),  # 40% weight (40k) < headroom (60k)
        (300_000.0, 180_000.0),  # 60% weight (180k) < headroom (260k)
        (1_000_000.0, 600_000.0),
    ],
)
def test_value_target_capital_glide(equity: float, expected: float) -> None:
    p = SleevePolicy(_cfg(enabled=True))
    assert p.value_target_capital_yuan(equity) == pytest.approx(expected)


def test_latched_below_trigger_targets_zero_stop_adding() -> None:
    p = SleevePolicy(_cfg(enabled=True))
    # active via latch but below the lowest breakpoint → weight 0 → target 0
    assert p.is_value_sleeve_active(45_000.0, latched=True) is True
    assert p.value_target_capital_yuan(45_000.0, latched=True) == 0.0


def test_short_working_floor_caps_value_target() -> None:
    p = SleevePolicy(_cfg(enabled=True, floor=45_000.0))
    # equity 50k, 20% weight = 10k, but floor leaves only 5k headroom
    assert p.value_target_capital_yuan(50_000.0) == pytest.approx(5_000.0)


# ── sleeve assignment + caps ────────────────────────────────────────────────
def test_assign_sleeve_by_style() -> None:
    p = SleevePolicy(_cfg())
    assert p.assign_sleeve(StyleTag.VALUE) is Sleeve.VALUE
    assert p.assign_sleeve(StyleTag.SHORT_TERM) is Sleeve.SHORT


def test_per_sleeve_caps_active_vs_dormant() -> None:
    p = SleevePolicy(_cfg(enabled=True))
    # active (equity ≥ 50k): short ≤5, value ≤3
    assert p.cap_for(Sleeve.SHORT, 60_000.0) == 5
    assert p.cap_for(Sleeve.VALUE, 60_000.0) == 3
    # dormant (equity < 50k): single ≤5 pool, no value sub-account
    assert p.cap_for(Sleeve.SHORT, 9_000.0) == 5
    assert p.cap_for(Sleeve.VALUE, 9_000.0) == 0


def test_position_admissible_respects_cap() -> None:
    p = SleevePolicy(_cfg(enabled=True))
    assert p.position_admissible(Sleeve.VALUE, 2, 60_000.0) is True
    assert p.position_admissible(Sleeve.VALUE, 3, 60_000.0) is False  # at cap
    assert p.position_admissible(Sleeve.SHORT, 4, 60_000.0) is True
    assert p.position_admissible(Sleeve.SHORT, 5, 60_000.0) is False
    # dormant: value never admissible (cap 0)
    assert p.position_admissible(Sleeve.VALUE, 0, 9_000.0) is False


# ── config validation (fail-closed) ─────────────────────────────────────────
@pytest.mark.parametrize(
    "kw, match",
    [
        ({"activate_total_equity_yuan": 0.0}, "activate_total_equity"),
        ({"short_working_floor_yuan": 60000.0}, "short_working_floor"),
        ({"caps": SleeveCaps(0, 3)}, "caps must be"),
        ({"glide_path": ()}, "non-empty"),
        ({"glide_path": (GlidePoint(50000.0, 1.5),)}, "not in"),
        (
            {"glide_path": (GlidePoint(100000.0, 0.4), GlidePoint(50000.0, 0.2))},
            "strictly increase",
        ),
        (
            {"glide_path": (GlidePoint(50000.0, 0.4), GlidePoint(100000.0, 0.2))},
            "non-decreasing",
        ),
        ({"glide_path": (GlidePoint(40000.0, 0.2),)}, "first glide breakpoint"),
    ],
)
def test_config_validation_fails_closed(kw: dict, match: str) -> None:
    base = dict(
        enabled=True,
        activate_total_equity_yuan=50000.0,
        short_working_floor_yuan=40000.0,
        caps=SleeveCaps(5, 3),
        glide_path=(GlidePoint(50000.0, 0.20), GlidePoint(100000.0, 0.40)),
    )
    base.update(kw)
    with pytest.raises(SleevePolicyError, match=match):
        SleevePolicyConfig(**base)  # type: ignore[arg-type]


# ── real shipped config ─────────────────────────────────────────────────────
def test_real_config_loads_and_is_dormant() -> None:
    cfg = load_sleeve_policy_config(DEFAULT_SLEEVE_POLICY_PATH)
    assert cfg.enabled is False  # ships OFF → byte-identical until owner enables
    assert cfg.activate_total_equity_yuan == 50000.0
    assert cfg.caps.short_max_positions == 5 and cfg.caps.value_max_positions == 3
    p = SleevePolicy(cfg)
    # current sim equity ~¥9k + switch off → fully dormant
    assert p.is_value_sleeve_active(9_000.0) is False
    assert p.value_target_capital_yuan(9_000.0) == 0.0


def test_load_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_sleeve_policy_config(tmp_path / "nope.yaml")


def test_load_rejects_bad_enabled_and_caps(tmp_path: Path) -> None:
    bad = tmp_path / "sleeve.yaml"
    bad.write_text(
        "enabled: maybe\nactivate_total_equity_yuan: 50000\n"
        "short_working_floor_yuan: 40000\ncaps: {short_max_positions: 5, "
        "value_max_positions: 3}\nglide_path: [[50000, 0.2]]\n",
        encoding="utf-8",
    )
    with pytest.raises(SleevePolicyError, match="enabled must be a bool"):
        load_sleeve_policy_config(bad)


@pytest.mark.parametrize(
    "floor_line",
    [
        "short_working_floor_yuan: true",  # YAML bool (=1) must not slip through
        'short_working_floor_yuan: "40000"',  # numeric string rejected
        "",  # missing → not silently 0.0
    ],
)
def test_load_validates_short_working_floor_fail_closed(
    tmp_path: Path, floor_line: str
) -> None:
    bad = tmp_path / "sleeve.yaml"
    bad.write_text(
        "enabled: false\nactivate_total_equity_yuan: 50000\n"
        f"{floor_line}\ncaps: {{short_max_positions: 5, value_max_positions: 3}}\n"
        "glide_path: [[50000, 0.2]]\n",
        encoding="utf-8",
    )
    with pytest.raises(SleevePolicyError, match="short_working_floor_yuan"):
        load_sleeve_policy_config(bad)
