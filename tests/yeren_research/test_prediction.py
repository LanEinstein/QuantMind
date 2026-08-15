"""M3-A prediction registry and settlement tests."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.yeren_research.prediction import (
    hit_rate_stats,
    settle_market,
    settle_with_observables,
)
from scripts.yeren_research.schema import (
    PredictionDirection,
    PredictionRecord,
    PredictionVerdict,
    SettlementKind,
)

PUBLISHED = datetime.fromisoformat("2026-08-12T20:45:40+08:00")
RECORDED = datetime.fromisoformat("2026-08-15T09:00:00+08:00")


def _record(**overrides: object) -> PredictionRecord:
    fields: dict[str, object] = {
        "prediction_id": "pred-test-1",
        "aweme_id": "7673125459068214178",
        "published_at": PUBLISHED,
        "recorded_at": RECORDED,
        "source_statement_ids": ("214178-statement-x",),
        "claim_text": "明天普涨",
        "object_kind": "market",
        "object_spec": "全市场广度与中位收益",
        "direction": PredictionDirection.UP,
        "settle_kind": SettlementKind.BREADTH_MEDIAN,
        "window_start": "20260813",
        "window_end": "20260813",
    }
    fields.update(overrides)
    return PredictionRecord(**fields)


def _obs(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "trade_date": "20260813",
        "advance": 4128,
        "decline": 1280,
        "unchanged": 131,
        "row_count": 5539,
        "median_pct": 0.8361,
        "amount_thousand_yuan": 2.1e12,
        "prev_amount_thousand_yuan": 2.3e12,
    }
    fields.update(overrides)
    return fields


class TestSchema:
    def test_minimal_record_validates(self) -> None:
        record = _record()
        assert record.verdict == PredictionVerdict.UNSETTLED
        assert record.settlement is None

    def test_window_must_be_yyyymmdd(self) -> None:
        with pytest.raises(ValidationError):
            _record(window_start="2026-08-13")

    def test_window_end_cannot_precede_start(self) -> None:
        with pytest.raises(ValidationError):
            _record(window_start="20260813", window_end="20260812")

    def test_recorded_at_cannot_precede_publish(self) -> None:
        with pytest.raises(ValidationError):
            _record(
                recorded_at=datetime.fromisoformat("2026-08-12T10:00:00+08:00"),
            )

    def test_naive_times_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _record(published_at=datetime(2026, 8, 12, 20, 45))

    def test_settle_returns_new_record_and_leaves_original(self) -> None:
        record = _record()
        settled = settle_with_observables(record, _obs())
        assert settled is not record
        assert record.settlement is None
        assert record.verdict == PredictionVerdict.UNSETTLED
        assert settled.settlement is not None


class TestSettlement:
    def test_breadth_median_up_hits_on_strong_day(self) -> None:
        settled = settle_with_observables(_record(), _obs())
        assert settled.verdict == PredictionVerdict.HIT

    def test_breadth_median_up_misses_when_breadth_inverts(self) -> None:
        settled = settle_with_observables(
            _record(), _obs(advance=1200, decline=4300, median_pct=-1.4)
        )
        assert settled.verdict == PredictionVerdict.MISS

    def test_breadth_median_mixed_feedback_keeps_claim_miss(self) -> None:
        # breadth strong but median negative: the two-part contract fails
        settled = settle_with_observables(
            _record(), _obs(advance=4100, decline=1300, median_pct=-0.4)
        )
        assert settled.verdict == PredictionVerdict.MISS

    def test_breadth_down_hits_on_weak_day(self) -> None:
        record = _record(
            direction=PredictionDirection.DOWN,
            settle_kind=SettlementKind.BREADTH,
        )
        settled = settle_with_observables(
            record, _obs(advance=1615, decline=3777, median_pct=-0.68)
        )
        assert settled.verdict == PredictionVerdict.HIT

    def test_median_only_ignores_breadth(self) -> None:
        record = _record(settle_kind=SettlementKind.MEDIAN)
        settled = settle_with_observables(
            record, _obs(advance=1615, decline=3777, median_pct=0.2)
        )
        assert settled.verdict == PredictionVerdict.HIT

    def test_volume_delta_down_hits_on_shrinking_amount(self) -> None:
        record = _record(
            direction=PredictionDirection.DOWN,
            settle_kind=SettlementKind.VOLUME_DELTA,
        )
        settled = settle_with_observables(
            record,
            _obs(amount_thousand_yuan=2.1e12, prev_amount_thousand_yuan=2.3e12),
        )
        assert settled.verdict == PredictionVerdict.HIT

    def test_volume_delta_missing_previous_is_not_auto_verdict(self) -> None:
        record = _record(
            direction=PredictionDirection.DOWN,
            settle_kind=SettlementKind.VOLUME_DELTA,
        )
        pending = settle_with_observables(
            record.model_copy(update={"prediction_id": "pred-test-2"}),
            _obs(prev_amount_thousand_yuan=None),
        )
        assert pending.verdict == PredictionVerdict.UNSETTLED
        assert pending.settlement is not None

    def test_flat_breadth_hits_on_balanced_day(self) -> None:
        record = _record(
            direction=PredictionDirection.FLAT,
            settle_kind=SettlementKind.BREADTH,
        )
        settled = settle_with_observables(
            record, _obs(advance=2603, decline=2769, row_count=5539)
        )
        assert settled.verdict == PredictionVerdict.HIT

    def test_flat_breadth_misses_on_one_sided_day(self) -> None:
        record = _record(
            direction=PredictionDirection.FLAT,
            settle_kind=SettlementKind.BREADTH,
        )
        settled = settle_with_observables(record, _obs())
        assert settled.verdict == PredictionVerdict.MISS

    def test_conditional_prediction_never_auto_verdicts(self) -> None:
        record = _record(branch_trigger="下一交易日放量站上 4000 点")
        settled = settle_with_observables(record, _obs())
        assert settled.verdict == PredictionVerdict.UNSETTLED
        assert settled.settlement is not None

    def test_volume_up_hits_on_expanding_amount(self) -> None:
        record = _record(
            direction=PredictionDirection.UP,
            settle_kind=SettlementKind.VOLUME_DELTA,
        )
        settled = settle_with_observables(
            record,
            _obs(amount_thousand_yuan=2.3e12, prev_amount_thousand_yuan=2.1e12),
        )
        assert settled.verdict == PredictionVerdict.HIT

    def test_volume_up_misses_on_shrinking_amount(self) -> None:
        record = _record(
            direction=PredictionDirection.UP,
            settle_kind=SettlementKind.VOLUME_DELTA,
        )
        settled = settle_with_observables(
            record,
            _obs(amount_thousand_yuan=2.1e12, prev_amount_thousand_yuan=2.3e12),
        )
        assert settled.verdict == PredictionVerdict.MISS

    def test_other_direction_never_auto_verdicts(self) -> None:
        record = _record(direction=PredictionDirection.OTHER)
        settled = settle_with_observables(record, _obs())
        assert settled.verdict == PredictionVerdict.UNSETTLED

    def test_median_flat_never_auto_verdicts(self) -> None:
        record = _record(
            direction=PredictionDirection.FLAT,
            settle_kind=SettlementKind.MEDIAN,
        )
        settled = settle_with_observables(record, _obs())
        assert settled.verdict == PredictionVerdict.UNSETTLED


    def test_manual_kinds_never_auto_verdict(self) -> None:
        record = _record(settle_kind=SettlementKind.EVENT_FACT)
        settled = settle_with_observables(record, _obs())
        assert settled.verdict == PredictionVerdict.UNSETTLED


class TestAdapter:
    def test_manual_kinds_pass_through_untouched(self, tmp_path: Path) -> None:
        record = _record(settle_kind=SettlementKind.EVENT_FACT)
        assert settle_market(record, tmp_path) is record

    def test_missing_archive_is_beyond_coverage(self, tmp_path: Path) -> None:
        (tmp_path / "index.jsonl").write_text("", encoding="utf-8")
        settled = settle_market(_record(), tmp_path)
        assert settled.verdict == PredictionVerdict.BEYOND_COVERAGE


    def test_invalid_calendar_window_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _record(window_start="20261399")

    def test_flat_breadth_median_stays_unsettled(self) -> None:
        record = _record(direction=PredictionDirection.FLAT)
        settled = settle_with_observables(record, _obs())
        assert settled.verdict == PredictionVerdict.UNSETTLED

    def test_all_missing_pct_chg_stays_unsettled(self) -> None:
        bad = _obs(advance=None, decline=None, unchanged=None, median_pct=None)
        up = settle_with_observables(_record(), bad)
        assert up.verdict == PredictionVerdict.UNSETTLED
        down = settle_with_observables(
            _record(
                direction=PredictionDirection.DOWN,
                settle_kind=SettlementKind.BREADTH,
            ),
            bad,
        )
        assert down.verdict == PredictionVerdict.UNSETTLED

    def test_equal_amounts_are_tie(self) -> None:
        record = _record(
            direction=PredictionDirection.DOWN,
            settle_kind=SettlementKind.VOLUME_DELTA,
        )
        settled = settle_with_observables(
            record,
            _obs(amount_thousand_yuan=2.0e12, prev_amount_thousand_yuan=2.0e12),
        )
        assert settled.verdict == PredictionVerdict.TIE

    def test_manual_rationale_preserved_on_resettle(self) -> None:
        record = _record(
            settle_kind=SettlementKind.EVENT_FACT,
            verdict=PredictionVerdict.HIT,
            verdict_rationale="official reading matched claim",
        )
        resettled = settle_with_observables(record, _obs())
        assert resettled.verdict == PredictionVerdict.HIT
        assert resettled.verdict_rationale == "official reading matched claim"


class TestLookahead:
    def test_post_close_same_day_window_raises(self, tmp_path: Path) -> None:
        (tmp_path / "index.jsonl").write_text("", encoding="utf-8")
        record = _record(window_start="20260812", window_end="20260812")
        with pytest.raises(ValueError, match="lookahead"):
            settle_market(record, tmp_path)

    def test_multi_day_window_stays_unsettled(self, tmp_path: Path) -> None:
        (tmp_path / "index.jsonl").write_text("", encoding="utf-8")
        record = _record(window_start="20260813", window_end="20260817")
        settled = settle_market(record, tmp_path)
        assert settled.verdict == PredictionVerdict.UNSETTLED
        assert settled.verdict_rationale is not None

    def test_same_day_pre_close_window_is_not_lookahead(self, tmp_path: Path) -> None:
        (tmp_path / "index.jsonl").write_text("", encoding="utf-8")
        record = _record(
            published_at=datetime.fromisoformat("2026-08-12T09:00:00+08:00"),
            window_start="20260812",
            window_end="20260812",
        )
        settled = settle_market(record, tmp_path)
        assert settled.verdict == PredictionVerdict.BEYOND_COVERAGE


class TestStats:
    def test_counts_and_rates(self) -> None:
        weak_day = _obs(advance=1500, decline=3900, median_pct=-1.2)
        records = [
            _record(prediction_id=f"pred-test-{i}", **kw)
            for i, kw in enumerate(
                [
                    {},
                    {},
                    {},
                    {
                        "direction": PredictionDirection.DOWN,
                        "settle_kind": SettlementKind.BREADTH,
                    },
                    {
                        "settle_kind": SettlementKind.NOT_SETTLEABLE,
                        "verdict": PredictionVerdict.NOT_SETTLEABLE,
                        "verdict_rationale": "no frozen observable",
                    },
                    {"window_start": "20260814", "window_end": "20260814"},
                ]
            )
        ]
        settled = [
            settle_with_observables(r, weak_day)
            if r.settle_kind != SettlementKind.NOT_SETTLEABLE
            and r.window_start == "20260813"
            else r
            for r in records
        ]
        stats = hit_rate_stats(settled)
        assert stats["hit"] == 1
        assert stats["miss"] == 3
        assert stats["unsettled"] == 1
        assert stats["not_settleable"] == 1
        assert stats["hit_rate_excluding_ties"] == 0.25
