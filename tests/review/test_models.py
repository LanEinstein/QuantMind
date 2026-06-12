"""AA-002 ReviewRecord model invariants (P1-2.A-amendment-2026-06-12 §1.3)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.review.models import (
    ATTRIBUTION_CODE_VERSION,
    REVIEW_SCHEMA_VERSION,
    CounterfactualEntry,
    CounterfactualKind,
    DailyReviewRecord,
    TradeFact,
    TradeSide,
    VwapQuality,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = dt.datetime(2026, 6, 12, 18, 0, tzinfo=SHANGHAI)


def _fact(**overrides: object) -> TradeFact:
    base: dict[str, object] = {
        "trade_id": "T-1",
        "order_id": "O-1",
        "code": "600519",
        "side": TradeSide.BUY,
        "volume": 200,
        "price": 12.34,
        "amount": 2468.0,
        "traded_at": NOW,
        "commission": 5.0,
        "stamp_tax": 0.0,
        "transfer_fee": 0.0,
        "slippage_cost": 0.37,
    }
    base.update(overrides)
    return TradeFact(**base)  # type: ignore[arg-type]


class TestTradeFact:
    def test_minimal_fact_defaults_to_missing_vwap(self) -> None:
        fact = _fact()
        assert fact.day_vwap is None
        assert fact.vwap_quality is VwapQuality.MISSING
        assert fact.policy_hash is None
        assert fact.style is None

    def test_vwap_bps_without_vwap_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires day_vwap"):
            _fact(execution_vs_vwap_bps=12.0)

    def test_recorded_vwap_requires_ok_quality(self) -> None:
        with pytest.raises(ValidationError, match="vwap_quality"):
            _fact(day_vwap=12.30, vwap_quality=VwapQuality.MISSING)

    def test_holding_return_requires_entry_cost(self) -> None:
        with pytest.raises(ValidationError, match="entry_cost_price"):
            _fact(side=TradeSide.SELL, holding_return_pct=0.10)

    def test_frozen(self) -> None:
        fact = _fact()
        with pytest.raises(ValidationError):
            fact.price = 99.0  # type: ignore[misc]


class TestCounterfactualEntry:
    def test_pre_registered_signal_may_be_promotable(self) -> None:
        entry = CounterfactualEntry(
            signal_id="QM-20260612-093500-000001-HOLD-001",
            kind=CounterfactualKind.HOLD_PLAN,
            pre_registered=True,
            promotable=True,
        )
        assert entry.promotable

    def test_default_is_non_promotable(self) -> None:
        entry = CounterfactualEntry(
            signal_id="missed-runner-600519",
            kind=CounterfactualKind.HYPOTHETICAL,
            pre_registered=False,
        )
        assert entry.promotable is False

    def test_hindsight_promotable_rejected(self) -> None:
        """The anti-hindsight red line (codex P2-6)."""
        with pytest.raises(ValidationError, match="anti-hindsight"):
            CounterfactualEntry(
                signal_id="missed-runner-600519",
                kind=CounterfactualKind.HYPOTHETICAL,
                pre_registered=False,
                promotable=True,
            )

    def test_hypothetical_cannot_claim_pre_registration(self) -> None:
        with pytest.raises(ValidationError, match="not.*pre-registered"):
            CounterfactualEntry(
                signal_id="missed-runner-600519",
                kind=CounterfactualKind.HYPOTHETICAL,
                pre_registered=True,
            )


class TestDailyReviewRecord:
    def test_round_trip_with_versions(self) -> None:
        record = DailyReviewRecord(
            trade_date="2026-06-12",
            created_at=NOW,
            policy_hash=None,
            trade_facts=(_fact(),),
            risk_rejected_count=2,
            builder_early_return_count=1,
        )
        assert record.schema_version == REVIEW_SCHEMA_VERSION
        assert record.attribution_code_version == ATTRIBUTION_CODE_VERSION
        revived = DailyReviewRecord.model_validate(
            record.model_dump(mode="python"), strict=False
        )
        assert revived == record

    def test_no_free_prose_field_exists(self) -> None:
        """LLM-zero-write by construction: no str field accepts prose.

        The only string fields are ids / hashes / enums-with-pattern;
        a ``notes``/``summary``-style field must never be added without
        an amendment (it would be the camel's nose for LLM review text
        inside the promotion-evidence store).
        """
        free_text_names = {"notes", "summary", "comment", "reasoning"}
        assert free_text_names.isdisjoint(
            DailyReviewRecord.model_fields.keys()
        )
        assert free_text_names.isdisjoint(TradeFact.model_fields.keys())
        assert free_text_names.isdisjoint(
            CounterfactualEntry.model_fields.keys()
        )

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            DailyReviewRecord(
                trade_date="2026-06-12",
                created_at=NOW,
                llm_summary="injected",  # type: ignore[call-arg]
            )


class TestClosedThesesOn:
    """Codex Phase-AA P2 — same-day closed theses stay visible to the
    18:00 attribution review (the 17:30 sync retires them first)."""

    def test_closed_today_is_returned(self, tmp_path) -> None:
        import datetime as _dt

        from backend.position_thesis.derivation import build_position_thesis
        from backend.position_thesis.store import PositionThesisStore

        store = PositionThesisStore(tmp_path / "theses.jsonl")
        thesis = build_position_thesis(
            instruction_id="QM-20260610-093500-600519-BUY-001",
            signal_id="SIG-1",
            stock_code="600519",
            stock_name="标的",
            created_at=_dt.datetime(2026, 6, 10, 9, 40, tzinfo=_dt.UTC),
            trade_date="2026-06-10",
            pillars=("p1", "p2", "p3"),
            entry_price=12.00,
            entry_score=2.0,
            snapshot_id="snap-1",
        )
        assert store.open_thesis(thesis)
        store.close_position("600519", trade_date="2026-06-12")
        closed = store.closed_theses_on("2026-06-12")
        assert closed["600519"].entry_price == 12.00
        # Other dates see nothing; open_theses no longer has it.
        assert store.closed_theses_on("2026-06-11") == {}
        assert store.open_theses() == {}
