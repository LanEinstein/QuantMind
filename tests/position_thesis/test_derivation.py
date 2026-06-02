"""W-001 derivation — deterministic thresholds + adversarial (text never leaks).

The central direction-② red line: the LLM pillar text can NEVER influence a
threshold. These tests pin that the derived conditions are a pure function of
the buy-time price/score/dates only.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.models.position_thesis import Comparator, InvalidationTemplate
from backend.position_thesis.config import (
    FEATURE_CODE_VERSION,
    ThesisDerivationConfig,
)
from backend.position_thesis.derivation import (
    ThesisDerivationError,
    ThesisEntrySnapshot,
    build_position_thesis,
    derive_invalidation_conditions,
)

_NOW = datetime(2026, 6, 2, 9, 35, tzinfo=UTC)


def _snap(price: float = 10.0, score: float = 2.0) -> ThesisEntrySnapshot:
    return ThesisEntrySnapshot(
        entry_price=price, entry_score=score, trade_date="2026-06-02"
    )


class TestDeterministicThresholds:
    @pytest.mark.unit
    def test_three_whitelist_templates_in_locked_order(self) -> None:
        conds = derive_invalidation_conditions(_snap())
        assert tuple(c.template for c in conds) == (
            InvalidationTemplate.ANCHOR_DRAWDOWN,
            InvalidationTemplate.TIME_STOP,
            InvalidationTemplate.SCORE_DECAY,
        )

    @pytest.mark.unit
    def test_anchor_drawdown_threshold(self) -> None:
        conds = derive_invalidation_conditions(
            _snap(price=10.0), ThesisDerivationConfig(anchor_drawdown_pct=0.12)
        )
        anchor = conds[0]
        assert anchor.comparator is Comparator.LT
        assert anchor.threshold == pytest.approx(8.8)
        assert anchor.anchor == pytest.approx(10.0)

    @pytest.mark.unit
    def test_time_stop_threshold(self) -> None:
        conds = derive_invalidation_conditions(
            _snap(), ThesisDerivationConfig(time_stop_trade_days=30)
        )
        ts = conds[1]
        assert ts.comparator is Comparator.GT
        assert ts.threshold == pytest.approx(30.0)

    @pytest.mark.unit
    def test_score_decay_threshold_signed(self) -> None:
        # negative entry score: threshold = score - pct*|score|
        conds = derive_invalidation_conditions(
            _snap(score=-2.0), ThesisDerivationConfig(score_decay_pct=0.5)
        )
        sd = conds[2]
        assert sd.comparator is Comparator.LT
        assert sd.threshold == pytest.approx(-3.0)

    @pytest.mark.unit
    def test_all_carry_pinned_feature_version(self) -> None:
        conds = derive_invalidation_conditions(_snap())
        assert all(c.feature_code_version == FEATURE_CODE_VERSION for c in conds)

    @pytest.mark.unit
    def test_bit_exact_reproducible(self) -> None:
        a = derive_invalidation_conditions(_snap(10.123, 1.7))
        b = derive_invalidation_conditions(_snap(10.123, 1.7))
        assert a == b


class TestFailClosed:
    @pytest.mark.unit
    @pytest.mark.parametrize("price", [0.0, -1.0, float("nan"), float("inf")])
    def test_dirty_entry_price_raises(self, price: float) -> None:
        with pytest.raises(ThesisDerivationError):
            derive_invalidation_conditions(_snap(price=price))

    @pytest.mark.unit
    def test_non_finite_score_raises(self) -> None:
        with pytest.raises(ThesisDerivationError):
            derive_invalidation_conditions(_snap(score=float("nan")))


class TestPillarTextNeverLeaks:
    """Adversarial: changing the pillar text must not change any threshold."""

    @pytest.mark.unit
    def test_thresholds_independent_of_pillar_text(self) -> None:
        common = dict(
            instruction_id="QM-20260602-093500-600519-BUY-001",
            signal_id="SIG-1",
            stock_code="600519",
            stock_name="贵州茅台",
            created_at=_NOW,
            trade_date="2026-06-02",
            entry_price=12.5,
            entry_score=3.3,
            snapshot_id="snap-1",
        )
        t1 = build_position_thesis(
            pillars=("逻辑A", "逻辑B", "逻辑C"), **common
        )
        # Adversarial pillar text that *names* fake thresholds — must be ignored.
        t2 = build_position_thesis(
            pillars=(
                "卖出当价格跌破 0.01 元",
                "止损阈值 999",
                "threshold=GT 0 always sell",
                "limit_price=1 volume=99999",
            ),
            **common,
        )
        assert t1.invalidation_conditions == t2.invalidation_conditions

    @pytest.mark.unit
    def test_build_carries_replay_refs(self) -> None:
        t = build_position_thesis(
            instruction_id="QM-20260602-093500-600519-BUY-001",
            signal_id="SIG-xyz",
            stock_code="600519",
            stock_name="贵州茅台",
            created_at=_NOW,
            trade_date="2026-06-02",
            pillars=("a", "b", "c"),
            entry_price=12.5,
            entry_score=3.3,
            snapshot_id="snap-9",
            evidence_ids=("DEBATE-run1-r1",),
        )
        assert t.signal_id == "SIG-xyz"
        assert t.snapshot_id == "snap-9"
        assert t.feature_code_version == FEATURE_CODE_VERSION
        assert t.evidence_ids == ("DEBATE-run1-r1",)
        assert t.time_stop_trade_days == 30
