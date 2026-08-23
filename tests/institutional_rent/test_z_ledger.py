"""Unit tests for the Z-line ledger (append-only JSONL)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.institutional_rent.z_ledger import (
    append_record,
    load_records,
    main,
    make_record,
    summarize,
)


def test_make_record_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown ledger type"):
        make_record(type="lottery", code="x", name="", amount=1.0)


def test_make_record_requires_code_for_trades() -> None:
    with pytest.raises(ValueError, match="code is required"):
        make_record(type="ipo_win", code="", name="", amount=1.0)
    # cash_yield has no instrument code
    record = make_record(type="cash_yield", code="", name="", amount=250.0)
    assert record.amount == 250.0


def test_append_load_summarize_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    win = make_record(type="ipo_win", code="301689.SZ", name="电科思仪", amount=5000.0)
    sell = make_record(
        type="ipo_sell", code="301689.SZ", name="电科思仪", amount=12000.0
    )
    append_record(path, win)
    append_record(path, sell)
    append_record(path, make_record(type="cash_yield", code="", name="", amount=300.0))
    records = load_records(path)
    assert len(records) == 3
    summary = summarize(records)
    assert summary["realized_pnl"] == pytest.approx(12300.0)  # win cost is NOT P&L
    assert summary["ipo_win"] == pytest.approx(5000.0)
    assert summary["records"] == 3


def test_load_rejects_corrupt_type(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"type": "bogus", "amount": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unknown ledger type"):
        load_records(path)


def test_cli_add_and_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = str(tmp_path / "ledger.jsonl")
    assert main(["--ledger", ledger, "add", "--type", "cb_sell",
                 "--code", "123284.SZ", "--amount", "420.5"]) == 0
    assert main(["--ledger", ledger, "summary"]) == 0
    out = capsys.readouterr().out
    assert "420.5" in out
    assert "realized_pnl" in out
