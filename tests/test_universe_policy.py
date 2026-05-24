"""Tests for backend.services.universe_policy (P0-9 v3 schema).

P0-9-amendment-2026-05-24 tore out the fixed 13-code lock and replaced it
with a full-market *ruleset* universe = board whitelist + forbidden board
set + the (unchanged) four exclusion rules + long-only. These tests lock
the v3 schema: v2 (13-code) YAML must be rejected, the board whitelist /
forbidden set must equal their locked values, and cap / exclusion /
direction invariants are unchanged.
"""

from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.services.universe_policy import (
    BOARD_WHITELIST,
    FORBIDDEN_BOARDS,
    FORBIDDEN_SIDES,
    LOCKED_EVENT_CAP,
    LOCKED_TOTAL_DAILY_CAP,
    LOCKED_TRADITIONAL_CAP,
    BucketConfig,
    CapAllocation,
    DirectionPolicy,
    ExclusionRules,
    UniversePolicy,
    UniversePolicyError,
    UniverseRules,
    assign_category,
    load_policy,
    partition_watchlist,
)

VALID_YAML = """
policy_version: 3
locked_decision: P0-9
last_updated: 2026-05-24

fast:
  cron: "0 9,11,13,15 * * mon-fri"
  pipeline: fast_pipeline
  max_debate_rounds: 1
  pipeline_timeout_seconds: 480
  default_codes: ["600519"]

slow:
  cron: "0 9 * * mon-fri"
  pipeline: slow_pipeline
  max_debate_rounds: 2
  pipeline_timeout_seconds: 900
  default_codes:
    - "000858"

overrides:
  "600519": fast

universe:
  board_whitelist:
    - sh_main
    - sz_main
    - chuangye
    - etf
  forbidden_boards:
    - kechuang_688
    - beijiao_8
    - st
    - convertible_bond

exclusion_rules:
  ipo_min_trading_days: 30
  sub_new_min_trading_days: 180
  min_avg_amount_20d_yuan: 200000000
  max_unit_price_yuan: 500.0

cap_allocation:
  total_daily_cap: 5
  traditional_path_default_cap: 4
  event_path_reserved_cap: 1
  reserved_cap_release_time: "14:30"

direction_policy:
  long_only: true
  forbidden_sides:
    - SHORT
    - COVER
    - MARGIN_BUY
    - REVERSE_REPO
    - ETF_SUBSCRIBE
    - ETF_REDEEM
  etf_arbitrage_enabled: false
"""


@pytest.fixture()
def policy_path(tmp_path: Path) -> Path:
    p = tmp_path / "universe_policy.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def policy(policy_path: Path) -> UniversePolicy:
    return load_policy(policy_path)


def _write_with(tmp_path: Path, mutation: tuple[str, str]) -> Path:
    """Helper: produce a YAML variant by string substitution."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(VALID_YAML.replace(*mutation), encoding="utf-8")
    return bad


class TestLoadPolicyHappyPath:
    @pytest.mark.unit
    def test_loads_valid_yaml(self, policy: UniversePolicy) -> None:
        assert policy.fast.max_debate_rounds == 1
        assert policy.fast.pipeline_timeout_seconds == 480
        assert policy.slow.max_debate_rounds == 2
        assert policy.slow.pipeline_timeout_seconds == 900
        assert policy.fast.cron == "0 9,11,13,15 * * mon-fri"
        assert policy.slow.cron == "0 9 * * mon-fri"
        assert policy.policy_version == 3
        assert policy.locked_decision == "P0-9"
        assert policy.last_updated == "2026-05-24"
        assert policy.overrides == {"600519": "fast"}
        assert policy.default_category == "slow"

    @pytest.mark.unit
    def test_universe_ruleset_locked(self, policy: UniversePolicy) -> None:
        assert policy.universe.board_whitelist == BOARD_WHITELIST
        assert policy.universe.forbidden_boards == FORBIDDEN_BOARDS

    @pytest.mark.unit
    def test_is_board_whitelisted(self, policy: UniversePolicy) -> None:
        for board in ("sh_main", "sz_main", "chuangye", "etf"):
            assert policy.is_board_whitelisted(board) is True
        # A forbidden / unknown board is never whitelisted.
        assert policy.is_board_whitelisted("kechuang_688") is False
        assert policy.is_board_whitelisted("beijiao_8") is False
        assert policy.is_board_whitelisted("nasdaq") is False

    @pytest.mark.unit
    def test_exclusion_rules(self, policy: UniversePolicy) -> None:
        assert policy.exclusion_rules.ipo_min_trading_days == 30
        assert policy.exclusion_rules.sub_new_min_trading_days == 180
        assert policy.exclusion_rules.min_avg_amount_20d_yuan == 200_000_000
        assert policy.exclusion_rules.max_unit_price_yuan == 500.0

    @pytest.mark.unit
    def test_cap_allocation(self, policy: UniversePolicy) -> None:
        assert policy.cap_allocation.total_daily_cap == LOCKED_TOTAL_DAILY_CAP
        assert (
            policy.cap_allocation.traditional_path_default_cap
            == LOCKED_TRADITIONAL_CAP
        )
        assert policy.cap_allocation.event_path_reserved_cap == LOCKED_EVENT_CAP
        assert policy.cap_allocation.reserved_cap_release_time == "14:30"

    @pytest.mark.unit
    def test_direction_policy(self, policy: UniversePolicy) -> None:
        assert policy.direction_policy.long_only is True
        assert policy.direction_policy.forbidden_sides == FORBIDDEN_SIDES
        assert policy.direction_policy.etf_arbitrage_enabled is False

    @pytest.mark.unit
    def test_all_watchlist_codes_union(self, policy: UniversePolicy) -> None:
        # Only manually-pinned codes count now (no enumerated universe).
        assert policy.all_watchlist_codes() == frozenset({"600519", "000858"})

    @pytest.mark.unit
    def test_all_watchlist_codes_empty_when_no_pins(
        self, tmp_path: Path
    ) -> None:
        """Full-market default: no pinned codes → empty set."""
        bad = _write_with(
            tmp_path,
            ('default_codes: ["600519"]', "default_codes: []"),
        )
        bad_text = bad.read_text(encoding="utf-8")
        # Also clear the slow pin + the now-dangling override.
        bad_text = bad_text.replace('    - "000858"\n', "")
        bad_text = bad_text.replace('overrides:\n  "600519": fast\n', "overrides: {}\n")
        bad.write_text(bad_text, encoding="utf-8")
        loaded = load_policy(bad)
        assert loaded.all_watchlist_codes() == frozenset()


class TestLoadPolicySchemaRejections:
    @pytest.mark.unit
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_policy(tmp_path / "nope.yaml")

    @pytest.mark.unit
    def test_v2_schema_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("policy_version: 3", "policy_version: 2"))
        with pytest.raises(UniversePolicyError, match="policy_version"):
            load_policy(bad)

    @pytest.mark.unit
    def test_v1_schema_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("policy_version: 3", "policy_version: 1"))
        with pytest.raises(UniversePolicyError, match="policy_version"):
            load_policy(bad)

    @pytest.mark.unit
    def test_wrong_locked_decision_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path, ("locked_decision: P0-9", "locked_decision: P0-7")
        )
        with pytest.raises(UniversePolicyError, match="locked_decision"):
            load_policy(bad)

    @pytest.mark.unit
    def test_missing_section_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "policy_version: 3\nlocked_decision: P0-9\n", encoding="utf-8"
        )
        with pytest.raises(UniversePolicyError, match="missing required section"):
            load_policy(bad)

    @pytest.mark.unit
    def test_missing_universe_section_rejected(self, tmp_path: Path) -> None:
        """A v2 file (no universe section) must fail fast at boot."""
        bad = tmp_path / "bad.yaml"
        # Strip the entire universe block.
        text = VALID_YAML
        start = text.index("universe:")
        end = text.index("exclusion_rules:")
        bad.write_text(text[:start] + text[end:], encoding="utf-8")
        with pytest.raises(UniversePolicyError, match="missing required section"):
            load_policy(bad)

    @pytest.mark.unit
    def test_malformed_yaml_wraps_to_policy_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "broken.yaml"
        bad.write_text("fast: [unclosed", encoding="utf-8")
        with pytest.raises(UniversePolicyError, match="not valid YAML"):
            load_policy(bad)

    @pytest.mark.unit
    def test_overlapping_default_codes_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ('default_codes: ["600519"]', 'default_codes: ["000858"]'),
        )
        with pytest.raises(UniversePolicyError, match="both fast.default_codes"):
            load_policy(bad)

    @pytest.mark.unit
    def test_negative_timeout_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("pipeline_timeout_seconds: 480", "pipeline_timeout_seconds: 0"),
        )
        with pytest.raises(UniversePolicyError, match="pipeline_timeout_seconds"):
            load_policy(bad)


class TestUniverseRulesetInvariants:
    """P0-9-amendment-2026-05-24 §2.1 — whitelist + forbidden set locked."""

    @pytest.mark.unit
    def test_whitelist_cannot_widen_to_forbidden_board(
        self, tmp_path: Path
    ) -> None:
        # Add 'kechuang_688' to the whitelist — must be rejected (永禁).
        bad = _write_with(
            tmp_path,
            (
                "    - etf\n  forbidden_boards:",
                "    - etf\n    - kechuang_688\n  forbidden_boards:",
            ),
        )
        with pytest.raises(UniversePolicyError, match="board_whitelist must equal"):
            load_policy(bad)

    @pytest.mark.unit
    def test_whitelist_cannot_drop_a_board(self, tmp_path: Path) -> None:
        # Removing 'etf' from the whitelist drifts from the locked set.
        bad = _write_with(
            tmp_path, ("    - etf\n  forbidden_boards:", "  forbidden_boards:")
        )
        with pytest.raises(UniversePolicyError, match="board_whitelist must equal"):
            load_policy(bad)

    @pytest.mark.unit
    def test_forbidden_boards_cannot_drop_kechuang(self, tmp_path: Path) -> None:
        # Dropping 科创 688 from forbidden_boards must be rejected (永禁).
        bad = _write_with(tmp_path, ("    - kechuang_688\n", ""))
        with pytest.raises(
            UniversePolicyError, match="forbidden_boards must equal"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_universe_missing_key_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        text = VALID_YAML
        start = text.index("  forbidden_boards:")
        end = text.index("exclusion_rules:")
        bad.write_text(text[:start] + text[end:], encoding="utf-8")
        with pytest.raises(UniversePolicyError, match="missing key: forbidden_boards"):
            load_policy(bad)


class TestExclusionRulesInvariants:
    """P0-9 §2.1 thresholds locked exactly (unchanged by the amendment)."""

    @pytest.mark.unit
    def test_ipo_threshold_must_equal_30(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path, ("ipo_min_trading_days: 30", "ipo_min_trading_days: 31")
        )
        with pytest.raises(UniversePolicyError, match="ipo_min_trading_days"):
            load_policy(bad)

    @pytest.mark.unit
    def test_sub_new_threshold_must_equal_180(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("sub_new_min_trading_days: 180", "sub_new_min_trading_days: 179"),
        )
        with pytest.raises(UniversePolicyError, match="sub_new_min_trading_days"):
            load_policy(bad)

    @pytest.mark.unit
    def test_amount_threshold_must_equal_2e8(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            (
                "min_avg_amount_20d_yuan: 200000000",
                "min_avg_amount_20d_yuan: 100000000",
            ),
        )
        with pytest.raises(UniversePolicyError, match="min_avg_amount_20d_yuan"):
            load_policy(bad)

    @pytest.mark.unit
    def test_max_unit_price_must_equal_500(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path, ("max_unit_price_yuan: 500.0", "max_unit_price_yuan: 499.99")
        )
        with pytest.raises(UniversePolicyError, match="max_unit_price_yuan"):
            load_policy(bad)

    @pytest.mark.unit
    def test_max_unit_price_accepts_int_form(self, tmp_path: Path) -> None:
        """YAML int (500) must be accepted as equal to locked float 500.0."""
        bad = _write_with(
            tmp_path, ("max_unit_price_yuan: 500.0", "max_unit_price_yuan: 500")
        )
        policy = load_policy(bad)
        assert policy.exclusion_rules.max_unit_price_yuan == 500.0


class TestCapAllocationInvariants:
    @pytest.mark.unit
    def test_total_cap_locked_to_5(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("total_daily_cap: 5", "total_daily_cap: 6"))
        with pytest.raises(UniversePolicyError, match="total_daily_cap"):
            load_policy(bad)

    @pytest.mark.unit
    def test_traditional_cap_locked_to_4(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("traditional_path_default_cap: 4", "traditional_path_default_cap: 5"),
        )
        with pytest.raises(
            UniversePolicyError, match="traditional_path_default_cap"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_event_cap_locked_to_1(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path, ("event_path_reserved_cap: 1", "event_path_reserved_cap: 2")
        )
        with pytest.raises(UniversePolicyError, match="event_path_reserved_cap"):
            load_policy(bad)

    @pytest.mark.unit
    def test_release_time_must_equal_14_30(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            (
                'reserved_cap_release_time: "14:30"',
                'reserved_cap_release_time: "09:00"',
            ),
        )
        with pytest.raises(UniversePolicyError, match="14:30 slide rule locked"):
            load_policy(bad)


class TestDirectionPolicyInvariants:
    @pytest.mark.unit
    def test_long_only_must_be_true(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("long_only: true", "long_only: false"))
        with pytest.raises(UniversePolicyError, match="long_only"):
            load_policy(bad)

    @pytest.mark.unit
    def test_etf_arbitrage_must_stay_disabled(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("etf_arbitrage_enabled: false", "etf_arbitrage_enabled: true"),
        )
        with pytest.raises(UniversePolicyError, match="etf_arbitrage_enabled"):
            load_policy(bad)

    @pytest.mark.unit
    def test_forbidden_sides_locked_set(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("    - SHORT\n", ""))
        with pytest.raises(UniversePolicyError, match="forbidden_sides"):
            load_policy(bad)


class TestOverrides:
    @pytest.mark.unit
    def test_numeric_override_codes_normalized_to_str(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ('"600519": fast', "600519: fast"))
        p = load_policy(bad)
        assert "600519" in p.overrides
        assert p.overrides["600519"] == "fast"

    @pytest.mark.unit
    def test_invalid_override_category_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ('"600519": fast', '"600519": medium'))
        with pytest.raises(UniversePolicyError, match="must be 'fast' or 'slow'"):
            load_policy(bad)

    @pytest.mark.unit
    def test_dangling_override_rejected(self, tmp_path: Path) -> None:
        """Overrides must reference codes pinned in fast or slow default_codes."""
        bad = _write_with(tmp_path, ('"600519": fast', '"999999": fast'))
        with pytest.raises(
            UniversePolicyError, match="overrides reference codes outside"
        ):
            load_policy(bad)


class TestAssignCategory:
    @pytest.mark.unit
    def test_override_wins(self, policy: UniversePolicy) -> None:
        assert assign_category("600519", policy) == "fast"

    @pytest.mark.unit
    def test_slow_default_codes(self, policy: UniversePolicy) -> None:
        assert assign_category("000858", policy) == "slow"

    @pytest.mark.unit
    def test_unknown_code_falls_back_to_default(self, policy: UniversePolicy) -> None:
        assert assign_category("999999", policy) == "slow"


class TestPartitionWatchlist:
    @pytest.mark.unit
    def test_partition_preserves_order(self, policy: UniversePolicy) -> None:
        codes = ["000858", "600519", "510300", "999999"]
        fast, slow = partition_watchlist(codes, policy)
        # 600519 → fast (via override); rest → slow (default bucket)
        assert fast == ["600519"]
        assert slow == ["000858", "510300", "999999"]

    @pytest.mark.unit
    def test_empty_input(self, policy: UniversePolicy) -> None:
        fast, slow = partition_watchlist([], policy)
        assert fast == []
        assert slow == []


class TestRuntimeImmutability:
    """P0-9 §1.3 forbids runtime mutation — verify helpers are gone + frozen."""

    @pytest.mark.unit
    def test_save_policy_not_exported(self) -> None:
        module = importlib.import_module("backend.services.universe_policy")
        assert not hasattr(module, "save_policy"), (
            "save_policy must not be re-introduced (P0-9 §1.3 runtime-immutable)"
        )

    @pytest.mark.unit
    def test_update_override_not_exported(self) -> None:
        module = importlib.import_module("backend.services.universe_policy")
        assert not hasattr(module, "update_override"), (
            "update_override must not be re-introduced (P0-9 §1.3 runtime-immutable)"
        )

    @pytest.mark.unit
    def test_bucket_config_is_frozen(self) -> None:
        bucket = BucketConfig(
            cron="* * * * *",
            pipeline="x",
            max_debate_rounds=1,
            pipeline_timeout_seconds=60,
        )
        with pytest.raises(FrozenInstanceError):
            bucket.cron = "0 0 * * *"  # type: ignore[misc]

    @pytest.mark.unit
    def test_policy_is_frozen(self, policy: UniversePolicy) -> None:
        with pytest.raises(FrozenInstanceError):
            policy.default_category = "fast"  # type: ignore[misc]

    @pytest.mark.unit
    def test_universe_rules_is_frozen(self) -> None:
        rules = UniverseRules()
        with pytest.raises(FrozenInstanceError):
            rules.board_whitelist = frozenset()  # type: ignore[misc]

    @pytest.mark.unit
    def test_exclusion_rules_is_frozen(self) -> None:
        rules = ExclusionRules()
        with pytest.raises(FrozenInstanceError):
            rules.ipo_min_trading_days = 99  # type: ignore[misc]

    @pytest.mark.unit
    def test_cap_allocation_is_frozen(self) -> None:
        cap = CapAllocation()
        with pytest.raises(FrozenInstanceError):
            cap.total_daily_cap = 99  # type: ignore[misc]

    @pytest.mark.unit
    def test_direction_policy_is_frozen(self) -> None:
        dp = DirectionPolicy()
        with pytest.raises(FrozenInstanceError):
            dp.long_only = False  # type: ignore[misc]


class TestLockedConstants:
    """The module exports locked constants — verify their values."""

    @pytest.mark.unit
    def test_board_whitelist_locked_set(self) -> None:
        assert BOARD_WHITELIST == frozenset(
            {"sh_main", "sz_main", "chuangye", "etf"}
        )

    @pytest.mark.unit
    def test_forbidden_boards_locked_set(self) -> None:
        assert FORBIDDEN_BOARDS == frozenset(
            {"kechuang_688", "beijiao_8", "st", "convertible_bond"}
        )

    @pytest.mark.unit
    def test_whitelist_and_forbidden_disjoint(self) -> None:
        assert BOARD_WHITELIST.isdisjoint(FORBIDDEN_BOARDS)

    @pytest.mark.unit
    def test_locked_total_daily_cap_is_5(self) -> None:
        assert LOCKED_TOTAL_DAILY_CAP == 5

    @pytest.mark.unit
    def test_locked_total_matches_split(self) -> None:
        assert LOCKED_TRADITIONAL_CAP + LOCKED_EVENT_CAP == LOCKED_TOTAL_DAILY_CAP

    @pytest.mark.unit
    def test_forbidden_sides_locked_set(self) -> None:
        assert FORBIDDEN_SIDES == frozenset(
            {
                "SHORT",
                "COVER",
                "MARGIN_BUY",
                "REVERSE_REPO",
                "ETF_SUBSCRIBE",
                "ETF_REDEEM",
            }
        )


class TestShippedYamlMatchesV3:
    """The shipped config/universe_policy.yaml must load + lock the v3 ruleset."""

    @pytest.mark.unit
    def test_shipped_yaml_loads_clean(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        shipped = repo_root / "config" / "universe_policy.yaml"
        if not shipped.exists():
            pytest.skip(f"shipped policy not present: {shipped}")
        loaded = load_policy(shipped)
        assert loaded.policy_version == 3
        assert loaded.locked_decision == "P0-9"
        assert loaded.universe.board_whitelist == BOARD_WHITELIST
        assert loaded.universe.forbidden_boards == FORBIDDEN_BOARDS
        # Full-market default: no hardcoded codes seeded any more.
        assert loaded.all_watchlist_codes() == frozenset()

    @pytest.mark.unit
    def test_shipped_yaml_has_no_13_code_lock(self) -> None:
        """The dead 13-code invariants must be gone from the shipped YAML."""
        repo_root = Path(__file__).resolve().parent.parent
        shipped = repo_root / "config" / "universe_policy.yaml"
        if not shipped.exists():
            pytest.skip(f"shipped policy not present: {shipped}")
        text = shipped.read_text(encoding="utf-8")
        assert "total_codes" not in text
        assert "watchlist_size_must_equal" not in text
        assert "required_etfs" not in text
