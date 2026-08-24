"""Tests for the free-text reconciliation loop (fake LLM; zero network)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from backend.models.manual_trade import (
    ExternalExecutionEvent,
    ManualTradeReason,
    ManualTradeSide,
)
from backend.portfolio.mirror_ledger import append_fill, load_book
from backend.portfolio.reconcile import (
    ReconcileExtraction,
    build_extraction_prompt,
    handle_owner_text,
    mint_external_id,
    missing_fill_fields,
    parse_extraction,
)
from backend.portfolio.sleeve_push_state import (
    AWAITING_KEY,
    load_push_state,
    save_push_state,
)

SHANGHAI = dt.timezone(dt.timedelta(hours=8))
RECEIVED = dt.datetime(2026, 8, 24, 15, 30, tzinfo=SHANGHAI)


def _fake_llm(payload: dict | str):
    async def complete(_prompt: str) -> str:
        return payload if isinstance(payload, str) else json.dumps(payload)

    return complete


def _paths(tmp_path: Path) -> dict[str, Path]:
    push_state = tmp_path / "push_state.json"
    save_push_state(
        push_state,
        {
            "last_sent_status": "ACCRUING",
            AWAITING_KEY: {"hash": "h", "delivered_asof": "20260824"},
        },
    )
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(
            {
                "advisory": {
                    "holdings": [
                        {"ts_code": "002271.SZ", "name": "东方雨虹"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return {
        "ledger_path": tmp_path / "mirror.jsonl",
        "z_ledger_path": tmp_path / "z.jsonl",
        "push_state_path": push_state,
        "status_path": status,
    }


async def _run(tmp_path: Path, payload: dict | str, text: str = "买了"):
    paths = _paths(tmp_path)
    result = await handle_owner_text(
        text,
        received_at=RECEIVED,
        complete_fn=_fake_llm(payload),
        **paths,
    )
    return result, paths


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #


def test_parse_extraction_accepts_fenced_json() -> None:
    raw = '```json\n{"outcome": "unfilled"}\n```'
    parsed = parse_extraction(raw)
    assert parsed is not None and parsed.outcome == "unfilled"


def test_parse_extraction_rejects_garbage_and_bad_schema() -> None:
    assert parse_extraction("今天没买") is None
    assert parse_extraction('{"outcome": "bought"}') is None
    assert parse_extraction('{"outcome": "filled", "extra_field": 1}') is None


def test_missing_fill_fields_and_unit_never_guessed() -> None:
    e = ReconcileExtraction(
        outcome="filled", code="002271", side="BUY", volume=50, price=12.3
    )
    assert missing_fill_fields(e) == ["volume"]  # unit unknown → unusable
    e2 = e.model_copy(update={"volume_unit": "lots"})
    assert missing_fill_fields(e2) == []


def test_mint_external_id_advances_sequence() -> None:
    first = mint_external_id(
        code="002271", side="BUY", executed_at=RECEIVED, existing=frozenset()
    )
    assert first == "UT-20260824-153000-002271-BUY-001"
    second = mint_external_id(
        code="002271", side="BUY", executed_at=RECEIVED,
        existing=frozenset({first}),
    )
    assert second.endswith("-002")


def test_prompt_contains_context_and_raw_text() -> None:
    prompt = build_extraction_prompt(
        "买了东方雨虹",
        advisory_holdings=[{"ts_code": "002271.SZ", "name": "东方雨虹"}],
        mirror_positions=[{"code": "000858", "volume": 100}],
    )
    assert "002271" in prompt and "000858" in prompt and "买了东方雨虹" in prompt


# --------------------------------------------------------------------------- #
# End-to-end handler branches                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_filled_books_and_clears_awaiting(tmp_path: Path) -> None:
    result, paths = await _run(
        tmp_path,
        {
            "outcome": "filled",
            "code": "002271",
            "side": "BUY",
            "volume": 5000,
            "volume_unit": "shares",
            "price": 12.3,
        },
        text="买了东方雨虹5000股,成交12.3",
    )
    assert result.booked
    assert "已记录-用户自主操作" in result.reply_text
    assert "002271" in result.reply_text
    book = load_book(paths["ledger_path"])
    assert book.position_for("002271").volume == 5000
    assert AWAITING_KEY not in load_push_state(paths["push_state_path"])


@pytest.mark.asyncio
async def test_lots_are_normalized_to_shares(tmp_path: Path) -> None:
    result, paths = await _run(
        tmp_path,
        {
            "outcome": "filled",
            "code": "002271",
            "side": "BUY",
            "volume": 50,
            "volume_unit": "lots",
            "price": 12.3,
        },
    )
    assert result.booked
    assert load_book(paths["ledger_path"]).position_for("002271").volume == 5000


@pytest.mark.asyncio
async def test_missing_price_asks_once_and_books_nothing(tmp_path: Path) -> None:
    result, paths = await _run(
        tmp_path,
        {
            "outcome": "filled",
            "code": "002271",
            "side": "BUY",
            "volume": 5000,
            "volume_unit": "shares",
        },
    )
    assert not result.booked
    assert "对账追问" in result.reply_text and "成交价格" in result.reply_text
    assert load_book(paths["ledger_path"]).positions == ()
    # Awaiting stays — the advisory is still unexecuted.
    assert AWAITING_KEY in load_push_state(paths["push_state_path"])


@pytest.mark.asyncio
async def test_unfilled_keeps_awaiting(tmp_path: Path) -> None:
    result, paths = await _run(tmp_path, {"outcome": "unfilled"}, text="没买到")
    assert not result.booked
    assert "未成交" in result.reply_text
    assert AWAITING_KEY in load_push_state(paths["push_state_path"])


@pytest.mark.asyncio
async def test_no_action_clears_awaiting(tmp_path: Path) -> None:
    result, paths = await _run(tmp_path, {"outcome": "no_action"}, text="不跟")
    assert not result.booked
    assert "不跟随" in result.reply_text
    assert AWAITING_KEY not in load_push_state(paths["push_state_path"])


@pytest.mark.asyncio
async def test_garbage_llm_output_degrades_to_clarification(
    tmp_path: Path,
) -> None:
    result, _ = await _run(tmp_path, "我不知道该输出什么", text="???")
    assert not result.booked
    assert "对账追问" in result.reply_text


@pytest.mark.asyncio
async def test_oversell_returns_drift_clarification(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    append_fill(
        paths["ledger_path"],
        ExternalExecutionEvent(
            external_trade_id="UT-20260824-100000-002271-BUY-001",
            code="002271",
            side=ManualTradeSide.BUY,
            volume=100,
            price=10.0,
            executed_at=RECEIVED.replace(hour=10, minute=0),
            reason=ManualTradeReason.USER_OTHER,
        ),
        recorded_at=RECEIVED.isoformat(),
    )
    result = await handle_owner_text(
        "卖了002271两千股 10.5",
        received_at=RECEIVED,
        complete_fn=_fake_llm(
            {
                "outcome": "filled",
                "code": "002271",
                "side": "SELL",
                "volume": 2000,
                "volume_unit": "shares",
                "price": 10.5,
            }
        ),
        **paths,
    )
    assert not result.booked
    assert "超过镜像账本" in result.reply_text
    assert load_book(paths["ledger_path"]).position_for("002271").volume == 100


@pytest.mark.asyncio
async def test_adjust_position_repairs_then_sell_books(tmp_path: Path) -> None:
    # codex P1: the drift clarification promises a repair workflow — the
    # owner states the actual holding, the mirror corrects, and the
    # re-reported sell then books.
    paths = _paths(tmp_path)
    append_fill(
        paths["ledger_path"],
        ExternalExecutionEvent(
            external_trade_id="UT-20260824-093000-002271-BUY-001",
            code="002271",
            side=ManualTradeSide.BUY,
            volume=100,
            price=10.0,
            executed_at=RECEIVED.replace(hour=9, minute=30),
            reason=ManualTradeReason.USER_OTHER,
        ),
        recorded_at=RECEIVED.isoformat(),
    )
    adjust = await handle_owner_text(
        "002271我实际持有300股",
        received_at=RECEIVED,
        complete_fn=_fake_llm(
            {
                "outcome": "adjust_position",
                "code": "002271",
                "volume": 300,
                "volume_unit": "shares",
            }
        ),
        **paths,
    )
    assert adjust.booked
    assert "100 股 → 300 股" in adjust.reply_text
    sell = await handle_owner_text(
        "上午卖了002271两百股 10.5",
        received_at=RECEIVED,
        complete_fn=_fake_llm(
            {
                "outcome": "filled",
                "code": "002271",
                "side": "SELL",
                "volume": 200,
                "volume_unit": "shares",
                "price": 10.5,
                "executed_time": "10:00",
            }
        ),
        **paths,
    )
    assert sell.booked
    assert load_book(paths["ledger_path"]).position_for("002271").volume == 100


@pytest.mark.asyncio
async def test_adjust_position_consistent_is_noop(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    result = await handle_owner_text(
        "002271没有持仓",
        received_at=RECEIVED,
        complete_fn=_fake_llm(
            {
                "outcome": "adjust_position",
                "code": "002271",
                "volume": 0,
                "volume_unit": "shares",
            }
        ),
        **paths,
    )
    # Zero is a legitimate holding declaration; the mirror is already
    # empty, so no correction row is written and the ack says consistent.
    assert not result.booked
    assert "一致" in result.reply_text


@pytest.mark.asyncio
async def test_adjust_zero_clears_same_day_fill(tmp_path: Path) -> None:
    # Regression (owner drill 2026-08-24): a position bought THIS morning,
    # then "002271我实际持有0股" — the midnight-backdated correction replayed
    # 0−100 at 00:00 and degraded to "did not understand". Effective-now
    # placement must clear it cleanly.
    paths = _paths(tmp_path)
    append_fill(
        paths["ledger_path"],
        ExternalExecutionEvent(
            external_trade_id="UT-20260824-094101-002271-BUY-001",
            code="002271",
            side=ManualTradeSide.BUY,
            volume=100,
            price=11.2,
            executed_at=RECEIVED.replace(hour=9, minute=41),
            reason=ManualTradeReason.USER_OTHER,
        ),
        recorded_at=RECEIVED.isoformat(),
    )
    result = await handle_owner_text(
        "002271我实际持有0股",
        received_at=RECEIVED,
        complete_fn=_fake_llm(
            {
                "outcome": "adjust_position",
                "code": "002271",
                "volume": 0,
                "volume_unit": "shares",
            }
        ),
        **paths,
    )
    assert result.booked
    assert "100 股 → 0 股" in result.reply_text
    assert load_book(paths["ledger_path"]).position_for("002271") is None


@pytest.mark.asyncio
async def test_declare_capital_sets_equity_and_is_idempotent(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    payload = {"outcome": "declare_capital", "amount": 100000}
    first = await handle_owner_text(
        "R线本金10万",
        received_at=RECEIVED,
        complete_fn=_fake_llm(payload),
        **paths,
    )
    assert first.booked
    assert "本金申报" in first.reply_text and "100000.00" in first.reply_text
    book = load_book(paths["ledger_path"])
    assert book.opening_declared and book.cash == 100000.0
    again = await handle_owner_text(
        "R线本金10万",
        received_at=RECEIVED,
        complete_fn=_fake_llm(payload),
        **paths,
    )
    assert not again.booked  # equity already matches — no double-count
    assert "无需调整" in again.reply_text


@pytest.mark.asyncio
async def test_z_record_books_to_z_ledger(tmp_path: Path) -> None:
    result, paths = await _run(
        tmp_path,
        {
            "outcome": "z_record",
            "z_type": "ipo_sell",
            "code": "301689",
            "name": "电科思仪",
            "amount": 21850,
        },
        text="中签的电科思仪卖了,赚了21850",
    )
    assert result.booked
    assert "已记录-Z线" in result.reply_text and "打新卖出" in result.reply_text
    lines = paths["z_ledger_path"].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["amount"] == 21850.0


@pytest.mark.asyncio
async def test_executed_time_overrides_message_time(tmp_path: Path) -> None:
    result, paths = await _run(
        tmp_path,
        {
            "outcome": "filled",
            "code": "002271",
            "side": "BUY",
            "volume": 100,
            "volume_unit": "shares",
            "price": 12.3,
            "executed_time": "09:45",
        },
    )
    assert result.booked
    row = json.loads(
        paths["ledger_path"].read_text(encoding="utf-8").splitlines()[0]
    )
    assert "09:45" in row["executed_at"]
