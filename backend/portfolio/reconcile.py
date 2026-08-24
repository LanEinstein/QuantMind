"""MI-1 free-text reconciliation — owner report → structured event → mirror.

The owner writes natural language in the decision chat ("买了东方雨虹5000股
成交12.3" / "没买" / "不跟" / "中签卖了赚21850"). One LLM call maps that text
plus deterministic context (the current advisory book + mirrored positions)
into a STRICT structured extraction; everything after the extraction is
deterministic code:

* ``filled``   → mint a ``UT-`` :class:`ExternalExecutionEvent`, book it to
  the R-line mirror ledger, clear the push loop's awaiting-report flag,
  ack via :meth:`MessageRenderer.render_manual_trade_ack`;
* ``unfilled`` → ack; the awaiting flag STAYS so tomorrow's cron re-pushes;
* ``no_action`` → ack; the awaiting flag clears (owner chose not to follow);
* ``z_record`` → append to the Z-line ledger, ack;
* ``unclear`` / missing fields → ONE renderer-composed clarification; the
  owner restates and the flow simply restarts (stateless, codex-agreed —
  no conversation state machine).

Red lines: the LLM produces ONLY the structured extraction — it never
composes wire copy (every reply below goes through MessageRenderer), never
touches risk or research ledgers, and a schema-invalid output degrades to a
clarification, never to a booked row.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.data.stock_metadata import ForbiddenCodeError, UnknownCodeError
from backend.integrations.feishu.renderer import MessageRenderer
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
    recorded_fill_ids,
)
from backend.portfolio.sleeve_push_state import clear_awaiting_report
from backend.portfolio.z_ledger_io import append_record, make_record

CompleteFn = Callable[[str], Awaitable[str]]

Z_TYPE_LABEL: dict[str, str] = {
    "ipo_win": "打新中签",
    "ipo_sell": "打新卖出",
    "cb_win": "转债中签",
    "cb_sell": "转债卖出",
    "cash_yield": "现金收益",
}


class ReconcileExtraction(BaseModel):
    """The ONLY thing the LLM is allowed to produce (schema-validated)."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal[
        "filled",
        "unfilled",
        "no_action",
        "adjust_position",
        "declare_capital",
        "z_record",
        "unclear",
    ]
    code: str | None = None
    name: str | None = None
    side: Literal["BUY", "SELL"] | None = None
    volume: float | None = None
    volume_unit: Literal["shares", "lots"] | None = None
    price: float | None = None
    executed_time: str | None = None  # "HH:MM[:SS]", optional
    z_type: (
        Literal["ipo_win", "ipo_sell", "cb_win", "cb_sell", "cash_yield"] | None
    ) = None
    amount: float | None = None
    note: str = ""


@dataclass(frozen=True)
class ReconcileResult:
    """What one inbound message produced (reply is renderer-composed)."""

    reply_text: str
    booked: bool  # True when the mirror or Z ledger gained a row


def build_extraction_prompt(
    text: str,
    *,
    advisory_holdings: Sequence[Mapping[str, Any]],
    mirror_positions: Sequence[Mapping[str, Any]],
) -> str:
    """Deterministic prompt: context + rules + the owner's raw text."""
    advisory = [
        {"code": str(h.get("ts_code", ""))[:6], "name": str(h.get("name", ""))}
        for h in advisory_holdings
    ]
    held = [
        {"code": str(p.get("code", "")), "volume": int(p.get("volume", 0))}
        for p in mirror_positions
    ]
    return (
        "你是一个 A 股成交回报抽取器。owner 会用自由中文汇报当天在券商 App"
        " 的真实操作;你只输出一个 JSON 对象,不输出任何其他文字。\n"
        "JSON schema(字段全为可选,除 outcome):\n"
        '{"outcome": "filled|unfilled|no_action|z_record|unclear",\n'
        ' "code": "6位股票代码", "name": "标的名", "side": "BUY|SELL",\n'
        ' "volume": 数值, "volume_unit": "shares|lots",\n'
        ' "price": 每股成交价(元), "executed_time": "HH:MM",\n'
        ' "z_type": "ipo_win|ipo_sell|cb_win|cb_sell|cash_yield",\n'
        ' "amount": 金额(元), "note": "原文里值得保留的备注"}\n'
        "规则:\n"
        "1. 一条消息只抽一笔;多笔或与交易无关 → outcome=unclear。\n"
        "2. 买入/卖出成交 → filled;明说没成交/没买到 → unfilled;"
        "明说不跟/不操作本次建议 → no_action;打新中签/中签卖出/"
        "转债/现金理财收益 → z_record(amount=盈亏或中签金额);"
        "owner 声明某标的的实际总持股数量(纠正账本,非一笔成交,"
        "「清仓了/没有持仓」= volume 0)→ adjust_position(volume=实际总持股);"
        "owner 申报这条线的本金/总资金(如「R线本金10万」「这条线配5万」)→ "
        "declare_capital(amount=金额,元)。\n"
        "3. 「手」是 lots(1手=100股),「股」是 shares;不确定单位时"
        "留空 volume_unit,不要猜。数字只搬运,绝不换算或凑整。\n"
        "4. 只有下方上下文或原文能确定代码时才填 code;猜不出就留空。\n"
        "5. 缺哪个字段就省略哪个字段,不要编造。\n"
        f"当前建议书标的: {json.dumps(advisory, ensure_ascii=False)}\n"
        f"当前镜像持仓: {json.dumps(held, ensure_ascii=False)}\n"
        f"owner 原文: {text}"
    )


def parse_extraction(raw: str) -> ReconcileExtraction | None:
    """Parse the LLM output; None = not valid JSON/schema (→ clarification)."""
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return ReconcileExtraction.model_validate(payload)
    except ValidationError:
        return None


def missing_fill_fields(extraction: ReconcileExtraction) -> list[str]:
    """Which required fill fields are absent/unusable (deterministic)."""
    missing: list[str] = []
    if not (extraction.code and extraction.code.isdigit()
            and len(extraction.code) == 6):
        missing.append("code")
    if extraction.side is None:
        missing.append("side")
    if not _normalized_volume(extraction):
        missing.append("volume")
    if extraction.price is None or extraction.price <= 0:
        missing.append("price")
    return missing


def _normalized_volume(
    extraction: ReconcileExtraction, *, allow_zero: bool = False
) -> int | None:
    """Shares as a positive int; lots ×100; non-integral/unknown → None.

    ``allow_zero`` is for adjust_position — "cleared the position" is a
    legitimate holding declaration; a fill volume must stay positive.
    """
    if extraction.volume is None or extraction.volume < 0:
        return None
    if extraction.volume == 0:
        return 0 if allow_zero else None
    if extraction.volume != int(extraction.volume):
        return None
    shares = int(extraction.volume)
    if extraction.volume_unit == "lots":
        return shares * 100
    if extraction.volume_unit == "shares":
        return shares
    return None  # unit unknown — never guess (codex-agreed)


def _executed_at(extraction: ReconcileExtraction, received_at: datetime) -> datetime:
    """Reported HH:MM on the message's date, else the message time itself."""
    if extraction.executed_time:
        try:
            parsed = time.fromisoformat(extraction.executed_time)
            return datetime.combine(received_at.date(), parsed, received_at.tzinfo)
        except ValueError:
            pass
    return received_at


def mint_external_id(
    *, code: str, side: str, executed_at: datetime, existing: frozenset[str]
) -> str:
    """UT-YYYYMMDD-HHMMSS-CODE-SIDE-SEQ, first free SEQ for that identity."""
    stamp = executed_at.strftime("%Y%m%d-%H%M%S")
    for seq in range(1, 1000):
        candidate = f"UT-{stamp}-{code}-{side}-{seq:03d}"
        if candidate not in existing:
            return candidate
    raise ValueError("no free UT- sequence — implausible volume of fills")


async def handle_owner_text(
    text: str,
    *,
    received_at: datetime,
    complete_fn: CompleteFn,
    ledger_path: Path,
    z_ledger_path: Path,
    push_state_path: Path,
    status_path: Path,
) -> ReconcileResult:
    """One inbound owner message → at most one booked row + one reply."""
    renderer = MessageRenderer()

    advisory_holdings: Sequence[Mapping[str, Any]] = ()
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            advisory_holdings = (status.get("advisory") or {}).get("holdings") or ()
        except (json.JSONDecodeError, OSError):
            advisory_holdings = ()
    book = load_book(ledger_path)
    prompt = build_extraction_prompt(
        text,
        advisory_holdings=advisory_holdings,
        mirror_positions=[
            {"code": p.code, "volume": p.volume} for p in book.positions
        ],
    )
    extraction = parse_extraction(await complete_fn(prompt))
    if extraction is None or extraction.outcome == "unclear":
        return ReconcileResult(
            reply_text=renderer.render_reconcile_clarification(
                raw_text_excerpt=text
            ),
            booked=False,
        )

    if extraction.outcome == "unfilled":
        # Awaiting flag deliberately KEPT — tomorrow's cron re-pushes.
        return ReconcileResult(
            reply_text=renderer.render_reconcile_outcome(kind="unfilled"),
            booked=False,
        )
    if extraction.outcome == "no_action":
        clear_awaiting_report(push_state_path)
        return ReconcileResult(
            reply_text=renderer.render_reconcile_outcome(kind="no_action"),
            booked=False,
        )
    if extraction.outcome == "z_record":
        return _book_z_record(extraction, z_ledger_path, renderer, text)
    if extraction.outcome == "adjust_position":
        return _book_adjustment(
            extraction, text, received_at, ledger_path, renderer
        )
    if extraction.outcome == "declare_capital":
        return _book_capital(extraction, text, received_at, ledger_path, renderer)
    return _book_fill(
        extraction, text, received_at, ledger_path, push_state_path, renderer
    )


def _book_adjustment(
    extraction: ReconcileExtraction,
    raw_text: str,
    received_at: datetime,
    ledger_path: Path,
    renderer: MessageRenderer,
) -> ReconcileResult:
    """Owner states the ACTUAL total holding → correct the mirror (codex P1:
    the drift clarification promised this workflow; here it is reachable)."""
    missing: list[str] = []
    if not (extraction.code and extraction.code.isdigit()
            and len(extraction.code) == 6):
        missing.append("code")
    actual = _normalized_volume(extraction, allow_zero=True)
    if actual is None:
        missing.append("volume")
    if missing:
        return ReconcileResult(
            reply_text=renderer.render_reconcile_clarification(
                missing_fields=missing, raw_text_excerpt=raw_text
            ),
            booked=False,
        )
    code = str(extraction.code)
    held = load_book(ledger_path).position_for(code)
    old_volume = held.volume if held else 0
    if actual != old_volume:
        # Effective NOW: a correction states the holding AS OF NOW, and
        # placed last in replay it is always replayable (current + delta =
        # actual ≥ 0). Backdating to midnight broke against same-day booked
        # fills (owner drill 2026-08-24: clearing a position bought that
        # morning replayed 0−100 at 00:00 → spurious "did not understand").
        append_adjust(
            ledger_path,
            code=code,
            volume_delta=int(actual) - old_volume,
            note=(
                f"owner-confirmed holding: {extraction.note}"[:256]
            ).rstrip(": "),
            recorded_at=received_at.isoformat(),
            effective_at=received_at.isoformat(),
        )
    return ReconcileResult(
        reply_text=renderer.render_reconcile_adjust_ack(
            code=code, old_volume=old_volume, new_volume=int(actual)
        ),
        booked=actual != old_volume,
    )


def _book_z_record(
    extraction: ReconcileExtraction,
    z_ledger_path: Path,
    renderer: MessageRenderer,
    raw_text: str,
) -> ReconcileResult:
    if extraction.z_type is None:
        return ReconcileResult(
            reply_text=renderer.render_reconcile_clarification(
                raw_text_excerpt=raw_text
            ),
            booked=False,
        )
    missing: list[str] = []
    if extraction.amount is None:
        missing.append("amount")
    if extraction.z_type != "cash_yield" and not (
        extraction.code or extraction.name
    ):
        missing.append("code")
    if missing:
        return ReconcileResult(
            reply_text=renderer.render_reconcile_clarification(
                missing_fields=missing, raw_text_excerpt=raw_text
            ),
            booked=False,
        )
    record = make_record(
        type=str(extraction.z_type),
        code=extraction.code or extraction.name or "",
        name=extraction.name or "",
        amount=float(extraction.amount),  # type: ignore[arg-type]
        note=extraction.note,
    )
    append_record(z_ledger_path, record)
    return ReconcileResult(
        reply_text=renderer.render_z_record_ack(
            type_label=Z_TYPE_LABEL[str(extraction.z_type)],
            code=record.code,
            name=record.name,
            amount=record.amount,
        ),
        booked=True,
    )


def _book_capital(
    extraction: ReconcileExtraction,
    raw_text: str,
    received_at: datetime,
    ledger_path: Path,
    renderer: MessageRenderer,
) -> ReconcileResult:
    """Owner declares the R-line capital → SET the mirror equity to it.

    Equity = cash + holdings at cost; the delta lands as one cash row, so
    a re-declaration adjusts instead of double-counting. This is what
    makes suggested share counts in the advisory possible.
    """
    if extraction.amount is None or extraction.amount <= 0:
        return ReconcileResult(
            reply_text=renderer.render_reconcile_clarification(
                missing_fields=["amount"], raw_text_excerpt=raw_text
            ),
            booked=False,
        )
    target = float(extraction.amount)
    book = load_book(ledger_path)
    equity = book.cash + sum(p.volume * p.avg_cost for p in book.positions)
    delta = round(target - equity, 2)
    if abs(delta) >= 0.01:
        append_cash(
            ledger_path,
            amount=delta,
            note=f"owner-declared capital {target:.2f}",
            recorded_at=received_at.isoformat(),
        )
    return ReconcileResult(
        reply_text=renderer.render_capital_ack(
            total_capital=target, cash_delta=delta
        ),
        booked=abs(delta) >= 0.01,
    )


def _book_fill(
    extraction: ReconcileExtraction,
    raw_text: str,
    received_at: datetime,
    ledger_path: Path,
    push_state_path: Path,
    renderer: MessageRenderer,
) -> ReconcileResult:
    missing = missing_fill_fields(extraction)
    if missing:
        return ReconcileResult(
            reply_text=renderer.render_reconcile_clarification(
                missing_fields=missing, raw_text_excerpt=raw_text
            ),
            booked=False,
        )
    volume = _normalized_volume(extraction)
    executed_at = _executed_at(extraction, received_at)
    side = str(extraction.side)
    code = str(extraction.code)
    try:
        event = ExternalExecutionEvent(
            external_trade_id=mint_external_id(
                code=code,
                side=side,
                executed_at=executed_at,
                existing=recorded_fill_ids(ledger_path),
            ),
            code=code,
            side=ManualTradeSide(side),
            volume=int(volume),  # type: ignore[arg-type]
            price=float(extraction.price),  # type: ignore[arg-type]
            executed_at=executed_at,
            reason=ManualTradeReason.USER_OTHER,
            note=extraction.note[:256],
        )
    except ValidationError:
        return ReconcileResult(
            reply_text=renderer.render_reconcile_clarification(
                raw_text_excerpt=raw_text
            ),
            booked=False,
        )
    try:
        row = append_fill(
            ledger_path, event, recorded_at=received_at.isoformat()
        )
    except (ForbiddenCodeError, UnknownCodeError):
        # e.g. a STAR-board code — outside the supported cost model; the
        # mirror cannot book it, so say so instead of crashing the loop.
        return ReconcileResult(
            reply_text=renderer.render_reconcile_clarification(
                raw_text_excerpt=raw_text
            ),
            booked=False,
        )
    except MirrorDriftError:
        held = load_book(ledger_path).position_for(code)
        held_volume = held.volume if held else 0
        if not event.side_is_buy and held_volume >= event.volume:
            # The FINAL holding covers this sell — only the intraday
            # ordering does not (a re-reported sell executed before a
            # drift correction). Book it effective NOW; executed_at stays
            # on the row for display.
            row = append_fill(
                ledger_path,
                event,
                recorded_at=received_at.isoformat(),
                effective_at=received_at.isoformat(),
            )
        else:
            return ReconcileResult(
                reply_text=renderer.render_reconcile_drift(
                    code=code,
                    reported_volume=event.volume,
                    held_volume=held_volume,
                ),
                booked=False,
            )
    if row is None:  # duplicate external id — cannot happen with minting, but
        return ReconcileResult(  # keep the honest idempotent ack anyway
            reply_text=renderer.render_manual_trade_ack(
                event=event, cash_delta=0.0, broker_event_sequence=None,
                is_duplicate=True,
            ),
            booked=False,
        )
    clear_awaiting_report(push_state_path)
    cash_delta = -float(row["net"]) if event.side_is_buy else float(row["net"])
    return ReconcileResult(
        reply_text=renderer.render_manual_trade_ack(
            event=event, cash_delta=cash_delta, broker_event_sequence=None
        ),
        booked=True,
    )
