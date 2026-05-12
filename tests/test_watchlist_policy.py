"""Tests for backend.services.watchlist_policy (P0-9 v2 schema)."""

from __future__ import annotations

import importlib
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.services.watchlist_policy import (
    FORBIDDEN_SIDES,
    LOCKED_COMPOSITION,
    LOCKED_EVENT_CAP,
    LOCKED_TOTAL_CODES,
    LOCKED_TOTAL_DAILY_CAP,
    LOCKED_TRADITIONAL_CAP,
    MANDATORY_ETF_CODES,
    BucketConfig,
    CapAllocation,
    DirectionPolicy,
    ExclusionRules,
    RequiredETF,
    WatchlistComposition,
    WatchlistPolicy,
    WatchlistPolicyError,
    assign_category,
    load_policy,
    partition_watchlist,
)

VALID_YAML = """
policy_version: 2
locked_decision: P0-9
last_updated: 2026-05-12

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
    - "510300"
    - "510500"
    - "159949"

overrides:
  "600519": fast

watchlist:
  total_codes: 13
  composition:
    sh_main: 4
    sz_main: 3
    chuangye: 3
    etf: 3
  default_category: slow

required_etfs:
  - code: "510300"
    name: "沪深300 ETF"
    tracking: "沪深300指数"
  - code: "510500"
    name: "中证500 ETF"
    tracking: "中证500指数"
  - code: "159949"
    name: "创业板50 ETF"
    tracking: "创业板50指数"

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

constraints:
  watchlist_size_must_equal: 13
  watchlist_etf_count_must_equal: 3
  total_daily_cap_must_equal_p0_7: 5
  long_only_must_be_true: true
"""


@pytest.fixture()
def policy_path(tmp_path: Path) -> Path:
    p = tmp_path / "watchlist_policy.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def policy(policy_path: Path) -> WatchlistPolicy:
    return load_policy(policy_path)


def _write_with(tmp_path: Path, mutation: tuple[str, str]) -> Path:
    """Helper: produce a YAML variant by string substitution."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(VALID_YAML.replace(*mutation), encoding="utf-8")
    return bad


class TestLoadPolicyHappyPath:
    @pytest.mark.unit
    def test_loads_valid_yaml(self, policy: WatchlistPolicy) -> None:
        assert policy.fast.max_debate_rounds == 1
        assert policy.fast.pipeline_timeout_seconds == 480
        assert policy.slow.max_debate_rounds == 2
        assert policy.slow.pipeline_timeout_seconds == 900
        assert policy.fast.cron == "0 9,11,13,15 * * mon-fri"
        assert policy.slow.cron == "0 9 * * mon-fri"
        assert policy.policy_version == 2
        assert policy.locked_decision == "P0-9"
        assert policy.last_updated == "2026-05-12"
        assert policy.overrides == {"600519": "fast"}
        assert policy.default_category == "slow"

    @pytest.mark.unit
    def test_composition_locked(self, policy: WatchlistPolicy) -> None:
        assert policy.composition.total_codes == LOCKED_TOTAL_CODES
        assert policy.composition.sh_main == LOCKED_COMPOSITION["sh_main"]
        assert policy.composition.sz_main == LOCKED_COMPOSITION["sz_main"]
        assert policy.composition.chuangye == LOCKED_COMPOSITION["chuangye"]
        assert policy.composition.etf == LOCKED_COMPOSITION["etf"]
        assert policy.composition.default_category == "slow"

    @pytest.mark.unit
    def test_required_etfs(self, policy: WatchlistPolicy) -> None:
        codes = {e.code for e in policy.required_etfs}
        assert codes == MANDATORY_ETF_CODES
        names = {e.code: e.name for e in policy.required_etfs}
        assert names["510300"] == "沪深300 ETF"

    @pytest.mark.unit
    def test_exclusion_rules(self, policy: WatchlistPolicy) -> None:
        assert policy.exclusion_rules.ipo_min_trading_days == 30
        assert policy.exclusion_rules.sub_new_min_trading_days == 180
        assert policy.exclusion_rules.min_avg_amount_20d_yuan == 200_000_000
        assert policy.exclusion_rules.max_unit_price_yuan == 500.0

    @pytest.mark.unit
    def test_cap_allocation(self, policy: WatchlistPolicy) -> None:
        assert policy.cap_allocation.total_daily_cap == LOCKED_TOTAL_DAILY_CAP
        assert (
            policy.cap_allocation.traditional_path_default_cap
            == LOCKED_TRADITIONAL_CAP
        )
        assert policy.cap_allocation.event_path_reserved_cap == LOCKED_EVENT_CAP
        assert policy.cap_allocation.reserved_cap_release_time == "14:30"

    @pytest.mark.unit
    def test_direction_policy(self, policy: WatchlistPolicy) -> None:
        assert policy.direction_policy.long_only is True
        assert policy.direction_policy.forbidden_sides == FORBIDDEN_SIDES
        assert policy.direction_policy.etf_arbitrage_enabled is False

    @pytest.mark.unit
    def test_all_watchlist_codes_union(self, policy: WatchlistPolicy) -> None:
        assert policy.all_watchlist_codes() == frozenset(
            {"600519", "000858", "510300", "510500", "159949"}
        )


class TestLoadPolicySchemaRejections:
    @pytest.mark.unit
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_policy(tmp_path / "nope.yaml")

    @pytest.mark.unit
    def test_v1_schema_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("policy_version: 2", "policy_version: 1"))
        with pytest.raises(WatchlistPolicyError, match="policy_version"):
            load_policy(bad)

    @pytest.mark.unit
    def test_wrong_locked_decision_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path, ("locked_decision: P0-9", "locked_decision: P0-7")
        )
        with pytest.raises(WatchlistPolicyError, match="locked_decision"):
            load_policy(bad)

    @pytest.mark.unit
    def test_missing_section_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "policy_version: 2\nlocked_decision: P0-9\n", encoding="utf-8"
        )
        with pytest.raises(
            WatchlistPolicyError, match="missing required section"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_malformed_yaml_wraps_to_policy_error(
        self, tmp_path: Path
    ) -> None:
        bad = tmp_path / "broken.yaml"
        bad.write_text("fast: [unclosed", encoding="utf-8")
        with pytest.raises(WatchlistPolicyError, match="not valid YAML"):
            load_policy(bad)

    @pytest.mark.unit
    def test_overlapping_default_codes_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ('default_codes: ["600519"]', 'default_codes: ["000858"]'),
        )
        with pytest.raises(
            WatchlistPolicyError, match="both fast.default_codes"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_negative_timeout_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("pipeline_timeout_seconds: 480", "pipeline_timeout_seconds: 0"),
        )
        with pytest.raises(
            WatchlistPolicyError, match="pipeline_timeout_seconds"
        ):
            load_policy(bad)


class TestCompositionInvariants:
    @pytest.mark.unit
    def test_total_codes_must_equal_13(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("total_codes: 13", "total_codes: 14"))
        with pytest.raises(WatchlistPolicyError, match="total_codes"):
            load_policy(bad)

    @pytest.mark.unit
    def test_composition_board_count_locked(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("sh_main: 4", "sh_main: 5"))
        with pytest.raises(WatchlistPolicyError, match="sh_main"):
            load_policy(bad)

    @pytest.mark.unit
    def test_composition_unknown_board_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("etf: 3", "etf: 3\n    star_market: 1"),
        )
        with pytest.raises(WatchlistPolicyError, match="unexpected boards"):
            load_policy(bad)


class TestRequiredETFs:
    @pytest.mark.unit
    def test_required_etfs_must_match_mandatory_set(
        self, tmp_path: Path
    ) -> None:
        # Replace 510300 with a non-mandatory ETF code.
        bad = _write_with(tmp_path, ('"510300"', '"159915"'))
        with pytest.raises(
            WatchlistPolicyError, match="required_etfs must contain"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_mandatory_etfs_must_be_in_slow_default_codes(
        self, tmp_path: Path
    ) -> None:
        # Remove the 510300 line from slow.default_codes (keep it in
        # required_etfs so the section validates first).
        original = (
            'default_codes:\n'
            '    - "000858"\n'
            '    - "510300"\n'
            '    - "510500"\n'
            '    - "159949"'
        )
        replacement = (
            'default_codes:\n'
            '    - "000858"\n'
            '    - "510500"\n'
            '    - "159949"'
        )
        bad = _write_with(tmp_path, (original, replacement))
        with pytest.raises(
            WatchlistPolicyError, match="mandatory ETFs not in slow.default_codes"
        ):
            load_policy(bad)


class TestExclusionRulesInvariants:
    """P0-9 §2.1 thresholds locked exactly — any drift must fail at boot."""

    @pytest.mark.unit
    def test_ipo_threshold_must_equal_30(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("ipo_min_trading_days: 30", "ipo_min_trading_days: 31"),
        )
        with pytest.raises(WatchlistPolicyError, match="ipo_min_trading_days"):
            load_policy(bad)

    @pytest.mark.unit
    def test_ipo_threshold_negative_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("ipo_min_trading_days: 30", "ipo_min_trading_days: -1"),
        )
        with pytest.raises(WatchlistPolicyError, match="ipo_min_trading_days"):
            load_policy(bad)

    @pytest.mark.unit
    def test_sub_new_threshold_must_equal_180(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            (
                "sub_new_min_trading_days: 180",
                "sub_new_min_trading_days: 179",
            ),
        )
        with pytest.raises(
            WatchlistPolicyError, match="sub_new_min_trading_days"
        ):
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
        with pytest.raises(
            WatchlistPolicyError, match="min_avg_amount_20d_yuan"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_max_unit_price_must_equal_500(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("max_unit_price_yuan: 500.0", "max_unit_price_yuan: 499.99"),
        )
        with pytest.raises(
            WatchlistPolicyError, match="max_unit_price_yuan"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_max_unit_price_accepts_int_form(self, tmp_path: Path) -> None:
        """YAML int (500) must be accepted as equal to locked float 500.0."""
        bad = _write_with(
            tmp_path,
            ("max_unit_price_yuan: 500.0", "max_unit_price_yuan: 500"),
        )
        policy = load_policy(bad)
        assert policy.exclusion_rules.max_unit_price_yuan == 500.0


class TestCapAllocationInvariants:
    @pytest.mark.unit
    def test_total_cap_locked_to_5(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path, ("total_daily_cap: 5", "total_daily_cap: 6")
        )
        with pytest.raises(WatchlistPolicyError, match="total_daily_cap"):
            load_policy(bad)

    @pytest.mark.unit
    def test_traditional_cap_locked_to_4(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            (
                "traditional_path_default_cap: 4",
                "traditional_path_default_cap: 5",
            ),
        )
        with pytest.raises(
            WatchlistPolicyError, match="traditional_path_default_cap"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_event_cap_locked_to_1(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("event_path_reserved_cap: 1", "event_path_reserved_cap: 2"),
        )
        with pytest.raises(
            WatchlistPolicyError, match="event_path_reserved_cap"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_release_time_format_validated(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ('reserved_cap_release_time: "14:30"',
             'reserved_cap_release_time: "2:30 PM"'),
        )
        with pytest.raises(
            WatchlistPolicyError, match="reserved_cap_release_time"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_release_time_must_equal_14_30(self, tmp_path: Path) -> None:
        """Well-formed HH:MM other than 14:30 must still be rejected."""
        bad = _write_with(
            tmp_path,
            ('reserved_cap_release_time: "14:30"',
             'reserved_cap_release_time: "09:00"'),
        )
        with pytest.raises(
            WatchlistPolicyError, match="14:30 slide rule locked"
        ):
            load_policy(bad)


class TestDirectionPolicyInvariants:
    @pytest.mark.unit
    def test_long_only_must_be_true(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ("long_only: true", "long_only: false"))
        with pytest.raises(WatchlistPolicyError, match="long_only"):
            load_policy(bad)

    @pytest.mark.unit
    def test_etf_arbitrage_must_stay_disabled(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("etf_arbitrage_enabled: false", "etf_arbitrage_enabled: true"),
        )
        with pytest.raises(
            WatchlistPolicyError, match="etf_arbitrage_enabled"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_forbidden_sides_locked_set(self, tmp_path: Path) -> None:
        # Drop SHORT from forbidden_sides so the set differs.
        bad = _write_with(tmp_path, ("    - SHORT\n", ""))
        with pytest.raises(WatchlistPolicyError, match="forbidden_sides"):
            load_policy(bad)


class TestConstraintsSelfCheck:
    @pytest.mark.unit
    def test_constraints_drift_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(
            tmp_path,
            ("watchlist_size_must_equal: 13", "watchlist_size_must_equal: 14"),
        )
        with pytest.raises(
            WatchlistPolicyError, match="constraints.watchlist_size_must_equal"
        ):
            load_policy(bad)


class TestOverrides:
    @pytest.mark.unit
    def test_numeric_override_codes_normalized_to_str(
        self, tmp_path: Path
    ) -> None:
        """YAML may parse pure-numeric codes as ints — they must come back as str."""
        bad = _write_with(tmp_path, ('"600519": fast', "600519: fast"))
        p = load_policy(bad)
        assert "600519" in p.overrides
        assert p.overrides["600519"] == "fast"

    @pytest.mark.unit
    def test_invalid_override_category_rejected(self, tmp_path: Path) -> None:
        bad = _write_with(tmp_path, ('"600519": fast', '"600519": medium'))
        with pytest.raises(
            WatchlistPolicyError, match="must be 'fast' or 'slow'"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_dangling_override_rejected(self, tmp_path: Path) -> None:
        """Overrides must reference codes in fast or slow default_codes."""
        bad = _write_with(tmp_path, ('"600519": fast', '"999999": fast'))
        with pytest.raises(
            WatchlistPolicyError, match="overrides reference codes outside"
        ):
            load_policy(bad)


class TestAssignCategory:
    @pytest.mark.unit
    def test_override_wins(self, policy: WatchlistPolicy) -> None:
        # YAML overrides "600519" → fast; slow.default_codes does not
        # include 600519, so default cannot decide.
        assert assign_category("600519", policy) == "fast"

    @pytest.mark.unit
    def test_slow_default_codes(self, policy: WatchlistPolicy) -> None:
        assert assign_category("000858", policy) == "slow"

    @pytest.mark.unit
    def test_etf_resolves_to_slow(self, policy: WatchlistPolicy) -> None:
        for etf in MANDATORY_ETF_CODES:
            assert assign_category(etf, policy) == "slow"

    @pytest.mark.unit
    def test_unknown_code_falls_back_to_default(
        self, policy: WatchlistPolicy
    ) -> None:
        assert assign_category("999999", policy) == "slow"


class TestPartitionWatchlist:
    @pytest.mark.unit
    def test_partition_preserves_order(
        self, policy: WatchlistPolicy
    ) -> None:
        codes = ["000858", "600519", "510300", "999999"]
        fast, slow = partition_watchlist(codes, policy)
        # 600519 → fast (via override); rest → slow
        assert fast == ["600519"]
        assert slow == ["000858", "510300", "999999"]

    @pytest.mark.unit
    def test_empty_input(self, policy: WatchlistPolicy) -> None:
        fast, slow = partition_watchlist([], policy)
        assert fast == []
        assert slow == []


class TestRuntimeImmutability:
    """P0-9 §1.3 forbids runtime mutation — verify helpers are gone."""

    @pytest.mark.unit
    def test_save_policy_not_exported(self) -> None:
        module = importlib.import_module("backend.services.watchlist_policy")
        assert not hasattr(module, "save_policy"), (
            "save_policy must not be re-introduced (P0-9 §1.3 runtime-immutable)"
        )

    @pytest.mark.unit
    def test_update_override_not_exported(self) -> None:
        module = importlib.import_module("backend.services.watchlist_policy")
        assert not hasattr(module, "update_override"), (
            "update_override must not be re-introduced "
            "(P0-9 §1.3 runtime-immutable)"
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
    def test_policy_is_frozen(self, policy: WatchlistPolicy) -> None:
        with pytest.raises(FrozenInstanceError):
            policy.default_category = "fast"  # type: ignore[misc]

    @pytest.mark.unit
    def test_composition_is_frozen(self) -> None:
        comp = WatchlistComposition()
        with pytest.raises(FrozenInstanceError):
            comp.sh_main = 99  # type: ignore[misc]

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

    @pytest.mark.unit
    def test_required_etf_is_frozen(self) -> None:
        etf = RequiredETF(code="510300", name="x", tracking="y")
        with pytest.raises(FrozenInstanceError):
            etf.code = "000001"  # type: ignore[misc]


class TestLockedConstants:
    """The module exports locked numeric constants — verify their values."""

    @pytest.mark.unit
    def test_locked_total_codes_is_13(self) -> None:
        assert LOCKED_TOTAL_CODES == 13

    @pytest.mark.unit
    def test_locked_total_daily_cap_is_5(self) -> None:
        assert LOCKED_TOTAL_DAILY_CAP == 5

    @pytest.mark.unit
    def test_locked_traditional_cap_is_4(self) -> None:
        assert LOCKED_TRADITIONAL_CAP == 4

    @pytest.mark.unit
    def test_locked_event_cap_is_1(self) -> None:
        assert LOCKED_EVENT_CAP == 1

    @pytest.mark.unit
    def test_locked_total_matches_split(self) -> None:
        assert (
            LOCKED_TRADITIONAL_CAP + LOCKED_EVENT_CAP
            == LOCKED_TOTAL_DAILY_CAP
        )

    @pytest.mark.unit
    def test_mandatory_etfs_locked_set(self) -> None:
        assert MANDATORY_ETF_CODES == frozenset(
            {"510300", "510500", "159949"}
        )

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

    @pytest.mark.unit
    def test_composition_sums_to_total(self) -> None:
        assert sum(LOCKED_COMPOSITION.values()) == LOCKED_TOTAL_CODES


class TestShippedYamlMatchesP09:
    """The shipped config/watchlist_policy.yaml must load and lock P0-9."""

    @pytest.mark.unit
    def test_shipped_yaml_loads_clean(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        shipped = repo_root / "config" / "watchlist_policy.yaml"
        if not shipped.exists():
            pytest.skip(f"shipped policy not present: {shipped}")
        loaded = load_policy(shipped)
        assert loaded.policy_version == 2
        assert loaded.locked_decision == "P0-9"
        # The 3 mandatory ETFs are seeded; individual codes stay empty
        # (10 owner picks deferred per P0-9 §1.3).
        assert MANDATORY_ETF_CODES <= set(loaded.slow.default_codes)
