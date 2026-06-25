"""AF-001 — config/policy_themes.yaml loader (fail-closed) + the real draft."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.theme_mapping.registry import (
    DEFAULT_POLICY_THEMES_PATH,
    PolicyThemeConfigError,
    load_policy_theme_registry,
)

_GOOD = """
version: "t1"
status: "frozen"
themes:
  - theme_id: semis
    name_cn: 集成电路
    tier: national_event
    effective_from: "20150519"
    policy_source: 中国制造2025
    sw_l3_codes: ["850816.SI", "850818.SI"]
"""


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "policy_themes.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_loads_good_frozen_mapping(tmp_path: Path) -> None:
    reg = load_policy_theme_registry(_write(tmp_path, _GOOD))
    assert reg.frozen is True and reg.version == "t1"
    assert len(reg.themes) == 1 and reg.themes[0].theme_id == "semis"


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PolicyThemeConfigError, match="not found"):
        load_policy_theme_registry(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    "text, match",
    [
        ("version: v\nstatus: live\nthemes: []\n", "status"),
        ("version: v\nstatus: frozen\n", "missing required key 'themes'"),
        ("version: v\nstatus: frozen\nthemes: []\n", "non-empty list"),
        ("status: frozen\nthemes: [{}]\n", "missing required key"),
        ("[]\n", "must be a mapping"),
        (
            "version: v\nstatus: frozen\nthemes:\n  - theme_id: a\n"
            '    name_cn: x\n    tier: bogus\n    effective_from: "20200101"\n'
            '    policy_source: p\n    sw_l3_codes: ["850816.SI"]\n',
            "unknown theme tier",
        ),
        (
            "version: v\nstatus: frozen\nthemes:\n  - theme_id: a\n"
            '    name_cn: x\n    tier: policy\n    effective_from: "2020"\n'
            '    policy_source: p\n    sw_l3_codes: ["850816.SI"]\n',
            "YYYYMMDD",
        ),
        (  # present-but-null value (blank policy_source) must fail closed, not "None"
            "version: v\nstatus: frozen\nthemes:\n  - theme_id: a\n"
            '    name_cn: x\n    tier: policy\n    effective_from: "20200101"\n'
            '    policy_source:\n    sw_l3_codes: ["850816.SI"]\n',
            "missing required key 'policy_source'",
        ),
    ],
)
def test_malformed_config_fails_closed(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(PolicyThemeConfigError, match=match):
        load_policy_theme_registry(_write(tmp_path, text))


def test_real_config_loads_and_is_frozen() -> None:
    """The shipped mapping parses and is owner-frozen (codex PIT-gate passed)."""
    reg = load_policy_theme_registry(DEFAULT_POLICY_THEMES_PATH)
    assert reg.frozen is True, "owner-frozen mapping must present as frozen"
    ids = {t.theme_id for t in reg.themes}
    # national-strategy themes are represented
    assert {"semiconductor_selfsufficiency", "ai_plus_full_chain"} <= ids
    assert "new_energy" in ids
    assert "aerospace" in ids  # v2: renamed from aerospace_commercial
    # v2 codex PIT-gate revisions (owner-confirmed)
    # dropped (BACK-FITTED) → moved to a value factor
    assert "traditional_upgrade_highdiv" not in ids
    assert "low_altitude_economy" not in ids  # merged into aerospace (no distinct L3)
    # no future-industry speculation leaked into the mapping
    assert not any(
        kw in t.name_cn for t in reg.themes for kw in ("量子", "核聚变", "脑机", "6G")
    )
