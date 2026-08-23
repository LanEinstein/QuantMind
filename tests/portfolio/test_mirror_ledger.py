"""Tests for the R-line mirror ledger (record → replay → book)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from backend.models.manual_trade import (
    ExternalExecutionEvent,
    ManualTradeReason,
    ManualTradeSide,
)
from backend.portfolio.mirror_ledger import (
    MirrorDriftError,
    append_adjust,
    append_cash,
    append_fill,
    load_book,
)

SHANGHAI = dt.timezone(dt.timedelta(hours=8))
_NOW = "2026-08-24T18:00:00+08:00"


def _event(
    *,
    seq: str = "001",
    code: str = "002271",
    side: ManualTradeSide = ManualTradeSide.BUY,
    volume: int = 5000,
    price: float = 12.30,
    hhmmss: str = "101200",
) -> ExternalExecutionEvent:
    return ExternalExecutionEvent(
        external_trade_id=f"UT-20260824-{hhmmss}-{code}-{side.value}-{seq}",
        code=code,
        side=side,
        volume=volume,
        price=price,
        executed_at=dt.datetime(
            2026, 8, 24, int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:]),
            tzinfo=SHANGHAI,
        ),
        reason=ManualTradeReason.USER_OTHER,
    )


def test_empty_ledger_loads_empty_book(tmp_path: Path) -> None:
    book = load_book(tmp_path / "ledger.jsonl")
    assert book.positions == ()
    assert book.cash == 0.0
    assert not book.opening_declared


def test_buy_books_fee_inclusive_cost_and_cash(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_cash(path, amount=100_000.0, note="opening", recorded_at=_NOW)
    row = append_fill(path, _event(), recorded_at=_NOW)
    assert row is not None
    # 5000 × 12.30 = 61500 gross; commission 万1.5 = 9.23 (>5 floor);
    # SZ code but 002xxx has no transfer fee on SZ (transfer fee is SH).
    book = load_book(path)
    pos = book.position_for("002271")
    assert pos is not None and pos.volume == 5000
    assert pos.avg_cost == pytest.approx(row["net"] / 5000, abs=1e-4)
    assert book.cash == pytest.approx(100_000.0 - row["net"], abs=0.01)
    assert book.opening_declared


def test_duplicate_external_trade_id_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    assert append_fill(path, _event(), recorded_at=_NOW) is not None
    assert append_fill(path, _event(), recorded_at=_NOW) is None
    assert load_book(path).position_for("002271").volume == 5000


def test_sell_reduces_position_keeps_avg_and_credits_cash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(), recorded_at=_NOW)
    buy_book = load_book(path)
    sell = _event(
        seq="002", side=ManualTradeSide.SELL, volume=2000, price=13.0,
        hhmmss="140000",
    )
    row = append_fill(path, sell, recorded_at=_NOW)
    book = load_book(path)
    pos = book.position_for("002271")
    assert pos.volume == 3000
    assert pos.avg_cost == buy_book.position_for("002271").avg_cost  # unchanged
    assert book.cash == pytest.approx(buy_book.cash + row["net"], abs=0.01)


def test_full_exit_resets_cost_basis(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(volume=100, price=10.0), recorded_at=_NOW)
    append_fill(
        path,
        _event(seq="002", side=ManualTradeSide.SELL, volume=100, price=11.0,
               hhmmss="140000"),
        recorded_at=_NOW,
    )
    book = load_book(path)
    assert book.position_for("002271") is None
    # A later re-entry starts a fresh basis, not a blend with the old one.
    append_fill(
        path, _event(seq="003", volume=100, price=20.0, hhmmss="145900"),
        recorded_at=_NOW,
    )
    fresh = load_book(path).position_for("002271")
    assert fresh.avg_cost > 20.0  # fee-inclusive, near 20 — never near 10/15


def test_oversell_rejected_before_write(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(volume=100, price=10.0), recorded_at=_NOW)
    with pytest.raises(MirrorDriftError, match="exceeds"):
        append_fill(
            path,
            _event(seq="002", side=ManualTradeSide.SELL, volume=200,
                   price=10.0, hhmmss="140000"),
            recorded_at=_NOW,
        )
    # Nothing was written — the book still replays cleanly.
    assert load_book(path).position_for("002271").volume == 100


def test_odd_lot_sell_books(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(volume=100, price=10.0), recorded_at=_NOW)
    append_fill(
        path,
        _event(seq="002", side=ManualTradeSide.SELL, volume=33, price=10.0,
               hhmmss="140000"),
        recorded_at=_NOW,
    )
    assert load_book(path).position_for("002271").volume == 67


def test_replay_orders_by_executed_at_not_append_order(tmp_path: Path) -> None:
    # A late back-filled morning BUY must land BEFORE the afternoon SELL
    # even though it was appended after it.
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(volume=100, price=10.0, hhmmss="093100"),
                recorded_at=_NOW)
    # The 14:30 SELL arrives first — guard rejects it (only 100 held);
    # the owner then back-fills the 10:00 BUY, and the SELL books cleanly,
    # replayed in executed_at order rather than append order.
    with pytest.raises(MirrorDriftError):
        append_fill(
            path,
            _event(seq="002", side=ManualTradeSide.SELL, volume=150,
                   price=11.0, hhmmss="143000"),
            recorded_at=_NOW,
        )
    append_fill(path, _event(seq="003", volume=100, price=10.5,
                             hhmmss="100000"), recorded_at=_NOW)
    append_fill(
        path,
        _event(seq="004", side=ManualTradeSide.SELL, volume=150, price=11.0,
               hhmmss="143000"),
        recorded_at=_NOW,
    )
    assert load_book(path).position_for("002271").volume == 50


def test_backfilled_sell_before_recorded_buy_rejected(tmp_path: Path) -> None:
    # codex P1: 100 held in the morning, 100 more bought at 14:00 (already
    # recorded) — a back-filled 10:00 sale of 150 must NOT append (it would
    # break every later replay), even though the CURRENT holding is 200.
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(volume=100, price=10.0, hhmmss="093000"),
                recorded_at=_NOW)
    append_fill(path, _event(seq="002", volume=100, price=10.2,
                             hhmmss="140000"), recorded_at=_NOW)
    with pytest.raises(MirrorDriftError):
        append_fill(
            path,
            _event(seq="003", side=ManualTradeSide.SELL, volume=150,
                   price=10.1, hhmmss="100000"),
            recorded_at=_NOW,
        )
    assert load_book(path).position_for("002271").volume == 200  # untouched


def test_adjust_effective_at_orders_before_intraday_fills(
    tmp_path: Path,
) -> None:
    # Drift-repair flow: sell rejected → owner confirms the real holding →
    # the re-reported MORNING sell must replay AFTER the correction.
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(volume=100, price=10.0, hhmmss="093000"),
                recorded_at=_NOW)
    append_adjust(
        path, code="002271", volume_delta=100,
        note="owner-confirmed holding", recorded_at=_NOW,
        effective_at="2026-08-24T00:00:00+08:00",
    )
    append_fill(
        path,
        _event(seq="002", side=ManualTradeSide.SELL, volume=150, price=10.1,
               hhmmss="100000"),
        recorded_at=_NOW,
    )
    assert load_book(path).position_for("002271").volume == 50


def test_adjust_repairs_drift(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(volume=100, price=10.0), recorded_at=_NOW)
    append_adjust(path, code="002271", volume_delta=100,
                  note="owner-confirmed holding", recorded_at=_NOW)
    assert load_book(path).position_for("002271").volume == 200


def test_cash_withdraw_and_negative_cash_allowed(tmp_path: Path) -> None:
    # The owner's real account is the truth: an undeclared opening makes
    # cash a running delta (possibly negative), disclosed — never an error.
    path = tmp_path / "ledger.jsonl"
    append_fill(path, _event(volume=100, price=10.0), recorded_at=_NOW)
    book = load_book(path)
    assert book.cash < 0
    assert not book.opening_declared
    append_cash(path, amount=-500.0, note="withdraw", recorded_at=_NOW)
    assert load_book(path).cash == pytest.approx(book.cash - 500.0, abs=0.01)


def test_corrupt_row_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"kind": "fill"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        load_book(path)
