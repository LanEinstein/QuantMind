"""V-002 — deterministic rotation policy (propose_rotation + config loader).

Adversarial-first: the policy must never sell a healthy incumbent to chase a
phantom, must pick the weakest weak incumbent + the strongest qualified
challenger, fails closed on ambiguous (duplicate) input, and replays bit-exact.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.slot_portfolio.policy import (
    ChurnConfig,
    ExpiryConfig,
    RotationPolicyConfig,
    load_rotation_policy_config,
    propose_rotation,
)
from backend.slot_portfolio.scoring import (
    ChallengerMarginConfig,
    ChallengerState,
    IncumbentState,
    IncumbentWeakConfig,
    SlotPortfolioError,
)

CONFIG = RotationPolicyConfig(
    version="test",
    incumbent_weak=IncumbentWeakConfig(
        min_holding_age_trading_days=5,
        max_line1_percentile=0.40,
        min_rank_deterioration_pct=0.20,
        score_below_median_mad_mult=0.75,
        drawdown_soft_threshold=0.08,
    ),
    challenger_margin=ChallengerMarginConfig(
        min_percentile=0.75,
        min_rank_lead_pct=0.25,
        min_composite_score_margin=0.10,
    ),
    churn=ChurnConfig(
        max_rotations_per_day=1,
        max_open_intents=1,
        rotation_subcap=1,
        same_incumbent_cooldown_td=20,
        same_pair_cooldown_td=30,
    ),
    expiry=ExpiryConfig(max_trading_days=3),
    config_hash="deadbeef",
)


def _weak(code: str, score: float = 0.30, pct: float = 0.30) -> IncumbentState:
    return IncumbentState(
        code=code, line1_percentile=pct, composite_score=score,
        entry_percentile=0.70, holding_age_trading_days=10,
        protective_stop_active=False, hard_exit_pending=False,
        score_median_20d=0.50, score_mad_20d=0.0,
        anomaly_flag_active=False, drawdown_from_local_high=0.12,
        suspended=False, limit_down_unsellable=False,
        corporate_action_unsafe=False,
    )


def _healthy(code: str, score: float = 0.85, pct: float = 0.85) -> IncumbentState:
    # Strong percentile + no deterioration + no confirmation → never weak.
    return IncumbentState(
        code=code, line1_percentile=pct, composite_score=score,
        entry_percentile=0.80, holding_age_trading_days=30,
        protective_stop_active=False, hard_exit_pending=False,
        score_median_20d=0.50, score_mad_20d=0.10,
        anomaly_flag_active=False, drawdown_from_local_high=0.0,
        suspended=False, limit_down_unsellable=False,
        corporate_action_unsafe=False,
    )


def _challenger(code: str, score: float = 0.90, pct: float = 0.90,
                qualified: bool = True) -> ChallengerState:
    return ChallengerState(
        code=code, qualified=qualified, line1_percentile=pct, composite_score=score
    )


class TestNoRotation:
    def test_no_weak_incumbent_no_rotation(self) -> None:
        # The phantom-chasing guard: a screaming-strong challenger NEVER displaces
        # a healthy incumbent (the protection core).
        p = propose_rotation(
            [_healthy("600001"), _healthy("600002")],
            [_challenger("000009", score=0.99, pct=0.99)],
            CONFIG,
        )
        assert not p.should_rotate
        assert p.weak_incumbents == ()
        assert "protect" in p.reason

    def test_weak_but_no_qualified_challenger(self) -> None:
        p = propose_rotation(
            [_weak("600001")],
            [_challenger("000009", qualified=False, score=0.99, pct=0.99)],
            CONFIG,
        )
        assert not p.should_rotate
        assert p.incumbent_code == "600001"
        assert p.challenger_code is None

    def test_weak_but_challenger_below_margin(self) -> None:
        # Weak incumbent, challenger qualified + >=P75 but composite margin short.
        p = propose_rotation(
            [_weak("600001", score=0.30, pct=0.30)],
            [_challenger("000009", score=0.36, pct=0.80)],  # margin 0.06 < 0.10
            CONFIG,
        )
        assert not p.should_rotate
        assert p.incumbent_code == "600001" and p.challenger_code == "000009"
        assert p.margin is not None and not p.margin.composite_margin_sufficient


class TestRotationProposed:
    def test_weak_plus_margin_winner_rotates(self) -> None:
        p = propose_rotation(
            [_weak("600001")],
            [_challenger("000009")],
            CONFIG,
        )
        assert p.should_rotate
        assert p.incumbent_code == "600001"
        assert p.challenger_code == "000009"
        assert p.weakness is not None and p.weakness.independently_weak
        assert p.margin is not None and p.margin.wins_by_margin

    def test_picks_weakest_weak_incumbent(self) -> None:
        # Two weak incumbents — the lower composite score is the sell target.
        p = propose_rotation(
            [_weak("600001", score=0.35), _weak("600002", score=0.20)],
            [_challenger("000009")],
            CONFIG,
        )
        assert p.should_rotate and p.incumbent_code == "600002"
        assert set(p.weak_incumbents) == {"600001", "600002"}

    def test_picks_strongest_qualified_challenger(self) -> None:
        p = propose_rotation(
            [_weak("600001")],
            [_challenger("000007", score=0.80), _challenger("000009", score=0.95)],
            CONFIG,
        )
        assert p.should_rotate and p.challenger_code == "000009"

    def test_challenger_tie_breaks_to_lower_code(self) -> None:
        # Equal composite scores → deterministic code-asc tie-break.
        p = propose_rotation(
            [_weak("600001")],
            [_challenger("000009", score=0.90), _challenger("000002", score=0.90)],
            CONFIG,
        )
        assert p.should_rotate and p.challenger_code == "000002"

    def test_healthy_incumbent_excluded_only_weak_rotates(self) -> None:
        p = propose_rotation(
            [_healthy("600001"), _weak("600002", score=0.25)],
            [_challenger("000009")],
            CONFIG,
        )
        assert p.should_rotate and p.incumbent_code == "600002"
        assert p.weak_incumbents == ("600002",)


class TestEdgeCases:
    def test_challenger_already_held_is_dropped(self) -> None:
        # A "challenger" whose code is already a holding cannot free a slot.
        p = propose_rotation(
            [_weak("600001"), _healthy("000009")],
            [_challenger("000009", score=0.99, pct=0.99)],
            CONFIG,
        )
        assert not p.should_rotate
        assert p.challenger_code is None

    def test_duplicate_incumbent_fails_closed(self) -> None:
        with pytest.raises(SlotPortfolioError, match="duplicate incumbent"):
            propose_rotation(
                [_weak("600001"), _weak("600001")], [_challenger("000009")], CONFIG
            )

    def test_duplicate_challenger_fails_closed(self) -> None:
        with pytest.raises(SlotPortfolioError, match="duplicate challenger"):
            propose_rotation(
                [_weak("600001")],
                [_challenger("000009"), _challenger("000009")],
                CONFIG,
            )

    def test_empty_inputs_no_rotation(self) -> None:
        p = propose_rotation([], [], CONFIG)
        assert not p.should_rotate

    def test_deterministic_replay(self) -> None:
        args = (
            [_weak("600001", score=0.35), _weak("600002", score=0.20)],
            [_challenger("000007", score=0.80), _challenger("000009", score=0.95)],
            CONFIG,
        )
        assert propose_rotation(*args) == propose_rotation(*args)


class TestConfigLoader:
    def test_loads_production_config(self) -> None:
        cfg = load_rotation_policy_config(Path("config/slot_rotation_policy.yaml"))
        assert cfg.version == "v1"
        assert cfg.incumbent_weak.min_holding_age_trading_days == 5
        assert cfg.incumbent_weak.max_line1_percentile == 0.40
        assert cfg.challenger_margin.min_percentile == 0.75
        assert cfg.churn.max_rotations_per_day == 1
        assert cfg.churn.max_open_intents == 1
        assert cfg.churn.same_incumbent_cooldown_td == 20
        assert cfg.churn.same_pair_cooldown_td == 30
        assert cfg.expiry.max_trading_days == 3
        assert cfg.config_hash  # non-empty pin

    def test_missing_churn_block_rejected(self, tmp_path: Path) -> None:
        # A config without the churn block must fail closed (no silent defaults).
        p = tmp_path / "c.yaml"
        p.write_text(
            "version: v1\n"
            "incumbent_weak:\n"
            "  min_holding_age_trading_days: 5\n"
            "  max_line1_percentile: 0.4\n"
            "  min_rank_deterioration_pct: 0.2\n"
            "  confirmation:\n"
            "    score_below_median_mad_mult: 0.75\n"
            "    drawdown_soft_threshold: 0.08\n"
            "challenger_margin:\n"
            "  min_percentile: 0.75\n"
            "  min_rank_lead_pct: 0.25\n"
            "  min_composite_score_margin: 0.10\n",
            encoding="utf-8",
        )
        with pytest.raises(SlotPortfolioError, match="churn"):
            load_rotation_policy_config(p)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_rotation_policy_config(tmp_path / "nope.yaml")

    def test_missing_version_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "c.yaml"
        p.write_text("incumbent_weak: {}\n", encoding="utf-8")
        with pytest.raises(SlotPortfolioError, match="version"):
            load_rotation_policy_config(p)

    def test_out_of_range_percentile_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "c.yaml"
        p.write_text(
            "version: v1\n"
            "incumbent_weak:\n"
            "  min_holding_age_trading_days: 5\n"
            "  max_line1_percentile: 1.5\n"          # out of [0,1]
            "  min_rank_deterioration_pct: 0.2\n"
            "  confirmation:\n"
            "    score_below_median_mad_mult: 0.75\n"
            "    drawdown_soft_threshold: 0.08\n"
            "challenger_margin:\n"
            "  min_percentile: 0.75\n"
            "  min_rank_lead_pct: 0.25\n"
            "  min_composite_score_margin: 0.10\n",
            encoding="utf-8",
        )
        with pytest.raises(SlotPortfolioError, match="max_line1_percentile"):
            load_rotation_policy_config(p)

    def test_missing_block_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "c.yaml"
        p.write_text("version: v1\nincumbent_weak: 7\n", encoding="utf-8")
        with pytest.raises(SlotPortfolioError, match="must be a mapping"):
            load_rotation_policy_config(p)

    def test_negative_composite_margin_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "c.yaml"
        p.write_text(
            "version: v1\n"
            "incumbent_weak:\n"
            "  min_holding_age_trading_days: 5\n"
            "  max_line1_percentile: 0.4\n"
            "  min_rank_deterioration_pct: 0.2\n"
            "  confirmation:\n"
            "    score_below_median_mad_mult: 0.75\n"
            "    drawdown_soft_threshold: 0.08\n"
            "challenger_margin:\n"
            "  min_percentile: 0.75\n"
            "  min_rank_lead_pct: 0.25\n"
            "  min_composite_score_margin: -0.10\n",   # negative → invalid
            encoding="utf-8",
        )
        with pytest.raises(SlotPortfolioError, match="min_composite_score_margin"):
            load_rotation_policy_config(p)

    def test_bool_as_int_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "c.yaml"
        p.write_text(
            "version: v1\n"
            "incumbent_weak:\n"
            "  min_holding_age_trading_days: true\n"   # bool, not a count
            "  max_line1_percentile: 0.4\n"
            "  min_rank_deterioration_pct: 0.2\n"
            "  confirmation:\n"
            "    score_below_median_mad_mult: 0.75\n"
            "    drawdown_soft_threshold: 0.08\n"
            "challenger_margin:\n"
            "  min_percentile: 0.75\n"
            "  min_rank_lead_pct: 0.25\n"
            "  min_composite_score_margin: 0.10\n",
            encoding="utf-8",
        )
        with pytest.raises(SlotPortfolioError, match="min_holding_age_trading_days"):
            load_rotation_policy_config(p)
