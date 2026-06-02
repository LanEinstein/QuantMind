"""W-001 store — append-only round-trip + open/close lifecycle + idempotency."""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import pytest

from backend.position_thesis.derivation import build_position_thesis
from backend.position_thesis.store import (
    PositionThesisError,
    PositionThesisStore,
)

_NOW = datetime(2026, 6, 2, 9, 35, tzinfo=UTC)


def _thesis(code: str = "600519", seq: str = "001", price: float = 10.0):
    return build_position_thesis(
        instruction_id=f"QM-20260602-093500-{code}-BUY-{seq}",
        signal_id="SIG-1",
        stock_code=code,
        stock_name="标的",
        created_at=_NOW,
        trade_date="2026-06-02",
        pillars=("a", "b", "c"),
        entry_price=price,
        entry_score=2.0,
        snapshot_id="snap-1",
    )


def _store(tmp_path: pathlib.Path) -> PositionThesisStore:
    return PositionThesisStore(tmp_path / "theses.jsonl")


class TestRoundTrip:
    @pytest.mark.unit
    def test_open_then_read_back_equal(self, tmp_path: pathlib.Path) -> None:
        store = _store(tmp_path)
        t = _thesis()
        assert store.open_thesis(t) is True
        assert store.thesis_for("600519") == t

    @pytest.mark.unit
    def test_empty_store_returns_none(self, tmp_path: pathlib.Path) -> None:
        assert _store(tmp_path).thesis_for("600519") is None
        assert _store(tmp_path).open_theses() == {}

    @pytest.mark.unit
    def test_reload_from_disk(self, tmp_path: pathlib.Path) -> None:
        _store(tmp_path).open_thesis(_thesis())
        # Fresh instance reads the same file.
        assert _store(tmp_path).thesis_for("600519") is not None


class TestLifecycle:
    @pytest.mark.unit
    def test_close_retires_thesis(self, tmp_path: pathlib.Path) -> None:
        store = _store(tmp_path)
        store.open_thesis(_thesis())
        assert store.close_position("600519", trade_date="2026-06-03") is True
        assert store.thesis_for("600519") is None

    @pytest.mark.unit
    def test_resold_code_gets_fresh_thesis(self, tmp_path: pathlib.Path) -> None:
        store = _store(tmp_path)
        store.open_thesis(_thesis(seq="001", price=10.0))
        store.close_position("600519", trade_date="2026-06-03")
        store.open_thesis(_thesis(seq="002", price=20.0))
        fresh = store.thesis_for("600519")
        assert fresh is not None
        assert fresh.entry_price == 20.0
        assert fresh.instruction_id.endswith("BUY-002")

    @pytest.mark.unit
    def test_sync_holdings_closes_exited(self, tmp_path: pathlib.Path) -> None:
        store = _store(tmp_path)
        store.open_thesis(_thesis(code="600519"))
        store.open_thesis(_thesis(code="000001"))
        closed = store.sync_holdings(
            frozenset({"600519"}), trade_date="2026-06-03"
        )
        assert closed == ("000001",)
        assert set(store.open_theses()) == {"600519"}


class TestIdempotency:
    @pytest.mark.unit
    def test_reopen_same_instruction_is_noop(self, tmp_path: pathlib.Path) -> None:
        store = _store(tmp_path)
        t = _thesis()
        assert store.open_thesis(t) is True
        assert store.open_thesis(t) is False  # same instruction_id → skip
        # Only one OPENED event on disk.
        assert store.path.read_text().count("opened") == 1

    @pytest.mark.unit
    def test_close_when_none_open_is_noop(self, tmp_path: pathlib.Path) -> None:
        assert _store(tmp_path).close_position("600519", trade_date="x") is False


class TestCorruption:
    @pytest.mark.unit
    def test_corrupt_row_raises(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "theses.jsonl"
        path.write_text("{not json}\n", encoding="utf-8")
        with pytest.raises(PositionThesisError):
            PositionThesisStore(path).open_theses()
