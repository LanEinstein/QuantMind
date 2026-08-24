"""R-line mirror ledger — append-only JSONL of owner-reported events.

A deliberate BYPASS of the sealed MockBroker constellation (MI-1 decision,
codex-discussed 2026-08-23): the mirror needs record → replay → display,
not order matching / T+1 / price limits — the owner already executed in the
real broker app, so the mirror only books what they report. Fee economics
reuse :func:`backend.broker.cost_calculator.calculate_cost` (pure function,
``apply_slippage_model=False`` — the reported price IS the real fill).

Row kinds:

* ``fill``  — one :class:`ExternalExecutionEvent` with derived friction;
* ``cash``  — a declared cash movement (opening capital / deposit / withdraw);
* ``adjust`` — an owner-CONFIRMED position correction (drift repair after a
  clarification round; keeps the ledger append-only yet repairable).

Replay orders rows by ``(effective_at, seq)`` so a late back-filled trade
lands in its true position instead of distorting the moving average cost.
Negative cash is NOT an error: the owner's account is the truth and our cash
line is only as good as the declared opening — the view discloses it instead.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.broker.cost_calculator import calculate_cost
from backend.broker.models import BrokerConfig, OrderDirection
from backend.data.stock_metadata import classify_board
from backend.models.manual_trade import ExternalExecutionEvent

DEFAULT_LEDGER = Path("data/portfolio/mirror_ledger.jsonl")
R_LINE = "R"

# The owner's real broker economics (P0-4-amendment defaults: 万1.5 commission
# floored at 5 CNY, stamp tax on SELL, SZ transfer fee) — model defaults.
_COST_CONFIG = BrokerConfig()


class MirrorDriftError(ValueError):
    """A reported event contradicts the mirror (e.g. selling more than held).

    Raised at replay/append time; the reconciliation loop turns it into a
    clarification back to the owner instead of booking a broken row.
    """


@dataclass(frozen=True)
class PositionState:
    """One mirrored position (fee-inclusive average cost on the BUY side)."""

    code: str
    volume: int
    avg_cost: float


@dataclass(frozen=True)
class MirrorBook:
    """Replayed R-line state (immutable snapshot of the ledger)."""

    positions: tuple[PositionState, ...]
    cash: float
    opening_declared: bool
    fill_count: int

    def position_for(self, code: str) -> PositionState | None:
        for p in self.positions:
            if p.code == code:
                return p
        return None


def fill_economics(event: ExternalExecutionEvent) -> dict[str, float]:
    """Derive friction for an owner-reported fill (v2 system-fee schema)."""
    breakdown = calculate_cost(
        code=event.code,
        board=classify_board(event.code),
        order_price=event.price,
        volume=event.volume,
        direction=(
            OrderDirection.BUY if event.side_is_buy else OrderDirection.SELL
        ),
        config=_COST_CONFIG,
        apply_slippage_model=False,  # the reported price IS the real fill
    )
    return {
        "commission": breakdown.commission,
        "stamp_tax": breakdown.stamp_tax,
        "transfer_fee": breakdown.transfer_fee,
        "gross": breakdown.gross_amount,
        "net": breakdown.net_amount,
    }


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: corrupt ledger row") from exc
        if row.get("kind") not in ("fill", "cash", "adjust"):
            raise ValueError(f"{path}:{line_no}: unknown kind {row.get('kind')!r}")
        rows.append(row)
    return rows


def _append_row(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def recorded_fill_ids(path: Path) -> frozenset[str]:
    """All external_trade_ids already booked (append-time dedupe)."""
    return frozenset(
        str(r["external_trade_id"])
        for r in _read_rows(path)
        if r.get("kind") == "fill"
    )


def append_fill(
    path: Path,
    event: ExternalExecutionEvent,
    *,
    recorded_at: str,
    effective_at: str | None = None,
) -> dict[str, Any] | None:
    """Book one owner-reported fill; returns the row, or None if duplicate.

    The PROSPECTIVE ledger (existing rows + this one) is replayed in
    effective-time order before anything is written, so an impossible SELL
    — including one back-filled BEFORE an already-recorded buy (codex P1)
    — raises :class:`MirrorDriftError` and the caller clarifies with the
    owner instead of persisting a row that would break every later replay.

    ``effective_at`` overrides the replay position (``executed_at`` stays
    on the row for display): the reconciliation loop uses it to book a
    RE-reported sell whose executed time precedes a drift correction —
    the final holding covers it, only the intraday ordering does not.
    """
    if event.external_trade_id in recorded_fill_ids(path):
        return None
    economics = fill_economics(event)
    row = {
        "kind": "fill",
        "line": R_LINE,
        "external_trade_id": event.external_trade_id,
        "code": event.code,
        "side": event.side.value,
        "volume": event.volume,
        "price": event.price,
        "executed_at": event.executed_at.isoformat(),
        "reason": event.reason.value,
        "note": event.note,
        **economics,
        "recorded_at": recorded_at,
        **({"effective_at": effective_at} if effective_at else {}),
    }
    _replay([*_read_rows(path), row])  # raises MirrorDriftError on drift
    _append_row(path, row)
    return row


def append_cash(
    path: Path, *, amount: float, note: str, recorded_at: str
) -> dict[str, Any]:
    """Declare a cash movement (opening capital / deposit / withdraw)."""
    if amount == 0:
        raise ValueError("cash movement amount must be non-zero")
    row = {
        "kind": "cash",
        "line": R_LINE,
        "amount": float(amount),
        "note": note,
        "recorded_at": recorded_at,
    }
    _append_row(path, row)
    return row


def append_adjust(
    path: Path,
    *,
    code: str,
    volume_delta: int,
    note: str,
    recorded_at: str,
    effective_at: str | None = None,
) -> dict[str, Any]:
    """Owner-confirmed position correction (drift repair, no cash effect).

    ``effective_at`` places the correction in replay time — the caller
    passes "now" (a correction states the holding AS OF NOW; placed last
    in replay it is always replayable: current + delta = actual ≥ 0).
    """
    if volume_delta == 0:
        raise ValueError("adjust volume_delta must be non-zero")
    row = {
        "kind": "adjust",
        "line": R_LINE,
        "code": code,
        "volume_delta": int(volume_delta),
        "note": note,
        "recorded_at": recorded_at,
        **({"effective_at": effective_at} if effective_at else {}),
    }
    _replay([*_read_rows(path), row])  # raises MirrorDriftError on drift
    _append_row(path, row)
    return row


def _effective_at(row: Mapping[str, Any]) -> datetime:
    # An explicit effective_at OVERRIDES executed_at: it is the deliberate
    # replay position (drift-repair re-reports; owner-stated corrections).
    raw = str(
        row.get("effective_at")
        or row.get("executed_at")
        or row.get("recorded_at")
    )
    return datetime.fromisoformat(raw)


def load_book(path: Path) -> MirrorBook:
    """Replay the ledger into the current R-line book (fail-closed on drift)."""
    return _replay(_read_rows(path))


def _replay(rows: list[dict[str, Any]]) -> MirrorBook:
    """Replay rows in effective-time order (fail-closed on drift)."""
    ordered = sorted(
        enumerate(rows), key=lambda item: (_effective_at(item[1]), item[0])
    )
    volumes: dict[str, int] = {}
    costs: dict[str, float] = {}
    cash = 0.0
    opening_declared = False
    fill_count = 0
    for _, row in ordered:
        if row["kind"] == "cash":
            cash += float(row["amount"])
            opening_declared = True
        elif row["kind"] == "adjust":
            code = str(row["code"])
            volumes[code] = volumes.get(code, 0) + int(row["volume_delta"])
            if volumes[code] < 0:
                raise MirrorDriftError(
                    f"adjust drives {code} negative at replay — ledger broken"
                )
            if volumes[code] == 0:
                volumes.pop(code)
                costs.pop(code, None)
        else:  # fill
            fill_count += 1
            code = str(row["code"])
            volume = int(row["volume"])
            net = float(row["net"])
            if row["side"] == "BUY":
                old_vol = volumes.get(code, 0)
                old_cost = costs.get(code, 0.0)
                volumes[code] = old_vol + volume
                costs[code] = (old_vol * old_cost + net) / (old_vol + volume)
                cash -= net
            else:
                held = volumes.get(code, 0)
                if held < volume:
                    raise MirrorDriftError(
                        f"replay: SELL {volume} of {code} exceeds held {held}"
                    )
                volumes[code] = held - volume
                cash += net
                if volumes[code] == 0:
                    volumes.pop(code)
                    costs.pop(code)  # full exit resets the cost basis
    positions = tuple(
        PositionState(code=code, volume=vol, avg_cost=round(costs.get(code, 0.0), 4))
        for code, vol in sorted(volumes.items())
    )
    return MirrorBook(
        positions=positions,
        cash=round(cash, 2),
        opening_declared=opening_declared,
        fill_count=fill_count,
    )
