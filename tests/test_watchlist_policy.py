"""Tests for backend.services.watchlist_policy (Phase 5B-T02)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.watchlist_policy import (
    BucketConfig,
    WatchlistPolicy,
    WatchlistPolicyError,
    assign_category,
    load_policy,
    partition_watchlist,
    save_policy,
    update_override,
)

VALID_YAML = """
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
  default_codes: ["000858"]
overrides:
  "300750": slow
  "601318": slow
default_category: slow
policy_version: 1
last_updated: 2026-05-02
"""


@pytest.fixture()
def policy_path(tmp_path: Path) -> Path:
    p = tmp_path / "watchlist_policy.yaml"
    p.write_text(VALID_YAML, encoding="utf-8")
    return p


@pytest.fixture()
def policy(policy_path: Path) -> WatchlistPolicy:
    return load_policy(policy_path)


class TestLoadPolicy:
    @pytest.mark.unit
    def test_loads_valid_yaml(self, policy: WatchlistPolicy) -> None:
        assert policy.fast.max_debate_rounds == 1
        assert policy.fast.pipeline_timeout_seconds == 480
        assert policy.slow.max_debate_rounds == 2
        assert policy.slow.pipeline_timeout_seconds == 900
        assert policy.fast.cron == "0 9,11,13,15 * * mon-fri"
        assert policy.slow.cron == "0 9 * * mon-fri"
        assert policy.overrides == {"300750": "slow", "601318": "slow"}
        assert policy.default_category == "slow"
        assert policy.policy_version == 1

    @pytest.mark.unit
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_policy(tmp_path / "nope.yaml")

    @pytest.mark.unit
    def test_overlapping_default_codes_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            VALID_YAML.replace(
                'default_codes: ["000858"]',
                'default_codes: ["600519", "000858"]',
            ),
            encoding="utf-8",
        )
        with pytest.raises(WatchlistPolicyError, match="both fast.default_codes"):
            load_policy(bad)

    @pytest.mark.unit
    def test_invalid_override_category_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            VALID_YAML.replace('"300750": slow', '"300750": medium'),
            encoding="utf-8",
        )
        with pytest.raises(
            WatchlistPolicyError, match="must be 'fast' or 'slow'"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_missing_bucket_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("fast:\n  cron: '* * * * *'\n", encoding="utf-8")
        with pytest.raises(
            WatchlistPolicyError, match="must define both 'fast' and 'slow'"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_malformed_yaml_wraps_to_policy_error(
        self, tmp_path: Path
    ) -> None:
        """PyYAML errors must surface as WatchlistPolicyError so callers
        only need to import a single project-defined exception type."""
        bad = tmp_path / "broken.yaml"
        bad.write_text("fast: [unclosed", encoding="utf-8")
        with pytest.raises(WatchlistPolicyError, match="not valid YAML"):
            load_policy(bad)

    @pytest.mark.unit
    def test_negative_timeout_rejected(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            VALID_YAML.replace(
                "pipeline_timeout_seconds: 480",
                "pipeline_timeout_seconds: 0",
            ),
            encoding="utf-8",
        )
        with pytest.raises(
            WatchlistPolicyError, match="pipeline_timeout_seconds"
        ):
            load_policy(bad)

    @pytest.mark.unit
    def test_numeric_override_codes_normalized_to_str(
        self, tmp_path: Path
    ) -> None:
        """YAML may parse pure-numeric codes as ints — they must come back as str."""
        bad = tmp_path / "numeric.yaml"
        # Drop quotes around the override key so PyYAML parses it as int.
        bad.write_text(
            VALID_YAML.replace('"300750": slow', "300750: slow"),
            encoding="utf-8",
        )
        p = load_policy(bad)
        assert "300750" in p.overrides
        assert p.overrides["300750"] == "slow"


class TestAssignCategory:
    @pytest.mark.unit
    def test_override_wins_over_defaults(
        self, policy: WatchlistPolicy
    ) -> None:
        assert assign_category("300750", policy) == "slow"

    @pytest.mark.unit
    def test_fast_default_codes(self, policy: WatchlistPolicy) -> None:
        assert assign_category("600519", policy) == "fast"

    @pytest.mark.unit
    def test_slow_default_codes(self, policy: WatchlistPolicy) -> None:
        assert assign_category("000858", policy) == "slow"

    @pytest.mark.unit
    def test_unknown_code_falls_back_to_default(
        self, policy: WatchlistPolicy
    ) -> None:
        assert assign_category("999999", policy) == "slow"

    @pytest.mark.unit
    def test_override_can_force_fast_on_slow_default(
        self, policy: WatchlistPolicy
    ) -> None:
        # Override for 000858 is not present; reach via update_override.
        new = update_override(policy, "000858", "fast")
        assert assign_category("000858", new) == "fast"


class TestPartitionWatchlist:
    @pytest.mark.unit
    def test_partition_preserves_order(
        self, policy: WatchlistPolicy
    ) -> None:
        codes = ["600519", "000858", "601318", "300750", "999999"]
        fast, slow = partition_watchlist(codes, policy)
        assert fast == ["600519"]
        # 000858 (slow default), 601318 (override slow),
        # 300750 (override slow), 999999 (default slow) in order
        assert slow == ["000858", "601318", "300750", "999999"]

    @pytest.mark.unit
    def test_empty_input(self, policy: WatchlistPolicy) -> None:
        fast, slow = partition_watchlist([], policy)
        assert fast == []
        assert slow == []


class TestUpdateOverride:
    @pytest.mark.unit
    def test_add_override_returns_new_policy(
        self, policy: WatchlistPolicy
    ) -> None:
        new = update_override(policy, "002594", "fast")
        # Original is unchanged (immutability invariant)
        assert "002594" not in policy.overrides
        assert new.overrides["002594"] == "fast"

    @pytest.mark.unit
    def test_clearing_override_removes_entry(
        self, policy: WatchlistPolicy
    ) -> None:
        new = update_override(policy, "300750", None)
        assert "300750" not in new.overrides
        # 300750 now resolves via default (slow)
        assert assign_category("300750", new) == "slow"

    @pytest.mark.unit
    def test_invalid_category_raises(self, policy: WatchlistPolicy) -> None:
        with pytest.raises(WatchlistPolicyError):
            update_override(policy, "002594", "medium")  # type: ignore[arg-type]


class TestSavePolicy:
    @pytest.mark.unit
    def test_round_trip_save_load(
        self, policy: WatchlistPolicy, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.yaml"
        new_policy = update_override(policy, "002594", "fast")
        save_policy(new_policy, out)
        loaded = load_policy(out)
        assert loaded.overrides == new_policy.overrides
        assert loaded.fast.max_debate_rounds == policy.fast.max_debate_rounds
        assert loaded.slow.cron == policy.slow.cron
        assert loaded.default_category == policy.default_category
        assert loaded.policy_version == policy.policy_version

    @pytest.mark.unit
    def test_save_uses_atomic_replace(
        self, policy: WatchlistPolicy, tmp_path: Path
    ) -> None:
        out = tmp_path / "atomic.yaml"
        save_policy(policy, out)
        # No leftover .tmp file after a successful write
        assert not out.with_suffix(out.suffix + ".tmp").exists()
        assert out.exists()


class TestBucketConfig:
    @pytest.mark.unit
    def test_bucket_config_is_frozen(self) -> None:
        bucket = BucketConfig(
            cron="* * * * *",
            pipeline="x",
            max_debate_rounds=1,
            pipeline_timeout_seconds=60,
        )
        with pytest.raises(Exception):
            bucket.cron = "0 0 * * *"  # type: ignore[misc]
