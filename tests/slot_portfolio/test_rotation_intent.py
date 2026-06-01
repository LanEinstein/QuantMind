"""V-003 — append-only RotationIntent ledger + churn gates + expiry fallback.

Adversarial-first: the ledger round-trips + replays from bytes, the churn gates
each block independently (esp. yield-to-protective-stop), the 3-path expiry
fallback never silently under-invests, and the UNDERINVESTED block stops further
rotation until a manual gate clears it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.slot_portfolio.policy import (
    ChallengerMarginConfig,
    ChurnConfig,
    ExpiryConfig,
    IncumbentWeakConfig,
    RotationPolicyConfig,
    RotationProposal,
)
from backend.slot_portfolio.rotation_intent import (
    ChurnGateInputs,
    ExpiryOutcomeKind,
    RotationIntent,
    RotationIntentError,
    RotationIntentStore,
    apply_churn_gates,
    build_intent_id,
    build_rotation_intent,
    compute_expires_at,
    is_expired,
    resolve_expiry,
)
from backend.slot_portfolio.scoring import ChallengerState

CONFIG = RotationPolicyConfig(
    version="test",
    incumbent_weak=IncumbentWeakConfig(
        min_holding_age_trading_days=5, max_line1_percentile=0.40,
        min_rank_deterioration_pct=0.20, score_below_median_mad_mult=0.75,
        drawdown_soft_threshold=0.08,
    ),
    challenger_margin=ChallengerMarginConfig(
        min_percentile=0.75, min_rank_lead_pct=0.25, min_composite_score_margin=0.10,
    ),
    churn=ChurnConfig(
        max_rotations_per_day=1, max_open_intents=1, rotation_subcap=1,
        same_incumbent_cooldown_td=20, same_pair_cooldown_td=30,
    ),
    expiry=ExpiryConfig(max_trading_days=3),
    config_hash="cfghash",
)


def _proposal() -> RotationProposal:
    return RotationProposal(
        should_rotate=True,
        incumbent_code="600001", challenger_code="000009",
        incumbent_score=0.30, challenger_score=0.90,
        incumbent_percentile=0.30, challenger_percentile=0.90,
        reason="weak incumbent + challenger wins by margin",
        weak_incumbents=("600001",),
    )


def _intent(store: RotationIntentStore | None = None) -> RotationIntent:
    intent = build_rotation_intent(
        _proposal(),
        created_trade_date="20260601",
        expires_at_trade_date="20260604",
        sell_instruction_id="QM-20260601-093500-000001-SELL-001",
        signal_id="LINE1-20260601",
        config=CONFIG,
    )
    if store is not None:
        store.record_proposed(intent)
    return intent


# ---------------------------------------------------------------------------
# RotationIntent build + ledger round-trip / replay
# ---------------------------------------------------------------------------


class TestIntentBuild:
    def test_deterministic_intent_id(self) -> None:
        assert build_intent_id("20260601", "600001", "000009") == (
            "ROT-20260601-600001-000009"
        )

    def test_build_from_proposal(self) -> None:
        i = _intent()
        assert i.intent_id == "ROT-20260601-600001-000009"
        assert i.incumbent_code == "600001" and i.challenger_code == "000009"
        assert i.config_hash == "cfghash" and i.signal_id == "LINE1-20260601"

    def test_build_rejects_non_actionable_proposal(self) -> None:
        bad = RotationProposal(
            should_rotate=False, incumbent_code=None, challenger_code=None,
            incumbent_score=None, challenger_score=None,
            incumbent_percentile=None, challenger_percentile=None,
            reason="no", weak_incumbents=(),
        )
        with pytest.raises(RotationIntentError, match="non-actionable"):
            build_rotation_intent(
                bad, created_trade_date="20260601",
                expires_at_trade_date="20260604",
                sell_instruction_id="x", signal_id="y", config=CONFIG,
            )


class TestLedgerRoundTrip:
    def test_append_and_replay_from_bytes(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        intent = _intent(store)
        # Reload from disk (no in-memory state shared) — replay must reconstruct
        # the identical intent bit-for-bit.
        reloaded = RotationIntentStore(tmp_path / "rot.jsonl")
        events = reloaded.load_events()
        assert len(events) == 1
        assert events[0].intent == intent

    def test_corrupt_row_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "rot.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        store = RotationIntentStore(path)
        with pytest.raises(RotationIntentError, match="corrupt"):
            store.load_events()

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "absent.jsonl")
        assert store.load_events() == ()
        assert store.open_intents() == ()

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        _intent(store)
        # Inject a blank line — load_events must skip it, not choke.
        with (tmp_path / "rot.jsonl").open("a", encoding="utf-8") as f:
            f.write("\n")
        assert len(store.load_events()) == 1

    def test_store_path_property(self, tmp_path: Path) -> None:
        path = tmp_path / "rot.jsonl"
        assert RotationIntentStore(path).path == path

    def test_malformed_intent_row_fails_closed(self) -> None:
        with pytest.raises(RotationIntentError, match="malformed RotationIntent"):
            RotationIntent.from_dict({"intent_id": "x"})  # missing required keys

    def test_malformed_event_row_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "rot.jsonl"
        path.write_text('{"event_type": "bogus"}\n', encoding="utf-8")
        with pytest.raises(RotationIntentError, match="malformed rotation event"):
            RotationIntentStore(path).load_events()


class TestOpenIntentsFold:
    def test_proposed_is_open(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        intent = _intent(store)
        assert store.open_intents() == (intent,)

    def test_resolved_closes_intent(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        intent = _intent(store)
        store.record_resolved(intent.intent_id, trade_date="20260602")
        assert store.open_intents() == ()

    def test_expired_closes_intent(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        intent = _intent(store)
        outcome = resolve_expiry(
            intent, original_challenger_qualified=True,
            best_challenger=None, config=CONFIG,
        )
        store.record_expired(intent.intent_id, trade_date="20260604", outcome=outcome)
        assert store.open_intents() == ()


class TestCooldownFolds:
    def test_last_rotation_date_for_incumbent(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        _intent(store)
        assert store.last_rotation_date_for_incumbent("600001") == "20260601"
        assert store.last_rotation_date_for_incumbent("999999") is None

    def test_last_rotation_date_for_pair(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        _intent(store)
        assert store.last_rotation_date_for_pair("000009", "600001") == "20260601"
        assert store.last_rotation_date_for_pair("000009", "600002") is None


# ---------------------------------------------------------------------------
# Churn gates
# ---------------------------------------------------------------------------


def _gate_inputs(**overrides: object) -> ChurnGateInputs:
    base = dict(
        rotations_today=0, open_intent_count=0,
        daily_new_instruction_budget_remaining=3,
        protective_action_needs_cap_today=False,
        underinvested_block_active=False,
        trading_days_since_incumbent_rotation=None,
        trading_days_since_pair_rotation=None,
    )
    base.update(overrides)
    return ChurnGateInputs(**base)  # type: ignore[arg-type]


class TestChurnGates:
    def test_clean_inputs_allow_rotation(self) -> None:
        r = apply_churn_gates(_proposal(), _gate_inputs(), CONFIG)
        assert r.allowed and r.blocked_by == ()

    def test_non_actionable_proposal_never_allowed(self) -> None:
        no_rot = RotationProposal(
            should_rotate=False, incumbent_code=None, challenger_code=None,
            incumbent_score=None, challenger_score=None,
            incumbent_percentile=None, challenger_percentile=None,
            reason="no", weak_incumbents=(),
        )
        r = apply_churn_gates(no_rot, _gate_inputs(), CONFIG)
        assert not r.allowed and r.blocked_by == ("no_proposal",)

    def test_daily_rotation_cap_blocks(self) -> None:
        r = apply_churn_gates(_proposal(), _gate_inputs(rotations_today=1), CONFIG)
        assert not r.allowed and "daily_rotation_cap" in r.blocked_by

    def test_open_intent_cap_blocks(self) -> None:
        r = apply_churn_gates(_proposal(), _gate_inputs(open_intent_count=1), CONFIG)
        assert not r.allowed and "open_intent_cap" in r.blocked_by

    def test_yield_to_protective_stop_blocks(self) -> None:
        # The priority rule: a protective stop needing today's cap pre-empts.
        r = apply_churn_gates(
            _proposal(), _gate_inputs(protective_action_needs_cap_today=True), CONFIG
        )
        assert not r.allowed and "yield_to_protective_stop" in r.blocked_by

    def test_insufficient_daily_cap_blocks(self) -> None:
        r = apply_churn_gates(
            _proposal(),
            _gate_inputs(daily_new_instruction_budget_remaining=0),
            CONFIG,
        )
        assert not r.allowed and "insufficient_daily_cap" in r.blocked_by

    def test_incumbent_cooldown_blocks(self) -> None:
        # Rotated this incumbent 10 td ago < 20 td cooldown.
        r = apply_churn_gates(
            _proposal(),
            _gate_inputs(trading_days_since_incumbent_rotation=10),
            CONFIG,
        )
        assert not r.allowed and "incumbent_cooldown" in r.blocked_by

    def test_incumbent_cooldown_expired_allows(self) -> None:
        r = apply_churn_gates(
            _proposal(),
            _gate_inputs(trading_days_since_incumbent_rotation=20),
            CONFIG,
        )
        assert r.allowed

    def test_pair_cooldown_blocks(self) -> None:
        r = apply_churn_gates(
            _proposal(),
            _gate_inputs(trading_days_since_pair_rotation=29),
            CONFIG,
        )
        assert not r.allowed and "pair_cooldown" in r.blocked_by

    def test_underinvested_block_blocks(self) -> None:
        r = apply_churn_gates(
            _proposal(), _gate_inputs(underinvested_block_active=True), CONFIG
        )
        assert not r.allowed and "underinvested_block" in r.blocked_by

    def test_multiple_blocks_all_reported(self) -> None:
        r = apply_churn_gates(
            _proposal(),
            _gate_inputs(rotations_today=1, open_intent_count=1),
            CONFIG,
        )
        assert not r.allowed
        assert "daily_rotation_cap" in r.blocked_by
        assert "open_intent_cap" in r.blocked_by


# ---------------------------------------------------------------------------
# Expiry + fallback
# ---------------------------------------------------------------------------


class TestExpiry:
    def test_compute_expires_at_takes_earlier(self) -> None:
        assert compute_expires_at("20260604", "20260603") == "20260603"
        assert compute_expires_at("20260604", "20260610") == "20260604"
        assert compute_expires_at("20260604", None) == "20260604"

    def test_is_expired_boundary(self) -> None:
        i = _intent()
        assert not is_expired(i, "20260603")
        assert is_expired(i, "20260604")  # on the expiry date
        assert is_expired(i, "20260605")

    def test_fallback_original_challenger(self) -> None:
        i = _intent()
        out = resolve_expiry(
            i, original_challenger_qualified=True, best_challenger=None, config=CONFIG
        )
        assert out.kind is ExpiryOutcomeKind.FALLBACK_ORIGINAL
        assert out.buy_code == "000009" and not out.blocks_further_rotation

    def test_fallback_best_when_original_disqualified(self) -> None:
        i = _intent()
        best = ChallengerState(
            code="000123", qualified=True, line1_percentile=0.80, composite_score=0.8
        )
        out = resolve_expiry(
            i, original_challenger_qualified=False, best_challenger=best, config=CONFIG
        )
        assert out.kind is ExpiryOutcomeKind.FALLBACK_BEST
        assert out.buy_code == "000123" and not out.blocks_further_rotation

    def test_best_below_p75_is_not_a_fallback(self) -> None:
        i = _intent()
        weak_best = ChallengerState(
            code="000123", qualified=True, line1_percentile=0.74, composite_score=0.8
        )
        out = resolve_expiry(
            i, original_challenger_qualified=False,
            best_challenger=weak_best, config=CONFIG,
        )
        assert out.kind is ExpiryOutcomeKind.UNDERINVESTED
        assert out.buy_code is None and out.blocks_further_rotation

    def test_underinvested_when_no_replacement(self) -> None:
        i = _intent()
        out = resolve_expiry(
            i, original_challenger_qualified=False,
            best_challenger=None, config=CONFIG,
        )
        assert out.kind is ExpiryOutcomeKind.UNDERINVESTED
        assert out.buy_code is None and out.blocks_further_rotation


class TestUnderinvestedBlock:
    def test_block_set_then_cleared(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        intent = _intent(store)
        out = resolve_expiry(
            intent, original_challenger_qualified=False,
            best_challenger=None, config=CONFIG,
        )
        store.record_expired(intent.intent_id, trade_date="20260604", outcome=out)
        assert store.underinvested_block_active()
        # A subsequent gate must block while the underinvested flag is active.
        gate = apply_churn_gates(
            _proposal(),
            _gate_inputs(underinvested_block_active=store.underinvested_block_active()),
            CONFIG,
        )
        assert not gate.allowed and "underinvested_block" in gate.blocked_by
        # Manual gate clears it.
        store.record_underinvested_cleared(trade_date="20260610", note="owner gate")
        assert not store.underinvested_block_active()

    def test_block_survives_reload(self, tmp_path: Path) -> None:
        store = RotationIntentStore(tmp_path / "rot.jsonl")
        intent = _intent(store)
        out = resolve_expiry(
            intent, original_challenger_qualified=False,
            best_challenger=None, config=CONFIG,
        )
        store.record_expired(intent.intent_id, trade_date="20260604", outcome=out)
        # Reload from bytes — the derived block state must persist.
        assert RotationIntentStore(tmp_path / "rot.jsonl").underinvested_block_active()
