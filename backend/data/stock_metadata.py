"""A-share stock board classification + price-limit single source (C-001).

Pure Python — no LLM, no network, no DB. The module is consumed by
``InstructionPlanBuilder`` (Phase D) and ``MockBroker`` (Phase E); the
``RiskEngine`` receives a :class:`StockMetadata` DTO via
``DailyTradingState`` per P0-7 §1 and never imports this module
directly (redline: ``backend/risk`` cannot import ``backend.data``).

Locked invariants (P0-7 §1.3 + P0-9 §1.2 + P1-2.C §1.3):

* Allowed boards: ``SH_MAIN`` / ``SZ_MAIN`` / ``CHUANGYE`` / ``ETF``.
* Forbidden codes raise :class:`ForbiddenCodeError` with a
  ``reason`` namespace ready for audit:

    - STAR market (688/689) — ``reason='star_forbidden'``
    - 北交所 (4xx/8xx/920) — ``reason='bj_forbidden'``
    - 可转债 (110/113/118/123/127/128/132) — ``reason='cb_forbidden'``
    - B-shares (200/900) — ``reason='b_share_forbidden'``

* Unknown / malformed codes raise :class:`UnknownCodeError`.
* ST status is name-derived via :func:`is_st_name`; the
  ``InstructionPlanBuilder`` fifth early-return uses it to bounce
  ST/*ST/退市/PT entries before they reach the 14-check.
* :func:`get_price_limit_pct` is the **single source of truth** for the
  涨跌停 percentage — the orphan helper at ``backend/broker/mock_broker.py:35``
  is scheduled for removal in D-007 / E-003; until then both call sites
  must produce identical (low, high) tuples for any classifiable code.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOT_SIZE = 100
"""A-share minimum trading lot. Mirrors ``RiskConfig.volume_lot_size``."""

_PCT_MAIN = 0.10
"""Main-board + ETF daily price-limit percentage (10%)."""

_PCT_CHUANGYE = 0.20
"""ChiNext (300/301) daily price-limit percentage (20% post-2020 reform).
P0-9 keeps ChiNext in scope; STAR (688/689) at 20% is forbidden via
``ForbiddenCodeError`` because P0-9 §1.2 explicitly excludes 科创板."""

_ALLOWED_PREFIXES_BY_BOARD: dict[str, tuple[str, ...]] = {
    "SH_MAIN": ("600", "601", "603", "605"),
    "SZ_MAIN": ("000", "001", "002", "003"),
    "CHUANGYE": ("300", "301"),
    # ETF SH prefixes 51x + 588 (STAR-tracking ETF wrapper is allowed even
    # though raw 688 STAR stocks are forbidden); ETF SZ prefix 159.
    "ETF": (
        "510", "511", "512", "513", "515", "516", "517", "518", "588",
        "159",
    ),
}
"""Prefix → board allowlist. Anything not matching falls into the
forbidden table or :class:`UnknownCodeError`."""

_FORBIDDEN_PREFIXES: dict[str, tuple[tuple[str, ...], str]] = {
    # STAR market raw individual stocks (688/689). 588 STAR-tracking ETF
    # wrappers are allowed and live in _ALLOWED_PREFIXES_BY_BOARD["ETF"].
    "STAR": (("688", "689"), "star_forbidden"),
    # 北交所 (Beijing Stock Exchange) + 全国股转 (NEEQ) — all 4-prefix /
    # 8-prefix / 92-prefix codes. Broad prefix match is intentional so
    # any legacy NEEQ / new BJ listing tag is fail-closed under the
    # stable ``bj_forbidden`` audit namespace, not UnknownCodeError
    # (matches the docstring promise "4xx/8xx/920").
    "BJ": (("4", "8", "92"), "bj_forbidden"),
    "CB": (("110", "113", "118", "123", "127", "128", "132"), "cb_forbidden"),
    "B_SHARE": (("200", "900"), "b_share_forbidden"),
}
"""Prefix → (prefixes, audit reason) for boards/securities P0-9 forbids."""

# Tokens that mark a name as a special-treatment / delisting / PT stock.
# ``InstructionPlanBuilder`` fifth early-return rejects any matching name.
_ST_TOKENS: tuple[str, ...] = ("ST", "*ST", "退", "PT")


# ---------------------------------------------------------------------------
# Enums + Exceptions
# ---------------------------------------------------------------------------


class Board(StrEnum):
    """The four allowed A-share boards (P0-7 + P0-9)."""

    SH_MAIN = "sh_main"
    SZ_MAIN = "sz_main"
    CHUANGYE = "chuangye"
    ETF = "etf"


class ForbiddenCodeError(ValueError):
    """Raised when a code's prefix maps to an explicitly forbidden board.

    The ``reason`` namespace is stable so audit + Builder early-return
    tags can match without string-parsing the message.
    """

    def __init__(self, code: str, reason: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason


class UnknownCodeError(ValueError):
    """Raised when a code does not match any known prefix (allowed or forbidden)."""


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


class StockMetadata(BaseModel):
    """Per-code metadata DTO assembled by ``InstructionPlanBuilder``.

    ``RiskEngine`` consumes this via ``DailyTradingState`` (P0-7 §1) so
    the engine stays a pure function with no IO and no ``backend.data``
    import.

    Frozen + strict + ``extra='forbid'`` per P0-3 §2 redline 12.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=64)
    board: Board
    lot_size: int = Field(default=_LOT_SIZE, ge=1)
    price_limit_pct: float = Field(gt=0.0, lt=1.0)
    is_st: bool = False
    listed_date: date | None = None

    @field_validator("name")
    @classmethod
    def _strip_control(cls, value: str) -> str:
        for ch in value:
            if ord(ch) < 0x20 or ord(ch) == 0x7F:
                raise ValueError(f"name contains control character {ch!r}")
        return value


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def _validate_code_shape(code: str) -> None:
    if not isinstance(code, str):
        raise TypeError(f"code must be str, got {type(code).__name__}")
    if len(code) != 6 or not code.isdigit():
        raise UnknownCodeError(f"code {code!r} must be a 6-digit string")


def classify_board(code: str) -> Board:
    """Return the :class:`Board` for a 6-digit A-share code.

    Raises :class:`ForbiddenCodeError` for STAR / 北交 / 可转债 / B-share
    prefixes (audit ``reason`` attached), :class:`UnknownCodeError` for
    anything else (including malformed input).
    """
    _validate_code_shape(code)

    for board_name, prefixes in _ALLOWED_PREFIXES_BY_BOARD.items():
        if code.startswith(prefixes):
            return Board[board_name]

    for label, (prefixes, reason) in _FORBIDDEN_PREFIXES.items():
        if code.startswith(prefixes):
            raise ForbiddenCodeError(
                code=code,
                reason=reason,
                message=f"code {code!r} maps to forbidden board {label} ({reason})",
            )

    raise UnknownCodeError(f"code {code!r} has no known board prefix")


def get_price_limit_pct(board: Board) -> float:
    """Return the ±daily price-limit percentage for an allowed board.

    Single source of truth. The orphan ``backend/broker/mock_broker.py``
    helper is scheduled for removal in D-007 / E-003.
    """
    if board is Board.CHUANGYE:
        return _PCT_CHUANGYE
    return _PCT_MAIN


_TWO_PLACES = Decimal("0.01")
_ONE = Decimal("1")


def _to_decimal(value: float) -> Decimal:
    """Lossless float→Decimal via ``str()`` (preserves the shortest repr)."""
    return Decimal(str(value))


def _round_half_up(value: float) -> float:
    """Round to 2 decimal places using HALF_UP (exchange-standard 四舍五入).

    Python's built-in ``round()`` uses banker's rounding (HALF_EVEN),
    which can give an off-by-one-cent answer at the legal price-limit
    boundary (e.g. 9.225 → 9.22 instead of the exchange-published 9.23).
    Using ``Decimal`` with ``ROUND_HALF_UP`` matches what SSE/SZSE
    publish in their 涨跌停限价 announcements.
    """
    return float(_to_decimal(value).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def get_price_limits(board: Board, prev_close: float) -> tuple[float, float]:
    """Return (lower_limit, upper_limit) rounded to 2 decimals (HALF_UP).

    The full computation runs in :class:`decimal.Decimal` so float
    multiplication can never bias the half — for example
    ``prev_close=1.65`` in IEEE 754 evaluates to ``1.649999…`` and
    ``× 0.9`` lands at ``1.4849999…`` instead of the exact ``1.485``;
    multiplying as Decimals first and only converting to float on the
    final ``quantize`` call keeps the answer exchange-correct (here
    ``1.49``).

    ``prev_close`` ≤ 0 returns ``(0.0, 0.0)`` so callers can short-circuit
    without raising; the at-fill check in MockBroker treats this as
    ``data_unavailable`` rather than a configuration error.
    """
    if prev_close <= 0:
        return (0.0, 0.0)
    pct = _to_decimal(get_price_limit_pct(board))
    prev = _to_decimal(prev_close)
    low = (prev * (_ONE - pct)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    high = (prev * (_ONE + pct)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    return (float(low), float(high))


def is_st_name(name: str) -> bool:
    """Return ``True`` if ``name`` carries any ST / *ST / 退 / PT marker.

    The check is substring-based to catch both Chinese tag (``退``) and
    Latin tags (``ST``, ``*ST``, ``PT``) which can appear as prefix or
    embedded in stock names like ``"*ST 中安"`` or ``"中视退"``.
    """
    if not name:
        return False
    upper = name.upper()
    return any(token in upper for token in _ST_TOKENS)


def get_lot_size(board: Board) -> int:  # noqa: ARG001
    """Return the minimum trading lot — currently 100 across all boards."""
    return _LOT_SIZE


def build_metadata(
    *,
    code: str,
    name: str,
    listed_date: date | None = None,
) -> StockMetadata:
    """Assemble :class:`StockMetadata` from raw inputs.

    Convenience constructor used by ``InstructionPlanBuilder`` so the
    board / pct / lot / ST detection stay consistent across callers.
    Forbidden / unknown codes propagate the original exception so the
    Builder fifth early-return can tag the audit event.
    """
    board = classify_board(code)
    return StockMetadata(
        code=code,
        name=name,
        board=board,
        lot_size=get_lot_size(board),
        price_limit_pct=get_price_limit_pct(board),
        is_st=is_st_name(name),
        listed_date=listed_date,
    )


__all__ = [
    "Board",
    "ForbiddenCodeError",
    "StockMetadata",
    "UnknownCodeError",
    "build_metadata",
    "classify_board",
    "get_lot_size",
    "get_price_limit_pct",
    "get_price_limits",
    "is_st_name",
]
