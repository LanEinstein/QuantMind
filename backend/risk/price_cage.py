"""A-share continuous-auction price cage (价格笼子) — PURE, ZERO IO/LLM.

Lives in ``backend/risk/`` (like :mod:`backend.risk.stock_meta`) because the
RiskEngine check #02 cage subcheck consumes it. The module imports only stdlib
+ :class:`backend.risk.stock_meta.Board` — no ``backend.{llm,agents,mirofish,
data}`` (P0-7 §2 redline 9 / U-E2 amendment 2026-05-27).

Rule (2023 全面注册制, 连续竞价 limit-order valid price range), see
``docs/research/a-share-trading-rules-2026-05-27.md`` §2:

* BUY limit ≤ ``max(best_ask × 1.02, best_ask + 10 × tick)``  (沪深主板 / ETF)
* BUY limit ≤ ``best_ask × 1.02``  (创业板 / 科创板 — no 10-tick floor)

where ``best_ask`` is the current 卖一 (lowest ask) and the 10-tick alternative
widens the cage for low-priced names. ``tick`` is board-specific: stocks 0.01,
ETF/funds 0.001. The **ceiling** is computed with the true board tick so we
never *over*-estimate it; the **final limit** is floored to 0.01 (a valid
multiple of 0.001 for ETFs, and what the InstructionPlan / renderer pipeline
renders) — flooring down keeps the limit ≤ ceiling, never above it.

This cage is INDEPENDENT of and ADDITIONAL to the daily price-limit band
(±10%/±20% vs prev_close, RiskEngine check #02/#12). A compliant BUY limit must
satisfy BOTH. The cage is a continuous-auction 废单 guard (an order above the
cage is rejected by the exchange), so a *simulated* human-execution signal must
never propose a limit the operator's real broker would bounce.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from backend.risk.stock_meta import Board


@dataclass(frozen=True)
class CageQuote:
    """The minimal live-quote object the RiskEngine check #02 cage subcheck reads.

    Carries only the 卖一 (``best_ask``) the cage is computed against plus a
    ``source`` provenance label for the reject message. Kept in ``backend/risk/``
    (not ``backend.data``/``backend.models``) so the engine stays import-isolated
    and pure (P0-7 §2 redline 9): the data layer fetches the orderbook and the
    Line-1 provider hands the engine this frozen, IO-free view. A ``None``
    ``best_ask`` makes the cage unprovable → the subcheck fails closed (the
    provider should already have degraded the lead before reaching here).
    """

    best_ask: float | None
    source: str

# Continuous-auction cage band: 2% of the reference (best-ask) price, with a
# board-tick-scaled 10-tick alternative floor for low-priced names (孰高值).
CAGE_PCT = Decimal("0.02")
CAGE_TICK_MULTIPLE = 10

# Minimum price increment (申报价格最小变动单位) by board. Stocks 0.01 RMB;
# ETF / funds 0.001 RMB (docs/research/a-share-trading-rules-2026-05-27.md §4).
_TICK_STOCK = Decimal("0.01")
_TICK_ETF = Decimal("0.001")

# The InstructionPlan + renderer pipeline render/round prices to 0.01. The final
# cage-bounded limit is floored to this granularity (0.01 is a valid multiple of
# the ETF 0.001 tick, and flooring DOWN keeps it ≤ the ceiling).
_CENT = Decimal("0.01")

# Boards whose cage has NO 10-tick alternative (creative/sci-tech boards use a
# plain ±2% range per the SSE/SZSE special rules).
_PCT_ONLY_BOARDS = frozenset({Board.CHUANGYE, Board.KCHUANG})


def tick_size(board: Board) -> Decimal:
    """Return the minimum price increment (RMB) for ``board``.

    ETF/fund 0.001; every other (stock) board 0.01. The cage ceiling uses this
    so a low-priced ETF's 10-tick alternative (0.01) is not over-stated as the
    stock 10-tick (0.10).

    Compared by VALUE (``==``), never identity (``is``): the caller's ``Board``
    may be a different ``StrEnum`` class instance than this module's (the data
    layer re-exports it, and a test ``importlib.reload`` mints a fresh class), so
    ``board is Board.ETF`` would silently fall through and mis-tick an ETF as a
    stock — a 废单 risk (codex U-E2 P2). ``==`` matches on the "etf" value across
    classes (same fix class as the MarketPhase reload issue).
    """
    return _TICK_ETF if board == Board.ETF else _TICK_STOCK


def _raw_cage_ceiling(best_ask: float, board: Board) -> Decimal:
    """Exact (un-rounded) legal cage cap as a :class:`~decimal.Decimal`.

    ``max(best_ask × 1.02, best_ask + 10 × tick)`` (沪深主板 / ETF) or
    ``best_ask × 1.02`` (创业板 / 科创板). This is the authoritative ``≤`` bound the
    exchange enforces; callers compare against it exactly (no cent rounding) so a
    sub-cent-precision-valid ETF limit is not falsely rejected (codex U-E2 P2),
    while a cent-rounded *display* ceiling never sits above it.

    Raises :class:`ValueError` on an absent / non-positive / non-finite
    ``best_ask`` (U-E2: never fall back to ``last`` as the cage base).
    """
    ask = _require_positive_price(best_ask, "best_ask")
    pct_cap = ask * (Decimal("1") + CAGE_PCT)
    if board in _PCT_ONLY_BOARDS:
        return pct_cap
    tick_cap = ask + CAGE_TICK_MULTIPLE * tick_size(board)
    return max(pct_cap, tick_cap)  # 孰高值


def cage_ceiling(best_ask: float, board: Board) -> float:
    """Display '限价上限' — the highest valid 0.01 price ``≤`` the exact cage cap.

    Floors the exact cap (:func:`_raw_cage_ceiling`) DOWN to 0.01: rounding UP
    (HALF_UP) could push it above the raw cap (10.25 × 1.02 = 10.455 → 10.46),
    making a 废单 limit look legal (codex U-E2 P1). The 0.01 granularity matches
    what the InstructionPlan / renderer pipeline shows; the authoritative ``≤``
    comparison uses the exact cap via :func:`is_within_cage`.

    Raises :class:`ValueError` on an absent / non-positive / non-finite ``best_ask``.
    """
    raw = _raw_cage_ceiling(best_ask, board)
    return float(raw.quantize(_CENT, rounding=ROUND_DOWN))


def cage_bounded_buy_limit(
    *,
    last_price: float,
    best_ask: float,
    board: Board,
    tolerance_pct: float,
) -> float:
    """Deterministic cage-bounded BUY limit (the '限价上限' shown to the operator).

    ``floor_to_cent( min(last_price × (1 + tolerance_pct), cage_ceiling) )``:

    * ``last_price × (1 + tolerance_pct)`` — how far above the current trade we
      are willing to bid (a wide/stale ask cannot drag the limit far above the
      last print).
    * ``cage_ceiling`` — the legal exchange ceiling vs 卖一.

    The result is floored DOWN to 0.01 so it is a valid, renderable price that is
    guaranteed ``≤ cage_ceiling`` (never a 废单) and ``≤`` the tolerance bound.
    Purely a function of its inputs — ``last_price`` / ``best_ask`` are caller-
    fetched live quote fields; this module never does IO and never reads an LLM
    field (R0 §4 single-construction-point determinism).

    Raises :class:`ValueError` on bad inputs (the caller degrades to
    non-actionable rather than shipping a guessed price).
    """
    tol = _finite_nonneg_decimal(tolerance_pct, "tolerance_pct")
    last = _require_positive_price(last_price, "last_price")
    raw_ceiling = _raw_cage_ceiling(best_ask, board)  # exact cap; validates best_ask
    tol_cap = last * (Decimal("1") + tol)
    bounded = min(tol_cap, raw_ceiling)
    floored = bounded.quantize(_CENT, rounding=ROUND_DOWN)
    return float(floored)


def is_within_cage(*, limit_price: float, best_ask: float, board: Board) -> bool:
    """True iff ``limit_price`` ≤ the cage ceiling for ``best_ask`` / ``board``.

    The authoritative predicate the RiskEngine check #02 cage subcheck calls on
    the *rounded* order price (U-E2). An absent / non-positive / non-numeric
    ``best_ask`` (e.g. ``None`` when no 卖一 field exists) or a bad
    ``limit_price`` makes the cage unprovable → returns ``False`` (fail-closed;
    the engine rejects rather than crashing inside validation — codex U-E2 P2).
    """
    try:
        # Compare against the EXACT cap (not the cent-floored display ceiling) so
        # a sub-cent-valid ETF limit (e.g. 1.025) is not falsely rejected, while
        # an over-cap limit (10.46 > 10.455) is still caught (codex U-E2 P1/P2).
        raw_ceiling = _raw_cage_ceiling(best_ask, board)
        return _require_positive_price(limit_price, "limit_price") <= raw_ceiling
    except (ValueError, InvalidOperation, TypeError):
        return False


def _finite_nonneg_decimal(value: object, name: str) -> Decimal:
    """Coerce ``value`` to a finite, ``≥ 0`` :class:`Decimal` or raise ValueError.

    Used for ``tolerance_pct`` so a bad config ``inf`` (which would silently
    bypass the ``last × (1 + tol)`` cap) or ``nan`` (which would raise
    :class:`~decimal.InvalidOperation` deep in the arithmetic) fails closed with
    the documented ``ValueError`` (codex U-E2 P2).
    """
    if value is None:
        raise ValueError(f"{name} is required")
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"{name} is not a number: {value!r}") from exc
    if not dec.is_finite() or dec < 0:
        raise ValueError(f"{name} must be a finite value ≥ 0, got {value!r}")
    return dec


def _require_positive_price(value: object, name: str) -> Decimal:
    """Coerce ``value`` to a positive finite :class:`Decimal` or raise ValueError.

    Centralises the fail-closed guard so an absent quote (``None`` — no 卖一/last
    field), a non-numeric value, NaN/Inf, or ≤0 never slips through as a price.
    ``Decimal(str(None))`` would raise :class:`~decimal.InvalidOperation`, so we
    convert defensively and re-raise as ``ValueError`` (the documented contract).
    """
    if value is None:
        raise ValueError(f"{name} is required (absent quote → degrade non-actionable)")
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"{name} is not a number: {value!r}") from exc
    if not dec.is_finite() or dec <= 0:
        raise ValueError(f"{name} must be a positive finite price, got {value!r}")
    return dec


__all__ = [
    "CAGE_PCT",
    "CAGE_TICK_MULTIPLE",
    "CageQuote",
    "cage_bounded_buy_limit",
    "cage_ceiling",
    "is_within_cage",
    "tick_size",
]
