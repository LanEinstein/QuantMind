"""AC-001 — PositionThesis.style landing, round-trip, legacy compat + adversarial.

The style label is display-only: stamping a different style must NOT change a
single numeric / threshold field of the thesis (the "标签不改任何数值字段" gate).
Legacy theses written before AC-001 (no ``style`` key) must still deserialize.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import pytest

from backend.models.position_thesis import PositionThesis, StyleTag
from backend.position_thesis.derivation import build_position_thesis
from backend.position_thesis.store import PositionThesisStore

_NOW = datetime(2026, 6, 12, 9, 35, tzinfo=UTC)


def _thesis(style: StyleTag | None) -> PositionThesis:
    return build_position_thesis(
        instruction_id="QM-20260612-093500-600519-BUY-001",
        signal_id="SIG-1",
        stock_code="600519",
        stock_name="标的",
        created_at=_NOW,
        trade_date="2026-06-12",
        pillars=("a", "b", "c"),
        entry_price=10.0,
        entry_score=2.0,
        snapshot_id="snap-1",
        style=style,
    )


class TestStyleLanding:
    @pytest.mark.unit
    def test_style_lands_on_thesis(self) -> None:
        assert _thesis(StyleTag.VALUE).style is StyleTag.VALUE
        assert _thesis(StyleTag.SHORT_TERM).style is StyleTag.SHORT_TERM

    @pytest.mark.unit
    def test_default_style_is_none_legacy(self) -> None:
        assert _thesis(None).style is None

    @pytest.mark.unit
    def test_legacy_json_without_style_deserializes(self) -> None:
        """A thesis JSON written before AC-001 has no ``style`` key → None."""
        import json

        payload = json.loads(_thesis(StyleTag.VALUE).model_dump_json())
        del payload["style"]
        revived = PositionThesis.model_validate_json(json.dumps(payload))
        assert revived.style is None

    @pytest.mark.unit
    def test_style_survives_json_round_trip(self) -> None:
        t = _thesis(StyleTag.VALUE)
        revived = PositionThesis.model_validate_json(t.model_dump_json())
        assert revived.style is StyleTag.VALUE


class TestStyleIsDisplayOnly:
    """Adversarial: the style label changes nothing numeric."""

    @pytest.mark.unit
    def test_style_does_not_change_any_numeric_field(self) -> None:
        value = _thesis(StyleTag.VALUE)
        short = _thesis(StyleTag.SHORT_TERM)
        legacy = _thesis(None)
        # Every field except ``style`` must be bit-identical across labels.
        for other in (short, legacy):
            a = value.model_dump(exclude={"style"})
            b = other.model_dump(exclude={"style"})
            assert a == b

    @pytest.mark.unit
    def test_invalidation_conditions_style_invariant(self) -> None:
        """The deterministic SELL thresholds never see the style label."""
        assert (
            _thesis(StyleTag.VALUE).invalidation_conditions
            == _thesis(StyleTag.SHORT_TERM).invalidation_conditions
            == _thesis(None).invalidation_conditions
        )


class TestStyleStoreRoundTrip:
    @pytest.mark.unit
    def test_store_preserves_style(self, tmp_path: pathlib.Path) -> None:
        store = PositionThesisStore(tmp_path / "theses.jsonl")
        store.open_thesis(_thesis(StyleTag.VALUE))
        # Fresh instance reads the same file from disk.
        reread = PositionThesisStore(tmp_path / "theses.jsonl").thesis_for("600519")
        assert reread is not None
        assert reread.style is StyleTag.VALUE
