"""W-004 — monitoring.thesis_break deterministic THESIS_QUANT_BREAK evaluator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.models.position_thesis import InvalidationTemplate
from backend.monitoring.thesis_break import ThesisBreak, evaluate_thesis_breaks
from backend.position_thesis.derivation import build_position_thesis

_NOW = datetime(2026, 6, 2, 10, 0, tzinfo=UTC)


def _thesis(code: str = "600519", *, price: float = 10.0, score: float = 2.0):
    return build_position_thesis(
        instruction_id=f"QM-20260601-093500-{code}-BUY-001",
        signal_id="SIG-1",
        stock_code=code,
        stock_name="标的",
        created_at=_NOW,
        trade_date="2026-06-01",
        pillars=("a", "b", "c"),
        entry_price=price,
        entry_score=score,
        snapshot_id="snap-1",
    )


class TestEvaluate:
    @pytest.mark.unit
    def test_broken_on_anchor_drawdown(self) -> None:
        theses = {"600519": _thesis(price=10.0)}  # anchor floor 8.8
        breaks = evaluate_thesis_breaks(theses, price_by_code={"600519": 8.0})
        assert "600519" in breaks
        b = breaks["600519"]
        assert isinstance(b, ThesisBreak)
        assert InvalidationTemplate.ANCHOR_DRAWDOWN in b.broken_templates
        assert "买入逻辑失效" in b.reason

    @pytest.mark.unit
    def test_intact_position_omitted(self) -> None:
        theses = {"600519": _thesis(price=10.0)}
        breaks = evaluate_thesis_breaks(theses, price_by_code={"600519": 9.5})
        assert breaks == {}

    @pytest.mark.unit
    def test_broken_on_time_stop(self) -> None:
        theses = {"600519": _thesis()}  # time stop 30 td
        breaks = evaluate_thesis_breaks(
            theses,
            price_by_code={"600519": 10.0},
            holding_trade_days_by_code={"600519": 31},
        )
        assert InvalidationTemplate.TIME_STOP in breaks["600519"].broken_templates

    @pytest.mark.unit
    def test_unavailable_metrics_never_break(self) -> None:
        # No price, no holding days, no score → nothing evaluable → no break.
        theses = {"600519": _thesis()}
        breaks = evaluate_thesis_breaks(theses, price_by_code={})
        assert breaks == {}

    @pytest.mark.unit
    def test_no_theses_is_empty(self) -> None:
        assert evaluate_thesis_breaks({}, price_by_code={"600519": 1.0}) == {}

    @pytest.mark.unit
    def test_deterministic(self) -> None:
        theses = {"600519": _thesis(price=10.0)}
        a = evaluate_thesis_breaks(theses, price_by_code={"600519": 8.0})
        b = evaluate_thesis_breaks(theses, price_by_code={"600519": 8.0})
        assert a == b

    @pytest.mark.unit
    def test_only_broken_codes_returned(self) -> None:
        theses = {"600519": _thesis(price=10.0), "000001": _thesis(price=20.0)}
        breaks = evaluate_thesis_breaks(
            theses, price_by_code={"600519": 8.0, "000001": 19.5}
        )
        # 600519 broke (8.0 < 8.8); 000001 intact (19.5 > 17.6 floor).
        assert set(breaks) == {"600519"}
